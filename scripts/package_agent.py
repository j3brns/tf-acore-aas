"""
package_agent.py — Package agent code for deployment.

Zips agent source code excluding: __pycache__, .venv, tests/, *.pyc, .git.
Output: .build/{agent_name}-code.zip

Usage:
    uv run python scripts/package_agent.py <agent_name>

Implemented in TASK-035.
ADRs: ADR-005, ADR-008
"""
