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

# ===== 强制 UTF-8 模式（解决中文 Windows 编码问题）=====
os.environ['PYTHONUTF8'] = '1'

# ===== 关键: 优先使用预编译目录 _build_src，回退到 src =====
BUILD_SRC = os.path.join(os.path.abspath("."), "_build_src")
SRC_DIR = os.path.join(os.path.abspath("."), "src")
# _build_src 优先（预编译的 .pyc 避免编码问题）
SEARCH_PATHS = [p for p in [BUILD_SRC, SRC_DIR] if os.path.isdir(p)]
for p in SEARCH_PATHS:
    if p not in sys.path:
        sys.path.insert(0, p)

# ===== 路径 =====
SITE_PACKAGES = os.path.dirname(importlib.import_module("flet").__file__)
SITE_PACKAGES = os.path.dirname(SITE_PACKAGES)  # 上级 site-packages

FLET_DESKTOP_APP = os.path.join(SITE_PACKAGES, "flet_desktop", "app")

# novel_translator 的所有子模块（显式列出，避免 collect_submodules 在 src 布局下的兼容性问题）
nt_hiddenimports = [
    'novel_translator',
    'novel_translator.engine',
    'novel_translator.gui',
    'novel_translator.cli',
]

# ===== 分析 =====
a = Analysis(
    ["launcher.py"],
    pathex=SEARCH_PATHS,
    binaries=[],
    datas=[
        # Flet 桌面客户端 (flet.exe + DLLs + data)
        (FLET_DESKTOP_APP, "flet_desktop/app"),
        # Flet 资源文件 (icons.json 等)
        (os.path.join(SITE_PACKAGES, "flet"), "flet"),
        # novel_translator 包源码 (显式打包，解决 src layout 兼容性问题)
        (os.path.join(SRC_DIR, "novel_translator"), "novel_translator"),
        # 示例术语表
        ("examples", "examples"),
    ],
    hiddenimports=nt_hiddenimports + [
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
    console=False,           # 发行模式：隐藏控制台
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
