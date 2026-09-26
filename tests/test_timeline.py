from pathlib import Path

from ai_draft_builder.models import ClipInfo
from ai_draft_builder.timeline import TimelineBuilder


def test_thirty_clips_are_exactly_contiguous() -> None:
    clips = [ClipInfo(Path(f"{i}.mp4"), 1_000_000 + i, 1080, 1920) for i in range(30)]
    timeline = TimelineBuilder().build(clips)
    assert len(timeline) == 30
    assert timeline[0].start_us == 0
    for previous, current in zip(timeline, timeline[1:]):
        assert current.start_us == previous.end_us
    assert timeline[-1].end_us == sum(clip.duration_us for clip in clips)
