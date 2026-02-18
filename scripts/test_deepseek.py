"""
快速测试 DeepSeek Beta 的前缀续写 / FIM / 流式输出行为脚本。
用法（在命令行中运行）：

Windows PowerShell:
$env:DEEPSEEK_API_KEY="<your_key>"; python .\scripts\test_deepseek.py

Linux/macOS:
DEEPSEEK_API_KEY=<your_key> python3 scripts/test_deepseek.py

注意：脚本不会记录或打印你的密钥。
"""

import os
import sys
import time

# Ensure `src` directory is on sys.path so `novel_translator` package can be imported
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from novel_translator.providers import create_provider

API_KEY = os.environ.get("DEEPSEEK_API_KEY")
if not API_KEY:
    print("请先在环境变量 DEEPSEEK_API_KEY 中设置你的 DeepSeek API Key")
    sys.exit(1)

system_prompt = "你是一位翻译测试助手。\n【强制术语表】\n- ウルタス -> 厄尔塔ス\n"
user_content = "これはテストです。ウルタスは勇者です。"

# Collect outputs to a UTF-8 file to avoid terminal encoding issues
OUT_PATH = os.path.join(ROOT, 'scripts', 'test_deepseek_result.txt')
_out_lines = []

def _safe_print(*args, **kwargs):
    text = " ".join(str(a) for a in args)
    end = kwargs.get('end', "\n")
    sys.stdout.write(text + end)
    sys.stdout.flush()
    _out_lines.append(text + ("" if end=="" else end))

_safe_print("\n=== 测试 1：前缀续写（stream=True） ===")
prov = create_provider(
    provider_type="openai",
    api_key=API_KEY,
    base_url="https://api.deepseek.com/beta",
    model_name="deepseek-chat",
    deepseek_beta=True,
    use_prefix_completion=True,
)

# 流式回调示例
_safe_print("开始流式输出（如有）:")

def stream_cb(chunk: str):
    _safe_print(chunk, end="")

try:
    prov.translate(system_prompt, user_content, stream=True, stream_callback=stream_cb)
except Exception as e:
    print("\n调用失败：", e)

_safe_print("\n\n=== 测试 2：FIM 补全（非流或流） ===")
prov2 = create_provider(
    provider_type="openai",
    api_key=API_KEY,
    base_url="https://api.deepseek.com/beta",
    model_name="deepseek-chat",
    deepseek_beta=True,
    use_fim_completion=True,
)

try:
    # FIM 测试先尝试一次性获取
    out = prov2.translate(system_prompt, user_content)
    _safe_print("输出（一次性）:\n" + str(out))
except Exception as e:
    print("调用失败：", e)

_safe_print("\n测试完成。请检查上方输出以判断 prefix / FIM / streaming 行为是否符合预期。")

# 写入文件（UTF-8）
try:
    with open(OUT_PATH, 'w', encoding='utf-8') as wf:
        wf.writelines(_out_lines)
    _safe_print(f"结果已写入: {OUT_PATH}")
except Exception as e:
    _safe_print("写入结果文件失败：", e)
