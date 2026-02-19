import json
import os
import sys
import time
import hashlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from novel_translator.engine import TranslationConfig, TranslatorEngine

API_KEY = os.environ.get("TRANSLATOR_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: missing TRANSLATOR_API_KEY")
    raise SystemExit(1)

INPUT_FILE = r"E:\Download\jp.絮叨的我和冷淡的你.epub"
OUTPUT_FILE = os.path.join(ROOT, "audit_beta_first3_output.txt")
AUDIT_FILE = os.path.join(ROOT, "audit_beta_first3_model_io.json")

cfg = TranslationConfig(
    provider="openai",
    api_key=API_KEY,
    base_url="https://api.deepseek.com/beta",
    model_name="deepseek-chat",
    model_type="auto",
    temperature=0.7,
    top_p=0.9,
    frequency_penalty=0.1,
    presence_penalty=0.0,
    max_tokens=8192,
    chunk_size=0,
    concurrent_workers=1,
    retry_count=3,
    input_file=INPUT_FILE,
    output_file=OUTPUT_FILE,
    output_format="txt",
    glossary_file="",
    start_chapter=1,
    end_chapter=3,
    custom_prompt="",
    enable_checkpoint=False,
    context_lines=6,
    few_shot_examples="",
    deepseek_beta=True,
    use_prefix_completion=True,
    use_fim_completion=False,
    stream_logs=False,
)

engine = TranslatorEngine(cfg)
model_io = []


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _preview(text: str, limit: int = 600) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    return t[:limit]


orig_init_provider = engine._init_provider


def patched_init_provider():
    orig_init_provider()
    original_translate = engine.provider.translate

    def traced_translate(system_prompt, user_content, assistant_prefix=None, **kwargs):
        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stream": bool(kwargs.get("stream", False)),
            "system_prompt_len": len(system_prompt or ""),
            "system_prompt_sha256": _sha256(system_prompt or ""),
            "system_prompt_preview": _preview(system_prompt or "", 800),
            "assistant_prefix_len": len(assistant_prefix or ""),
            "assistant_prefix_preview": _preview(assistant_prefix or "", 200),
            "user_content_len": len(user_content or ""),
            "user_content_preview": _preview(user_content or "", 1000),
        }
        try:
            out = original_translate(
                system_prompt, user_content, assistant_prefix=assistant_prefix, **kwargs
            )
            record["output_len"] = len(out or "")
            record["output_preview"] = _preview(out or "", 1200)
            model_io.append(record)
            return out
        except Exception as exc:
            record["error"] = str(exc)
            model_io.append(record)
            raise

    engine.provider.translate = traced_translate


engine._init_provider = patched_init_provider
engine.on_log = lambda msg: print(msg)

thread = engine.start_translation()
thread.join()

output_text = ""
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        output_text = f.read()

payload = {
    "config": {
        "input_file": INPUT_FILE,
        "output_file": OUTPUT_FILE,
        "start_chapter": cfg.start_chapter,
        "end_chapter": cfg.end_chapter,
        "deepseek_beta": cfg.deepseek_beta,
        "use_prefix_completion": cfg.use_prefix_completion,
        "use_fim_completion": cfg.use_fim_completion,
    },
    "model_io_count": len(model_io),
    "model_io": model_io,
    "output_exists": os.path.exists(OUTPUT_FILE),
    "output_len": len(output_text),
    "output_preview": _preview(output_text, 2500),
}

with open(AUDIT_FILE, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print(f"AUDIT_FILE={AUDIT_FILE}")
print(f"OUTPUT_FILE={OUTPUT_FILE}")
print(f"MODEL_IO_COUNT={len(model_io)}")
print(f"OUTPUT_LEN={len(output_text)}")
