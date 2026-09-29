from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_draft_builder.errors import DraftBuildError
from ai_draft_builder.models import (
    BatchBuildRequest,
    BatchItemStatus,
    BatchProgressState,
    BuildRequest,
    ClipInfo,
    CompatibilityMode,
    COMPATIBILITY_TEST_DRAFT_NAME,
)
from ai_draft_builder.registry import DraftRegistrar
from ai_draft_builder.service import DraftBuildService
from conftest import make_clips, write_valid_draft


class ReadyEnvironment:
    def __init__(self) -> None:
        self.call_count = 0

    def ensure_ready(self, _profile) -> None:
        self.call_count += 1

    def ensure_commit_ready(self, _profile) -> None:
        pass


class FixedScanner:
    def __init__(self, clips: list[ClipInfo]) -> None:
        self.clips = clips

    def scan(self, _source_dir: Path) -> list[Path]:
        return [clip.path for clip in self.clips]


class FixedProbe:
    def __init__(self, clips: list[ClipInfo]) -> None:
        self.clips = clips

    def probe_all(self, _paths: list[Path]) -> list[ClipInfo]:
        return self.clips


class JsonAdapter:
    def build(self, staging_root, staging_name, _final_dir, _draft_name, clips, profile):
        target = staging_root / staging_name
        write_valid_draft(target, clips, profile)
        return target


class FailingAdapter:
    def build(self, staging_root, staging_name, *_args):
        (staging_root / staging_name).mkdir()
        raise DraftBuildError("模拟构建失败")


class SelectiveFailingAdapter(JsonAdapter):
    def __init__(self, failed_name: str) -> None:
        self.failed_name = failed_name

    def build(self, staging_root, staging_name, final_dir, draft_name, clips, profile):
        if draft_name == self.failed_name:
            (staging_root / staging_name).mkdir()
            raise DraftBuildError("模拟单集构建失败")
        return super().build(staging_root, staging_name, final_dir, draft_name, clips, profile)


class FailingRegistrar:
    def existing_names(self, _profile) -> set[str]:
        return set()

    def register(self, **_kwargs):
        raise DraftBuildError("模拟注册失败")


def _service(profile, clips, *, adapter=None, registrar=None) -> DraftBuildService:
    return DraftBuildService(
        profile,
        scanner=FixedScanner(clips),
        probe=FixedProbe(clips),
        adapter=adapter or JsonAdapter(),
        registrar=registrar or DraftRegistrar(),
        environment=ReadyEnvironment(),
    )


def test_two_builds_never_overwrite_and_are_both_registered(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "真实素材", 3)
    service = _service(profile, clips)
    request = BuildRequest(tmp_path / "真实素材", "第一集")

    first = service.build(request)
    second = service.build(request)

    assert first.draft_dir.name == "第一集"
    assert second.draft_dir.name == "第一集 (1)"
    assert first.draft_dir.is_dir() and second.draft_dir.is_dir()
    root_meta = json.loads((profile.draft_root / profile.root_meta_name).read_text(encoding="utf-8"))
    assert [item["draft_name"] for item in root_meta["all_draft_store"]] == ["第一集", "第一集 (1)"]
    assert first.root_meta_backup_path is not None
    assert first.root_meta_backup_path.is_file()


def test_registration_failure_preserves_installed_draft_for_review(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "素材", 1)
    service = _service(profile, clips, registrar=FailingRegistrar())
    with pytest.raises(DraftBuildError, match="模拟注册失败"):
        service.build(BuildRequest(tmp_path / "素材", "失败草稿"))
    assert (profile.draft_root / "失败草稿").is_dir()
    assert len(service.recover()) == 1
    root_meta = json.loads((profile.draft_root / profile.root_meta_name).read_text(encoding="utf-8"))
    assert root_meta["all_draft_store"] == []


def test_build_failure_cleans_only_own_transaction_folder(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "素材", 1)
    staging_root = profile.draft_root / ".ai-draft-builder-staging"
    staging_root.mkdir()
    unrelated = staging_root / "用户文件"
    unrelated.mkdir()
    service = _service(profile, clips, adapter=FailingAdapter())
    with pytest.raises(DraftBuildError, match="模拟构建失败"):
        service.build(BuildRequest(tmp_path / "素材", "失败草稿"))
    assert unrelated.is_dir()
    assert not list(staging_root.glob(".txn-*"))


def test_batch_build_is_sorted_skips_empty_and_checks_environment_once(
    profile, tmp_path: Path
) -> None:
    parent = tmp_path / "中文 短剧（测试）"
    clips = make_clips(parent / "第10集", 1)
    make_clips(parent / "第2集", 1)
    make_clips(parent / "第1集", 1)
    (parent / "空集").mkdir()
    environment = ReadyEnvironment()
    progress = []
    service = DraftBuildService(
        profile,
        scanner=FixedScanner(clips),
        probe=FixedProbe(clips),
        adapter=JsonAdapter(),
        registrar=DraftRegistrar(),
        environment=environment,
    )

    result = service.build_batch(BatchBuildRequest(parent), progress.append)

    assert [item.requested_name for item in result.items] == ["空集", "第1集", "第2集", "第10集"]
    assert [item.status for item in result.items] == [
        BatchItemStatus.SKIPPED,
        BatchItemStatus.SUCCESS,
        BatchItemStatus.SUCCESS,
        BatchItemStatus.SUCCESS,
    ]
    assert (result.succeeded_count, result.failed_count, result.skipped_count) == (3, 0, 1)
    assert result.total_clips == 3
    assert environment.call_count == 1
    assert [event.state for event in progress] == [
        BatchProgressState.SKIPPED,
        BatchProgressState.PROCESSING,
        BatchProgressState.SUCCESS,
        BatchProgressState.PROCESSING,
        BatchProgressState.SUCCESS,
        BatchProgressState.PROCESSING,
        BatchProgressState.SUCCESS,
    ]


def test_batch_failure_does_not_remove_successful_drafts(profile, tmp_path: Path) -> None:
    parent = tmp_path / "短剧"
    clips = make_clips(parent / "第1集", 2)
    make_clips(parent / "第2集", 1)
    make_clips(parent / "第3集", 1)
    service = DraftBuildService(
        profile,
        scanner=FixedScanner(clips),
        probe=FixedProbe(clips),
        adapter=SelectiveFailingAdapter("第2集"),
        registrar=DraftRegistrar(),
        environment=ReadyEnvironment(),
    )

    result = service.build_batch(BatchBuildRequest(parent))

    assert [item.status for item in result.items] == [
        BatchItemStatus.SUCCESS,
        BatchItemStatus.FAILED,
        BatchItemStatus.SUCCESS,
    ]
    assert (profile.draft_root / "第1集").is_dir()
    assert not (profile.draft_root / "第2集").exists()
    assert (profile.draft_root / "第3集").is_dir()
    root_meta = json.loads((profile.draft_root / profile.root_meta_name).read_text(encoding="utf-8"))
    assert [item["draft_name"] for item in root_meta["all_draft_store"]] == ["第1集", "第3集"]
    assert not list((profile.draft_root / ".ai-draft-builder-staging").glob(".txn-*"))


def _probe_profile(profile):
    return replace(
        profile,
        version="11.5.0.14471",
        draft_app_version="11.3.0",
        compatibility_mode=CompatibilityMode.LEGACY_IMPORT_PROBE,
    )


def _verified_legacy_profile(profile):
    return replace(
        profile,
        version="11.5.0.14471",
        draft_app_version="11.3.0",
        compatibility_mode=CompatibilityMode.VERIFIED_LEGACY_IMPORT,
    )


def test_11_5_probe_requires_fixed_name(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "素材", 1)
    build = _service(_probe_profile(profile), clips)

    with pytest.raises(DraftBuildError, match="必须精确为"):
        build.build(BuildRequest(tmp_path / "素材", "任意草稿名"))


def test_11_5_probe_rejects_batch_mode(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "素材", 1)
    build = _service(_probe_profile(profile), clips)

    with pytest.raises(DraftBuildError, match="仅允许单集"):
        build.build_batch(BatchBuildRequest(tmp_path / "素材"))


def test_11_5_probe_refuses_existing_name_without_suffix(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "素材", 1)
    probe_profile = _probe_profile(profile)
    (probe_profile.draft_root / COMPATIBILITY_TEST_DRAFT_NAME).mkdir()
    build = _service(probe_profile, clips)

    with pytest.raises(DraftBuildError, match="已存在"):
        build.build(BuildRequest(tmp_path / "素材", COMPATIBILITY_TEST_DRAFT_NAME))
    assert not (probe_profile.draft_root / f"{COMPATIBILITY_TEST_DRAFT_NAME} (1)").exists()


def test_verified_11_5_import_allows_custom_draft_name(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "E48素材", 2)
    build = _service(_verified_legacy_profile(profile), clips)

    result = build.build(BuildRequest(tmp_path / "E48素材", "E48"))

    assert result.draft_dir.name == "E48"
    assert result.root_meta_backup_path is not None
    assert result.root_meta_backup_path.is_file()


def test_verified_11_5_import_allows_batch_mode(profile, tmp_path: Path) -> None:
    parent = tmp_path / "正式批量"
    clips = make_clips(parent / "第1集", 1)
    make_clips(parent / "第2集", 1)
    build = _service(_verified_legacy_profile(profile), clips)

    result = build.build_batch(BatchBuildRequest(parent))

    assert result.succeeded_count == 2
    assert [item.requested_name for item in result.items] == ["第1集", "第2集"]

