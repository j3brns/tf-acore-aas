#!/usr/bin/env python3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CLI_PATH = _REPO_ROOT / "scripts" / "issue_tool" / "cli.py"
exec(compile(_CLI_PATH.read_text(encoding="utf-8"), str(_CLI_PATH), "exec"), globals(), globals())


if __name__ == "__main__":
    raise SystemExit(globals()["main"]())
