from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.issue_tool.shared import CliError


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=capture_output,
        text=text,
        input=input_text,
    )


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def repo_root() -> Path:
    try:
        common_dir = Path(
            run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout.strip()
        )
        if common_dir.name == ".git":
            return common_dir.parent.resolve()
        return Path(run(["git", "rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
    except subprocess.CalledProcessError as exc:
        raise CliError("Not inside a git repository") from exc


def current_path() -> Path:
    return Path.cwd().resolve()


def origin_repo_slug(root: Path) -> str:
    try:
        url = run(["git", "remote", "get-url", "origin"], cwd=root).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise CliError("Could not read git remote 'origin'") from exc
    if url.startswith("git@") and "github.com:" in url:
        path = url.split("github.com:", 1)[1]
    elif "github.com/" in url:
        path = url.split("github.com/", 1)[1]
    else:
        raise CliError(f"Origin is not a GitHub remote: {url}")
    return path.removesuffix(".git").strip("/")
