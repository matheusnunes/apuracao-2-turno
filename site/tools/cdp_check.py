"""Verificação da tela num Chrome headless (CDP): erros de console, memória após GC ao longo do tempo e screenshots 1920×1080.

  uv run python site/tools/cdp_check.py --url 'http://127.0.0.1:8000/?live=data/live-fake/#tv=1' --minutes 10 \
      --shots 20,300,590 --out /tmp/cdp

Saída: <out>/report.json (amostras de heap, erros, estado da tela) e <out>/shot-<t>s.png.
Usa o Chrome instalado (macOS) com perfil temporário; não toca no perfil do usuário.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import itertools
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import aiohttp

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
STATE_JS = """(() => { const T = window.__tela; if (!T || !T.FEED) return null; const an = T.FEED.an;
  return { phase: an?.status?.phase, pct: an?.status?.pct_secoes, dv: T.FEED.dv, err: T.FEED.err, indexOk: T.FEED.indexOk,
           tv: T.S.tv.on ? `${T.S.tv.idx + 1}/${T.S.tv.views.length} ${T.S.tv.cur?.key}` : 'off', mode: T.S.mode,
           zoom: T.map && T.map() ? +T.map().getZoom().toFixed(2) : null, telaErrors: (window.__telaErrors || []).slice(-10),
           dom: document.getElementsByTagName('*').length,
           leftOverflow: (() => { const L = document.getElementById('left'); return L ? L.scrollHeight - L.clientHeight : null; })(),
           rightOverflow: (() => { const R = document.getElementById('right'); return R ? R.scrollHeight - R.clientHeight : null; })() }; })()"""


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


async def main_async(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="cdp-prof-")
    port = a.port
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                             f"--window-size={a.width},{a.height}", "--no-first-run", "--no-default-browser-check",
                             "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--hide-scrollbars", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {"url": a.url, "samples": [], "console_errors": [], "exceptions": [], "shots": []}
    try:
        async with aiohttp.ClientSession() as s:
            for _ in range(100):
                try:
                    async with s.get(f"http://127.0.0.1:{port}/json/list") as r:
                        targets = await r.json()
                    pages = [t for t in targets if t.get("type") == "page"]
                    if pages:
                        break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.2)
            ws = await s.ws_connect(pages[0]["webSocketDebuggerUrl"], max_msg_size=0)
            c = CDP(ws)
            rd = asyncio.create_task(c.reader())
            for m in ("Runtime.enable", "Log.enable", "Page.enable", "HeapProfiler.enable"):
                await c.send(m)
            await c.send("Emulation.setDeviceMetricsOverride", {"width": a.width, "height": a.height, "deviceScaleFactor": 1, "mobile": False})
            await c.send("Page.navigate", {"url": a.url})
            t0 = time.time()
            shots = sorted(int(x) for x in a.shots.split(",") if x.strip()) if a.shots else []
            next_sample = 0.0
            while time.time() - t0 < a.minutes * 60:
                el = time.time() - t0
                if shots and el >= shots[0]:
                    t = shots.pop(0)
                    r = await c.send("Page.captureScreenshot", {"format": "png"})
                    p = out / f"shot-{t:04d}s.png"
                    p.write_bytes(base64.b64decode(r["data"]))
                    report["shots"].append(str(p))
                if el >= next_sample:
                    await c.send("HeapProfiler.collectGarbage")
                    hu = await c.send("Runtime.getHeapUsage")
                    st = await c.send("Runtime.evaluate", {"expression": STATE_JS, "returnByValue": True})
                    smp = {"t": round(el), "heap_mb": round(hu["usedSize"] / 1e6, 1), "total_mb": round(hu["totalSize"] / 1e6, 1),
                           "state": st.get("result", {}).get("value")}
                    report["samples"].append(smp)
                    print(json.dumps(smp, ensure_ascii=False), flush=True)
                    next_sample += a.interval
                await asyncio.sleep(1)
            for e in c.events:
                m, p = e["method"], e.get("params", {})
                if m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
                    report["console_errors"].append(" ".join(str(x.get("value", x.get("description", ""))) for x in p.get("args", [])))
                elif m == "Runtime.exceptionThrown":
                    d = p.get("exceptionDetails", {})
                    report["exceptions"].append(d.get("exception", {}).get("description") or d.get("text"))
                elif m == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
                    report["console_errors"].append(f"[log] {p['entry'].get('text')} {p['entry'].get('url', '')}")
            rd.cancel()
            await ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(prof, ignore_errors=True)
    hs = [x["heap_mb"] for x in report["samples"]]
    if hs:
        report["heap_first_mb"], report["heap_last_mb"], report["heap_max_mb"] = hs[0], hs[-1], max(hs)
        tail = hs[len(hs) // 3:]
        report["heap_trend_mb_per_min"] = round((tail[-1] - tail[0]) / max(1e-9, (report["samples"][-1]["t"] - report["samples"][len(hs) // 3]["t"]) / 60), 2) if len(tail) > 1 else 0
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000/?live=data/live-fake/#tv=1")
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--shots", default="20")
    ap.add_argument("--out", default="cdp-out")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--port", type=int, default=9333)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
