from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_draft_builder.errors import DraftBuildError
from ai_draft_builder.models import BuildRequest, ClipInfo
from ai_draft_builder.registry import DraftRegistrar
from ai_draft_builder.service import DraftBuildService
from conftest import make_clips, write_valid_draft


class ReadyEnvironment:
    def ensure_ready(self, _profile) -> None:
        return None


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


def test_registration_failure_removes_installed_half_draft(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "素材", 1)
    service = _service(profile, clips, registrar=FailingRegistrar())
    with pytest.raises(DraftBuildError, match="模拟注册失败"):
        service.build(BuildRequest(tmp_path / "素材", "失败草稿"))
    assert not (profile.draft_root / "失败草稿").exists()
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

