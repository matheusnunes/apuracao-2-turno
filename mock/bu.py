"""BU sintético do 2T (eleição 6258, pleito 3221) a partir de um BU real do 1T.

Template: mock/templates/template-1t-bu.dat (Recife z1 s31, 1T 2026, decodificado com a
spec 2026 de research/scripts/bu-2026.asn1, copiada em mock/bu-2026.asn1).

Substitui identificação (município/zona/local/seção), aptos (seção + TTE),
comparecimento, horários, dados da urna/carga e o resultado: mantém só a eleição 6258,
cargo Presidente, com 13, 22, brancos e nulos (votáveis com 0 votos são omitidos, como
nos BUs reais). Reencoda em BER com asn1tools (o roundtrip do template é byte a byte).
"""

from __future__ import annotations

import base64
import copy
import hashlib
from pathlib import Path

import asn1tools

HERE = Path(__file__).resolve().parent
SPEC = HERE / "bu-2026.asn1"
TEMPLATE = HERE / "templates" / "template-1t-bu.dat"

PLEITO = 3221
ELEICAO = 6258

# Diferença do horário local para Brasília (BUs trazem hora local da seção).
UF_TZ_OFFSET_H = {"ac": -2, "am": -1, "rr": -1, "ro": -1, "mt": -1, "ms": -1}

_conv = None
_tmpl_env = None
_tmpl_bu = None


def conv():
    global _conv
    if _conv is None:
        _conv = asn1tools.compile_files(str(SPEC), codec="ber")
    return _conv


def _template():
    global _tmpl_env, _tmpl_bu
    if _tmpl_bu is None:
        c = conv()
        env = c.decode("EntidadeEnvelopeGenerico", TEMPLATE.read_bytes())
        bu = c.decode("EntidadeBoletimUrna", env["conteudo"])
        _tmpl_env, _tmpl_bu = env, bu
    return _tmpl_env, _tmpl_bu


def _prng(seed: bytes, n: int) -> bytes:
    out = b""
    i = 0
    while len(out) < n:
        out += hashlib.sha512(seed + i.to_bytes(4, "big")).digest()
        i += 1
    return out[:n]


def _djt(sec_since_midnight_brt: int, uf: str) -> str:
    """Segundos desde 25/10/2026 00:00 BRT -> 'YYYYMMDDThhmmss' na hora local da UF."""
    s = sec_since_midnight_brt + UF_TZ_OFFSET_H.get(uf, 0) * 3600
    day = 25 + s // 86400
    s %= 86400
    return f"202610{day:02d}T{s // 3600:02d}{(s // 60) % 60:02d}{s % 60:02d}"


def section_hash(seed: int, scenario: str, idx: int) -> str:
    """Diretório do hash: hex do texto base64url de um SHA-256 (como o real)."""
    d = hashlib.sha256(f"mock|{seed}|{scenario}|{idx}".encode()).digest()
    return base64.urlsafe_b64encode(d).hex()


def bu_filename(uf: str, mun: int, zona: int, secao: int, sa: bool = False) -> str:
    return f"o{PLEITO:05d}{uf}{mun:05d}{zona:04d}{secao:04d}-{'busa' if sa else 'bu'}.dat"


def make_bu(*, uf: str, mun: int, zona: int, secao: int, local: int,
            aptos: int, aptos_tte: int, comp: int, l: int, o: int, b: int, n: int,
            t_recv: int, origem: int = 0, salt: bytes = b"") -> bytes:
    """Gera o .bu.dat (envelope BER). t_recv = segundos desde 25/10 00:00 BRT."""
    c = conv()
    env0, bu0 = _template()
    key = f"{uf}{mun:05d}{zona:04d}{secao:04d}".encode() + salt
    rnd = _prng(key, 64)

    # Horários: encerramento ~17:00 BRT (ou pouco antes do recebimento), emissão logo após.
    enc = max(17 * 3600 + 30 + rnd[0] * 2, t_recv - 600 - rnd[1] * 7)
    enc = min(enc, t_recv - 60) if t_recv - 60 > 8 * 3600 else enc
    emis = enc + 60 + rnd[2] % 120
    ger = emis + 2 + rnd[3] % 20
    abert = 8 * 3600 + rnd[4] % 50

    ident = {"municipioZona": {"municipio": mun, "zona": zona}, "local": local, "secao": secao}
    nome_gm = f"Z{uf.upper()}{(rnd[5] % 99) + 1:03d}WKS{(rnd[6] % 20) + 1:02d}"
    carga = {
        "numeroInternoUrna": 2_000_000 + int.from_bytes(rnd[7:10], "big") % 3_000_000,
        "numeroSerieFC": rnd[10:14],
        "identificadorGeradorMidia": {
            "nome": nome_gm,
            "serialCertificadoTPM": rnd[14:24].hex().upper(),
            "serialInstalacao": rnd[24:28].hex().upper(),
        },
        "dataHoraCarga": f"202610{15 + rnd[28] % 6:02d}T{8 + rnd[29] % 10:02d}{rnd[30] % 60:02d}00",
        "codigoCarga": "".join(str(x % 10) for x in _prng(key + b"carga", 24)),
    }
    sa = origem == 2
    urna = {
        "tipoUrna": "secao",
        "versaoVotacao": bu0["urna"]["versaoVotacao"],
        "correspondenciaResultado": {"identificacao": ("identificacaoSecaoEleitoral", dict(ident)),
                                     "carga": carga},
        "tipoArquivo": "saManual" if sa else ("votacaoRED" if origem == 1 else "votacaoUE"),
        "numeroSerieFV": rnd[32:36],
    }
    if sa:
        urna["motivoUtilizacaoSA"] = ("apuracaoTotalmenteManual",
                                      {"tipoapuracao": "totalmenteManual", "motivoApuracao": "urnaComDefeito"})

    # Votáveis (ordem do BU real: nominais por número, depois branco, nulo), cadeia de hash.
    votaveis = []
    for tipo, q, num in (("nominal", l, 13), ("nominal", o, 22), ("branco", b, None), ("nulo", n, None)):
        if q <= 0:
            continue
        v = {"tipoVoto": tipo, "quantidadeVotos": int(q)}
        if num is not None:
            v["identificacaoVotavel"] = {"partido": num, "codigo": num}
        votaveis.append(v)
    h = hashlib.sha512(key + b"seed").digest()
    for i, v in enumerate(votaveis, 1):
        h = hashlib.sha512(h + f"{v['tipoVoto']}{v.get('identificacaoVotavel', {}).get('codigo', 0)}"
                           f"{v['quantidadeVotos']}".encode()).digest()
        v["ordemGeracaoHash"] = i
        v["hash"] = h
    rve = {
        "idEleicao": ELEICAO,
        "qtdEleitoresAptos": int(aptos),
        "qtdEleitoresAptosSecao": int(aptos - aptos_tte),
        "qtdEleitoresAptosTTE": int(aptos_tte),
        "resultadosVotacao": [{
            "tipoCargo": "majoritario",
            "qtdComparecimento": int(comp),
            "totaisVotosCargo": [{
                "codigoCargo": ("cargoConstitucional", "presidente"),
                "ordemImpressao": 1,
                "votosVotaveis": votaveis,
            }],
        }],
        "ultimoHashVotosVotavel": h if votaveis else hashlib.sha512(key).digest(),
        "assinaturaUltimoHashVotosVotavel": _prng(key + b"sig", 132),
    }
    cab = {"dataGeracao": _djt(ger, uf), "idEleitoral": ("idPleito", PLEITO)}
    bu = {
        "cabecalho": cab,
        "fase": "oficial",
        "urna": urna,
        "identificacaoSecao": ident,
        "dataHoraEmissao": _djt(emis, uf),
        "dadosSecaoSA": (("dadosSA", {"juntaApuradora": 1, "turmaApuradora": 1 + rnd[37] % 5})
                         if sa else
                         ("dadosSecao", {"dataHoraAbertura": _djt(abert, uf),
                                         "dataHoraEncerramento": _djt(enc, uf)})),
        "qtdEleitoresCompareceram": int(comp),
        "resultadosVotacaoPorEleicao": [rve],
        "historicoCodigosCarga": [carga["codigoCarga"]],
    }
    if not sa:
        # Detalhamento: soma = comparecimento (como no BU real).
        sem = min(comp, (comp * (1 + rnd[38] % 4)) // 100)
        bio = min(comp - sem, (comp * (3 + rnd[39] % 6)) // 100)
        bu["detalhamentoComparecimento"] = {
            "qtdEleitoresCompareceramSemBiometria": int(sem),
            "qtdEleitoresHabilitadosPorBiometria": int(comp - sem - bio),
            "qtdEleitoresHabilitadosPorBiografia": int(bio),
        }
    conteudo = c.encode("EntidadeBoletimUrna", bu)
    env = {
        "cabecalho": dict(cab),
        "fase": "oficial",
        "identificacao": ("identificacaoSecaoEleitoral", copy.deepcopy(ident)),
        "tipoEnvelope": "envelopeBoletimUrna",
        "conteudo": conteudo,
    }
    return c.encode("EntidadeEnvelopeGenerico", env)
