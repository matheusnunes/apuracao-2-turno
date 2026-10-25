"""HTTP polido em duas pistas independentes (contracts §6).

Cada pista (`Lane`) tem cliente HTTP próprio (HTTP/2, conexões próprias),
balde de tokens próprio com AIMD, concorrência própria e política própria
para 403/429 (ver `Gate`): falhas isoladas só recuam a URL; a pista só pausa numa
"tempestade" (várias URLs distintas e fração alta de 403/429 na janela de 60 s).
  - AGG: pausas 15 -> 30 -> 60 s (teto); br/UF furam a pausa após 60 s.
  - BU:  pausas 60 -> 120 -> 300 -> 600 s enquanto as tempestades se repetem; a AGG segue.
Dentro de cada pista há prioridade (menor = mais urgente):
  AGG: 0 br, 1 UF, 2 município/zona     BU: 3 cs.json, 4 .bu.dat, 5 aux conhecido, 6 aux sondagem
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import random
import time
from collections import Counter, deque
from dataclasses import dataclass

import httpx

log = logging.getLogger("collector.net")

P_BR, P_UF, P_AGG = 0, 1, 2
P_CS, P_BU, P_AUX, P_PROBE = 3, 4, 5, 6


class BudgetExhausted(Exception):
    pass


class Gate:
    """Concorrência + balde de tokens (AIMD) com fila de prioridade.

    Política de congestionamento por FRAÇÃO de falhas numa janela deslizante (60 s), não por
    evento isolado: 2% de 5xx/429 aleatórios (plausíveis na Akamai) não derrubam a taxa nem pausam.
      - 5xx/timeout/conexão ("soft"): taxa x0,7 só se >= 5% da janela falhou (mín. 3 falhas), 1x a cada 5 s.
      - 403/429 ("hard") isolado: nada na pista (o chamador recua só aquela URL); se >= 5% da janela
        for hard, taxa x0,7.
      - "Tempestade": >= storm_distinct URLs distintas com hard E >= storm_frac da janela => pausa a pista,
        escalonando por `pause_steps` enquanto as tempestades se repetem; o nível zera após `level_reset` s
        sem tempestade. AGG: 15 -> 30 -> 60 s; BU: 60 -> 120 -> 300 -> 600 s.
      - Pedidos de prioridade <= bypass_prio (br/UF na AGG) que esperaram max(pause_steps) furam a pausa.
    """

    def __init__(self, name: str, concurrency: int, max_rate: float, min_rate: float = 0.5,
                 start_rate: float | None = None, hard_policy: str = "escalate",
                 pause_steps: tuple | None = None, storm_distinct: int | None = None,
                 storm_frac: float = 0.2, window: float = 60.0, level_reset: float | None = None,
                 bypass_prio: int = P_UF, pause_cap: float | None = None):
        self.name = name
        self.hard_policy = hard_policy      # "escalate" (AGG) | "fixed600" (BU; nome histórico)
        bu = hard_policy == "fixed600"
        self.pause_steps = tuple(pause_steps or ((60.0, 120.0, 300.0, 600.0) if bu else (15.0, 30.0, 60.0)))
        if pause_cap is not None:
            self.pause_steps = tuple(min(x, pause_cap) for x in self.pause_steps)
        self.pause_cap = max(self.pause_steps)
        self.storm_distinct = storm_distinct or (5 if bu else 3)
        self.storm_frac = storm_frac
        self.window = window
        self.level_reset = level_reset if level_reset is not None else (600.0 if bu else 300.0)
        self.bypass_prio = bypass_prio if not bu else -1
        self._win: deque = deque()          # (monotonic, cls, url) de cada resposta
        self._n = Counter()                 # contagens por classe dentro da janela
        self.lane_pauses = 0
        self.concurrency = max(1, concurrency)
        self.max_rate = max_rate
        self.min_rate = min(min_rate, max_rate)
        self.rate = start_rate if start_rate else max(self.min_rate, max_rate / 2)
        self.tokens = 1.0
        self._last_refill = time.monotonic()
        self.in_flight = 0
        self._heap: list = []
        self._seq = itertools.count()
        self._wake: asyncio.Event | None = None
        self.paused_until = 0.0             # monotonic
        self.paused_until_wall = 0.0
        self.pause_level = 0
        self._last_storm = float("-inf")
        self._last_decrease = float("-inf")
        self._task: asyncio.Task | None = None

    @property
    def burst(self) -> float:
        return max(1.0, min(self.rate / 2, 8.0))

    def start(self):
        if self._wake is None:
            self._wake = asyncio.Event()
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._dispatch(), name=f"gate-{self.name}")

    async def acquire(self, prio: int):
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._heap, (prio, next(self._seq), time.monotonic(), fut))
        self._wake.set()
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release()
            else:
                fut.cancel()
            raise

    def release(self):
        self.in_flight -= 1
        self._wake.set()

    def waiting(self) -> int:
        return sum(1 for x in self._heap if not x[3].done())

    async def _dispatch(self):
        while True:
            self._wake.clear()
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self._last_refill) * self.rate)
            self._last_refill = now
            while self._heap and self._heap[0][3].done():
                heapq.heappop(self._heap)
            if not self._heap:
                await self._wake.wait()
                continue
            if now < self.paused_until:
                hp, _, t_enq, _ = self._heap[0]
                bypass_at = t_enq + self.pause_cap if hp <= self.bypass_prio else float("inf")
                if now < bypass_at:
                    try:
                        await asyncio.wait_for(self._wake.wait(),
                                               timeout=max(0.001, min(self.paused_until, bypass_at) - now))
                    except asyncio.TimeoutError:
                        pass
                    continue
                # br/UF esperando há >= pause_cap: fura a pausa (ainda respeita taxa e concorrência)
            if self.in_flight >= self.concurrency:
                await self._wake.wait()
                continue
            if self.tokens < 1.0:
                await asyncio.sleep(max(0.002, (1.0 - self.tokens) / self.rate))
                continue
            _, _, _, fut = heapq.heappop(self._heap)
            self.tokens -= 1.0
            self.in_flight += 1
            fut.set_result(None)

    # ---- AIMD por fração de falhas ----
    def _trim(self, now: float):
        while self._win and self._win[0][0] < now - self.window:
            _, c, _ = self._win.popleft()
            self._n[c] -= 1

    def _pause(self, now: float, pause: float):
        self.paused_until = max(self.paused_until, now + pause)
        self.paused_until_wall = time.time() + (self.paused_until - now)
        self.lane_pauses += 1

    def _decrease(self, now: float, why: str):
        if now - self._last_decrease > 5.0:
            self.rate = max(self.min_rate, self.rate * 0.7)
            self._last_decrease = now
            log.info("[%s] %s: taxa -> %.2f req/s", self.name, why, self.rate)

    def on_result(self, cls: str, url: str | None = None, status: int | None = None,
                  retry_after: float | None = None) -> bool:
        """cls: 'ok' (200/304/404), 'soft' (5xx/timeout/conexão), 'hard' (403/429).
        Devolve True se a pista inteira foi pausada agora."""
        now = time.monotonic()
        self._win.append((now, cls, url))
        self._n[cls] += 1
        self._trim(now)
        if cls == "ok":
            self.rate = min(self.max_rate, self.rate + 0.05)
            if self.pause_level and now - self._last_storm > self.level_reset:
                self.pause_level = 0
            return False
        n = max(1, len(self._win))
        if cls == "soft":
            if self._n["soft"] >= 3 and self._n["soft"] / n >= 0.05:
                self._decrease(now, f"5xx/timeout em {self._n['soft']}/{n} da janela")
            return False
        n_hard = self._n["hard"]
        frac = n_hard / n
        distinct = len({u for _, c, u in self._win if c == "hard"})
        storm = n_hard >= 3 and distinct >= self.storm_distinct and frac >= self.storm_frac
        if not storm:
            if n_hard >= 3 and frac >= 0.05:
                self._decrease(now, f"403/429 em {n_hard}/{n} da janela")
            return False
        if now < self.paused_until:
            return False
        if now - self._last_storm > self.level_reset:
            self.pause_level = 0
        self.pause_level = min(self.pause_level + 1, len(self.pause_steps))
        pause = self.pause_steps[self.pause_level - 1]
        if retry_after:
            pause = max(pause, min(retry_after, self.pause_cap))
        self._pause(now, pause)
        self.rate = max(self.min_rate, self.rate * 0.5)
        self._last_storm = now
        self._last_decrease = now
        self._win.clear()
        self._n.clear()
        log.warning("[%s] 403/429 em %d URLs distintas (%d/%d da janela): pista pausada %.0fs (nível %d), "
                    "taxa -> %.2f req/s", self.name, distinct, n_hard, n, pause, self.pause_level, self.rate)
        return True

    # compatibilidade (testes/simulações antigas)
    def on_success(self):
        self.on_result("ok")

    def on_congestion(self, hard: bool = False, retry_after: float | None = None, url: str | None = None,
                      status: int | None = None) -> bool:
        return self.on_result("hard" if hard else "soft", url=url, status=status, retry_after=retry_after)

    def paused_for(self) -> float:
        return max(0.0, self.paused_until - time.monotonic())


class Metrics:
    def __init__(self):
        self.events: deque = deque()     # (t_wall, kind, outcome)
        self.errors: deque = deque()     # t_wall
        self.counts: Counter = Counter() # (kind, outcome) acumulado nesta execução
        self.total = 0
        self.last_ok: float | None = None
        self.bytes = 0

    def record(self, kind: str, outcome: str, ok: bool, nbytes: int = 0):
        t = time.time()
        self.total += 1
        self.events.append((t, kind, outcome))
        self.counts[(kind, outcome)] += 1
        self.bytes += nbytes
        if ok:
            self.last_ok = t
        else:
            self.errors.append(t)
        self._trim(t)

    def _trim(self, t):
        while self.events and self.events[0][0] < t - 300:
            self.events.popleft()
        while self.errors and self.errors[0] < t - 300:
            self.errors.popleft()

    def errors_5min(self) -> int:
        self._trim(time.time())
        return len(self.errors)

    def req_rate(self, window: float = 60.0) -> float:
        t = time.time()
        self._trim(t)
        n = 0
        for e in reversed(self.events):
            if e[0] < t - window:
                break
            n += 1
        return n / window

    def summary(self) -> dict:
        out: dict = {}
        for (k, o), n in sorted(self.counts.items()):
            out.setdefault(k, {})[o] = n
        return out


@dataclass
class Resp:
    status: int                 # 0 = falha de rede após retries; -1 = pulado (intervalo mínimo)
    body: bytes | None = None
    etag: str | None = None
    lm: str | None = None
    error: str | None = None
    retry_after: float | None = None   # já limitado ao teto de pausa da pista

    @property
    def ok(self) -> bool:
        return self.status == 200


class Budget:
    """Teto global de requisições nesta execução (somadas as pistas)."""

    def __init__(self, limit: int | None):
        self.limit = limit
        self.used = 0

    def take(self):
        if self.limit is not None and self.used >= self.limit:
            raise BudgetExhausted()
        self.used += 1


class Lane:
    """Uma pista: cliente HTTP próprio + Gate próprio + métricas próprias."""

    def __init__(self, name: str, cfg, gate: Gate, budget: Budget, validators: dict, on_validator=None,
                 retries: int = 3):
        self.name = name
        self.cfg = cfg
        self.gate = gate
        self.budget = budget
        self.metrics = Metrics()
        self.validators = validators          # url -> (etag, lm), compartilhado (URLs são disjuntas)
        self.on_validator = on_validator      # callback(url, etag, lm)
        self.retries = retries
        self.last_req: dict[str, float] = {}  # url -> monotonic (só condicionais)
        self.min_url_interval = 12.0          # rede de segurança: CDN guarda ~60 s
        self._consec_conn_err = 0
        self.client: httpx.AsyncClient | None = None
        self._hlog = open(cfg.http_log, "a", buffering=1) if getattr(cfg, "http_log", None) else None

    def start(self):
        self.gate.start()
        if self.client is None:
            self.client = self._new_client()

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            http2=True,
            headers={"User-Agent": self.cfg.user_agent, "Accept-Encoding": "gzip"},
            timeout=httpx.Timeout(self.cfg.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=self.gate.concurrency + 2,
                                max_keepalive_connections=self.gate.concurrency + 2),
            follow_redirects=False,
        )

    def commit_validator(self, url: str, etag, lm):
        self.validators[url] = (etag, lm)
        if self.on_validator:
            self.on_validator(url, etag, lm)

    async def close(self):
        if self.client is not None:
            await self.client.aclose()
        if self._hlog is not None:
            self._hlog.close()
            self._hlog = None

    async def _recycle(self):
        log.warning("[%s] muitos erros de conexão seguidos: recriando cliente HTTP", self.name)
        old = self.client
        self.client = self._new_client()
        try:
            await old.aclose()
        except Exception:  # noqa: BLE001
            pass

    async def get(self, url: str, *, prio: int, kind: str, conditional: bool = False,
                  retries: int | None = None, save_validator: bool = True) -> Resp:
        """GET com portão da pista. `save_validator=False`: o chamador grava o validador
        (via `commit_validator`) só depois de aplicar o conteúdo ao estado."""
        if conditional:
            last = self.last_req.get(url)
            if last is not None and time.monotonic() - last < self.min_url_interval:
                return Resp(status=-1)
        attempts = (self.retries if retries is None else retries) + 1
        last_err = None
        status = 0
        for attempt in range(attempts):
            self.budget.take()
            headers = {}
            if conditional and url in self.validators:
                etag, lm = self.validators[url]
                if etag:
                    headers["If-None-Match"] = etag
                if lm:
                    headers["If-Modified-Since"] = lm
            await self.gate.acquire(prio)
            status = 0
            outcome = ""
            body = None
            rh = None
            try:
                if conditional:
                    self.last_req[url] = time.monotonic()
                r = await self.client.get(url, headers=headers)
                status = r.status_code
                rh = r.headers
                if status == 200:
                    body = r.content
                outcome = str(status)
                self._consec_conn_err = 0
            except httpx.TimeoutException as e:
                outcome, last_err = "timeout", repr(e)[:200]
            except (httpx.TransportError, OSError) as e:
                outcome, last_err = "conn", repr(e)[:200]
                self._consec_conn_err += 1
            except Exception as e:  # noqa: BLE001  (erros internos do h2 etc.)
                outcome, last_err = "exc", repr(e)[:200]
                self._consec_conn_err += 1
            finally:
                self.gate.release()
            if self._hlog is not None:
                self._hlog.write(f"{time.time():.3f}\t{self.name}\t{kind}\t{outcome}\t{url}\n")

            if status in (200, 304, 404):
                self.metrics.record(kind, outcome, ok=True, nbytes=len(body or b""))
                self.gate.on_result("ok")
                etag = rh.get("etag") if rh is not None else None
                lm = rh.get("last-modified") if rh is not None else None
                if status == 200 and conditional and (etag or lm) and save_validator:
                    self.commit_validator(url, etag, lm)
                return Resp(status=status, body=body, etag=etag, lm=lm)

            self.metrics.record(kind, outcome or "err", ok=False)
            if status in (403, 429):
                ra = None
                try:
                    if rh is not None and rh.get("retry-after"):
                        ra = float(rh.get("retry-after"))
                except ValueError:
                    ra = None
                self.gate.on_result("hard", url=url, status=status, retry_after=ra)
                return Resp(status=status, error=f"HTTP {status}",
                            retry_after=min(ra, self.gate.pause_cap) if ra else None)
            if status and status < 500:
                return Resp(status=status, error=f"HTTP {status}")
            # 5xx / timeout / conexão: tenta de novo (a taxa só cai se a fração de falhas subir)
            self.gate.on_result("soft", url=url, status=status)
            if self._consec_conn_err >= 20:
                self._consec_conn_err = 0
                await self._recycle()
            last_err = last_err or f"HTTP {status}"
            if attempt + 1 < attempts:
                await asyncio.sleep(min(30.0, 0.5 * 2 ** attempt) * random.uniform(0.5, 1.5))
        return Resp(status=status or 0, error=last_err)
