from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import DraftBuildError
from .models import ClipInfo, JianyingProfile


@dataclass(frozen=True, slots=True)
class ValidationResult:
    draft_id: str
    duration_us: int
    clip_count: int


class DraftValidator:
    def validate(
        self,
        draft_dir: Path,
        clips: list[ClipInfo],
        profile: JianyingProfile,
    ) -> ValidationResult:
        content_path = draft_dir / profile.draft_content_name
        meta_path = draft_dir / profile.draft_meta_name
        if not content_path.is_file() or not meta_path.is_file():
            raise DraftBuildError("草稿缺少主文件或元数据文件。")

        try:
            content = json.loads(content_path.read_text(encoding="utf-8"))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DraftBuildError(f"草稿 JSON 无法读取：{exc}") from exc

        draft_id = content.get("id")
        if not isinstance(draft_id, str) or not draft_id:
            raise DraftBuildError("草稿主文件缺少有效 ID。")
        if meta.get("draft_id") != draft_id:
            raise DraftBuildError("草稿主文件和元数据的 ID 不一致。")
        if content.get("canvas_config", {}).get("width") != profile.width:
            raise DraftBuildError("草稿画布宽度不正确。")
        if content.get("canvas_config", {}).get("height") != profile.height:
            raise DraftBuildError("草稿画布高度不正确。")
        if int(content.get("fps", 0)) != profile.fps:
            raise DraftBuildError("草稿帧率不正确。")

        video_tracks = [track for track in content.get("tracks", []) if track.get("type") == "video"]
        if len(video_tracks) != 1:
            raise DraftBuildError("草稿必须且只能包含一条视频轨。")
        segments = video_tracks[0].get("segments", [])
        materials = content.get("materials", {}).get("videos", [])
        if len(segments) != len(clips) or len(materials) != len(clips):
            raise DraftBuildError("草稿中的片段数或素材数不正确。")

        material_by_id: dict[str, dict] = {}
        seen_ids: set[str] = {draft_id}
        for material in materials:
            material_id = material.get("id")
            if not isinstance(material_id, str) or not material_id:
                raise DraftBuildError("存在缺少 ID 的视频素材。")
            if material_id in seen_ids:
                raise DraftBuildError("草稿中存在重复 ID。")
            seen_ids.add(material_id)
            material_by_id[material_id] = material

        cursor = 0
        for index, (segment, expected) in enumerate(zip(segments, clips, strict=True), start=1):
            segment_id = segment.get("id")
            if not isinstance(segment_id, str) or not segment_id or segment_id in seen_ids:
                raise DraftBuildError(f"第 {index} 个片段的 ID 无效或重复。")
            seen_ids.add(segment_id)
            timerange = segment.get("target_timerange", {})
            start = timerange.get("start")
            duration = timerange.get("duration")
            if start != cursor:
                raise DraftBuildError(f"第 {index} 个片段与上一片段之间存在空隙或重叠。")
            if duration != expected.duration_us or not isinstance(duration, int) or duration <= 0:
                raise DraftBuildError(f"第 {index} 个片段时长不正确。")
            material = material_by_id.get(segment.get("material_id"))
            if material is None:
                raise DraftBuildError(f"第 {index} 个片段引用了不存在的素材。")
            actual_path = Path(str(material.get("path", ""))).resolve()
            if actual_path != expected.path.resolve() or not actual_path.is_file():
                raise DraftBuildError(f"第 {index} 个片段的素材路径无效。")
            cursor += duration

        if content.get("duration") != cursor or meta.get("tm_duration") != cursor:
            raise DraftBuildError("草稿总时长与片段总时长不一致。")
        return ValidationResult(draft_id=draft_id, duration_us=cursor, clip_count=len(clips))

