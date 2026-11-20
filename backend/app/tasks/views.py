"""Read-only derived views: sessions, entities, graph, markdown report."""
import json
import re
from datetime import UTC, datetime
from typing import Any

from app.tasks import service as svc

MAX_NODES = 400
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


def _j(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _tool_failed(r: Any) -> bool:
    out = _j(r["output"])
    return isinstance(out, dict) and out.get("ok") is False


async def _events(pool: Any, task_id: int) -> list[Any]:
    return await pool.fetch("SELECT * FROM task_events WHERE task_id=$1 ORDER BY id", int(task_id))


# ------------------------------------------------------------------ sessions
def _blank(run_no: int | None, label: str) -> dict:
    return {"run_no": run_no, "label": label, "started_at": None, "ended_at": None, "steps": 0, "tool_calls": 0,
            "errors": 0, "input_tokens": 0, "output_tokens": 0}


def _sessions_from(events: list[Any], task: dict) -> dict:
    by: dict[int | None, dict] = {}
    first: dict[int | None, Any] = {}
    last: dict[int | None, Any] = {}
    for r in events:
        rn = r["run_no"]
        if rn is None and r["kind"] == "status":
            continue  # lifecycle noise, not a manual call
        s = by.setdefault(rn, _blank(rn, f"Run {rn}" if rn is not None else "Manual calls"))
        first.setdefault(rn, r["created_at"])
        last[rn] = r["created_at"]
        if r["kind"] == "llm":
            s["steps"] += 1
            u = (_j(r["output"]) or {}).get("usage") or {}
            s["input_tokens"] += int(u.get("input_tokens") or 0)
            s["output_tokens"] += int(u.get("output_tokens") or 0)
        elif r["kind"] == "tool_call":
            s["tool_calls"] += 1
            if _tool_failed(r):
                s["errors"] += 1
        elif r["kind"] == "error":
            s["errors"] += 1
    runs = sorted(k for k in by if k is not None)
    live = task["status"] == "running"
    out = []
    for k in [*runs, *([None] if None in by else [])]:
        s = by[k]
        s["started_at"] = _iso(first[k])
        s["ended_at"] = None if (live and k == (runs[-1] if runs else None)) else _iso(last[k])
        out.append(s)
    totals = {f: sum(s[f] for s in out) for f in ("steps", "tool_calls", "errors", "input_tokens", "output_tokens")}
    return {"sessions": out, "totals": totals}


async def sessions(pool: Any, task_id: int) -> dict:
    task = await svc.get_task(pool, task_id)
    return _sessions_from(await _events(pool, task_id), task)


# ------------------------------------------------------------------ entities
async def findings(pool: Any, task_id: int) -> list[dict]:
    await svc.get_task(pool, task_id)
    rows = await pool.fetch("SELECT * FROM task_findings WHERE task_id=$1 ORDER BY id DESC", int(task_id))
    return [{"id": str(r["id"]), "task_id": str(r["task_id"]), "vulnclass": r["vulnclass"], "name": r["name"],
             "severity": r["severity"], "summary": r["summary"], "evidence": r["evidence"], "report": r["report"],
             "status": r["status"], "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"])}
            for r in rows]


async def facts(pool: Any, task_id: int) -> list[dict]:
    await svc.get_task(pool, task_id)
    rows = await pool.fetch("SELECT * FROM task_facts WHERE task_id=$1 ORDER BY id DESC", int(task_id))
    return [{"id": str(r["id"]), "task_id": str(r["task_id"]), "summary": r["summary"], "detail": r["detail"],
             "status": r["status"], "created_at": _iso(r["created_at"])} for r in rows]


async def assets(pool: Any, task_id: int) -> list[dict]:
    await svc.get_task(pool, task_id)
    rows = await pool.fetch("SELECT * FROM task_assets WHERE task_id=$1 ORDER BY id DESC", int(task_id))
    return [{"id": str(r["id"]), "task_id": str(r["task_id"]), "type": r["type"], "value": r["value"],
             "attrs": _j(r["attrs"]) or {}, "tested": r["tested"], "created_at": _iso(r["created_at"])} for r in rows]


# ------------------------------------------------------------------ graph
def _first_sentence(text: str, n: int = 80) -> str:
    t = " ".join(text.split())
    m = re.search(r"[.!?\u3002\uff01\uff1f](\s|$)", t)
    if m:
        t = t[: m.end()].strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _ids_from(output: Any) -> list[int]:
    """Created ids in a tool_call result {ok, output}: 'recorded: N' strings, {id}, {ids}, items with id."""
    if isinstance(output, dict) and "output" in output and "ok" in output:
        output = output["output"]
    found: list[int] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            m = re.match(r"\s*(?:fact|finding) recorded: (\d+)", v)
            if m:
                found.append(int(m.group(1)))
        elif isinstance(v, dict):
            if str(v.get("id", "")).isdigit() and int(v["id"]) > 0:
                found.append(int(v["id"]))
            for k in ("ids", "results", "items", "assets"):
                if k in v:
                    walk(v[k])
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, (int, str)) and str(x).isdigit():
                    if int(x) > 0:
                        found.append(int(x))
                else:
                    walk(x)
    walk(output)
    return list(dict.fromkeys(found))


_CREATES = {"record_fact": "fact", "report_finding": "finding", "insert_assets": "asset"}


async def graph(pool: Any, task_id: int) -> dict:
    task = await svc.get_task(pool, task_id)
    tid = int(task_id)
    events = await _events(pool, tid)
    goals = await pool.fetch("SELECT * FROM task_goals WHERE task_id=$1 ORDER BY id", tid)
    ent = {
        "fact": {r["id"]: r for r in await pool.fetch("SELECT * FROM task_facts WHERE task_id=$1", tid)},
        "finding": {r["id"]: r for r in await pool.fetch("SELECT * FROM task_findings WHERE task_id=$1", tid)},
        "asset": {r["id"]: r for r in await pool.fetch("SELECT * FROM task_assets WHERE task_id=$1", tid)},
    }

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_edge(src: str, dst: str, kind: str) -> None:
        eid = f"{src}->{dst}:{kind}"
        if not any(e["id"] == eid for e in edges):
            edges.append({"id": eid, "source": src, "target": dst, "kind": kind})

    nodes["task"] = {"id": "task", "type": "task", "label": task["name"] or f"Task {task['id']}",
                     "status": task["status"], "run_no": None, "step": None,
                     "data": {"goal": task["goal"], "stop_reason": task["stop_reason"]}}
    goal_ids = set()
    for g in goals:
        gid = f"goal:{g['id']}"
        goal_ids.add(g["id"])
        nodes[gid] = {"id": gid, "type": "goal", "label": g["text"], "status": "met" if g["met"] else "unmet",
                      "run_no": None, "step": None,
                      "data": {"evidence": g["evidence"], "report": g["report"], "met_at": _iso(g["met_at"])}}
        add_edge("task", gid, "plan")

    # group events into steps
    steps: dict[tuple, dict] = {}
    order: list[tuple] = []
    manual = 0
    for r in events:
        if r["kind"] not in ("llm", "tool_call"):
            continue
        rn, st = r["run_no"], r["step"]
        if rn is None or st is None:
            if r["kind"] != "tool_call":
                continue
            manual += 1
            key: tuple = (None, manual)
        else:
            key = (rn, st)
        if key not in steps:
            steps[key] = {"run_no": key[0], "step": key[1], "text": "", "tools": [], "input_tokens": 0,
                          "output_tokens": 0, "calls": []}
            order.append(key)
        s = steps[key]
        if r["kind"] == "llm":
            out = _j(r["output"]) or {}
            s["text"] = out.get("text") or ""
            u = out.get("usage") or {}
            s["input_tokens"] += int(u.get("input_tokens") or 0)
            s["output_tokens"] += int(u.get("output_tokens") or 0)
        else:
            res = _j(r["output"])
            s["tools"].append({"id": str(r["id"]), "name": r["tool_name"], "ok": not _tool_failed(r),
                               "input": _j(r["input"]), "output": res})
            s["calls"].append(r)
    order.sort(key=lambda k: (k[0] is None, k[0] or 0, k[1]))

    running = task["status"] == "running"
    prev: str | None = None
    prev_run: Any = object()
    for i, key in enumerate(order):
        s = steps[key]
        sid = f"step:{key[0] if key[0] is not None else 'm'}:{key[1]}"
        label = _first_sentence(s["text"]) if s["text"].strip() else (s["tools"][0]["name"] if s["tools"] else f"step {key[1]}")
        status = "error" if any(not t["ok"] for t in s["tools"]) else "ok"
        if running and i == len(order) - 1 and status == "ok":
            status = "running"
        nodes[sid] = {"id": sid, "type": "step", "label": label, "status": status, "run_no": key[0], "step": key[1],
                      "data": {"text": s["text"], "tools": s["tools"], "input_tokens": s["input_tokens"],
                               "output_tokens": s["output_tokens"]}}
        add_edge(prev if (prev and key[0] == prev_run) else "task", sid, "flow")
        prev, prev_run = sid, key[0]
        for c in s["calls"]:
            name = c["tool_name"]
            if name == "prove_goal":
                gid_raw = (_j(c["input"]) or {}).get("goal_id")
                if str(gid_raw).isdigit() and int(gid_raw) in goal_ids:
                    add_edge(sid, f"goal:{int(gid_raw)}", "proves")
            elif name in _CREATES and not _tool_failed(c):
                kind = _CREATES[name]
                for eid in _ids_from(_j(c["output"])):
                    if eid in ent[kind]:
                        nid = f"{kind}:{eid}"
                        if nid not in nodes:
                            nodes[nid] = _entity_node(kind, ent[kind][eid], key)
                        add_edge(sid, nid, "creates")

    truncated = False
    for kind in ("asset", "fact"):
        if len(nodes) <= MAX_NODES:
            break
        for nid in [n for n, v in nodes.items() if v["type"] == kind]:
            del nodes[nid]
            truncated = True
            if len(nodes) <= MAX_NODES:
                break
    edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes]
    out: dict = {"nodes": list(nodes.values()), "edges": edges}
    if truncated:
        out["truncated"] = True
    return out


def _entity_node(kind: str, r: Any, key: tuple) -> dict:
    if kind == "fact":
        label, status = _first_sentence(r["summary"]), r["status"]
        data = {"summary": r["summary"], "detail": r["detail"]}
    elif kind == "finding":
        label, status = f"[{r['severity'].upper()}] {r['name'] or r['vulnclass']}"[:80], r["status"]
        data = {"vulnclass": r["vulnclass"], "severity": r["severity"], "summary": r["summary"],
                "evidence": r["evidence"], "report": r["report"]}
    else:
        label, status = f"{r['type']}: {r['value']}"[:80], "tested" if r["tested"] else "untested"
        data = {"type": r["type"], "value": r["value"], "attrs": _j(r["attrs"]) or {}, "tested": r["tested"]}
    return {"id": f"{kind}:{r['id']}", "type": kind, "label": label, "status": status, "run_no": key[0],
            "step": key[1], "data": data}


# ------------------------------------------------------------------ report
def _fmt_secs(n: int) -> str:
    h, rem = divmod(int(n), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


def _demote(md: str, base: int = 4) -> str:
    """Shift headings so the shallowest one lands on `base`; keeps fenced code untouched."""
    lines, fence, levels = md.splitlines(), False, []
    for ln in lines:
        if ln.lstrip().startswith("```"):
            fence = not fence
        elif not fence and (m := re.match(r"^(#{1,6})\s", ln)):
            levels.append(len(m.group(1)))
    if not levels:
        return md
    shift = base - min(levels)
    out, fence = [], False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            fence = not fence
        elif not fence and shift > 0 and (m := re.match(r"^(#{1,6})(\s.*)$", ln)):
            ln = "#" * min(6, len(m.group(1)) + shift) + m.group(2)
        out.append(ln)
    return "\n".join(out)


def _cell(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


async def report(pool: Any, task_id: int) -> dict:
    task = await svc.get_task(pool, task_id)
    goals = await svc.list_goals(pool, task_id)
    hints = await svc.list_hints(pool, task_id)
    fnd = sorted(await findings(pool, task_id), key=lambda f: _SEV_ORDER.get(f["severity"], 9))
    fcs = await facts(pool, task_id)
    ast = await assets(pool, task_id)
    sess = _sessions_from(await _events(pool, task_id), task)
    tot = sess["totals"]

    title = task["name"] or f"Task {task['id']}"
    L = [f"# {title} — Report", "",
         f"- **Status:** {task['status']}",
         f"- **Run time:** {_fmt_secs(task['run_seconds'])}",
         f"- **Goals met:** {task['goals_met']}/{task['goals_total']}",
         f"- **Steps:** {tot['steps']}",
         f"- **Tool calls:** {tot['tool_calls']}",
         f"- **Tokens:** {tot['input_tokens']} in / {tot['output_tokens']} out", ""]
    L += ["## Summary", "", _demote(task["summary_report"].strip(), 3) or task["stop_reason"] or "_No summary yet._", ""]
    if goals:
        L += ["## Goals", ""]
        for g in goals:
            L += [f"### {'✓' if g['met'] else '○'} {g['text']}", ""]
            if g["evidence"]:
                L += [f"**Evidence:** {g['evidence']}", ""]
            if g["report"].strip():
                L += [_demote(g["report"].strip()), ""]
    if fnd:
        L += ["## Findings", ""]
        for f in fnd:
            L += [f"### [{f['severity'].upper()}] {f['name'] or f['vulnclass']}", ""]
            if f["summary"]:
                L += [f["summary"], ""]
            if f["evidence"]:
                L += [f"**Evidence:** {f['evidence']}", ""]
            if f["report"].strip():
                L += [_demote(f["report"].strip()), ""]
    if fcs:
        L += ["## Facts", ""]
        for f in reversed(fcs):
            L.append(f"- {f['summary']}" + (f" — {f['detail']}" if f["detail"] else ""))
        L.append("")
    if ast:
        L += ["## Assets", "", "| type | value | tested |", "| --- | --- | --- |"]
        L += [f"| {_cell(a['type'])} | {_cell(a['value'])} | {'yes' if a['tested'] else 'no'} |" for a in reversed(ast)]
        L.append("")
    if hints:
        L += ["## Hints", ""] + [f"- {h['text']}" for h in hints] + [""]
    if sess["sessions"]:
        L += ["## Process", ""]
        for s in sess["sessions"]:
            L.append(f"- **{s['label']}**: {s['steps']} steps, {s['tool_calls']} tool calls, {s['errors']} errors, "
                     f"{s['input_tokens']} in / {s['output_tokens']} out tokens")
        L.append("")
    return {"markdown": "\n".join(L).rstrip() + "\n", "generated_at": datetime.now(UTC).isoformat()}
