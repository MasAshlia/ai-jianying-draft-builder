from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from .errors import DraftBuildError, FatalBuildError
from .models import JianyingProfile
from .validator import ValidationResult


def _entry(
    *,
    draft_dir: Path,
    draft_name: str,
    validation: ValidationResult,
    profile: JianyingProfile,
    now_us: int,
) -> dict:
    root = profile.draft_root.as_posix()
    folder = draft_dir.as_posix()
    return {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": f"{folder}/draft_cover.jpg",
        "draft_fold_path": folder,
        "draft_id": validation.draft_id,
        "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_infinite_canvas_draft": False,
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_json_file": f"{folder}/{profile.draft_content_name}",
        "draft_name": draft_name,
        "draft_new_version": "",
        "draft_root_path": root,
        "draft_timeline_materials_size": 0,
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "pippit_avatar_url": "",
        "pippit_extra_info": "",
        "pippit_id": "",
        "pippit_user_name": "",
        "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": validation.duration_us,
    }


class DraftRegistrar:
    def __init__(self, backup_dir: Path | None = None) -> None:
        self.backup_dir = backup_dir

    def existing_names(self, profile: JianyingProfile) -> set[str]:
        data = self._read(profile)
        return {
            str(item.get("draft_name"))
            for item in data.get("all_draft_store", [])
            if item.get("draft_name")
        }

    def register(
        self,
        *,
        draft_dir: Path,
        draft_name: str,
        validation: ValidationResult,
        profile: JianyingProfile,
        before_replace=None,
        expected_original: bytes | None = None,
    ) -> Path:
        root_meta = profile.draft_root / profile.root_meta_name
        original = root_meta.read_bytes()
        if expected_original is not None and original != expected_original:
            raise FatalBuildError("剪映草稿索引已被其他程序修改，已停止后续写入。")
        data = self._decode(original)

        stores = data.get("all_draft_store")
        if not isinstance(stores, list):
            raise FatalBuildError("剪映草稿索引缺少 all_draft_store，已停止写入。")
        if any(item.get("draft_id") == validation.draft_id for item in stores):
            raise DraftBuildError("草稿 ID 已存在，已停止写入。")
        if profile.is_legacy_import_probe and any(
            item.get("draft_name") == draft_name for item in stores
        ):
            raise DraftBuildError("兼容性测试草稿已注册，已停止写入。")

        now_us = time.time_ns() // 1_000
        stores.append(
            _entry(
                draft_dir=draft_dir,
                draft_name=draft_name,
                validation=validation,
                profile=profile,
                now_us=now_us,
            )
        )
        data["draft_ids"] = len(stores)
        data.setdefault("root_path", profile.draft_root.as_posix())

        backup_dir = self.backup_dir or (profile.draft_root.parent / ".ai-draft-builder-backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"root_meta_info.{now_us}.bak"
        with backup_path.open("xb") as backup:
            backup.write(original)
            backup.flush()
            os.fsync(backup.fileno())

        temp_path = root_meta.with_name(f".{root_meta.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            if before_replace:
                before_replace()
            if root_meta.read_bytes() != original:
                raise FatalBuildError("剪映草稿索引已被其他程序修改，已停止后续写入。")
            os.replace(temp_path, root_meta)
            return backup_path
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            if isinstance(exc, FatalBuildError):
                raise
            raise FatalBuildError(f"更新剪映草稿列表失败，原索引未被覆盖：{exc}") from exc

    @staticmethod
    def _decode(raw: bytes) -> dict:
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("all_draft_store"), list):
                raise ValueError("缺少草稿列表")
            if any(not isinstance(item, dict) for item in data["all_draft_store"]):
                raise ValueError("草稿条目格式无效")
            return data
        except (UnicodeDecodeError, ValueError) as exc:
            raise FatalBuildError("剪映草稿索引格式无效，已停止写入。") from exc

    @staticmethod
    def _read(profile: JianyingProfile) -> dict:
        path = profile.draft_root / profile.root_meta_name
        try:
            return DraftRegistrar._decode(path.read_bytes())
        except FileNotFoundError as exc:
            raise FatalBuildError(f"找不到剪映草稿索引：{path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise FatalBuildError(f"无法读取剪映草稿索引：{exc}") from exc
