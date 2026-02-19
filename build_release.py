"""
构建发行包脚本 — Novel Translator

解决中文 Windows 上 PyInstaller 无法编译含中文字符的 UTF-8 源文件的问题。
方案：先将 .py 预编译为 .pyc，然后将 .pyc 放入 src 目录供 PyInstaller 打包。

使用方式：
    python build_release.py
"""
import os
import sys
import shutil
import subprocess
import py_compile
import compileall

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_PKG = os.path.join(PROJECT_ROOT, "src", "novel_translator")
BUILD_SRC = os.path.join(PROJECT_ROOT, "_build_src", "novel_translator")


def step(msg):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def precompile_sources():
    """将 src/novel_translator/*.py 预编译为 _build_src/novel_translator/*.pyc"""
    step("Step 1: 预编译 Python 源文件为 .pyc")

    if os.path.exists(BUILD_SRC):
        shutil.rmtree(BUILD_SRC)
    os.makedirs(BUILD_SRC)

    for fname in os.listdir(SRC_PKG):
        if not fname.endswith(".py"):
            continue
        src_path = os.path.join(SRC_PKG, fname)
        # 同时复制 .py 原文件（用于 PyInstaller 分析）和 .pyc
        shutil.copy2(src_path, os.path.join(BUILD_SRC, fname))
        pyc_path = os.path.join(BUILD_SRC, fname + "c")
        py_compile.compile(src_path, pyc_path, doraise=True)
        print(f"  [OK] {fname} -> {fname}c")

    print(f"\n  预编译完成，输出目录: {BUILD_SRC}")


def run_pyinstaller():
    """执行 PyInstaller 打包"""
    step("Step 2: PyInstaller 打包")

    spec_file = os.path.join(PROJECT_ROOT, "novel_translator.spec")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 将预编译的包目录加入 PYTHONPATH
    env["PYTHONPATH"] = os.path.join(PROJECT_ROOT, "_build_src") + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", spec_file, "--noconfirm"],
        cwd=PROJECT_ROOT,
        env=env,
    )
    if result.returncode != 0:
        print("\n  [ERROR] PyInstaller 打包失败！")
        sys.exit(1)
    print("\n  [OK] 打包完成")


def create_release_zip():
    """创建发行压缩包"""
    step("Step 3: 创建发行压缩包")

    dist_dir = os.path.join(PROJECT_ROOT, "dist", "NovelTranslator")
    if not os.path.isdir(dist_dir):
        print("  [ERROR] dist/NovelTranslator 目录不存在，请先确认打包成功")
        sys.exit(1)

    # 复制附带文件
    for fname in ["README.md", "LICENSE"]:
        src = os.path.join(PROJECT_ROOT, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dist_dir, fname))

    glossary_src = os.path.join(PROJECT_ROOT, "examples", "glossary_example.json")
    if os.path.exists(glossary_src):
        shutil.copy2(glossary_src, os.path.join(dist_dir, "glossary_example.json"))

    # 创建 zip
    zip_name = "NovelTranslator-win-x64"
    zip_path = os.path.join(PROJECT_ROOT, "dist", zip_name)
    shutil.make_archive(zip_path, "zip", dist_dir)

    final_zip = zip_path + ".zip"
    size_mb = os.path.getsize(final_zip) / 1024 / 1024
    print(f"  [OK] 发行包: {final_zip}")
    print(f"  [OK] 大小: {size_mb:.1f} MB")


def cleanup():
    """清理临时文件"""
    if os.path.exists(os.path.join(PROJECT_ROOT, "_build_src")):
        shutil.rmtree(os.path.join(PROJECT_ROOT, "_build_src"))


def main():
    print("Novel Translator — 构建发行包")
    print(f"Python: {sys.version}")
    print(f"项目目录: {PROJECT_ROOT}")

    try:
        precompile_sources()
        run_pyinstaller()
        create_release_zip()
    finally:
        cleanup()

    step("构建完成")
    print("  发行文件位于 dist/ 目录")
    print("  将 dist/NovelTranslator 文件夹或 .zip 分发给用户即可")


if __name__ == "__main__":
    main()
