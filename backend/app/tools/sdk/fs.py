"""File-system tools mirroring ARTEX/norma: Read, Write, Edit, MultiEdit, LS, Glob, Grep, sleep."""
from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.tools.base import Tool, ToolContext, ToolError

MAX_OUT = 30000
READ_BONUS = 1000
MAX_LINE = 2000
SKIP_DIRS = {".git", "node_modules", "vendor"}

FILE_TYPES: dict[str, list[str]] = {
    "go": ["*.go"], "py": ["*.py", "*.pyi"], "python": ["*.py", "*.pyi"],
    "js": ["*.js", "*.jsx", "*.mjs", "*.cjs", "*.vue"], "ts": ["*.ts", "*.tsx", "*.mts", "*.cts"],
    "rust": ["*.rs"], "java": ["*.java"], "kotlin": ["*.kt", "*.kts"], "c": ["*.c", "*.h"],
    "cpp": ["*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hh", "*.hxx", "*.h"], "cs": ["*.cs"],
    "rb": ["*.rb"], "ruby": ["*.rb"], "php": ["*.php"], "swift": ["*.swift"],
    "sh": ["*.sh", "*.bash", "*.zsh"], "html": ["*.html", "*.htm"],
    "css": ["*.css", "*.scss", "*.sass", "*.less"], "json": ["*.json"], "yaml": ["*.yaml", "*.yml"],
    "toml": ["*.toml"], "xml": ["*.xml"], "md": ["*.md", "*.markdown"],
    "markdown": ["*.md", "*.markdown"], "sql": ["*.sql"], "proto": ["*.proto"],
}


def resolve(ctx: ToolContext, p: str) -> str:
    """Absolute paths are used as-is; relative ones join the task workdir (no confinement)."""
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else str(ctx.workdir() / p)


def capture(s: str, limit: int = MAX_OUT) -> str:
    """Head+tail truncation like norma's Capture."""
    if len(s) <= limit:
        return s
    half = limit // 2
    return s[:half] + f"\n\n... [{len(s) - limit} characters truncated] ...\n\n" + s[-half:]


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def _need_str(a: dict, key: str) -> str:
    v = a.get(key)
    if not isinstance(v, str):
        raise ToolError(f"Error: {key} is required")
    return v


def _int(a: dict, key: str, default: int = 0) -> int:
    v = a.get(key)
    try:
        return default if v is None else int(v)
    except (TypeError, ValueError):
        raise ToolError(f"Error: {key} must be an integer")


# ---------------------------------------------------------------- Read
def _read_sync(path: str, shown: str, offset: int, limit: int) -> str:
    if not os.path.exists(path):
        raise ToolError(f"Error reading {shown}: [Errno 2] No such file or directory: '{path}'")
    if os.path.isdir(path):
        raise ToolError(f"Error: {shown} is a directory, not a file. Use LS or Glob to list its contents.")
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise ToolError(f"Error reading {shown}: {e}")
    if _is_binary(data):
        return f"[binary file {shown} not shown — {len(data)} bytes]"
    if not data:
        return "(file is empty)"
    lines = data.decode("utf-8", errors="replace").split("\n")
    start = offset - 1 if offset > 0 else 0
    start = min(start, len(lines))
    if limit <= 0:
        limit = 2000
    end = min(start + limit, len(lines))
    out: list[str] = []
    for i in range(start, end):
        ln = lines[i]
        if len(ln) > MAX_LINE:
            ln = ln[:MAX_LINE] + "… [line truncated]"
        out.append(f"{i + 1:6d}\t{ln}\n")
    s = "".join(out)
    if not s:
        return "(no lines in the requested range)"
    budget = MAX_OUT + READ_BONUS
    if len(s) > budget:
        cut = s.rfind("\n", 0, budget)
        head = s[: cut + 1] if cut >= 0 else s[:budget]
        nxt = start + (head.count("\n")) + 1
        return head + (
            f"\n... [Read truncated to fit the output limit. Continue reading the file with Read offset={nxt}"
            " (and an optional limit), or narrow with Grep/`sed -n`.] ..."
        )
    return s


async def _read(ctx: ToolContext, a: dict) -> Any:
    fp = _need_str(a, "file_path")
    return await asyncio.to_thread(_read_sync, resolve(ctx, fp), fp, _int(a, "offset"), _int(a, "limit"))


# ---------------------------------------------------------------- Write
def _write_sync(path: str, shown: str, content: str) -> str:
    existed = False
    if os.path.exists(path):
        if os.path.isdir(path):
            raise ToolError(f"Error: {shown} is an existing directory, not a file.")
        existed = True
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.chmod(path, 0o644) if not existed else None
    except OSError as e:
        raise ToolError(f"Error writing {shown}: {e}")
    return f"{'Overwrote' if existed else 'Created'} {shown} ({len(content.encode())} bytes)"


async def _write(ctx: ToolContext, a: dict) -> Any:
    fp = _need_str(a, "file_path")
    return await asyncio.to_thread(_write_sync, resolve(ctx, fp), fp, _need_str(a, "content"))


# ---------------------------------------------------------------- Edit / MultiEdit
def _load(path: str, shown: str) -> str:
    try:
        return Path(path).read_bytes().decode("utf-8", errors="replace")
    except OSError as e:
        raise ToolError(f"Error reading {shown}: {e}")


def _store(path: str, shown: str, content: str) -> None:
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
    except OSError as e:
        raise ToolError(f"Error writing {shown}: {e}")


def _edit_sync(path: str, shown: str, old: str, new: str, replace_all: bool) -> str:
    if old == new:
        raise ToolError("Error: old_string and new_string are identical")
    content = _load(path, shown)
    n = content.count(old) if old else 0
    if n == 0:
        raise ToolError(f"Error: old_string not found in {shown}")
    if n > 1 and not replace_all:
        raise ToolError(
            f"Error: old_string is not unique in {shown} ({n} matches); add context or set replace_all")
    _store(path, shown, content.replace(old, new) if replace_all else content.replace(old, new, 1))
    return f"Edited {shown} ({n} replacement(s))"


async def _edit(ctx: ToolContext, a: dict) -> Any:
    fp = _need_str(a, "file_path")
    return await asyncio.to_thread(
        _edit_sync, resolve(ctx, fp), fp, _need_str(a, "old_string"), _need_str(a, "new_string"),
        bool(a.get("replace_all")))


def _multi_sync(path: str, shown: str, edits: list) -> str:
    if not edits:
        raise ToolError("Error: no edits provided")
    content = _load(path, shown)
    total = 0
    for i, e in enumerate(edits, 1):
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError(f"Error in edit {i}: old_string and new_string are required")
        if old == new:
            raise ToolError(f"Error in edit {i}: old_string and new_string are identical")
        n = content.count(old) if old else 0
        if n == 0:
            raise ToolError(f"Error in edit {i}: old_string not found")
        ra = bool(e.get("replace_all"))
        if n > 1 and not ra:
            raise ToolError(f"Error in edit {i}: old_string is not unique ({n} matches); add context or set replace_all")
        content = content.replace(old, new) if ra else content.replace(old, new, 1)
        total += n
    _store(path, shown, content)
    return f"Applied {len(edits)} edits to {shown} ({total} replacement(s))"


async def _multiedit(ctx: ToolContext, a: dict) -> Any:
    fp = _need_str(a, "file_path")
    edits = a.get("edits")
    if not isinstance(edits, list):
        raise ToolError("Error: edits is required")
    return await asyncio.to_thread(_multi_sync, resolve(ctx, fp), fp, edits)


# ---------------------------------------------------------------- LS
def _ls_sync(path: str, shown: str) -> str:
    try:
        entries = list(os.scandir(path))
    except OSError as e:
        raise ToolError(f"Error listing {shown}: {e}")
    if not entries:
        return "(empty directory)"
    entries.sort(key=lambda e: (not e.is_dir(), e.name))
    out = []
    for e in entries:
        if e.is_dir():
            out.append(f"{e.name}/\n")
        else:
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            out.append(f"{e.name}\t{size} bytes\n")
    return capture("".join(out))


async def _ls(ctx: ToolContext, a: dict) -> Any:
    p = a.get("path") or ""
    d = resolve(ctx, p) if p else str(ctx.workdir())
    return await asyncio.to_thread(_ls_sync, d, p)


# ---------------------------------------------------------------- Glob
def _glob_segments(pat: list[str], name: list[str]) -> bool:
    if not pat:
        return not name
    if pat[0] == "**":
        return any(_glob_segments(pat[1:], name[i:]) for i in range(len(name) + 1))
    if not name:
        return False
    return fnmatch.fnmatchcase(name[0], pat[0]) and _glob_segments(pat[1:], name[1:])


def match_glob(pattern: str, path: str) -> bool:
    pattern, path = pattern.replace("\\", "/"), path.replace("\\", "/")
    if "/" not in pattern and fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern):
        return True
    return _glob_segments(pattern.split("/"), path.split("/"))


def _walk(root: str):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            yield os.path.join(dp, fn)


def _glob_sync(pattern: str, root: str) -> str:
    found: list[tuple[float, str]] = []
    for p in _walk(root):
        if match_glob(pattern, os.path.relpath(p, root)):
            try:
                m = os.stat(p).st_mtime
            except OSError:
                m = 0.0
            found.append((m, p))
    if not found:
        return "No files found"
    found.sort(key=lambda t: -t[0])
    return capture("".join(p + "\n" for _, p in found))


async def _glob(ctx: ToolContext, a: dict) -> Any:
    pat = _need_str(a, "pattern")
    p = a.get("path") or ""
    root = resolve(ctx, p) if p else str(ctx.workdir())
    return await asyncio.to_thread(_glob_sync, pat, root)


# ---------------------------------------------------------------- Grep
def _expand_braces(g: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", g)
    if not m:
        return [g]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out += _expand_braces(g[: m.start()] + alt + g[m.end():])
    return out


def _type_globs(t: str) -> list[str]:
    if not t:
        return []
    return FILE_TYPES.get(t.lower(), [f"*.{t}"])


def _page(lines: list[str], limit: int, offset: int) -> tuple[list[str], int]:
    if offset > 0:
        if offset >= len(lines):
            return [], 0
        lines = lines[offset:]
    if limit > 0 and len(lines) > limit:
        return lines[:limit], len(lines) - limit
    return lines, 0


def _scan_file(path: str, base: str, rx: re.Pattern, mode: str, show_n: bool, multiline: bool,
               before: int, after: int) -> tuple[str, int, list[str]] | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if _is_binary(data):
        return None
    rel = os.path.relpath(path, base)
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    matches: list[int] = []
    if multiline:
        starts, off = [], 0
        for ln in lines:
            starts.append(off)
            off += len(ln) + 1
        hit = [False] * len(lines)
        for m in rx.finditer(text):
            s, e = m.start(), max(m.end(), m.start() + 1)
            for i, ln in enumerate(lines):
                if s <= starts[i] + len(ln) and e > starts[i]:
                    hit[i] = True
        matches = [i for i, h in enumerate(hit) if h]
    else:
        matches = [i for i, ln in enumerate(lines) if rx.search(ln)]
    if not matches:
        return None
    if mode != "content":
        return rel, len(matches), []
    is_match = set(matches)
    order: set[int] = set()
    for m in matches:
        order.update(range(max(0, m - before), min(len(lines) - 1, m + after) + 1))
    out: list[str] = []
    ctxmode = before > 0 or after > 0
    prev = -2
    for j in sorted(order):
        if ctxmode and prev >= 0 and j != prev + 1:
            out.append("--")
        sep = ":" if j in is_match else "-"
        out.append(f"{rel}{sep}{j + 1}{sep}{lines[j]}" if show_n else f"{rel}{sep}{lines[j]}")
        prev = j
    return rel, len(matches), out


def _format(lines: list[str], head_limit: int, offset: int) -> str:
    kept, more = _page(lines, head_limit, offset)
    out = "\n".join(kept) + ("\n" if kept else "")
    if more > 0:
        out += f"... [{more} more result(s) — page with offset={offset + len(kept)}, or head_limit=0 for all] ...\n"
    return capture(out)


def _rg_grep(rg: str, a: dict, mode: str, root: str, show_n: bool, before: int, after: int,
             head_limit: int, offset: int) -> str | None:
    args = [rg, "--color", "never", "--hidden", "--glob", "!.git", "--glob", "!node_modules",
            "--glob", "!vendor", "--no-ignore", "--sort", "path"]
    if a.get("-i"):
        args.append("-i")
    if a.get("multiline"):
        args += ["-U", "--multiline-dotall"]
    if mode == "files_with_matches":
        args.append("-l")
    elif mode == "count":
        args.append("-c")
    else:
        args.append("--no-heading")
        if show_n:
            args.append("-n")
        if before > 0:
            args += ["-B", str(before)]
        if after > 0:
            args += ["-A", str(after)]
    if a.get("glob"):
        args += ["--glob", a["glob"]]
    if a.get("type"):
        if str(a["type"]).lower() in FILE_TYPES:
            args += ["--type", a["type"]]
        else:
            args += ["--glob", f"*.{a['type']}"]
    d, target = (root, ".")
    if os.path.isfile(root):
        d, target = os.path.dirname(root), os.path.basename(root)
    args += ["-e", a["pattern"], "--", target]
    try:
        r = subprocess.run(args, check=False, cwd=d, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode == 1:
        return "No matches found"
    if r.returncode != 0:
        return None
    text = r.stdout.decode("utf-8", errors="replace").rstrip("\n")
    if not text:
        return "No matches found"
    lines = [ln.removeprefix("./") for ln in text.split("\n")]
    if mode == "count":  # match the pure-Python fallback format: "path: N"
        lines = [f"{a_}: {b_}" if sep else a_ for a_, sep, b_ in (ln.rpartition(":") for ln in lines)]
    return _format(lines, head_limit, offset)


def _grep_sync(a: dict, root: str) -> str:
    mode = a.get("output_mode") or "files_with_matches"
    show_n = True if a.get("-n") is None else bool(a.get("-n"))
    before, after = _int(a, "-B"), _int(a, "-A")
    if _int(a, "-C") > 0:
        before = after = _int(a, "-C")
    head_limit = _int(a, "head_limit", 250)
    offset = _int(a, "offset")
    multiline = bool(a.get("multiline"))
    flags = (re.IGNORECASE if a.get("-i") else 0) | (re.DOTALL if multiline else 0)
    try:
        rx = re.compile(a["pattern"], flags)
    except re.error as e:
        raise ToolError(f"Error: invalid regex: {e}")
    if not os.path.exists(root):
        raise ToolError(f"Error: [Errno 2] No such file or directory: '{root}'")
    rg = shutil.which("rg")
    if rg:
        res = _rg_grep(rg, a, mode, root, show_n, before, after, head_limit, offset)
        if res is not None:
            return res
    globs = _expand_braces(a["glob"]) if a.get("glob") else []
    tglobs = _type_globs(a.get("type") or "")
    hits: list[tuple[str, int, list[str]]] = []
    if os.path.isdir(root):
        for p in _walk(root):
            base = os.path.basename(p)
            if globs and not any(fnmatch.fnmatchcase(base, g) for g in globs):
                continue
            if tglobs and not any(fnmatch.fnmatchcase(base, g) for g in tglobs):
                continue
            h = _scan_file(p, root, rx, mode, show_n, multiline, before, after)
            if h:
                hits.append(h)
    else:
        h = _scan_file(root, os.path.dirname(root), rx, mode, show_n, multiline, before, after)
        if h:
            hits.append(h)
    if not hits:
        return "No matches found"
    hits.sort(key=lambda h: h[0])
    lines: list[str] = []
    for rel, n, ls in hits:
        if mode == "content":
            lines += ls
        elif mode == "count":
            lines.append(f"{rel}: {n}")
        else:
            lines.append(rel)
    return _format(lines, head_limit, offset)


async def _grep(ctx: ToolContext, a: dict) -> Any:
    _need_str(a, "pattern")
    p = a.get("path") or ""
    root = resolve(ctx, p) if p else str(ctx.workdir())
    return await asyncio.to_thread(_grep_sync, a, root)


# ---------------------------------------------------------------- sleep
async def _sleep(ctx: ToolContext, a: dict) -> Any:
    n = max(1, min(3600, _int(a, "seconds", 1)))
    await asyncio.sleep(n)  # cancellation (pause/stop) propagates immediately
    return f"slept {n}s"


def _o(props: dict, req: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": req or []}


GREP_DESC = (
    "A powerful search tool built on ripgrep\n\nUsage:\n"
    "- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been "
    "optimized for correct permissions and access.\n"
    "- Supports full regex syntax (e.g., \"log.*Error\", \"function\\s+\\w+\")\n"
    "- Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\") or type parameter (e.g., \"js\", \"py\", \"rust\")\n"
    "- Output modes: \"content\" shows matching lines, \"files_with_matches\" shows only file paths (default), "
    "\"count\" shows match counts\n"
    "- Prefer `head_limit` to avoid large result sets.\n"
    "- Multiline matching: By default patterns match within single lines only. For cross-line patterns, use "
    "`multiline: true`\n"
    "- Prefer Grep over terminal rg/grep for codebase search unless you specifically need shell features."
)

_PATH = {"type": "string"}
TOOLS: list[Tool] = [
    Tool("Read",
         "Reads a file from the local filesystem and returns its contents with line numbers. Use offset/limit for "
         "large files. Always read a file before editing it. file_path may be absolute or relative to the working "
         "directory. Output lines are prefixed with their 1-based line number.",
         _o({"file_path": {**_PATH, "description": "Path to the file to read."},
             "offset": {"type": "integer", "description": "1-based line to start from."},
             "limit": {"type": "integer", "description": "Max lines to read (default 2000)."}}, ["file_path"]),
         _read, True, {"category": "file"}),
    Tool("Write",
         "Writes a file to the local filesystem, creating parent directories and overwriting any existing file. "
         "Prefer Edit for modifying existing files. Provide the complete file contents — Write replaces the whole file.",
         _o({"file_path": {**_PATH, "description": "Path of the file to write."},
             "content": {"type": "string", "description": "Full contents to write."}}, ["file_path", "content"]),
         _write, False, {"category": "file"}),
    Tool("Edit",
         "Performs an exact string replacement in a file. old_string must match exactly once unless replace_all is "
         "set. Read the file first so old_string matches precisely. old_string must match the file exactly "
         "(whitespace included) and be unique unless replace_all is true. new_string must differ from old_string.",
         _o({"file_path": {**_PATH, "description": "Path of the file to edit."},
             "old_string": {"type": "string", "description": "Exact text to replace."},
             "new_string": {"type": "string", "description": "Replacement text."},
             "replace_all": {"type": "boolean", "description": "Replace all occurrences."}},
            ["file_path", "old_string", "new_string"]),
         _edit, False, {"category": "file"}),
    Tool("MultiEdit",
         "Applies several exact string replacements to a single file in one atomic operation. Edits are applied in "
         "order; if any old_string fails to match (or isn't unique without replace_all), the whole operation aborts "
         "and the file is left unchanged. Use this instead of multiple Edit calls on the same file. Each edit's "
         "old_string must match the file as modified by the preceding edits. Read the file first.",
         _o({"file_path": {**_PATH, "description": "Path of the file to edit."},
             "edits": {"type": "array", "description": "Edits to apply in order.", "items": _o({
                 "old_string": {"type": "string", "description": "Exact text to replace."},
                 "new_string": {"type": "string", "description": "Replacement text."},
                 "replace_all": {"type": "boolean", "description": "Replace all occurrences."}},
                 ["old_string", "new_string"])}}, ["file_path", "edits"]),
         _multiedit, False, {"category": "file"}),
    Tool("LS",
         "Lists the files and subdirectories of a directory (non-recursive). Use Glob to match by pattern across "
         "depth, or Grep to search contents. path may be absolute or relative to the working directory; defaults "
         "to the working directory.",
         _o({"path": {**_PATH, "description": "Directory to list (default working directory)."}}),
         _ls, True, {"category": "file"}),
    Tool("Glob",
         "Finds files whose path matches a glob pattern (supports ** for any depth, e.g. \"**/*.go\"). Returns "
         "paths sorted by modification time, newest first. Use this instead of `find`. The pattern matches paths "
         "relative to the search directory.",
         _o({"pattern": {**_PATH, "description": "Glob pattern, e.g. src/**/*.go"},
             "path": {**_PATH, "description": "Directory to search (default working dir)."}}, ["pattern"]),
         _glob, True, {"category": "file"}),
    Tool("Grep", GREP_DESC,
         _o({"pattern": {**_PATH, "description": "Regular expression to search for."},
             "path": {**_PATH, "description": "File or directory to search. Defaults to the working directory."},
             "glob": {**_PATH, "description": "Only search files matching this glob, e.g. *.go or *.{ts,tsx}"},
             "type": {**_PATH, "description": "Only search a file type, e.g. go, py, js, ts, rust, java. Unknown "
                      "types are treated as an extension (type:foo → *.foo)."},
             "output_mode": {**_PATH, "description": "files_with_matches (default) | content | count"},
             "-i": {"type": "boolean", "description": "Case-insensitive."},
             "-n": {"type": "boolean", "description": "Show line numbers (content mode). Default true."},
             "-A": {"type": "integer", "description": "Lines of context to show after each match (content mode)."},
             "-B": {"type": "integer", "description": "Lines of context to show before each match (content mode)."},
             "-C": {"type": "integer", "description": "Lines of context before and after each match (content mode)."},
             "multiline": {"type": "boolean", "description": "Multiline mode: . matches newlines and patterns may "
                           "span lines. Default false."},
             "head_limit": {"type": "integer", "description": "Limit output to the first N results (lines/files/"
                            "counts). Default 250; pass 0 for unlimited."},
             "offset": {"type": "integer", "description": "Skip the first N results before applying head_limit "
                        "(paging). Default 0."}}, ["pattern"]),
         _grep, True, {"category": "file"}),
    Tool("sleep",
         "Waits for N seconds, then returns. Use to let a backgrounded command make progress before reading its "
         "partial output with TaskOutput. Interrupted immediately if the run is paused or stopped.",
         _o({"seconds": {"type": "integer", "description": "Seconds to wait, 1-3600."}}, ["seconds"]),
         _sleep, True, {"category": "exec"}),
]
