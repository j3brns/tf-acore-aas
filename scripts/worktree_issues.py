#!/usr/bin/env python3
"""
Issue-driven worktree workflow (GitHub Issues as source of truth).

Key behavior:
- Queue order uses `Seq:` in issue bodies as the canonical ordering.
- `Depends on:` task IDs (TASK-###) gate runnable items.
- Uses `gh` CLI for GitHub reads/writes and local `git worktree` for worktree ops.

This intentionally keeps Makefile targets thin; the policy/selection logic lives here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

WORKTREE_BRANCH_REGEX = re.compile(r"^wt/[a-z0-9._-]+/[0-9]+-[a-z0-9._-]+$")
WORKTREE_BRANCH_ISSUE_RE = re.compile(r"^wt/[^/]+/([0-9]+)-")
MANAGED_TASK_ID_RE = re.compile(r"<!--\s*codex-task-id:\s*(TASK-\d+)\s*-->", re.I)
SEQ_RE = re.compile(r"(?mi)^Seq:\s*(\d+)\s*$")
DEPENDS_RE = re.compile(r"(?mi)^Depends on:\s*(.+?)\s*$")
TASK_ID_TOKEN_RE = re.compile(r"TASK-\d+")
TITLE_TASK_RE = re.compile(r"^(TASK-\d+):\s")
STATUS_LABELS = {"status:not-started", "status:in-progress", "status:blocked", "status:done"}


class CliError(RuntimeError):
    pass


@dataclass(slots=True)
class WorktreeInfo:
    path: Path
    head: str
    branch: str
    is_primary: bool = False


@dataclass(slots=True)
class Issue:
    number: int
    title: str
    state: str
    created_at: str
    body: str
    labels: list[str]
    url: str
    task_id: str | None = None
    seq: int | None = None
    depends_on: list[str] = field(default_factory=list)

    def has_label(self, label: str) -> bool:
        return label in self.labels

    def priority_rank(self) -> int:
        labelset = {label.lower() for label in self.labels}
        if {"p0", "priority:p0", "priority:high", "priority:critical"} & labelset:
            return 0
        if {"p1", "priority:p1", "priority:medium"} & labelset:
            return 1
        if {"p2", "priority:p2", "priority:low"} & labelset:
            return 2
        if {"p3", "priority:p3"} & labelset:
            return 3
        return 50


@dataclass(slots=True)
class QueueItem:
    issue: Issue
    runnable: bool
    blocked_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QueueSelection:
    source_mode: str
    items: list[QueueItem]
    source_note: str = ""

    @property
    def runnable(self) -> list[QueueItem]:
        return [item for item in self.items if item.runnable]


@dataclass(slots=True)
class AuditFinding:
    severity: Literal["error", "warning"]
    issue_number: int
    message: str


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
        return Path(run(["git", "rev-parse", "--show-toplevel"]).stdout.strip())
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


def gh_available() -> bool:
    return shutil_which("gh") is not None


def shutil_which(binary: str) -> str | None:
    from shutil import which

    return which(binary)


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


def parse_task_id_from_issue(issue: dict) -> str | None:
    body = str(issue.get("body") or "")
    title = str(issue.get("title") or "")
    if m := MANAGED_TASK_ID_RE.search(body):
        return m.group(1).upper()
    if m := TITLE_TASK_RE.match(title):
        return m.group(1).upper()
    return None


def parse_depends(text: str | None) -> list[str]:
    if not text:
        return []
    text = text.strip()
    if not text or text.lower() in {"none", "n/a", "-"}:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for token in TASK_ID_TOKEN_RE.findall(text.upper()):
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def lifecycle_status(issue: Issue) -> str:
    labels = set(issue.labels)
    if "status:blocked" in labels:
        return "blocked"
    if "status:in-progress" in labels:
        return "in-progress"
    if "status:done" in labels:
        return "done"
    if "status:not-started" in labels:
        return "not-started"
    return "unknown"


def status_labels(issue: Issue) -> list[str]:
    return [label for label in issue.labels if label in STATUS_LABELS]


def choose_reconciled_status(issue: Issue) -> str:
    statuses = status_labels(issue)
    state = issue.state
    if state == "closed":
        return "status:done"
    # open state
    if "status:in-progress" in statuses:
        return "status:in-progress"
    if "status:blocked" in statuses:
        return "status:blocked"
    if "status:not-started" in statuses:
        return "status:not-started"
    # open+done or missing/invalid status should return to startable backlog state
    return "status:not-started"


def reconcile_issue_label_changes(issue: Issue) -> tuple[list[str], list[str]]:
    """Return (add_labels, remove_labels) to enforce lifecycle label policy."""
    desired = choose_reconciled_status(issue)
    labels = set(issue.labels)
    current_status = set(status_labels(issue))
    remove_labels = sorted(current_status - {desired})
    add_labels: list[str] = []
    if desired not in labels:
        add_labels.append(desired)
    if "ready" in labels and desired != "status:not-started":
        remove_labels.append("ready")
    return add_labels, sorted(set(remove_labels))


def assert_issue_startable(issue: Issue, *, allow_blocked: bool) -> None:
    if issue.state != "open":
        raise CliError(f"Issue #{issue.number} is {issue.state}; must be open to start work")
    status = lifecycle_status(issue)
    if status == "unknown":
        raise CliError(
            f"Issue #{issue.number} is missing/invalid status:* label. Run `make issues-reconcile`."
        )
    if status == "done":
        raise CliError(f"Issue #{issue.number} is status:done; cannot start new work")
    if status == "in-progress":
        raise CliError(f"Issue #{issue.number} is already status:in-progress. Use worktree-resume.")
    if status == "blocked" and not allow_blocked:
        raise CliError(f"Issue #{issue.number} is status:blocked (use --allow-blocked to override)")


def parse_issue_meta(body: str) -> tuple[int | None, list[str]]:
    seq = int(m.group(1)) if (m := SEQ_RE.search(body or "")) else None
    depends = parse_depends(m.group(1)) if (m := DEPENDS_RE.search(body or "")) else []
    return seq, depends


def fetch_repo_issues(
    root: Path,
    repo: str,
    *,
    state: Literal["open", "closed", "all"] = "all",
) -> list[Issue]:
    page = 1
    out: list[Issue] = []
    while True:
        data = gh_json(
            [
                "api",
                f"repos/{repo}/issues",
                "--method",
                "GET",
                "-f",
                f"state={state}",
                "-f",
                "per_page=100",
                "-f",
                f"page={page}",
            ],
            root=root,
        )
        if not isinstance(data, list):
            raise CliError("Unexpected GitHub API response for issues list")
        if not data:
            break
        for raw in data:
            if not isinstance(raw, dict):
                continue
            if "pull_request" in raw:
                continue
            labels = [
                x["name"] for x in raw.get("labels", []) if isinstance(x, dict) and "name" in x
            ]
            body = str(raw.get("body") or "")
            seq, depends = parse_issue_meta(body)
            out.append(
                Issue(
                    number=int(raw["number"]),
                    title=str(raw.get("title") or ""),
                    state=str(raw.get("state") or "").lower(),
                    created_at=str(raw.get("created_at") or raw.get("createdAt") or ""),
                    body=body,
                    labels=labels,
                    url=str(raw.get("html_url") or raw.get("url") or ""),
                    task_id=parse_task_id_from_issue(raw),
                    seq=seq,
                    depends_on=depends,
                )
            )
        if len(data) < 100:
            break
        page += 1
    return out


def build_queue(
    issues: list[Issue],
    *,
    stream_label: str | None = None,
    mode: Literal["auto", "ready", "open-task"] = "auto",
) -> QueueSelection:
    task_issues = [i for i in issues if "type:task" in i.labels]
    by_task_id = {i.task_id: i for i in task_issues if i.task_id}
    source_note = ""

    def stream_ok(issue: Issue) -> bool:
        return not stream_label or stream_label in issue.labels

    open_task = [i for i in task_issues if i.state == "open" and stream_ok(i)]
    # Queue excludes actively worked items. They remain visible via issue views / finish-summary.
    queued_open_task = [i for i in open_task if lifecycle_status(i) != "in-progress"]
    open_ready = [i for i in queued_open_task if "ready" in i.labels]

    source_mode = mode
    if mode == "auto":
        if open_ready:
            source_mode = "ready"
        else:
            source_mode = "open-task"
            source_note = (
                "auto-fallback: no queued task issues labeled 'ready' (excludes status:in-progress)"
            )
    if source_mode == "ready":
        candidates = open_ready
    elif source_mode == "open-task":
        candidates = queued_open_task
    else:
        raise CliError(f"Unsupported queue mode: {mode}")

    items: list[QueueItem] = []
    for issue in candidates:
        reasons: list[str] = []
        if lifecycle_status(issue) == "blocked":
            reasons.append("blocked by status label (status:blocked)")
        for dep_task_id in issue.depends_on:
            dep = by_task_id.get(dep_task_id)
            if dep is None:
                reasons.append(f"missing dependency {dep_task_id}")
                continue
            if dep.state != "closed":
                reasons.append(f"blocked by {dep_task_id} (issue #{dep.number} is {dep.state})")
        items.append(QueueItem(issue=issue, runnable=(len(reasons) == 0), blocked_reasons=reasons))

    items.sort(
        key=lambda item: (
            item.issue.seq if item.issue.seq is not None else 999_999_999,
            item.issue.priority_rank(),
            item.issue.created_at or "",
            item.issue.number,
        )
    )
    return QueueSelection(source_mode=str(source_mode), items=items, source_note=source_note)


def print_queue(
    selection: QueueSelection, *, limit: int | None = None, show_blocked: bool = True
) -> None:
    items = selection.items if show_blocked else selection.runnable
    if limit is not None:
        items = items[: max(0, limit)]
    if not items:
        print(f"No issues in queue (source={selection.source_mode}).")
        return
    print(f"Issue queue (source={selection.source_mode}; order=Seq -> priority -> createdAt)")
    if selection.source_note:
        print(f"  note: {selection.source_note}")
    for idx, item in enumerate(items, start=1):
        issue = item.issue
        seq_text = str(issue.seq) if issue.seq is not None else "unset"
        labels = "|".join(issue.labels) if issue.labels else "-"
        status = "RUNNABLE" if item.runnable else "BLOCKED"
        print(f"{idx:>2}. #{issue.number} [{status}] Seq:{seq_text} {issue.title}")
        print(f"    labels: {labels}")
        if item.blocked_reasons:
            print(f"    why:    {'; '.join(item.blocked_reasons)}")


def choose_next_runnable(selection: QueueSelection) -> QueueItem:
    for item in selection.items:
        if item.runnable:
            return item
    raise CliError(
        f"No runnable issues found in queue (source={selection.source_mode}). "
        "Resolve dependencies or adjust labels."
    )


def audit_issues(issues: list[Issue]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    task_issues = [i for i in issues if "type:task" in i.labels]

    for issue in task_issues:
        states = status_labels(issue)
        state_set = set(states)
        if len(state_set) != 1:
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message=(
                        f"expected exactly one status:* label, found {sorted(state_set) or 'none'}"
                    ),
                )
            )
            continue

        status = states[0]
        if issue.state == "open" and status == "status:done":
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message="open task cannot be status:done",
                )
            )
        if issue.state == "closed" and status != "status:done":
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message=f"closed task must be status:done (found {status})",
                )
            )
        if "ready" in issue.labels and status != "status:not-started":
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message=f"ready label requires status:not-started (found {status})",
                )
            )
        if issue.state == "open" and issue.seq is None:
            findings.append(
                AuditFinding(
                    severity="warning",
                    issue_number=issue.number,
                    message="open task is missing Seq marker",
                )
            )

    # Objective gate: next runnable item must be a startable task, never in-progress/blocked/done.
    selection = build_queue(issues, mode="auto")
    try:
        next_item = choose_next_runnable(selection)
        next_status = lifecycle_status(next_item.issue)
        if next_status != "not-started":
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=next_item.issue.number,
                    message=(
                        "next runnable queue item must be status:not-started "
                        f"(found status:{next_status})"
                    ),
                )
            )
    except CliError:
        # Empty/runnable-none queue is valid during full blockage or completion.
        pass

    return findings


def slugify_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"^[a-z0-9._-]+:\s*", "", text)  # trim issue prefix like TASK-015:
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:60] or "task"


def infer_scope(issue: Issue) -> str:
    labels = {label.lower() for label in issue.labels}
    title = issue.title.lower()
    if "docs" in labels or any(
        t in title for t in ("readme", "roadmap", "runbook", "adr", "docs/")
    ):
        return "docs"
    if "ci" in labels or any(t in title for t in ("pipeline", "gitlab", "ci/cd")):
        return "ci"
    if any(t in title for t in ("spa", "frontend", "react", "bff")):
        return "frontend"
    if any(t in title for t in ("stack", "cdk", "terraform", "infra")):
        return "infra"
    return "task"


def list_worktrees(root: Path) -> list[WorktreeInfo]:
    try:
        text = run(["git", "worktree", "list", "--porcelain"], cwd=root).stdout
    except subprocess.CalledProcessError as exc:
        raise CliError("Failed to list worktrees") from exc
    entries: list[WorktreeInfo] = []
    cur_path: Path | None = None
    cur_head = ""
    cur_branch = "(detached)"
    for line in text.splitlines():
        if line.startswith("worktree "):
            if cur_path is not None:
                entries.append(WorktreeInfo(cur_path, cur_head, cur_branch))
            cur_path = Path(line[len("worktree ") :]).resolve()
            cur_head = ""
            cur_branch = "(detached)"
        elif line.startswith("HEAD "):
            cur_head = line[len("HEAD ") :]
        elif line.startswith("branch refs/heads/"):
            cur_branch = line[len("branch refs/heads/") :]
        elif line.strip() == "":
            if cur_path is not None:
                entries.append(WorktreeInfo(cur_path, cur_head, cur_branch))
                cur_path = None
                cur_head = ""
                cur_branch = "(detached)"
    if cur_path is not None:
        entries.append(WorktreeInfo(cur_path, cur_head, cur_branch))
    if entries:
        primary = entries[0].path
        for entry in entries:
            entry.is_primary = entry.path == primary
    return entries


def default_worktrees_dir(root: Path) -> Path:
    return root.parent / "worktrees"


def suggest_worktree_dir_name(issue_number: int, base_dir: Path) -> str:
    preferred = f"wt{issue_number}"
    if not (base_dir / preferred).exists():
        return preferred
    i = 2
    while True:
        candidate = f"wt{issue_number}-{i}"
        if not (base_dir / candidate).exists():
            return candidate
        i += 1


def choose_base_ref(root: Path, required_main_branch: str = "main") -> str:
    remote_ref = f"refs/remotes/origin/{required_main_branch}"
    try:
        run(["git", "show-ref", "--verify", "--quiet", remote_ref], cwd=root, check=True)
        return f"origin/{required_main_branch}"
    except subprocess.CalledProcessError:
        return required_main_branch


def issue_by_number(issues: list[Issue], number: int) -> Issue:
    for issue in issues:
        if issue.number == number:
            return issue
    raise CliError(f"Issue #{number} not found in fetched dataset")


def claim_issue(root: Path, repo: str, issue: Issue) -> bool:
    # Re-fetch labels to reduce stale-queue races.
    data = gh_json(["issue", "view", str(issue.number), "-R", repo, "--json", "labels"], root=root)
    if not isinstance(data, dict):
        raise CliError(f"Unexpected response while checking issue #{issue.number}")
    labels = [x["name"] for x in data.get("labels", []) if isinstance(x, dict) and "name" in x]
    had_ready = "ready" in labels
    states = [label for label in labels if label in STATUS_LABELS]
    if len(set(states)) != 1:
        raise CliError(
            f"Issue #{issue.number} has invalid status labels {sorted(set(states)) or 'none'}; "
            "run `make issues-reconcile`"
        )
    if states[0] != "status:not-started":
        raise CliError(
            f"Issue #{issue.number} must be status:not-started to claim (found {states[0]})"
        )
    ensure_label_exists(root, repo, "status:in-progress")

    args = ["issue", "edit", str(issue.number), "-R", repo]
    if had_ready:
        args += ["--remove-label", "ready"]
    if "status:not-started" in labels:
        args += ["--remove-label", "status:not-started"]
    if "status:in-progress" not in labels:
        args += ["--add-label", "status:in-progress"]
    gh_text(args, root=root)
    return had_ready


def unclaim_issue(root: Path, repo: str, issue: Issue, *, add_ready: bool = True) -> None:
    ensure_label_exists(root, repo, "status:not-started")
    if add_ready:
        ensure_label_exists(root, repo, "ready")
    args = ["issue", "edit", str(issue.number), "-R", repo]
    # Best-effort rollback for failed worktree creation.
    args += ["--remove-label", "status:in-progress", "--add-label", "status:not-started"]
    if add_ready:
        args += ["--add-label", "ready"]
    gh_text(args, root=root)


def create_worktree_for_issue(
    *,
    root: Path,
    repo: str,
    issue: Issue,
    base_dir: Path,
    base_ref: str | None,
    scope: str | None,
    slug: str | None,
    folder_name: str | None,
    auto_claim: bool,
    preflight: bool,
    dry_run: bool,
) -> Path:
    scope_val = scope or infer_scope(issue)
    slug_val = slug or slugify_text(issue.title)
    if not re.fullmatch(r"[a-z0-9._-]+", scope_val):
        raise CliError(f"Invalid scope '{scope_val}'")
    if not re.fullmatch(r"[a-z0-9._-]+", slug_val):
        raise CliError(f"Invalid slug '{slug_val}'")
    branch = f"wt/{scope_val}/{issue.number}-{slug_val}"
    if not WORKTREE_BRANCH_REGEX.fullmatch(branch):
        raise CliError(
            f"Branch name '{branch}' does not match policy {WORKTREE_BRANCH_REGEX.pattern}"
        )

    base_dir.mkdir(parents=True, exist_ok=True)
    name_val = folder_name or suggest_worktree_dir_name(issue.number, base_dir)
    wt_path = (base_dir / name_val).resolve()
    if wt_path.exists():
        raise CliError(f"Worktree path already exists: {wt_path}")

    start_ref = base_ref or choose_base_ref(root)
    # Ensure local branch doesn't already exist.
    try:
        run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=root, check=True
        )
        raise CliError(f"Local branch already exists: {branch}")
    except subprocess.CalledProcessError:
        pass

    print("Create worktree")
    print(f"  issue:   #{issue.number} {issue.title}")
    print(f"  path:    {wt_path}")
    print(f"  branch:  {branch}")
    print(f"  baseRef: {start_ref}")
    if dry_run:
        return wt_path

    claimed = False
    claim_had_ready = False
    try:
        if auto_claim:
            claim_had_ready = claim_issue(root, repo, issue)
            claimed = True
            if claim_had_ready:
                print(f"Claimed issue #{issue.number} (ready -> in-progress)")
            else:
                print(f"Claimed issue #{issue.number} (set in-progress; no ready label to remove)")

        run(["git", "worktree", "add", str(wt_path), "-b", branch, start_ref], cwd=root)
        print(f"Created worktree at {wt_path}")
        ensure_uv_venv(wt_path)
    except Exception:
        if claimed:
            try:
                unclaim_issue(root, repo, issue, add_ready=claim_had_ready)
                eprint(f"Rolled back claim for issue #{issue.number}")
            except Exception as rollback_exc:  # pragma: no cover - best effort
                eprint(f"WARNING: failed to roll back claim for #{issue.number}: {rollback_exc}")
        raise

    if preflight:
        try:
            run_preflight(path=wt_path, root=root, repo=repo)
        except CliError as exc:
            eprint(f"WARNING: post-create preflight failed: {exc}")
    return wt_path


def parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def current_branch(path: Path) -> str:
    out = run(["git", "branch", "--show-current"], cwd=path).stdout.strip()
    if not out:
        raise CliError("Detached HEAD is not allowed for session work")
    return out


def resolve_current_worktree(path: Path, worktrees: list[WorktreeInfo]) -> WorktreeInfo:
    path_resolved = path.resolve()
    matches = [
        wt for wt in worktrees if path_resolved == wt.path or path_resolved.is_relative_to(wt.path)
    ]
    if not matches:
        raise CliError("Current path is not inside a registered git worktree")
    # Prefer longest path for nested matching correctness.
    matches.sort(key=lambda wt: len(str(wt.path)), reverse=True)
    return matches[0]


def run_preflight(
    *,
    path: Path,
    root: Path,
    repo: str | None = None,
    required_main_branch: str = "main",
) -> None:
    worktrees = list_worktrees(root)
    if not worktrees:
        raise CliError("No git worktrees found")
    primary = worktrees[0]
    current = resolve_current_worktree(path, worktrees)
    branch = current_branch(current.path)
    errors: list[str] = []
    warnings: list[str] = []

    enforce_lookup = parse_bool_env("ENFORCE_GH_ISSUE_LOOKUP", True)
    require_clean = parse_bool_env("REQUIRE_CLEAN_WORKTREE", False)

    if current.path == primary.path:
        if branch != required_main_branch:
            errors.append(
                f"primary worktree must stay on '{required_main_branch}', found '{branch}'"
            )
    else:
        if not WORKTREE_BRANCH_REGEX.fullmatch(branch):
            errors.append(
                f"linked worktree branch '{branch}' does not match {WORKTREE_BRANCH_REGEX.pattern}"
            )
        issue_id = extract_issue_id_from_branch(branch)
        if issue_id is None:
            errors.append(f"cannot extract issue id from branch '{branch}'")
        elif enforce_lookup:
            if repo is None:
                try:
                    repo = origin_repo_slug(root)
                except CliError as exc:
                    errors.append(str(exc))
                    repo = None
            if repo is not None:
                if not gh_available():
                    warnings.append(f"gh CLI not found; skipped issue lookup for #{issue_id}")
                else:
                    try:
                        gh_json(["api", f"repos/{repo}/issues/{issue_id}"], root=root)
                    except CliError as exc:
                        errors.append(f"gh issue lookup failed for #{issue_id}: {exc}")

    if require_clean:
        status = run(["git", "status", "--porcelain"], cwd=current.path).stdout.strip()
        if status:
            errors.append("working tree is not clean")

    print("Preflight context:")
    print(f"  repo:     {root}")
    print(f"  path:     {path.resolve()}")
    print(f"  worktree: {current.path}")
    print(f"  primary:  {primary.path}")
    print(f"  branch:   {branch}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        print("Preflight result: FAILED")
        for error in errors:
            print(f"  - {error}")
        raise CliError("Preflight failed")
    print("Preflight result: PASS")


def extract_issue_id_from_branch(branch: str) -> int | None:
    if m := WORKTREE_BRANCH_ISSUE_RE.match(branch):
        return int(m.group(1))
    return None


def list_resume_candidates(root: Path) -> list[WorktreeInfo]:
    worktrees = list_worktrees(root)
    return [wt for wt in worktrees if not wt.is_primary]


def find_linked_worktree_for_issue(root: Path, issue_number: int) -> WorktreeInfo | None:
    for wt in list_resume_candidates(root):
        if extract_issue_id_from_branch(wt.branch) == issue_number:
            return wt
    return None


def choose_next_runnable_without_existing_worktree(
    root: Path, selection: QueueSelection
) -> tuple[QueueItem, list[tuple[int, Path]]]:
    skipped: list[tuple[int, Path]] = []
    for item in selection.items:
        if not item.runnable:
            continue
        existing = find_linked_worktree_for_issue(root, item.issue.number)
        if existing is None:
            return item, skipped
        skipped.append((item.issue.number, existing.path))
    if skipped:
        skipped_text = ", ".join(f"#{num}:{path}" for num, path in skipped)
        raise CliError(
            "All runnable queue issues already have linked worktrees. "
            f"Use worktree-resume to continue them ({skipped_text})."
        )
    raise CliError(f"No runnable issues found in queue (source={selection.source_mode}).")


def select_worktree_interactive(worktrees: list[WorktreeInfo]) -> WorktreeInfo:
    if not worktrees:
        raise CliError("No linked worktrees available")
    print("Select a worktree:")
    for idx, wt in enumerate(worktrees, start=1):
        print(f"  {idx}) {wt.path} | {wt.branch}")
    print("  0) Back")
    while True:
        choice = input("Choice [1]: ").strip() or "1"
        if choice in {"0", "back"}:
            raise CliError("Back")
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(worktrees):
                return worktrees[n - 1]
        print("Invalid choice.")


def ensure_uv_venv(path: Path) -> None:
    venv_activate = path / ".venv" / "bin" / "activate"
    if venv_activate.exists():
        print(f"Python venv ready: {venv_activate.parent.parent}")
        return
    if shutil_which("uv") is None:
        eprint("WARNING: uv not found; skipping virtual environment creation")
        return
    try:
        run(["uv", "venv"], cwd=path)
        print("Created .venv with `uv venv`")
    except subprocess.CalledProcessError as exc:
        eprint(f"WARNING: failed to create .venv with uv: {exc}")


def open_shell(path: Path) -> None:
    shell = os.environ.get("SHELL") or "bash"
    ensure_uv_venv(path)
    print(f"Opening shell in {path} (with .venv activation when available)")
    path_q = shell_quote(str(path))
    shell_q = shell_quote(shell)
    cmd = (
        f"cd {path_q} && "
        "if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; "
        f"exec {shell_q} -l"
    )
    os.execvp("bash", ["bash", "-lc", cmd])


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def choose_agent_interactive(default: str = "codex") -> str:
    mapping = {
        "1": "gemini",
        "gemini": "gemini",
        "2": "claude",
        "claude": "claude",
        "3": "codex",
        "codex": "codex",
    }
    while True:
        print("Choose agent:")
        print("  1) gemini")
        print("  2) claude")
        print("  3) codex")
        print("  0) Back")
        default_choice = {"gemini": "1", "claude": "2", "codex": "3"}.get(default, "3")
        raw = input(f"Choice [{default_choice}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def choose_agent_mode_interactive(default: str = "yolo") -> str:
    mapping = {
        "1": "normal",
        "normal": "normal",
        "2": "yolo",
        "yolo": "yolo",
    }
    while True:
        print(f"Choose launch mode ({default} default):")
        print("  1) normal")
        print("  2) yolo / equivalent")
        print("  0) Back")
        raw = input(f"Choice [{'2' if default == 'yolo' else '1'}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def choose_handoff_action_interactive(default: str = "execute-now") -> str:
    mapping = {
        "1": "execute-now",
        "execute-now": "execute-now",
        "execute": "execute-now",
        "2": "print-only",
        "print-only": "print-only",
        "print": "print-only",
    }
    while True:
        print("Choose handoff behavior:")
        print("  1) execute-now")
        print("  2) print-only (open shell, do not launch agent)")
        print("  0) Back")
        raw = input(f"Choice [{'1' if default == 'execute-now' else '2'}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def choose_post_create_action_interactive() -> str:
    while True:
        print("Next action after worktree creation:")
        print("  1) Open shell with agent handoff (default)")
        print("  2) Return to menu")
        print("  0) Back")
        raw = input("Choice [1]: ").strip() or "1"
        if raw in {"1", "shell"}:
            return "shell"
        if raw in {"2", "return"}:
            return "return"
        if raw in {"0", "back"}:
            raise CliError("Back")
        print("Invalid choice.")


def worktree_issue_id(path: Path) -> int | None:
    try:
        branch = current_branch(path)
    except CliError:
        return None
    return extract_issue_id_from_branch(branch)


def fetch_issue_labels_for_prompt(root: Path, repo: str | None, issue_id: int | None) -> str:
    if repo is None or issue_id is None or not gh_available():
        return ""
    try:
        data = gh_json(["issue", "view", str(issue_id), "-R", repo, "--json", "labels"], root=root)
    except CliError:
        return ""
    if not isinstance(data, dict):
        return ""
    labels = [x["name"] for x in data.get("labels", []) if isinstance(x, dict) and "name" in x]
    return "|".join(labels)


def build_agent_prompt_for_worktree(path: Path, root: Path, repo: str | None) -> str:
    branch = run(["git", "branch", "--show-current"], cwd=path).stdout.strip() or "(detached)"
    issue_id = worktree_issue_id(path)
    issue_ref = f"#{issue_id}" if issue_id is not None else "(no issue id parsed)"
    issue_labels = fetch_issue_labels_for_prompt(root, repo, issue_id)
    labels_clause = f" Labels: {issue_labels}." if issue_labels else ""
    return (
        "Role: pragmatic, rigorous, concise coding agent. "
        f"Task: issue {issue_ref} on branch {branch} in worktree {path}.{labels_clause} "
        "First response: confirm you have read AGENTS.md and will follow it as source of truth. "
        "Read first: AGENTS.md, CLAUDE.md (if present), README.md, docs/ARCHITECTURE.md. "
        "Scope: work only in this worktree; keep changes scoped to the issue. "
        "Loop: inspect -> state plan + touched paths -> implement -> validate -> "
        "rerun checks -> continue until done. "
        "Required: run make preflight-session now and before commit/push; run "
        "make pre-validate-session before any push; include "
        "validation evidence in issue/PR. "
        "Finish: open/merge PR and close the issue only after merge verification."
    )


def build_agent_command(agent: str, mode: str, prompt: str) -> str:
    quoted = shell_quote(prompt)
    if agent == "gemini":
        return f"gemini {'--yolo ' if mode == 'yolo' else ''}{quoted}".strip()
    if agent == "claude":
        flag = "--dangerously-skip-permissions " if mode == "yolo" else ""
        return f"claude {flag}{quoted}".strip()
    if agent == "codex":
        flag = "--yolo " if mode == "yolo" else ""
        return f"codex {flag}{quoted}".strip()
    raise CliError(f"Unsupported agent '{agent}'")


def handoff_to_agent_or_shell(
    *,
    path: Path,
    root: Path,
    repo: str | None,
    agent: str | None = None,
    agent_mode: str | None = None,
    handoff: str | None = None,
    print_only_override: bool = False,
) -> None:
    ensure_uv_venv(path)
    agent_val = (agent or choose_agent_interactive()).lower()
    mode_val = (agent_mode or choose_agent_mode_interactive()).lower()
    handoff_val = (handoff or choose_handoff_action_interactive()).lower()
    if print_only_override:
        handoff_val = "print-only"

    prompt = build_agent_prompt_for_worktree(path, root, repo)
    command = build_agent_command(agent_val, mode_val, prompt)

    print()
    print(f"Opening shell in {path}")
    print("Boilerplate prompt (copy/edit if needed):")
    print(prompt)
    print(
        "Final line below is the agent launch command "
        f"({agent_val}, {mode_val}; handoff={handoff_val})."
    )
    print(command)
    sys.stdout.flush()

    if handoff_val == "execute-now":
        path_q = shell_quote(str(path))
        cmd = (
            f"cd {path_q} && "
            "if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; "
            f"{command}"
        )
        os.execvp("bash", ["bash", "-lc", cmd])

    if not sys.stdin.isatty():
        return
    open_shell(path)


def run_command_in_worktree(path: Path, command: str) -> None:
    print(f"Running in {path}: {command}")
    subprocess.run(["bash", "-lc", command], cwd=path, check=True)


def run_pre_validate(path: Path) -> None:
    print(f"Running pre-push validation in {path} (make validate-pre-push)")
    subprocess.run(["bash", "-lc", "make validate-pre-push"], cwd=path, check=True)


def gh_repo_ready(root: Path) -> tuple[bool, str | None]:
    if not gh_available():
        return False, None
    try:
        return True, origin_repo_slug(root)
    except CliError:
        return False, None


def pr_for_branch(root: Path, repo: str, branch: str, state: str) -> dict | None:
    data = gh_json(
        [
            "pr",
            "list",
            "-R",
            repo,
            "--head",
            branch,
            "--state",
            state,
            "--limit",
            "1",
            "--json",
            "number,url,title,isDraft,mergedAt",
        ],
        root=root,
    )
    if not isinstance(data, list) or not data:
        return None
    return data[0] if isinstance(data[0], dict) else None


def issue_state_info(root: Path, repo: str, issue_id: int) -> dict | None:
    data = gh_json(
        ["issue", "view", str(issue_id), "-R", repo, "--json", "state,labels,url,title"],
        root=root,
    )
    return data if isinstance(data, dict) else None


def finish_stage(root: Path, wt: WorktreeInfo, repo: str | None) -> str:
    dirty = run(["git", "status", "--porcelain"], cwd=wt.path).stdout.strip()
    if dirty:
        return "implementing"
    branch = wt.branch
    if branch and branch != "(detached)" and repo:
        open_pr = pr_for_branch(root, repo, branch, "open")
        if open_pr:
            return "review"
        merged_pr = pr_for_branch(root, repo, branch, "merged")
        if merged_pr:
            return "merged"
    try:
        upstream = run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=wt.path,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return "ready-to-push"
    if upstream:
        ab = run(
            ["git", "rev-list", "--left-right", "--count", f"{upstream}...HEAD"],
            cwd=wt.path,
        ).stdout.strip()
        if ab:
            behind, ahead = [int(x) for x in ab.split()]
            if ahead > 0:
                return "ready-to-push"
            if ahead == 0 and behind == 0:
                return "pr-open"
    return "pr-open"


def finish_summary(root: Path, *, path: Path | None = None) -> None:
    worktrees = list_worktrees(root)
    target = resolve_current_worktree(path or current_path(), worktrees)
    ready, repo = gh_repo_ready(root)
    branch = target.branch
    issue_id = extract_issue_id_from_branch(branch) if branch else None
    stage = finish_stage(root, target, repo if ready else None)
    print("Finish Worktree Summary")
    print(f"  worktree: {target.path}")
    print(f"  primary:  {worktrees[0].path}")
    print(f"  branch:   {branch}")
    print(f"  issue:    #{issue_id}" if issue_id else "  issue:    (unparsed)")
    print(f"  stage:    {stage}")
    print(f"  git:      {run(['git', 'status', '-sb'], cwd=target.path).stdout.strip()}")

    if ready and repo and issue_id:
        info = issue_state_info(root, repo, issue_id)
        if info:
            labels = "|".join(x["name"] for x in info.get("labels", []) if isinstance(x, dict))
            print(f"  issue:    {info.get('state')} - {info.get('title')}")
            print(f"  labels:   {labels}")
            print(f"  issueurl: {info.get('url')}")
        open_pr = pr_for_branch(root, repo, branch, "open")
        merged_pr = pr_for_branch(root, repo, branch, "merged")
        if open_pr:
            print(f"  pr:       #{open_pr.get('number')} OPEN - {open_pr.get('title')}")
            print(f"  prurl:    {open_pr.get('url')}")
        elif merged_pr:
            print(f"  pr:       #{merged_pr.get('number')} MERGED")
            print(f"  prurl:    {merged_pr.get('url')}")
            print(f"  mergedAt: {merged_pr.get('mergedAt')}")
        else:
            print("  pr:       none")
    else:
        if not (ready and repo):
            print("  pr:       (gh unavailable)")
        elif issue_id is None:
            print("  pr:       (not an issue worktree branch)")
        else:
            print("  pr:       (unavailable)")

    print("  policy:   pushes must run preflight + validate-pre-push")


def close_issue_done(root: Path, *, path: Path | None = None, force: bool = False) -> None:
    worktrees = list_worktrees(root)
    target = resolve_current_worktree(path or current_path(), worktrees)
    ready, repo = gh_repo_ready(root)
    if not ready or not repo:
        raise CliError("gh/GitHub repo not available")
    issue_id = extract_issue_id_from_branch(target.branch)
    if issue_id is None:
        raise CliError(f"Could not parse issue id from branch {target.branch}")
    merged_pr = pr_for_branch(root, repo, target.branch, "merged")
    if not merged_pr and not force:
        raise CliError("No merged PR found for branch; refusing to close issue without --force")
    info = issue_state_info(root, repo, issue_id)
    if info and str(info.get("state", "")).upper() == "CLOSED":
        print(f"Issue #{issue_id} already closed.")
        return
    args = ["issue", "edit", str(issue_id), "-R", repo]
    if info:
        label_names = [x["name"] for x in info.get("labels", []) if isinstance(x, dict)]
        if "review" in label_names:
            args += ["--remove-label", "review"]
        if "in-progress" in label_names:
            args += ["--remove-label", "in-progress"]
        if "done" not in label_names:
            args += ["--add-label", "done"]
        if "status:in-progress" in label_names:
            args += ["--remove-label", "status:in-progress"]
        if "status:not-started" in label_names:
            args += ["--remove-label", "status:not-started"]
        if "status:done" not in label_names:
            args += ["--add-label", "status:done"]
    gh_text(args, root=root)
    gh_text(["issue", "close", str(issue_id), "-R", repo], root=root)
    print(f"Closed issue #{issue_id}.")


def push_branch_enforced(
    root: Path,
    *,
    path: Path | None = None,
    dry_run: bool = False,
) -> None:
    worktrees = list_worktrees(root)
    target = resolve_current_worktree(path or current_path(), worktrees)
    branch = current_branch(target.path)
    if target.is_primary:
        raise CliError("Refusing to push from primary worktree via issue-worktree push command")
    if not WORKTREE_BRANCH_REGEX.fullmatch(branch):
        raise CliError(f"Branch '{branch}' is not a policy-compliant worktree branch")

    try:
        repo = origin_repo_slug(root)
    except CliError:
        repo = None
    run_preflight(path=target.path, root=root, repo=repo)
    run_pre_validate(target.path)

    push_cmd = ["git", "push", "-u", "origin", branch]
    print(f"Push command: {' '.join(push_cmd)}")
    if dry_run:
        print("Dry run: push not executed.")
        return
    subprocess.run(push_cmd, cwd=target.path, check=True)
    print("Push complete.")


def choose_issue_interactive(selection: QueueSelection) -> Issue:
    if not selection.items:
        raise CliError("Queue is empty")
    print_queue(selection)
    while True:
        raw = input("Pick queue index [1] (0=back): ").strip() or "1"
        if raw in {"0", "back"}:
            raise CliError("Back")
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(selection.items):
                return selection.items[idx - 1].issue
        print("Invalid choice.")


def cmd_issue_queue(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    selection = build_queue(
        issues,
        stream_label=args.stream_label,
        mode=args.mode,
    )
    print_queue(selection, limit=args.limit, show_blocked=not args.runnable_only)
    if args.json:
        payload = []
        items = selection.runnable if args.runnable_only else selection.items
        if args.limit is not None:
            items = items[: args.limit]
        for item in items:
            payload.append(
                {
                    "number": item.issue.number,
                    "title": item.issue.title,
                    "seq": item.issue.seq,
                    "runnable": item.runnable,
                    "blocked_reasons": item.blocked_reasons,
                    "labels": item.issue.labels,
                    "task_id": item.issue.task_id,
                }
            )
        print(
            json.dumps(
                {
                    "source_mode": selection.source_mode,
                    "source_note": selection.source_note,
                    "items": payload,
                },
                indent=2,
            )
        )
    return 0


def cmd_issues_audit(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    findings = audit_issues(issues)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    if args.json:
        print(
            json.dumps(
                {
                    "errors": [{"issue": f.issue_number, "message": f.message} for f in errors],
                    "warnings": [{"issue": f.issue_number, "message": f.message} for f in warnings],
                    "ok": len(errors) == 0,
                },
                indent=2,
            )
        )
    else:
        if errors:
            print("Issue audit: FAILED")
            for finding in errors:
                print(f"  ERROR  #{finding.issue_number}: {finding.message}")
        else:
            print("Issue audit: PASS")
        if warnings:
            for finding in warnings:
                print(f"  WARN   #{finding.issue_number}: {finding.message}")
        print(f"Summary: errors={len(errors)} warnings={len(warnings)}")

    return 1 if errors else 0


def cmd_issues_reconcile(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    task_issues = [issue for issue in issues if "type:task" in issue.labels]

    changed = 0
    for issue in task_issues:
        add_labels, remove_labels = reconcile_issue_label_changes(issue)
        if not add_labels and not remove_labels:
            continue
        changed += 1
        print(f"#{issue.number}: +{','.join(add_labels) or '-'} -{','.join(remove_labels) or '-'}")
        if args.dry_run:
            continue
        for label in add_labels:
            if label in STATUS_LABELS:
                ensure_label_exists(root, repo, label)
        edit_args = ["issue", "edit", str(issue.number), "-R", repo]
        for label in add_labels:
            edit_args += ["--add-label", label]
        for label in remove_labels:
            edit_args += ["--remove-label", label]
        gh_text(edit_args, root=root)

    print(f"Issues reconciled: {changed} issue(s) {'(dry-run)' if args.dry_run else ''}".strip())
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = None
    try:
        repo = args.repo or origin_repo_slug(root)
    except CliError:
        if parse_bool_env("ENFORCE_GH_ISSUE_LOOKUP", True):
            raise
    run_preflight(
        path=Path(args.path).resolve() if args.path else current_path(), root=root, repo=repo
    )
    return 0


def cmd_pre_validate(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve() if args.path else current_path()
    if args.dry_run:
        print(f"Would run in {target}: make validate-pre-push")
        return 0
    run_pre_validate(target)
    return 0


def cmd_worktree_next(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    selection = build_queue(issues, stream_label=args.stream_label, mode=args.mode)
    if args.choose:
        issue = choose_issue_interactive(selection)
        queue_item = next(
            (item for item in selection.items if item.issue.number == issue.number), None
        )
        if queue_item and (not queue_item.runnable) and not args.allow_blocked:
            blocked_msg = "; ".join(queue_item.blocked_reasons)
            raise CliError(f"Selected issue #{issue.number} is blocked: {blocked_msg}")
        existing_wt = find_linked_worktree_for_issue(root, issue.number)
        if existing_wt is not None:
            print(f"Issue #{issue.number} already has linked worktree: {existing_wt.path}")
            if args.open_shell and not args.dry_run:
                if not args.no_preflight:
                    run_preflight(path=existing_wt.path, root=root, repo=repo)
                handoff_to_agent_or_shell(
                    path=existing_wt.path,
                    root=root,
                    repo=repo,
                    agent=args.agent,
                    agent_mode=args.agent_mode,
                    handoff=args.handoff,
                    print_only_override=args.print_only,
                )
            return 0
    else:
        queue_item, skipped = choose_next_runnable_without_existing_worktree(root, selection)
        for issue_number, wt_path in skipped:
            print(f"Skipping issue #{issue_number}: existing linked worktree at {wt_path}")
        issue = queue_item.issue

    if (not args.allow_blocked) and queue_item and not queue_item.runnable:
        raise CliError(f"Issue #{issue.number} is blocked: {'; '.join(queue_item.blocked_reasons)}")

    base_dir = (
        Path(args.base_dir).expanduser().resolve() if args.base_dir else default_worktrees_dir(root)
    )
    auto_claim = not args.no_claim

    wt_path = create_worktree_for_issue(
        root=root,
        repo=repo,
        issue=issue,
        base_dir=base_dir,
        base_ref=args.base_ref,
        scope=args.scope,
        slug=args.slug,
        folder_name=args.name,
        auto_claim=auto_claim,
        preflight=(not args.no_preflight),
        dry_run=args.dry_run,
    )
    if args.open_shell and not args.dry_run:
        handoff_to_agent_or_shell(
            path=wt_path,
            root=root,
            repo=repo,
            agent=args.agent,
            agent_mode=args.agent_mode,
            handoff=args.handoff,
            print_only_override=args.print_only,
        )
    return 0


def cmd_worktree_create(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    issue = issue_by_number(issues, args.issue)
    existing_wt = find_linked_worktree_for_issue(root, issue.number)
    if existing_wt is not None:
        print(f"Issue #{issue.number} already has linked worktree: {existing_wt.path}")
        if args.open_shell and not args.dry_run:
            if not args.no_preflight:
                run_preflight(path=existing_wt.path, root=root, repo=repo)
            handoff_to_agent_or_shell(
                path=existing_wt.path,
                root=root,
                repo=repo,
                agent=args.agent,
                agent_mode=args.agent_mode,
                handoff=args.handoff,
                print_only_override=args.print_only,
            )
        return 0
    assert_issue_startable(issue, allow_blocked=args.allow_blocked)
    selection = build_queue(issues, stream_label=args.stream_label, mode=args.mode)
    item = next((x for x in selection.items if x.issue.number == issue.number), None)
    if item and (not item.runnable) and not args.allow_blocked:
        raise CliError(f"Issue #{issue.number} is blocked: {'; '.join(item.blocked_reasons)}")
    base_dir = (
        Path(args.base_dir).expanduser().resolve() if args.base_dir else default_worktrees_dir(root)
    )
    auto_claim = not args.no_claim

    wt_path = create_worktree_for_issue(
        root=root,
        repo=repo,
        issue=issue,
        base_dir=base_dir,
        base_ref=args.base_ref,
        scope=args.scope,
        slug=args.slug,
        folder_name=args.name,
        auto_claim=auto_claim,
        preflight=(not args.no_preflight),
        dry_run=args.dry_run,
    )
    if args.open_shell and not args.dry_run:
        handoff_to_agent_or_shell(
            path=wt_path,
            root=root,
            repo=repo,
            agent=args.agent,
            agent_mode=args.agent_mode,
            handoff=args.handoff,
            print_only_override=args.print_only,
        )
    return 0


def cmd_worktree_resume(args: argparse.Namespace) -> int:
    root = repo_root()
    worktrees = list_resume_candidates(root)
    if not worktrees:
        print("No linked worktrees found.")
        return 0
    if args.path:
        target = next(
            (wt for wt in worktrees if str(wt.path) == str(Path(args.path).resolve())), None
        )
        if target is None:
            raise CliError(f"Worktree not found: {args.path}")
    else:
        target = select_worktree_interactive(worktrees)
    if not args.no_preflight:
        try:
            repo = origin_repo_slug(root)
        except CliError:
            repo = None
        run_preflight(path=target.path, root=root, repo=repo)
    else:
        try:
            repo = origin_repo_slug(root)
        except CliError:
            repo = None
    if args.command:
        run_command_in_worktree(target.path, args.command)
    elif args.open_shell:
        agent = getattr(args, "agent", None)
        agent_mode = getattr(args, "agent_mode", None)
        handoff = getattr(args, "handoff", None)
        print_only = bool(getattr(args, "print_only", False))
        handoff_to_agent_or_shell(
            path=target.path,
            root=root,
            repo=repo,
            agent=agent,
            agent_mode=agent_mode,
            handoff=handoff,
            print_only_override=print_only,
        )
    else:
        print(target.path)
        print(f"branch={target.branch}")
    return 0


def cmd_finish_summary(args: argparse.Namespace) -> int:
    root = repo_root()
    finish_summary(root, path=Path(args.path).resolve() if args.path else None)
    return 0


def cmd_finish_close(args: argparse.Namespace) -> int:
    root = repo_root()
    close_issue_done(root, path=Path(args.path).resolve() if args.path else None, force=args.force)
    return 0


def cmd_push_branch(args: argparse.Namespace) -> int:
    root = repo_root()
    push_branch_enforced(
        root,
        path=Path(args.path).resolve() if args.path else None,
        dry_run=args.dry_run,
    )
    return 0


def cmd_agent_handoff(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        repo = args.repo or origin_repo_slug(root)
    except CliError:
        repo = None
    handoff_to_agent_or_shell(
        path=Path(args.path).resolve() if args.path else current_path(),
        root=root,
        repo=repo,
        agent=args.agent,
        agent_mode=args.agent_mode,
        handoff=args.handoff,
        print_only_override=args.print_only or args.handoff == "print-only",
    )
    return 0


def cmd_menu(args: argparse.Namespace) -> int:
    # Lightweight interactive wrapper. Keep policies in the underlying commands.
    while True:
        print()
        print("Issue Worktree Menu")
        print("  1) Show queue")
        print("  2) Create next runnable worktree")
        print("  3) Create worktree from queue (pick issue)")
        print("  4) Resume worktree (shell)")
        print("  5) Resume worktree (print path)")
        print("  6) Preflight current worktree")
        print("  7) Pre-validate current worktree (make validate-pre-push)")
        print("  8) Push current worktree branch (preflight + pre-validate enforced)")
        print("  9) Finish summary (current worktree)")
        print("  10) Close issue done (current worktree, requires merged PR)")
        print("  0) Exit")
        choice = input("Choice [1]: ").strip() or "1"
        try:
            if choice == "1":
                ns = argparse.Namespace(
                    repo=args.repo,
                    stream_label=args.stream_label,
                    mode=args.mode,
                    limit=None,
                    runnable_only=False,
                    json=False,
                )
                cmd_issue_queue(ns)
            elif choice == "2":
                post_create = choose_post_create_action_interactive()
                ns = argparse.Namespace(
                    repo=args.repo,
                    stream_label=args.stream_label,
                    mode=args.mode,
                    choose=False,
                    allow_blocked=False,
                    base_dir=args.base_dir,
                    base_ref=None,
                    scope=None,
                    slug=None,
                    name=None,
                    no_claim=False,
                    no_preflight=False,
                    dry_run=False,
                    open_shell=(post_create == "shell"),
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
                cmd_worktree_next(ns)
            elif choice == "3":
                post_create = choose_post_create_action_interactive()
                ns = argparse.Namespace(
                    repo=args.repo,
                    stream_label=args.stream_label,
                    mode=args.mode,
                    choose=True,
                    allow_blocked=False,
                    base_dir=args.base_dir,
                    base_ref=None,
                    scope=None,
                    slug=None,
                    name=None,
                    no_claim=False,
                    no_preflight=False,
                    dry_run=False,
                    open_shell=(post_create == "shell"),
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
                cmd_worktree_next(ns)
            elif choice == "4":
                ns = argparse.Namespace(
                    path=None,
                    no_preflight=False,
                    open_shell=True,
                    command=None,
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
                cmd_worktree_resume(ns)
            elif choice == "5":
                ns = argparse.Namespace(
                    path=None,
                    no_preflight=False,
                    open_shell=False,
                    command=None,
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
                cmd_worktree_resume(ns)
            elif choice == "6":
                ns = argparse.Namespace(repo=args.repo, path=None)
                cmd_preflight(ns)
            elif choice == "7":
                ns = argparse.Namespace(path=None, dry_run=False)
                cmd_pre_validate(ns)
            elif choice == "8":
                ns = argparse.Namespace(path=None, dry_run=False)
                cmd_push_branch(ns)
            elif choice == "9":
                ns = argparse.Namespace(path=None)
                cmd_finish_summary(ns)
            elif choice == "10":
                ns = argparse.Namespace(path=None, force=False)
                cmd_finish_close(ns)
            elif choice in {"0", "exit", "quit"}:
                return 0
            else:
                print("Invalid choice.")
        except CliError as exc:
            print(f"ERROR: {exc}")
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: command failed ({exc.returncode}): {' '.join(exc.cmd)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    common_repo = argparse.ArgumentParser(add_help=False)
    common_repo.add_argument(
        "--repo", help="GitHub repo slug (owner/repo). Defaults to origin remote."
    )

    queue_common = argparse.ArgumentParser(add_help=False)
    queue_common.add_argument(
        "--mode",
        choices=["auto", "ready", "open-task"],
        default="auto",
        help=(
            "Queue source: ready-labelled tasks, all open tasks, or auto fallback (default: auto)."
        ),
    )
    queue_common.add_argument(
        "--stream-label", help="Optional label filter (e.g. a, b, provider-matrix)."
    )

    q = sub.add_parser("issue-queue", parents=[common_repo, queue_common], help="Show issue queue")
    q.add_argument("--limit", type=int, help="Limit displayed items")
    q.add_argument("--runnable-only", action="store_true", help="Show only runnable items")
    q.add_argument(
        "--json", action="store_true", help="Also emit JSON payload after human-readable output"
    )
    q.set_defaults(func=cmd_issue_queue)

    aud = sub.add_parser(
        "issues-audit",
        parents=[common_repo],
        help="Audit issue lifecycle/queue invariants (objective gate)",
    )
    aud.add_argument("--json", action="store_true", help="Emit JSON output")
    aud.set_defaults(func=cmd_issues_audit)

    rec = sub.add_parser(
        "issues-reconcile",
        parents=[common_repo],
        help="Reconcile task issue labels to lifecycle rules",
    )
    rec.add_argument("--dry-run", action="store_true", help="Show changes without editing issues")
    rec.set_defaults(func=cmd_issues_reconcile)

    pf = sub.add_parser("preflight", parents=[common_repo], help="Run session preflight checks")
    pf.add_argument("--path", help="Path to check (default: current path)")
    pf.set_defaults(func=cmd_preflight)

    pv = sub.add_parser(
        "pre-validate",
        help="Run pre-push validation (make validate-pre-push; skips cdk synth)",
    )
    pv.add_argument("--path", help="Worktree path (default: current path)")
    pv.add_argument("--dry-run", action="store_true", help="Print command without running it")
    pv.set_defaults(func=cmd_pre_validate)

    wt_common = argparse.ArgumentParser(add_help=False)
    wt_common.add_argument("--base-dir", help="Linked worktree base dir (default: ../worktrees)")
    wt_common.add_argument(
        "--base-ref", help="Base ref (default: origin/main if available else main)"
    )
    wt_common.add_argument("--scope", help="Branch scope namespace (e.g. docs, infra, task)")
    wt_common.add_argument("--slug", help="Branch slug (lowercase hyphenated)")
    wt_common.add_argument("--name", help="Worktree folder name")
    wt_common.add_argument(
        "--no-claim", action="store_true", help="Do not auto-claim issue (ready -> in-progress)"
    )
    wt_common.add_argument("--no-preflight", action="store_true", help="Skip post-create preflight")
    wt_common.add_argument(
        "--dry-run", action="store_true", help="Print create plan without changes"
    )
    wt_common.add_argument(
        "--open-shell", action="store_true", help="Open a shell in the created worktree"
    )
    wt_common.add_argument(
        "--allow-blocked", action="store_true", help="Allow creating worktree for blocked issue"
    )
    wt_common.add_argument(
        "--agent",
        choices=["gemini", "claude", "codex"],
        help="Agent for open-shell handoff (otherwise prompt interactively)",
    )
    wt_common.add_argument(
        "--agent-mode",
        choices=["normal", "yolo"],
        help="Agent mode for open-shell handoff (otherwise prompt interactively)",
    )
    wt_common.add_argument(
        "--handoff",
        choices=["execute-now", "print-only"],
        help="Handoff behavior for open-shell flow (otherwise prompt interactively)",
    )
    wt_common.add_argument(
        "--print-only",
        action="store_true",
        help="Force print-only handoff when using open-shell (prints prompt/command, opens shell)",
    )

    nxt = sub.add_parser(
        "worktree-next",
        parents=[common_repo, queue_common, wt_common],
        help="Create worktree for next runnable queued issue",
    )
    nxt.add_argument(
        "--choose", action="store_true", help="Interactively choose an issue from queue"
    )
    nxt.set_defaults(func=cmd_worktree_next)

    crt = sub.add_parser(
        "worktree-create",
        parents=[common_repo, queue_common, wt_common],
        help="Create worktree for a specific issue number",
    )
    crt.add_argument("--issue", type=int, required=True, help="Issue number")
    crt.set_defaults(func=cmd_worktree_create)

    res = sub.add_parser("worktree-resume", help="Resume a linked worktree")
    res.add_argument("--path", help="Worktree path (default: choose interactively)")
    res.add_argument("--no-preflight", action="store_true", help="Skip preflight before resume")
    res.add_argument("--open-shell", action="store_true", help="Open shell in selected worktree")
    res.add_argument("--command", help="Run command in selected worktree")
    res.add_argument("--agent", choices=["gemini", "claude", "codex"])
    res.add_argument("--agent-mode", choices=["normal", "yolo"])
    res.add_argument("--handoff", choices=["execute-now", "print-only"])
    res.add_argument(
        "--print-only",
        action="store_true",
        help="Force print-only handoff when using open-shell",
    )
    res.set_defaults(func=cmd_worktree_resume)

    fs = sub.add_parser("finish-summary", help="Show finish/handoff summary for a worktree")
    fs.add_argument("--path", help="Worktree path (default: current path)")
    fs.set_defaults(func=cmd_finish_summary)

    fc = sub.add_parser("finish-close", help="Close issue for worktree after merge")
    fc.add_argument("--path", help="Worktree path (default: current path)")
    fc.add_argument(
        "--force", action="store_true", help="Close issue even without a detected merged PR"
    )
    fc.set_defaults(func=cmd_finish_close)

    pb = sub.add_parser(
        "push-branch",
        help="Push current worktree branch (preflight + validate-pre-push enforced)",
    )
    pb.add_argument("--path", help="Worktree path (default: current path)")
    pb.add_argument("--dry-run", action="store_true", help="Run checks but skip git push")
    pb.set_defaults(func=cmd_push_branch)

    ah = sub.add_parser(
        "agent-handoff",
        parents=[common_repo],
        help="Agent selection/yolo handoff for current or specified worktree path",
    )
    ah.add_argument("--path", help="Worktree path (default: current path)")
    ah.add_argument("--agent", choices=["gemini", "claude", "codex"])
    ah.add_argument("--agent-mode", choices=["normal", "yolo"])
    ah.add_argument("--handoff", choices=["execute-now", "print-only"], default="print-only")
    ah.add_argument(
        "--print-only",
        action="store_true",
        help="Force print-only handoff (recommended for testing)",
    )
    ah.set_defaults(func=cmd_agent_handoff)

    menu = sub.add_parser(
        "menu", parents=[common_repo, queue_common], help="Interactive issue worktree menu"
    )
    menu.add_argument("--base-dir", help="Linked worktree base dir (default: ../worktrees)")
    menu.set_defaults(func=cmd_menu)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CliError as exc:
        eprint(f"ERROR: {exc}")
        raise SystemExit(1)
