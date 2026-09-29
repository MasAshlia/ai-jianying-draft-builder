from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import ai_draft_builder.environment as environment_module
from ai_draft_builder.environment import (
    EnvironmentChecker,
    default_profile,
    launch_jianying,
    profile_for_version,
)
from ai_draft_builder.errors import EnvironmentCheckError
from ai_draft_builder.models import CompatibilityMode


def _checker(**overrides) -> EnvironmentChecker:
    options = {
        "version_reader": lambda _path: "11.3.0.14362",
        "process_checker": lambda: False,
        "disk_usage": lambda _path: shutil._ntuple_diskusage(100, 20, 80),
        "minimum_free_bytes": 50,
    }
    options.update(overrides)
    return EnvironmentChecker(**options)


def test_ready_environment_passes(monkeypatch, profile) -> None:
    monkeypatch.setattr(environment_module.platform, "system", lambda: "Windows")
    _checker().ensure_ready(profile)


def test_running_jianying_is_rejected(monkeypatch, profile) -> None:
    monkeypatch.setattr(environment_module.platform, "system", lambda: "Windows")
    with pytest.raises(EnvironmentCheckError, match="正在运行"):
        _checker(process_checker=lambda: True).ensure_ready(profile)


def test_wrong_version_is_rejected(monkeypatch, profile) -> None:
    monkeypatch.setattr(environment_module.platform, "system", lambda: "Windows")
    with pytest.raises(EnvironmentCheckError, match="当前仅支持"):
        _checker(version_reader=lambda _path: "12.0.0.1").ensure_ready(profile)


def test_exact_11_5_profile_uses_verified_legacy_11_3_format(tmp_path: Path) -> None:
    executable = tmp_path / "11.5.0.14471" / "JianyingPro.exe"
    selected = profile_for_version("11.5.0.14471", executable, tmp_path / "drafts")

    assert selected.version == "11.5.0.14471"
    assert selected.draft_app_version == "11.3.0"
    assert selected.compatibility_mode is CompatibilityMode.VERIFIED_LEGACY_IMPORT


def test_unlisted_version_has_no_profile(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentCheckError, match="当前仅支持"):
        profile_for_version("11.5.1.1", tmp_path / "JianyingPro.exe", tmp_path / "drafts")


def test_default_profile_prefers_local_versioned_install(monkeypatch, tmp_path: Path) -> None:
    local = tmp_path / "LocalAppData"
    apps = local / "JianyingPro" / "Apps"
    wrapper = apps / "JianyingPro.exe"
    versioned = apps / "11.5.0.14471" / "JianyingPro.exe"
    versioned.parent.mkdir(parents=True)
    wrapper.write_bytes(b"wrapper")
    versioned.write_bytes(b"versioned")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(
        environment_module, "read_windows_file_version", lambda _path: "11.5.0.14471"
    )

    discovered = default_profile()

    assert discovered.executable == versioned
    assert discovered.version == "11.5.0.14471"
    assert discovered.draft_app_version == "11.3.0"


def test_default_profile_finds_local_version_directory_without_readable_wrapper(
    monkeypatch, tmp_path: Path
) -> None:
    local = tmp_path / "LocalAppData"
    wrapper = local / "JianyingPro" / "Apps" / "JianyingPro.exe"
    versioned = (
        local / "JianyingPro" / "Apps" / "11.5.0.14471" / "JianyingPro.exe"
    )
    versioned.parent.mkdir(parents=True)
    wrapper.write_bytes(b"wrapper-without-readable-version")
    versioned.write_bytes(b"versioned")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(
        environment_module,
        "read_windows_file_version",
        lambda _path: (_ for _ in ()).throw(EnvironmentCheckError("unreadable")),
    )

    discovered = default_profile()

    assert discovered.executable == versioned
    assert discovered.version == "11.5.0.14471"


def test_low_disk_space_is_rejected(monkeypatch, profile) -> None:
    monkeypatch.setattr(environment_module.platform, "system", lambda: "Windows")
    low = lambda _path: shutil._ntuple_diskusage(100, 90, 10)
    with pytest.raises(EnvironmentCheckError, match="空间不足"):
        _checker(disk_usage=low).ensure_ready(profile)


def test_unwritable_root_is_rejected(monkeypatch, profile) -> None:
    monkeypatch.setattr(environment_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(environment_module.os, "access", lambda *_args: False)
    with pytest.raises(EnvironmentCheckError, match="不可写"):
        _checker().ensure_ready(profile)


def test_launch_jianying_uses_target_executable(monkeypatch, profile) -> None:
    captured = {}

    class FakeProcess:
        pass

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(environment_module.subprocess, "Popen", fake_popen)
    launch_jianying(profile)
    assert captured["args"] == [str(profile.executable)]
    assert captured["kwargs"]["cwd"] == str(profile.executable.parent)


def test_process_detection_error_fails_closed(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(environment_module.subprocess, "run",
                        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""))
    with pytest.raises(EnvironmentCheckError, match="检测失败"):
        environment_module.is_jianying_running()


def test_process_detection_timeout_fails_closed(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise environment_module.subprocess.TimeoutExpired("tasklist", 10)
    monkeypatch.setattr(environment_module.subprocess, "run", timeout)
    with pytest.raises(EnvironmentCheckError, match="无法检测"):
        environment_module.is_jianying_running()
