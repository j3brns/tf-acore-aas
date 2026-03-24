"""
Starter artifact audit for the reference echo agent.

This keeps the documented starter contract aligned with the files shipped in the
reference template. In particular, the guide expects a committed uv.lock.
"""

from pathlib import Path


def test_reference_agent_ships_expected_starter_artifacts() -> None:
    agent_root = Path(__file__).resolve().parent.parent
    required_paths = [
        agent_root / "pyproject.toml",
        agent_root / "uv.lock",
        agent_root / "handler.py",
        agent_root / "tests" / "test_handler.py",
        agent_root / "tests" / "golden" / "invoke_cases.json",
    ]

    missing = [
        path.relative_to(agent_root).as_posix() for path in required_paths if not path.exists()
    ]
    assert missing == [], f"Missing starter artifacts: {missing}"
