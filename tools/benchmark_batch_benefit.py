"""用一集真实素材构造隔离的 10 集任务，衡量批量生成收益。"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_draft_builder.environment import EnvironmentChecker, default_profile
from ai_draft_builder.models import BatchBuildRequest, BatchItemStatus
from ai_draft_builder.probe import MediaProbe
from ai_draft_builder.runtime import atomic_json
from ai_draft_builder.scanner import MediaScanner
from ai_draft_builder.service import DraftBuildService
from ai_draft_builder.validator import DraftValidator


EPISODES = 10
NORMAL_EPISODES = 8
MANUAL_SECONDS_PER_EPISODE = (30, 60)
ACTIVE_SAVING_TARGET = 0.70


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _validate_timeline(draft: Path, clips, profile) -> None:
    validated = DraftValidator().validate(draft, clips, profile)
    content = json.loads((draft / profile.draft_content_name).read_text(encoding="utf-8"))
    segments = next(track for track in content["tracks"] if track["type"] == "video")["segments"]
    cursor = 0
    for segment, clip in zip(segments, clips, strict=True):
        source = segment.get("source_timerange", {})
        target = segment.get("target_timerange", {})
        if source.get("start") != 0 or source.get("duration") != clip.duration_us:
            raise AssertionError(f"素材未保持完整时长：{clip.path.name}")
        if target.get("start") != cursor or target.get("duration") != clip.duration_us:
            raise AssertionError(f"时间线不连续：{clip.path.name}")
        cursor += clip.duration_us
    if validated.duration_us != cursor:
        raise AssertionError("草稿总时长与片段时长总和不一致")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    videos = MediaScanner().scan(args.media)
    if not 10 <= len(videos) <= 20:
        raise ValueError("代表性素材必须包含 10～20 个视频片段")
    args.output.mkdir(parents=True, exist_ok=False)
    sources = args.output / "素材"
    drafts = args.output / "草稿"
    state = args.output / "state"
    drafts.mkdir(parents=True)
    atomic_json(
        drafts / "root_meta_info.json",
        {"all_draft_store": [], "draft_ids": 0, "root_path": drafts.as_posix(), "keep": "preserve"},
    )

    for episode_index in range(1, EPISODES + 1):
        episode = sources / f"第{episode_index:02d}集"
        episode.mkdir(parents=True)
        if episode_index <= NORMAL_EPISODES:
            for clip_index, video in enumerate(videos, start=1):
                _link_or_copy(video, episode / f"{clip_index:03d}{video.suffix.casefold()}")
        elif episode_index == NORMAL_EPISODES + 1:
            (episode / "001.mp4").write_bytes(b"intentionally broken benchmark video")
        # 最后一集保持为空目录。

    profile = replace(default_profile(), draft_root=drafts)
    environment = EnvironmentChecker(process_checker=lambda: False)
    service = DraftBuildService(profile, environment=environment, state_dir=state)

    started = time.monotonic()
    result = service.build_batch(BatchBuildRequest(sources))
    batch_wall_seconds = time.monotonic() - started
    statuses = [item.status for item in result.items]
    expected = (
        [BatchItemStatus.SUCCESS] * NORMAL_EPISODES
        + [BatchItemStatus.FAILED, BatchItemStatus.SKIPPED]
    )
    if statuses != expected:
        raise AssertionError(f"批量结果不符合预期：{statuses}")

    validated_drafts = []
    for item in result.items:
        if item.status is not BatchItemStatus.SUCCESS or item.build_result is None:
            continue
        paths = MediaScanner().scan(item.source_dir)
        clips = MediaProbe().probe_all(paths)
        _validate_timeline(item.build_result.draft_dir, clips, profile)
        validated_drafts.append(item.build_result.draft_dir.name)

    failed_dir = result.items[NORMAL_EPISODES].source_dir
    broken = failed_dir / "001.mp4"
    broken.unlink()
    _link_or_copy(videos[0], broken)
    retry_started = time.monotonic()
    retry = service.build_batch(BatchBuildRequest(sources, (failed_dir,)))
    retry_wall_seconds = time.monotonic() - retry_started
    if retry.succeeded_count != 1:
        raise AssertionError("修复损坏素材后，失败集没有成功重试")
    if list(drafts.glob("第0[1-8]集 (*)")):
        raise AssertionError("重试失败集时生成了正常集副本")

    registry = json.loads((drafts / profile.root_meta_name).read_text(encoding="utf-8"))
    names = [entry["draft_name"] for entry in registry["all_draft_store"]]
    if len(names) != NORMAL_EPISODES + 1 or len(names) != len(set(names)):
        raise AssertionError("根索引注册数量或名称唯一性不正确")
    if registry.get("keep") != "preserve":
        raise AssertionError("根索引未知字段未被保留")

    task = json.loads(result.report_path.read_text(encoding="utf-8"))
    episode_times = [
        item["elapsed_seconds"]
        for item in task["items"]
        if item.get("status") == "success" and item.get("elapsed_seconds") is not None
    ]
    manual_low = EPISODES * MANUAL_SECONDS_PER_EPISODE[0]
    manual_high = EPISODES * MANUAL_SECONDS_PER_EPISODE[1]
    maximum_active_seconds = manual_low * (1 - ACTIVE_SAVING_TARGET)
    report = {
        "success": True,
        "profile": {
            "jianying_version": profile.version,
            "draft_app_version": profile.draft_app_version,
            "compatibility_mode": profile.compatibility_mode.value,
        },
        "dataset": {
            "episodes": EPISODES,
            "normal": NORMAL_EPISODES,
            "broken": 1,
            "empty": 1,
            "clips_per_normal_episode": len(videos),
            "total_normal_clips": NORMAL_EPISODES * len(videos),
        },
        "batch": {
            "wall_seconds": round(batch_wall_seconds, 3),
            "successful_episode_seconds": episode_times,
            "average_successful_episode_seconds": round(sum(episode_times) / len(episode_times), 3),
            "retry_wall_seconds": round(retry_wall_seconds, 3),
            "retry_success": retry.succeeded_count,
            "validated_drafts": validated_drafts,
            "registered_drafts_after_retry": len(names),
            "duplicate_success_drafts": False,
            "interventions": 1,
        },
        "comparison": {
            "declared_manual_seconds_per_episode": list(MANUAL_SECONDS_PER_EPISODE),
            "estimated_manual_seconds_for_10_episodes": [manual_low, manual_high],
            "wall_time_not_slower_than_manual": batch_wall_seconds <= manual_low,
            "active_saving_target_percent": int(ACTIVE_SAVING_TARGET * 100),
            "maximum_batch_active_seconds_to_pass": round(maximum_active_seconds, 3),
            "manual_three_episode_stopwatch": "pending_user_measurement",
            "batch_active_stopwatch": "pending_user_measurement",
        },
        "integrity": {
            "natural_order": True,
            "full_source_ranges": True,
            "continuous_target_ranges": True,
            "all_normal_drafts_valid": True,
            "failed_episode_retry_only": True,
            "root_unknown_fields_preserved": True,
        },
    }
    atomic_json(args.output / "benchmark-result.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    main()
