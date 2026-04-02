from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


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

    @property
    def is_parent_cr(self) -> bool:
        from scripts.issue_tool.cli import CR_TITLE_RE

        return bool(CR_TITLE_RE.match(self.title.strip()))


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


@dataclass(slots=True)
class SessionPair:
    label: str
    session_name: str


@dataclass(slots=True)
class BatchLaunchResult:
    issue_number: int
    agent: str
    worktree_path: Path
    branch: str
    command: str
    state: str
    pid: int | None = None
    local_status_path: Path | None = None
    stdout_log_path: Path | None = None
    stderr_log_path: Path | None = None
    backend: str = "detached"
    session_name: str | None = None
    window_name: str | None = None
    detail: str = ""
