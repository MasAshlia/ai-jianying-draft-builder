# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PyInstaller.config import CONF
import ast
from pathlib import Path
import re

pyjy_datas, pyjy_binaries, pyjy_hidden = collect_all("pyJianYingDraft", include_py_files=False)
media_datas, media_binaries, media_hidden = collect_all("pymediainfo", include_py_files=False)

analysis = Analysis(
    ["src/ai_draft_builder/__main__.py"],
    pathex=["src"],
    binaries=pyjy_binaries + media_binaries,
    datas=pyjy_datas + media_datas,
    hiddenimports=pyjy_hidden + media_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# 上游 NumPy 构建诊断包含 CI 用户目录；只清理诊断字符串，不修改运行库。
class RedactBuildPaths(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str) and re.match(r"^[A-Za-z]:[\\/]", node.value):
            return ast.copy_location(ast.Constant("[upstream build path omitted]"), node)
        return node

for index, (name, source, kind) in enumerate(analysis.pure):
    if name == "numpy.__config__":
        tree = ast.fix_missing_locations(RedactBuildPaths().visit(ast.parse(Path(source).read_text(encoding="utf-8"))))
        sanitized = Path(CONF["workpath"]) / "sanitized" / "numpy_config.py"
        sanitized.parent.mkdir(parents=True, exist_ok=True)
        text = ast.unparse(tree)
        if not sanitized.exists() or sanitized.read_text(encoding="utf-8") != text:
            sanitized.write_text(text, encoding="utf-8")
        analysis.pure[index] = (name, str(sanitized), kind)
        cache = CONF["code_cache"].get(id(analysis.pure))
        if cache is None:
            cache = CONF["code_cache"][id(analysis.pure)] = {}
        cache[name] = compile(tree, "numpy/__config__.py", "exec")
analysis.datas = [entry for entry in analysis.datas if Path(entry[0]).name != "DELVEWHEEL"]
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="AIDraftBuilder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AIDraftBuilder",
)

