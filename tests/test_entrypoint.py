from pathlib import Path


def test_packaged_entrypoint_uses_absolute_import() -> None:
    entrypoint = Path(__file__).parents[1] / "src" / "ai_draft_builder" / "__main__.py"
    source = entrypoint.read_text(encoding="utf-8")
    assert "from ai_draft_builder.gui import main" in source
    assert "from .gui import main" not in source
