"""Rebaixa `eleitorado_local_votacao_2026` (domingo de manhã) e compara com a base anterior.

Uso normal: `uv run python -m etl.build_base --refresh-locais`.

- HEAD primeiro; se ETag e Last-Modified forem iguais aos da cópia em uso, não baixa.
- Baixa do zero (sem retomar, HTTP/1.1), valida o zip (testzip) e extrai só o
  `eleitorado_local_votacao_2026_BRASIL.csv` para data/raw/eleitorado_local_votacao_2026_refresh/.
- `pick_csv()` usa a cópia do refresh, se existir; senão a original de data/raw/eleitorado_local_votacao_2026/.
- `diff()` compara a secoes.parquet anterior com a nova e grava data/base/locais_diff_2t.csv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import zipfile

import orjson
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

from .common import BASE, LOCAIS_REFRESH_DIR, RAW

URL = "https://cdn.tse.jus.br/estatistica/sead/odsele/eleitorado_locais_votacao/eleitorado_local_votacao_2026.zip"
MEMBER = "eleitorado_local_votacao_2026_BRASIL.csv"
ORIG_CSV = RAW / "eleitorado_local_votacao_2026" / MEMBER
# Anotado em research/baseline-datasets.md (arquivo baixado no sábado 24/10)
ORIG_META = {"etag": '"9fded1c-65e9a6d40e6cc"', "last_modified": "Sat, 24 Oct 2026 18:40:00 GMT"}
REFRESH_CSV = LOCAIS_REFRESH_DIR / MEMBER
META = LOCAIS_REFRESH_DIR / "meta.json"


def pick_csv():
    return REFRESH_CSV if REFRESH_CSV.exists() else ORIG_CSV


def _head():
    out = subprocess.run(["curl", "-sS", "--http1.1", "-I", "-m", "60", URL], capture_output=True, text=True, check=True)
    h = {}
    for line in out.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            h[k.strip().lower()] = v.strip()
    return h


def download():
    LOCAIS_REFRESH_DIR.mkdir(parents=True, exist_ok=True)
    cur = orjson.loads(META.read_bytes()) if META.exists() else ORIG_META
    h = _head()
    etag, lm = h.get("etag"), h.get("last-modified")
    info = {"url": URL, "etag_remoto": etag, "last_modified_remoto": lm,
            "etag_em_uso": cur.get("etag"), "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if etag and etag == cur.get("etag"):
        print(f"    eleitorado_local_votacao_2026 inalterado (ETag {etag}, LM {lm}); nada a baixar")
        info["mudou"] = False
        return info
    print(f"    novo arquivo no CDN: ETag {etag} (em uso {cur.get('etag')}), LM {lm}; baixando…")
    zpath = LOCAIS_REFRESH_DIR / "eleitorado_local_votacao_2026.zip"
    tmp = zpath.with_suffix(".zip.part")
    if tmp.exists():
        tmp.unlink()
    subprocess.run(["curl", "-sS", "--http1.1", "--fail", "-m", "1800", "-o", str(tmp), URL], check=True)
    with zipfile.ZipFile(tmp) as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"zip corrompido ({bad}); rode de novo")
        names = zf.namelist()
        if MEMBER not in names:
            raise RuntimeError(f"{MEMBER} não está no zip: {names}")
        csv_tmp = REFRESH_CSV.with_suffix(".csv.part")
        with zf.open(MEMBER) as src, open(csv_tmp, "wb") as dst:  # nome de destino fixo, não o do zip
            shutil.copyfileobj(src, dst, 1 << 20)
    os.replace(tmp, zpath)
    os.replace(csv_tmp, REFRESH_CSV)
    META.write_bytes(orjson.dumps({"etag": etag, "last_modified": lm, "downloaded_at": info["checked_at"]},
                                  option=orjson.OPT_INDENT_2))
    info["mudou"] = True
    info["bytes_csv"] = REFRESH_CSV.stat().st_size
    return info


def diff(prev: pa.Table, new: pa.Table):
    """Compara seção a seção (alinhado por idx) local, coordenada e aptos do 2T."""
    cols = ["idx", "uf", "cd_mun", "zona", "secao", "nr_local", "lat", "lng", "coord_src", "aptos2_cad"]
    new = new.select(cols)
    if prev.num_rows != new.num_rows:
        print(f"    AVISO: número de seções mudou ({prev.num_rows} -> {new.num_rows}); diff por chave")
    p = prev.rename_columns([c if c in ("idx", "uf", "cd_mun", "zona", "secao") else f"{c}_antes" for c in prev.column_names])
    j = new.join(p.drop_columns(["idx"]), keys=["uf", "cd_mun", "zona", "secao"], join_type="full outer")

    def changed(a, b):
        x, y = j.column(a), j.column(b)
        both_null = pc.and_(pc.is_null(x), pc.is_null(y))
        return pc.and_(pc.invert(both_null), pc.fill_null(pc.not_equal(x, y), True))

    lat_d = pc.abs(pc.subtract(j.column("lat"), j.column("lat_antes")))
    lng_d = pc.abs(pc.subtract(j.column("lng"), j.column("lng_antes")))
    coord_moved = pc.fill_null(pc.or_(pc.greater(lat_d, 1e-6), pc.greater(lng_d, 1e-6)), False)
    m_local = changed("nr_local", "nr_local_antes")
    m_coord = pc.or_(coord_moved, changed("coord_src", "coord_src_antes"))
    m_aptos = changed("aptos2_cad", "aptos2_cad_antes")
    any_ = pc.or_(pc.or_(m_local, m_coord), m_aptos)
    d = j.filter(any_)
    d = d.append_column("mudou_local", m_local.filter(any_) if hasattr(m_local, "filter") else pc.filter(m_local, any_))
    d = d.append_column("mudou_coord", pc.filter(m_coord, any_))
    d = d.append_column("mudou_aptos2", pc.filter(m_aptos, any_))
    d = d.sort_by([("idx", "ascending")])
    out = BASE / "locais_diff_2t.csv"
    pacsv.write_csv(d, out)
    res = {
        "secoes_com_mudanca": d.num_rows,
        "mudou_local": int(pc.sum(pc.cast(m_local, pa.int64())).as_py() or 0),
        "mudou_coordenada": int(pc.sum(pc.cast(m_coord, pa.int64())).as_py() or 0),
        "mudou_aptos2_cad": int(pc.sum(pc.cast(m_aptos, pa.int64())).as_py() or 0),
        "arquivo": str(out.relative_to(BASE.parent.parent)),
    }
    print(f"    diff locais 2T: {res}")
    return res
