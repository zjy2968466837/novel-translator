"""PyInstaller 打包入口 — 启动 GUI"""
import sys
import os

# 强制 UTF-8 模式（解决中文 Windows 编码问题）
os.environ['PYTHONUTF8'] = '1'
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# PyInstaller 打包后的路径修正
if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
    # 确保能找到 novel_translator 包
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    # 设置工作目录为 exe 所在目录（方便读取术语表等文件）
    os.chdir(os.path.dirname(sys.executable))
else:
    # 开发模式: 将 src 加入路径
    src_dir = os.path.join(os.path.dirname(__file__), "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

from novel_translator.gui import run_gui

if __name__ == "__main__":
    run_gui()
