from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.issue_tool.shared import CliError


def validation_receipts_root(root: Path, receipts_dir: str) -> Path:
    return root / receipts_dir


def validation_receipt_path(
    root: Path, issue_number: int, head_sha: str, receipts_dir: str
) -> Path:
    filename = f"issue-{issue_number}-{head_sha[:12]}.json"
    return validation_receipts_root(root, receipts_dir) / filename


def find_latest_validation_receipt(root: Path, issue_id: int, receipts_dir: str) -> Path | None:
    receipts_root = validation_receipts_root(root, receipts_dir)
    if not receipts_root.exists():
        return None
    matches = sorted(
        receipts_root.glob(f"issue-{issue_id}-*.json"),
        key=lambda candidate: candidate.stat().st_mtime,
    )
    return matches[-1] if matches else None


def git_issue_branches(
    root: Path, issue_id: int, *, run_fn: Callable[..., Any]
) -> dict[str, list[str]]:
    def _list_branches(pattern: str, *, remote: bool) -> list[str]:
        cmd = ["git", "branch"]
        if remote:
            cmd.append("-r")
        cmd.extend(["--format=%(refname:short)", "--list", pattern])
        output = run_fn(cmd, cwd=root, check=False).stdout
        return [line.strip() for line in output.splitlines() if line.strip()]

    return {
        "local": _list_branches(f"wt/*/{issue_id}-*", remote=False),
        "remote": _list_branches(f"origin/wt/*/{issue_id}-*", remote=True),
    }


def git_log_issue_matches(
    root: Path, issue_id: int, *, run_fn: Callable[..., Any], limit: int = 5
) -> list[dict[str, str]]:
    output = run_fn(
        [
            "git",
            "log",
            "--all",
            "--extended-regexp",
            f"-n{limit}",
            "--pretty=format:%H%x09%cI%x09%s",
            "--grep",
            rf"#{issue_id}\b",
            "--grep",
            rf"issue[- ]{issue_id}\b",
        ],
        cwd=root,
        check=False,
    ).stdout.strip()
    matches: list[dict[str, str]] = []
    if not output:
        return matches
    for line in output.splitlines():
        sha, ts, subject = (line.split("\t", 2) + ["", ""])[:3]
        matches.append({"sha": sha, "timestamp": ts, "subject": subject})
    return matches


def historical_issue_evidence(
    root: Path, issue_id: int, *, run_fn: Callable[..., Any]
) -> dict[str, object] | None:
    branches = git_issue_branches(root, issue_id, run_fn=run_fn)
    preferred_branch = next(iter(branches["local"]), None) or next(iter(branches["remote"]), None)
    branch_tip: dict[str, str] | None = None
    divergence: dict[str, int] | None = None
    if preferred_branch:
        tip = run_fn(
            ["git", "log", "-1", "--format=%H%x09%cI%x09%s", preferred_branch],
            cwd=root,
            check=False,
        ).stdout.strip()
        if tip:
            sha, ts, subject = (tip.split("\t", 2) + ["", ""])[:3]
            branch_tip = {"sha": sha, "timestamp": ts, "subject": subject}
        counts = run_fn(
            ["git", "rev-list", "--left-right", "--count", f"origin/main...{preferred_branch}"],
            cwd=root,
            check=False,
        ).stdout.strip()
        if counts:
            behind, ahead = [int(part) for part in counts.split()]
            divergence = {"behind": behind, "ahead": ahead}
    log_matches = git_log_issue_matches(root, issue_id, run_fn=run_fn)
    if preferred_branch is None and not log_matches:
        return None
    return {
        "branches": branches,
        "preferred_branch": preferred_branch,
        "branch_tip": branch_tip,
        "divergence_vs_origin_main": divergence,
        "log_matches": log_matches,
    }


def write_validation_receipt(
    root: Path,
    *,
    issue_id: int,
    worktree_path: Path,
    branch: str | None,
    check_name: str,
    run_fn: Callable[..., Any],
    write_json_file_fn: Callable[[Path, dict[str, object]], Path],
    receipts_dir: str,
) -> Path:
    head_sha = run_fn(["git", "rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
    payload = {
        "issue_number": issue_id,
        "branch": branch,
        "worktree_path": str(worktree_path),
        "check": check_name,
        "result": "pass",
        "head_sha": head_sha,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return write_json_file_fn(
        validation_receipt_path(root, issue_id, head_sha, receipts_dir),
        payload,
    )


def audit_issue_handoff_evidence(
    *,
    root: Path,
    repo: str,
    issue_id: int,
    target: Any,
    report_path: Path,
    read_json_file_fn: Callable[[Path], dict[str, object] | None],
    read_closeout_report_fn: Callable[[Path], dict[str, object]],
    issue_state_path_fn: Callable[[Path, int], Path],
) -> dict[str, object]:
    state_path = issue_state_path_fn(root, issue_id)
    if not state_path.exists():
        raise CliError(f"Missing issue state evidence: {state_path}")
    if not report_path.exists():
        raise CliError(f"Missing closeout report: {report_path}")

    issue_state = read_json_file_fn(state_path)
    if not isinstance(issue_state, dict):
        raise CliError(f"Invalid issue state evidence: {state_path}")
    closeout = read_closeout_report_fn(report_path)
    if str(closeout.get("stage")) != "complete":
        raise CliError("Closeout report is not complete")

    events = issue_state.get("events")
    if not isinstance(events, list) or not events:
        raise CliError("Issue state evidence has no events")

    event_types = [
        str(event.get("event_type"))
        for event in events
        if isinstance(event, dict) and event.get("event_type")
    ]
    if not event_types:
        raise CliError("Issue state evidence has no typed events")

    required_any_start = {"worktree-created", "worktree-reused", "worktree-resumed"}
    if not any(event_type in required_any_start for event_type in event_types):
        raise CliError("Issue state evidence is missing a worktree start/resume event")
    if "closeout-started" not in event_types:
        raise CliError("Issue state evidence is missing closeout-started")
    if event_types[-1] != "closeout-complete":
        raise CliError(
            f"Final issue state event must be closeout-complete (found {event_types[-1]})"
        )

    summary_payload: dict[str, object] = {
        "issue_number": issue_id,
        "repo": repo,
        "branch": target.branch,
        "worktree_path": str(target.path),
        "final_state": issue_state.get("state"),
        "last_event_type": issue_state.get("last_event_type"),
        "event_types": event_types,
        "event_count": len(event_types),
        "cleanup_verified": bool(closeout.get("cleanup_verified")),
        "cleanup": closeout.get("cleanup"),
        "issue_closed": bool(closeout.get("issue_closed")),
        "closeout_stage": closeout.get("stage"),
        "report_path": str(report_path),
    }
    evidence_hash = hashlib.sha256(
        json.dumps(summary_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **summary_payload,
        "evidence_hash": evidence_hash,
        "state_path": str(state_path),
    }


def build_issue_handback_comment(summary: dict[str, object]) -> str:
    event_types = summary.get("event_types")
    ordered = ", ".join(event_types) if isinstance(event_types, list) else ""
    return "\n".join(
        [
            "Execution evidence: PASS",
            f"Issue: #{summary['issue_number']}",
            f"Branch: {summary['branch']}",
            f"Worktree: {summary['worktree_path']}",
            f"Terminal state: {summary['final_state']}",
            f"Last event: {summary['last_event_type']}",
            f"Events ({summary['event_count']}): {ordered}",
            f"Cleanup verified: {summary['cleanup_verified']}",
            f"Closeout: {summary['closeout_stage']}",
            f"Evidence hash: {summary['evidence_hash']}",
        ]
    )


def issue_evidence_summary(
    root: Path,
    issue_id: int,
    *,
    issue_state_path_fn: Callable[[Path, int], Path],
    latest_closeout_report_path_fn: Callable[[Path, int], Path | None],
    read_json_file_fn: Callable[[Path], dict[str, object] | None],
    find_latest_validation_receipt_fn: Callable[[Path, int], Path | None],
    historical_issue_evidence_fn: Callable[[Path, int], dict[str, object] | None],
    linked_worktree_for_issue_fn: Callable[[Path, int], Any],
) -> dict[str, object]:
    state_path = issue_state_path_fn(root, issue_id)
    state = read_json_file_fn(state_path)
    closeout_path = latest_closeout_report_path_fn(root, issue_id)
    closeout = read_json_file_fn(closeout_path) if closeout_path is not None else None
    validation_path = find_latest_validation_receipt_fn(root, issue_id)
    validation_receipt = read_json_file_fn(validation_path) if validation_path else None
    linked = linked_worktree_for_issue_fn(root, issue_id)
    historical = None
    has_local_evidence = any(
        value is not None
        for value in (
            state,
            closeout,
            validation_receipt,
            linked,
        )
    )
    if not has_local_evidence:
        historical = historical_issue_evidence_fn(root, issue_id)
    evidence_source = "local" if has_local_evidence else ("historical" if historical else "none")
    return {
        "issue_number": issue_id,
        "evidence_source": evidence_source,
        "linked_worktree": str(linked.path) if linked is not None else None,
        "linked_branch": linked.branch if linked is not None else None,
        "state_path": str(state_path) if state_path.exists() else None,
        "state": state,
        "closeout_path": str(closeout_path) if closeout_path is not None else None,
        "closeout": closeout,
        "validation_receipt_path": str(validation_path) if validation_path is not None else None,
        "validation_receipt": validation_receipt,
        "historical": historical,
    }
