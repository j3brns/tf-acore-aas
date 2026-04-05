#!/usr/bin/env python3
"""Legacy compatibility shim for the issue-tool CLI.

Canonical invocation is `python -m scripts.issue_tool ...`.
This module remains only so older local entry paths keep delegating without
using an exec-based loader.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from scripts.issue_tool import main
    from scripts.issue_tool.shared import CliError

    try:
        raise SystemExit(main())
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
