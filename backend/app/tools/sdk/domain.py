"""ARTEX domain tools (facts / findings / assets / scope / traces) on the Postgres task tables."""
from __future__ import annotations

import ipaddress
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

from app.tasks import service as svc
from app.tools.base import Tool, ToolContext, ToolError

_ID = {"type": ["integer", "string"]}
_SEVERITIES = ("info", "low", "medium", "high", "critical")
_ASSET_TYPES = ("root_domain", "ip", "subdomain", "app", "service", "endpoint")
_SCOPE_KINDS = ("company", "root_domain", "subdomain", "ip", "cidr", "icp", "keyword")


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def _need_task(ctx: ToolContext) -> int:
    if ctx.task_id is None:
        raise ToolError("this tool can only be used inside a task context")
    return int(ctx.task_id)


def _task_arg(ctx: ToolContext, a: dict, key: str = "task_id") -> int:
    v = a.get(key)
    if v is None or v == "":
        return _need_task(ctx)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ToolError(f"invalid {key}: {v!r}")


def _int(v: Any, name: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ToolError(f"invalid {name}: {v!r}")


def _jv(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "..."


async def _ensure_task(ctx: ToolContext, tid: int) -> None:
    if not await ctx.pool.fetchval("SELECT 1 FROM tasks WHERE id=$1 AND deleted_at IS NULL", tid):
        raise ToolError(f"task {tid} not found")


# ---------------------------------------------------------------- DTOs
def _fact(r: Any, full: bool = False) -> dict:
    d = {"id": r["id"], "task_id": r["task_id"], "kind": "fact",
         "summary": r["summary"] if full else _trunc(r["summary"], 160), "status": r["status"],
         "created_at": _iso(r["created_at"])}
    if full:
        d["detail"] = r["detail"]
    return d


def _finding(r: Any, full: bool = False) -> dict:
    d = {"id": r["id"], "finding_id": r["id"], "finding_node_id": r["id"], "task_id": r["task_id"],
         "kind": "finding", "vulnclass": r["vulnclass"], "name": r["name"], "severity": r["severity"],
         "summary": r["summary"], "status": r["status"], "created_at": _iso(r["created_at"])}
    if full:
        d.update(evidence=r["evidence"], report=r["report"], updated_at=_iso(r["updated_at"]))
    return d


def _asset(r: Any) -> dict:
    return {"id": r["id"], "type": r["type"], "value": r["value"], "tested": r["tested"],
            "attrs": _jv(r["attrs"]) or {}}


def _scope(r: Any) -> dict:
    return {"id": r["id"], "kind": r["kind"], "value": r["value"], "reason": r["reason"],
            "created_at": _iso(r["created_at"])}


# ---------------------------------------------------------------- facts
async def _record_fact(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    items = a.get("facts")
    batch = items is not None
    if not batch:
        items = [a]
    ids: list[int] = []
    errors: dict[str, str] = {}
    for i, f in enumerate(items):
        summary = str(f.get("summary") or "").strip()
        if not summary:
            ids.append(0)
            errors[str(i)] = "summary is required"
            continue
        detail = str(f.get("detail") or "")
        if f.get("evidence"):
            detail = (detail + "\n" if detail else "") + "Evidence: " + str(f["evidence"])
        conf = f.get("confidence")
        status = conf if conf in ("observed", "inferred") else "confirmed"
        fid = await ctx.pool.fetchval(
            "INSERT INTO task_facts (task_id, summary, detail, status) VALUES ($1,$2,$3,$4) RETURNING id",
            tid, summary, detail, status)
        ids.append(fid)
        aids = [int(x) for x in (f.get("asset_ids") or []) if str(x).lstrip("-").isdigit()]
        if aids:
            await ctx.pool.execute("UPDATE task_assets SET tested=TRUE WHERE task_id=$1 AND id = ANY($2::bigint[])",
                                   tid, aids)
    if batch:
        out: dict = {"ids": ids}
        out["errors"] = errors
        return out
    if not ids[0]:
        raise ToolError(errors.get("0", "summary is required"))
    return f"fact recorded: {ids[0]}"


async def _list_facts(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    limit = min(max(int(a.get("limit") or 20), 1), 100)
    before = int(a.get("before") or 0)
    q = (a.get("q") or "").strip()
    cond, args = "task_id=$1", [tid]
    if q:
        args.append(f"%{q}%")
        cond += f" AND summary ILIKE ${len(args)}"
    total = await ctx.pool.fetchval(f"SELECT count(*) FROM task_facts WHERE {cond}", *args)
    pargs = list(args)
    pcond = cond
    if before > 0:
        pargs.append(before)
        pcond += f" AND id < ${len(pargs)}"
    pargs.append(limit + 1)
    rows = await ctx.pool.fetch(f"SELECT * FROM task_facts WHERE {pcond} ORDER BY id DESC LIMIT ${len(pargs)}", *pargs)
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"facts": [_fact(r) for r in rows], "total": total, "has_more": has_more,
            "next_before": rows[-1]["id"] if has_more and rows else 0}


async def _node_detail(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    nid = _int(a["id"], "id")
    kind = a.get("kind")
    fact = finding = None
    if kind in (None, "", "fact"):
        r = await ctx.pool.fetchrow("SELECT * FROM task_facts WHERE id=$1 AND task_id=$2", nid, tid)
        fact = _fact(r, True) if r else None
    if kind in (None, "", "finding"):
        r = await ctx.pool.fetchrow("SELECT * FROM task_findings WHERE id=$1 AND task_id=$2", nid, tid)
        finding = _finding(r, True) if r else None
    if fact and finding:
        return {"ambiguous": "id matches both a fact and a finding; pass kind to select one",
                "fact": fact, "finding": finding}
    if fact or finding:
        return fact or finding
    raise ToolError(f"node {nid} not found")


# ---------------------------------------------------------------- findings
async def _report_finding(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    sev = str(a.get("severity") or "").lower()
    if sev not in _SEVERITIES:
        raise ToolError("severity must be one of critical|high|medium|low")
    vc = str(a.get("vulnclass") or "").strip()
    summ = str(a.get("summary") or "").strip()
    if not vc or not summ:
        raise ToolError("vulnclass and summary are required")
    r = await ctx.pool.fetchrow(
        "INSERT INTO task_findings (task_id, vulnclass, name, severity, summary, evidence) "
        "VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
        tid, vc, str(a.get("name") or ""), sev, summ, str(a.get("evidence") or ""))
    aids = [int(x) for x in (a.get("asset_ids") or []) if str(x).lstrip("-").isdigit()]
    if aids:
        await ctx.pool.execute("UPDATE task_assets SET tested=TRUE WHERE task_id=$1 AND id = ANY($2::bigint[])",
                               tid, aids)
    rec = _finding(r, True)
    rec["evidence_status"] = "bound" if a.get("traffic_refs") else "not_bound"
    rec["evidence_note"] = "traffic binding is not supported; evidence stored as text"
    return f"finding recorded: {r['id']}\n" + json.dumps(rec, ensure_ascii=False, default=str)


async def _list_findings(ctx: ToolContext, a: dict) -> Any:
    return await _findings_of(ctx, _need_task(ctx))


async def _findings_of(ctx: ToolContext, tid: int) -> list[dict]:
    rows = await ctx.pool.fetch("SELECT * FROM task_findings WHERE task_id=$1 ORDER BY id DESC", tid)
    return [_finding(r) for r in rows]


async def _list_task_findings(ctx: ToolContext, a: dict) -> Any:
    tid = _task_arg(ctx, a)
    await _ensure_task(ctx, tid)
    rows = await ctx.pool.fetch("SELECT * FROM task_findings WHERE task_id=$1 ORDER BY id DESC", tid)
    return [_finding(r, True) for r in rows]


async def _get_finding_record(ctx: ToolContext, a: dict) -> Any:
    fid = _int(a["finding_id"], "finding_id")
    r = await ctx.pool.fetchrow("SELECT * FROM task_findings WHERE id=$1", fid)
    if not r:
        raise ToolError(f"finding {fid} not found")
    return {"finding": _finding(r, True), "finding_node_id": r["id"]}


async def _update_finding_report(ctx: ToolContext, a: dict) -> Any:
    fid = _int(a["finding_id"], "finding_id")
    report = a.get("report")
    if not isinstance(report, str) or not report.strip():
        raise ToolError("report is required")
    r = await ctx.pool.fetchrow("UPDATE task_findings SET report=$2, updated_at=now() WHERE id=$1 RETURNING id",
                                fid, report)
    if not r:
        raise ToolError(f"finding {fid} not found")
    return f"finding {fid} report updated ({len(report)} chars)"


# ---------------------------------------------------------------- assets
def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _asset_key(it: dict) -> tuple[str, str]:
    t = it.get("type")
    if t not in _ASSET_TYPES:
        raise ToolError(f"type must be one of {', '.join(_ASSET_TYPES)}")
    if t in ("root_domain", "subdomain"):
        v = str(it.get("domain") or "").strip().lower()
        if not v:
            raise ToolError(f"domain is required for {t}")
        return t, v
    if t == "ip":
        v = str(it.get("ip") or "").strip()
        if not v:
            raise ToolError("ip is required for ip")
        if not _is_ip(v):
            raise ToolError(f"{v!r} is not a valid IPv4/IPv6 address (use type=subdomain for hostnames)")
        return t, v
    if t == "app":
        v = str(it.get("app_name") or "").strip()
        if not v:
            raise ToolError("app_name is required for app")
        return t, v
    if t == "service":
        url = str(it.get("url") or "").strip()
        if url:
            return t, url
        name, port = it.get("service_name"), it.get("port")
        host = it.get("ip") or it.get("domain")
        if not name or not port:
            raise ToolError("service requires url (HTTP) or service_name + port (non-HTTP)")
        if not host:
            raise ToolError("non-HTTP service requires at least one of ip/domain")
        return t, f"{name}://{host}:{port}"
    url, method = str(it.get("url") or "").strip(), str(it.get("method") or "").strip().upper()
    if not url or not method:
        raise ToolError("endpoint requires url and method")
    return t, f"{method} {url}"


def _merge(old: dict, new: dict) -> dict:
    out = dict(old)
    for k, v in new.items():
        if isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + [x for x in v if x not in out[k]]
        elif v not in (None, "", []):
            out[k] = v
    return out


async def _insert_assets(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    results: list[dict] = []
    errors: list[dict] = []
    for i, it in enumerate(a["assets"]):
        try:
            t, v = _asset_key(it)
            attrs = {k: x for k, x in it.items() if k != "type" and x not in (None, "", [])}
            if t == "service" and it.get("url"):
                attrs["service_type"] = "http"
            elif t == "service":
                attrs.setdefault("service_type", "other")
            async with ctx.pool.acquire() as conn, conn.transaction():
                row = await conn.fetchrow(
                    "SELECT id, attrs FROM task_assets WHERE task_id=$1 AND type=$2 AND value=$3 FOR UPDATE",
                    tid, t, v)
                if row:
                    merged = _merge(_jv(row["attrs"]) or {}, attrs)
                    await conn.execute("UPDATE task_assets SET attrs=$2::jsonb WHERE id=$1",
                                       row["id"], json.dumps(merged))
                    aid = row["id"]
                else:
                    aid = await conn.fetchval(
                        "INSERT INTO task_assets (task_id, type, value, attrs) VALUES ($1,$2,$3,$4::jsonb) "
                        "RETURNING id", tid, t, v, json.dumps(attrs))
            results.append({"index": i, "id": aid, "type": t})
        except ToolError as e:
            errors.append({"index": i, "error": str(e)})
    return {"results": results, "errors": errors}


# --- tiny DSL for list_assets
_TOK = re.compile(r'\(|\)|"[^"]*"|[^\s()]+')
_OPRE = re.compile(r"^(\w+?)(>=|<=|!=|==|=|>|<)(.*)$")
_NUM_FIELDS = {"port", "status_code", "company_id", "content_length"}
_ASSET_FIELDS = {"domain", "root_domain", "ip", "url", "page_title", "icp", "service_name", "app_name", "method",
                 "service_type", "record_type", "technology", "port", "status_code", "company_id"}


def _vals(asset: dict, field: str) -> list[Any]:
    at, typ, val = asset["attrs"], asset["type"], asset["value"]
    out: list[Any] = []

    def add(x: Any) -> None:
        if isinstance(x, list):
            for y in x:
                add(y)
        elif x not in (None, ""):
            out.append(x)

    if field == "technology":
        add(at.get("technologies"))
    elif field == "domain":
        add(at.get("domain"))
        add(at.get("bound_domains"))
        if at.get("url"):
            add(urlparse(at["url"]).hostname)
    elif field == "root_domain":
        add(at.get("root_domain"))
        if typ == "root_domain":
            add(val)
        for d in _vals(asset, "domain"):
            add(".".join(str(d).split(".")[-2:]))
    elif field == "port":
        add(at.get("port"))
        add([p.get("port") for p in at.get("open_ports") or [] if isinstance(p, dict)])
        if at.get("url"):
            try:
                p = urlparse(at["url"])
                add(p.port or (443 if p.scheme == "https" else 80))
            except ValueError:
                pass
    elif field == "ip":
        add(at.get("ip"))
        if typ == "ip":
            add(val)
    elif field == "icp":
        add(at.get("icp"))
        add(at.get("app_icp"))
    else:
        add(at.get(field))
    return out


def _cmp(have: Any, op: str, want: str, numeric: bool) -> bool:
    if numeric:
        try:
            h, w = float(have), float(want)
        except (TypeError, ValueError):
            return False
        return {"=": h == w, "==": h == w, "!=": h != w, ">": h > w, ">=": h >= w, "<": h < w, "<=": h <= w}[op]
    h, w = str(have).lower(), want.lower()
    if op == "==":
        return h == w
    if op == "!=":
        return h != w
    if op == "=":
        return w in h
    try:
        return {">": h > w, ">=": h >= w, "<": h < w, "<=": h <= w}[op]
    except KeyError:
        return False


def _either(a: Any, b: Any) -> Any:
    return lambda x: a(x) or b(x)


def _both(a: Any, b: Any) -> Any:
    return lambda x: a(x) and b(x)


def _compile_dsl(dsl: str):
    toks = _TOK.findall(dsl)
    pos = 0

    def peek() -> str | None:
        return toks[pos] if pos < len(toks) else None

    def parse_or():
        nonlocal pos
        left = parse_and()
        while peek() and peek().upper() == "OR":  # type: ignore[union-attr]
            pos += 1
            right = parse_and()
            left = _either(left, right)
        return left

    def parse_and():
        nonlocal pos
        left = parse_term()
        while peek() and peek().upper() == "AND":  # type: ignore[union-attr]
            pos += 1
            right = parse_term()
            left = _both(left, right)
        return left

    def parse_term():
        nonlocal pos
        t = peek()
        if t is None:
            raise ToolError("invalid dsl: unexpected end")
        pos += 1
        if t == "(":
            e = parse_or()
            if peek() != ")":
                raise ToolError("invalid dsl: missing ')'")
            pos += 1
            return e
        if t == ")":
            raise ToolError("invalid dsl: unexpected ')'")
        m = _OPRE.match(t)
        if m and m.group(1) in _ASSET_FIELDS:
            field, op, val = m.groups()
            if val == "":
                val = peek() or ""
                pos += 1
            val = val.strip('"')
            numeric = field in _NUM_FIELDS
            return lambda x: any(_cmp(h, op, val, numeric) for h in _vals(x, field))
        word = t.strip('"').lower()
        return lambda x: word in (x["type"] + " " + x["value"] + " " + json.dumps(x["attrs"], default=str)).lower()

    fn = parse_or()
    if pos != len(toks):
        raise ToolError("invalid dsl: trailing tokens")
    return fn


async def _list_assets(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    limit = max(int(a.get("limit") or 10), 1)
    offset = max(int(a.get("offset") or 0), 0)
    ids = [_int(x, "ids") for x in (a.get("ids") or [])]
    if a.get("id") is not None:
        ids.append(_int(a["id"], "id"))
    if ids:
        rows = await ctx.pool.fetch(
            "SELECT * FROM task_assets WHERE task_id=$1 AND id = ANY($2::bigint[]) ORDER BY id", tid, ids)
        assets = [_asset(r) for r in rows]
        return {"count": len(assets), "assets": assets[offset:offset + limit]}
    dsl = (a.get("dsl") or "").strip()
    if not dsl:
        raise ToolError("dsl must be non-empty when id/ids is not given")
    pred = _compile_dsl(dsl)
    args: list[Any] = [tid]
    sql = "SELECT * FROM task_assets WHERE task_id=$1"
    if a.get("type"):
        args.append(a["type"])
        sql += " AND type=$2"
    rows = await ctx.pool.fetch(sql + " ORDER BY id LIMIT 20000", *args)
    matched = [x for x in (_asset(r) for r in rows) if pred(x)]
    return {"count": len(matched), "assets": matched[offset:offset + limit]}


async def _list_untested_assets(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    page = max(int(a.get("page") or 1), 1)
    size = min(max(int(a.get("page_size") or 10), 1), 200)
    args: list[Any] = [tid]
    cond = "task_id=$1 AND NOT tested"
    if a.get("type"):
        args.append(a["type"])
        cond += " AND type=$2"
    total = await ctx.pool.fetchval(f"SELECT count(*) FROM task_assets WHERE {cond}", *args)
    rows = await ctx.pool.fetch(
        f"SELECT id, type, value FROM task_assets WHERE {cond} ORDER BY id LIMIT {size} OFFSET {(page - 1) * size}",
        *args)
    return {"assets": [{"id": r["id"], "type": r["type"], "label": r["value"]} for r in rows],
            "total": total, "page": page, "page_size": size}


# ---------------------------------------------------------------- scope / companies
def _classify(line: str) -> tuple[str, str] | None:
    """Return (kind, value) for one scope line, or raise ToolError if rejected."""
    s = line.strip()
    if not s:
        return None
    if re.search(r"ICP\S*\d{4,}", s, re.IGNORECASE):
        return "icp", s
    if "/" in s and "://" not in s:
        try:
            net = ipaddress.ip_network(s, strict=False)
        except ValueError:
            raise ToolError(f"invalid CIDR: {s}")
        lo, hi = (16, 32) if net.version == 4 else (32, 128)
        if not lo <= net.prefixlen <= hi:
            raise ToolError(f"CIDR prefix too broad or invalid ({'IPv4 /16-/32' if net.version == 4 else 'IPv6 /32-/128'}): {s}")
        return "cidr", str(net)
    if _is_ip(s):
        return "ip", s
    if "://" in s:
        host = urlparse(s).hostname
        if not host:
            raise ToolError(f"invalid URL: {s}")
        s = host
        if _is_ip(s):
            return "ip", s
    if re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", s.lower()):
        return "root_domain", s.lower()
    if re.match(r"^\.?[a-z]{2,}$", s.lower()) and s.startswith("."):
        raise ToolError(f"bare TLD rejected: {s}")
    return "keyword", s


_COMPANY_TAG = "company:"


async def _scope_insert(ctx: ToolContext, tid: int, kind: str, value: str, reason: str) -> tuple[int, bool]:
    r = await ctx.pool.fetchrow(
        "INSERT INTO task_scope (task_id, kind, value, reason) VALUES ($1,$2,$3,$4) "
        "ON CONFLICT (task_id, kind, value) DO NOTHING RETURNING id", tid, kind, value, reason)
    if r:
        return r["id"], True
    return await ctx.pool.fetchval("SELECT id FROM task_scope WHERE task_id=$1 AND kind=$2 AND value=$3",
                                   tid, kind, value), False


async def _add_company_scope(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    company = str(a.get("company") or "").strip()
    if not company:
        raise ToolError("company is required")
    reason = str(a.get("reason") or "").strip()
    if not reason:
        raise ToolError("reason is required (basis for attribution)")
    cid, _ = await _scope_insert(ctx, tid, "company", company, reason)
    added = skipped = invalid = 0
    errors: list[str] = []
    for line in str(a.get("scope") or "").splitlines():
        try:
            c = _classify(line)
        except ToolError as e:
            invalid += 1
            errors.append(str(e))
            continue
        if c is None:
            continue
        _, new = await _scope_insert(ctx, tid, c[0], c[1], f"{_COMPANY_TAG}{company}; {reason}")
        added += new
        skipped += not new
    out: dict = {"company_id": cid, "added": added, "skipped": skipped, "invalid": invalid}
    if errors:
        out["errors"] = errors
    return out


async def _add_task_scope(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    reason = str(a.get("reason") or "").strip()
    if not reason:
        raise ToolError("reason is required")
    entries = a.get("entries") or ([{"kind": a.get("kind"), "value": a.get("value")}] if a.get("kind") else [])
    if not entries:
        raise ToolError("provide kind+value or entries")
    added: list[dict] = []
    errors: list[str] = []
    for e in entries:
        kind, value = str(e.get("kind") or ""), str(e.get("value") or "").strip()
        if kind not in _SCOPE_KINDS or not value:
            errors.append(f"invalid entry {e!r}: kind must be one of {'/'.join(_SCOPE_KINDS)} and value non-empty")
            continue
        try:
            if kind == "cidr":
                value = _classify(value if "/" in value else value + "/32")[1]  # type: ignore[index]
            elif kind in ("root_domain", "subdomain"):
                value = value.lower()
                if "." not in value.strip("."):
                    raise ToolError(f"bare TLD rejected: {value}")
            elif kind == "ip" and not _is_ip(value):
                raise ToolError(f"invalid IP: {value}")
        except ToolError as ex:
            errors.append(str(ex))
            continue
        sid, new = await _scope_insert(ctx, tid, kind, value, reason)
        added.append({"id": sid, "kind": kind, "value": value, "new": new})
    out: dict = {"added": added}
    if errors:
        out["errors"] = errors
    return out


async def _list_companies(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    search = str(a.get("search") or "").strip().lower()
    rows = await ctx.pool.fetch("SELECT * FROM task_scope WHERE task_id=$1 ORDER BY id", tid)
    companies = [r for r in rows if r["kind"] == "company" and (not search or search in r["value"].lower())]
    out = []
    for c in companies:
        tag = f"{_COMPANY_TAG}{c['value']};"
        scope = [_scope(r) for r in rows if r["kind"] != "company" and r["reason"].startswith(tag)]
        out.append({"id": c["id"], "name": c["value"], "reason": c["reason"], "asset_count": 0, "scope": scope})
    cids = {x["id"]: x for x in out}
    if cids:
        arows = await ctx.pool.fetch("SELECT attrs FROM task_assets WHERE task_id=$1", tid)
        for r in arows:
            cid = (_jv(r["attrs"]) or {}).get("company_id")
            if cid in cids:
                cids[cid]["asset_count"] += 1
    return {"count": len(out), "companies": out}


# ---------------------------------------------------------------- hints
async def _add_hint(ctx: ToolContext, a: dict) -> Any:
    tid = _need_task(ctx)
    hints = a.get("hints")
    if hints is None:
        text = a.get("text")
        if not text or not str(text).strip():
            raise ToolError("text is required")
        h = await svc.add_hint(ctx.pool, tid, str(text))
        return f"hint added: {h['id']}"
    ids: list[int] = []
    errors: dict[str, str] = {}
    for i, h in enumerate(hints):
        try:
            r = await svc.add_hint(ctx.pool, tid, str((h or {}).get("text") or ""))
            ids.append(int(r["id"]))
        except (svc.TaskError, ToolError) as e:
            ids.append(0)
            errors[str(i)] = getattr(e, "message", str(e))
    return {"ids": ids, "errors": errors}


# ---------------------------------------------------------------- LLM profiles
async def _list_llm_profiles(ctx: ToolContext, a: dict) -> Any:
    model = os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
    base = os.environ.get("LLM_BASE_URL", "") or "https://api.anthropic.com"
    host = urlparse(base).netloc or base
    return {"profiles": [{"id": "default", "name": "default", "model": model, "format": "anthropic",
                          "base_host": host, "is_active": True}]}


# ---------------------------------------------------------------- graph overview
async def _overview(ctx: ToolContext, tid: int) -> dict:
    pool = ctx.pool
    t = await pool.fetchrow("SELECT * FROM tasks WHERE id=$1 AND deleted_at IS NULL", tid)
    if not t:
        raise ToolError(f"task {tid} not found")
    goals = await pool.fetch("SELECT id, text, met, evidence FROM task_goals WHERE task_id=$1 ORDER BY id", tid)
    hints = await pool.fetch("SELECT id, text FROM task_hints WHERE task_id=$1 ORDER BY id", tid)
    facts = await pool.fetch("SELECT * FROM task_facts WHERE task_id=$1 ORDER BY id DESC LIMIT 20", tid)
    facts_total = await pool.fetchval("SELECT count(*) FROM task_facts WHERE task_id=$1", tid)
    fcounts = await pool.fetch(
        "SELECT severity, count(*) c FROM task_findings WHERE task_id=$1 GROUP BY severity", tid)
    flatest = await pool.fetch("SELECT * FROM task_findings WHERE task_id=$1 ORDER BY id DESC LIMIT 10", tid)
    atypes = await pool.fetch(
        "SELECT type, count(*) c, count(*) FILTER (WHERE tested) tested FROM task_assets WHERE task_id=$1 "
        "GROUP BY type ORDER BY type", tid)
    scope = await pool.fetch("SELECT * FROM task_scope WHERE task_id=$1 ORDER BY id", tid)
    total = sum(r["c"] for r in atypes)
    tested = sum(r["tested"] for r in atypes)
    by_sev = {r["severity"]: r["c"] for r in fcounts}
    return {
        "task": {"id": tid, "name": t["name"], "description": t["description"], "goal": t["goal"],
                 "status": "paused" if t["paused"] else t["status"]},
        "goals": [{"id": r["id"], "text": r["text"], "met": r["met"], "evidence": r["evidence"]} for r in goals],
        "hints": [{"id": r["id"], "text": r["text"]} for r in hints],
        "facts": {"total": facts_total, "recent": [_fact(r) for r in facts]},
        "findings": {"total": sum(by_sev.values()), "by_severity": by_sev, "latest": [_finding(r) for r in flatest]},
        "assets": {"total": total, "by_type": {r["type"]: r["c"] for r in atypes}, "untested": total - tested},
        "scope": [_scope(r) for r in scope],
        "coverage": {"denominator": total, "tested": tested,
                     "by_type": {r["type"]: {"total": r["c"], "tested": r["tested"]} for r in atypes},
                     "pct": round(100.0 * tested / total, 1) if total else 0.0},
    }


async def _graph_overview(ctx: ToolContext, a: dict) -> Any:
    return await _overview(ctx, _need_task(ctx))


async def _get_task_graph(ctx: ToolContext, a: dict) -> Any:
    return await _overview(ctx, _task_arg(ctx, a))


# ---------------------------------------------------------------- worker traces (task_events)
def _step_summary(r: Any) -> dict:
    inp = json.dumps(_jv(r["input"]), ensure_ascii=False, default=str) if r["input"] is not None else ""
    out = json.dumps(_jv(r["output"]), ensure_ascii=False, default=str) if r["output"] is not None else ""
    return {"step_id": r["id"], "kind": r["kind"], "tool_name": r["tool_name"],
            "input": _trunc(inp, 200), "output": _trunc(out, 200), "created_at": _iso(r["created_at"])}


def _step_full(r: Any) -> dict:
    return {"step_id": r["id"], "kind": r["kind"], "tool_name": r["tool_name"], "input": _jv(r["input"]),
            "output": _jv(r["output"]), "created_at": _iso(r["created_at"])}


async def _list_traces(ctx: ToolContext, a: dict) -> Any:
    tid = _task_arg(ctx, a)
    await _ensure_task(ctx, tid)
    rows = await ctx.pool.fetch(
        "SELECT kind, count(*) c, min(id) first_id, max(id) last_id FROM task_events WHERE task_id=$1 "
        "GROUP BY kind ORDER BY min(id)", tid)
    return {"task_id": tid, "works": [{"intent_id": 0, "name": "task run", "kinds": [
        {"kind": r["kind"], "steps": r["c"], "first_step_id": r["first_id"], "last_step_id": r["last_id"]}
        for r in rows]}], "steps": sum(r["c"] for r in rows)}


async def _get_trace(ctx: ToolContext, a: dict) -> Any:
    tid = _task_arg(ctx, a)
    await _ensure_task(ctx, tid)
    ids = [_int(x, "step_ids") for x in (a.get("step_ids") or [])]
    if ids:
        take, omitted = ids[:5], ids[5:]
        rows = await ctx.pool.fetch(
            "SELECT * FROM task_events WHERE task_id=$1 AND id = ANY($2::bigint[]) ORDER BY id", tid, take)
        out: dict = {"steps": [_step_full(r) for r in rows]}
        if omitted:
            out["omitted_step_ids"] = omitted
        return out
    rows = await ctx.pool.fetch("SELECT * FROM task_events WHERE task_id=$1 ORDER BY id DESC LIMIT 100", tid)
    return {"steps": [_step_summary(r) for r in reversed(rows)]}


async def _search_traces(ctx: ToolContext, a: dict) -> Any:
    tid = _task_arg(ctx, a)
    q = str(a.get("q") or "").strip()
    if not q:
        raise ToolError("q is required")
    await _ensure_task(ctx, tid)
    rows = await ctx.pool.fetch(
        "SELECT * FROM task_events WHERE task_id=$1 AND (tool_name ILIKE $2 OR input::text ILIKE $2 "
        "OR output::text ILIKE $2) ORDER BY id DESC LIMIT 50", tid, f"%{q}%")
    return {"matches": [{**_step_summary(r), "intent_id": 0} for r in rows]}


# ---------------------------------------------------------------- tool table
def _t(name: str, desc: str, schema: dict, run: Any, cat: str, ro: bool = False) -> Tool:
    return Tool(name, desc, schema, run, ro, {"category": cat})


_FACT_ITEM = {
    "summary": {"type": "string", "description": "One-sentence summary of this exploration conclusion (a digest of detail)"},
    "detail": {"type": "string", "description": "Relevant details of this fact: put all the observations from this exploration here"},
    "evidence": {"type": "string", "description": "One line of key evidence: the command + the one or two output lines that best prove the conclusion."},
    "confidence": {"type": "string", "enum": ["observed", "inferred"],
                   "description": "observed (seen directly in the output) | inferred (deduced from observed behavior)."},
    "intent_id": {"type": "integer", "description": "Id of the intent that produced this fact (unused in this build)"},
    "asset_ids": {"type": "array", "items": {"type": "integer"}, "description": "Related asset ids; they are marked as tested"},
}

_ASSET_ITEM = {
    "type": {"type": "string", "enum": list(_ASSET_TYPES), "description": "Asset type"},
    "domain": {"type": "string", "description": "Root domain or subdomain (required for root_domain/subdomain)"},
    "icp": {"type": "string", "description": "ICP filing number (optional)"},
    "record_type": {"type": "string", "description": "DNS record type: A/AAAA/CNAME/MX, etc. (optional for subdomain)"},
    "record_value": {"type": "array", "items": {"type": "string"}, "description": "List of DNS record values (optional for subdomain)"},
    "ip": {"type": "string", "description": "IP address; must be an IPv4/IPv6 address, not a hostname; required for type ip; optional for service/endpoint"},
    "bound_domains": {"type": "array", "items": {"type": "string"}, "description": "Domains bound to this IP (optional for ip type)"},
    "open_ports": {"type": "array", "items": {"type": "object", "properties": {"port": {"type": "integer"}, "service": {"type": "string"}}, "required": ["port"]},
                   "description": "Open ports (optional for ip type)"},
    "app_name": {"type": "string", "description": "Application name (required for app type)"},
    "bundle_id": {"type": "string", "description": "Bundle ID (optional for app type)"},
    "category": {"type": "string", "description": "Application category (optional)"},
    "description": {"type": "string", "description": "Application description (optional)"},
    "app_icp": {"type": "string", "description": "Application ICP filing (optional)"},
    "company_id": {"type": "integer", "description": "Owning company id (optional for app type; returned by add_company_scope)"},
    "url": {"type": "string", "description": "Full URL including scheme and port (required for HTTP services and endpoints)"},
    "status_code": {"type": "integer", "description": "HTTP response status code (optional)"},
    "content_length": {"type": "integer", "description": "HTTP response body size in bytes (optional)"},
    "page_title": {"type": "string", "description": "Page <title> content (optional)"},
    "favicon_mmh3": {"type": "string", "description": "favicon MMH3 hash (optional)"},
    "technologies": {"type": "array", "items": {"type": "string"}, "description": "Fingerprint/tech-stack list (optional)"},
    "auth": {"type": "array", "items": {"type": "object"}, "description": "Discovered credentials, each with type/username/password (appended, not overwritten)"},
    "service_name": {"type": "string", "description": "Service name, e.g. ssh/mysql/redis (required for non-HTTP service)"},
    "port": {"type": "integer", "description": "Port number (required for non-HTTP service)"},
    "method": {"type": "string", "description": "HTTP method (required for endpoint)"},
    "params": {"type": "array", "items": {"type": "object"}, "description": "Request parameters with location/name/value/type (appended, not overwritten)"},
}

_TASK_ID = {"type": ["string", "integer"], "description": "Task id"}

TOOLS: list[Tool] = [
    _t("graph_overview",
       "(Exploration graph) Distilled summary of the exploration state: goals, hints, recent facts, findings, "
       "asset counts and test coverage. Call this first when planning.",
       _obj({}), _graph_overview, "task", True),
    _t("get_task_graph",
       "Read the exploration graph overview of the specified task (same as graph_overview); specify the task with task_id.",
       _obj({"task_id": _TASK_ID}, ["task_id"]), _get_task_graph, "task", True),
    _t("list_facts",
       "List the [exploration facts/conclusions] of this task page by page, newest first (compact: id + summary + "
       "status; long summaries are truncated, use node_detail(id) for the full text). All parameters are optional: "
       "limit (default 20, max 100), before (cursor; pass the next_before returned by the previous page), q (filter by "
       "summary keyword). Returns {facts, total, has_more, next_before}. For findings, see list_findings.",
       _obj({"limit": {"type": "integer", "description": "Number of items to return; default 20, max 100"},
             "before": {"type": "integer", "description": "Pagination cursor: return only older facts with id below this value; omitted or 0 = latest page"},
             "q": {"type": "string", "description": "Filter by fact summary keyword (case-insensitive); omitted = no filter"}}),
       _list_facts, "task", True),
    _t("record_fact",
       "Write exploration [facts/conclusions] into the task graph, including positive (fingerprints/enumeration) and "
       "negative ('port closed' / 'not injectable') conclusions. Combine the multiple observations of one exploration "
       "into a single fact: summary = one-sentence conclusion, detail = relevant details. Write only conclusions you "
       "actually saw. Use the facts array to write several distinct conclusions at once. Returns 'fact recorded: <id>' "
       "or, in batch mode, {ids[], errors{}}.",
       _obj({"facts": {"type": "array", "items": _obj(_FACT_ITEM), "description": "Array of facts; element fields are the same as the top-level fields."},
             **_FACT_ITEM}),
       _record_fact, "task"),
    _t("node_detail",
       "Get the full content of an exploration node of this task by id (a fact or a finding id returned by "
       "list_facts/list_findings/graph_overview). Pass kind when the id could be either; for assets use list_assets.",
       _obj({"id": {"type": ["integer", "string"], "description": "Exploration node id (not an asset id)"},
             "kind": {"type": "string", "enum": ["fact", "finding"], "description": "Optional: which table the id belongs to"}},
            ["id"]),
       _node_detail, "task", True),
    _t("report_finding",
       "Record a confirmed vulnerability, using evidence to provide verifiable proof such as command output and logs. "
       "Returns 'finding recorded: <id>' followed by the record JSON.",
       _obj({"vulnclass": {"type": "string", "description": "Vulnerability class"},
             "name": {"type": "string", "description": "Vulnerability name"},
             "severity": {"type": "string", "description": "critical|high|medium|low"},
             "summary": {"type": "string", "description": "Finding summary"},
             "intent_id": {"type": "integer", "description": "Intent id of the current task (unused in this build)"},
             "asset_ids": {"type": "array", "items": {"type": "integer"}, "description": "Affected asset ids"},
             "evidence": {"type": "string", "description": "Evidence / PoC text"},
             "evidence_hint_id": {"type": "integer", "description": "Optional hint id (accepted, not used in this build)"},
             "traffic_refs": {"type": "array", "items": {"type": "object"}, "description": "Optional traffic references (accepted, not used in this build)"}},
            ["vulnclass", "severity", "summary"]),
       _report_finding, "finding"),
    _t("list_findings",
       "List the [confirmed findings] of this task (compact: id + vulnclass + severity + summary + status). Use "
       "list_facts for ordinary exploration facts and node_detail(id) for details.",
       _obj({}), _list_findings, "finding", True),
    _t("list_task_findings",
       "Read the confirmed findings of the specified task (including evidence and report); specify the task with task_id.",
       _obj({"task_id": _TASK_ID}, ["task_id"]), _list_task_findings, "finding", True),
    _t("get_finding_record",
       "Read the complete original evidence and report of a finding.",
       _obj({"finding_id": {"type": ["integer", "string"], "description": "Finding record ID"}}, ["finding_id"]),
       _get_finding_record, "finding", True),
    _t("update_finding_report",
       "Write/update the [detailed report] for a registered finding (full Markdown text; replaces the old content "
       "entirely). Pass the id returned by report_finding as finding_id. The report should include: vulnerability "
       "overview, impact and risk, reproduction steps, evidence/PoC, and remediation advice.",
       _obj({"finding_id": {"type": ["integer", "string"], "description": "Finding id returned by report_finding; number or numeric string accepted"},
             "report": {"type": "string", "description": "Full Markdown report"},
             "evidence_version": {"type": "integer", "description": "Accepted for compatibility; ignored"}},
            ["finding_id", "report"]),
       _update_finding_report, "finding"),
    _t("insert_assets",
       "Register newly discovered assets in bulk; a single call may mix several types. Required fields per type: "
       "root_domain -> domain; ip -> ip (must be IPv4/IPv6, not a hostname); subdomain -> domain; app -> app_name; "
       "service (HTTP) -> url; service (non-HTTP) -> service_name + port (and ip or domain); endpoint -> url + method. "
       "auth/technologies/params are merged by appending and do not overwrite existing values. "
       "Returns {results:[{index,id,type}], errors:[{index,error}]}.",
       _obj({"assets": {"type": "array", "items": _obj(_ASSET_ITEM, ["type"]),
                        "description": "Array of assets; each element corresponds to one asset record"}}, ["assets"]),
       _insert_assets, "asset"),
    _t("list_assets",
       "Query the asset library: search with a DSL expression, or fetch directly by id/ids; pagination is supported. "
       "DSL: field=value fuzzy (ILIKE) | field==value exact | field!=value exclude | numeric fields support > >= < <= "
       "| a bare word = full-text fuzzy; combine with AND/OR (AND has higher precedence); parentheses may be used. "
       "Use the separate type parameter for asset type. When id/ids is not passed, dsl must be non-empty. "
       "Fields: domain, root_domain, ip, url, page_title, icp, service_name, app_name, method, service_type, "
       "record_type, technology, port/status_code/company_id (integers). "
       "Examples: status_code>=400 AND technology=shiro ; (port==80 OR port==443) AND technology=nginx. "
       "Returns {count, assets[]}.",
       _obj({"dsl": {"type": "string", "description": "DSL query expression. Must be non-empty when id/ids is not passed."},
             "type": {"type": "string", "description": "Asset type filter: root_domain|ip|subdomain|app|service|endpoint"},
             "id": {"type": "integer", "description": "Fetch directly by a single asset id"},
             "ids": {"type": "array", "items": {"type": "integer"}, "description": "Fetch directly by multiple asset ids"},
             "limit": {"type": "integer", "description": "Maximum number of results, default 10"},
             "offset": {"type": "integer", "description": "Pagination offset, default 0"}}),
       _list_assets, "asset", True),
    _t("list_untested_assets",
       "Query assets of this task that are not yet covered by any fact (not marked tested). Optionally filter by asset "
       "type. Pagination: page starts at 1, page_size defaults to 10. Returns {assets:[{id,type,label}], total, page, page_size}.",
       _obj({"type": {"type": "string", "description": "Asset type filter (optional): root_domain/subdomain/service/app/endpoint/ip"},
             "page": {"type": "integer", "description": "Page number, starting at 1 (default 1)"},
             "page_size": {"type": "integer", "description": "Page size (default 10)"}}),
       _list_untested_assets, "asset", True),
    _t("add_task_scope",
       "Add test scope to this task: the authorization boundary of the task and the denominator of asset test "
       "coverage. Kinds: company / root_domain / subdomain / ip / cidr / icp / keyword. Always give a reason stating "
       "the basis (auditable). Use the entries array for multiple items. Returns {added:[...], errors?}.",
       _obj({"entries": {"type": "array", "items": _obj({"kind": {"type": "string"}, "value": {"type": "string"}}),
                         "description": "Batch: [{kind, value}]"},
             "kind": {"type": "string", "enum": list(_SCOPE_KINDS), "description": "[Single] scope kind"},
             "value": {"type": "string", "description": "[Single] Company name / domain / IP / CIDR / ICP / keyword"},
             "reason": {"type": "string", "description": "Basis for adding (for auditing); required"}},
            ["reason"]),
       _add_task_scope, "asset"),
    _t("list_companies",
       "List the companies recorded in this task's scope, with their scope entries and linked asset counts. Used to "
       "obtain company_id (for insert_assets apps, or list_assets filtering by company_id). The optional search "
       "filters fuzzily by company name; leave empty to return all.",
       _obj({"search": {"type": "string", "description": "Filter fuzzily by company name (optional, case-insensitive)"}}),
       _list_companies, "asset", True),
    _t("add_company_scope",
       "Add domains/IPs/CIDRs/ICP filings/company keywords to a company's asset scope. Company names are unique: if "
       "the company does not exist it is created, otherwise reused. scope is one entry per line, recognized "
       "automatically: root domain / URL / single IP / CIDR range / ICP filing / company keyword. Always give a reason "
       "stating the basis (whois/certificate/ASN, etc.). Guardrails: bare TLDs and overly broad ranges are rejected "
       "(IPv4 prefix must be /16-/32, IPv6 prefix must be /32-/128); invalid lines are skipped and returned in errors. "
       "Returns {company_id, added, skipped, invalid, errors?}.",
       _obj({"company": {"type": "string", "description": "Company name (created if it does not exist, reused if it does)"},
             "scope": {"type": "string", "description": "Asset scope, one entry per line: domain / URL / IP / CIDR / ICP filing / company keyword"},
             "reason": {"type": "string", "description": "Basis for attribution (evidence/source); required"},
             "logo": {"type": "string", "description": "Company logo URL (optional; ignored in this build)"}},
            ["company", "scope", "reason"]),
       _add_company_scope, "asset"),
    _t("list_llm_profiles",
       "List available LLM profiles: id, name, model, format, base host and whether it is the active profile. API keys "
       "are never included.",
       _obj({}), _list_llm_profiles, "task", True),
    _t("add_hint",
       "Attach a strategic hint from a human or the main agent to this task; it is injected into the agent's context "
       "on the next step. Prefer batching: submit multiple hints in the hints array. For a single hint, give the "
       "top-level text.",
       _obj({"hints": {"type": "array", "items": _obj({"text": {"type": "string", "description": "Hint content"}}, ["text"]),
                       "description": "[Preferred] Array of hints to add, processed in order. Returns ids of the same length and order."},
             "text": {"type": "string", "description": "[Single] Hint content, e.g. 'focus on post-authentication endpoints'"}}),
       _add_hint, "task"),
    _t("get_task_worker_trace",
       "View the execution record of the specified task (its logged steps): get_task_worker_trace(task_id) shows step "
       "summaries; add step_ids=[...] to fetch the full content of those steps (at most 5 at a time; extra ids are "
       "listed in omitted_step_ids).",
       _obj({"task_id": _TASK_ID, "intent_id": {"type": "integer", "description": "Accepted for compatibility; ignored"},
             "step_ids": {"type": "array", "items": {"type": "integer"}, "description": "At most 5; extras listed in omitted_step_ids"}},
            ["task_id"]),
       _get_trace, "task", True),
    _t("list_task_worker_traces",
       "List the recorded execution of the specified task with its step counts, to find what is worth reviewing "
       "(then use get_task_worker_trace).",
       _obj({"task_id": _TASK_ID}, ["task_id"]), _list_traces, "task", True),
    _t("search_task_worker_traces",
       "Search the recorded execution steps of the specified task by keyword (returns matching step summaries).",
       _obj({"task_id": _TASK_ID, "q": {"type": "string", "description": "Search keyword"}}, ["task_id", "q"]),
       _search_traces, "task", True),
]
