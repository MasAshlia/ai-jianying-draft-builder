from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_draft_builder.registry as registry_module
from ai_draft_builder.errors import DraftBuildError
from ai_draft_builder.registry import DraftRegistrar
from ai_draft_builder.validator import DraftValidator
from conftest import make_clips, write_valid_draft


def test_registration_preserves_old_entries_and_unknown_fields(profile, tmp_path: Path) -> None:
    root_meta = profile.draft_root / profile.root_meta_name
    original = {
        "all_draft_store": [{"draft_id": "old", "draft_name": "旧草稿", "future_field": 7}],
        "draft_ids": 1,
        "root_path": profile.draft_root.as_posix(),
        "unknown_top_level": {"keep": True},
    }
    root_meta.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    clips = make_clips(tmp_path / "素材", 2)
    draft_dir = profile.draft_root / "新草稿"
    write_valid_draft(draft_dir, clips, profile)
    validation = DraftValidator().validate(draft_dir, clips, profile)

    backup_dir = tmp_path / "backups"
    backup = DraftRegistrar(backup_dir).register(
        draft_dir=draft_dir,
        draft_name="新草稿",
        validation=validation,
        profile=profile,
    )

    updated = json.loads(root_meta.read_text(encoding="utf-8"))
    assert updated["unknown_top_level"] == {"keep": True}
    assert updated["all_draft_store"][0] == original["all_draft_store"][0]
    assert updated["draft_ids"] == 2
    assert updated["all_draft_store"][1]["draft_json_file"].endswith("/draft_content.json")
    assert updated["all_draft_store"][1]["draft_id"] == validation.draft_id
    assert backup.read_text(encoding="utf-8") == json.dumps(original, ensure_ascii=False)


def test_atomic_registration_failure_leaves_original_index(monkeypatch, profile, tmp_path: Path) -> None:
    root_meta = profile.draft_root / profile.root_meta_name
    original = root_meta.read_bytes()
    clips = make_clips(tmp_path / "素材", 1)
    draft_dir = profile.draft_root / "草稿"
    write_valid_draft(draft_dir, clips, profile)
    validation = DraftValidator().validate(draft_dir, clips, profile)

    def fail_replace(_src, _dst):
        raise OSError("simulated")

    monkeypatch.setattr(registry_module.os, "replace", fail_replace)
    with pytest.raises(DraftBuildError, match="原索引未被覆盖"):
        DraftRegistrar(tmp_path / "backups").register(
            draft_dir=draft_dir,
            draft_name="草稿",
            validation=validation,
            profile=profile,
        )
    assert root_meta.read_bytes() == original
    assert not list(profile.draft_root.glob("*.tmp"))

