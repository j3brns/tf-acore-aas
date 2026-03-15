#!/usr/bin/env python3
"""
plan_dev.py - Generate a structured implementation plan for a task description.

Usage:
    uv run python scripts/plan_dev.py "Implement the billing metering pipeline"
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a structured implementation plan for a task description."
    )
    parser.add_argument("task_description", help='Task description, e.g. "Implement X"')
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def existing_paths(root: Path, candidates: list[str]) -> list[str]:
    return [candidate for candidate in candidates if (root / candidate).exists()]


def infer_paths(task_description: str, root: Path) -> list[str]:
    lowered = task_description.lower()
    keyword_hints: list[tuple[tuple[str, ...], list[str]]] = [
        (
            ("plan-dev", "plan_dev", "makefile", "workflow", "stub script"),
            ["scripts/plan_dev.py", "Makefile", "tests/unit/test_plan_dev.py"],
        ),
        (
            ("authoriser", "authorizer", "jwt", "sigv4"),
            ["src/authoriser/handler.py", "tests/unit/test_authoriser_handler.py"],
        ),
        (
            ("tenant api", "tenant-api", "tenant_api"),
            ["src/tenant_api/handler.py", "tests/unit/test_tenant_api_handler.py"],
        ),
        (
            ("bridge", "invocation", "streaming", "async"),
            ["src/bridge/handler.py", "tests/unit/test_bridge_handler.py"],
        ),
        (
            ("cdk", "infrastructure", "stack", "terraform"),
            ["infra/cdk", "infra/terraform", "docs/ARCHITECTURE.md"],
        ),
        (
            ("spa", "frontend", "react", "portal"),
            ["spa", "tests", "README.md"],
        ),
    ]

    inferred: list[str] = []
    for keywords, paths in keyword_hints:
        if any(keyword in lowered for keyword in keywords):
            inferred.extend(paths)

    if not inferred:
        inferred.extend(["README.md", "docs/TASKS.md", "tests"])

    unique_paths: list[str] = []
    seen: set[str] = set()
    for path in inferred:
        if path not in seen and (root / path).exists():
            seen.add(path)
            unique_paths.append(path)
    return unique_paths


def extract_task_id(task_description: str) -> str | None:
    match = re.search(r"\bTASK-(\d+)\b", task_description, re.IGNORECASE)
    if not match:
        return None
    return f"TASK-{match.group(1).zfill(3)}"


def find_task_snapshot(root: Path, task_id: str | None) -> str | None:
    if not task_id:
        return None

    tasks_file = root / "docs" / "TASKS.md"
    if not tasks_file.exists():
        return None

    lines = tasks_file.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\[[ ~x!]\] {re.escape(task_id)}\s{{2,}}(.+)")

    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue

        detail_lines = [f"{task_id}: {match.group(1).strip()}"]
        next_index = index + 1
        while next_index < len(lines):
            candidate = lines[next_index]
            if candidate and not candidate[0].isspace():
                break
            if candidate.strip():
                detail_lines.append(candidate.strip())
            next_index += 1
        return "\n".join(detail_lines)

    return None


def build_plan(task_description: str, root: Path) -> str:
    read_first_docs = existing_paths(
        root,
        ["AGENTS.md", "CLAUDE.md", "README.md", "docs/ARCHITECTURE.md", "docs/TASKS.md"],
    )
    touched_paths = infer_paths(task_description, root)
    task_id = extract_task_id(task_description)
    task_snapshot = find_task_snapshot(root, task_id)

    lines = [
        "# Development Plan",
        "",
        f"Task: {task_description}",
    ]

    if task_snapshot:
        lines.extend(
            [
                "",
                "Related task snapshot:",
                task_snapshot,
            ]
        )

    lines.extend(
        [
            "",
            "Read-first context:",
            *[f"- `{path}`" for path in read_first_docs],
            "",
            "Likely touched paths:",
            *[f"- `{path}`" for path in touched_paths],
            "",
            "Execution plan:",
            (
                "1. Inspect the current implementation, command wiring, and any "
                "existing tests for this task area."
            ),
            (
                "2. Confirm the required behavior and workflow gates from the "
                "repo rules and adjacent docs."
            ),
            (
                "3. Implement the smallest scoped change that makes the command "
                "or feature real instead of implied."
            ),
            (
                "4. Add or update focused regression tests for the interface "
                "contract and the primary success path."
            ),
            (
                "5. Run targeted checks first, then rerun repo workflow "
                "validation commands required for issue work."
            ),
            "",
            "Validation checklist:",
            "- Run the smallest relevant unit tests for the touched files.",
            "- Run `make preflight-session`.",
            "- Run `make pre-validate-session` before any push.",
            "- Run `make issues-audit` and reconcile if needed.",
            "",
            "Risks to watch:",
            (
                "- Keep changes scoped to the described task; do not widen into "
                "adjacent workflow refactors."
            ),
            (
                "- Keep repo rules, Make targets, and tests aligned so "
                "contributors are not sent through dead paths."
            ),
            (
                "- Stop and ask if the change would alter IAM, tenant "
                "isolation, authoriser validation, or other guarded areas."
            ),
        ]
    )

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(build_plan(args.task_description, repo_root()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
