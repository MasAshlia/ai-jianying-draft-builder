from __future__ import annotations

import re
from pathlib import Path

from .errors import UserFacingError


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_draft_name(name: str) -> str:
    value = name.strip()
    if not value:
        raise UserFacingError("请输入草稿名称。")
    if len(value) > 100:
        raise UserFacingError("草稿名称过长，请控制在 100 个字符以内。")
    if _INVALID.search(value) or value.endswith((".", " ")):
        raise UserFacingError("草稿名称包含 Windows 不允许的字符，或以点/空格结尾。")
    if value.split(".", 1)[0].upper() in _RESERVED:
        raise UserFacingError("该草稿名称是 Windows 保留名称，请换一个名称。")
    return value


def unique_draft_name(root: Path, requested: str, registered_names: set[str] | None = None) -> str:
    base = validate_draft_name(requested)
    occupied = {item.name.casefold() for item in root.iterdir() if item.is_dir()}
    occupied.update(name.casefold() for name in (registered_names or set()))
    if base.casefold() not in occupied:
        return base
    index = 1
    while f"{base} ({index})".casefold() in occupied:
        index += 1
    return f"{base} ({index})"

