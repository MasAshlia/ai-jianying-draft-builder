from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_draft_builder.probe as probe_module
from ai_draft_builder.errors import MediaProbeError
from ai_draft_builder.probe import MediaProbe


class FakeMediaInfo:
    can_parse_value = True
    parsed = SimpleNamespace(video_tracks=[], general_tracks=[])

    @classmethod
    def can_parse(cls) -> bool:
        return cls.can_parse_value

    @classmethod
    def parse(cls, *_args, **_kwargs):
        return cls.parsed


def test_probe_returns_integer_microseconds(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "中文 视频.mp4"
    video.write_bytes(b"x")
    FakeMediaInfo.parsed = SimpleNamespace(
        video_tracks=[SimpleNamespace(duration="1234.567", width="1080", height=1920)],
        general_tracks=[],
    )
    monkeypatch.setattr(probe_module, "MediaInfo", FakeMediaInfo)
    clip = MediaProbe().probe(video)
    assert clip.duration_us == 1_234_567
    assert (clip.width, clip.height) == (1080, 1920)


@pytest.mark.parametrize(
    "parsed, message",
    [
        (SimpleNamespace(video_tracks=[], general_tracks=[]), "没有视频轨道"),
        (
            SimpleNamespace(
                video_tracks=[SimpleNamespace(duration=0, width=1080, height=1920)],
                general_tracks=[],
            ),
            "时长为零",
        ),
    ],
)
def test_probe_rejects_bad_video(monkeypatch, tmp_path: Path, parsed, message: str) -> None:
    video = tmp_path / "损坏.mp4"
    video.write_bytes(b"bad")
    FakeMediaInfo.parsed = parsed
    monkeypatch.setattr(probe_module, "MediaInfo", FakeMediaInfo)
    with pytest.raises(MediaProbeError, match=message):
        MediaProbe().probe(video)

