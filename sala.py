"""Supervisor da sala de guerra: sobe coletor, analytics, servidor (e mock), reinicia o que cair.

Uso:
    uv run python sala.py --mode live
    uv run python sala.py --mode mock --scenario swing2022 --speed 20
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVE_DIR = ROOT / "site" / "data" / "live"
LOG_DIR = ROOT / "data" / "live" / "logs"
PIDFILE = LOG_DIR / "sala.pids.json"
MOCK_STATE_DIR = ROOT / "data" / "live-mock"
LIVE_STATE_DIR = ROOT / "data" / "live"
# Trecho da linha de comando que identifica cada filho (o PID pode ter sido reaproveitado).
CHILD_MARKERS = {"caffeinate": "caffeinate", "mock": "mock.server", "collector": "collector run",
                 "analytics": "analytics.run", "server": "serve.py"}
PY = sys.executable

BACKOFF_MAX_S = 30
HEALTHY_UPTIME_S = 300
HEALTH_EVERY_S = 30


@dataclass
class Child:
    name: str
    cmd: list[str]
    proc: subprocess.Popen | None = None
    started_at: float = 0.0
    backoff_s: float = 1.0
    next_start_at: float = 0.0
    restarts: int = 0
    log: object = field(default=None, repr=False)

    def start(self) -> None:
        if self.log is None:
            self.log = open(LOG_DIR / f"{self.name}.log", "ab", buffering=0)
        stamp = datetime.now().isoformat(timespec="seconds")
        self.log.write(f"\n=== {stamp} start: {' '.join(self.cmd)}\n".encode())
        self.proc = subprocess.Popen(
            self.cmd, cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.started_at = time.monotonic()
        print(f"[sala] {self.name} pid={self.proc.pid}", flush=True)

    def check(self, now: float) -> None:
        if self.proc is None:
            if now >= self.next_start_at:
                self.start()
            return
        code = self.proc.poll()
        if code is None:
            if now - self.started_at > HEALTHY_UPTIME_S:
                self.backoff_s = 1.0
            return
        self.restarts += 1
        print(f"[sala] {self.name} saiu (code={code}); reinicia em {self.backoff_s:.0f}s", flush=True)
        self.proc = None
        self.next_start_at = now + self.backoff_s
        self.backoff_s = min(self.backoff_s * 2, BACKOFF_MAX_S)

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _cmdline(pid: int) -> str:
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout


def kill_orphans() -> None:
    """Encerra uma execução anterior: o supervisor, se ainda vivo (senão ele reiniciaria os filhos),
    e os filhos que sobraram (ex.: kill -9 no supervisor)."""
    try:
        pids = json.loads(PIDFILE.read_text())
    except (OSError, ValueError):
        return
    sala = pids.pop("sala", None)
    if sala is None and "caffeinate" in pids:  # pidfile antigo: o caffeinate guarda o pid do supervisor (-w)
        parts = _cmdline(pids["caffeinate"]).split()
        sala = int(parts[-1]) if "caffeinate" in " ".join(parts) and parts[-1].isdigit() else None
    if sala and sala != os.getpid() and "sala.py" in _cmdline(sala):
        print(f"[sala] outro supervisor ainda rodando (pid={sala}); encerrando…", flush=True)
        os.kill(sala, signal.SIGTERM)
        for _ in range(60):
            time.sleep(0.5)
            if "sala.py" not in _cmdline(sala):
                break
        else:
            os.kill(sala, signal.SIGKILL)
    for name, pid in pids.items():
        try:
            if CHILD_MARKERS.get(name, "\0") in _cmdline(pid):
                os.killpg(pid, signal.SIGTERM)
                print(f"[sala] órfão de execução anterior encerrado: {name} pid={pid}", flush=True)
        except (ProcessLookupError, PermissionError):
            pass
    PIDFILE.unlink(missing_ok=True)


def write_pidfile(children: list[Child]) -> None:
    pids = {"sala": os.getpid()} | {c.name: c.proc.pid for c in children if c.proc is not None}
    tmp = PIDFILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(pids))
    os.replace(tmp, PIDFILE)


def build_children(args: argparse.Namespace) -> list[Child]:
    children: list[Child] = [Child("caffeinate", ["caffeinate", "-dimsu", "-w", str(os.getpid())])]
    # Estado do coletor separado por modo, para o ensaio não contaminar a noite real.
    collector = [PY, "-m", "collector", "run", "--source", args.mode, "--agg-rate", str(args.agg_rate)]
    if args.mode == "mock":
        children.append(Child("mock", [
            PY, "-m", "mock.server", "--scenario", args.scenario, "--speed", str(args.speed),
            "--start", args.start, "--order", args.order, "--port", str(args.mock_port),
        ] + args.mock_args))
        collector += ["--base-url", f"http://127.0.0.1:{args.mock_port}/oficial",
                      "--state-dir", str(MOCK_STATE_DIR)]
    if args.no_bu:
        collector.append("--no-bu")
    children += [
        Child("collector", collector + args.collector_args),
        Child("analytics", [PY, "-m", "analytics.run", "--live-dir", str(LIVE_DIR), "--interval", "20"]),
        Child("server", [PY, str(ROOT / "site" / "serve.py"), "--port", str(args.port)]),
    ]
    return children


def _age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def print_health(children: list[Child]) -> None:
    def load(name: str) -> dict:
        try:
            return json.loads((LIVE_DIR / name).read_text())
        except (OSError, ValueError):
            return {}

    man, ana = load("manifest.json"), load("analytics.json")
    cov, health = man.get("coverage", {}), man.get("health", {})
    status, proj = ana.get("status", {}), ana.get("projection", {})
    age = _age_s(man.get("generated_at"))
    if age is None:
        print(f"[sala {datetime.now():%H:%M:%S}] aguardando manifest…", flush=True)
        return
    tot = cov.get("secoes_totalizadas", 0) / max(cov.get("secoes_total", 1), 1) * 100
    restarts = ", ".join(f"{c.name}={c.restarts}" for c in children if c.restarts)
    line = (
        f"[sala {datetime.now():%H:%M:%S}] manifest v{man.get('version', '-')} "
        f"(há {age:.0f}s) | apurado {tot:.1f}% | BU {cov.get('secoes_com_bu', 0)} | "
        f"TSE {health.get('tse_br_dg_hg', '-')} br_frozen={health.get('br_frozen', '-')} | "
        f"fase {status.get('phase', '-')} | proj margem {proj.get('margin_votes', '-')} "
        f"faixa {proj.get('band_votes', '-')}"
    )
    if restarts:
        line += f" | reinícios: {restarts}"
    print(line, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["live", "mock"], default="live")
    ap.add_argument("--scenario", default="swing2022")
    ap.add_argument("--speed", type=float, default=20)
    ap.add_argument("--start", default="16:55")
    ap.add_argument("--order", default="real")
    ap.add_argument("--no-bu", action="store_true", help="coletor só com agregados (sem boletins)")
    ap.add_argument("--agg-rate", type=float, default=30,
                    help="teto da pista de agregados (req/s); 20 atrasa municípios pequenos no pico")
    ap.add_argument("--clean-live", action="store_true",
                    help="apaga site/data/live e, no modo live, arquiva o estado do coletor (só às 16:30; para reiniciar no meio da noite, rode sem)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--mock-port", type=int, default=8787)
    ap.add_argument("--mock-arg", dest="mock_args", action="append", default=[],
                    help="argumento extra repassado ao mock (repetível)")
    ap.add_argument("--collector-arg", dest="collector_args", action="append", default=[],
                    help="argumento extra repassado ao coletor (repetível)")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    kill_orphans()
    if args.mode == "mock":
        # Cada ensaio recomeça o relógio do mock: estado e saídas de um ensaio anterior
        # (talvez de outro cenário) contaminariam a análise.
        shutil.rmtree(MOCK_STATE_DIR, ignore_errors=True)
        args.clean_live = True
    if args.clean_live and LIVE_DIR.is_dir():
        for f in LIVE_DIR.iterdir():
            if f.is_file():
                f.unlink()
        print(f"[sala] {LIVE_DIR} limpo", flush=True)
    if args.clean_live and args.mode == "live":
        # A noite real começa com o coletor do zero; o estado de testes anteriores vai para old/.
        old = LIVE_STATE_DIR / "old" / datetime.now().strftime("%Y%m%d-%H%M%S")
        for f in LIVE_STATE_DIR.glob("state-live-*.sqlite*"):
            old.mkdir(parents=True, exist_ok=True)
            shutil.move(f, old / f.name)
            print(f"[sala] estado anterior do coletor arquivado: {old / f.name}", flush=True)
    children = build_children(args)
    stopping = False

    def on_signal(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    print(f"[sala] modo {args.mode} — tela em http://127.0.0.1:{args.port}/ — logs em {LOG_DIR}", flush=True)
    last_health = 0.0
    try:
        while not stopping:
            now = time.monotonic()
            pids_before = [c.proc.pid if c.proc else None for c in children]
            for child in children:
                child.check(now)
            if [c.proc.pid if c.proc else None for c in children] != pids_before:
                write_pidfile(children)
            if now - last_health >= HEALTH_EVERY_S:
                print_health(children)
                last_health = now
            time.sleep(1)
    finally:
        print("[sala] encerrando…", flush=True)
        for child in reversed(children):
            child.stop()
        PIDFILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
