#!/usr/bin/env python3
"""
Decodificador de Boletim de Urna (BU) do TSE -> JSON resumido.

Formato: arquivo "*-bu.dat" (ou "*-busa.dat") publicado em
  https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/{pleito}/dados/{uf}/{mun5}/{zona4}/{secao4}/{hash}/{arquivo}
O arquivo e um EntidadeEnvelopeGenerico (ASN.1 BER) cujo campo `conteudo`
contem um EntidadeBoletimUrna (spec oficial TSE: bu.asn1).

Uso (sem instalar nada globalmente):
  uv run --with asn1tools -- python -I decode_bu.py ARQUIVO-bu.dat [...]
  (spec default: bu-2026.asn1 ao lado deste script; --spec para trocar)
Opcoes:
  --cargo presidente   (default; use "todos" para todos os cargos)
  --raw                imprime a estrutura completa decodificada (debug)

Saida: um objeto JSON por arquivo (JSON Lines).
"""
import argparse
import json
import os
import sys

try:
    import asn1tools
except ImportError:  # pragma: no cover
    sys.exit("asn1tools nao encontrado. Rode: uv run --with asn1tools -- python -I decode_bu.py ...")

DEFAULT_SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bu-2026.asn1")


def _jsonable(o):
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    if isinstance(o, tuple):
        return [_jsonable(x) for x in o]
    if isinstance(o, list):
        return [_jsonable(x) for x in o]
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    return o


def _cargo_nome(codigo_cargo):
    # CHOICE: ('cargoConstitucional', 'presidente') | ('numeroCargoConsultaLivre', 25)
    kind, val = codigo_cargo
    return val if kind == "cargoConstitucional" else f"livre:{val}"


def decode(path, conv, cargo_filtro="presidente", raw=False):
    data = open(path, "rb").read()
    env = conv.decode("EntidadeEnvelopeGenerico", data)
    bu = conv.decode("EntidadeBoletimUrna", env["conteudo"])
    if raw:
        return {"envelope": _jsonable({k: v for k, v in env.items() if k != "conteudo"}), "bu": _jsonable(bu)}

    ident = bu["identificacaoSecao"]
    urna = bu["urna"]
    carga = urna["correspondenciaResultado"]["carga"]
    dss_kind, dss = bu["dadosSecaoSA"]

    out = {
        "arquivo": os.path.basename(path),
        "fase": bu["fase"],                                   # 'oficial' | 'simulado' | 'treinamento'
        "idEleitoral": list(bu["cabecalho"]["idEleitoral"]),  # ex.: ['idPleito', 3220]
        "dataGeracao": bu["cabecalho"]["dataGeracao"],
        "dataHoraEmissao": bu["dataHoraEmissao"],
        "municipio": ident["municipioZona"]["municipio"],
        "zona": ident["municipioZona"]["zona"],
        "local": ident["local"],
        "secao": ident["secao"],
        "urna": {
            "tipoUrna": urna["tipoUrna"],                    # secao | contingencia | reservaSecao | reservaEncerrandoSecao
            "tipoArquivo": urna["tipoArquivo"],              # votacaoUE | votacaoRED | sa*
            "versaoVotacao": urna["versaoVotacao"],
            "numeroInternoUrna": carga["numeroInternoUrna"],
            "codigoCarga": carga["codigoCarga"],
            "geradorMidia": carga.get("identificadorGeradorMidia"),  # [2026]
            "dataHoraCarga": carga["dataHoraCarga"],
            "motivoUtilizacaoSA": _jsonable(urna.get("motivoUtilizacaoSA")),
        },
        "tipoBU": "secao" if dss_kind == "dadosSecao" else "SA",
        "dataHoraAbertura": dss.get("dataHoraAbertura") if dss_kind == "dadosSecao" else None,
        "dataHoraEncerramento": dss.get("dataHoraEncerramento") if dss_kind == "dadosSecao" else None,
        # 2022/2024: qtdEleitoresLibCodigo/qtdEleitoresCompBiometrico; 2026: qtdEleitoresCompareceram + detalhamento
        "qtdEleitoresCompareceram": bu.get("qtdEleitoresCompareceram"),
        "detalhamentoComparecimento": bu.get("detalhamentoComparecimento"),
        "qtdEleitoresLibCodigo": bu.get("qtdEleitoresLibCodigo"),
        "qtdEleitoresCompBiometrico": bu.get("qtdEleitoresCompBiometrico"),
        "eleicoes": [],
    }

    for rpe in bu["resultadosVotacaoPorEleicao"]:
        aptos = rpe["qtdEleitoresAptos"]
        el = {"idEleicao": rpe["idEleicao"], "aptos": aptos,
              "aptosSecao": rpe.get("qtdEleitoresAptosSecao"), "aptosTTE": rpe.get("qtdEleitoresAptosTTE"),
              "cargos": []}
        for rv in rpe["resultadosVotacao"]:
            comp = rv["qtdComparecimento"]
            for tvc in rv["totaisVotosCargo"]:
                nome = _cargo_nome(tvc["codigoCargo"])
                if cargo_filtro != "todos" and nome != cargo_filtro:
                    continue
                c = {
                    "cargo": nome,
                    "tipoCargo": rv["tipoCargo"],
                    "aptos": aptos,
                    "comparecimento": comp,
                    "faltosos": aptos - comp,  # abstencao da secao
                    "nominais": {},            # numero do votavel -> votos
                    "legenda": {},
                    "brancos": 0,
                    "nulos": 0,
                }
                for v in tvc["votosVotaveis"]:
                    tipo, q = v["tipoVoto"], v["quantidadeVotos"]
                    num = v.get("identificacaoVotavel", {}).get("codigo")
                    if tipo == "nominal":
                        c["nominais"][str(num)] = c["nominais"].get(str(num), 0) + q
                    elif tipo == "legenda":
                        c["legenda"][str(num)] = c["legenda"].get(str(num), 0) + q
                    elif tipo == "branco":
                        c["brancos"] += q
                    elif tipo == "nulo":
                        c["nulos"] += q
                    else:
                        c.setdefault("outros", {})[tipo] = c.get("outros", {}).get(tipo, 0) + q
                c["totalVotos"] = sum(c["nominais"].values()) + sum(c["legenda"].values()) + c["brancos"] + c["nulos"]
                el["cargos"].append(c)
        if el["cargos"]:
            out["eleicoes"].append(el)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--cargo", default="presidente")
    ap.add_argument("--raw", action="store_true")
    a = ap.parse_args()
    conv = asn1tools.compile_files(a.spec, codec="ber")
    for f in a.files:
        try:
            print(json.dumps(decode(f, conv, a.cargo, a.raw), ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"arquivo": f, "erro": repr(e)}), file=sys.stderr)


if __name__ == "__main__":
    main()
