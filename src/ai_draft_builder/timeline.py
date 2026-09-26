from __future__ import annotations

from dataclasses import dataclass

from .errors import DraftBuildError
from .models import ClipInfo


@dataclass(frozen=True, slots=True)
class TimelineClip:
    clip: ClipInfo
    start_us: int
    duration_us: int

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


class TimelineBuilder:
    def build(self, clips: list[ClipInfo]) -> list[TimelineClip]:
        if not clips:
            raise DraftBuildError("没有可加入时间线的视频片段。")
        cursor = 0
        result: list[TimelineClip] = []
        for clip in clips:
            if clip.duration_us <= 0:
                raise DraftBuildError(f"视频“{clip.path.name}”的时长无效。")
            item = TimelineClip(clip=clip, start_us=cursor, duration_us=clip.duration_us)
            result.append(item)
            cursor = item.end_us
        return result

