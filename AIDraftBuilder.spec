# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

pyjy_datas, pyjy_binaries, pyjy_hidden = collect_all("pyJianYingDraft")
media_datas, media_binaries, media_hidden = collect_all("pymediainfo")

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

