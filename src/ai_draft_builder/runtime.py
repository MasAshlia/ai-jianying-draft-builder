from __future__ import annotations

import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time
import uuid

from . import __version__
from .errors import FatalBuildError


def application_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "AIDraftBuilder"


def configure_logging() -> Path:
    directory = application_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ai_draft_builder")
    if not logger.handlers:
        handler = RotatingFileHandler(directory / "AIDraftBuilder.log", maxBytes=5_000_000,
                                      backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    logger.info("启动版本 %s", __version__)
    return directory


class GenerationLock:
    """Windows 命名互斥量；进程退出后由系统释放，无陈旧锁文件。"""

    def __init__(self, draft_root: Path) -> None:
        digest = hashlib.sha256(str(draft_root.resolve()).casefold().encode()).hexdigest()
        self.name = "Local\\AIDraftBuilder-" + digest
        self.handle = None

    def __enter__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.kernel.WaitForSingleObject.restype = ctypes.c_uint32
        self.kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.kernel.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise FatalBuildError("无法建立生成锁，请检查系统权限。")
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 0x80):  # 正常获得或接管已退出进程的互斥量
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise FatalBuildError("已有另一个任务正在生成草稿，请等待其完成后重试。")
        return self

    def __exit__(self, *_args):
        if self.handle:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class TaskJournal:
    def __init__(self, directory: Path, draft_root: Path, requests: list[tuple[Path, str]]) -> None:
        self.task_id = uuid.uuid4().hex
        self.path = directory / f"{self.task_id}.json"
        self.data = {
            "schema": 1, "task_id": self.task_id, "app_version": __version__,
            "draft_root": str(draft_root.resolve()), "created_at": time.time(),
            "finished": False,
            "items": [{"source_dir": str(source.resolve()), "requested_name": name,
                       "status": "not_processed", "phase": "pending", "message": "尚未处理"}
                      for source, name in requests],
        }
        self.save()

    def save(self) -> None:
        try:
            atomic_json(self.path, self.data)
        except OSError as exc:
            raise FatalBuildError(f"无法保存任务记录，已停止生成：{exc}") from exc

    def update(self, index: int, **fields) -> None:
        self.data["items"][index].update(fields)
        item = self.data["items"][index]
        if fields.get("status") in ("success", "failed", "needs_review") and item.get("started_at"):
            item["elapsed_seconds"] = round(time.time() - item["started_at"], 3)
        self.save()

    def finish(self, *, stopped: bool) -> None:
        self.data.update(finished=True, stopped=stopped, finished_at=time.time())
        self.save()


def reconcile_records(directory: Path, profile) -> list[dict]:
    """只通过明文根索引核对；从不读取已保存草稿或删除最终目录。调用方持锁。"""
    from .registry import DraftRegistrar
    registry = DraftRegistrar()._read(profile)
    entries = registry["all_draft_store"]
    unresolved = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("schema") != 1:
                raise ValueError("未知记录格式")
            if Path(record["draft_root"]).resolve() != profile.draft_root.resolve():
                continue
            changed = False
            for item in record["items"]:
                if item.get("status") not in ("processing", "needs_review"):
                    continue
                final = Path(item["draft_dir"]) if item.get("draft_dir") else None
                match = next((entry for entry in entries
                              if item.get("draft_id") and entry.get("draft_id") == item["draft_id"]
                              and final and Path(entry.get("draft_fold_path", "")).resolve() == final.resolve()), None)
                if match and final and final.is_dir():
                    item.update(status="success", phase="reconciled", message="已核对注册与草稿目录，生成成功")
                else:
                    item.update(status="needs_review", message="上次生成中断，结果待核查；未自动重试或删除目录")
                    unresolved.append({**item, "report_path": str(path)})
                changed = True
            if changed:
                atomic_json(path, record)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise FatalBuildError(f"任务记录无法核对：{path.name}。请查看日志后处理，暂不生成。") from exc
    return unresolved
