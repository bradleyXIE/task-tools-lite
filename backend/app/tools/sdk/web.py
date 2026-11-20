"""WebFetch and web_search, ported from ARTEX (internal/webfetch + norma tool/websearch.go).

Pure stdlib HTML parsing (html.parser); network via httpx, which honours HTTP(S)_PROXY env.
"""
from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.tools.base import Tool, ToolContext, ToolError

MAX_FETCH_BYTES = 2 << 20
MAX_OUTPUT = 50000
FETCH_TIMEOUT = 30.0
USER_AGENT = "norma/0.4"
DDG_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# Endpoints are module-level so tests (or deployments) can point them elsewhere.
DDG_ENDPOINT = os.environ.get("DDG_ENDPOINT", "https://html.duckduckgo.com/html/")
BRAVE_ENDPOINT = os.environ.get("BRAVE_ENDPOINT", "https://api.search.brave.com/res/v1/web/search")
TAVILY_ENDPOINT = os.environ.get("TAVILY_ENDPOINT", "https://api.tavily.com/search")

STATIC_EXTS = {
    ".js", ".mjs", ".cjs", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif",
    ".ico", ".bmp", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".wasm", ".mp4", ".webm", ".mp3",
    ".wav", ".ogg",
}


def static_asset_ext(url: str) -> str:
    path = urlparse(url).path
    seg = path.rsplit("/", 1)[-1]
    dot = seg.rfind(".")
    if dot < 0:
        return ""
    ext = seg[dot:].lower()
    return ext if ext in STATIC_EXTS else ""


def truncate(s: str, limit: int) -> str:
    if limit <= 0 or len(s) <= limit:
        return s
    half = limit // 2
    return s[:half] + f"\n\n... [{len(s) - limit} characters truncated] ...\n\n" + s[-half:]


# ---------------------------------------------------------------- HTML tree

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Node:
    __slots__ = ("attrs", "children", "kind", "tag", "text")

    def __init__(self, kind: str, tag: str = "", attrs: dict[str, str] | None = None, text: str = ""):
        self.kind = kind  # element | text | comment
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.text = text


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("element", "#document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        n = Node("element", tag, {k: (v or "") for k, v in attrs})
        self.stack[-1].children.append(n)
        if tag not in _VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(Node("element", tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(Node("text", text=data))

    def handle_comment(self, data: str) -> None:
        self.stack[-1].children.append(Node("comment", text=data))


def parse_html(src: str) -> Node:
    b = _Builder()
    b.feed(src)
    b.close()
    return b.root


def _text_only(n: Node, out: list[str]) -> None:
    if n.kind == "text":
        out.append(n.text)
        return
    for c in n.children:
        _text_only(c, out)


_SKIP = {"script", "style", "noscript", "svg", "template"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "ul", "ol", "table", "tr", "blockquote"}


def _render(n: Node, b: list[str]) -> None:
    if n.kind == "text":
        b.append(n.text.replace("\n", " "))
        return
    if n.kind != "element" or n.tag in _SKIP:
        return
    t = n.tag

    def kids() -> None:
        for c in n.children:
            _render(c, b)

    if len(t) == 2 and t[0] == "h" and t[1] in "123456":
        b.append("\n\n" + "#" * int(t[1]) + " ")
        kids()
        b.append("\n\n")
    elif t == "title":
        b.append("\n\n# ")
        kids()
        b.append("\n\n")
    elif t in _BLOCK:
        b.append("\n\n")
        kids()
        b.append("\n\n")
    elif t == "br":
        b.append("\n")
    elif t == "hr":
        b.append("\n\n---\n\n")
    elif t == "li":
        b.append("\n- ")
        kids()
    elif t == "a":
        inner: list[str] = []
        for c in n.children:
            _render(c, inner)
        text = "".join(inner).strip()
        href = n.attrs.get("href", "")
        b.append(f"[{text}]({href})" if href and text else text)
    elif t == "img":
        if n.attrs.get("src"):
            b.append(f"![{n.attrs.get('alt', '')}]({n.attrs['src']})")
    elif t in ("strong", "b"):
        b.append("**")
        kids()
        b.append("**")
    elif t in ("em", "i"):
        b.append("*")
        kids()
        b.append("*")
    elif t == "code":
        b.append("`")
        kids()
        b.append("`")
    elif t == "pre":
        inner = []
        _text_only(n, inner)
        b.append("\n\n```\n" + "".join(inner).strip() + "\n```\n\n")
    else:
        kids()


def html_to_markdown(doc: Node) -> str:
    b: list[str] = []
    _render(doc, b)
    s = re.sub(r"[ \t]+", " ", "".join(b))
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


_RE_ENDPOINT = re.compile(r"""["'`](https?://[^"'`\s]+|/[A-Za-z0-9._~$&'()*+,;=:@%/?#\[\]-]{2,})["'`]""")


def _one_line(s: str, mx: int) -> str:
    s = " ".join(s.split())
    return s[:mx] + "…" if len(s) > mx else s


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for s in items:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _describe_form(form: Node) -> str:
    method = (form.attrs.get("method") or "GET").upper()
    inputs: list[str] = []

    def walk(n: Node) -> None:
        if n.kind == "element":
            if n.tag in ("input", "textarea", "select"):
                typ = n.attrs.get("type") or n.tag
                desc = f"{n.attrs.get('name', '')}({typ}"
                if typ == "hidden" and n.attrs.get("value"):
                    desc += "=" + _one_line(n.attrs["value"], 60)
                inputs.append(desc + ")")
            for c in n.children:
                walk(c)

    walk(form)
    return f"{method} {form.attrs.get('action', '')} → {' '.join(inputs)}"


def extract_recon(doc: Node) -> str:
    comments: list[str] = []
    scripts: list[str] = []
    endpoints: list[str] = []
    forms: list[str] = []
    metas: list[str] = []
    links: list[str] = []
    seen_link: set[str] = set()

    def walk(n: Node) -> None:
        if n.kind == "comment":
            c = _one_line(n.text, 300)
            if c:
                comments.append(c)
        elif n.kind == "element":
            if n.tag == "script":
                if n.attrs.get("src"):
                    scripts.append(n.attrs["src"])
                else:
                    buf: list[str] = []
                    _text_only(n, buf)
                    for m in list(_RE_ENDPOINT.finditer("".join(buf)))[:40]:
                        endpoints.append(m.group(1))
            elif n.tag == "form":
                forms.append(_describe_form(n))
            elif n.tag == "meta":
                name = n.attrs.get("name") or n.attrs.get("property", "")
                content = n.attrs.get("content", "")
                if name and content:
                    metas.append(f"{name}={_one_line(content, 120)}")
            elif n.tag in ("a", "link"):
                h = n.attrs.get("href", "")
                if h and h not in seen_link:
                    seen_link.add(h)
                    links.append(h)
            for c in n.children:
                walk(c)

    walk(doc)
    out = ["=== RECON ==="]
    cap_links = links if len(links) <= 100 else links[:100] + [f"… ({len(links) - 100} more)"]
    for title, items in (
        ("COMMENTS", comments), ("SCRIPTS (src)", _dedup(scripts)), ("INLINE ENDPOINTS", _dedup(endpoints)),
        ("FORMS", forms), ("META", metas), ("LINKS", cap_links),
    ):
        if items:
            out.append(f"\n\n## {title}\n" + "".join(f"- {i}\n" for i in items))
    return "" if len(out) == 1 else "".join(out)


# ---------------------------------------------------------------- WebFetch

_PROXY_ERRS = ("eof", "connection reset", "connection refused", "proxyconnect", "broken pipe", "malformed",
               "http2", "http/2", "tls:", "protocol error", "proxy", "ssl")
_NOT_PROXY = ("no such host", "name or service not known", "nodename nor servname", "timeout", "timed out")


def _proxy_likely(err: Exception) -> bool:
    s = f"{type(err).__name__} {err}".lower()
    if any(n in s for n in _NOT_PROXY):
        return False
    return any(p in s for p in _PROXY_ERRS)


def _has_proxy_env() -> bool:
    return any(os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"))


async def _get(url: str, trust_env: bool) -> tuple[httpx.Response, bytes]:
    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT, trust_env=trust_env) as c,
        c.stream("GET", url, headers={"User-Agent": USER_AGENT}) as r,
    ):
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf += chunk
            if len(buf) >= MAX_FETCH_BYTES:
                break
        return r, bytes(buf[:MAX_FETCH_BYTES])


def _decode(r: httpx.Response, body: bytes) -> str:
    enc = r.charset_encoding or "utf-8"
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


async def _web_fetch(ctx: ToolContext, a: dict) -> Any:
    url = str(a.get("url", ""))
    if not url.startswith(("http://", "https://")):
        raise ToolError("Error: url must start with http:// or https://")
    ext = static_asset_ext(url)
    if ext:
        raise ToolError(
            f"Error: WebFetch does not fetch static assets ({ext} file). It reads HTML pages, documentation, "
            "and API responses — not linked resources like scripts, styles, images, or fonts. "
            "Fetch the page that references this asset instead.")
    try:
        try:
            r, body = await _get(url, True)
        except httpx.HTTPError as e:
            if _has_proxy_env() and _proxy_likely(e):
                r, body = await _get(url, False)
            else:
                raise
    except httpx.HTTPError as e:
        raise ToolError(f"Error fetching URL: {type(e).__name__}: {e}") from e
    phrase = httpx.codes.get_reason_phrase(r.status_code)
    header = f"[{r.status_code} {phrase}] {url}\n"
    if r.headers.get("server"):
        header += f"Server: {r.headers['server']}\n"
    if r.headers.get("x-powered-by"):
        header += f"X-Powered-By: {r.headers['x-powered-by']}\n"
    text = _decode(r, body)
    if "html" in r.headers.get("content-type", "").lower():
        doc = parse_html(text)
        out = html_to_markdown(doc)
        if a.get("extract"):
            recon = extract_recon(doc)
            if recon:
                out += "\n\n" + recon
    else:
        out = text
    return truncate(header + out.strip(), MAX_OUTPUT)


# ---------------------------------------------------------------- web_search

def _unwrap_ddg(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    q = parse_qs(urlparse(href).query)
    if q.get("uddg"):
        return unquote(q["uddg"][0])
    return href if href.startswith(("http://", "https://")) else ""


def parse_ddg(doc: Node, limit: int) -> list[dict]:
    hits: list[tuple[str, str]] = []
    snippets: list[str] = []

    def classes(n: Node) -> list[str]:
        return n.attrs.get("class", "").split()

    def walk(n: Node) -> None:
        if n.kind != "element":
            return
        if n.tag == "a" and "result__a" in classes(n):
            buf: list[str] = []
            _text_only(n, buf)
            hits.append((" ".join("".join(buf).split()), _unwrap_ddg(n.attrs.get("href", ""))))
        if "result__snippet" in classes(n):
            buf = []
            _text_only(n, buf)
            snippets.append(" ".join("".join(buf).split()))
        for c in n.children:
            walk(c)

    walk(doc)
    out: list[dict] = []
    for i, (title, href) in enumerate(hits):
        if len(out) >= limit:
            break
        if not href:
            continue
        out.append({"title": title, "url": href, "description": snippets[i] if i < len(snippets) else "",
                    "position": len(out) + 1})
    return out


async def _search_ddg(query: str, limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.post(DDG_ENDPOINT, data={"q": query, "kl": "wt-wt"},
                         headers={"User-Agent": DDG_UA, "Referer": "https://html.duckduckgo.com/"})
    if r.status_code in (202, 429):
        raise ToolError(f"DuckDuckGo is rate-limiting (HTTP {r.status_code}); try again later or switch backend")
    if r.status_code != 200:
        raise ToolError(f"DuckDuckGo returned HTTP {r.status_code}")
    return parse_ddg(parse_html(r.text[:MAX_FETCH_BYTES]), limit)


async def _search_brave(query: str, limit: int) -> list[dict]:
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        raise ToolError("web_search: backend 'brave' requires BRAVE_API_KEY")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(BRAVE_ENDPOINT, params={"q": query, "count": min(limit, 20)},
                        headers={"Accept": "application/json", "X-Subscription-Token": key})
    if r.status_code != 200:
        raise ToolError(f"Brave Search returned HTTP {r.status_code}")
    res = (r.json().get("web") or {}).get("results") or []
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "description": x.get("description", ""),
             "position": i + 1} for i, x in enumerate(res[:limit])]


async def _search_tavily(query: str, limit: int) -> list[dict]:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise ToolError("web_search: backend 'tavily' requires TAVILY_API_KEY")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(TAVILY_ENDPOINT, json={"api_key": key, "query": query, "max_results": min(limit, 20),
                                                "search_depth": "basic"},
                         headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise ToolError(f"Tavily Search returned HTTP {r.status_code}")
    res = r.json().get("results") or []
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "description": x.get("content", ""),
             "position": i + 1} for i, x in enumerate(res[:limit])]


_PROVIDERS = {
    "duckduckgo": ("ddgs", _search_ddg), "ddgs": ("ddgs", _search_ddg),
    "brave": ("brave-free", _search_brave), "brave-free": ("brave-free", _search_brave),
    "tavily": ("tavily", _search_tavily),
}


async def _web_search(ctx: ToolContext, a: dict) -> Any:
    query = str(a.get("query", "")).strip()
    if not query:
        raise ToolError("Error: query must not be empty")
    limit = int(a.get("limit") or 5)
    limit = max(1, min(limit, 20))
    pname = (os.environ.get("SEARCH_PROVIDER") or "duckduckgo").strip().lower()
    if pname not in _PROVIDERS:
        raise ToolError(f"web_search: unknown provider {pname!r} (want duckduckgo, tavily, or brave)")
    backend, fn = _PROVIDERS[pname]
    try:
        results = await fn(query, limit)
    except ToolError as e:
        raise ToolError(f"Error searching web via {backend}: {e}") from e
    except (httpx.HTTPError, ValueError) as e:
        raise ToolError(f"Error searching web via {backend}: {type(e).__name__}: {e}") from e
    out = json.dumps({"backend": backend, "query": query, "results": results}, indent=2, ensure_ascii=False)
    return truncate(out, MAX_OUTPUT)


TOOLS: list[Tool] = [
    Tool(
        "WebFetch",
        "Fetches an HTTP(S) HTML page, documentation page, or API response and returns it as Markdown. "
        "Refuses static assets (JS, CSS, source maps, images, fonts, audio, video). extract=true appends a "
        "RECON section (comments, scripts, stylesheets, endpoints, forms/hidden inputs, links, meta tags). "
        "Large responses may be truncated. External network request.",
        {"type": "object",
         "properties": {
             "url": {"type": "string", "description": "The http:// or https:// URL to fetch."},
             "extract": {"type": "boolean", "description": (
                 "When true, append a RECON section preserving HTML comments, <script src>, inline-script "
                 "endpoints, forms/inputs (including hidden), links and meta tags. Default false.")}},
         "required": ["url"]},
        _web_fetch, False, {"category": "web"}),
    Tool(
        "web_search",
        "Search the web and return a ranked list of results (title, URL, description) — metadata only, up to 5 "
        "by default. Use WebFetch to read the full content of a specific URL. This makes an external network "
        "request.",
        {"type": "object",
         "properties": {
             "query": {"type": "string", "description": "The search query."},
             "limit": {"type": "integer", "description": "Maximum number of results to return (1-20). Default 5.",
                       "minimum": 1, "maximum": 20}},
         "required": ["query"]},
        _web_search, True, {"category": "web"}),
]
