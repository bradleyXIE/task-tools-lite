"""Interactive PTY shell sessions: shell_open / shell_send / shell_read / shell_close / shell_list.

Mirrors norma tool/shell_tools.go + shell_session.go. Sessions live on the per-task Scope of
app.tools.sdk.background (so they are cleaned up by background.shutdown()).
"""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import fcntl
import os
import re
import struct
import termios
import time
from typing import Any

from app.tools.base import Tool, ToolContext, ToolError
from app.tools.sdk.safeenv import child_env

from .background import Scope, kill_tree, scope_for, short

RING_CAP = 256 << 10
DEF_QUIET_MS = 8000
DEF_GRACE_MS = 15000
IDLE_MS = 30 * 60 * 1000
REREAD_MS = 5 * 60 * 1000
DEF_ROWS, DEF_COLS = 24, 80
DEF_READ_LINES = 200
DEF_READ_BYTES = 8192
WAIT_CAP_MS = 120000

NAMED_KEYS = {
    "enter": "\r", "return": "\r", "tab": "\t", "esc": "\x1b", "escape": "\x1b", "backspace": "\x7f",
    "space": " ", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D", "home": "\x1b[H",
    "end": "\x1b[F", "pageup": "\x1b[5~", "pagedown": "\x1b[6~", "delete": "\x1b[3~", "insert": "\x1b[2~",
}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")


def key_to_bytes(k: str) -> bytes | None:
    k = k.strip().lower()
    if k in NAMED_KEYS:
        return NAMED_KEYS[k].encode()
    if k.startswith("ctrl+") and len(k) == 6:
        c = k[5]
        if "a" <= c <= "z":
            return bytes([ord(c) & 0x1F])
        if c == "[":
            return b"\x1b"
        if c == "\\":
            return b"\x1c"
    return None


def hex_to_bytes(items: list[str]) -> bytes:
    out = bytearray()
    for h in items:
        h = h.strip().lower().removeprefix("0x")
        with contextlib.suppress(ValueError):
            v = int(h, 16)
            if 0 <= v <= 255:
                out.append(v)
    return bytes(out)


class Screen:
    """Minimal VT100 screen emulator (enough for snapshotting vim/htop-like output)."""

    def __init__(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        self.grid = [[" "] * cols for _ in range(rows)]
        self.x = self.y = 0
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        self._st = "n"  # n | esc | csi | osc | cs
        self._buf = ""

    def _scroll(self) -> None:
        self.grid.pop(0)
        self.grid.append([" "] * self.cols)

    def _lf(self) -> None:
        if self.y >= self.rows - 1:
            self._scroll()
        else:
            self.y += 1

    def feed(self, data: bytes) -> None:
        for ch in self._dec.decode(data):
            st = self._st
            if st == "n":
                if ch == "\x1b":
                    self._st = "esc"
                elif ch == "\n":
                    self._lf()
                elif ch == "\r":
                    self.x = 0
                elif ch == "\b":
                    self.x = max(0, self.x - 1)
                elif ch == "\t":
                    self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
                elif ch >= " " and ch != "\x7f":
                    if self.x >= self.cols:
                        self.x = 0
                        self._lf()
                    self.grid[self.y][self.x] = ch
                    self.x += 1
            elif st == "esc":
                if ch == "[":
                    self._st, self._buf = "csi", ""
                elif ch == "]":
                    self._st = "osc"
                elif ch in "()":
                    self._st = "cs"
                else:
                    self._st = "n"
            elif st == "cs":
                self._st = "n"
            elif st == "osc":
                if ch == "\x07" or ch == "\\":
                    self._st = "n"
            else:  # csi
                if "@" <= ch <= "~":
                    self._csi(self._buf, ch)
                    self._st = "n"
                else:
                    self._buf += ch

    def _csi(self, params: str, fin: str) -> None:
        p = [int(x) if x.isdigit() else 0 for x in params.lstrip("?").split(";")] if params else []
        n = p[0] if p and p[0] > 0 else 1
        if fin == "A":
            self.y = max(0, self.y - n)
        elif fin == "B":
            self.y = min(self.rows - 1, self.y + n)
        elif fin == "C":
            self.x = min(self.cols - 1, self.x + n)
        elif fin == "D":
            self.x = max(0, self.x - n)
        elif fin in "Hf":
            r = p[0] if p and p[0] > 0 else 1
            c = p[1] if len(p) > 1 and p[1] > 0 else 1
            self.y, self.x = min(self.rows - 1, r - 1), min(self.cols - 1, c - 1)
        elif fin == "G":
            self.x = min(self.cols - 1, n - 1)
        elif fin == "J":
            m = p[0] if p else 0
            if m == 0:
                self.grid[self.y][self.x:] = [" "] * (self.cols - self.x)
                for r in range(self.y + 1, self.rows):
                    self.grid[r] = [" "] * self.cols
            elif m == 1:
                for r in range(self.y):
                    self.grid[r] = [" "] * self.cols
                self.grid[self.y][: self.x + 1] = [" "] * (self.x + 1)
            else:
                self.grid = [[" "] * self.cols for _ in range(self.rows)]
        elif fin == "K":
            m = p[0] if p else 0
            if m == 0:
                self.grid[self.y][self.x:] = [" "] * (self.cols - self.x)
            elif m == 1:
                self.grid[self.y][: self.x + 1] = [" "] * (self.x + 1)
            else:
                self.grid[self.y] = [" "] * self.cols
        # SGR, modes etc. are ignored

    def render(self) -> str:
        return "".join("".join(r).rstrip(" ") + "\n" for r in self.grid)


class PtySession:
    def __init__(self, sid: str, command: str, rows: int, cols: int, mode: str, prompt: re.Pattern | None) -> None:
        self.id, self.command, self.rows, self.cols, self.mode, self.prompt = sid, command, rows, cols, mode, prompt
        self.buf = bytearray()
        self.total = 0
        self.state = "running"  # running | exited | closed
        self.exit_code: int | None = None
        self.last_out = time.time()
        self.exited_at = 0.0
        self.read_cur = 0
        self.master = -1
        self.proc: asyncio.subprocess.Process | None = None
        self.emu: Screen | None = None
        self.notify = asyncio.Event()
        self.done = asyncio.Event()
        self.bg: list[asyncio.Task] = []

    # -- output
    def append(self, p: bytes) -> None:
        self.buf += p
        self.total += len(p)
        if len(self.buf) > RING_CAP:
            del self.buf[: len(self.buf) - RING_CAP]
        if self.emu is not None:
            self.emu.feed(p)
        self.last_out = time.time()
        self.notify.set()

    def tail_str(self, n: int) -> str:
        b = bytes(self.buf if n <= 0 or n > len(self.buf) else self.buf[-n:])
        return b.decode("utf-8", "replace")

    def read_since(self, c: int, max_lines: int, max_bytes: int, drain: bool) -> tuple[str, int, bool]:
        start = self.total - len(self.buf)
        if c >= self.total:
            return "", self.total, False
        omitted = False
        frm = c
        if frm < start:
            frm, omitted = start, True
        seg = bytes(self.buf[frm - start:])
        if not drain:
            if max_bytes > 0 and len(seg) > max_bytes:
                seg = seg[:max_bytes]
            if max_lines > 0:
                idx = -1
                for _ in range(max_lines):
                    idx = seg.find(b"\n", idx + 1)
                    if idx < 0:
                        break
                if idx >= 0:
                    seg = seg[: idx + 1]
        return seg.decode("utf-8", "replace"), frm + len(seg), omitted

    def screen(self) -> tuple[str, int, int, int, int]:
        if self.emu is None:
            self.emu = Screen(self.rows, self.cols)
            self.emu.feed(bytes(self.buf))
        return self.emu.render(), self.rows, self.cols, self.emu.x, self.emu.y

    @property
    def running(self) -> bool:
        return self.state == "running"

    # -- io
    def _on_readable(self) -> None:
        try:
            data = os.read(self.master, 32768)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            self.append(data)
        else:
            self._stop_reader()

    def _stop_reader(self) -> None:
        with contextlib.suppress(Exception):
            asyncio.get_running_loop().remove_reader(self.master)

    async def write(self, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            try:
                n = os.write(self.master, view)
                view = view[n:]
            except BlockingIOError:
                await asyncio.sleep(0.01)

    async def _reap(self) -> None:
        assert self.proc is not None
        rc = await self.proc.wait()
        # drain whatever is left in the pty
        for _ in range(64):
            try:
                data = os.read(self.master, 32768)
            except (BlockingIOError, OSError):
                break
            if not data:
                break
            self.append(data)
        self._stop_reader()
        if self.state != "closed":
            self.state = "exited"
        self.exit_code = rc
        self.exited_at = time.time()
        self.done.set()
        self.notify.set()
        with contextlib.suppress(OSError):
            os.close(self.master)
        self.master = -1

    def kill(self) -> None:
        self.state = "closed"
        if self.proc is not None and self.proc.returncode is None:
            kill_tree(self.proc.pid)
        for t in self.bg:
            t.cancel()
        # _reap is cancelled above, so release the PTY master here (otherwise one fd leaks per closed session)
        if self.master >= 0:
            self._stop_reader()
            with contextlib.suppress(OSError):
                os.close(self.master)
            self.master = -1
        self.done.set()
        self.notify.set()

    async def wait_complete(self, quiet: float, grace: float, timeout: float, prompt: re.Pattern | None,
                            since: int = 0) -> str:
        """Block until prompt match, output silence, process exit or timeout. Returns the reason."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        end = now + timeout
        qd = now + max(quiet, grace)
        last = self.total
        while True:
            if self.done.is_set():
                return "exited"
            if self.total != last:
                last = self.total
                new, _, _ = self.read_since(max(since, self.total - 4096), 0, 0, True)
                if prompt is not None and prompt.search(_ANSI.sub("", new)):
                    return "prompt"
                qd = loop.time() + quiet
            now = loop.time()
            if now >= end:
                return "timeout"
            if now >= qd:
                return "quiet"
            self.notify.clear()
            if self.total != last or self.done.is_set():
                continue
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.notify.wait(), min(end, qd) - now)


def _reap_sessions(sc: Scope) -> None:
    now = time.time()
    for sid, s in list(sc.sessions.items()):
        gone = s.state == "exited" and now - s.exited_at > REREAD_MS / 1000
        stale = s.state == "running" and now - s.last_out > IDLE_MS / 1000
        if gone or stale:
            s.kill()
            del sc.sessions[sid]


async def _open_session(ctx: ToolContext, command: str, rows: int, cols: int, mode: str,
                        prompt: re.Pattern | None) -> PtySession:
    sc = scope_for(ctx)
    _reap_sessions(sc)
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    os.set_blocking(master, False)
    env = child_env({"TERM": "xterm-256color"})

    def _ctty() -> None:
        with contextlib.suppress(BaseException):  # best effort: not every platform/state allows it
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", command, stdin=slave, stdout=slave, stderr=slave, cwd=str(ctx.workdir()),
            env=env, start_new_session=True, preexec_fn=_ctty)
    except Exception:
        os.close(master)
        os.close(slave)
        raise
    finally:
        with contextlib.suppress(OSError):
            os.close(slave)
    sc.sess_seq += 1
    s = PtySession(f"sh_{sc.sess_seq}", command, rows, cols, mode, prompt)
    s.master, s.proc = master, proc
    asyncio.get_running_loop().add_reader(master, s._on_readable)
    s.bg.append(asyncio.create_task(s._reap()))
    sc.sessions[s.id] = s
    return s


async def _dispatch_waiter(sc: Scope, s: PtySession, quiet: float, grace: float, timeout: float) -> None:
    by = await s.wait_complete(quiet, grace, timeout, s.prompt)
    tail, _, _ = s.read_since(max(0, s.total - 4096), 0, 4096, True)
    await sc.note("shell_open", f"Interactive session {s.id} ({short(s.command)}) {by}. Use "
                  f"shell_read(session_id={s.id!r}) to continue, or shell_close to end it.\n{tail}",
                  {"session_id": s.id})


async def _monitor_waiter(sc: Scope, s: PtySession, pats: list[re.Pattern]) -> None:
    fired = [False] * len(pats)
    last = -1
    while True:
        if s.done.is_set():
            tail, _, _ = s.read_since(max(0, s.total - 4096), 0, 4096, True)
            await sc.note("shell_open", f"Monitored session {s.id} ({short(s.command)}) ended "
                          f"(exit {s.exit_code}).\n{tail}", {"session_id": s.id})
            return
        if s.total != last:
            last = s.total
            tail = _ANSI.sub("", s.tail_str(8192))
            for i, p in enumerate(pats):
                if not fired[i] and p.search(tail):
                    fired[i] = True
                    await sc.note("shell_open", f"Monitored session {s.id} matched /{p.pattern}/:\n{tail}",
                                  {"session_id": s.id, "pattern": p.pattern})
        s.notify.clear()
        if s.total != last or s.done.is_set():
            continue
        await s.notify.wait()


def _session(ctx: ToolContext, sid: str) -> PtySession:
    s = scope_for(ctx).sessions.get(sid)
    if s is None:
        raise ToolError(f"session not found: {sid}")
    return s


def _ms(v: Any, default: int) -> float:
    v = int(v or 0)
    return (v if v > 0 else default) / 1000


def _compile(pat: str, what: str) -> re.Pattern:
    try:
        return re.compile(pat)
    except re.error as e:
        raise ToolError(f"{what} invalid {pat}: {e}" if what != "prompt_regex" else f"prompt_regex invalid: {e}")


async def _shell_open(ctx: ToolContext, a: dict) -> Any:
    command = a.get("command") or ""
    if not command:
        raise ToolError("command is required")
    mode = a.get("mode") or "hands-free"
    if mode not in ("hands-free", "interactive", "dispatch", "monitor"):
        raise ToolError("mode must be interactive | hands-free | dispatch | monitor")
    watch: list[re.Pattern] = []
    if mode == "monitor":
        if not a.get("watch"):
            raise ToolError("monitor mode requires watch (keywords/regexes to watch for)")
        watch = [_compile(w, "watch regex") for w in a["watch"]]
    pr = _compile(a["prompt_regex"], "prompt_regex") if a.get("prompt_regex") else None
    rows = int(a.get("rows") or 0) or DEF_ROWS
    cols = int(a.get("cols") or 0) or DEF_COLS
    s = await _open_session(ctx, command, rows, cols, mode, pr)
    sc = scope_for(ctx)
    quiet, grace = _ms(a.get("quiet_ms"), DEF_QUIET_MS), _ms(a.get("startup_grace_ms"), DEF_GRACE_MS)
    if mode == "interactive":
        by = await s.wait_complete(quiet, grace, WAIT_CAP_MS / 1000, pr)
        out, cur, _ = s.read_since(0, 0, DEF_READ_BYTES * 4, False)
        return {"session_id": s.id, "state": "running", "done_by": by, "output": out, "cursor": cur,
                "running": s.running}
    if mode == "dispatch":
        s.bg.append(asyncio.create_task(_dispatch_waiter(sc, s, quiet, grace, WAIT_CAP_MS / 1000)))
        return {"session_id": s.id, "state": "running", "dispatched": True}
    if mode == "monitor":
        s.bg.append(asyncio.create_task(_monitor_waiter(sc, s, watch)))
        return {"session_id": s.id, "state": "running", "monitoring": len(watch)}
    return {"session_id": s.id, "state": "running"}


async def _shell_send(ctx: ToolContext, a: dict) -> Any:
    s = _session(ctx, a.get("session_id") or "")
    if not s.running:
        raise ToolError(f"session ended (exit {s.exit_code if s.exit_code is not None else '?'}); "
                        "use shell_read for the tail output or shell_close")
    before = s.total
    payload = bytearray()
    if a.get("text"):
        payload += a["text"].encode()
    if a.get("submit"):
        payload += b"\r"
    for k in a.get("keys") or []:
        b = key_to_bytes(k)
        if b is None:
            raise ToolError(f"unknown key: {k}")
        payload += b
    if a.get("hex"):
        payload += hex_to_bytes(a["hex"])
    if a.get("paste"):
        payload += b"\x1b[200~" + a["paste"].encode() + b"\x1b[201~"
    if not payload:
        raise ToolError("nothing to send (give at least one of text/submit/keys/hex/paste)")
    try:
        await s.write(bytes(payload))
    except OSError as e:
        raise ToolError(f"write failed: {e}")
    if a.get("wait") is False:
        return {"session_id": s.id, "sent": len(payload), "running": s.running}
    pr = s.prompt
    if a.get("prompt_regex"):
        with contextlib.suppress(ToolError):
            pr = _compile(a["prompt_regex"], "prompt_regex")
    by = await s.wait_complete(_ms(a.get("quiet_ms"), DEF_QUIET_MS), 0, _ms(a.get("timeout_ms"), WAIT_CAP_MS), pr,
                                since=before)
    out, cur, omitted = s.read_since(before, 0, DEF_READ_BYTES, False)
    return {"session_id": s.id, "output": out, "done_by": by, "cursor": cur, "running": s.running,
            "omitted": omitted}


async def _shell_read(ctx: ToolContext, a: dict) -> Any:
    s = _session(ctx, a.get("session_id") or "")
    if a.get("view") == "screen":
        grid, rows, cols, cx, cy = s.screen()
        return {"view": "screen", "screen": grid, "rows": rows, "cols": cols,
                "cursor": {"row": cy, "col": cx}, "running": s.running}
    frm = int(a["since_cursor"]) if a.get("since_cursor") is not None else s.read_cur
    out, cur, omitted = s.read_since(frm, int(a.get("max_lines") or DEF_READ_LINES),
                                     int(a.get("max_bytes") or DEF_READ_BYTES), bool(a.get("drain")))
    s.read_cur = cur
    res: dict = {"view": "stream", "output": out, "cursor": cur, "running": s.running, "omitted": omitted}
    if s.exit_code is not None:
        res["exit_code"] = s.exit_code
    return res


async def _shell_close(ctx: ToolContext, a: dict) -> Any:
    sid = a.get("session_id") or ""
    s = _session(ctx, sid)
    ec = s.exit_code
    s.kill()
    scope_for(ctx).sessions.pop(sid, None)
    return {"closed": sid, "exit_code": ec}


async def _shell_list(ctx: ToolContext, a: dict) -> Any:
    sc = scope_for(ctx)
    _reap_sessions(sc)
    return {"sessions": [
        {"session_id": s.id, "command": s.command, "state": s.state, "mode": s.mode,
         "idle_ms": int((time.time() - s.last_out) * 1000)} for s in sc.sessions.values()]}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def _s(d: str) -> dict:
    return {"type": "string", "description": d}


def _i(d: str) -> dict:
    return {"type": "integer", "description": d}


def _b(d: str) -> dict:
    return {"type": "boolean", "description": d}


def _arr(d: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": d}


_META = {"category": "exec"}

TOOLS: list[Tool] = [
    Tool("shell_open",
         "Opens a persistent PTY interactive session for programs that need a real TTY (msfconsole, interactive "
         "ssh, mysql/python REPLs, password prompts, nc sessions) and returns a session_id. Use Bash for one-shot "
         "non-interactive commands. mode: interactive = block until the first prompt/quiet period and return the "
         "startup output; hands-free (default) = return immediately, poll with shell_read; dispatch = return "
         "immediately and be notified when it settles; monitor = watch in the background for the given regexes "
         "and be notified on a hit (the session keeps running). Set prompt_regex (e.g. 'msf6? >\\s*$') to detect "
         "command completion precisely. Always shell_close when done.",
         _obj({"command": _s("Interactive program to run, e.g. 'msfconsole -q' / 'ssh user@host' / 'python3'"),
               "mode": _s("interactive | hands-free (default) | dispatch | monitor"),
               "watch": _arr("Required for monitor mode: keywords/regexes to watch for; each is reported once"),
               "prompt_regex": _s("Optional: prompt regex (matched against the end of the output) marking completion"),
               "rows": _i("Optional: PTY rows (default 24)"),
               "cols": _i("Optional: PTY columns (default 80)"),
               "quiet_ms": _i("Optional: milliseconds of silence that count as done (default 8000)"),
               "startup_grace_ms": _i("Optional: startup grace in ms during which silence does not count (default 15000)")},
              ["command"]),
         _shell_open, False, _META),
    Tool("shell_send",
         "Sends input to an interactive session: text/submit/keys/hex/paste can be combined and are sent in that "
         "order. wait=true (default) waits for the command to finish and returns the incremental output.",
         _obj({"session_id": _s("Session id returned by shell_open"),
               "text": _s("Text to type (no automatic Enter)"),
               "submit": _b("Append Enter after the text"),
               "keys": _arr("Named keys: ctrl+c/ctrl+d/tab/enter/esc/up/down/left/right/..."),
               "hex": _arr("Raw bytes in hex, e.g. ['0x1b','0x5b','0x41']"),
               "paste": _s("Multi-line paste (bracketed paste, does not execute line by line)"),
               "wait": _b("Wait synchronously for the command to finish (default true)"),
               "prompt_regex": _s("Optional: override the session's completion prompt"),
               "quiet_ms": _i("Optional: silence threshold in ms (default 8000)"),
               "timeout_ms": _i("Optional: hard timeout for wait in ms (default 120000)")},
              ["session_id"]),
         _shell_send, False, _META),
    Tool("shell_read",
         "Reads the output of an interactive session without sending input. view=stream (default) is the "
         "incremental raw stream (continues from the last cursor; drain returns everything since); view=screen is "
         "a full-screen snapshot (for vim/htop style programs).",
         _obj({"session_id": _s("Session id"),
               "view": _s("stream (default) | screen"),
               "since_cursor": _i("stream: read after this cursor; omit to continue from the session's read cursor"),
               "max_lines": _i("stream: max lines to return (default 200)"),
               "max_bytes": _i("stream: max bytes to return (default 8192)"),
               "drain": _b("stream: true returns the whole raw stream since the cursor, ignoring limits")},
              ["session_id"]),
         _shell_read, True, _META),
    Tool("shell_close", "Closes an interactive session (kills the process, releases the PTY).",
         _obj({"session_id": _s("Session id")}, ["session_id"]), _shell_close, False, _META),
    Tool("shell_list", "Lists all current interactive sessions and their state.",
         _obj({}), _shell_list, True, _META),
]
