from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .errors import EnvironmentCheckError
from .models import JianyingProfile, SUPPORTED_VERSION_PROFILES, TARGET_VERSION


def profile_for_version(version: str, executable: Path, draft_root: Path) -> JianyingProfile:
    try:
        draft_app_version, compatibility_mode = SUPPORTED_VERSION_PROFILES[version]
    except KeyError as exc:
        raise EnvironmentCheckError(
            f"检测到剪映版本 {version}，当前仅支持："
            f"{', '.join(SUPPORTED_VERSION_PROFILES)}。"
        ) from exc
    return JianyingProfile(
        version=version,
        executable=executable,
        draft_root=draft_root,
        draft_app_version=draft_app_version,
        compatibility_mode=compatibility_mode,
    )


def default_profile() -> JianyingProfile:
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    draft_root = (
        local_app_data / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"
    )
    local_apps = local_app_data / "JianyingPro" / "Apps"
    wrapper = local_apps / "JianyingPro.exe"
    if wrapper.is_file():
        try:
            installed_version = read_windows_file_version(wrapper)
        except EnvironmentCheckError:
            installed_version = ""
        if installed_version in SUPPORTED_VERSION_PROFILES:
            versioned = local_apps / installed_version / "JianyingPro.exe"
            executable = versioned if versioned.is_file() else wrapper
            return profile_for_version(installed_version, executable, draft_root)
    else:
        installed_version = ""

    versions = sorted(
        SUPPORTED_VERSION_PROFILES,
        key=lambda value: tuple(int(part) for part in value.split(".")),
        reverse=True,
    )
    for version in versions:
        versioned = local_apps / version / "JianyingPro.exe"
        if versioned.is_file():
            return profile_for_version(version, versioned, draft_root)

    if wrapper.is_file():
        return JianyingProfile(installed_version, wrapper, draft_root)

    legacy_executable = (
        program_files / "JianyingPro" / "Apps" / TARGET_VERSION / "JianyingPro.exe"
    )
    return profile_for_version(TARGET_VERSION, legacy_executable, draft_root)


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
    except Exception as exc:
        raise EnvironmentCheckError("无法读取剪映程序的真实版本，已停止生成。") from exc


def is_jianying_running() -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=flags, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentCheckError("无法检测剪映运行状态，已停止写入，请稍后重试。") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise EnvironmentCheckError("剪映进程检测失败，已停止写入，请稍后重试。")
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
        if installed_version not in SUPPORTED_VERSION_PROFILES:
            raise EnvironmentCheckError(
                f"检测到剪映版本 {installed_version}，当前仅支持："
                f"{', '.join(SUPPORTED_VERSION_PROFILES)}。"
            )
        expected_app_version, expected_mode = SUPPORTED_VERSION_PROFILES[installed_version]
        if (
            profile.draft_app_version != expected_app_version
            or profile.compatibility_mode is not expected_mode
        ):
            raise EnvironmentCheckError("剪映兼容配置与支持映射不一致，已停止生成草稿。")
        if installed_version != profile.version:
            raise EnvironmentCheckError(
                f"检测到剪映版本 {installed_version}，本版本工具只允许 {profile.version}。"
            )
        self.ensure_commit_ready(profile)
        if not profile.draft_root.is_dir():
            raise EnvironmentCheckError(f"找不到剪映草稿目录：{profile.draft_root}")
        root_meta = profile.draft_root / profile.root_meta_name
        if not root_meta.is_file():
            raise EnvironmentCheckError(f"找不到剪映草稿索引：{root_meta}")
        if not os.access(profile.draft_root, os.W_OK) or not os.access(root_meta, os.W_OK):
            raise EnvironmentCheckError("剪映草稿目录不可写，请检查权限。")
        if self.disk_usage(profile.draft_root).free < self.minimum_free_bytes:
            raise EnvironmentCheckError("磁盘剩余空间不足 50 MB，无法安全生成草稿。")

    def ensure_commit_ready(self, profile: JianyingProfile) -> None:
        try:
            if self.process_checker():
                raise EnvironmentCheckError("剪映正在运行。请完全退出剪映后再生成草稿。")
            if self.disk_usage(profile.draft_root).free < self.minimum_free_bytes:
                raise EnvironmentCheckError("磁盘剩余空间不足，已停止后续生成。")
        except EnvironmentCheckError:
            raise
        except Exception as exc:
            raise EnvironmentCheckError("无法检查剪映进程或磁盘状态，已停止后续生成。") from exc
