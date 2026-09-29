from __future__ import annotations

import ctypes
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .adapter import JianyingAdapter
from .environment import EnvironmentChecker
from .errors import DraftBuildError, EnvironmentCheckError, FatalBuildError, NeedsReviewError, UserFacingError
from .models import (BatchBuildRequest, BatchBuildResult, BatchItemResult, BatchItemStatus,
                     BatchProgress, BatchProgressState, BuildRequest, BuildResult, EpisodeFolder,
                     JianyingProfile, COMPATIBILITY_TEST_DRAFT_NAME)
from .naming import unique_draft_name, validate_draft_name
from .preparation import LocalPreparer, ProcessPreparer, verify_sources
from .probe import MediaProbe
from .registry import DraftRegistrar
from .runtime import GenerationLock, TaskJournal, application_dir, reconcile_records
from .scanner import EpisodeBatchScanner, MediaScanner, natural_sort_key
from .validator import DraftValidator


_STAGING_DIR_NAME = ".ai-draft-builder-staging"
_TXN_PREFIX = ".txn-"
logger = logging.getLogger(__name__)


def _mark_hidden(path: Path) -> None:
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


def _safe_remove_tree(path: Path, allowed_parent: Path, required_prefix: str | None = None) -> None:
    if not path.exists():
        return
    if path.is_symlink() or path.is_junction():
        raise DraftBuildError("拒绝清理链接形式的临时目录。")
    if path.parent.resolve() != allowed_parent.resolve():
        raise DraftBuildError("拒绝清理应用目录之外的路径。")
    if required_prefix is None or not path.name.startswith(required_prefix):
        raise DraftBuildError("拒绝清理名称不符合规则的临时目录。")
    shutil.rmtree(path)


class DraftBuildService:
    def __init__(self, profile: JianyingProfile, *, scanner=None, probe=None, adapter=None,
                 validator=None, registrar=None, environment=None, batch_scanner=None,
                 preparer=None, state_dir: Path | None = None) -> None:
        self.profile = profile
        self.scanner = scanner or MediaScanner()
        self.validator = validator or DraftValidator()
        self.registrar = registrar or DraftRegistrar()
        self.environment = environment or EnvironmentChecker()
        self.batch_scanner = batch_scanner or EpisodeBatchScanner()
        self.preparer = preparer or (
            LocalPreparer(probe or MediaProbe(), adapter or JianyingAdapter())
            if probe is not None or adapter is not None else ProcessPreparer()
        )
        self.state_dir = state_dir or application_dir()
        self.records_dir = self.state_dir / "tasks"

    def recover(self) -> list[dict]:
        with GenerationLock(self.profile.draft_root):
            return reconcile_records(self.records_dir, self.profile)

    def build(self, request: BuildRequest) -> BuildResult:
        try:
            with GenerationLock(self.profile.draft_root):
                self.environment.ensure_ready(self.profile)
                self._reject_unresolved(request.source_dir, reconcile_records(self.records_dir, self.profile))
                journal = TaskJournal(self.records_dir, self.profile.draft_root,
                                      [(request.source_dir, request.draft_name)])
                try:
                    result = self._build_one(request, journal, 0, lambda *_: None)
                except Exception as exc:
                    error = self._translate_error(exc)
                    journal.update(0, status="needs_review" if isinstance(error, NeedsReviewError) else "failed",
                                   message=str(error))
                    journal.finish(stopped=True)
                    raise error
                journal.finish(stopped=False)
                return result
        except UserFacingError:
            logger.exception("单集任务失败：%s", request.source_dir)
            raise
        except Exception as exc:
            logger.exception("单集任务异常")
            raise self._translate_error(exc) from exc

    def build_batch(self, request: BatchBuildRequest,
                    progress_callback: Callable[[BatchProgress], None] | None = None,
                    stop_event: threading.Event | None = None) -> BatchBuildResult:
        try:
            with GenerationLock(self.profile.draft_root):
                self.environment.ensure_ready(self.profile)
                if self.profile.is_legacy_import_probe:
                    raise DraftBuildError("剪映 11.5 兼容性探针仅允许单集模式。")
                unresolved = reconcile_records(self.records_dir, self.profile)
                episodes = self._episodes(request)
                names = [request.name_prefix + episode.source_dir.name for episode in episodes]
                journal = TaskJournal(self.records_dir, self.profile.draft_root,
                                      list(zip([e.source_dir for e in episodes], names)))
                results = []
                stopped = False
                total = len(episodes)
                for offset, (episode, name) in enumerate(zip(episodes, names)):
                    def progress(stage, video=""):
                        message = f"正在处理 {offset + 1}/{total}：{episode.source_dir.name} — {stage} {video}"
                        logger.info("任务 %s %s", journal.task_id, message)
                        self._emit_progress(progress_callback, BatchProgress(
                            offset + 1, total, episode.source_dir, BatchProgressState.PROCESSING,
                            message, stage, video))

                    if stopped or (stop_event is not None and stop_event.is_set()):
                        stopped = True
                        item = BatchItemResult(episode.source_dir, name, BatchItemStatus.NOT_PROCESSED,
                                               None, "任务已停止，本集未处理")
                    else:
                        try:
                            self._reject_unresolved(episode.source_dir, unresolved)
                            if episode.error:
                                raise DraftBuildError(episode.error)
                            if episode.video_count == 0:
                                item = BatchItemResult(episode.source_dir, name, BatchItemStatus.SKIPPED,
                                                       None, "没有找到支持的视频，已跳过。")
                            else:
                                progress("准备")
                                result = self._build_one(BuildRequest(episode.source_dir, name), journal,
                                                         offset, progress)
                                item = BatchItemResult(episode.source_dir, name, BatchItemStatus.SUCCESS,
                                                       result, f"已生成：{result.draft_dir.name}")
                        except Exception as exc:
                            error = self._translate_error(exc)
                            logger.exception("任务 %s 分集 %s 失败", journal.task_id, episode.source_dir)
                            status = BatchItemStatus.NEEDS_REVIEW if isinstance(error, NeedsReviewError) else BatchItemStatus.FAILED
                            if getattr(error, "record", None):
                                previous = error.record
                                journal.update(offset, draft_id=previous.get("draft_id"),
                                               draft_dir=previous.get("draft_dir"),
                                               previous_report=previous.get("report_path"))
                            item = BatchItemResult(episode.source_dir, name, status, None, str(error))
                            if isinstance(error, (FatalBuildError, EnvironmentCheckError)):
                                stopped = True
                    results.append(item)
                    journal.update(offset, status=item.status.value, message=item.message)
                    self._emit_progress(progress_callback, BatchProgress(
                        offset + 1, total, episode.source_dir, BatchProgressState(item.status.value), item.message))
                stopped = stopped or (stop_event is not None and stop_event.is_set())
                journal.finish(stopped=stopped)
                successful = [item.build_result for item in results if item.build_result]
                return BatchBuildResult(
                    tuple(results), len(successful),
                    sum(item.status is BatchItemStatus.FAILED for item in results),
                    sum(item.status is BatchItemStatus.SKIPPED for item in results),
                    sum(item.clip_count for item in successful), sum(item.duration_us for item in successful),
                    stopped, journal.task_id, journal.path,
                )
        except UserFacingError:
            raise
        except Exception as exc:
            logger.exception("批量任务异常")
            raise self._translate_error(exc) from exc

    def _episodes(self, request: BatchBuildRequest) -> list[EpisodeFolder]:
        if request.selected_dirs is None:
            return self.batch_scanner.discover(request.parent_dir)
        parent = request.parent_dir.expanduser().resolve()
        paths = []
        for original in request.selected_dirs:
            path = original.expanduser().absolute()
            if path.is_symlink() or path.is_junction() or path.resolve().parent != parent or path.name.startswith("."):
                raise UserFacingError("所选分集必须是总目录的直属普通子文件夹。")
            if path.resolve() not in paths:
                paths.append(path.resolve())
        if not paths:
            raise UserFacingError("请至少勾选一集。")
        # 冻结的选择决定本次任务，不将新增子目录加入任务。
        available = {e.source_dir: e for e in self.batch_scanner.discover(parent)}
        return [available.get(path, EpisodeFolder(path, 0, "所选分集目录已不存在，请重新选择。"))
                for path in sorted(paths, key=natural_sort_key)]

    @staticmethod
    def _reject_unresolved(source: Path, unresolved: list[dict]) -> None:
        previous = next((item for item in unresolved if Path(item["source_dir"]).resolve() == source.resolve()), None)
        if previous:
            error = NeedsReviewError("此分集有中断任务待核查，请打开日志目录核对任务记录，暂不重复生成。")
            error.record = previous
            raise error

    def _commit_guard(self):
        self.environment.ensure_commit_ready(self.profile)

    def _build_one(self, request: BuildRequest, journal: TaskJournal, index: int, progress) -> BuildResult:
        started = time.monotonic()
        name = validate_draft_name(request.draft_name)
        if self.profile.is_legacy_import_probe and name != COMPATIBILITY_TEST_DRAFT_NAME:
            raise DraftBuildError(
                "剪映 11.5 兼容性探针的草稿名必须精确为："
                f"{COMPATIBILITY_TEST_DRAFT_NAME}"
            )
        journal.update(index, status="processing", phase="scanning", started_at=time.time())
        paths = self.scanner.scan(request.source_dir)
        original = (self.profile.draft_root / self.profile.root_meta_name).read_bytes()
        DraftRegistrar._decode(original)
        names = self.registrar.existing_names(self.profile)
        if self.profile.is_legacy_import_probe:
            final_name = name
            if final_name in names or (self.profile.draft_root / final_name).exists():
                raise DraftBuildError(
                    "兼容性测试草稿已存在。为防止覆盖或自动追加名称，已停止生成。"
                )
        else:
            final_name = unique_draft_name(self.profile.draft_root, name, names)
        final_dir = self.profile.draft_root / final_name
        staging_root = self.profile.draft_root / _STAGING_DIR_NAME
        if staging_root.is_symlink() or staging_root.is_junction():
            raise FatalBuildError("应用临时目录不能是链接，请检查草稿目录。")
        staging_name = f"{_TXN_PREFIX}{uuid.uuid4().hex}"
        staging_dir = staging_root / staging_name
        journal.update(index, phase="preparing", draft_dir=str(final_dir), staging_dir=str(staging_dir))
        staging_root.mkdir(exist_ok=True)
        _mark_hidden(staging_root)
        installed = False
        try:
            built_dir, clips, snapshots = self.preparer.prepare(
                paths, staging_root, staging_name, final_dir, final_name, self.profile, progress)
            if built_dir.resolve() != staging_dir.resolve():
                raise DraftBuildError("适配层返回了意外的临时草稿路径。")
            validation = self.validator.validate(staging_dir, clips, self.profile)
            journal.update(index, phase="ready", draft_id=validation.draft_id,
                           clip_count=validation.clip_count, duration_us=validation.duration_us)
            verify_sources(snapshots)
            self._commit_guard()
            if (self.profile.draft_root / self.profile.root_meta_name).read_bytes() != original:
                raise FatalBuildError("剪映草稿索引在准备期间发生变化，已停止写入。")
            journal.update(index, phase="installing")
            # Windows rename 在目标已存在时失败，绝不替换已有目录。
            os.rename(staging_dir, final_dir)
            installed = True
            journal.update(index, phase="registering")
            backup_path = self.registrar.register(
                draft_dir=final_dir, draft_name=final_name,
                validation=validation, profile=self.profile,
                expected_original=original, before_replace=self._commit_guard)
            journal.update(index, phase="committed", status="success", message=f"已生成：{final_name}",
                           elapsed_seconds=round(time.monotonic() - started, 3))
            logger.info("任务 %s 成功 %s 剪映 %s 片段 %d 时长 %d 耗时 %.2fs",
                        journal.task_id, final_dir, self.profile.version, validation.clip_count,
                        validation.duration_us, time.monotonic() - started)
            return BuildResult(
                final_dir, validation.clip_count, validation.duration_us, backup_path
            )
        except Exception as exc:
            if installed:
                raise NeedsReviewError(f"草稿安装后的操作未完成，目录已保留待核查：{final_dir}。原因：{exc}") from exc
            _safe_remove_tree(staging_dir, staging_root, _TXN_PREFIX)
            raise

    @staticmethod
    def _emit_progress(callback, progress):
        if callback:
            try:
                callback(progress)
            except Exception:
                logger.exception("进度观察者失败，不影响事务")

    @staticmethod
    def _translate_error(exc: Exception) -> UserFacingError:
        if isinstance(exc, UserFacingError):
            return exc
        if isinstance(exc, PermissionError):
            return FatalBuildError("目录权限不足，已停止后续生成，请检查素材、日志和草稿目录权限。")
        if isinstance(exc, OSError):
            return FatalBuildError(f"文件操作失败，已停止后续生成：{exc}")
        return DraftBuildError(f"生成草稿失败：{exc}")
