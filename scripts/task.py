#!/usr/bin/env python3
"""
task.py — Task lifecycle management for platform AaaS.

Reads docs/TASKS.md, manages git worktrees, generates structured Claude Code
prompts, and launches the agent on the task.

Commands:
    next              Print the next not-started task
    list              List all tasks with status
    start [TASK-NNN]  Run install-dev-tools.sh, create worktree, mark [~],
                      run validate-local, launch Claude Code.
                      Omit TASK-NNN to auto-select the next [ ] task.
    resume [TASK-NNN] Run install-dev-tools.sh, resume existing worktree,
                      relaunch Claude Code.
                      Omit TASK-NNN to auto-select the first [~] task with a worktree.
    finish TASK-NNN   Print finish checklist and next git/gh commands
    prompt TASK-NNN   Print the agent prompt without creating a worktree

Usage (via make):
    make task-next
    make task-start                  # auto-selects next [ ] task
    make task-start  TASK=TASK-011   # explicit task
    make task-resume                 # auto-selects first [~] task with worktree
    make task-resume TASK=TASK-011
    make task-finish TASK=TASK-011
    make task-prompt TASK=TASK-011
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Task:
    number: str  # zero-padded, e.g. "011"
    status: str  # "[ ]"  "[~]"  "[x]"  "[!]"
    title: str
    description: str  # full indented continuation block, stripped
    adrs: list[str] = field(default_factory=list)  # ["ADR-012"]
    tests: str = ""
    gate: str = ""
    phase: str = ""


# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(r.stdout.strip())


def default_worktrees_dir(root: Path) -> Path:
    return root.parent / "worktrees"


RuntimeEnv = Literal["local-wsl", "remote"]


def _looks_like_wsl(proc_version: str) -> bool:
    return "microsoft" in proc_version.lower()


def is_wsl_environment() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return _looks_like_wsl(Path("/proc/version").read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return False


def detect_runtime_env(requested: str = "auto") -> RuntimeEnv:
    req = (requested or "auto").strip().lower()
    if req == "local":
        return "local-wsl"
    if req == "remote":
        return "remote"

    # Explicit override requested by operator.
    if os.environ.get("WSL", "").strip().lower() == "local":
        return "local-wsl"

    return "local-wsl" if is_wsl_environment() else "remote"


# ---------------------------------------------------------------------------
# TASKS.md parser
# ---------------------------------------------------------------------------

_TASK_RE = re.compile(r"^\[([~x! ])\] (TASK-(\d+))\s{2,}(.+)")
_PHASE_RE = re.compile(r"^## (Phase \d+)")
_ADRS_RE = re.compile(r"ADRs:\s*([^|]+)")
_TESTS_RE = re.compile(r"Tests:\s*(.+)")
_GATE_RE = re.compile(r"Gate:\s*(.+)")


def parse_tasks(tasks_file: Path) -> list[Task]:
    content = tasks_file.read_text(encoding="utf-8")
    tasks: list[Task] = []
    current_phase = ""
    lines = content.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        m = _PHASE_RE.match(line)
        if m:
            current_phase = m.group(1)
            i += 1
            continue

        m = _TASK_RE.match(line)
        if not m:
            i += 1
            continue

        status_char = m.group(1)
        task_num = m.group(3)
        title = m.group(4).strip()
        status_map = {" ": "[ ]", "~": "[~]", "x": "[x]", "!": "[!]"}
        status = status_map.get(status_char, "[ ]")

        # Collect the indented continuation block
        desc_lines: list[str] = []
        adrs: list[str] = []
        tests = ""
        gate = ""
        i += 1

        while i < len(lines):
            cont = lines[i]
            # A non-empty line that starts without whitespace ends the block
            if cont and not cont[0].isspace():
                break
            desc_lines.append(cont)

            ma = _ADRS_RE.search(cont)
            if ma:
                raw = ma.group(1).strip()
                if raw.lower() not in ("none", "none (these are the adrs)"):
                    adrs = [a.strip() for a in raw.split(",") if a.strip()]

            mt = _TESTS_RE.search(cont)
            if mt:
                tests = mt.group(1).strip()

            mg = _GATE_RE.search(cont)
            if mg:
                gate = mg.group(1).strip()

            i += 1

        tasks.append(
            Task(
                number=task_num,
                status=status,
                title=title,
                description="\n".join(desc_lines).strip(),
                adrs=adrs,
                tests=tests,
                gate=gate,
                phase=current_phase,
            )
        )

    return tasks


def find_task(tasks: list[Task], task_id: str) -> Task | None:
    """Match 'TASK-011', '011', or '11'."""
    raw = task_id.upper().replace("TASK-", "")
    # Try zero-padded match first, then strip leading zeros
    for task in tasks:
        if task.number == raw.zfill(len(task.number)) or task.number.lstrip("0") == raw.lstrip("0"):
            return task
    return None


def next_available(tasks: list[Task]) -> Task | None:
    return next((t for t in tasks if t.status == "[ ]"), None)


def ensure_dev_tools(root: Path) -> None:
    """Run install-dev-tools.sh if present — idempotent pre-agent preflight."""
    script = root / "scripts" / "install-dev-tools.sh"
    if not script.exists():
        return
    print("==> Running install-dev-tools.sh (pre-agent preflight)...")
    result = subprocess.run(["bash", str(script)], cwd=root)
    if result.returncode != 0:
        print("ERROR: install-dev-tools.sh failed. Fix the environment before starting.")
        sys.exit(1)


def set_task_status(tasks_file: Path, task: Task, new_char: str) -> None:
    """Replace the status character for a task line in a TASKS.md file."""
    content = tasks_file.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^(\[[^\]]\]) (TASK-{re.escape(task.number)}\s)",
        re.MULTILINE,
    )
    new_content = pattern.sub(f"[{new_char}] \\2", content)
    if new_content == content:
        raise ValueError(f"Could not find TASK-{task.number} in {tasks_file}")
    tasks_file.write_text(new_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:50]


def worktree_path(root: Path, task: Task) -> Path:
    return default_worktrees_dir(root) / f"TASK-{task.number}-{slugify(task.title)}"


def branch_name(task: Task) -> str:
    return f"task/{task.number}-{slugify(task.title)}"


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------


def generate_prompt(
    task: Task,
    wt: Path,
    branch: str,
    runtime_env: RuntimeEnv = "local-wsl",
) -> str:
    adr_list = ", ".join(task.adrs) if task.adrs else "none"
    is_local = runtime_env == "local-wsl"

    worktree_line = (
        str(wt) if is_local else "(remote/mobile session; operator will provide repo path)"
    )
    preflight_step = (
        "4. Run `make validate-local` in this worktree — it must pass before you start"
        if is_local
        else (
            "4. Confirm remote/mobile session context with the operator and ask "
            "for repo path/tool availability before running validation"
        )
    )
    if is_local:
        work_loop = (
            "  inspect codebase → state plan with expected file changes →\n"
            "  implement → run the smallest relevant checks → inspect failures/logs/signals →\n"
            "  fix next issue → re-run tests / make validate-local → "
            "update docs/tests if required → repeat"
        )
        constraints_block = (
            f"  - Work only in this worktree ({wt})\n"
            f"  - Keep all changes scoped to TASK-{task.number}\n"
            "  - Follow every forbidden pattern in CLAUDE.md\n"
            "  - For any security decision: STOP and ask. Do not guess.\n"
            "  - When uncertain about DynamoDB schema, IAM, or authoriser logic: STOP and ask.\n"
            "  - Do not stop at the first failure; use test output, "
            "validate-local output, synth errors, and logs as signals."
        )
    else:
        work_loop = (
            "  inspect codebase → state plan with expected file changes →\n"
            "  implement → run checks/tests (as available) → inspect failures/logs/signals →\n"
            "  fix next issue → ask operator to run missing local-only checks when needed →\n"
            "  update docs/tests if required → repeat"
        )
        constraints_block = (
            f"  - Execution context is remote/mobile (`{runtime_env}`); "
            "do not assume local WSL tooling\n"
            "  - Do not assume a git worktree exists; ask the operator which "
            "repo path/branch to use\n"
            f"  - Keep all changes scoped to TASK-{task.number}\n"
            "  - Follow every forbidden pattern in CLAUDE.md\n"
            "  - For any security decision: STOP and ask. Do not guess.\n"
            "  - When uncertain about DynamoDB schema, IAM, or authoriser logic: STOP and ask.\n"
            "  - Do not stop at the first failure; use available test output, "
            "logs, and operator-provided signals to continue."
        )

    signals_block = (
        "  - test failures and stack traces\n"
        "  - make validate-local / validate-local-full output\n"
        "  - Ruff / Pyright / TypeScript / CDK synth output\n"
        "  - local logs (`make dev-logs`, `docker compose logs`) or platform logs (`make logs-*`)\n"
        "  - git diff/status/conflict state"
    )

    gate_block = ""
    if task.gate:
        gate_block = f"""
Gate: {task.gate}
STOP at this gate. Present your findings to the operator and wait for written
confirmation before proceeding. Do not advance past the gate unilaterally."""

    test_line = f"\nTests required: {task.tests}" if task.tests else ""

    return f"""\
Role: rigorous coding agent. CLAUDE.md is your single source of truth. Follow it exactly.

Task:     TASK-{task.number}: {task.title}
Phase:    {task.phase}
Branch:   {branch}
Worktree: {worktree_line}
Context:  {runtime_env}

Before writing any code, do these steps in order — do not skip any:
1. Read CLAUDE.md (already loaded as system prompt — re-read it now and confirm all constraints)
2. Read docs/ARCHITECTURE.md
3. Read ADRs: {adr_list}
{preflight_step}
5. State explicitly: "Starting TASK-{task.number}: {task.title}"

Task definition:
{task.description}
{test_line}{gate_block}

Work loop (repeat until closure condition is met):
{work_loop}

Primary diagnostic signals (use what is available):
{signals_block}

Constraints:
{constraints_block}

Closure condition: {
        "operator sign-off at gate — present findings and stop"
        if task.gate
        else "all task tests pass, make validate-local passes clean, and errors are cleared"
    }

Finish protocol (perform in order when closure is reached):
1. State: "TASK-{task.number} complete. Tests passing."
2. Run `make validate-local` — confirm it passes (do not skip this)
3. Run a senior engineer review focused on bugs, regressions, risks, and missing tests
4. Action review findings and re-run relevant tests/validation
5. Re-run senior engineer review; repeat steps 4-5 until findings are cleared
   (or operator accepts residual risk)
6. Commit all changes; commit message must reference TASK-{task.number}
7. Update docs/TASKS.md: mark this task [x] with today's date and commit SHA
8. Push only when errors are cleared, then open PR titled "TASK-{task.number}: {task.title}"
   Body must include: what was implemented, tests evidence, validate-local output,
   review findings addressed

Never mark complete or push if tests are failing, make validate-local fails,
or unresolved errors remain."""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _print_mobile_prompt(prompt: str, task: Task, runtime_env: RuntimeEnv) -> None:
    print()
    print(f"Remote/mobile handoff mode ({runtime_env})")
    print(
        "No local worktree was created. Prompt is printed for copy/paste into Claude Code mobile."
    )
    print()
    print("--- BEGIN CLAUDE CODE PROMPT ---")
    print(prompt)
    print("--- END CLAUDE CODE PROMPT ---")


def cmd_next(tasks: list[Task]) -> None:
    task = next_available(tasks)
    if not task:
        print("No not-started tasks found.")
        return
    print(f"Next:  TASK-{task.number}: {task.title}")
    print(f"Phase: {task.phase}")
    print(f"ADRs:  {', '.join(task.adrs) or 'none'}")
    if task.gate:
        print(f"Gate:  {task.gate}")
    print(f"\nTo start: make task-start TASK=TASK-{task.number}")


def cmd_list(tasks: list[Task]) -> None:
    current_phase = ""
    for task in tasks:
        if task.phase != current_phase:
            current_phase = task.phase
            print(f"\n{current_phase}")
        print(f"  {task.status} TASK-{task.number}: {task.title}")
    print()


def cmd_prompt(task_id: str, tasks: list[Task], root: Path, runtime_env: RuntimeEnv) -> None:
    task = _require_task(task_id, tasks)
    wt = worktree_path(root, task)
    branch = branch_name(task)
    print(generate_prompt(task, wt, branch, runtime_env))


def cmd_start(
    task_id: str | None,
    tasks: list[Task],
    root: Path,
    runtime_env: RuntimeEnv,
    dry_run: bool = False,
) -> None:
    is_local = runtime_env == "local-wsl"
    if is_local and not dry_run:
        ensure_dev_tools(root)

    task: Task
    if task_id:
        task = _require_task(task_id, tasks)
    else:
        _t = next_available(tasks)
        if not _t:
            print("No not-started tasks available.")
            sys.exit(1)
        task = _t
        print(f"Auto-selected: TASK-{task.number}: {task.title}")

    if task.status == "[x]":
        print(f"TASK-{task.number} is already complete ([x]).")
        sys.exit(1)
    if task.status == "[!]":
        print(f"TASK-{task.number} is blocked ([!]).")
        print(task.description)
        sys.exit(1)

    wt = worktree_path(root, task)
    branch = branch_name(task)
    prompt = generate_prompt(task, wt, branch, runtime_env)

    print(f"Task:     TASK-{task.number}: {task.title}")
    print(f"Branch:   {branch}")
    print(f"Worktree: {wt}")
    print(f"Context:  {runtime_env}")
    print()

    if dry_run:
        print("--- Generated prompt (dry run — worktree not created) ---")
        print(prompt)
        print("---------------------------------------------------------")
        return

    if not is_local:
        _print_mobile_prompt(prompt, task, runtime_env)
        print()
        print("Note: task status/worktree creation is skipped in remote/mobile mode.")
        print("Use `--env local` (or `WSL=local`) from WSL to use the full worktree workflow.")
        return

    if wt.exists():
        print(f"Worktree already exists at {wt}")
        print(f"Run: make task-resume TASK=TASK-{task.number}")
        sys.exit(1)

    # Create worktree
    default_worktrees_dir(root).mkdir(parents=True, exist_ok=True)

    # Prefer origin/main as base so we're always off the latest
    base = "origin/main"
    check = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/remotes/origin/main"],
        cwd=root,
    )
    if check.returncode != 0:
        base = "main"

    subprocess.run(
        ["git", "worktree", "add", str(wt), "-b", branch, base],
        cwd=root,
        check=True,
    )
    print(f"Created worktree at {wt}")

    # Mark task in-progress in the worktree's TASKS.md
    wt_tasks_file = wt / "docs" / "TASKS.md"
    try:
        set_task_status(wt_tasks_file, task, "~")
        subprocess.run(
            ["git", "commit", "-am", f"Mark TASK-{task.number} in progress [~]"],
            cwd=wt,
            check=True,
        )
        print(f"Marked TASK-{task.number} as [~] in TASKS.md")
    except Exception as e:
        print(f"WARNING: could not update TASKS.md status: {e}")

    # Preflight
    print("Running make validate-local in new worktree...")
    result = subprocess.run(["make", "validate-local"], cwd=wt)
    if result.returncode != 0:
        print()
        print("WARNING: validate-local failed. Fix before the agent starts work.")
        print("The worktree has been created; you can resume after fixing the issue.")
        print(f"  cd {wt} && make validate-local")
        sys.exit(1)

    # Hand off to Claude Code
    print()
    print("validate-local passed.")
    print()
    if not shutil.which("claude"):
        print("WARNING: 'claude' not found in PATH.")
        print(
            "Install Claude Code locally (`npm install -g "
            "@anthropic-ai/claude-code`) or use the prompt below."
        )
        _print_mobile_prompt(prompt, task, runtime_env)
        return
    print("Launching Claude Code...")
    os.chdir(wt)
    os.execvp("claude", ["claude", "--dangerously-skip-permissions", prompt])


def cmd_resume(task_id: str | None, tasks: list[Task], root: Path, runtime_env: RuntimeEnv) -> None:
    is_local = runtime_env == "local-wsl"
    if is_local:
        ensure_dev_tools(root)

    task: Task
    if task_id:
        task = _require_task(task_id, tasks)
    else:
        # Auto-select local: first [~] task whose worktree exists, else any task with a worktree.
        # Auto-select remote/mobile: first [~] task, else any task.
        if is_local:
            _t = next(
                (t for t in tasks if t.status == "[~]" and worktree_path(root, t).exists()),
                None,
            ) or next((t for t in tasks if worktree_path(root, t).exists()), None)
        else:
            _t = next((t for t in tasks if t.status == "[~]"), None) or next_available(tasks)
        if not _t:
            print("No in-progress tasks found. Use: make task-start")
            sys.exit(1)
        task = _t
        print(f"Auto-selected: TASK-{task.number}: {task.title}")

    wt = worktree_path(root, task)
    branch = branch_name(task)
    prompt = generate_prompt(task, wt, branch, runtime_env)

    if is_local and not wt.exists():
        print(f"Worktree not found at {wt}")
        print(f"Run: make task-start TASK=TASK-{task.number}")
        sys.exit(1)

    print(f"Resuming TASK-{task.number}: {task.title}")
    print(f"Worktree: {wt}")
    print(f"Context:  {runtime_env}")

    if not is_local:
        _print_mobile_prompt(prompt, task, runtime_env)
        return

    print()
    print("Running make validate-local...")
    subprocess.run(["make", "validate-local"], cwd=wt)
    print()
    if not shutil.which("claude"):
        print("WARNING: 'claude' not found in PATH.")
        print("Use the prompt below in Claude Code mobile or install the CLI locally.")
        _print_mobile_prompt(prompt, task, runtime_env)
        return
    print("Launching Claude Code...")
    os.chdir(wt)
    os.execvp("claude", ["claude", "--dangerously-skip-permissions", prompt])


def cmd_finish(task_id: str, tasks: list[Task], root: Path) -> None:
    task = _require_task(task_id, tasks)
    wt = worktree_path(root, task)
    branch = branch_name(task)

    print(f"Finish checklist — TASK-{task.number}: {task.title}")
    print(f"Branch:   {branch}")
    print(f"Worktree: {wt}")
    print()
    print("Before opening PR, confirm all of these:")
    print("  [ ] make validate-local passes")
    print("  [ ] All task tests pass")
    print("  [ ] Senior engineer review completed (bugs/regressions/risks/missing tests)")
    print(
        "  [ ] Review findings actioned and re-reviewed clean (or operator accepted residual risk)"
    )
    print("  [ ] No unresolved errors remain (tests/validation/logs)")
    if task.gate:
        print(f"  [ ] Gate cleared: {task.gate}")
    print(f"  [ ] Commit message references TASK-{task.number}")
    print("  [ ] docs/TASKS.md updated: [ ] → [x] with date")
    print()

    if wt.exists():
        # Uncommitted changes
        r = subprocess.run(["git", "status", "--short"], cwd=wt, capture_output=True, text=True)
        if r.stdout.strip():
            print("Uncommitted changes:")
            for line in r.stdout.strip().splitlines():
                print(f"  {line}")
            print()

        # Ahead/behind origin/main
        r = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"],
            cwd=wt,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split()
            if len(parts) == 2:
                behind, ahead = parts[0], parts[1]
                print(f"Commits ahead of origin/main: {ahead}")
                if int(behind) > 0:
                    print(f"  (behind by {behind} — consider rebasing first)")
                print()

    # Detect GH repo
    gh_repo = _detect_gh_repo(root)

    print("Next commands:")
    print("  # Close only after errors are cleared and review findings are addressed")
    print(f"  git -C {wt} push -u origin {branch}")
    if gh_repo:
        print(f"  gh pr create -R {gh_repo} \\")
        print(f"       --base main --head {branch} \\")
        print(f"       --title 'TASK-{task.number}: {task.title}' \\")
        print(f"       --body 'Implements TASK-{task.number}. Closes on merge.'")
    else:
        print(f"  gh pr create --base main --head {branch} \\")
        print(f"       --title 'TASK-{task.number}: {task.title}'")

    print()
    print("After PR is merged:")
    print(f"  git worktree remove {wt}")
    print(f"  git -C {root} branch -d {branch}")
    print("  git worktree prune")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_task(task_id: str, tasks: list[Task]) -> Task:
    task = find_task(tasks, task_id)
    if not task:
        print(f"ERROR: '{task_id}' not found in docs/TASKS.md")
        sys.exit(1)
    return task


def _detect_gh_repo(root: Path) -> str:
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return ""
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", r.stdout.strip())
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Platform AaaS task lifecycle")
    p.add_argument("command", choices=["next", "list", "start", "resume", "finish", "prompt"])
    p.add_argument("task", nargs="?", help="Task ID, e.g. TASK-011 or 011")
    p.add_argument(
        "--dry-run", action="store_true", help="Print prompt without creating worktree (start only)"
    )
    p.add_argument(
        "--env",
        choices=["auto", "local", "remote"],
        default="auto",
        help="Execution environment for task prompt/launch flow (default: auto via WSL detection)",
    )
    args = p.parse_args()
    runtime_env = detect_runtime_env(args.env)

    root = repo_root()
    tasks_file = root / "docs" / "TASKS.md"
    if not tasks_file.exists():
        print(f"ERROR: {tasks_file} not found")
        sys.exit(1)

    tasks = parse_tasks(tasks_file)

    match args.command:
        case "next":
            cmd_next(tasks)
        case "list":
            cmd_list(tasks)
        case "prompt":
            cmd_prompt(args.task or _require_task_arg(p), tasks, root, runtime_env)
        case "start":
            cmd_start(args.task, tasks, root, runtime_env, args.dry_run)
        case "resume":
            cmd_resume(args.task, tasks, root, runtime_env)
        case "finish":
            cmd_finish(args.task or _require_task_arg(p), tasks, root)


def _require_task_arg(p: argparse.ArgumentParser) -> str:
    p.error("task ID required, e.g. TASK-011")
    return ""  # unreachable


if __name__ == "__main__":
    main()
