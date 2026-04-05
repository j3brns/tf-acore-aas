from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ValidationTask:
    label: str
    target: str


@dataclass(frozen=True)
class ValidationResult:
    label: str
    target: str
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


FAST_TASKS = (
    ValidationTask("Rules sync", "rules-sync-audit"),
    ValidationTask("Python", "validate-python"),
    ValidationTask("CDK TypeScript", "validate-cdk-ts-local"),
    ValidationTask("Secrets diff", "validate-secrets-diff"),
)

FULL_TASKS = (
    ValidationTask("Rules sync", "rules-sync-audit"),
    ValidationTask("Python", "validate-python"),
    ValidationTask("CDK", "validate-cdk"),
    ValidationTask("Secrets full", "validate-secrets-full"),
)


def build_task_set(mode: str) -> tuple[ValidationTask, ...]:
    if mode == "fast":
        return FAST_TASKS
    if mode == "full":
        return FULL_TASKS
    raise ValueError(f"Unsupported validation mode: {mode}")


def run_task(
    task: ValidationTask,
    *,
    repo_root: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ValidationResult:
    started = time.perf_counter()
    completed = runner(
        ["make", "--no-print-directory", task.target],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    duration = time.perf_counter() - started
    return ValidationResult(
        label=task.label,
        target=task.target,
        returncode=completed.returncode,
        duration_seconds=duration,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def print_summary(results: list[ValidationResult]) -> None:
    print("==> Validation summary")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.label} ({result.target}) {result.duration_seconds:.1f}s")


def print_failures(results: list[ValidationResult]) -> None:
    failures = [result for result in results if not result.ok]
    if not failures:
        return
    print()
    print("==> Failure details")
    for result in failures:
        print(f"--- {result.label} ({result.target}) ---")
        output = result.stdout.strip()
        error = result.stderr.strip()
        if output:
            print(output)
        if error:
            print(error)


def run_validation_mode(
    *,
    mode: str,
    repo_root: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    tasks = build_task_set(mode)
    print(f"==> Running local validation ({mode})")
    print("==> Launching parallel tasks")
    for task in tasks:
        print(f" - {task.label}: make {task.target}")

    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [
            executor.submit(run_task, task, repo_root=repo_root, runner=runner) for task in tasks
        ]
        results = [future.result() for future in futures]

    task_order = [task.target for task in tasks]
    ordered = sorted(results, key=lambda result: task_order.index(result.target))
    print()
    print_summary(ordered)
    print_failures(ordered)

    if any(not result.ok for result in ordered):
        return 1

    print()
    print("==> Validation passed")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fast or full local validation")
    parser.add_argument("mode", choices=("fast", "full"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    return run_validation_mode(mode=args.mode, repo_root=repo_root)


if __name__ == "__main__":
    sys.exit(main())
