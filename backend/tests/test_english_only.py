"""The project is English-only: fail on any CJK character or CJK punctuation in text sources."""
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve()
# Repo checkout: <root>/backend/tests/this_file. In the api container the tests live in /app/tests.
ROOT = _HERE.parents[2] if (_HERE.parents[2] / "backend").is_dir() else _HERE.parents[1]
EXTS = {".py", ".ts", ".tsx", ".js", ".mjs", ".md", ".json", ".yml", ".yaml", ".sh", ".sql", ".css", ".toml", ".txt",
        ".example", ".html", ".mk"}
NAMES = {"Dockerfile", "Makefile", ".gitignore", ".dockerignore", ".env.example"}
SKIP_DIRS = {"node_modules", ".venv", ".next", ".git", "data", "__pycache__", ".pytest_cache", ".ruff_cache",
             "task_tools_lite.egg-info", ".verify"}
# Written as escape sequences (raw string) so this file itself stays ASCII.
CJK = re.compile(
    r"[\u2e80-\u2fdf\u3000-\u303f\u3040-\u30ff\u3100-\u312f\u3130-\u318f\u31a0-\u31ff\u3200-\u33ff"
    r"\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]"
)


def _files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or any(part in SKIP_DIRS or part.startswith(".next") for part in p.relative_to(ROOT).parts):
            continue
        if p.suffix in EXTS or p.name in NAMES or p.name.startswith(".env"):
            yield p


def test_no_cjk_characters():
    bad = []
    for p in _files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            m = CJK.search(line)
            if m:
                bad.append(f"{p.relative_to(ROOT)}:{n}: {m.group(0)!r}")
    assert not bad, "non-English characters found:\n" + "\n".join(bad[:50])
