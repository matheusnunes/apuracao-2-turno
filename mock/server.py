"""Mock do servidor de resultados do TSE (2T 2026: eleição 6258, pleito 3221).

  uv run python -m mock.server --scenario swing2022 --speed 10 --start 16:55 --port 8787

Serve em http://HOST:PORT/oficial/... os mesmos caminhos do TSE (ver mock/README.md).
Controle (fora do TSE): /__mock/status, /__mock/clock?t=HH:MM[:SS]&speed=N, /__mock/health
"""

from __future__ import annotations

import argparse
import asyncio
import random
import re
import sys
import time
from dataclasses import dataclass

import orjson
from aiohttp import web

from . import prepare, scenario
from .state import DAY0_UTC, Clock, Resource, Sim, brt_date, brt_time, http_date, parse_hhmm, parse_window

B = r"^/oficial/ele2026/"
R_BR_U = re.compile(B + r"6258/dados/br/br-c0001-e006258-u\.(json|jws)$")
R_UF_U = re.compile(B + r"6258/dados/([a-z]{2})/\1-c0001-e006258-u\.(json|jws)$")
R_MU_U = re.compile(B + r"6258/dados/([a-z]{2})/\1(\d{5})-c0001-e006258-u\.(json|jws)$")
R_ZN_U = re.compile(B + r"6258/dados/([a-z]{2})/\1(\d{5})-z(\d{4})-c0001-e006258-u\.(json|jws)$")
R_BR_AB = re.compile(B + r"6258/dados/br/br-e006258-ab\.(json|jws)$")
R_UF_AB = re.compile(B + r"6258/dados/([a-z]{2})/\1-e006258-ab\.(json|jws)$")
R_CM = re.compile(B + r"6258/config/mun-e006258-cm\.(json|jws)$")
R_CS = re.compile(B + r"arquivo-urna/3221/config/([a-z]{2})/\1-p003221-cs\.(json|jws)$")
R_AUX = re.compile(B + r"arquivo-urna/3221/dados/([a-z]{2})/(\d{5})/(\d{4})/(\d{4})/"
                   r"p003221-\1-m\2-z\3-s\4-aux\.json$")
R_FILE = re.compile(B + r"arquivo-urna/3221/dados/([a-z]{2})/(\d{5})/(\d{4})/(\d{4})/([0-9a-f]{16,200})/"
                    r"(o03221\1\2\3\4-[a-z]+\.[a-z]+)$")
R_ELEC = re.compile(r"^/oficial/comum/config/ele-c\.json$")

AKAMAI_403 = ("<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n<H1>Access Denied</H1>\n \n"
              "You don't have permission to access \"{url}\" on this server.<P>\n"
              "Reference&#32;&#35;18&#46;{ref}\n<P>https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;18&#46;{ref}</P>\n"
              "</BODY>\n</HTML>\n")
S3_ERR = ('<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>{msg}</Message>'
          '<BucketName>tse-resultados-1</BucketName><RequestId>{rid}</RequestId>'
          '<HostId>3edceb4-default-default</HostId></Error>')


@dataclass
class Burst:
    code: int
    t0: int
    t1: int
    rate: float


def parse_burst(s: str) -> Burst:
    """'403@19:10-19:20' ou '503@20:00-20:05@0.3' (janela em hora simulada)."""
    parts = s.split("@")
    code = int(parts[0])
    t0, t1 = parse_window(parts[1])
    rate = float(parts[2]) if len(parts) > 2 else 1.0
    return Burst(code, t0, t1, rate)


class Edge:
    """Emula o cache do CDN (hora real): 404 "gruda" por sticky_404 s; 200 por edge_ttl s."""

    def __init__(self, sticky_404: float, ttl_200: float):
        self.sticky_404 = sticky_404
        self.ttl_200 = ttl_200
        self.d: dict[str, tuple] = {}

    def get(self, path: str):
        e = self.d.get(path)
        if e is None:
            return None
        if e[0] <= time.time():
            del self.d[path]
            return None
        return e

    def put(self, path: str, status: int, res: Resource | None):
        ttl = self.sticky_404 if status == 404 else self.ttl_200
        if ttl > 0:
            self.d[path] = (time.time() + ttl, status, res)

    def purge(self):
        now = time.time()
        for k in [k for k, v in self.d.items() if v[0] <= now]:
            del self.d[k]


class Stats:
    def __init__(self):
        self.c: dict[str, int] = {}
        self.t0 = time.time()
        self.last = (time.time(), 0)

    def inc(self, k: str, n: int = 1):
        self.c[k] = self.c.get(k, 0) + n


class App:
    def __init__(self, args, tr, sim: Sim):
        self.args = args
        self.tr = tr
        self.sim = sim
        self.clock = Clock(parse_hhmm(args.start), args.speed)
        self.edge = Edge(args.sticky_404, args.edge_ttl)
        self.stats = Stats()
        self.rng = random.Random(args.seed * 7919 + 13)
        self.bursts = [parse_burst(b) for b in (args.burst or [])]
        if args.burst_403:
            t0, t1 = parse_window(args.burst_403)
            self.bursts.append(Burst(403, t0, t1, 1.0))
        self.rid = 0

    # ----------------------------------------------------------- respostas
    def _base_headers(self, T: float, max_age: int, origin: str | None):
        self.rid += 1
        h = {
            "Date": http_date(T),
            "x-amz-request-id": f"tx{self.rid:021x}-{int(T) & 0xffffffff:08x}-mock-default",
            "x-ratelimit-limit": "5000, 5000;w=1",
            "x-ratelimit-remaining": str(4900 + self.rng.randint(0, 99)),
            "x-ratelimit-reset": "1",
            "Cache-Control": f"max-age={max_age}",
            "Expires": http_date(T + max_age),
            "X-Mock-Sim-Time": f"{brt_date(int(T))} {brt_time(int(T))}",
        }
        if origin:
            h["Access-Control-Allow-Origin"] = origin
            h["Vary"] = "Accept-Encoding, Origin"
        else:
            h["Vary"] = "Accept-Encoding"
        return h

    def not_found(self, request, path: str, T: float, max_age: int = 60, edge_hit=False):
        self.stats.inc("404")
        self.rid += 1
        body = S3_ERR.format(code="NoSuchKey", msg="", rid=f"tx{self.rid:021x}-{int(T):08x}-mock-default").encode()
        h = self._base_headers(T, max_age, request.headers.get("Origin"))
        h["Content-Type"] = "application/json" if path.endswith(".json") else "application/xml"
        h["cdn-cache-status"] = "Hit from child" if edge_hit else "Miss from child"
        return web.Response(status=404, body=body, headers=h)

    def error(self, request, code: int, T: float):
        self.stats.inc(f"err{code}")
        origin = request.headers.get("Origin")
        h = self._base_headers(T, 0, origin)
        h["Cache-Control"] = "no-store"
        if code == 403:
            ref = f"{self.rng.getrandbits(28):x}&#46;{int(time.time())}&#46;{self.rng.getrandbits(32):x}"
            url = ("http://resultados.tse.jus.br" + request.path).replace(":", "&#58;").replace("/", "&#47;") \
                .replace(".", "&#46;").replace("-", "&#45;")
            body = AKAMAI_403.format(url=url, ref=ref).encode()
            h["Content-Type"] = "text/html"
            h["Server"] = "AkamaiGHost"
            h.pop("Access-Control-Allow-Origin", None)
        elif code == 429:
            body = b"Too Many Requests"
            h["Content-Type"] = "text/plain"
            h["Retry-After"] = "2"
            h["x-ratelimit-remaining"] = "0"
        elif code in (500, 503):
            c, m = ("InternalError", "We encountered an internal error. Please try again.") if code == 500 else \
                ("SlowDown", "Please reduce your request rate.")
            body = S3_ERR.format(code=c, msg=m, rid=f"tx{self.rid:021x}").encode()
            h["Content-Type"] = "application/xml"
        else:
            title = {502: "Bad Gateway", 504: "Gateway Timeout"}.get(code, "Error")
            body = (f"<HTML><HEAD><TITLE>{title}</TITLE></HEAD><BODY><H1>{title}</H1>"
                    f"The server did not respond in time.<P>Reference&#32;&#35;18&#46;{self.rid:x}</BODY></HTML>\n").encode()
            h["Content-Type"] = "text/html"
            h["Server"] = "AkamaiGHost"
        return web.Response(status=code, body=body, headers=h)

    def ok(self, request, res: Resource, T: float, edge_hit=False, max_age=None):
        origin = request.headers.get("Origin")
        h = self._base_headers(T, res.max_age if max_age is None else max_age, origin)
        h["ETag"] = res.etag
        h["Last-Modified"] = http_date(res.last_modified)
        h["Accept-Ranges"] = "bytes"
        h["cdn-cache-status"] = "Hit from child" if edge_hit else "Miss from child"
        inm = request.headers.get("If-None-Match")
        ims = request.headers.get("If-Modified-Since")
        not_mod = False
        if inm is not None:
            tags = [t.strip().removeprefix("W/") for t in inm.split(",")]
            not_mod = "*" in tags or res.etag in tags
        elif ims:
            try:
                from email.utils import parsedate_to_datetime
                ims_t = parsedate_to_datetime(ims).timestamp()
                not_mod = int(DAY0_UTC + res.last_modified) <= int(ims_t)
            except (TypeError, ValueError):
                not_mod = False
        if not_mod:
            self.stats.inc("304")
            return web.Response(status=304, headers=h)
        self.stats.inc("200")
        h["Content-Type"] = res.content_type
        if "gzip" in request.headers.get("Accept-Encoding", ""):
            h["Content-Encoding"] = "gzip"
            return web.Response(status=200, body=res.gz, headers=h)
        return web.Response(status=200, body=res.body, headers=h)

    # ----------------------------------------------------------- roteamento
    def route(self, path: str, T: float) -> tuple[Resource | None, str]:
        sim = self.sim
        m = R_AUX.match(path)
        if m:
            i = sim.find_idx(m[1], int(m[2]), int(m[3]), int(m[4]))
            return (sim.aux_json(i, T) if i is not None else None), "aux"
        m = R_FILE.match(path)
        if m:
            i = sim.find_idx(m[1], int(m[2]), int(m[3]), int(m[4]))
            if i is None or m[5] != self.tr.hashes[i] or m[6] != sim.bu_name(i):
                return None, "bu"
            return sim.bu_dat(i, T), "bu"
        m = R_BR_U.match(path)
        if m:
            return sim.u_json("br", 0, T, m[1] == "jws"), "br-u"
        m = R_BR_AB.match(path)
        if m:
            return sim.ab_json(None, T, m[1] == "jws"), "br-ab"
        m = R_UF_U.match(path)
        if m:
            if m[1] not in scenario.UF_IDX:
                return None, "uf-u"
            return sim.u_json("uf", scenario.UF_IDX[m[1]], T, m[2] == "jws"), "uf-u"
        m = R_UF_AB.match(path)
        if m:
            if m[1] not in scenario.UF_IDX:
                return None, "uf-ab"
            return sim.ab_json(m[1], T, m[2] == "jws"), "uf-ab"
        m = R_MU_U.match(path)
        if m:
            g = sim.mun_lookup.get((m[1], int(m[2])))
            return (sim.u_json("mu", g, T, m[3] == "jws") if g is not None else None), "mu-u"
        m = R_ZN_U.match(path)
        if m:
            g = sim.zon_lookup.get((m[1], int(m[2]), int(m[3])))
            return (sim.u_json("zona", g, T, m[4] == "jws") if g is not None else None), "zona-u"
        m = R_CS.match(path)
        if m:
            return sim.cs_json(m[1], T, m[2] == "jws"), "cs"
        m = R_CM.match(path)
        if m:
            r = sim.static["cm"]
            if m[1] == "jws":
                from .state import _jws, make_resource
                r = make_resource(_jws(r.body), r.last_modified)
            return r, "cm"
        if R_ELEC.match(path):
            return sim.static["elec"], "ele-c"
        return None, "other"

    def pick_fault(self, T: float) -> int | None:
        for b in self.bursts:
            if b.t0 <= T < b.t1 and (b.rate >= 1 or self.rng.random() < b.rate):
                return b.code
        if self.args.error_rate > 0 and self.rng.random() < self.args.error_rate:
            return self.rng.choice([429, 429, 500, 502, 503, 503, 504])
        return None

    async def handle(self, request: web.Request):
        path = request.path
        if path.startswith("/__mock/"):
            return self.control(request)
        T = self.clock.now()
        if request.method == "OPTIONS":
            h = {"Access-Control-Allow-Origin": request.headers.get("Origin", "*"),
                 "Access-Control-Allow-Methods": "GET",
                 "Access-Control-Max-Age": "600", "Vary": "Origin", "Date": http_date(T)}
            if request.headers.get("Access-Control-Request-Headers"):
                h["Access-Control-Allow-Headers"] = request.headers["Access-Control-Request-Headers"]
            return web.Response(status=200, headers=h)
        if request.method not in ("GET", "HEAD"):
            return web.Response(status=405, headers={"Allow": "GET, HEAD"})
        self.stats.inc("req")
        if self.args.latency_ms > 0:
            await asyncio.sleep(self.rng.expovariate(1000.0 / self.args.latency_ms))
            T = self.clock.now()
        code = self.pick_fault(T)
        if code:
            return self.error(request, code, T)
        e = self.edge.get(path)
        if e is not None:
            remaining = max(1, int(e[0] - time.time()))
            if e[1] == 404:
                return self.not_found(request, path, T, remaining, edge_hit=True)
            return self.ok(request, e[2], T, edge_hit=True, max_age=min(remaining, e[2].max_age))
        res, kind = self.route(path, T)
        self.stats.inc(f"k:{kind}")
        if res is None:
            self.edge.put(path, 404, None)
            return self.not_found(request, path, T)
        if self.edge.ttl_200 > 0 and kind not in ("bu",):
            self.edge.put(path, 200, res)
            return self.ok(request, res, T, max_age=max(1, min(res.max_age, int(self.edge.ttl_200))))
        return self.ok(request, res, T)

    # ----------------------------------------------------------- controle
    def status(self) -> dict:
        T = self.clock.now()
        el = time.time() - self.stats.t0
        return {"sim_time": f"{brt_date(int(T))} {brt_time(int(T))}", "sim_seconds": round(T, 1),
                "speed": self.clock.speed, "scenario": self.tr.scenario, "seed": self.tr.seed,
                "target_margin_pp": self.tr.target_margin_pp, "order": self.tr.order, "tick_s": self.sim.tick,
                "cs_mode": self.sim.cs_mode, "freeze_br": self.args.freeze_br,
                "bursts": [b.__dict__ for b in self.bursts], "error_rate": self.args.error_rate,
                "sticky_404_s": self.args.sticky_404, "edge_ttl_s": self.args.edge_ttl,
                "progress": self.sim.progress(T), "requests": self.stats.c,
                "uptime_s": round(el, 1)}

    def control(self, request):
        p = request.path
        if p == "/__mock/health":
            return web.Response(text="ok")
        if p == "/__mock/clock":
            q = request.query
            t = parse_hhmm(q["t"]) if "t" in q else self.clock.now()
            sp = float(q["speed"]) if "speed" in q else None
            self.clock.set(t, sp)
            self.sim.on_clock_jump(t)
            self.edge.d.clear()
            return web.json_response(self.status(), dumps=lambda o: orjson.dumps(o).decode())
        if p == "/__mock/status":
            return web.json_response(self.status(), dumps=lambda o: orjson.dumps(o).decode())
        return web.Response(status=404, text="unknown control endpoint")

    async def ticker(self, app):
        async def loop():
            while True:
                await asyncio.sleep(self.args.status_every)
                self.edge.purge()
                if self.args.quiet:
                    continue
                T = self.clock.now()
                pr = self.sim.progress(T)
                now = time.time()
                nreq = self.stats.c.get("req", 0)
                rps = (nreq - self.stats.last[1]) / max(now - self.stats.last[0], 1e-6)
                self.stats.last = (now, nreq)
                c = self.stats.c
                errs = sum(v for k, v in c.items() if k.startswith("err"))
                print(f"[mock {brt_time(int(T))} x{self.clock.speed:g}] "
                      f"totalizadas {pr['pct_totalizadas']:6.2f}% ({pr['secoes_totalizadas']}) "
                      f"aux {pr['aux_publicados']} | margem apurada {pr['margem_pp_apurada']:+.2f} p.p."
                      f"{' [BR CONGELADO]' if pr['br_congelado'] else ''} | req {nreq} ({rps:.0f}/s) "
                      f"200 {c.get('200', 0)} 304 {c.get('304', 0)} 404 {c.get('404', 0)} erros {errs}",
                      flush=True)
        task = asyncio.create_task(loop())
        yield
        task.cancel()


def build_parser():
    ap = argparse.ArgumentParser(prog="python -m mock.server", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default=scenario.DEFAULT_SCENARIO, choices=list(scenario.SCENARIOS))
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--target-margin-pp", "--target-margin", dest="target_margin_pp", type=float, default=None,
                    help="margem final Lula−Flávio em p.p. dos válidos (default do cenário)")
    ap.add_argument("--speed", type=float, default=10.0, help="segundos simulados por segundo real")
    ap.add_argument("--start", default="16:55", help="hora simulada (BRT, 25/10) no início; 00:00–11:59 = dia 26")
    ap.add_argument("--order", default="real", choices=list(scenario.ORDERS),
                    help="ordem de chegada entre UFs: real (1T 2026), nordeste-first (NE/N antes de S/SE), "
                         "random (curvas das UFs embaralhadas; ordem dentro da UF mantida)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--tick", type=int, default=60, help="granularidade (s simulados) da geração dos agregados")
    ap.add_argument("--cs-mode", choices=["none", "progressive"], default="none",
                    help="none: cs.json real e estático; progressive: seções ganham da/ha quando o aux sai")
    ap.add_argument("--cs-interval", type=int, default=120, help="regeração do cs progressivo (s simulados)")
    ap.add_argument("--cs-dir", default=str(prepare.RAW / "cs_2t"))
    ap.add_argument("--lag", default=scenario.DEFAULT_LAG,
                    help="atraso aux/BU após o recebimento, minutos log-uniformes: 'ne=2-20,sp=35-240,default=10-60'")
    ap.add_argument("--error-rate", type=float, default=0.0, help="fração de requisições com 429/5xx aleatório")
    ap.add_argument("--burst", action="append", metavar="CODE@HH:MM-HH:MM[@RATE]",
                    help="rajada de erros em hora simulada (repetível), ex.: 503@19:40-19:45@0.5")
    ap.add_argument("--burst-403", metavar="HH:MM-HH:MM", help="bloqueio Akamai 403 total na janela simulada")
    ap.add_argument("--freeze-br", metavar="HH:MM-HH:MM",
                    help="br-c0001-u e br-ab param de atualizar na janela (UFs seguem), como 19:14–20:04 no 1T")
    ap.add_argument("--sticky-404", type=float, default=60.0,
                    help="segundos REAIS em que um 404 'gruda' no edge (cache do CDN); 0 desliga")
    ap.add_argument("--edge-ttl", type=float, default=0.0,
                    help="segundos REAIS de cache de edge para respostas 200 (0 = sempre fresco)")
    ap.add_argument("--latency-ms", type=float, default=0.0, help="latência média extra (exponencial)")
    ap.add_argument("--no-busa", action="store_true", help="seções do Sistema de Apuração usam -bu.dat comum")
    ap.add_argument("--status-every", type=float, default=10.0)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--access-log", action="store_true")
    return ap


def make_app(args) -> tuple[web.Application, App]:
    t0 = time.time()
    tr = scenario.generate(args.scenario, args.seed, args.target_margin_pp, args.lag, order=args.order)
    p = scenario.write_truth(tr)
    sim = Sim(tr, tick=args.tick, cs_mode=args.cs_mode, cs_interval=args.cs_interval,
              cs_dir=args.cs_dir, busa=not args.no_busa, freeze_br=parse_window(args.freeze_br))
    a = App(args, tr, sim)
    if not args.quiet:
        print("\n".join(scenario.summary_lines(tr)))
        print(f"[mock] verdade por seção: {p}")
        print(f"[mock] pronto em {time.time() - t0:.1f}s; relógio {args.start} BRT x{args.speed:g}; "
              f"http://{args.host}:{args.port}/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json",
              flush=True)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", a.handle)
    app.cleanup_ctx.append(a.ticker)
    return app, a


def main(argv=None):
    args = build_parser().parse_args(argv)
    app, _ = make_app(args)
    web.run_app(app, host=args.host, port=args.port, access_log=None if not args.access_log else
                __import__("logging").getLogger("aiohttp.access"), print=None if args.quiet else print)


if __name__ == "__main__":
    sys.exit(main())
