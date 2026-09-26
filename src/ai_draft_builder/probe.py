from __future__ import annotations

from pathlib import Path
from typing import Any

from pymediainfo import MediaInfo

from .errors import MediaProbeError
from .models import ClipInfo


def _number(value: Any, field: str) -> float:
    if value is None:
        raise ValueError(f"缺少{field}")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if not text:
        raise ValueError(f"缺少{field}")
    return float(text)


class MediaProbe:
    """使用 MediaInfo 读取视频参数；时长统一转换为整数微秒。"""

    def probe(self, path: Path) -> ClipInfo:
        try:
            if not path.is_file():
                raise ValueError("文件不存在")
            if not MediaInfo.can_parse():
                raise ValueError("MediaInfo 组件不可用")
            info = MediaInfo.parse(
                str(path), mediainfo_options={"File_TestContinuousFileNames": "0"}
            )
            if not info.video_tracks:
                raise ValueError("没有视频轨道")
            video = info.video_tracks[0]
            general = info.general_tracks[0] if info.general_tracks else None
            duration_ms = getattr(video, "duration", None)
            if duration_ms is None and general is not None:
                duration_ms = getattr(general, "duration", None)
            duration_us = int(round(_number(duration_ms, "视频时长") * 1000))
            width = int(round(_number(getattr(video, "width", None), "视频宽度")))
            height = int(round(_number(getattr(video, "height", None), "视频高度")))
            if duration_us <= 0:
                raise ValueError("视频时长为零")
            if width <= 0 or height <= 0:
                raise ValueError("视频尺寸无效")
            return ClipInfo(path=path.resolve(), duration_us=duration_us, width=width, height=height)
        except MediaProbeError:
            raise
        except Exception as exc:
            raise MediaProbeError(f"无法读取视频“{path.name}”：{exc}") from exc

    def probe_all(self, paths: list[Path]) -> list[ClipInfo]:
        return [self.probe(path) for path in paths]

