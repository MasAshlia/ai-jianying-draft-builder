"""Prepare a source-free workstation onedir ZIP and audit personal build paths."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distribution
import marshal
import os
from pathlib import Path
import re
import shutil
import sys
from types import CodeType
import zipfile

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ai_draft_builder import __version__


def code_strings(code):
    if isinstance(code, CodeType):
        yield code.co_filename
        for value in code.co_consts:
            yield from code_strings(value)
    elif isinstance(code, str):
        yield code
    elif isinstance(code, (list, tuple)):
        for value in code:
            yield from code_strings(value)


def audit(app: Path):
    needles = [str(ROOT).casefold(), str(Path.home()).casefold(), "masashlia"]
    needles += [value.replace("\\", "/") for value in list(needles)]
    user = os.environ.get("USERNAME", "")
    generic_windows_accounts = {"administrator", "admin", "user", "default", "public"}
    personal_path = re.compile(r"[a-z]:[\\/]+users[\\/]+[^\\/\s\x00]+|/Users/[^/\s\x00]+", re.I)
    username = (
        re.compile(r"(?<![\w\d])" + re.escape(user) + r"(?![\w\d])", re.I)
        if user and user.casefold() not in generic_windows_accounts
        else None
    )
    problems = []

    def check(text, origin):
        # CPython 自带的路径函数文档示例，不是用户信息或运行路径。
        if origin in {"_internal\\python312.dll", "_internal\\base_library.zip", "_internal\\base_library.zip:ntpath.pyc"}:
            text = text.replace("C:/Users/Barney", "<stdlib path example>")
        normalized = text.casefold().replace("\\\\", "\\")
        if any(needle in normalized for needle in needles) or personal_path.search(text) or (username and username.search(text)):
            problems.append(origin)

    for path in app.rglob("*"):
        if not path.is_file():
            continue
        relative = str(path.relative_to(app))
        if path.suffix.lower() in {".py", ".pyw", ".pyc", ".pyo", ".mp4", ".mov", ".mkv", ".avi", ".webm", ".log"}:
            problems.append(f"Forbidden distribution file: {relative}")
        parts = [part.casefold() for part in path.relative_to(app).parts]
        if any(part in {".venv", "test_materials", ".git"} for part in parts) or ("src" in parts and "licenses" not in parts):
            problems.append(f"Forbidden distribution directory: {relative}")
        raw = path.read_bytes()
        check(raw.decode("utf-8", errors="ignore"), relative)
        check(raw.decode("utf-16-le", errors="ignore"), relative)
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    data = archive.read(name)
                    check(data.decode("utf-8", errors="ignore"), relative + ":" + name)
                    if name.endswith(".pyc"):
                        for text in code_strings(marshal.loads(data[16:])):
                            check(text, relative + ":" + name)
    archive = CArchiveReader(str(app / "AIDraftBuilder.exe"))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    for name in pyz.toc:
        for text in code_strings(pyz.extract(name)):
            check(text, "PYZ:" + name)
    for name, entry in archive.toc.items():
        if entry[-1] in ("s", "m", "M"):
            for text in code_strings(marshal.loads(archive.extract(name))):
                check(text, "EXE:" + name)
    if problems:
        raise SystemExit("Distribution audit failed:\n" + "\n".join(sorted(set(problems))))
    print("Distribution audit passed: no source/media/venv or personal build paths.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    release = args.release_dir.resolve()
    app = release / "AIDraftBuilder"
    if not (app / "AIDraftBuilder.exe").is_file():
        raise SystemExit("Missing executable")
    if args.audit_only:
        audit(app)
        return
    for name in ("TESTING.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, app / name)
    licenses = app / "licenses"
    licenses.mkdir(exist_ok=True)
    packages = [line.split("==")[0] for line in (ROOT / "requirements-lock.txt").read_text().splitlines() if "==" in line]
    for name in packages:
        dist = distribution(name)
        for entry in dist.files or ():
            if any(word in str(entry).casefold() for word in ("license", "copying", "copyright", "notice")):
                source = Path(dist.locate_file(entry))
                if source.is_file() and ".." not in entry.parts and source.suffix.lower() not in {".py", ".pyc"}:
                    target = licenses / name / entry
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    base = Path(sys.base_prefix)
    for source, name in ((base / "LICENSE.txt", "Python-LICENSE.txt"),
                         (base / "tcl/tcl8.6/license.terms", "Tcl-license.terms"),
                         (base / "tcl/tk8.6/license.terms", "Tk-license.terms")):
        if source.is_file():
            shutil.copy2(source, licenses / name)
    audit(app)
    archive = release / f"AIDraftBuilder-v{__version__}-win64.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(release))
    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive)
    print(f"Bytes: {archive.stat().st_size}; SHA256: {digest}")


if __name__ == "__main__":
    main()
