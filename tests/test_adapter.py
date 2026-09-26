from __future__ import annotations

import json
import uuid
from pathlib import Path

import ai_draft_builder.adapter as adapter_module
from ai_draft_builder.adapter import JianyingAdapter
from ai_draft_builder.models import ClipInfo
from ai_draft_builder.validator import DraftValidator
from pyJianYingDraft import CropSettings, VideoMaterial as RealVideoMaterial


class FakeVideoMaterial(RealVideoMaterial):
    def __init__(self, path: str, material_name=None, crop_settings=CropSettings()):
        source = Path(path)
        self.material_name = material_name or source.name
        self.material_id = uuid.uuid4().hex
        self.local_material_id = ""
        self.path = str(source.resolve())
        self.crop_settings = crop_settings
        self.material_type = "video"
        self.duration = 1
        self.width = 1
        self.height = 1


def test_adapter_generates_unique_ids_and_valid_draft(monkeypatch, profile, tmp_path: Path) -> None:
    monkeypatch.setattr(adapter_module, "VideoMaterial", FakeVideoMaterial)
    source = tmp_path / "中文 素材"
    source.mkdir()
    clips = []
    for index in range(1, 4):
        path = source / f"镜头{index}.mp4"
        path.write_bytes(b"x")
        clips.append(ClipInfo(path.resolve(), 1_000_000 + index, 1080, 1920))

    staging = tmp_path / "staging"
    staging.mkdir()
    adapter = JianyingAdapter()
    first = adapter.build(staging, ".txn-first", profile.draft_root / "第一集", "第一集", clips, profile)
    second = adapter.build(staging, ".txn-second", profile.draft_root / "第二集", "第二集", clips, profile)

    first_result = DraftValidator().validate(first, clips, profile)
    second_result = DraftValidator().validate(second, clips, profile)
    assert first_result.draft_id != second_result.draft_id
    content = json.loads((first / profile.draft_content_name).read_text(encoding="utf-8"))
    assert [segment["target_timerange"]["start"] for segment in content["tracks"][0]["segments"]] == [
        0,
        clips[0].duration_us,
        clips[0].duration_us + clips[1].duration_us,
    ]
    assert content["platform"]["app_version"] == "11.3.0"

