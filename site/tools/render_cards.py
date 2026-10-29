"""Renderiza os cards de redes sociais (site/social/cards.html?n=1..7) em PNG 1080×1350 num Chrome headless (CDP).

  uv run python site/tools/render_cards.py                 # todos os cards -> data/social/card-N.png + textos.md
  uv run python site/tools/render_cards.py --cards 3,6     # só alguns

Precisa do servidor estático da pasta site/ rodando (por padrão em http://127.0.0.1:8000/).
Os números vêm de site/data/prorrog/{resumo,historia}.json na hora de renderizar: com dados novos, é só rodar de novo.
Também grava data/social/textos.md (legenda, alt text e fio), montado pelo próprio cards.html com os mesmos números.

Abre uma única instância do Chrome, com perfil temporário e porta de depuração própria (9450), e fecha no fim.
Sai com código 1 se algum card acusar texto vazando, erro de console ou falha ao carregar.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import itertools
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
W, H = 1080, 1350


class CDP:
    def __init__(self, ws):
        self.ws, self.ids, self.pending, self.events = ws, itertools.count(1), {}, []

    async def reader(self):
        async for msg in self.ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            d = json.loads(msg.data)
            if "id" in d and d["id"] in self.pending:
                self.pending.pop(d["id"]).set_result(d)
            elif "method" in d:
                self.events.append(d)

    async def send(self, method, params=None, timeout=60):
        i = next(self.ids)
        fut = asyncio.get_event_loop().create_future()
        self.pending[i] = fut
        await self.ws.send_str(json.dumps({"id": i, "method": method, "params": params or {}}))
        r = await asyncio.wait_for(fut, timeout)
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
        return r.get("result", {})

    async def eval(self, expr):
        r = await self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def drain_errors(c: CDP) -> list[str]:
    out = []
    for e in c.events:
        m, p = e["method"], e.get("params", {})
        if m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
            out.append(" ".join(str(x.get("value", x.get("description", ""))) for x in p.get("args", [])))
        elif m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            out.append(d.get("exception", {}).get("description") or d.get("text"))
        elif m == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
            out.append(f"[log] {p['entry'].get('text')} {p['entry'].get('url', '')}")
    c.events.clear()
    return out


async def render(a) -> int:
    out = (ROOT / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cards = sorted({int(x) for x in a.cards.split(",") if x.strip()}) if a.cards else list(range(1, 8))
    if port_in_use(a.port):
        print(f"porta {a.port} já está em uso: outra instância do Chrome ainda aberta? Feche-a ou use --port.")
        return 2
    prof = tempfile.mkdtemp(prefix="cards-prof-")
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={a.port}", f"--user-data-dir={prof}",
                             f"--window-size={W},{H}", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
                             "--force-color-profile=srgb", "--font-render-hinting=none", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report, textos, bad = [], None, 0
    try:
        async with aiohttp.ClientSession() as s:
            pages = []
            for _ in range(150):
                try:
                    async with s.get(f"http://127.0.0.1:{a.port}/json/list") as r:
                        pages = [t for t in await r.json() if t.get("type") == "page"]
                    if pages:
                        break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.2)
            if not pages:
                print("o Chrome headless não abriu a porta de depuração")
                return 2
            ws = await s.ws_connect(pages[0]["webSocketDebuggerUrl"], max_msg_size=0)
            c = CDP(ws)
            rd = asyncio.create_task(c.reader())
            for m in ("Runtime.enable", "Log.enable", "Page.enable"):
                await c.send(m)
            await c.send("Emulation.setDeviceMetricsOverride", {"width": W, "height": H, "deviceScaleFactor": 1, "mobile": False})
            for n in cards:
                nonce = uuid.uuid4().hex[:8]
                url = f"{a.url}?n={n}&r={nonce}"
                c.events.clear()
                await c.send("Page.navigate", {"url": url})
                st, t0 = None, time.time()
                while time.time() - t0 < a.timeout:
                    st = await c.eval(f"location.search.includes('{nonce}') && window.__card && window.__card.done ? window.__card : null")
                    if st:
                        break
                    await asyncio.sleep(0.15)
                if not st:
                    print(f"card {n}: não terminou de montar em {a.timeout} s")
                    bad += 1
                    continue
                await asyncio.sleep(0.25)  # um quadro a mais depois do ajuste dos números grandes
                shot = await c.send("Page.captureScreenshot", {"format": "png", "clip": {"x": 0, "y": 0, "width": W, "height": H, "scale": 1}})
                p = out / f"card-{n}.png"
                p.write_bytes(base64.b64decode(shot["data"]))
                errs = drain_errors(c)
                row = {"card": n, "png": str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p), "ok": bool(st.get("ok")) and not errs,
                       "previa": st.get("preview"), "cobertura": st.get("cov"), "fonte": st.get("font"), "conclusao": st.get("verdict"),
                       "problemas": (st.get("problems") or []) + errs}
                bad += not row["ok"]
                report.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                if textos is None:
                    textos = await c.eval("window.__textos ? window.__textos.md : null")
            rd.cancel()
            await ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(prof, ignore_errors=True)
    if textos and not a.no_text:
        (out / "textos.md").write_text(textos, encoding="utf-8")
        print(f"textos: {out / 'textos.md'}")
    print(f"{sum(r['ok'] for r in report)}/{len(cards)} cards ok em {out}")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000/social/cards.html")
    ap.add_argument("--cards", default="", help="lista, ex.: 1,3,7 (padrão: todos)")
    ap.add_argument("--out", default="data/social", help="pasta de saída (relativa à raiz do repositório)")
    ap.add_argument("--port", type=int, default=9450, help="porta de depuração do Chrome headless")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--no-text", action="store_true", help="não regrava data/social/textos.md")
    raise SystemExit(asyncio.run(render(ap.parse_args())))


if __name__ == "__main__":
    main()
