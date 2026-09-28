"""发布包自测：只写新建的指定输出目录，不访问或注册用户剪映草稿。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import traceback

from .environment import default_profile
from .preparation import ProcessPreparer, verify_sources
from .runtime import atomic_json
from .scanner import MediaScanner
from .validator import DraftValidator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = args.output / "smoke-result.json"
    try:
        profile = default_profile()
        paths = MediaScanner().scan(args.smoke_test)[:2]
        target, clips, snapshots = ProcessPreparer().prepare(
            paths, args.output, "草稿自测", args.output / "草稿自测", "草稿自测",
            profile, lambda *_: None)
        verify_sources(snapshots)
        validation = DraftValidator().validate(target, clips, profile)
        content = json.loads((target / profile.draft_content_name).read_text(encoding="utf-8"))
        atomic_json(report, {"success": True, "clip_count": validation.clip_count,
                             "duration_us": validation.duration_us,
                             "paths": [v["path"] for v in content["materials"]["videos"]]})
    except Exception:
        atomic_json(report, {"success": False, "error": traceback.format_exc()})
        raise
