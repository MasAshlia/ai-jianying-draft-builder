from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from pyJianYingDraft import (
    DraftFolder,
    Timerange,
    TrackSpec,
    TrackType,
    VideoMaterial,
    VideoSegment,
)

from .errors import DraftBuildError
from .models import ClipInfo, JianyingProfile
from .timeline import TimelineBuilder


class JianyingAdapter:
    """项目中唯一直接调用 pyJianYingDraft 的适配层。"""

    def __init__(self, timeline_builder: TimelineBuilder | None = None) -> None:
        self.timeline_builder = timeline_builder or TimelineBuilder()

    def build(
        self,
        staging_root: Path,
        staging_name: str,
        final_dir: Path,
        draft_name: str,
        clips: list[ClipInfo],
        profile: JianyingProfile,
        progress_callback=None,
    ) -> Path:
        timeline = self.timeline_builder.build(clips)
        draft_id = str(uuid.uuid4()).upper()
        now_us = time.time_ns() // 1_000

        try:
            folder = DraftFolder(str(staging_root))
            script = folder.create_draft(
                staging_name,
                profile.width,
                profile.height,
                profile.fps,
                maintrack_adsorb=True,
                allow_replace=False,
            )
            script.content["id"] = draft_id
            script.content["create_time"] = now_us
            script.content["update_time"] = now_us
            for key in ("platform", "last_modified_platform"):
                platform = script.content.setdefault(key, {})
                platform["app_id"] = 3704
                platform["app_source"] = "lv"
                platform["app_version"] = profile.draft_app_version
                platform["os"] = "windows"

            main_track = script.append_track(TrackSpec(TrackType.video, "主视频轨"))
            for item in timeline:
                if progress_callback:
                    progress_callback("构建", item.clip.path.name)
                material = VideoMaterial(str(item.clip.path))
                if material.material_type != "video":
                    raise ValueError(f"素材“{item.clip.path.name}”不是视频")
                # 探测阶段的整数微秒是全链路唯一时长来源。
                material.duration = item.duration_us
                material.width = item.clip.width
                material.height = item.clip.height
                segment = VideoSegment(
                    material,
                    Timerange(item.start_us, item.duration_us),
                    source_timerange=Timerange(0, item.duration_us),
                )
                script.add_segment(segment, main_track)
            script.save()

            draft_dir = staging_root / staging_name
            self._patch_meta(
                draft_dir=draft_dir,
                final_dir=final_dir,
                draft_name=draft_name,
                draft_id=draft_id,
                duration_us=timeline[-1].end_us,
                now_us=now_us,
                profile=profile,
            )
            return draft_dir
        except DraftBuildError:
            raise
        except Exception as exc:
            raise DraftBuildError(f"生成草稿文件失败：{exc}") from exc

    @staticmethod
    def _patch_meta(
        *,
        draft_dir: Path,
        final_dir: Path,
        draft_name: str,
        draft_id: str,
        duration_us: int,
        now_us: int,
        profile: JianyingProfile,
    ) -> None:
        meta_path = draft_dir / profile.draft_meta_name
        with meta_path.open("r", encoding="utf-8") as handle:
            meta = json.load(handle)
        meta.update(
            {
                "draft_id": draft_id,
                "draft_name": draft_name,
                "draft_fold_path": final_dir.as_posix(),
                "draft_root_path": profile.draft_root.as_posix(),
                "tm_duration": duration_us,
                "tm_draft_create": now_us,
                "tm_draft_modified": now_us,
            }
        )
        with meta_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(meta, handle, ensure_ascii=False, separators=(",", ":"))

