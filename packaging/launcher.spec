# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：把 launcher.py 打成 windowed 单文件 exe（无黑窗）。
# 用法：在 packaging/ 目录下执行
#   python -m PyInstaller --clean --noconfirm launcher.spec
import os

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ICON = os.path.join(SPEC_DIR, "app.ico")
icon_path = ICON if os.path.exists(ICON) else None  # 无 app.ico 时用默认图标

a = Analysis(
    ["launcher.py"],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=[],
    hiddenimports=["pystray._win32"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="科技项目管理系统",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # windowed（无黑窗）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
