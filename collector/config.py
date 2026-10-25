"""Configuração do coletor e construção das URLs do TSE."""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

UF_REGIAO = {
    "ac": "N", "am": "N", "ap": "N", "pa": "N", "ro": "N", "rr": "N", "to": "N",
    "al": "NE", "ba": "NE", "ce": "NE", "ma": "NE", "pb": "NE", "pe": "NE",
    "pi": "NE", "rn": "NE", "se": "NE",
    "df": "CO", "go": "CO", "ms": "CO", "mt": "CO",
    "es": "SE", "mg": "SE", "rj": "SE", "sp": "SE",
    "pr": "S", "rs": "S", "sc": "S",
    "zz": "ZZ",
}
# Ordem canônica (contracts §2): UFs minúsculas em ordem alfabética, zz no fim.
ALL_UFS = sorted(u for u in UF_REGIAO if u != "zz") + ["zz"]
UF_ORDER = {u: i for i, u in enumerate(ALL_UFS)}

BRT = dt.timezone(dt.timedelta(hours=-3))
# Dia de referência para t_recv_min (minutos desde 00:00 BRT deste dia).
ELECTION_DAY = {6258: dt.date(2026, 10, 25), 6257: dt.date(2026, 10, 4)}

USER_AGENT = "apuracao-sala-de-guerra/1.0 (uso pessoal)"


@dataclass
class Config:
    base_url: str = "https://resultados.tse.jus.br/oficial"
    ciclo: str = "ele2026"
    eleicao: int = 6258
    pleito: int = 3221
    cand_l: int = 13
    cand_o: int = 22
    ufs: list[str] = field(default_factory=lambda: list(ALL_UFS))
    muns: set[str] | None = None          # filtro de teste: códigos mun5
    # pista AGG (agregados: br/UF/município/zona)
    agg_concurrency: int = 6
    agg_rate: float = 20.0                # teto do AIMD da pista AGG (req/s)
    # pista BU (cs.json, aux, .bu.dat) - só a sobra, amostra
    bu_concurrency: int = 6
    bu_rate: float = 10.0                 # teto do AIMD da pista BU (req/s)
    probe_rate: float = 8.0               # teto das sondagens especulativas de aux (estratégia B), req/s
    probe_factor: float = 1.5             # por zona: no máx. (st - BUs conhecidos) x fator seções em sondagem
    probe_min_interval: float = 90.0      # nunca ressondar a mesma URL antes disso
    probe_max_interval: float = 600.0
    start_rate: float | None = None
    request_budget: int | None = None     # teto de requisições nesta execução (testes)
    timeout: float = 20.0
    retries: int = 3
    l0_interval: float = 30.0             # br u + br ab
    uf_interval: float = 60.0             # varredura das 28 UF ab (+ UF u)
    mun_min_interval: float = 15.0
    zone_min_interval: float = 30.0
    cs_interval: float = 120.0
    cs_idle_interval: float = 300.0
    cs_trust_window: float = 300.0        # cs com mudança recente => estratégia A dispensa B
    publish_interval: float = 15.0        # heartbeat do manifest (e ritmo do sections.bin)
    publish_min_gap: float = 5.0          # agg mudou => publica após no mín. isto desde a última
    keep_versions: int = 3
    fail_backoff: float = 30.0            # recuo por URL de município/zona após 403/429/erro: 30, 60, 120… (teto 300)
    oldest_share: float = 0.25            # fatia do lote AGG para os pendentes mais antigos (espera limitada)
    zone_share: float = 0.4               # fatia mínima de cada lote AGG reservada a arquivos de zona
    br_frozen_after: float = 120.0        # br_frozen só se o BR (idg/dg/hg) não muda há isto e as UFs estão à frente
    strategy: str = "auto"                # auto | cs | zonas
    fmt: str = "auto"                     # json | jws | auto
    bu_enabled: bool = True
    source: str | None = None             # live | mock | replay (default: pela URL)
    day0: dt.date | None = None
    state_dir: Path = ROOT / "data" / "live"
    out_dir: Path = ROOT / "site" / "data" / "live"
    index_path: Path = ROOT / "data" / "base" / "secoes_index.csv"
    base_secoes_path: Path = ROOT / "data" / "base" / "secoes.parquet"
    exit_when_done: bool = False
    max_runtime: float | None = None
    reset: bool = False
    log_level: str = "INFO"
    http_log: Path | None = None          # uma linha por requisição (diagnóstico)
    user_agent: str = USER_AGENT

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        self.ufs = [u.lower() for u in self.ufs]
        if self.source is None:
            host = urlparse(self.base_url).hostname or ""
            self.source = "live" if host.endswith("tse.jus.br") else "mock"
        if self.day0 is None:
            self.day0 = ELECTION_DAY.get(self.eleicao, dt.date(2026, 10, 25))

    # ---------------- URLs ----------------
    @property
    def ele6(self) -> str:
        return f"{self.eleicao:06d}"

    @property
    def pl6(self) -> str:
        return f"{self.pleito:06d}"

    def _dados(self, uf: str) -> str:
        return f"{self.base_url}/{self.ciclo}/{self.eleicao}/dados/{uf}"

    def url_br_u(self, ext="json") -> str:
        return f"{self._dados('br')}/br-c0001-e{self.ele6}-u.{ext}"

    def url_br_ab(self, ext="json") -> str:
        return f"{self._dados('br')}/br-e{self.ele6}-ab.{ext}"

    def url_uf_ab(self, uf: str, ext="json") -> str:
        return f"{self._dados(uf)}/{uf}-e{self.ele6}-ab.{ext}"

    def url_uf_u(self, uf: str, ext="json") -> str:
        return f"{self._dados(uf)}/{uf}-c0001-e{self.ele6}-u.{ext}"

    def url_mun_u(self, uf: str, mun5: str, ext="json") -> str:
        return f"{self._dados(uf)}/{uf}{mun5}-c0001-e{self.ele6}-u.{ext}"

    def url_zona_u(self, uf: str, mun5: str, z4: str, ext="json") -> str:
        return f"{self._dados(uf)}/{uf}{mun5}-z{z4}-c0001-e{self.ele6}-u.{ext}"

    def url_cs(self, uf: str) -> str:
        return f"{self.base_url}/{self.ciclo}/arquivo-urna/{self.pleito}/config/{uf}/{uf}-p{self.pl6}-cs.json"

    def _secdir(self, uf, mun5, z4, s4) -> str:
        return f"{self.base_url}/{self.ciclo}/arquivo-urna/{self.pleito}/dados/{uf}/{mun5}/{z4}/{s4}"

    def url_aux(self, uf, mun5, z4, s4) -> str:
        return f"{self._secdir(uf, mun5, z4, s4)}/p{self.pl6}-{uf}-m{mun5}-z{z4}-s{s4}-aux.json"

    def url_file(self, uf, mun5, z4, s4, hash_, nm) -> str:
        return f"{self._secdir(uf, mun5, z4, s4)}/{hash_}/{nm}"

    @property
    def db_path(self) -> Path:
        return self.state_dir / f"state-{self.source}-e{self.eleicao}-p{self.pleito}.sqlite"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "collector.log"

    def describe(self) -> dict:
        return {
            "base_url": self.base_url, "eleicao": self.eleicao, "pleito": self.pleito,
            "cand": {"l": self.cand_l, "o": self.cand_o}, "ufs": self.ufs,
            "muns": sorted(self.muns) if self.muns else None, "source": self.source,
            "agg": {"concurrency": self.agg_concurrency, "rate": self.agg_rate},
            "bu": {"concurrency": self.bu_concurrency, "rate": self.bu_rate, "probe_rate": self.probe_rate},
            "strategy": self.strategy,
            "fmt": self.fmt, "bu_enabled": self.bu_enabled, "day0": str(self.day0),
            "state_db": str(self.db_path), "out_dir": str(self.out_dir),
        }


def add_run_args(ap: argparse.ArgumentParser) -> None:
    d = Config.__dataclass_fields__
    g = ap.add_argument_group("fonte")
    g.add_argument("--base-url", default=d["base_url"].default,
                   help="raiz até /oficial (mock: http://localhost:8787/oficial)")
    g.add_argument("--ciclo", default=d["ciclo"].default)
    g.add_argument("--eleicao", type=int, default=6258, help="6258 = 2T; 6257 = 1T (teste)")
    g.add_argument("--pleito", type=int, default=3221, help="3221 = 2T; 3220 = 1T (teste)")
    g.add_argument("--cand-l", type=int, default=13)
    g.add_argument("--cand-o", type=int, default=22)
    g.add_argument("--ufs", default="all", help="lista separada por vírgula (ex.: ac,rr) ou 'all'")
    g.add_argument("--muns", default=None, help="filtro de teste: códigos de município (5 dígitos), vírgula")
    g.add_argument("--source", choices=["live", "mock", "replay"], default=None)
    g.add_argument("--day0", default=None, help="AAAA-MM-DD de referência para t_recv_min")
    g = ap.add_argument_group("polidez")
    g.add_argument("--agg-concurrency", type=int, default=d["agg_concurrency"].default)
    g.add_argument("--agg-rate", type=float, default=d["agg_rate"].default, help="teto AIMD da pista AGG, req/s")
    g.add_argument("--bu-concurrency", type=int, default=d["bu_concurrency"].default)
    g.add_argument("--bu-rate", type=float, default=d["bu_rate"].default, help="teto AIMD da pista BU, req/s")
    g.add_argument("--concurrency", type=int, default=None,
                   help="atalho: divide igualmente entre as pistas (ex.: 4 -> 2+2)")
    g.add_argument("--start-rate", type=float, default=None)
    g.add_argument("--request-budget", type=int, default=None, help="teto de requisições nesta execução")
    g.add_argument("--probe-rate", type=float, default=d["probe_rate"].default,
                   help="teto de sondagens de aux da estratégia B (req/s)")
    g.add_argument("--probe-factor", type=float, default=d["probe_factor"].default)
    g.add_argument("--zone-share", type=float, default=d["zone_share"].default,
                   help="fração mínima do lote AGG reservada a zonas (anti-inanição)")
    g.add_argument("--oldest-share", type=float, default=d["oldest_share"].default,
                   help="fração do lote AGG para os pendentes mais antigos (espera limitada)")
    g.add_argument("--probe-min-interval", type=float, default=d["probe_min_interval"].default)
    g.add_argument("--timeout", type=float, default=d["timeout"].default)
    g = ap.add_argument_group("cadência")
    for name in ("l0_interval", "uf_interval", "mun_min_interval",
                 "zone_min_interval", "cs_interval", "cs_idle_interval", "cs_trust_window",
                 "publish_interval", "publish_min_gap"):
        g.add_argument("--" + name.replace("_", "-"), type=float, default=d[name].default)
    g = ap.add_argument_group("modo")
    g.add_argument("--strategy", choices=["auto", "cs", "zonas"], default="auto")
    g.add_argument("--format", dest="fmt", choices=["auto", "json", "jws"], default="auto")
    g.add_argument("--no-bu", dest="bu_enabled", action="store_false", help="só agregados")
    g.add_argument("--state-dir", type=Path, default=d["state_dir"].default)
    g.add_argument("--out-dir", type=Path, default=d["out_dir"].default)
    g.add_argument("--index", dest="index_path", type=Path, default=d["index_path"].default)
    g.add_argument("--exit-when-done", action="store_true",
                   help="(teste) sai quando todas as seções do escopo têm BU e os agregados estão finais")
    g.add_argument("--max-runtime", type=float, default=None, help="(teste) segundos")
    g.add_argument("--reset", action="store_true", help="apaga o estado sqlite desta fonte/eleição")
    g.add_argument("--log-level", default="INFO")
    g.add_argument("--http-log", type=Path, default=None, help="grava uma linha por requisição (diagnóstico)")


def config_from_args(a: argparse.Namespace) -> Config:
    ufs = list(ALL_UFS) if a.ufs in (None, "", "all") else [u.strip().lower() for u in a.ufs.split(",") if u.strip()]
    bad = [u for u in ufs if u not in UF_REGIAO]
    if bad:
        raise SystemExit(f"UF desconhecida: {bad}")
    ufs = sorted(set(ufs), key=UF_ORDER.get)
    muns = {f"{int(m):05d}" for m in a.muns.split(",")} if a.muns else None
    agg_c, bu_c = a.agg_concurrency, a.bu_concurrency
    if a.concurrency:
        agg_c = max(1, a.concurrency // 2)
        bu_c = max(1, a.concurrency - agg_c)
    return Config(
        base_url=a.base_url, ciclo=a.ciclo, eleicao=a.eleicao, pleito=a.pleito,
        cand_l=a.cand_l, cand_o=a.cand_o, ufs=ufs, muns=muns,
        agg_concurrency=agg_c, agg_rate=a.agg_rate, bu_concurrency=bu_c, bu_rate=a.bu_rate,
        start_rate=a.start_rate, request_budget=a.request_budget, probe_rate=a.probe_rate,
        probe_factor=a.probe_factor, probe_min_interval=a.probe_min_interval, zone_share=a.zone_share, oldest_share=a.oldest_share,
        timeout=a.timeout, l0_interval=a.l0_interval, uf_interval=a.uf_interval,
        mun_min_interval=a.mun_min_interval,
        zone_min_interval=a.zone_min_interval, cs_interval=a.cs_interval,
        cs_idle_interval=a.cs_idle_interval, cs_trust_window=a.cs_trust_window,
        publish_interval=a.publish_interval, publish_min_gap=a.publish_min_gap, strategy=a.strategy, fmt=a.fmt,
        bu_enabled=a.bu_enabled, source=a.source,
        day0=dt.date.fromisoformat(a.day0) if a.day0 else None,
        state_dir=a.state_dir, out_dir=a.out_dir, index_path=a.index_path,
        exit_when_done=a.exit_when_done, max_runtime=a.max_runtime, reset=a.reset,
        log_level=a.log_level, http_log=a.http_log,
    )
