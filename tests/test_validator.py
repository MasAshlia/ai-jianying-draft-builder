import json
from pathlib import Path

import pytest

from ai_draft_builder.errors import DraftBuildError
from ai_draft_builder.validator import DraftValidator
from conftest import make_clips, write_valid_draft


def test_validator_accepts_valid_contiguous_draft(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "素材", 30)
    draft_dir = tmp_path / "draft"
    draft_id = write_valid_draft(draft_dir, clips, profile)
    result = DraftValidator().validate(draft_dir, clips, profile)
    assert result.draft_id == draft_id
    assert result.clip_count == 30
    assert result.duration_us == sum(item.duration_us for item in clips)


@pytest.mark.parametrize("offset", [-1, 1])
def test_validator_rejects_gap_or_overlap(profile, tmp_path: Path, offset: int) -> None:
    clips = make_clips(tmp_path / "素材", 2)
    draft_dir = tmp_path / "draft"
    write_valid_draft(draft_dir, clips, profile)
    path = draft_dir / profile.draft_content_name
    content = json.loads(path.read_text(encoding="utf-8"))
    content["tracks"][0]["segments"][1]["target_timerange"]["start"] += offset
    path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(DraftBuildError, match="空隙或重叠"):
        DraftValidator().validate(draft_dir, clips, profile)


def test_validator_rejects_missing_media(profile, tmp_path: Path) -> None:
    clips = make_clips(tmp_path / "素材", 1)
    draft_dir = tmp_path / "draft"
    write_valid_draft(draft_dir, clips, profile)
    clips[0].path.unlink()
    with pytest.raises(DraftBuildError, match="素材路径无效"):
        DraftValidator().validate(draft_dir, clips, profile)

