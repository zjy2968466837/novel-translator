"""PyInstaller 打包入口 — 启动 GUI"""
import sys
import os

# PyInstaller 打包后的路径修正
if getattr(sys, 'frozen', False):
    # 打包后: 将 _internal 目录加入 sys.path
    base_dir = sys._MEIPASS
    sys.path.insert(0, base_dir)
    # 设置工作目录为 exe 所在目录（方便读取术语表等文件）
    os.chdir(os.path.dirname(sys.executable))

from novel_translator.gui import run_gui

if __name__ == "__main__":
    run_gui()
