from __future__ import annotations

import json
import subprocess
from pathlib import Path
from shutil import which

from scripts.issue_tool.shared import CliError

WORKFLOW_LABEL_DEFAULTS: dict[str, tuple[str, str]] = {
    "ready": ("0E8A16", "Ready to start"),
    "in-progress": ("FBCA04", "Work in progress"),
    "review": ("5319E7", "In review"),
    "done": ("1D76DB", "Completed"),
    "status:in-progress": ("FBCA04", "Execution started"),
    "status:not-started": ("C2E0C6", "Not started"),
    "status:done": ("1D76DB", "Completed"),
    "status:blocked": ("B60205", "Blocked"),
}


def shutil_which(binary: str) -> str | None:
    return which(binary)


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


def gh_available() -> bool:
    return shutil_which("gh") is not None


def gh_json(args: list[str], *, root: Path, input_payload: dict | None = None) -> object:
    if not gh_available():
        raise CliError("gh CLI not found in PATH")
    cmd = ["gh", *args]
    input_text = json.dumps(input_payload) if input_payload is not None else None
    try:
        proc = run(cmd, cwd=root, input_text=input_text)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        stdout = exc.stdout.strip() if exc.stdout else ""
        raise CliError(
            "gh command failed "
            f"({exc.returncode}): {' '.join(cmd)}\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        ) from exc
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise CliError(f"gh returned non-JSON output for {' '.join(cmd)}") from exc


def gh_text(args: list[str], *, root: Path) -> str:
    if not gh_available():
        raise CliError("gh CLI not found in PATH")
    try:
        return run(["gh", *args], cwd=root).stdout
    except subprocess.CalledProcessError as exc:
        raise CliError(
            f"gh command failed ({exc.returncode}): {' '.join(['gh', *args])}\n"
            f"{(exc.stderr or exc.stdout or '').strip()}"
        ) from exc


def ensure_label_exists(root: Path, repo: str, label: str) -> None:
    color, desc = WORKFLOW_LABEL_DEFAULTS.get(label, ("BFDADC", "Workflow label"))
    gh_text(
        [
            "label",
            "create",
            label,
            "-R",
            repo,
            "--force",
            "--color",
            color,
            "--description",
            desc,
        ],
        root=root,
    )
