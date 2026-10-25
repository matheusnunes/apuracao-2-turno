"""Espelho online: servidor (cloud/server.py) + envio do Mac (cloud/sync.py) de ponta a ponta, em portas locais."""

import gzip
import json
import os
import threading
import time

import httpx
import pytest

import cloud.server as cs
from cloud.sync import Mirror

TOKEN = "t0k3n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    (remote / "data" / "live").mkdir(parents=True)
    (remote / "index.html").write_text("<html>tela</html>")
    (remote / "serve.py").write_text("# código")
    monkeypatch.setattr(cs.serve, "ROOT", remote.resolve())
    monkeypatch.setenv("SYNC_TOKEN", TOKEN)
    monkeypatch.setattr(cs, "_inv", {})
    srv = cs.serve.Server(("127.0.0.1", 0), cs.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    local = tmp_path / "site"
    (local / "data" / "base" / "h3").mkdir(parents=True)
    (local / "data" / "live").mkdir(parents=True)
    (local / "data" / "base" / "meta.json").write_text(json.dumps({"x": "y" * 2000}))
    (local / "data" / "base" / "h3" / "r5.bin").write_bytes(os.urandom(5000))
    yield url, local, remote
    srv.shutdown()


def publish(live, version, agg_dv, sec_dv, sec_bytes=None):
    """Imita o coletor: arquivos versionados primeiro, manifest por último."""
    (live / f"agg.{agg_dv}.json").write_text(json.dumps({"br": {"v": agg_dv}, "pad": "a" * 3000}))
    if sec_bytes is not None:
        (live / f"sections.{sec_dv}.bin").write_bytes(sec_bytes)
    (live / "analytics.json").write_text(json.dumps({"input_version": agg_dv}))
    man = {"version": version, "generated_at": f"t{version}", "data_versions": {"agg": agg_dv, "sections": sec_dv},
           "files": {"agg": f"agg.{agg_dv}.json", "sections": f"sections.{sec_dv}.bin", "analytics": "analytics.json"}}
    (live / "manifest.json").write_text(json.dumps(man))
    time.sleep(0.01)  # mtime distinto entre versões


def get(url, path):
    return httpx.get(url + path, headers={"Accept-Encoding": "gzip"})


def test_espelha_base_e_live(env):
    url, local, remote = env
    live = local / "data" / "live"
    m = Mirror(url, TOKEN, site=local, sections_every=0, status=None)
    publish(live, 1, 1, 1, os.urandom(20000))
    assert m.tick(time.monotonic())
    assert get(url, "/data/base/h3/r5.bin").content == (local / "data/base/h3/r5.bin").read_bytes()
    r = get(url, "/data/live/sections.1.bin")
    assert r.headers.get("content-encoding") == "gzip" and r.content == (live / "sections.1.bin").read_bytes()
    assert get(url, "/data/live/manifest.json").json()["version"] == 1
    assert get(url, "/index.html").status_code == 200
    assert get(url, "/serve.py").status_code == 404  # código não é servido

    # nada mudou: nenhum byte enviado
    m.sent_bytes = 0
    m.tick(time.monotonic())
    assert m.sent_bytes == 0


def test_sections_espacado_manifest_aponta_para_o_enviado(env):
    url, local, _ = env
    live = local / "data" / "live"
    m = Mirror(url, TOKEN, site=local, sections_every=60, status=None)
    publish(live, 1, 1, 1, b"\x01" * 4000)
    m.tick(0.0)
    publish(live, 2, 2, 2, b"\x02" * 4000)
    m.tick(10.0)  # dentro do intervalo: agg novo vai, sections novo espera
    man = get(url, "/data/live/manifest.json").json()
    assert man["files"]["agg"] == "agg.2.json" and man["version"] == 2
    assert man["files"]["sections"] == "sections.1.bin" and man["data_versions"]["sections"] == 1
    m.tick(61.0)
    man = get(url, "/data/live/manifest.json").json()
    assert man["files"]["sections"] == "sections.2.bin" and man["data_versions"]["sections"] == 2
    assert get(url, "/data/live/sections.2.bin").content == b"\x02" * 4000


def test_coletor_recomeca_do_1_e_poda_por_ordem_de_envio(env):
    url, local, remote = env
    live = local / "data" / "live"
    m = Mirror(url, TOKEN, site=local, sections_every=60, status=None)
    for v in range(50, 54):
        publish(live, v, v, v, bytes([v]) * 3000)
        m.tick(v * 100.0)
    assert sorted(p.name for p in (remote / "data/live").glob("agg.*.json")) == ["agg.51.json", "agg.52.json", "agg.53.json"]
    for f in live.iterdir():  # --clean-live às 16:30
        f.unlink()
    publish(live, 1, 1, 51, b"\xee" * 3000)  # mesmo nome de um sections antigo, conteúdo novo
    m.tick(5301.0)  # ainda dentro do intervalo de sections, mas o recomeço força o envio
    man = get(url, "/data/live/manifest.json").json()
    assert man["version"] == 1 and man["files"]["agg"] == "agg.1.json"
    assert get(url, "/data/live/agg.1.json").json()["br"] == {"v": 1}  # o recém-enviado não é podado
    assert get(url, "/data/live/sections.51.bin").content == b"\xee" * 3000


def test_servidor_reiniciado_recebe_tudo_de_novo(env, monkeypatch):
    url, local, remote = env
    live = local / "data" / "live"
    m = Mirror(url, TOKEN, site=local, sections_every=60, status=None)
    publish(live, 1, 1, 1, b"\x01" * 3000)
    m.tick(0.0)
    for p in (remote / "data").rglob("*"):  # redeploy: disco efêmero zerado
        if p.is_file():
            p.unlink()
    monkeypatch.setattr(cs, "_inv", {})
    monkeypatch.setattr(cs, "BOOT", "novo")
    m.tick(31.0)
    assert get(url, "/data/base/meta.json").status_code == 200
    assert get(url, "/data/live/sections.1.bin").content == b"\x01" * 3000


def test_escrita_exige_token_e_caminho_seguro(env):
    url, _, remote = env
    body = b"x"
    sha = __import__("hashlib").sha256(body).hexdigest()
    assert httpx.put(url + "/_sync/data/live/a.json", content=body, headers={"X-Sha256": sha}).status_code == 401
    auth = {"Authorization": f"Bearer {TOKEN}", "X-Sha256": sha}
    assert httpx.put(url + "/_sync/data/live/../../index.html", content=body, headers=auth).status_code == 400
    assert httpx.put(url + "/_sync/index.html", content=body, headers=auth).status_code == 400
    assert httpx.put(url + "/_sync/data/live/a.json", content=b"y", headers=auth).status_code == 400  # sha errado
    gz = {**auth, "Content-Encoding": "gzip"}
    assert httpx.put(url + "/_sync/data/live/a.json", content=gzip.compress(body), headers=gz).status_code == 204
    assert httpx.get(url + "/_sync/inventory").status_code == 401
    assert (remote / "index.html").read_text() == "<html>tela</html>"
