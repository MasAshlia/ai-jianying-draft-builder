from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .errors import EnvironmentCheckError
from .models import JianyingProfile, TARGET_VERSION


def default_profile() -> JianyingProfile:
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    return JianyingProfile(
        version=TARGET_VERSION,
        executable=program_files / "JianyingPro" / "Apps" / TARGET_VERSION / "JianyingPro.exe",
        draft_root=local_app_data / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
    )


class _VSFixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32),
        ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32),
        ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32),
        ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32),
        ("dwFileDateLS", ctypes.c_uint32),
    ]


def read_windows_file_version(path: Path) -> str:
    try:
        version = ctypes.windll.version  # type: ignore[attr-defined]
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            raise OSError("无法读取版本资源")
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
            raise OSError("无法读取版本信息")
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            raise OSError("版本信息格式无效")
        info = ctypes.cast(pointer, ctypes.POINTER(_VSFixedFileInfo)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        # 官方安装目录的版本文件夹仍可作为明确的降级判断依据。
        return path.parent.name


def is_jianying_running() -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        check=False,
    )
    return "jianyingpro.exe" in completed.stdout.casefold()


def launch_jianying(profile: JianyingProfile) -> None:
    """在草稿完成注册后启动剪映，不等待剪映进程退出。"""
    if not profile.executable.is_file():
        raise EnvironmentCheckError(f"草稿已生成，但找不到剪映程序：{profile.executable}")
    try:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            [str(profile.executable)],
            cwd=str(profile.executable.parent),
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        raise EnvironmentCheckError(f"草稿已生成，但自动启动剪映失败：{exc}") from exc


class EnvironmentChecker:
    def __init__(
        self,
        *,
        version_reader: Callable[[Path], str] = read_windows_file_version,
        process_checker: Callable[[], bool] = is_jianying_running,
        disk_usage: Callable[[Path], shutil._ntuple_diskusage] = shutil.disk_usage,
        minimum_free_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.version_reader = version_reader
        self.process_checker = process_checker
        self.disk_usage = disk_usage
        self.minimum_free_bytes = minimum_free_bytes

    def ensure_ready(self, profile: JianyingProfile) -> None:
        if platform.system() != "Windows":
            raise EnvironmentCheckError("本工具只支持 Windows 10/11。")
        if not profile.executable.is_file():
            raise EnvironmentCheckError(
                f"未找到剪映 {profile.version}：{profile.executable}"
            )
        installed_version = self.version_reader(profile.executable)
        if installed_version != profile.version:
            raise EnvironmentCheckError(
                f"检测到剪映版本 {installed_version}，本版本工具只允许 {profile.version}。"
            )
        if self.process_checker():
            raise EnvironmentCheckError("剪映正在运行。请完全退出剪映后再生成草稿。")
        if not profile.draft_root.is_dir():
            raise EnvironmentCheckError(f"找不到剪映草稿目录：{profile.draft_root}")
        root_meta = profile.draft_root / profile.root_meta_name
        if not root_meta.is_file():
            raise EnvironmentCheckError(f"找不到剪映草稿索引：{root_meta}")
        if not os.access(profile.draft_root, os.W_OK) or not os.access(root_meta, os.W_OK):
            raise EnvironmentCheckError("剪映草稿目录不可写，请检查权限。")
        if self.disk_usage(profile.draft_root).free < self.minimum_free_bytes:
            raise EnvironmentCheckError("磁盘剩余空间不足 50 MB，无法安全生成草稿。")
