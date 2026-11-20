"""WebFetch / web_search against a local stub HTTP server."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.tools.base import ToolContext, ToolError
from app.tools.sdk import web

PAGE = b"""<html><head><title>Hi</title><meta name="generator" content="X1">
<script>var u="/api/v1/users";</script><script src="/app.js"></script></head>
<body><!-- secret note --><h1>Head</h1><p>Hello <a href="/x">link</a> <b>b</b></p>
<form method="post" action="/login"><input name="u"><input type="hidden" name="csrf" value="tok"></form></body></html>"""

DDG = b"""<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=1">Title A</a>
<a class="result__snippet">Snippet A</a>
<a class="result__a" href="https://b.example/">Title B</a><a class="result__snippet">Snippet B</a>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/page":
            self._send(200, "text/html; charset=utf-8", PAGE)
        elif self.path == "/json":
            self._send(200, "application/json", b'{"a": 1}')
        elif self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.end_headers()
        elif self.path.startswith("/brave"):
            self._send(200, "application/json", json.dumps(
                {"web": {"results": [{"title": "T", "url": "http://u", "description": "D"}]}}).encode())
        else:
            self._send(404, "text/plain", b"nope")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        if self.path == "/ddg":
            assert b"q=" in body
            self._send(200, "text/html", DDG)
        elif self.path == "/tavily":
            self._send(200, "application/json", json.dumps(
                {"results": [{"title": "TT", "url": "http://t", "content": "C"}]}).encode())
        else:
            self._send(404, "text/plain", b"")


@pytest.fixture(scope="module")
def base():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _noproxy(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")


T = {t.name: t for t in web.TOOLS}
CTX = ToolContext(None, None)


async def test_fetch_html_and_recon(base):
    out = await T["WebFetch"].run(CTX, {"url": base + "/page"})
    assert out.startswith(f"[200 OK] {base}/page\nServer: BaseHTTP")
    assert "# Head" in out and "[link](/x)" in out and "**b**" in out
    assert "/api/v1/users" not in out and "RECON" not in out
    out = await T["WebFetch"].run(CTX, {"url": base + "/page", "extract": True})
    for s in ("=== RECON ===", "secret note", "/app.js", "/api/v1/users", "POST /login", "csrf(hidden=tok)",
              "generator=X1", "## LINKS"):
        assert s in out


async def test_fetch_json_redirect_and_errors(base):
    assert (await T["WebFetch"].run(CTX, {"url": base + "/redir"})).endswith('{"a": 1}')
    assert "[404 Not Found]" in await T["WebFetch"].run(CTX, {"url": base + "/missing"})
    with pytest.raises(ToolError, match="static assets"):
        await T["WebFetch"].run(CTX, {"url": base + "/a.JS?v=3"})
    with pytest.raises(ToolError, match="must start with"):
        await T["WebFetch"].run(CTX, {"url": "ftp://x"})
    with pytest.raises(ToolError, match="Error fetching URL"):
        await T["WebFetch"].run(CTX, {"url": "http://127.0.0.1:1/"})


async def test_search_providers(base, monkeypatch):
    monkeypatch.setattr(web, "DDG_ENDPOINT", base + "/ddg")
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    r = json.loads(await T["web_search"].run(CTX, {"query": "q"}))
    assert r["backend"] == "ddgs" and r["results"][0] == {
        "title": "Title A", "url": "https://example.com/a", "description": "Snippet A", "position": 1}
    r = json.loads(await T["web_search"].run(CTX, {"query": "q", "limit": 1}))
    assert len(r["results"]) == 1

    monkeypatch.setattr(web, "BRAVE_ENDPOINT", base + "/brave")
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    with pytest.raises(ToolError, match="BRAVE_API_KEY"):
        await T["web_search"].run(CTX, {"query": "q"})
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    assert json.loads(await T["web_search"].run(CTX, {"query": "q"}))["results"][0]["url"] == "http://u"

    monkeypatch.setattr(web, "TAVILY_ENDPOINT", base + "/tavily")
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    assert json.loads(await T["web_search"].run(CTX, {"query": "q"}))["results"][0]["description"] == "C"

    monkeypatch.setenv("SEARCH_PROVIDER", "bogus")
    with pytest.raises(ToolError, match="unknown provider"):
        await T["web_search"].run(CTX, {"query": "q"})
    with pytest.raises(ToolError, match="empty"):
        await T["web_search"].run(CTX, {"query": "  "})
