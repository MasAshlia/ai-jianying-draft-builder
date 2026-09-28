"""用公开真实素材复制成 10/30 集，在隔离目录验证批量链路，不注册用户草稿。"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    videos = MediaScanner().scan(args.media)
    if len(videos) < 2:
        raise ValueError("至少需要两个不同的真实视频")
    summaries = []
    for size in (10, 30):
        sandbox = args.output / str(size)
        sources = sandbox / "素材"
        drafts = sandbox / "草稿"
        drafts.mkdir(parents=True)
        atomic_json(drafts / "root_meta_info.json", {"all_draft_store": [], "draft_ids": 0, "keep": "preserve"})
        for index in range(1, size + 1):
            episode = sources / f"第{index:02d}集"
            episode.mkdir(parents=True)
            if index == size:
                continue  # 空集
            if index == size - 1:
                (episode / "坏视频.mp4").write_bytes(b"broken")
                continue
            count = 30 if index == 1 and size == 30 else 2
            for clip_index in range(1, count + 1):
                shutil.copyfile(videos[(index + clip_index) % len(videos)], episode / f"镜头{clip_index}.mp4")
        profile = replace(default_profile(), draft_root=drafts)
        # 仅隔离注册目录免于剪映进程检测，真实版本、媒体库与注册器仍验证。
        environment = EnvironmentChecker(process_checker=lambda: False)
        service = DraftBuildService(profile, environment=environment, state_dir=sandbox / "state")
        started = time.monotonic()
        result = service.build_batch(BatchBuildRequest(sources))
        elapsed = time.monotonic() - started
        assert (result.succeeded_count, result.failed_count, result.skipped_count) == (size - 2, 1, 1)
        for item in result.items:
            if item.status is BatchItemStatus.SUCCESS:
                paths = MediaScanner().scan(item.source_dir)
                clips = MediaProbe().probe_all(paths)
                validated = DraftValidator().validate(item.build_result.draft_dir, clips, profile)
                assert validated.duration_us == item.build_result.duration_us
        failed = tuple(item.source_dir for item in result.items if item.status is BatchItemStatus.FAILED)
        shutil.copyfile(videos[0], failed[0] / "坏视频.mp4")
        retried = service.build_batch(BatchBuildRequest(sources, failed))
        assert retried.succeeded_count == 1
        assert not list(drafts.glob("* (1)"))
        registered = json.loads((drafts / "root_meta_info.json").read_text(encoding="utf-8"))
        assert len(registered["all_draft_store"]) == size - 1 and registered["keep"] == "preserve"
        summaries.append({"episodes": size, "success": result.succeeded_count, "failed": result.failed_count,
                          "skipped": result.skipped_count, "clips": result.total_clips,
                          "elapsed_seconds": round(elapsed, 2), "retry_success": retried.succeeded_count,
                          "distinct_references_validated": True, "report_path": str(result.report_path)})
        print(json.dumps(summaries[-1], ensure_ascii=True), flush=True)
    atomic_json(args.output / "validation-result.json", {"success": True, "runs": summaries})


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
