# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec 文件 — Novel Translator v3.0.0

生成方式:
  pyinstaller novel_translator.spec

输出:
  dist/NovelTranslator/NovelTranslator.exe  (目录模式，含所有依赖)
"""

import os
import sys
import importlib

# ===== 路径 =====
SITE_PACKAGES = os.path.dirname(importlib.import_module("flet").__file__)
SITE_PACKAGES = os.path.dirname(SITE_PACKAGES)  # 上级 site-packages

FLET_DESKTOP_APP = os.path.join(SITE_PACKAGES, "flet_desktop", "app")

# ===== 分析 =====
a = Analysis(
    ["launcher.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        # Flet 桌面客户端 (flet.exe + DLLs + data)
        (FLET_DESKTOP_APP, "flet_desktop/app"),
        # 示例术语表
        ("examples", "examples"),
    ],
    hiddenimports=[
        "novel_translator",
        "novel_translator.engine",
        "novel_translator.gui",
        "novel_translator.cli",
        # Flet 核心
        "flet",
        "flet.auth",
        "flet.canvas",
        "flet.controls",
        "flet.messaging",
        "flet.pubsub",
        "flet.security",
        "flet.utils",
        "flet_desktop",
        "flet_runtime",
        # 翻译引擎依赖
        "openai",
        "ebooklib",
        "ebooklib.epub",
        "bs4",
        "lxml",
        "lxml.etree",
        "lxml.html",
        # 标准库
        "concurrent.futures",
        "dataclasses",
        "json",
        "re",
        "threading",
        "pathlib",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "cv2",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NovelTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.ico",  # 如有图标可取消注释
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="NovelTranslator",
)
