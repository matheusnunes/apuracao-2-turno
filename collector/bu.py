"""Decodificador do Boletim de Urna (BU) 2026 -> resumo por seção.

Adaptado de research/scripts/decode_bu.py (validado contra o agregado oficial
de Quixaba/PE no 1T). A spec ASN.1 2026 fica ao lado deste arquivo.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent / "bu-2026.asn1"

_conv = None
_lock = threading.Lock()


def _compiler():
    global _conv
    if _conv is None:
        with _lock:
            if _conv is None:
                import asn1tools

                _conv = asn1tools.compile_files(str(SPEC_PATH), codec="ber")
    return _conv


class BUDecodeError(Exception):
    """BU não decodificável. `kind` resume o motivo para a saúde."""

    def __init__(self, kind: str, msg: str):
        super().__init__(f"{kind}: {msg}")
        self.kind = kind


@dataclass
class BUResult:
    municipio: int
    zona: int
    secao: int
    id_eleicao: int
    aptos: int            # aptos da seção + aptos TTE
    aptos_secao: int | None
    aptos_tte: int | None
    comparecimento: int
    votos: dict[int, int] = field(default_factory=dict)   # nominais: número -> votos
    legenda: int = 0
    brancos: int = 0
    nulos: int = 0
    tipo_urna: str = ""
    tipo_arquivo: str = ""
    tipo_bu: str = ""     # 'secao' | 'SA'


def _cargo_nome(codigo_cargo) -> str:
    kind, val = codigo_cargo
    return val if kind == "cargoConstitucional" else f"livre:{val}"


def decode_bu(data: bytes, id_eleicao: int, cargo: str = "presidente") -> BUResult:
    """Decodifica um `-bu.dat`/`-busa.dat` e devolve os totais do cargo na eleição pedida."""
    conv = _compiler()
    try:
        env = conv.decode("EntidadeEnvelopeGenerico", data)
    except Exception as e:  # noqa: BLE001
        raise BUDecodeError("envelope", repr(e)[:300]) from e
    try:
        bu = conv.decode("EntidadeBoletimUrna", env["conteudo"])
    except Exception as e:  # noqa: BLE001
        raise BUDecodeError("boletim", repr(e)[:300]) from e

    try:
        ident = bu["identificacaoSecao"]
        urna = bu.get("urna", {})
        dss_kind, _dss = bu.get("dadosSecaoSA", ("dadosSecao", {}))
        res: BUResult | None = None
        seen = []
        for rpe in bu["resultadosVotacaoPorEleicao"]:
            seen.append(rpe["idEleicao"])
            if rpe["idEleicao"] != id_eleicao:
                continue
            a_sec = rpe.get("qtdEleitoresAptosSecao")
            a_tte = rpe.get("qtdEleitoresAptosTTE")
            aptos = (a_sec + (a_tte or 0)) if a_sec is not None else rpe["qtdEleitoresAptos"]
            for rv in rpe["resultadosVotacao"]:
                for tvc in rv["totaisVotosCargo"]:
                    if _cargo_nome(tvc["codigoCargo"]) != cargo:
                        continue
                    if res is None:
                        res = BUResult(
                            municipio=ident["municipioZona"]["municipio"],
                            zona=ident["municipioZona"]["zona"],
                            secao=ident["secao"],
                            id_eleicao=rpe["idEleicao"],
                            aptos=aptos,
                            aptos_secao=a_sec,
                            aptos_tte=a_tte,
                            comparecimento=rv["qtdComparecimento"],
                            tipo_urna=str(urna.get("tipoUrna", "")),
                            tipo_arquivo=str(urna.get("tipoArquivo", "")),
                            tipo_bu="secao" if dss_kind == "dadosSecao" else "SA",
                        )
                    for v in tvc["votosVotaveis"]:
                        tipo, q = v["tipoVoto"], v["quantidadeVotos"]
                        if tipo == "nominal":
                            num = int(v.get("identificacaoVotavel", {}).get("codigo"))
                            res.votos[num] = res.votos.get(num, 0) + q
                        elif tipo == "legenda":
                            res.legenda += q
                        elif tipo == "branco":
                            res.brancos += q
                        elif tipo == "nulo":
                            res.nulos += q
        if res is None:
            raise BUDecodeError("sem_eleicao", f"idEleicao {id_eleicao}/{cargo} ausente (vistas: {seen})")
        return res
    except BUDecodeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise BUDecodeError("estrutura", repr(e)[:300]) from e
