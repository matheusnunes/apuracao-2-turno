"""Servidor falso mínimo (aiohttp) com os mesmos caminhos do TSE, para testes do coletor.

Arquivos ficam em memória (`put`/`delete`), com ETag/Last-Modified e 304. Conta
requisições por caminho para verificar "sem trabalho duplicado".
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from email.utils import formatdate

from aiohttp import web


class FakeTSE:
    def __init__(self):
        self.files: dict[str, tuple[bytes, str, str]] = {}
        self.hits: Counter = Counter()
        self.status_override: dict[str, int] = {}
        self.runner = None
        self.port = None
        self.changed_at: dict[str, float] = {}

    def put(self, path: str, body: bytes):
        path = path.lstrip("/")
        etag = '"' + hashlib.md5(body).hexdigest() + '"'
        old = self.files.get(path)
        if old and old[1] == etag:
            return
        self.files[path] = (body, etag, formatdate(time.time(), usegmt=True))
        self.changed_at[path] = time.time()

    def delete(self, path: str):
        self.files.pop(path.lstrip("/"), None)

    async def handle(self, request: web.Request):
        path = request.path.lstrip("/")
        if path.startswith("oficial/"):
            path = path[len("oficial/"):]
        self.hits[path] += 1
        if path in self.status_override:
            return web.Response(status=self.status_override[path])
        f = self.files.get(path)
        if f is None:
            return web.Response(status=404, body=b"<Error><Code>NoSuchKey</Code></Error>",
                                content_type="application/xml")
        body, etag, lm = f
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag, "Last-Modified": lm})
        return web.Response(body=body, headers={"ETag": etag, "Last-Modified": lm,
                                                "Content-Type": "application/json"})

    async def start(self, port: int = 0) -> str:
        app = web.Application()
        app.router.add_route("GET", "/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}/oficial"

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    def count(self, contains: str) -> int:
        return sum(n for p, n in self.hits.items() if contains in p)
