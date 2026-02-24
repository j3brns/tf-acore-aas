"""
plan_dev.py — Generate a structured implementation plan for a task.

Reads TASKS.md, ARCHITECTURE.md, and relevant ADRs, then outputs a
step-by-step plan for the given task description.

Usage:
    uv run python scripts/plan_dev.py "Implement the billing metering pipeline"

Called by: make plan-dev TASK="..."

Implemented in TASK-019.
"""
