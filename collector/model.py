"""Modelo em memória: seções, zonas, municípios, UFs e a fila round-robin (amplitude)."""

from __future__ import annotations

from collections import deque

from .state import S_NEW


class Sec:
    __slots__ = ("sid", "key", "uf", "mun", "zona", "secao", "zone", "status", "cs_dh", "aux_dh",
                 "hash", "nm", "dr", "hr", "probes", "next_probe", "has_bu", "err", "queued",
                 "want_known", "prior", "t")

    def __init__(self, sid, key, uf, mun, zona, secao):
        self.sid = sid
        self.key = key
        self.uf = uf
        self.mun = mun
        self.zona = zona
        self.secao = secao
        self.zone: Zone | None = None
        self.status = S_NEW
        self.cs_dh = None
        self.aux_dh = None
        self.hash = None
        self.nm = None
        self.dr = None
        self.hr = None
        self.probes = 0
        self.next_probe = 0.0
        self.has_bu = 0
        self.err = None
        self.queued = 0          # 0 não, 1 fila conhecida, 2 fila de sondagem
        self.want_known = False  # estratégia A apontou aux novo
        self.prior = 0           # ordem de chegada no 1T (t_tot1); menor = antes
        self.t = 0.0


class Zone:
    __slots__ = ("key", "uf", "mun5", "z4", "secs", "uncaptured", "captured", "A", "need_since",
                 "last_fetch", "inflight", "mun", "probe_queued", "known_queued", "aptos1", "fetch_fail",
                 "retry_at", "gone_until")

    def __init__(self, uf, mun5, z4):
        self.key = (uf, mun5, z4)
        self.uf = uf
        self.mun5 = mun5
        self.z4 = z4
        self.secs: list[Sec] = []
        self.uncaptured: list[Sec] = []   # ordenada por prior; podada preguiçosamente
        self.captured = 0
        self.A: dict | None = None
        self.need_since: float | None = None
        self.last_fetch = -1e9            # monotonic
        self.inflight = False
        self.mun: Mun | None = None
        self.probe_queued = 0
        self.known_queued = 0
        self.aptos1 = 0
        self.fetch_fail = 0              # falhas seguidas (404/403/429/erro) desta URL
        self.retry_at = 0.0              # monotonic: recuo por URL após falha
        self.gone_until = 0.0            # monotonic: 404 permanente => fora da consistência até aqui

    @property
    def out_key(self) -> str:
        return f"{int(self.mun5)}-{self.z4}"


class Mun:
    __slots__ = ("uf", "mun5", "zones", "ab", "A", "out_A", "need_since", "last_fetch", "inflight",
                 "unsynced", "aptos1", "fetch_fail", "retry_at", "resetting")

    def __init__(self, uf, mun5):
        self.uf = uf
        self.mun5 = mun5
        self.zones: list[Zone] = []
        self.ab: dict | None = None      # última entrada do {uf}-ab.json
        self.A: dict | None = None       # último u.json buscado
        self.out_A: dict | None = None   # o que vai para agg.json
        self.need_since: float | None = None   # wall: quando o ab mudou e o u ainda não
        self.last_fetch = -1e9           # monotonic
        self.inflight = False
        self.unsynced = 0                # buscas seguidas em que u (st,c) != ab (st,c)
        self.aptos1 = 0
        self.fetch_fail = 0              # falhas seguidas (403/429/erro) desta URL
        self.retry_at = 0.0              # monotonic: recuo por URL após falha
        self.resetting = False           # st do ab despencou: esperar o u.json voltar a bater

    @property
    def multi(self) -> bool:
        return len(self.zones) > 1

    def zone_st(self) -> int:
        """st da zona única, pelo dado mais novo (ab ou u)."""
        a = self.ab["st"] if self.ab else 0
        b = self.A["st"] if self.A else 0
        return max(a, b)


class UFState:
    __slots__ = ("uf", "br_entry", "ab_self", "A", "ab_last", "ab_dirty", "ab_inflight", "u_last",
                 "u_inflight", "cs_last", "cs_inflight", "cs_dh_count", "cs_changed_at", "cs_lm",
                 "universe", "muns", "hit_ema", "probes", "hits", "cs_fail", "ab_fail", "u_fail",
                 "ab_retry_at", "u_retry_at")

    def __init__(self, uf):
        self.uf = uf
        self.br_entry: dict | None = None   # entrada desta UF no br-ab
        self.ab_self: dict | None = None    # entrada 'uf' do próprio {uf}-ab
        self.A: dict | None = None          # {uf}-c0001-u.json
        self.ab_last = -1e9
        self.ab_dirty = True
        self.ab_inflight = False
        self.u_last = -1e9
        self.u_inflight = False
        self.cs_last = -1e9
        self.cs_inflight = False
        self.cs_dh_count = 0
        self.cs_changed_at = 0.0            # wall
        self.cs_lm = None
        self.universe = False
        self.muns: dict[str, Mun] = {}
        self.hit_ema = 0.5
        self.probes = 0
        self.hits = 0
        self.cs_fail = 0
        self.ab_fail = 0
        self.u_fail = 0
        self.ab_retry_at = 0.0              # monotonic: recuo após falha (403/429/erro)
        self.u_retry_at = 0.0


class RRQueue:
    """Fila em dois níveis de round-robin (UF -> zona -> FIFO de seções).

    Garante amplitude: cada pop vem da próxima UF com trabalho e, dentro dela,
    da próxima zona; assim os primeiros BUs ficam espalhados pelo mapa.
    """

    def __init__(self):
        self._ufs: deque = deque()          # de [uf, deque_de_zonas]
        self._uf_map: dict = {}             # uf -> [uf, deque_de_zonas]
        self._zq: dict = {}                 # zone_key -> deque[Sec]
        self._n = 0

    def __len__(self):
        return self._n

    def push(self, sec: Sec):
        zk = sec.zone.key
        q = self._zq.get(zk)
        if q is None:
            q = self._zq[zk] = deque()
            ent = self._uf_map.get(sec.uf)
            if ent is None:
                ent = self._uf_map[sec.uf] = [sec.uf, deque()]
                self._ufs.append(ent)
            ent[1].append(zk)
        q.append(sec)
        self._n += 1

    def pop(self, accept=None) -> Sec | None:
        """Retira a próxima seção. `accept(uf) -> bool` permite pular UFs (ex.: taxa de acerto baixa)."""
        if not self._n:
            return None
        tries = len(self._ufs)
        while self._ufs:
            ent = self._ufs[0]
            if accept is not None and tries > 0 and not accept(ent[0]):
                self._ufs.rotate(-1)
                tries -= 1
                continue
            self._ufs.popleft()
            zk = ent[1].popleft()
            q = self._zq[zk]
            sec = q.popleft()
            self._n -= 1
            if q:
                ent[1].append(zk)
            else:
                del self._zq[zk]
            if ent[1]:
                self._ufs.append(ent)
            else:
                del self._uf_map[ent[0]]
            return sec
        return None

    def per_uf(self) -> dict:
        return {ent[0]: sum(len(self._zq[z]) for z in ent[1]) for ent in self._ufs}
