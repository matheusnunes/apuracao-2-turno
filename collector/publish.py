"""Escrita atômica e arquivos versionados (contracts §4 e §6.1)."""

from __future__ import annotations

import os
import re
from pathlib import Path


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


_VERSIONED = {
    "agg": re.compile(r"^agg\.(\d+)\.json$"),
    "sections": re.compile(r"^sections\.(\d+)\.bin$"),
}


def versioned_name(kind: str, dv: int) -> str:
    return f"agg.{dv}.json" if kind == "agg" else f"sections.{dv}.bin"


def prune_versions(out_dir: Path, kind: str, keep: int) -> list[str]:
    """Apaga versões antigas de `kind`, mantendo as `keep` mais novas. Só toca nomes agg.<n>.json / sections.<n>.bin."""
    rx = _VERSIONED[kind]
    found = []
    for p in out_dir.iterdir():
        m = rx.match(p.name)
        if m:
            found.append((int(m.group(1)), p))
    found.sort(reverse=True)
    removed = []
    for _, p in found[keep:]:
        try:
            p.unlink()
            removed.append(p.name)
        except FileNotFoundError:
            pass
    return removed


def latest_version(out_dir: Path, kind: str) -> int:
    rx = _VERSIONED[kind]
    best = 0
    if out_dir.exists():
        for p in out_dir.iterdir():
            m = rx.match(p.name)
            if m:
                best = max(best, int(m.group(1)))
    return best
