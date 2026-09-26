from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ai_draft_builder.models import ClipInfo, JianyingProfile


@pytest.fixture
def profile(tmp_path: Path) -> JianyingProfile:
    exe = tmp_path / "JianyingPro" / "Apps" / "11.3.0.14362" / "JianyingPro.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"exe")
    root = tmp_path / "草稿 root"
    root.mkdir()
    (root / "root_meta_info.json").write_text(
        json.dumps({"all_draft_store": [], "draft_ids": 0, "root_path": root.as_posix()}),
        encoding="utf-8",
    )
    return JianyingProfile("11.3.0.14362", exe, root)


def make_clips(folder: Path, count: int = 3) -> list[ClipInfo]:
    folder.mkdir(parents=True, exist_ok=True)
    clips: list[ClipInfo] = []
    for index in range(1, count + 1):
        path = folder / f"镜头 {index}.mp4"
        path.write_bytes(b"video")
        clips.append(ClipInfo(path.resolve(), 1_000_000 + index, 1080, 1920))
    return clips


def write_valid_draft(draft_dir: Path, clips: list[ClipInfo], profile: JianyingProfile) -> str:
    draft_dir.mkdir(parents=True, exist_ok=False)
    draft_id = str(uuid.uuid4()).upper()
    videos = []
    segments = []
    cursor = 0
    for clip in clips:
        material_id = uuid.uuid4().hex
        videos.append(
            {
                "id": material_id,
                "material_id": material_id,
                "path": str(clip.path),
                "duration": clip.duration_us,
                "width": clip.width,
                "height": clip.height,
            }
        )
        segments.append(
            {
                "id": uuid.uuid4().hex,
                "material_id": material_id,
                "target_timerange": {"start": cursor, "duration": clip.duration_us},
            }
        )
        cursor += clip.duration_us
    content = {
        "id": draft_id,
        "duration": cursor,
        "fps": 30.0,
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "original"},
        "materials": {"videos": videos},
        "tracks": [{"id": uuid.uuid4().hex, "type": "video", "segments": segments}],
    }
    meta = {"draft_id": draft_id, "tm_duration": cursor}
    (draft_dir / profile.draft_content_name).write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8"
    )
    (draft_dir / profile.draft_meta_name).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return draft_id

