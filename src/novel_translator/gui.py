# -*- coding: utf-8 -*-
"""
novel_translator.gui - Flet 图形界面

功能:
- 多 AI 提供商支持 (OpenAI兼容 / Anthropic / Google / Ollama)
- 跟随系统主题 (浅色/深色/自动)
- 翻译预设按模型分类
- 模型参数可折叠面板
- 断点续传 & 断点恢复
- 术语表管理
- 翻译修复 (质量扫描 + 选择性重翻)
- 整章翻译 & 上下文注入
- EPUB 结构/样式保留
"""

import os
import time
import json
import threading
import re

import flet as ft

from novel_translator import __version__
from novel_translator.engine import TranslatorEngine, TranslationConfig
from novel_translator.providers import (
    PROVIDER_PRESETS, get_provider_names, get_provider_models,
    get_provider_default_url, get_provider_default_model,
)
from novel_translator.downloader import download_with_site, SITE_HANDLERS

APP_TITLE = "轻小说翻译器"
APP_VERSION = __version__
CONFIG_FILE = "translator_config.json"
HISTORY_FILE = "translator_history.json"

# ===== 预设模型（动态生成 + 自定义） =====
def _build_preset_models(provider_key: str = "openai") -> list:
    """根据 Provider 生成预设模型列表"""
    models = get_provider_models(provider_key)
    result = [{"name": m["name"], "model": m["model"], "url": m["url"]} for m in models]
    result.append({"name": "自定义", "model": "", "url": ""})
    return result

PRESET_MODELS = _build_preset_models("openai")

# ===== 翻译预设 (按模型分类) =====
STYLE_CATEGORIES = {
    "DeepSeek 调优": {
        "经典风格 (推荐)": {
            "desc": "严格忠实原文、禁止添加语气词、流畅合并短句",
            "temperature": 1.1,
            "top_p": 0.9,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.0,
            "prompt": (
                "你是一位精通中日文化的专业轻小说翻译专家。"
                "请将用户输入的日文异世界转生小说片段翻译成流畅、地道的中文。\n\n"
                "核心翻译原则：\n"
                "1. 严格忠实原文：准确传达原文含义，不增加、不删减、不改写任何内容。原文没有的语气、情绪、语气词绝对不能添加。\n"
                "2. 禁止添加语气词：不得自行添加原文中不存在的\u201c呀\u201d\u201c呢\u201d\u201c嘛\u201d\u201c哦\u201d\u201c啦\u201d\u201c哟\u201d\u201c呃\u201d等语气词。"
                "只有原文明确包含对应的日文语气词（如「ね」「よ」「さ」「ぞ」「な」等）时，才可以翻译为相应的中文语气词。\n"
                "3. 克制\u201c吧\u201d的使用：\u201c吧\u201d只在原文明确表达推测、建议、请求语气时使用，陈述句中不得滥用。\n"
                "4. 本土化表达：使用简洁、符合中文书面语习惯的自然语句，避免日式直译和机翻腔调。\n"
                "5. 异世界氛围：完整保留专有名词、魔法体系、等级制度等世界观元素。\n"
                "6. 角色语气：保留原文角色的说话风格，但不要过度演绎或夸张化。\n"
                "7. 段落与断句：对话使用「」或\u201c\u201d。原文中语意连贯的相邻短句应合并为流畅的长句，不要逐句机械断行；"
                "仅在话题转换、场景切换或原文明确分段处另起新段。\n"
                "8. 术语统一：严格遵守术语表中的译名。\n"
                "9. 语体适配：第一人称内心独白和日常对话使用现代口语体，禁用文言或过度书面化措辞"
                "（如\u201c何以见得\u201d\u201c有何贵干\u201d\u201c愿闻其详\u201d等）。仅在原文使用正式/古风语体的角色台词中方可使用对应文体。\n"
                "10. 时态准确：阐述世界观设定和一般性规则时使用一般时态，不要误用完成时态\u201c了\u201d。叙述已发生事件时正常使用。\n"
                "11. 禁止添词：不得添加原文中没有的名词、量词或修饰语。日文拟态词（如ヌラヌラ、ネットリ等）"
                "应译为对应感觉的中文表达，不可擅自补充具体名词。\n"
                "12. 纯净输出：只输出翻译正文，严禁输出翻译注释、译者注、脚注、说明文字、括号补充解释。\n"
                "13. 术语前后一致：同一专有名词全文必须使用完全相同的译名和标记格式。\n"
                "14. 标记统一：专有名词一律使用「」标记，不得混用『』《》【】等。\n\n"
                "翻译预设：简洁准确，紧贴原文，语意连贯的短句合并为流畅长句，不添加原文没有的修辞和语气。\n"
            ),
        },
        "忠实流畅": {
            "desc": "在忠实原文基础上强调中文流畅度，适合 DeepSeek 系列",
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.0,
            "prompt": (
                "你是一位精通中日文化的专业轻小说翻译专家。"
                "请将用户输入的日文异世界转生小说片段翻译成流畅、地道的中文。\n\n"
                "核心翻译原则：\n"
                "1. 严格忠实原文：准确传达原文含义，不增加、不删减、不改写任何内容。原文没有的语气、情绪、语气词绝对不能添加。\n"
                "2. 禁止添加语气词：不得自行添加原文中不存在的\u201c呀\u201d\u201c呢\u201d\u201c嘛\u201d\u201c哦\u201d\u201c啦\u201d\u201c哟\u201d\u201c呃\u201d等语气词。"
                "只有原文明确包含对应的日文语气词（如「ね」「よ」「さ」「ぞ」「な」等）时，才可以翻译为相应的中文语气词。\n"
                "3. 克制\u201c吧\u201d的使用：\u201c吧\u201d只在原文明确表达推测、建议、请求语气时使用，陈述句中不得滥用。\n"
                "4. 本土化表达：使用简洁、符合中文书面语习惯的自然语句，避免日式直译和机翻腔调。\n"
                "5. 异世界氛围：完整保留专有名词、魔法体系、等级制度等世界观元素。\n"
                "6. 角色语气：保留原文角色的说话风格，但不要过度演绎或夸张化。\n"
                "7. 段落与断句：对话使用「」或\u201c\u201d。原文中语意连贯的相邻短句应合并为流畅的长句，不要逐句机械断行；"
                "仅在话题转换、场景切换或原文明确分段处另起新段。\n"
                "8. 术语统一：严格遵守术语表中的译名。\n"
                "9. 语体适配：第一人称内心独白和日常对话使用现代口语体，禁用文言或过度书面化措辞"
                "（如\u201c何以见得\u201d\u201c有何贵干\u201d\u201c愿闻其详\u201d等）。仅在原文使用正式/古风语体的角色台词中方可使用对应文体。\n"
                "10. 时态准确：阐述世界观设定和一般性规则时使用一般时态，不要误用完成时态\u201c了\u201d。叙述已发生事件时正常使用。\n"
                "11. 禁止添词：不得添加原文中没有的名词、量词或修饰语。日文拟态词（如ヌラヌラ、ネットリ等）"
                "应译为对应感觉的中文表达，不可擅自补充具体名词。\n"
                "12. 纯净输出：只输出翻译正文，严禁输出翻译注释、译者注、脚注、说明文字、括号补充解释。\n"
                "13. 术语前后一致：同一专有名词全文必须使用完全相同的译名和标记格式。\n"
                "14. 标记统一：专有名词一律使用「」标记，不得混用『』《》【】等。\n\n"
                "翻译预设：简洁准确，紧贴原文，语意连贯的短句合并为流畅长句，不添加原文没有的修辞和语气。\n"
            ),
        },
    },
    "自定义": {
        "自定义": {
            "desc": "使用自定义提示词，完全控制翻译预设",
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.0,
            "prompt": "",
        },
    },
}

# 展平风格，方便快速查找
FLAT_STYLES = {}
for _cat, _styles in STYLE_CATEGORIES.items():
    for _name, _preset in _styles.items():
        FLAT_STYLES[_name] = _preset

# ===== 参数提示文本 =====
TOOLTIPS = {
    "temperature": "控制翻译的创造性/随机性。\n• 低 (0.3-0.5): 严格直译，用词保守\n• 中 (0.6-0.8): 平衡忠实与流畅\n• 高 (0.9-1.2): 更自由灵活，但可能偏离原文",
    "top_p": "核采样参数，控制词汇选择范围。\n• 低 (0.7-0.85): 用词更精确集中\n• 中 (0.85-0.95): 平衡多样性\n• 高 (0.95-1.0): 用词更丰富多样",
    "frequency_penalty": "频率惩罚，抑制重复用词和句式。\n• 0: 不惩罚\n• 0.1: 轻微抑制重复 (翻译推荐)\n• 0.3+: 强力去重复，可能影响准确性\n范围 -2.0 ~ 2.0",
    "presence_penalty": "存在惩罚，鼓励引入新话题/词汇。\n• 0: 不惩罚 (直译推荐)\n• 0.05-0.1: 轻微鼓励多样表达\n• 0.3+: 强力引入新词，可能偏离原文\n范围 -2.0 ~ 2.0",
    "max_tokens": "单次 API 调用返回的最大 token 数。\n设置过小可能截断长段翻译。\n推荐 4096~8192。",
    "chunk_size": "每次发送给 API 的原文字符数。\n• 0 (整章模式): 整章一次性翻译，一致性最佳\n• 小 (800-1200): 翻译精度高但速度慢\n• 中 (1500): 平衡精度与速度 (推荐)\n• 大 (2000+): 速度快但后半段质量可能衰减",
    "context_lines": "前文上下文注入行数。\n将上一段翻译结果的最后 N 行注入到下一段的请求中，\n帮助模型保持人名、称谓的前后一致。\n• 0: 关闭\n• 3-5: 推荐（几乎不增加成本）\n• 8+: 更多上下文但增加 token 消耗",
    "concurrent": "同时进行翻译的线程数。\n• 1: 最稳定，不会触发限速\n• 2-4: 适度加速\n• 8+: 需要 API 配额支持",
    "checkpoint": "开启后翻译进度会实时保存。\n中断后再次开始会自动跳过已完成章节。\n更改提示词或参数不影响已翻译内容。",
    "format": "输出文件格式。\n• TXT: 纯文本，兼容性最好\n• EPUB: 电子书格式，带章节目录",
}


# ===== 工具函数 =====

def _load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_history(hist):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _add_to_history(hist, key, value, max_items=10):
    lst = hist.get(key, [])
    val_str = str(value)
    if val_str in lst:
        lst.remove(val_str)
    lst.insert(0, val_str)
    hist[key] = lst[:max_items]


def _strip_leading_xx_prefix(stem: str) -> str:
    """Remove short serial prefixes like '01.', 'AB-', 'Vol.1-' in default output names."""
    if not stem:
        return stem
    s = stem.strip()
    for _ in range(3):
        new_s = re.sub(r"^\s*[A-Za-z0-9]{1,12}[.\-_\s、．。]+", "", s)
        if new_s == s:
            break
        s = new_s.strip()
    return s or stem


def _default_output_name_for_input(input_path: str, fmt: str) -> str:
    ext = ".epub" if (fmt or "").lower() == "epub" else ".txt"
    in_dir = os.path.dirname(input_path or "")
    in_stem = os.path.splitext(os.path.basename(input_path or ""))[0]
    clean_stem = _strip_leading_xx_prefix(in_stem) or "novel"
    # 添加前缀zh_以标识这是中文翻译
    prefixed_stem = f"zh_{clean_stem}"
    return os.path.join(in_dir, f"{prefixed_stem}{ext}")


def _fallback_output_filename(fmt: str) -> str:
    ext = ".epub" if (fmt or "").lower() == "epub" else ".txt"
    return f"novel{ext}"


# =========================================================
# 主界面
# =========================================================
def main(page: ft.Page):
    page.title = APP_TITLE
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.padding = 0
    page.window.width = 1200
    page.window.height = 880
    page.window.min_width = 960
    page.window.min_height = 720

    seed_color = ft.Colors.DEEP_PURPLE
    page.theme = ft.Theme(color_scheme_seed=seed_color)
    page.dark_theme = ft.Theme(color_scheme_seed=seed_color)

    saved = _load_config()
    history = _load_history()
    engine_ref = {"engine": None}

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    # ===== 辅助函数 =====
    def show_snackbar(msg):
        page.overlay[:] = [c for c in page.overlay if not isinstance(c, ft.SnackBar)]
        sb = ft.SnackBar(content=ft.Text(msg))
        sb.open = True
        page.overlay.append(sb)
        page.update()

    def add_log(msg):
        ts = time.strftime("%H:%M:%S")
        color = ft.Colors.ERROR if ("❌" in msg or "失败" in msg or "出错" in msg) else None
        log_list.controls.append(ft.Text(f"[{ts}] {msg}", size=12, selectable=True, color=color))
        if len(log_list.controls) > 500:
            log_list.controls.pop(0)
        try:
            page.update()
        except Exception:
            pass

    def get_config():
        cfg = TranslationConfig()
        fmt = (format_dropdown.value or "TXT").lower()
        cfg.provider = provider_dropdown.value or "openai"
        cfg.api_key = api_key_field.value or ""
        cfg.base_url = api_url_field.value or "https://api.siliconflow.cn/v1"
        cfg.model_name = model_field.value or "deepseek-ai/DeepSeek-V3.2"
        cfg.model_type = model_type_dropdown.value or "auto"
        cfg.temperature = float(temp_slider.value)
        cfg.top_p = float(topp_slider.value)
        cfg.frequency_penalty = float(freq_penalty_slider.value)
        cfg.presence_penalty = float(pres_penalty_slider.value)
        cfg.max_tokens = int(max_tokens_field.value or 8192)
        cfg.chunk_size = int(chunk_size_field.value or 1500)
        if whole_chapter_switch.value:
            cfg.chunk_size = 0
        cfg.context_lines = int(context_slider.value)
        cfg.concurrent_workers = int(concurrent_slider.value)
        cfg.input_file = input_file_field.value or ""
        cfg.output_file = output_file_field.value or _fallback_output_filename(fmt)
        cfg.glossary_file = glossary_file_field.value or ""
        cfg.enable_checkpoint = checkpoint_switch.value
        cfg.output_format = fmt
        style_name = style_dropdown.value or "经典风格 (推荐)"
        if style_name == "自定义":
            cfg.custom_prompt = custom_prompt_field.value or ""
        else:
            preset = FLAT_STYLES.get(style_name, {})
            cfg.custom_prompt = preset.get("prompt", "")
        cfg.few_shot_examples = few_shot_field.value or ""
        cfg.deepseek_beta = deepseek_beta_switch.value
        cfg.use_prefix_completion = prefix_completion_switch.value
        cfg.use_fim_completion = fim_completion_switch.value
        # 流式日志选项
        try:
            cfg.stream_logs = stream_logs_switch.value
        except Exception:
            cfg.stream_logs = False
        try:
            cfg.start_chapter = int(start_chapter_field.value or 0)
        except ValueError:
            cfg.start_chapter = 0
        try:
            cfg.end_chapter = int(end_chapter_field.value or 0)
        except ValueError:
            cfg.end_chapter = 0
        return cfg

    def save_ui_config():
        _save_config({
            "provider": provider_dropdown.value,
            "api_key": api_key_field.value,
            "base_url": api_url_field.value,
            "model_name": model_field.value,
            "model_type": model_type_dropdown.value,
            "temperature": temp_slider.value,
            "top_p": topp_slider.value,
            "frequency_penalty": freq_penalty_slider.value,
            "presence_penalty": pres_penalty_slider.value,
            "max_tokens": max_tokens_field.value,
            "chunk_size": chunk_size_field.value,
            "whole_chapter": whole_chapter_switch.value,
            "context_lines": context_slider.value,
            "concurrent_workers": concurrent_slider.value,
            "output_file": output_file_field.value,
            "glossary_file": glossary_file_field.value,
            "output_format": format_dropdown.value,
            "style_preset": style_dropdown.value,
            "custom_prompt": custom_prompt_field.value,
            "few_shot_examples": few_shot_field.value,
            "deepseek_beta": deepseek_beta_switch.value,
            "use_prefix_completion": prefix_completion_switch.value,
            "use_fim_completion": fim_completion_switch.value,
            "stream_logs": stream_logs_switch.value,
        })

    def save_params_to_history():
        _add_to_history(history, "temperatures", f"{temp_slider.value:.2f}")
        _add_to_history(history, "top_ps", f"{topp_slider.value:.2f}")
        _add_to_history(history, "freq_penalties", f"{freq_penalty_slider.value:.2f}")
        _add_to_history(history, "pres_penalties", f"{pres_penalty_slider.value:.2f}")
        _add_to_history(history, "chunk_sizes", chunk_size_field.value)
        _add_to_history(history, "max_tokens_list", max_tokens_field.value)
        if custom_prompt_field.value and custom_prompt_field.value.strip():
            _add_to_history(history, "custom_prompts", custom_prompt_field.value.strip(), max_items=5)
        _save_history(history)

    def update_progress(progress):
        if progress.total_chapters > 0:
            pct = progress.current_chapter / progress.total_chapters
            progress_bar.value = pct
            progress_text.value = f"{progress.current_chapter}/{progress.total_chapters} 章"
            elapsed = time.time() - progress.start_time
            if elapsed > 0 and progress.current_chapter > 0:
                speed = progress.translated_chars / elapsed
                remaining = progress.total_chapters - progress.current_chapter
                eta = (elapsed / progress.current_chapter) * remaining
                speed_text.value = f"{speed:.0f} 字/秒 | 已用 {elapsed:.0f}s | 剩余 ~{eta:.0f}s"
        try:
            page.update()
        except Exception:
            pass

    def on_complete(progress):
        start_btn.disabled = False
        resume_btn.disabled = True
        pause_btn.disabled = True
        cancel_btn.disabled = True
        
        # 检查输出文件是否存在
        cfg = get_config()
        output_exists = os.path.exists(cfg.output_file) and os.path.getsize(cfg.output_file) > 0
        
        if output_exists and progress.translated_chars > 0:
            progress_bar.value = 1.0
            progress_text.value = "✅ 翻译完成!"
            show_snackbar(f"✅ 翻译完成！用时 {progress.elapsed_time:.1f} 秒")
        else:
            # 文件不存在或为空，或者没有翻译任何字符
            progress_bar.value = 0.0
            progress_text.value = "⚠️ 翻译完成但无输出"
            show_snackbar("⚠️ 翻译完成但未生成有效输出文件")
        
        try:
            page.update()
        except Exception:
            pass

    # ===== 事件处理 =====
    def on_theme_toggle(e):
        modes = [ft.ThemeMode.SYSTEM, ft.ThemeMode.LIGHT, ft.ThemeMode.DARK]
        icons = [ft.Icons.BRIGHTNESS_AUTO, ft.Icons.LIGHT_MODE, ft.Icons.DARK_MODE]
        labels = ["跟随系统", "浅色", "深色"]
        cur = modes.index(page.theme_mode) if page.theme_mode in modes else 0
        nxt = (cur + 1) % len(modes)
        page.theme_mode = modes[nxt]
        theme_btn.icon = icons[nxt]
        theme_btn.tooltip = labels[nxt]
        page.update()

    # 预设芯片已移除（不再提供快速预设按钮）

    def on_provider_change(e):
        """Provider 切换时更新默认 URL、模型名和预设列表"""
        nonlocal PRESET_MODELS
        provider_key = provider_dropdown.value or "openai"
        default_url = get_provider_default_url(provider_key)
        default_model = get_provider_default_model(provider_key)
        api_url_field.value = default_url
        model_field.value = default_model
        # 预设按钮已移除，保留模型与 URL 自动填充逻辑
        PRESET_MODELS = _build_preset_models(provider_key)
        # Ollama 不需要 API Key
        if provider_key == "ollama":
            api_key_field.hint_text = "Ollama 本地模式，API Key 可留空"
        else:
            api_key_field.hint_text = None
        page.update()
        save_ui_config()

    async def on_pick_input(e):
        try:
            files = await file_picker.pick_files(allowed_extensions=["epub"], dialog_title="选择 EPUB 文件")
        except Exception as ex:
            show_snackbar(f"❌ 文件选择失败: {ex}")
            return
        if files:
            path = files[0].path
            input_file_field.value = path
            fmt = (format_dropdown.value or "TXT").lower()
            output_file_field.value = _default_output_name_for_input(path, fmt)
            page.update()
            _load_chapters()
            save_ui_config()

    async def on_pick_output_dir(e):
        try:
            path = await file_picker.get_directory_path(dialog_title="选择输出目录")
        except Exception as ex:
            show_snackbar(f"❌ 目录选择失败: {ex}")
            return
        if path:
            fmt = (format_dropdown.value or "TXT").lower()
            fname = os.path.basename(output_file_field.value or _fallback_output_filename(fmt))
            output_file_field.value = os.path.join(path, fname)
            page.update()
            save_ui_config()

    async def on_pick_glossary(e):
        try:
            files = await file_picker.pick_files(allowed_extensions=["json"], dialog_title="选择术语表 JSON")
        except Exception as ex:
            show_snackbar(f"❌ 文件选择失败: {ex}")
            return
        if files:
            glossary_file_field.value = files[0].path
            page.update()
            _load_glossary_preview()
            save_ui_config()

    def _load_chapters():
        try:
            cfg = get_config()
            eng = TranslatorEngine(cfg)
            chapters = eng.get_chapters()
            chapter_info_text.value = f"共 {len(chapters)} 个有效章节"
            total_chapters = len(chapters)
            end_chapter_field.value = str(total_chapters)
            
            # 校正超出范围的章节号
            start_val = 1
            if start_chapter_field.value and start_chapter_field.value != "0":
                try:
                    start_val = int(start_chapter_field.value)
                except ValueError:
                    start_val = 1
            
            # 修正超出范围的起始章节号
            if start_val < 1:
                start_val = 1
            elif start_val > total_chapters and total_chapters > 0:
                start_val = total_chapters
                show_snackbar(f"⚠️ 起始章节超出范围，已修正为 {total_chapters}")
                
            start_chapter_field.value = str(start_val)
            
            # 修正结束章节号
            end_val = total_chapters
            if end_chapter_field.value and end_chapter_field.value != "0":
                try:
                    end_val = int(end_chapter_field.value)
                except ValueError:
                    end_val = total_chapters
            
            if end_val > total_chapters:
                end_val = total_chapters
                show_snackbar(f"⚠️ 结束章节超出范围，已修正为 {total_chapters}")
            elif end_val < start_val and start_val > 0:
                end_val = total_chapters  # 重置为最大值
                show_snackbar(f"⚠️ 结束章节小于起始章节，已重置为最大值")
                
            end_chapter_field.value = str(end_val)
            
            page.update()
        except Exception as ex:
            chapter_info_text.value = f"读取失败: {ex}"
            page.update()

    def _load_glossary_preview():
        path = glossary_file_field.value
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            glossary_table.rows.clear()
            for k, v in list(data.items())[:100]:
                glossary_table.rows.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(k, size=12)),
                    ft.DataCell(ft.Text(v, size=12)),
                ]))
            glossary_count.value = f"共 {len(data)} 条 (显示前100条)"
            page.update()
        except Exception as ex:
            glossary_count.value = f"加载失败: {ex}"
            page.update()

    def on_format_change(e):
        fmt = (format_dropdown.value or "TXT").lower()
        ext = ".epub" if fmt == "epub" else ".txt"
        cur = output_file_field.value or ""
        if cur:
            base = os.path.splitext(cur)[0]
            output_file_field.value = base + ext
        page.update()
        save_ui_config()

    def on_style_change(e):
        name = style_dropdown.value or "经典风格 (推荐)"
        if name.startswith("__header__"):
            style_dropdown.value = "经典风格 (推荐)"
            name = "经典风格 (推荐)"
        preset = FLAT_STYLES.get(name, {})
        style_desc.value = preset.get("desc", "")
        if name == "自定义":
            custom_prompt_container.visible = True
            history_prompt_container.visible = bool(history.get("custom_prompts"))
        else:
            custom_prompt_container.visible = False
            history_prompt_container.visible = False
            temp_slider.value = preset.get("temperature", 0.7)
            topp_slider.value = preset.get("top_p", 0.9)
            freq_penalty_slider.value = preset.get("frequency_penalty", 0.1)
            pres_penalty_slider.value = preset.get("presence_penalty", 0.0)
            temp_label.value = f"Temperature: {temp_slider.value:.2f}"
            topp_label.value = f"Top-p: {topp_slider.value:.2f}"
            freq_penalty_label.value = f"Freq Penalty: {freq_penalty_slider.value:.2f}"
            pres_penalty_label.value = f"Pres Penalty: {pres_penalty_slider.value:.2f}"
        page.update()
        save_ui_config()

    def on_history_prompt_click(e):
        custom_prompt_field.value = e.control.data
        page.update()

    def on_test_api(e):
        test_btn.disabled = True
        test_btn.text = "测试中..."
        page.update()
        cfg = get_config()
        eng = TranslatorEngine(cfg)
        eng.on_log = add_log
        ok, msg = eng.test_api_connection()
        test_btn.disabled = False
        test_btn.text = "测试连接"
        show_snackbar(f"{'✅' if ok else '❌'} {msg}")
        page.update()

    def on_start(e):
        cfg = get_config()
        if not cfg.api_key:
            show_snackbar("❌ 请先填写 API Key")
            return
        if not cfg.input_file or not os.path.exists(cfg.input_file):
            show_snackbar("❌ 请先选择输入文件")
            return

        save_ui_config()
        save_params_to_history()

        eng = TranslatorEngine(cfg)
        eng.on_progress = update_progress
        eng.on_log = add_log
        eng.on_error = lambda msg: None
        eng.on_complete = on_complete
        engine_ref["engine"] = eng

        log_list.controls.clear()
        progress_bar.value = 0
        progress_text.value = "准备中..."
        speed_text.value = ""
        start_btn.disabled = True
        pause_btn.disabled = False
        resume_btn.disabled = True
        cancel_btn.disabled = False
        page.update()

        eng.start_translation()

    def on_pause(e):
        eng = engine_ref.get("engine")
        if eng and eng.progress.is_running:
            eng.pause()
            pause_btn.disabled = True
            resume_btn.disabled = False
            page.update()

    def on_resume(e):
        eng = engine_ref.get("engine")
        if eng and eng.progress.is_paused:
            eng.resume()
            pause_btn.disabled = False
            resume_btn.disabled = True
            page.update()

    def on_cancel(e):
        eng = engine_ref.get("engine")
        if eng:
            eng.cancel()
            start_btn.disabled = False
            pause_btn.disabled = True
            resume_btn.disabled = True
            cancel_btn.disabled = True
            page.update()

    def on_clear_checkpoint(e):
        cfg = get_config()
        if cfg.input_file and cfg.output_file:
            TranslatorEngine.clear_checkpoint(cfg.output_file, cfg.input_file)
            show_snackbar("🗑️ 断点记录已清除")

    # 滑块
    def on_temp_change(e):
        temp_label.value = f"Temperature: {temp_slider.value:.2f}"
        page.update()

    def on_topp_change(e):
        topp_label.value = f"Top-p: {topp_slider.value:.2f}"
        page.update()

    def on_freq_penalty_change(e):
        freq_penalty_label.value = f"Freq Penalty: {freq_penalty_slider.value:.2f}"
        page.update()

    def on_pres_penalty_change(e):
        pres_penalty_label.value = f"Pres Penalty: {pres_penalty_slider.value:.2f}"
        page.update()

    def on_concurrent_change(e):
        concurrent_label.value = f"并发线程: {int(concurrent_slider.value)}"
        page.update()

    def on_context_change(e):
        v = int(context_slider.value)
        context_label.value = f"上下文注入: {v} 行" if v > 0 else "上下文注入: 关闭"
        page.update()

    def on_whole_chapter_toggle(e):
        if whole_chapter_switch.value:
            chunk_size_field.disabled = True
            chunk_size_field.value = "0"
        else:
            chunk_size_field.disabled = False
            chunk_size_field.value = str(saved.get("chunk_size", "1500"))
        page.update()
        save_ui_config()

    def _on_field_blur(e):
        save_ui_config()

    def on_add_term(e):
        if not add_jp.value or not add_cn.value:
            return
        path = glossary_file_field.value
        if not path:
            path = "glossary_custom.json"
            glossary_file_field.value = path
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        data[add_jp.value] = add_cn.value
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        add_jp.value = ""
        add_cn.value = ""
        _load_glossary_preview()
        page.update()

    # ===== 断点恢复事件 =====
    async def on_pick_checkpoint(e):
        try:
            files = await file_picker.pick_files(
                allowed_extensions=["json"],
                dialog_title="选择断点文件 (.checkpoint.json)",
            )
        except Exception as ex:
            show_snackbar(f"❌ 文件选择失败: {ex}")
            return
        if files:
            cp_path = files[0].path
            cp_file_field.value = cp_path
            info = TranslatorEngine.load_checkpoint_info(cp_path)
            if info:
                completed, _ = info
                cp_info_text.value = f"✅ 已找到 {len(completed)} 个已翻译章节"
                cp_info_text.color = ft.Colors.PRIMARY
                cp_restore_btn.disabled = False
            else:
                cp_info_text.value = "❌ 无法读取断点文件"
                cp_info_text.color = ft.Colors.ERROR
                cp_restore_btn.disabled = True
            page.update()

    async def on_pick_cp_source(e):
        try:
            files = await file_picker.pick_files(allowed_extensions=["epub"], dialog_title="选择源 EPUB（可选）")
        except Exception as ex:
            show_snackbar(f"❌ 文件选择失败: {ex}")
            return
        if files:
            cp_source_field.value = files[0].path
            page.update()

    def on_restore_checkpoint(e):
        cp_path = cp_file_field.value
        if not cp_path or not os.path.exists(cp_path):
            show_snackbar("❌ 请先选择断点文件")
            return

        out_fmt = (cp_format_dropdown.value or "EPUB").lower()
        base = os.path.splitext(cp_path)[0]
        if base.endswith(".checkpoint"):
            base = base[: -len(".checkpoint")]
        ext = ".epub" if out_fmt == "epub" else ".txt"
        out_path = base + ext

        cfg = TranslationConfig()
        cfg.input_file = cp_source_field.value or ""
        eng = TranslatorEngine(cfg)
        eng.on_log = add_log

        cp_restore_btn.disabled = True
        cp_restore_btn.text = "恢复中..."
        page.update()

        def _do_restore():
            ok = eng.restore_from_checkpoint(cp_path, out_path, out_fmt)
            cp_restore_btn.disabled = False
            cp_restore_btn.text = "生成文件"
            if ok:
                show_snackbar(f"✅ 已生成: {out_path}")
            else:
                show_snackbar("❌ 恢复失败，请查看日志")
            try:
                page.update()
            except Exception:
                pass

        threading.Thread(target=_do_restore, daemon=True).start()

    # ===== 翻译修复事件 =====
    fix_scan_results = {}
    fix_selected_chapters = set()

    async def on_pick_fix_checkpoint(e):
        try:
            files = await file_picker.pick_files(
                allowed_extensions=["json"],
                dialog_title="选择断点文件 (.checkpoint.json)",
            )
        except Exception as ex:
            show_snackbar(f"❌ 文件选择失败: {ex}")
            return
        if files:
            path = files[0].path
            fix_cp_field.value = path
            info = TranslatorEngine.load_checkpoint_info(path)
            if info:
                completed, _ = info
                fix_status_text.value = f"✅ 已加载 {len(completed)} 个章节"
                fix_status_text.color = ft.Colors.PRIMARY
                fix_scan_btn.disabled = False
            else:
                fix_status_text.value = "❌ 无法读取断点文件"
                fix_status_text.color = ft.Colors.ERROR
                fix_scan_btn.disabled = True
            fix_retranslate_btn.disabled = True
            fix_result_column.controls.clear()
            fix_scan_results.clear()
            fix_selected_chapters.clear()
            page.update()

    async def on_pick_fix_source(e):
        try:
            files = await file_picker.pick_files(allowed_extensions=["epub"], dialog_title="选择源 EPUB (重翻必需)")
        except Exception as ex:
            show_snackbar(f"❌ 文件选择失败: {ex}")
            return
        if files:
            fix_source_field.value = files[0].path
            page.update()

    def on_quality_scan(e):
        cp_path = fix_cp_field.value
        if not cp_path or not os.path.exists(cp_path):
            show_snackbar("❌ 请先选择断点文件")
            return

        # 从独立的关键词/说明输入栏构建规则字典，至少填写一对
        rules = {}
        try:
            for kf, df in zip(fix_rules_keyword_fields, fix_rules_desc_fields):
                k = (kf.value or "").strip()
                d = (df.value or "").strip()
                if not k:
                    continue
                rules[k] = d or "需修正"
        except Exception:
            rules = {}

        if not rules:
            show_snackbar("❌ 请至少填写一组规则（示例：关键词=前辈，说明=替换为 学姐）")
            return

        cfg = TranslationConfig()
        eng = TranslatorEngine(cfg)
        eng.on_log = add_log
        issues = eng.quality_scan(cp_path, rules)

        fix_scan_results.clear()
        fix_scan_results.update(issues)
        fix_selected_chapters.clear()
        fix_result_column.controls.clear()

        if not issues:
            fix_result_column.controls.append(
                ft.Text("✅ 未发现质量问题！所有章节通过检查。", color=ft.Colors.PRIMARY, size=13)
            )
            fix_retranslate_btn.disabled = True
        else:
            total_issues = sum(len(v) for v in issues.values())
            fix_result_column.controls.append(
                ft.Text(f"⚠️ 发现 {len(issues)} 个章节共 {total_issues} 处问题:", size=13, weight=ft.FontWeight.W_600)
            )
            for ch_name, ch_issues in sorted(issues.items()):
                detail = ", ".join(f'"{kw}"×{cnt}({hint})' for kw, cnt, hint in ch_issues)
                cb = ft.Checkbox(
                    label=f"{ch_name}: {detail}",
                    value=True,
                    data=ch_name,
                    on_change=_on_fix_chapter_toggle,
                )
                fix_result_column.controls.append(cb)
                fix_selected_chapters.add(ch_name)
            fix_retranslate_btn.disabled = False

        add_log(f"🔍 质量扫描完成: {len(issues)} 个章节有问题")
        page.update()

    def _on_fix_chapter_toggle(e):
        ch_name = e.control.data
        if e.control.value:
            fix_selected_chapters.add(ch_name)
        else:
            fix_selected_chapters.discard(ch_name)
        fix_retranslate_btn.disabled = len(fix_selected_chapters) == 0
        page.update()

    def on_fix_select_all(e):
        for ctrl in fix_result_column.controls:
            if isinstance(ctrl, ft.Checkbox):
                ctrl.value = True
                fix_selected_chapters.add(ctrl.data)
        fix_retranslate_btn.disabled = len(fix_selected_chapters) == 0
        page.update()

    def on_fix_select_none(e):
        for ctrl in fix_result_column.controls:
            if isinstance(ctrl, ft.Checkbox):
                ctrl.value = False
        fix_selected_chapters.clear()
        fix_retranslate_btn.disabled = True
        page.update()

    def on_retranslate(e):
        cp_path = fix_cp_field.value
        source_path = fix_source_field.value
        if not cp_path or not os.path.exists(cp_path):
            show_snackbar("❌ 请先选择断点文件")
            return
        if not source_path or not os.path.exists(source_path):
            show_snackbar("❌ 重翻需要选择源 EPUB 文件以获取原文")
            return
        if not fix_selected_chapters:
            show_snackbar("❌ 请至少选中一个章节")
            return

        cfg = get_config()
        cfg.input_file = source_path
        eng = TranslatorEngine(cfg)
        eng.on_log = add_log

        chapters_to_fix = list(fix_selected_chapters)
        fix_retranslate_btn.disabled = True
        fix_retranslate_btn.text = "重翻中..."
        fix_scan_btn.disabled = True
        page.update()

        def _do_retranslate():
            try:
                out_fmt = (fix_out_format.value or "EPUB").lower()
                base = os.path.splitext(cp_path)[0]
                if base.endswith(".checkpoint"):
                    base = base[: -len(".checkpoint")]
                out_path = base + "_fixed" + (".epub" if out_fmt == "epub" else ".txt")

                def _progress_cb(cur, total, ch_name):
                    fix_status_text.value = f"🔄 [{cur}/{total}] {ch_name}"
                    try:
                        page.update()
                    except Exception:
                        pass

                ok = eng.retranslate_chapters(
                    cp_path, chapters_to_fix, output_path=out_path,
                    output_format=out_fmt, on_retranslate_progress=_progress_cb,
                )
                if ok:
                    show_snackbar(f"✅ 重翻完成: {out_path}")
                    fix_status_text.value = f"✅ 重翻完成 ({len(chapters_to_fix)} 章) → {os.path.basename(out_path)}"
                    fix_status_text.color = ft.Colors.PRIMARY
                else:
                    show_snackbar("❌ 重翻失败，请查看日志")
                    fix_status_text.value = "❌ 重翻失败"
                    fix_status_text.color = ft.Colors.ERROR
            except Exception as ex:
                add_log(f"❌ 重翻出错: {ex}")
                import traceback
                add_log(traceback.format_exc())
                fix_status_text.value = f"❌ 出错: {ex}"
                fix_status_text.color = ft.Colors.ERROR
            finally:
                fix_retranslate_btn.disabled = False
                fix_retranslate_btn.text = "重新翻译选中章节"
                fix_scan_btn.disabled = False
                try:
                    page.update()
                except Exception:
                    pass

        threading.Thread(target=_do_retranslate, daemon=True).start()

    # ===== UI 组件 =====

    # ---------- API 配置 ----------
    provider_names = get_provider_names()
    provider_dropdown = ft.Dropdown(
        label="AI 提供商",
        value=saved.get("provider", "openai"),
        options=[ft.dropdown.Option(key=k, text=v) for k, v in provider_names.items()],
        width=200, border_radius=10, filled=True,
        on_select=on_provider_change,
        tooltip="选择 AI 提供商：\nOpenAI 兼容: DeepSeek/Qwen/GPT/SiliconFlow\nAnthropic: Claude\nGoogle: Gemini\nOllama: 本地模型",
    )
    api_key_field = ft.TextField(
        label="API Key", prefix_icon=ft.Icons.KEY,
        password=True, can_reveal_password=True,
        value=saved.get("api_key", ""),
        border_radius=10, filled=True, on_blur=_on_field_blur,
        hint_text="Ollama 本地模式，API Key 可留空" if saved.get("provider") == "ollama" else None,
    )
    api_url_field = ft.TextField(
        label="API 地址", prefix_icon=ft.Icons.LINK,
        value=saved.get("base_url", "https://api.siliconflow.cn/v1"),
        border_radius=10, filled=True, on_blur=_on_field_blur,
    )
    model_field = ft.TextField(
        label="模型名称", prefix_icon=ft.Icons.SMART_TOY,
        value=saved.get("model_name", "deepseek-ai/DeepSeek-V3.2"),
        border_radius=10, filled=True, on_blur=_on_field_blur,
    )
    model_type_dropdown = ft.Dropdown(
        label="模型类型",
        value=saved.get("model_type", "auto"),
        options=[
            ft.dropdown.Option(key="auto", text="自动检测"),
            ft.dropdown.Option(key="chat", text="对话模型 (Chat)"),
            ft.dropdown.Option(key="completion", text="补全模型 (Completion)"),
        ],
        width=195, border_radius=10, filled=True,
        tooltip="自动检测: 优先通过模型名判断，否则探测 API。\n对话模型: 使用 chat.completions API (GPT/DeepSeek/Qwen 等)\n补全模型: 使用 completions API + Few-shot (base 模型)",
        on_select=lambda _: save_ui_config(),
    )
    def update_few_shot_visibility(e=None):
        try:
            few_shot_field.visible = (model_type_dropdown.value == "completion")
        except Exception:
            few_shot_field.visible = False
        try:
            page.update()
        except Exception:
            pass
    def on_model_type_change(e):
        save_ui_config()
        update_few_shot_visibility()
    model_type_dropdown.on_change = on_model_type_change
    few_shot_field = ft.TextField(
        label="Few-shot 示例 (补全模型专用，选填)",
        value=saved.get("few_shot_examples", ""),
        multiline=True, min_lines=2, max_lines=5,
        border_radius=10, filled=True, on_blur=_on_field_blur,
        helper=ft.Text("格式: 【示例1】\n原文: ..\n译文: ..", size=10),
    )
    try:
        few_shot_field.visible = (saved.get("model_type", "auto") == "completion")
    except Exception:
        few_shot_field.visible = False
    # ---- DeepSeek Beta 功能开关 ----
    def on_deepseek_beta_toggle(e):
        """
        启用/禁用 DeepSeek Beta 模式。
        开启后 base_url 自动切换至 https://api.deepseek.com/beta，
        并显示子选项（对话前缀续写 / FIM 补全）。
        关闭时同步重置子选项，防止遗留状态。
        """
        enabled = deepseek_beta_switch.value
        beta_sub_options.visible = enabled
        if not enabled:
            prefix_completion_switch.value = False
            fim_completion_switch.value = False
        page.update()
        save_ui_config()

    deepseek_beta_switch = ft.Switch(
        label="启用 DeepSeek Beta（官方 API 专属）",
        value=saved.get("deepseek_beta", False),
        tooltip=(
            "启用 DeepSeek Beta 功能（需使用官方 DeepSeek API Key）。\n"
            "开启后 base_url 将自动切换至 https://api.deepseek.com/beta，\n"
            "以支持「对话前缀续写」和「FIM 补全」两项 Beta 特性。\n"
            "⚠️ 仅适用于直接调用 deepseek.com 官方 API 的场景，\n"
            "中转站 / SiliconFlow 等第三方服务不支持 Beta 端点。"
        ),
        on_change=on_deepseek_beta_toggle,
    )
    prefix_completion_switch = ft.Switch(
        label="对话前缀续写（Beta）",
        value=saved.get("use_prefix_completion", False),
        tooltip=(
            "【对话前缀续写 Beta】\n"
            "在 messages 末尾注入空的 assistant 前缀消息\n"
            "（{\"role\": \"assistant\", \"content\": \"\", \"prefix\": true}），\n"
            "强制模型从翻译正文直接续写，\n"
            "避免输出「好的，我来翻译」「以下是翻译」等无意义废话前缀。\n\n"
            "术语表通过 system_prompt 中的【强制术语表】区块注入，\n"
            "不会出现在输出中，翻译结果纯净。\n\n"
            "⚠️ 与 FIM 补全互斥，同时开启时以 FIM 补全优先。"
        ),
    )
    fim_completion_switch = ft.Switch(
        label="FIM 补全（Beta）",
        value=saved.get("use_fim_completion", False),
        tooltip=(
            "【FIM 补全 Beta（Fill In the Middle）】\n"
            "将 system_prompt + 原文 + 格式引导作为 prompt 前缀，\n"
            "suffix 留空，模型补全出纯净译文，\n"
            "有效减少输出格式噪声，提高翻译纯净度。\n\n"
            "术语表注入 prompt 前缀的 system_prompt 中，\n"
            "输出中不含术语表，翻译结果纯净。\n\n"
            "⚠️ 仅 deepseek-chat 支持，deepseek-reasoner 不支持。"
        ),
    )
    beta_sub_options = ft.Container(
        content=ft.Column([prefix_completion_switch], spacing=2),
        visible=saved.get("deepseek_beta", False),
        padding=ft.Padding(left=0, right=0, top=0, bottom=0),
    )
    stream_logs_switch = ft.Switch(
        label="启用流式日志输出",
        value=saved.get("stream_logs", False),
        tooltip=(
            "启用后翻译过程将以流式方式回调日志（逐片段/逐 token），\n"
            "可用于实时预览模型输出或将输出展示在日志面板中。\n"
            "注意：流式输出会增加 UI 更新频率，可能影响性能。"
        ),
        on_change=lambda e: save_ui_config(),
    )
    test_btn = ft.FilledTonalButton("测试连接", icon=ft.Icons.WIFI_TETHERING, on_click=on_test_api)

    # 根据保存的 provider 生成预设模型列表（UI 不展示预设按钮）
    _init_provider = saved.get("provider", "openai")
    PRESET_MODELS = _build_preset_models(_init_provider)

    api_card = ft.Card(
        content=ft.Container(
            padding=20,
            content=ft.Column([
                ft.Row([ft.Icon(ft.Icons.SETTINGS, color=ft.Colors.PRIMARY),
                        ft.Text("API 配置", size=17, weight=ft.FontWeight.W_600)], spacing=8),
                ft.Row([provider_dropdown, api_key_field], spacing=8),
                ft.Row([api_url_field, test_btn], spacing=12),
                ft.Row([model_field, model_type_dropdown], spacing=8),
                few_shot_field,
                ft.Divider(height=1),
                ft.Row([
                    ft.Icon(ft.Icons.SCIENCE, color=ft.Colors.SECONDARY, size=16),
                    ft.Text("DeepSeek Beta 功能", size=13, weight=ft.FontWeight.W_500, color=ft.Colors.SECONDARY),
                ], spacing=6),
                deepseek_beta_switch,
                beta_sub_options,
                # 流式开关 UI 移至运行日志面板以便更接近日志查看位置
            ], spacing=10),
        ),
        elevation=2,
    )

    # ---------- 文件设置 ----------
    input_file_field = ft.TextField(
        label="输入文件 (EPUB)", prefix_icon=ft.Icons.BOOK,
        read_only=True, border_radius=10, filled=True, expand=True,
    )
    output_file_field = ft.TextField(
        label="输出文件", prefix_icon=ft.Icons.SAVE_ALT,
        value=saved.get("output_file", "novel.txt"),
        border_radius=10, filled=True, expand=True, on_blur=_on_field_blur,
    )
    format_dropdown = ft.Dropdown(
        label="格式",
        value=saved.get("output_format", "TXT"),
        options=[ft.dropdown.Option("TXT"), ft.dropdown.Option("EPUB")],
        width=110, border_radius=10, filled=True,
        on_select=on_format_change,
        tooltip=TOOLTIPS["format"],
    )

    file_card = ft.Card(
        content=ft.Container(
            padding=20,
            content=ft.Column([
                ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN, color=ft.Colors.PRIMARY),
                        ft.Text("文件设置", size=17, weight=ft.FontWeight.W_600)], spacing=8),
                ft.Row([input_file_field,
                        ft.FilledTonalButton("选择文件", icon=ft.Icons.UPLOAD_FILE, on_click=on_pick_input)],
                       spacing=8),
                ft.Row([output_file_field, format_dropdown,
                        ft.FilledTonalButton("选择目录", icon=ft.Icons.FOLDER, on_click=on_pick_output_dir)],
                       spacing=8),
            ], spacing=10),
        ),
        elevation=2,
    )

    # ---------- 下载器（通过 URL） ----------
    url_field = ft.TextField(label="章节 URL", prefix_icon=ft.Icons.LINK, expand=True)
    site_options = [ft.dropdown.Option(k) for k in sorted(list(SITE_HANDLERS.keys()))]
    if not site_options:
        site_options = [ft.dropdown.Option("generic")]
    site_dropdown = ft.Dropdown(label="站点", value=site_options[0].key, options=site_options, width=200)
    selector_field = ft.TextField(label="内容选择器 (可选)", hint_text="例如: div.chapter-content", expand=True)
    title_selector_field = ft.TextField(label="标题选择器 (可选)", hint_text="例如: h1.title", expand=True)

    def on_download_click(e):
        dl_btn.disabled = True
        page.update()

        def _task():
            try:
                url = url_field.value or ""
                if not url:
                    show_snackbar("❌ 请输入 URL")
                    return
                site = site_dropdown.value or "generic"
                out = os.path.join(os.getcwd(), "downloaded_chapter.epub")
                opts = {}
                if selector_field.value:
                    opts["selector"] = selector_field.value
                if title_selector_field.value:
                    opts["title_selector"] = title_selector_field.value
                path = download_with_site(site, url, out, opts)
                input_file_field.value = path
                _load_chapters()
                save_ui_config()
                show_snackbar(f"✅ 下载并载入: {os.path.basename(path)}")
            except Exception as ex:
                show_snackbar(f"❌ 下载失败: {ex}")
            finally:
                dl_btn.disabled = False
                try:
                    page.update()
                except Exception:
                    pass

        threading.Thread(target=_task, daemon=True).start()

    dl_btn = ft.FilledTonalButton("下载并载入", icon=ft.Icons.DOWNLOAD, on_click=on_download_click)

    download_card = ft.Card(
        content=ft.Container(
            padding=12,
            content=ft.Column([
                ft.Row([ft.Icon(ft.Icons.CLOUD_DOWNLOAD, color=ft.Colors.PRIMARY), ft.Text("从 URL 下载章节", size=16)], spacing=8),
                ft.Row([url_field, site_dropdown, dl_btn], spacing=8),
                ft.Row([selector_field, title_selector_field], spacing=8),
            ], spacing=8),
        ),
        elevation=1,
    )

    # ---------- 翻译预设 ----------
    saved_style = saved.get("style_preset", "经典风格 (推荐)")
    if saved_style not in FLAT_STYLES:
        saved_style = "经典风格 (推荐)"

    style_options = []
    for cat_name, styles in STYLE_CATEGORIES.items():
        style_options.append(
            ft.dropdown.Option(key=f"__header__{cat_name}", text=f"── {cat_name} ──", disabled=True)
        )
        for sname in styles:
            style_options.append(ft.dropdown.Option(key=sname, text=sname))

    style_dropdown = ft.Dropdown(
        label="翻译预设", value=saved_style, options=style_options,
        border_radius=10, filled=True, expand=True,
        on_select=on_style_change, tooltip="选择翻译预设，自动调整提示词和参数",
    )
    style_desc = ft.Text(
        FLAT_STYLES.get(saved_style, {}).get("desc", ""),
        size=12, color=ft.Colors.ON_SURFACE_VARIANT, italic=True,
    )

    custom_prompt_field = ft.TextField(
        label="自定义系统提示词", value=saved.get("custom_prompt", ""),
        multiline=True, min_lines=3, max_lines=8,
        border_radius=10, filled=True, on_blur=_on_field_blur,
        helper="留空则使用内置默认提示词",
    )
    custom_prompt_container = ft.Container(content=custom_prompt_field, visible=(saved_style == "自定义"))

    history_prompts = history.get("custom_prompts", [])
    history_prompt_controls = [
        ft.TextButton(
            hp[:40] + "..." if len(hp) > 40 else hp,
            on_click=on_history_prompt_click, data=hp, tooltip=hp[:200],
        )
        for hp in history_prompts[:5]
    ]
    history_prompt_container = ft.Container(
        content=ft.Column([
            ft.Text("历史提示词:", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Row(history_prompt_controls, wrap=True, spacing=4),
        ], spacing=4),
        visible=(saved_style == "自定义" and bool(history_prompts)),
    )

    style_card = ft.Card(
        content=ft.Container(
            padding=20,
            content=ft.Column([
                ft.Row([ft.Icon(ft.Icons.PALETTE, color=ft.Colors.PRIMARY),
                    ft.Text("翻译预设", size=17, weight=ft.FontWeight.W_600)], spacing=8),
                ft.Row([style_dropdown], spacing=12),
                style_desc,
                custom_prompt_container,
                history_prompt_container,
            ], spacing=8),
        ),
        elevation=2,
    )

    # ---------- 模型参数（可折叠） ----------
    init_temp = float(saved.get("temperature", 0.7))
    init_topp = float(saved.get("top_p", 0.9))
    init_freq = float(saved.get("frequency_penalty", 0.1))
    init_pres = float(saved.get("presence_penalty", 0.0))
    init_concurrent = float(saved.get("concurrent_workers", 1))

    temp_label = ft.Text(f"Temperature: {init_temp:.2f}", size=13, weight=ft.FontWeight.W_500,
                         tooltip=TOOLTIPS["temperature"])
    temp_slider = ft.Slider(min=0, max=2, divisions=40, value=init_temp, label="{value}",
                            on_change=on_temp_change, on_change_end=_on_field_blur)

    topp_label = ft.Text(f"Top-p: {init_topp:.2f}", size=13, weight=ft.FontWeight.W_500,
                         tooltip=TOOLTIPS["top_p"])
    topp_slider = ft.Slider(min=0, max=1, divisions=20, value=init_topp, label="{value}",
                            on_change=on_topp_change, on_change_end=_on_field_blur)

    freq_penalty_label = ft.Text(f"Freq Penalty: {init_freq:.2f}", size=13, weight=ft.FontWeight.W_500,
                                 tooltip=TOOLTIPS["frequency_penalty"])
    freq_penalty_slider = ft.Slider(min=-2.0, max=2.0, divisions=40, value=init_freq, label="{value}",
                                    on_change=on_freq_penalty_change, on_change_end=_on_field_blur)

    pres_penalty_label = ft.Text(f"Pres Penalty: {init_pres:.2f}", size=13, weight=ft.FontWeight.W_500,
                                 tooltip=TOOLTIPS["presence_penalty"])
    pres_penalty_slider = ft.Slider(min=-2.0, max=2.0, divisions=40, value=init_pres, label="{value}",
                                    on_change=on_pres_penalty_change, on_change_end=_on_field_blur)

    max_tokens_field = ft.TextField(
        label="Max Tokens", value=str(saved.get("max_tokens", "8192")),
        width=140, border_radius=10, filled=True,
        input_filter=ft.NumbersOnlyInputFilter(), tooltip=TOOLTIPS["max_tokens"],
        on_blur=_on_field_blur,
    )
    chunk_size_field = ft.TextField(
        label="分块大小", value=str(saved.get("chunk_size", "1500")),
        width=140, border_radius=10, filled=True,
        input_filter=ft.NumbersOnlyInputFilter(), tooltip=TOOLTIPS["chunk_size"],
        on_blur=_on_field_blur,
        disabled=saved.get("whole_chapter", False),
    )
    whole_chapter_switch = ft.Switch(
        label="整章翻译", value=saved.get("whole_chapter", False),
        tooltip="开启后每章作为整体发送给 API，\n消除跨分块的术语不一致问题。\n需要模型支持长上下文 (如 DeepSeek 128K)",
        on_change=on_whole_chapter_toggle,
    )

    init_context = int(saved.get("context_lines", 5))
    context_label = ft.Text(
        f"上下文注入: {init_context} 行" if init_context > 0 else "上下文注入: 关闭",
        size=13, weight=ft.FontWeight.W_500, tooltip=TOOLTIPS["context_lines"],
    )
    context_slider = ft.Slider(
        min=0, max=15, divisions=15, value=init_context,
        label="{value}", on_change=on_context_change, on_change_end=_on_field_blur,
    )

    concurrent_label = ft.Text(f"并发线程: {int(init_concurrent)}", size=13, weight=ft.FontWeight.W_500,
                               tooltip=TOOLTIPS["concurrent"])
    concurrent_slider = ft.Slider(
        min=1, max=128, divisions=127, value=init_concurrent,
        label="{value}", on_change=on_concurrent_change, on_change_end=_on_field_blur,
    )

    params_panel = ft.ExpansionTile(
        title=ft.Text("模型参数", size=17, weight=ft.FontWeight.W_600),
        leading=ft.Icon(ft.Icons.TUNE, color=ft.Colors.PRIMARY),
        expanded=False,
        controls=[
            ft.Container(
                padding=ft.Padding(left=16, right=16, top=4, bottom=12),
                content=ft.Column([
                    temp_label, temp_slider,
                    topp_label, topp_slider,
                    ft.Divider(height=1),
                    ft.Text("质量调优", size=14, weight=ft.FontWeight.W_500, color=ft.Colors.TERTIARY),
                    freq_penalty_label, freq_penalty_slider,
                    pres_penalty_label, pres_penalty_slider,
                    ft.Divider(height=1),
                    ft.Row([max_tokens_field, chunk_size_field, whole_chapter_switch], spacing=12),
                    context_label, context_slider,
                    ft.Divider(height=1),
                    concurrent_label, concurrent_slider,
                ], spacing=4),
            ),
        ],
    )

    params_card = ft.Card(content=params_panel, elevation=2)

    # ---------- 章节范围 ----------
    chapter_info_text = ft.Text("请先选择 EPUB 文件", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    start_chapter_field = ft.TextField(label="起始", value="1", width=100, border_radius=10, filled=True,
                                       input_filter=ft.NumbersOnlyInputFilter())
    end_chapter_field = ft.TextField(label="结束", value="0", width=100, border_radius=10, filled=True,
                                     input_filter=ft.NumbersOnlyInputFilter(), hint_text="0=全部")
    checkpoint_switch = ft.Switch(label="断点续传", value=True, tooltip=TOOLTIPS["checkpoint"])
    clear_cp_btn = ft.TextButton("清除断点", icon=ft.Icons.DELETE_OUTLINE, on_click=on_clear_checkpoint)

    chapter_card = ft.Card(
        content=ft.Container(
            padding=20,
            content=ft.Column([
                ft.Row([ft.Icon(ft.Icons.BOOKMARK, color=ft.Colors.PRIMARY),
                        ft.Text("章节范围", size=17, weight=ft.FontWeight.W_600),
                        chapter_info_text], spacing=8),
                ft.Row([start_chapter_field, ft.Text("→", size=18), end_chapter_field], spacing=12),
                ft.Row([checkpoint_switch, clear_cp_btn], spacing=12),
                ft.Text('✅ 更改提示词或参数不影响已翻译的章节（断点保护）。\n如需重新翻译，请先"清除断点"。',
                        size=11, color=ft.Colors.ON_SURFACE_VARIANT),
            ], spacing=8),
        ),
        elevation=2,
    )

    # ---------- 术语表 ----------
    glossary_file_field = ft.TextField(
        label="术语表 (JSON)", prefix_icon=ft.Icons.MENU_BOOK,
        value=saved.get("glossary_file", ""), read_only=True,
        border_radius=10, filled=True, expand=True,
    )
    glossary_count = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    glossary_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("日文原文", weight=ft.FontWeight.W_600, size=12)),
            ft.DataColumn(ft.Text("中文翻译", weight=ft.FontWeight.W_600, size=12)),
        ],
        rows=[], column_spacing=24, heading_row_height=36,
        data_row_min_height=30, data_row_max_height=36,
    )
    add_jp = ft.TextField(label="日文", border_radius=10, filled=True, expand=True, dense=True)
    add_cn = ft.TextField(label="中文", border_radius=10, filled=True, expand=True, dense=True)

    glossary_panel = ft.ExpansionTile(
        title=ft.Text("术语表", size=17, weight=ft.FontWeight.W_600),
        leading=ft.Icon(ft.Icons.TRANSLATE, color=ft.Colors.PRIMARY),
        expanded=False,
        controls=[
            ft.Container(
                padding=ft.Padding(left=16, right=16, top=4, bottom=12),
                content=ft.Column([
                    ft.Row([glossary_file_field,
                            ft.FilledTonalButton("选择", icon=ft.Icons.FILE_OPEN, on_click=on_pick_glossary)],
                           spacing=8),
                    glossary_count,
                    ft.Container(
                        content=ft.Column([glossary_table], scroll=ft.ScrollMode.AUTO),
                        height=160, border_radius=8,
                    ),
                    ft.Row([add_jp, add_cn,
                            ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.PRIMARY,
                                          tooltip="添加术语", on_click=on_add_term)], spacing=8),
                ], spacing=8),
            ),
        ],
    )

    glossary_card = ft.Card(content=glossary_panel, elevation=2)

    # ---------- 断点恢复面板 ----------
    cp_file_field = ft.TextField(
        label="断点文件 (.checkpoint.json)", prefix_icon=ft.Icons.RESTORE,
        read_only=True, border_radius=10, filled=True, expand=True,
    )
    cp_source_field = ft.TextField(
        label="源 EPUB（可选，保持章节顺序）", prefix_icon=ft.Icons.BOOK,
        read_only=True, border_radius=10, filled=True, expand=True,
    )
    cp_info_text = ft.Text("请选择断点文件", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    cp_format_dropdown = ft.Dropdown(
        label="输出格式", value="EPUB",
        options=[ft.dropdown.Option("TXT"), ft.dropdown.Option("EPUB")],
        width=110, border_radius=10, filled=True,
    )
    cp_restore_btn = ft.FilledButton("生成文件", icon=ft.Icons.BUILD, on_click=on_restore_checkpoint, disabled=True)

    restore_panel = ft.ExpansionTile(
        title=ft.Text("断点恢复", size=17, weight=ft.FontWeight.W_600),
        subtitle=ft.Text("从中断的翻译断点文件恢复并导出", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
        leading=ft.Icon(ft.Icons.SETTINGS_BACKUP_RESTORE, color=ft.Colors.TERTIARY),
        expanded=False,
        controls=[
            ft.Container(
                padding=ft.Padding(left=16, right=16, top=4, bottom=12),
                content=ft.Column([
                    ft.Row([cp_file_field,
                            ft.FilledTonalButton("选择断点", icon=ft.Icons.FILE_OPEN, on_click=on_pick_checkpoint)],
                           spacing=8),
                    ft.Row([cp_source_field,
                            ft.FilledTonalButton("选择源文件", icon=ft.Icons.UPLOAD_FILE, on_click=on_pick_cp_source)],
                           spacing=8),
                    ft.Row([cp_format_dropdown, cp_restore_btn, cp_info_text], spacing=12),
                ], spacing=10),
            ),
        ],
    )

    restore_card = ft.Card(content=restore_panel, elevation=2)

    # ---------- 翻译修复面板 ----------
    fix_cp_field = ft.TextField(
        label="断点文件 (.checkpoint.json)", prefix_icon=ft.Icons.FIND_IN_PAGE,
        read_only=True, border_radius=10, filled=True, expand=True,
    )
    fix_source_field = ft.TextField(
        label="源 EPUB (重翻必需)", prefix_icon=ft.Icons.BOOK,
        read_only=True, border_radius=10, filled=True, expand=True,
    )
    # 检查规则：使用多行独立输入（关键词 + 说明），避免用户使用箭头文本格式
    fix_rules_keyword_fields = []
    fix_rules_desc_fields = []
    fix_rules_rows = []
    for i in range(5):
        kf = ft.TextField(label=f"关键词 #{i+1}", hint_text="示例: 前辈", border_radius=8, filled=True, expand=True)
        df = ft.TextField(label=f"说明 #{i+1}", hint_text="示例: 替换为 学姐", border_radius=8, filled=True, expand=True)
        fix_rules_keyword_fields.append(kf)
        fix_rules_desc_fields.append(df)
        fix_rules_rows.append(ft.Row([kf, df], spacing=8))
    fix_rules_container = ft.Column(fix_rules_rows, spacing=6)
    fix_status_text = ft.Text("请选择断点文件", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    fix_scan_btn = ft.FilledTonalButton("质量扫描", icon=ft.Icons.SEARCH, on_click=on_quality_scan, disabled=True)
    fix_retranslate_btn = ft.FilledButton(
        "重新翻译选中章节", icon=ft.Icons.AUTO_FIX_HIGH, on_click=on_retranslate, disabled=True,
    )
    fix_out_format = ft.Dropdown(
        label="输出格式", value="EPUB",
        options=[ft.dropdown.Option("TXT"), ft.dropdown.Option("EPUB")],
        width=110, border_radius=10, filled=True,
    )
    fix_result_column = ft.Column(spacing=4)

    fix_panel = ft.ExpansionTile(
        title=ft.Text("翻译修复", size=17, weight=ft.FontWeight.W_600),
        subtitle=ft.Text("扫描已翻译章节的质量问题并按需重翻，支持关键词检测与修改建议", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
        leading=ft.Icon(ft.Icons.AUTO_FIX_HIGH, color=ft.Colors.SECONDARY),
        expanded=False,
        controls=[
            ft.Container(
                padding=ft.Padding(left=16, right=16, top=4, bottom=12),
                content=ft.Column([
                    ft.Row([fix_cp_field,
                            ft.FilledTonalButton("选择断点", icon=ft.Icons.FILE_OPEN, on_click=on_pick_fix_checkpoint)],
                           spacing=8),
                    ft.Row([fix_source_field,
                            ft.FilledTonalButton("选择源文件", icon=ft.Icons.UPLOAD_FILE, on_click=on_pick_fix_source)],
                           spacing=8),
                    ft.Row([fim_completion_switch], spacing=8),
                    ft.Text("提示：为每组填写要检测的关键词与对应的修改建议（示例：关键词=前辈，说明=替换为 学姐）。至少填写一组。", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                    fix_rules_container,
                    ft.Row([fix_scan_btn, fix_out_format, fix_retranslate_btn], spacing=12),
                    ft.Row([
                        ft.TextButton("全选", on_click=on_fix_select_all),
                        ft.TextButton("全不选", on_click=on_fix_select_none),
                        ft.Container(expand=True),
                        fix_status_text,
                    ], spacing=8),
                    ft.Container(
                        content=fix_result_column, border_radius=8, padding=8,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                    ),
                ], spacing=10),
            ),
        ],
    )

    fix_card = ft.Card(content=fix_panel, elevation=2)

    # ---------- 翻译控制 & 日志 ----------
    progress_bar = ft.ProgressBar(value=0, bar_height=6, border_radius=3, color=ft.Colors.PRIMARY)
    progress_text = ft.Text("就绪", size=14, weight=ft.FontWeight.W_500)
    speed_text = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT)

    start_btn = ft.FilledButton("开始翻译", icon=ft.Icons.PLAY_ARROW, on_click=on_start)
    pause_btn = ft.OutlinedButton("暂停", icon=ft.Icons.PAUSE, on_click=on_pause, disabled=True)
    resume_btn = ft.FilledTonalButton("恢复", icon=ft.Icons.PLAY_ARROW, on_click=on_resume, disabled=True)
    cancel_btn = ft.OutlinedButton("取消", icon=ft.Icons.STOP, on_click=on_cancel, disabled=True)

    log_list = ft.ListView(spacing=2, auto_scroll=True, expand=True)

    log_panel = ft.Card(
        content=ft.Container(
            padding=12, expand=True,
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.TERMINAL, color=ft.Colors.PRIMARY, size=18),
                    ft.Text("运行日志", size=15, weight=ft.FontWeight.W_600),
                    ft.Container(expand=True),
                    # 流式日志开关移动到日志面板，便于用户即时开启/关闭日志流式输出
                    stream_logs_switch,
                    ft.IconButton(ft.Icons.DELETE_SWEEP, tooltip="清空日志", icon_size=16,
                                  on_click=lambda _: (log_list.controls.clear(), page.update())),
                ], spacing=6),
                ft.Container(
                    content=log_list, expand=True, border_radius=8, padding=8,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                ),
            ], spacing=6, expand=True),
        ),
        elevation=2, expand=True,
    )

    # ---------- 顶部标题栏 ----------
    theme_btn = ft.IconButton(ft.Icons.BRIGHTNESS_AUTO, on_click=on_theme_toggle, tooltip="跟随系统")

    title_bar = ft.Container(
        padding=ft.Padding(left=20, right=20, top=10, bottom=10),
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        content=ft.Row([
            ft.Row([
                ft.Icon(ft.Icons.AUTO_STORIES, color=ft.Colors.PRIMARY, size=26),
                ft.Text(APP_TITLE, size=21, weight=ft.FontWeight.W_700),
                ft.Container(
                    content=ft.Text(f"v{APP_VERSION}", size=10, color=ft.Colors.ON_SURFACE_VARIANT),
                    bgcolor=ft.Colors.SURFACE_CONTAINER, border_radius=8,
                    padding=ft.Padding(left=8, right=8, top=2, bottom=2),
                ),
            ], spacing=10),
            ft.Row([theme_btn], spacing=4),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
    )

    # ===== 布局 =====
    left = ft.Container(
        expand=True,
        padding=ft.Padding(left=16, right=8, top=8, bottom=16),
        content=ft.Column(
            [api_card, file_card, style_card, params_card, chapter_card, glossary_card, restore_card, fix_card],
            spacing=10, scroll=ft.ScrollMode.AUTO, expand=True,
        ),
    )

    right = ft.Container(
        expand=True,
        padding=ft.Padding(left=8, right=16, top=8, bottom=16),
        content=ft.Column([
            ft.Card(
                content=ft.Container(
                    padding=20,
                    content=ft.Column([
                        ft.Row([ft.Icon(ft.Icons.ROCKET_LAUNCH, color=ft.Colors.PRIMARY),
                                ft.Text("翻译控制", size=17, weight=ft.FontWeight.W_600)], spacing=8),
                        ft.Divider(height=1),
                        ft.Row([start_btn, pause_btn, resume_btn, cancel_btn], spacing=10,
                               alignment=ft.MainAxisAlignment.CENTER),
                        ft.Container(height=6),
                        progress_bar,
                        ft.Row([progress_text, ft.Container(expand=True), speed_text]),
                    ], spacing=6),
                ),
                elevation=2,
            ),
            log_panel,
        ], spacing=10, expand=True),
    )

    page.add(ft.Column([
        title_bar,
        ft.Row([left, right], spacing=0, expand=True,
               vertical_alignment=ft.CrossAxisAlignment.STRETCH),
    ], spacing=0, expand=True))

    # --- 窗口关闭时自动保存 ---
    def on_window_event(e):
        # 兼容不同 Flet 版本的事件结构，确保在窗口关闭请求时能保存状态并关闭窗口
        try:
            evdata = getattr(e, "data", e)
        except Exception:
            evdata = e

        # 更宽松的关闭检测：只要事件描述中包含 close 字样即视为关闭请求
        try:
            is_close = "close" in str(evdata).lower()
        except Exception:
            is_close = False

        if is_close:
            try:
                save_ui_config()
                save_params_to_history()
            except Exception:
                pass
            # 尝试优雅关闭窗口，若失败则尝试强制销毁
            try:
                page.window.destroy()
            except Exception:
                try:
                    page.window.close()
                except Exception:
                    pass

    # 允许系统正常关闭窗口（Flet 的不同版本在事件回调上存在差异）
    page.window.prevent_close = False
    page.window.on_event = on_window_event
    page.update()

    if glossary_file_field.value:
        _load_glossary_preview()


def run_gui():
    """启动 GUI 入口"""
    ft.run(main)


if __name__ == "__main__":
    run_gui()
