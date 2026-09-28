from __future__ import annotations

import shutil

import pytest

import ai_draft_builder.environment as environment_module
from ai_draft_builder.environment import EnvironmentChecker, launch_jianying
from ai_draft_builder.errors import EnvironmentCheckError


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
    with pytest.raises(EnvironmentCheckError, match="只允许"):
        _checker(version_reader=lambda _path: "12.0.0.1").ensure_ready(profile)


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
