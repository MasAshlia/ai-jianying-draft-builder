from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
import time
import traceback

from .adapter import JianyingAdapter
from .errors import DraftBuildError, FatalBuildError, MediaProbeError
from .probe import MediaProbe


def fingerprint(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError as exc:
        raise MediaProbeError(f"素材“{path.name}”无法读取或已被删除，请检查后重试。") from exc


def verify_sources(snapshots: dict[Path, tuple[int, int]]) -> None:
    for path, expected in snapshots.items():
        if fingerprint(path) != expected:
            raise MediaProbeError(f"素材“{path.name}”在生成期间发生变化，请等待复制完成后重试。")


def _prepare_worker(connection, paths, staging_root, staging_name, final_dir, name, profile):
    try:
        snapshots = {path: fingerprint(path) for path in paths}
        clips = []
        probe = MediaProbe()
        for path in paths:
            connection.send(("progress", ("读取媒体信息", path.name)))
            clips.append(probe.probe(path))
            verify_sources({path: snapshots[path]})
        verify_sources(snapshots)
        target = JianyingAdapter().build(
            staging_root, staging_name, final_dir, name, clips, profile,
            progress_callback=lambda stage, video: connection.send(("progress", (stage, video))),
        )
        verify_sources(snapshots)
        connection.send(("result", (target, clips, snapshots)))
    except Exception as exc:
        cause = exc
        fatal = False
        while cause:
            fatal = fatal or isinstance(cause, (PermissionError, FatalBuildError)) or (
                isinstance(cause, OSError) and cause.errno == 28)
            cause = cause.__cause__
        connection.send(("error", (str(exc), traceback.format_exc(), fatal)))
    finally:
        connection.close()


class ProcessPreparer:
    """只有此进程调用 native 媒体解析；父进程独占安装和注册。"""

    def __init__(self, timeout: float = 120, worker=_prepare_worker):
        self.timeout = timeout
        self.worker = worker

    def prepare(self, paths, staging_root, staging_name, final_dir, name, profile, progress):
        import logging
        context = mp.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=self.worker,
            args=(sender, paths, staging_root, staging_name, final_dir, name, profile),
        )
        last_progress = time.monotonic()
        current_video = paths[0].name if paths else ""
        try:
            process.start()
            sender.close()
            while True:
                if receiver.poll(0.1):
                    try:
                        kind, value = receiver.recv()
                    except EOFError as exc:
                        raise DraftBuildError(f"媒体处理进程异常退出：{current_video}") from exc
                    if kind == "progress":
                        last_progress = time.monotonic()
                        stage, current_video = value
                        progress(stage, current_video)
                    elif kind == "result":
                        return value
                    elif kind == "error":
                        message, trace, fatal = value
                        logging.getLogger(__name__).error("工作进程失败：%s\n%s", current_video, trace)
                        error_type = FatalBuildError if fatal else DraftBuildError
                        raise error_type(message)
                if time.monotonic() - last_progress > self.timeout:
                    raise DraftBuildError(f"视频“{current_video}”处理超过 {self.timeout:g} 秒没有进展，已结束该集。")
                if not process.is_alive() and not receiver.poll():
                    raise DraftBuildError(f"媒体处理进程异常退出：{current_video}")
        finally:
            if process.pid is not None:
                process.join(timeout=1)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join()
                process.close()
            receiver.close()
            sender.close()


class LocalPreparer:
    """仅用于依赖注入测试；生产默认始终使用 ProcessPreparer。"""

    def __init__(self, probe, adapter):
        self.probe, self.adapter = probe, adapter

    def prepare(self, paths, staging_root, staging_name, final_dir, name, profile, progress):
        snapshots = {path: fingerprint(path) for path in paths}
        clips = self.probe.probe_all(paths)
        verify_sources(snapshots)
        target = self.adapter.build(staging_root, staging_name, final_dir, name, clips, profile)
        verify_sources(snapshots)
        return target, clips, snapshots
