from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BuildRequest:
    source_dir: Path
    draft_name: str


@dataclass(frozen=True, slots=True)
class ClipInfo:
    path: Path
    duration_us: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class BuildResult:
    draft_dir: Path
    clip_count: int
    duration_us: int


@dataclass(frozen=True, slots=True)
class EpisodeFolder:
    source_dir: Path
    video_count: int


@dataclass(frozen=True, slots=True)
class BatchBuildRequest:
    parent_dir: Path


class BatchItemStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class BatchProgressState(Enum):
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class BatchProgress:
    current: int
    total: int
    source_dir: Path
    state: BatchProgressState
    message: str


@dataclass(frozen=True, slots=True)
class BatchItemResult:
    source_dir: Path
    requested_name: str
    status: BatchItemStatus
    build_result: BuildResult | None
    message: str


@dataclass(frozen=True, slots=True)
class BatchBuildResult:
    items: tuple[BatchItemResult, ...]
    succeeded_count: int
    failed_count: int
    skipped_count: int
    total_clips: int
    total_duration_us: int


@dataclass(frozen=True, slots=True)
class JianyingProfile:
    version: str
    executable: Path
    draft_root: Path
    draft_content_name: str = "draft_content.json"
    draft_meta_name: str = "draft_meta_info.json"
    root_meta_name: str = "root_meta_info.json"
    width: int = 1080
    height: int = 1920
    fps: int = 30


TARGET_VERSION = "11.3.0.14362"

