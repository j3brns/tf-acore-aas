from pathlib import Path


def test_claude_and_gemini_docs_are_in_sync():
    repo_root = Path(__file__).resolve().parents[2]
    claude = (repo_root / "CLAUDE.md").read_text(encoding="utf-8")
    gemini = (repo_root / "GEMINI.md").read_text(encoding="utf-8")
    assert claude == gemini
