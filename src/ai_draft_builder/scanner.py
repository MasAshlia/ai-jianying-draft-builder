from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .errors import UserFacingError


SUPPORTED_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm"})
_DIGITS = re.compile(r"(\d+)")


def natural_sort_key(path: Path | str) -> tuple[tuple[int, object, int], ...]:
    """按数字数值排序，同时为相同数值保留稳定、可预测的补零顺序。"""
    name = path.name if isinstance(path, Path) else str(path)
    parts: list[tuple[int, object, int]] = []
    for part in _DIGITS.split(name.casefold()):
        if not part:
            continue
        if part.isdigit():
            parts.append((0, int(part), len(part)))
        else:
            parts.append((1, part, 0))
    return tuple(parts)


class MediaScanner:
    def scan(self, source_dir: Path) -> list[Path]:
        source_dir = source_dir.expanduser()
        if not source_dir.exists():
            raise UserFacingError(f"素材文件夹不存在：{source_dir}")
        if not source_dir.is_dir():
            raise UserFacingError(f"请选择素材文件夹，而不是文件：{source_dir}")

        videos = [
            item.resolve()
            for item in source_dir.iterdir()
            if item.is_file() and item.suffix.casefold() in SUPPORTED_EXTENSIONS
        ]
        videos.sort(key=natural_sort_key)
        if not videos:
            extensions = "、".join(sorted(ext.removeprefix(".") for ext in SUPPORTED_EXTENSIONS))
            raise UserFacingError(f"所选文件夹中没有支持的视频文件（{extensions}）。")
        return videos


def sorted_video_names(items: Iterable[Path]) -> list[str]:
    """测试和界面预览共用的轻量排序入口。"""
    return [item.name for item in sorted(items, key=natural_sort_key)]

