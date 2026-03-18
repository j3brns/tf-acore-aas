from pathlib import Path


def test_agent_docs_point_to_claude():
    repo_root = Path(__file__).resolve().parents[2]
    agents = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    gemini = (repo_root / "GEMINI.md").read_text(encoding="utf-8")
    claude = (repo_root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Read [CLAUDE.md](CLAUDE.md)" in agents
    assert "Read [CLAUDE.md](CLAUDE.md)" in gemini
    assert ".gitnexus/ai-context.md" in agents
    assert ".gitnexus/ai-context.md" in gemini
    assert ".gitnexus/ai-context.md" in claude
    assert agents == gemini
    assert agents != claude


def test_ai_context_directory_is_retired():
    repo_root = Path(__file__).resolve().parents[2]
    assert not (repo_root / "ai-context").exists()
