from pathlib import Path

import pytest

from ai_draft_builder.errors import UserFacingError
from ai_draft_builder.scanner import EpisodeBatchScanner, MediaScanner, sorted_video_names


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["10.mp4", "2.mp4", "1.mp4"], ["1.mp4", "2.mp4", "10.mp4"]),
        (["003.mp4", "001.mp4", "002.mp4"], ["001.mp4", "002.mp4", "003.mp4"]),
        (["镜头10.mp4", "镜头2.mp4", "镜头1.mp4"], ["镜头1.mp4", "镜头2.mp4", "镜头10.mp4"]),
    ],
)
def test_natural_sort(names: list[str], expected: list[str]) -> None:
    assert sorted_video_names([Path(name) for name in names]) == expected


def test_scanner_handles_chinese_path_and_ignores_unsupported(tmp_path: Path) -> None:
    folder = tmp_path / "第一集（测试 空格）"
    folder.mkdir()
    for name in ["镜头10.MP4", "镜头2.mov", "说明.txt", "封面.jpg"]:
        (folder / name).write_bytes(b"x")
    nested = folder / "子目录"
    nested.mkdir()
    (nested / "镜头1.mp4").write_bytes(b"x")

    result = MediaScanner().scan(folder)
    assert [item.name for item in result] == ["镜头2.mov", "镜头10.MP4"]


def test_empty_folder_has_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(UserFacingError, match="没有支持的视频"):
        MediaScanner().scan(tmp_path)


def test_batch_scanner_finds_only_direct_children_in_natural_order(tmp_path: Path) -> None:
    parent = tmp_path / "短剧"
    parent.mkdir()
    for name, video_count in [("第10集", 1), ("第2集", 2), ("第1集", 1), ("空集", 0)]:
        episode = parent / name
        episode.mkdir()
        for index in range(video_count):
            (episode / f"镜头{index + 1}.mp4").write_bytes(b"video")
    nested = parent / "第1集" / "不应识别为分集"
    nested.mkdir()
    (nested / "镜头.mp4").write_bytes(b"video")
    hidden = parent / ".ai-draft-builder-staging"
    hidden.mkdir()
    (parent / "说明.txt").write_text("说明", encoding="utf-8")

    episodes = EpisodeBatchScanner().discover(parent)

    assert [item.source_dir.name for item in episodes] == ["空集", "第1集", "第2集", "第10集"]
    assert [item.video_count for item in episodes] == [0, 1, 2, 1]


def test_batch_scanner_requires_child_folders(tmp_path: Path) -> None:
    (tmp_path / "总目录视频.mp4").write_bytes(b"video")
    with pytest.raises(UserFacingError, match="没有可用的分集子文件夹"):
        EpisodeBatchScanner().discover(tmp_path)

