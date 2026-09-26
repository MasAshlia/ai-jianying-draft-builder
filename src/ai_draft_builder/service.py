from __future__ import annotations

import ctypes
import os
import shutil
import uuid
from pathlib import Path

from .adapter import JianyingAdapter
from .environment import EnvironmentChecker
from .errors import DraftBuildError, UserFacingError
from .models import BuildRequest, BuildResult, JianyingProfile
from .naming import unique_draft_name, validate_draft_name
from .probe import MediaProbe
from .registry import DraftRegistrar
from .scanner import MediaScanner
from .validator import DraftValidator


_STAGING_DIR_NAME = ".ai-draft-builder-staging"
_TXN_PREFIX = ".txn-"


def _mark_hidden(path: Path) -> None:
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # type: ignore[attr-defined]
    except Exception:
        pass


def _safe_remove_tree(path: Path, allowed_parent: Path, required_prefix: str | None = None) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise DraftBuildError("拒绝清理符号链接形式的临时目录。")
    if path.parent.resolve() != allowed_parent.resolve():
        raise DraftBuildError("拒绝清理应用目录之外的路径。")
    if required_prefix is not None and not path.name.startswith(required_prefix):
        raise DraftBuildError("拒绝清理名称不符合规则的临时目录。")
    shutil.rmtree(path)


class DraftBuildService:
    def __init__(
        self,
        profile: JianyingProfile,
        *,
        scanner: MediaScanner | None = None,
        probe: MediaProbe | None = None,
        adapter: JianyingAdapter | None = None,
        validator: DraftValidator | None = None,
        registrar: DraftRegistrar | None = None,
        environment: EnvironmentChecker | None = None,
    ) -> None:
        self.profile = profile
        self.scanner = scanner or MediaScanner()
        self.probe = probe or MediaProbe()
        self.adapter = adapter or JianyingAdapter()
        self.validator = validator or DraftValidator()
        self.registrar = registrar or DraftRegistrar()
        self.environment = environment or EnvironmentChecker()

    def build(self, request: BuildRequest) -> BuildResult:
        draft_name = validate_draft_name(request.draft_name)
        try:
            self.environment.ensure_ready(self.profile)
            paths = self.scanner.scan(request.source_dir)
            clips = self.probe.probe_all(paths)

            registered_names = self.registrar.existing_names(self.profile)
            final_name = unique_draft_name(self.profile.draft_root, draft_name, registered_names)
            final_dir = self.profile.draft_root / final_name
            staging_root = self.profile.draft_root / _STAGING_DIR_NAME
            staging_root.mkdir(exist_ok=True)
            _mark_hidden(staging_root)
            staging_name = f"{_TXN_PREFIX}{uuid.uuid4().hex}"
            staging_dir = staging_root / staging_name
            installed = False
            try:
                built_dir = self.adapter.build(
                    staging_root,
                    staging_name,
                    final_dir,
                    final_name,
                    clips,
                    self.profile,
                )
                if built_dir.resolve() != staging_dir.resolve():
                    raise DraftBuildError("适配层返回了意外的临时草稿路径。")
                validation = self.validator.validate(staging_dir, clips, self.profile)
                os.replace(staging_dir, final_dir)
                installed = True
                self.registrar.register(
                    draft_dir=final_dir,
                    draft_name=final_name,
                    validation=validation,
                    profile=self.profile,
                )
                return BuildResult(
                    draft_dir=final_dir,
                    clip_count=validation.clip_count,
                    duration_us=validation.duration_us,
                )
            except Exception:
                if installed:
                    _safe_remove_tree(final_dir, self.profile.draft_root)
                else:
                    _safe_remove_tree(staging_dir, staging_root, _TXN_PREFIX)
                raise
        except UserFacingError:
            raise
        except PermissionError as exc:
            raise DraftBuildError("没有权限写入剪映草稿目录，请关闭剪映并检查目录权限。") from exc
        except OSError as exc:
            raise DraftBuildError(f"文件操作失败：{exc}") from exc
        except Exception as exc:
            raise DraftBuildError(f"生成草稿失败：{exc}") from exc

