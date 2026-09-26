from pathlib import Path

import pytest

from ai_draft_builder.errors import UserFacingError
from ai_draft_builder.scanner import MediaScanner, sorted_video_names


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

