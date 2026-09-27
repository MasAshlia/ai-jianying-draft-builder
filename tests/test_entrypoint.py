from pathlib import Path

from ai_draft_builder.gui import folder_from_selected_video, should_launch_after_batch
from ai_draft_builder.models import BatchBuildResult


def test_packaged_entrypoint_uses_absolute_import() -> None:
    entrypoint = Path(__file__).parents[1] / "src" / "ai_draft_builder" / "__main__.py"
    source = entrypoint.read_text(encoding="utf-8")
    assert "from ai_draft_builder.gui import main" in source
    assert "from .gui import main" not in source


def test_selected_video_locates_parent_folder(tmp_path: Path) -> None:
    folder = tmp_path / "第 01 集（中文）"
    folder.mkdir()
    video = folder / "镜头 001.mp4"
    video.write_bytes(b"video")
    assert folder_from_selected_video(str(video)) == folder.resolve()


def test_batch_launches_only_when_at_least_one_draft_succeeded() -> None:
    all_failed = BatchBuildResult((), 0, 2, 1, 0, 0)
    partial_success = BatchBuildResult((), 1, 1, 0, 3, 9_000_000)

    assert should_launch_after_batch(all_failed) is False
    assert should_launch_after_batch(partial_success) is True
