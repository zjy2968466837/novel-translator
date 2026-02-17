# -*- coding: utf-8 -*-
"""
novel_translator.engine - 翻译引擎核心模块

功能:
- 支持 Chat / Completion 双模型后端 (自动检测)
- 支持并发翻译加速
- 支持断点续传 (checkpoint)
- 支持 TXT / EPUB 输出格式
- 前文上下文注入，保持译名一致
- 整章翻译模式 (chunk_size=0)
- 质量扫描与选择性重翻
"""

import os
import re
import time
import json
import hashlib
import threading
import warnings

import ebooklib
import ebooklib.utils as _ebooklib_utils
from ebooklib import epub
from bs4 import BeautifulSoup

# ── Monkey-patch ──────────────────────────────────────────────
# 修复 ebooklib 在 write_epub 时因 EpubNav 内容为空导致 lxml 解析崩溃
_original_get_pages = _ebooklib_utils.get_pages


def _safe_get_pages(item):
    try:
        body = item.get_body_content()
        if not body or not body.strip():
            return []
        return _original_get_pages(item)
    except Exception:
        return []


_ebooklib_utils.get_pages = _safe_get_pages
# ──────────────────────────────────────────────────────────────

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from novel_translator.providers import create_provider, AIProvider


# =====================================================================
# 数据类
# =====================================================================

@dataclass
class TranslationConfig:
    """翻译任务配置"""

    # API
    provider: str = "openai"  # "openai" / "anthropic" / "google" / "ollama"
    api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1"
    model_name: str = "deepseek-ai/DeepSeek-V3.2"
    model_type: str = "auto"  # "auto" / "chat" / "completion"

    # 生成参数
    temperature: float = 0.7
    top_p: float = 0.9
    frequency_penalty: float = 0.1
    presence_penalty: float = 0.0
    max_tokens: int = 8192

    # 分块与并发
    chunk_size: int = 1500      # 0 = 整章翻译
    concurrent_workers: int = 1
    retry_count: int = 3

    # 文件
    input_file: str = ""
    output_file: str = "novel_translated.txt"
    output_format: str = "txt"  # "txt" / "epub"
    glossary_file: str = ""

    # 章节范围
    start_chapter: int = 0
    end_chapter: int = 0

    # 提示词
    custom_prompt: str = ""

    # 断点续传
    enable_checkpoint: bool = True

    # 上下文注入
    context_lines: int = 5     # 前文上下文行数 (0=关闭)

    # 补全模型专用
    few_shot_examples: str = ""


@dataclass
class TranslationProgress:
    """运行时翻译进度"""

    total_chapters: int = 0
    current_chapter: int = 0
    current_chapter_name: str = ""
    total_chunks: int = 0
    current_chunk: int = 0
    is_running: bool = False
    is_paused: bool = False
    is_cancelled: bool = False
    translated_chars: int = 0
    start_time: float = 0
    elapsed_time: float = 0


# =====================================================================
# 辅助类
# =====================================================================

class ChapterInfo:
    """EPUB 章节元数据"""

    def __init__(self, index: int, name: str, content: str, item=None, html_content: str = ""):
        self.index = index
        self.name = name
        self.content = content        # 纯文本（用于分块和翻译）
        self.html_content = html_content  # 原始 HTML（用于结构保留输出）
        self.char_count = len(content)
        self.item = item


class CheckpointManager:
    """断点续传管理器 — 基于 JSON 文件"""

    def __init__(self, input_file: str, output_file: str):
        h = hashlib.md5(input_file.encode()).hexdigest()[:8]
        base = os.path.splitext(output_file)[0]
        self.checkpoint_file = f"{base}.checkpoint.json"
        self.data: dict = {"completed_chapters": {}, "config_hash": h}

    def load(self):
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {"completed_chapters": {}}
        return self.data

    def save(self):
        try:
            with open(self.checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def is_chapter_done(self, chapter_name: str) -> bool:
        return chapter_name in self.data.get("completed_chapters", {})

    def get_chapter_result(self, chapter_name: str) -> str:
        return self.data.get("completed_chapters", {}).get(chapter_name, "")

    def mark_chapter_done(self, chapter_name: str, translated_text: str):
        if "completed_chapters" not in self.data:
            self.data["completed_chapters"] = {}
        self.data["completed_chapters"][chapter_name] = translated_text
        self.save()

    def get_completed_count(self) -> int:
        return len(self.data.get("completed_chapters", {}))

    def clear(self):
        self.data = {"completed_chapters": {}}
        if os.path.exists(self.checkpoint_file):
            os.remove(self.checkpoint_file)


# =====================================================================
# 翻译引擎
# =====================================================================

class TranslatorEngine:
    """翻译引擎核心 — 驱动 CLI 与 GUI"""

    def __init__(self, config: TranslationConfig):
        self.config = config
        self.progress = TranslationProgress()
        self.provider: Optional[AIProvider] = None
        self.glossary: dict = {}
        self.system_prompt: str = ""
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self.checkpoint: Optional[CheckpointManager] = None

        # 回调接口
        self.on_progress: Optional[Callable] = None
        self.on_log: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        self.on_complete: Optional[Callable] = None
        self.on_chapter_start: Optional[Callable] = None

    # ── 日志 ──

    def log(self, message: str):
        if self.on_log:
            self.on_log(message)

    # ── Provider 初始化 ──

    def _init_provider(self):
        """根据 config.provider 创建对应的 AI Provider 实例"""
        provider_type = self.config.provider or "openai"
        if not self.config.api_key and provider_type != "ollama":
            raise ValueError("请填写 API Key")
        self.provider = create_provider(
            provider_type=provider_type,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model_name=self.config.model_name,
            model_type=self.config.model_type,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            frequency_penalty=self.config.frequency_penalty,
            presence_penalty=self.config.presence_penalty,
            max_tokens=self.config.max_tokens,
            few_shot_examples=self.config.few_shot_examples,
        )
        self.log(f"✅ {self.provider.provider_name} 已初始化 ({self.config.model_name})")

    # ── 术语表 ──

    def load_glossary(self, filepath: str = "") -> dict:
        path = filepath or self.config.glossary_file
        if not path or not os.path.exists(path):
            self.log("ℹ️ 未加载术语表")
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                glossary = json.load(f)
            self.log(f"✅ 术语表已加载: {len(glossary)} 条")
            return glossary
        except Exception as e:
            self.log(f"⚠️ 术语表加载失败: {e}")
            return {}

    def save_glossary(self, glossary: dict, filepath: str):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(glossary, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"⚠️ 术语表保存失败: {e}")

    # ── 提示词构建 ──

    def build_system_prompt(self, glossary_dict: dict | None = None) -> str:
        if self.config.custom_prompt:
            base_prompt = self.config.custom_prompt
        else:
            base_prompt = (
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
                "12. 纯净输出：只输出翻译正文，严禁输出任何翻译注释、译者注、脚注、说明文字、括号补充解释。"
                "不得添加\u201c注：\u201d、\u201c译注：\u201d、\u201c*\u201d注释、任何meta内容。\n"
                "13. 术语前后一致：同一专有名词在全文中必须使用完全相同的译名和标记格式。"
                "例如：『金剛』始终译为「金刚」、生涯の魔法始终译为\u201c终生魔法\u201d、ウルタス始终译为\u201c厄尔塔斯\u201d、"
                "マナ始终译为\u201c魔力素\u201d或术语表指定译名。禁止在不同段落中对同一术语使用不同译法。\n"
                "14. 标记统一：专有名词一律使用「」标记（如「金刚」「魅惑之瞳」），"
                "不得混用『』、《》、【】、\u201c\u201d等不同标记符号。\n"
                "15. 称呼翻译：日文\u201c先輩\u201d在学园背景下，必须根据性别翻译——"
                "女性先輩一律译为\u201c学姐\u201d，男性先輩一律译为\u201c学长\u201d。"
                "严禁使用\u201c前辈\u201d这一性别模糊的译法。同一角色的称呼在全文中必须保持完全一致，不得在不同段落间切换用词。\n"
                "16. 人名一致性：同一角色在全文中必须使用完全相同的中文译名，严禁出现变体。"
                "例如：ミヤ始终译为\u201c弥娅\u201d（不可出现\u201c米娅\u201d\u201c米亚\u201d\u201c宫\u201d等变体）；"
                "クリス始终译为\u201c克里斯\u201d（不可出现\u201c克莉丝\u201d等变体）；"
                "グリージャー的中文名始终为\u201c安涅莉丝\u201d（不可出现\u201c格里杰尔\u201d\u201c格里杰\u201d等音译变体）。"
                "当原文出现全名时（如アネスト・グリージャー），译为\u201c安涅莉丝·格里杰尔\u201d。\n\n"
                "翻译风格：简洁准确，紧贴原文，语意连贯的短句合并为流畅长句，不添加原文没有的修辞和语气。\n"
            )
        g = glossary_dict if glossary_dict is not None else self.glossary
        if g:
            glossary_text = "\n【强制术语表】\n"
            for k, v in g.items():
                glossary_text += f"- {k} -> {v}\n"
            return base_prompt + glossary_text
        return base_prompt

    def build_completion_prompt(self, text: str, prev_context: str = "") -> str:
        """为补全模型构建完整 prompt（含 few-shot 示例 + 术语表 + 上下文 + 原文）"""
        parts = []
        parts.append("以下是日文轻小说翻译任务。请将【待翻译原文】翻译为流畅的中文，只输出译文。\n")

        g = self.glossary
        if g:
            parts.append("【术语表（必须严格遵守）】")
            for k, v in g.items():
                parts.append(f"- {k} → {v}")
            parts.append("")

        if self.config.few_shot_examples:
            parts.append(self.config.few_shot_examples)
            parts.append("")

        if prev_context:
            parts.append("【前文译文参考（保持人名、称谓一致）】")
            parts.append(prev_context)
            parts.append("")

        parts.append("【待翻译原文】")
        parts.append(text)
        parts.append("")
        parts.append("【译文】")

        return "\n".join(parts)

    # ── 文本处理 ──

    # 需保留的行内标签（翻译内部文本但保留标签结构）
    _INLINE_TAGS = {'em', 'strong', 'b', 'i', 'u', 's', 'span', 'a', 'small', 'sub', 'sup', 'mark'}
    # Ruby 注音标签（保留原样不翻译）
    _RUBY_TAGS = {'ruby', 'rt', 'rp', 'rb'}
    # 块级元素（每个产生一个翻译段落）
    _BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'li', 'dt', 'dd', 'figcaption'}
    # 不翻译的标签（保留原样）
    _SKIP_TAGS = {'img', 'image', 'svg', 'br', 'hr', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'script', 'style'}

    @staticmethod
    def clean_html(html_content) -> str:
        """将 HTML 转换为纯文本（向后兼容）"""
        warnings.filterwarnings("ignore", category=UserWarning, module="bs4")
        soup = BeautifulSoup(html_content, "html.parser")
        return soup.get_text(separator="\n", strip=True)

    @staticmethod
    def parse_html_structured(html_content) -> tuple[str, list[dict]]:
        """结构感知的 HTML 解析。

        返回:
            (plain_text, segments)
            - plain_text: 用于分块和翻译的纯文本
            - segments: 每个元素的结构信息列表，包含:
              - type: "text" | "image" | "heading" | "skip"
              - tag: 原始标签名
              - text: 提取的纯文本
              - html: 原始 HTML 片段
              - attrs: 标签属性字典
        """
        warnings.filterwarnings("ignore", category=UserWarning, module="bs4")
        soup = BeautifulSoup(html_content, "html.parser")
        body = soup.find("body")
        if not body:
            body = soup

        segments = []
        text_parts = []

        for element in body.children:
            if isinstance(element, str):
                # 裸文本节点
                stripped = element.strip()
                if stripped:
                    segments.append({"type": "text", "tag": "", "text": stripped, "html": stripped, "attrs": {}})
                    text_parts.append(stripped)
                continue

            tag_name = getattr(element, 'name', None)
            if not tag_name:
                continue

            if tag_name in TranslatorEngine._SKIP_TAGS:
                # 图片、表格等不翻译，原样保留
                seg_type = "image" if tag_name in ('img', 'image', 'svg') else "skip"
                segments.append({
                    "type": seg_type, "tag": tag_name,
                    "text": "", "html": str(element), "attrs": dict(element.attrs) if hasattr(element, 'attrs') else {},
                })
                continue

            if tag_name in TranslatorEngine._BLOCK_TAGS or tag_name.startswith('h'):
                # 块级元素——提取文本用于翻译，保留内联标签结构
                inner_text = element.get_text(strip=True)
                if not inner_text:
                    # 空块级元素（可能含图片），保留原样
                    segments.append({"type": "skip", "tag": tag_name, "text": "", "html": str(element), "attrs": {}})
                    continue
                seg_type = "heading" if tag_name.startswith('h') else "text"
                segments.append({
                    "type": seg_type, "tag": tag_name,
                    "text": inner_text, "html": str(element),
                    "attrs": dict(element.attrs) if hasattr(element, 'attrs') else {},
                })
                text_parts.append(inner_text)
                continue

            # 其他元素（如 section, article, div 嵌套）——递归提取
            inner_text = element.get_text(separator="\n", strip=True)
            if inner_text:
                segments.append({"type": "text", "tag": tag_name, "text": inner_text, "html": str(element), "attrs": {}})
                text_parts.append(inner_text)

        plain_text = "\n".join(text_parts)
        return plain_text, segments

    @staticmethod
    def rebuild_chapter_html(segments: list[dict], translated_text: str, original_html: str = "") -> str:
        """将翻译结果回注到原始 HTML 结构中。

        策略：按段落顺序将翻译文本填回对应的 segment，
        保留非文本 segment（图片、表格等）原样不动。
        """
        trans_paragraphs = [p.strip() for p in translated_text.split("\n") if p.strip()]
        trans_idx = 0
        result_parts = []

        for seg in segments:
            if seg["type"] in ("image", "skip"):
                # 非文本元素原样保留
                result_parts.append(seg["html"])
            elif seg["type"] in ("text", "heading"):
                tag = seg.get("tag", "p") or "p"
                if trans_idx < len(trans_paragraphs):
                    trans_content = trans_paragraphs[trans_idx]
                    # HTML 转义
                    trans_content = trans_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    result_parts.append(f"<{tag}>{trans_content}</{tag}>")
                    trans_idx += 1
                else:
                    # 翻译段落不足，保留原文
                    result_parts.append(seg["html"])
            else:
                result_parts.append(seg["html"])

        # 如果翻译段落比 segment 多（模型拆分了段落），追加剩余部分
        while trans_idx < len(trans_paragraphs):
            extra = trans_paragraphs[trans_idx].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            result_parts.append(f"<p>{extra}</p>")
            trans_idx += 1

        return "\n".join(result_parts)

    @staticmethod
    def _extract_chapter_order_key(filename: str):
        """从文件名中提取排序键"""
        basename = os.path.basename(filename).lower()
        if basename in ("nav.xhtml", "toc.xhtml", "cover.xhtml"):
            return (0, 0)
        m = re.search(r"(\d+)", basename)
        if m:
            return (1, int(m.group(1)))
        return (2, 0)

    @staticmethod
    def _sort_chapters_data(chapters_data: list) -> list:
        return sorted(
            chapters_data,
            key=lambda x: TranslatorEngine._extract_chapter_order_key(x[0]),
        )

    @staticmethod
    def _extract_chapter_title(content: str, fallback_index=None):
        """从翻译内容首行提取章节标题，返回 (标题, 正文)"""
        if not content or not content.strip():
            title = f"第{fallback_index}章" if fallback_index is not None else "未命名章节"
            return title, content or ""

        lines = content.strip().split("\n")
        first_line = lines[0].strip()

        if first_line and len(first_line) <= 30:
            title = first_line
            body = "\n".join(lines[1:]).strip()
            return title, body

        title = f"第{fallback_index}章" if fallback_index is not None else first_line[:20]
        return title, content.strip()

    def split_text(self, text: str) -> list[str]:
        if not text:
            return []
        max_chars = self.config.chunk_size
        if max_chars <= 0:
            return [text.strip()]
        paragraphs = text.split("\n")
        chunks = []
        current_chunk = ""
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            if len(current_chunk) + len(p) > max_chars and current_chunk:
                chunks.append(current_chunk)
                current_chunk = p + "\n"
            else:
                current_chunk += p + "\n"
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    # ── 翻译核心 ──

    def translate_chunk(self, text: str, prev_context: str = "") -> str:
        if not text.strip():
            return ""

        # 构建用户内容（带上下文）
        if prev_context:
            user_content = (
                f"[前文翻译参考（仅供保持人名、称谓、术语一致，请勿翻译此部分）]\n"
                f"{prev_context}\n\n"
                f"[待翻译原文]\n{text}"
            )
        else:
            user_content = text

        for attempt in range(self.config.retry_count):
            self._pause_event.wait()
            if self.progress.is_cancelled:
                return "[翻译已取消]"
            try:
                result = self.provider.translate(self.system_prompt, user_content)
                return result
            except Exception as e:
                err_detail = self._format_api_error(e)
                self.log(f"⚠️ API 调用失败 (尝试 {attempt+1}/{self.config.retry_count}): {err_detail}")
                if attempt < self.config.retry_count - 1:
                    wait = 2 * (attempt + 1)
                    retry_after = self._get_retry_after(e)
                    if retry_after:
                        wait = max(wait, retry_after)
                        self.log(f"⏳ 服务端要求等待 {retry_after}s (retry-after)")
                    time.sleep(wait)
                else:
                    return f"\n[翻译失败: {err_detail}]\n"
        return "[翻译失败: 未知错误]"

    # ── 错误格式化 ──

    @staticmethod
    def _format_api_error(e) -> str:
        parts = []
        status = getattr(e, "status_code", None)
        if status:
            status_map = {
                400: "请求格式错误", 401: "认证失败(Key无效)", 402: "余额不足",
                403: "权限不足", 404: "模型/端点不存在", 429: "请求限速(触发速率限制)",
                500: "服务器内部错误", 502: "网关错误", 503: "服务暂不可用",
            }
            desc = status_map.get(status, "")
            parts.append(f"HTTP {status}" + (f" ({desc})" if desc else ""))

        body = getattr(e, "body", None)
        if isinstance(body, dict):
            err_msg = body.get("message", "") or body.get("error", {}).get("message", "")
            err_type = body.get("type", "") or body.get("error", {}).get("type", "")
            if err_type:
                parts.append(f"类型={err_type}")
            if err_msg:
                parts.append(err_msg[:200])
        elif body:
            parts.append(str(body)[:200])

        response = getattr(e, "response", None)
        if response:
            headers = getattr(response, "headers", None)
            if headers:
                req_id = headers.get("x-request-id") or headers.get("X-Request-Id")
                if req_id:
                    parts.append(f"请求ID={req_id}")

        if not parts:
            etype = type(e).__name__
            return f"[{etype}] {str(e)[:200]}"
        return " | ".join(parts)

    @staticmethod
    def _get_retry_after(e) -> int | None:
        response = getattr(e, "response", None)
        if response:
            headers = getattr(response, "headers", None)
            if headers:
                ra = headers.get("retry-after")
                if ra:
                    try:
                        return int(ra)
                    except (ValueError, TypeError):
                        pass
        return None

    # ── 章节读取 ──

    def get_chapters(self) -> list[ChapterInfo]:
        if not os.path.exists(self.config.input_file):
            raise FileNotFoundError(f"未找到文件: {self.config.input_file}")
        book = epub.read_epub(self.config.input_file)
        try:
            items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
        except (KeyError, AttributeError):
            items = [x for x in book.get_items() if x.get_type() == ebooklib.ITEM_DOCUMENT]
        chapters = []
        seen_names = set()
        for idx, item in enumerate(items):
            name = item.get_name()
            if name in seen_names:
                continue
            seen_names.add(name)
            raw_content = item.get_content()
            clean_text = self.clean_html(raw_content)
            if len(clean_text) >= 50:
                # 同时存储原始 HTML 以便后续结构保留
                html_str = raw_content.decode('utf-8', errors='replace') if isinstance(raw_content, bytes) else str(raw_content)
                chapters.append(ChapterInfo(idx + 1, name, clean_text, item, html_content=html_str))
        return chapters

    # ── 上下文注入 ──

    def _get_context_tail(self, text: str, n_lines: int | None = None) -> str:
        if n_lines is None:
            n_lines = self.config.context_lines
        if not text or n_lines <= 0:
            return ""
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        tail = lines[-n_lines:] if len(lines) > n_lines else lines
        return "\n".join(tail)

    # ── 分块翻译 ──

    def _translate_chunks(self, chunks: list[str]) -> list[str]:
        results = [None] * len(chunks)
        context_lines = self.config.context_lines

        def _do(index, chunk_text, prev_ctx=""):
            result = self.translate_chunk(chunk_text, prev_context=prev_ctx)
            with self._lock:
                self.progress.current_chunk += 1
                self.progress.translated_chars += len(result)
            if self.on_progress:
                self.on_progress(self.progress)
            return index, result

        workers = min(self.config.concurrent_workers, len(chunks))
        if workers <= 1:
            prev_ctx = ""
            for i, chunk in enumerate(chunks):
                if self.progress.is_cancelled:
                    break
                idx, result = _do(i, chunk, prev_ctx)
                results[idx] = result
                prev_ctx = self._get_context_tail(result, context_lines)
        else:
            if context_lines > 0:
                self.log("💡 并发模式下上下文注入仅在批次间生效")
            batch_prev_ctx = ""
            for batch_start in range(0, len(chunks), workers):
                batch_end = min(batch_start + workers, len(chunks))
                batch = list(enumerate(chunks[batch_start:batch_end], start=batch_start))
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {}
                    for j, (i, c) in enumerate(batch):
                        ctx = batch_prev_ctx if (j == 0 and context_lines > 0) else ""
                        futures[executor.submit(_do, i, c, ctx)] = i
                    for future in as_completed(futures):
                        if self.progress.is_cancelled:
                            break
                        idx, result = future.result()
                        results[idx] = result
                last_result = results[batch_end - 1]
                if last_result:
                    batch_prev_ctx = self._get_context_tail(last_result, context_lines)

        return [r for r in results if r is not None]

    # ── 输出写入 ──

    def _write_txt(self, output_path: str, chapters_data: list):
        sorted_data = self._sort_chapters_data(chapters_data)
        with open(output_path, "w", encoding="utf-8") as f:
            for i, (filename, content) in enumerate(sorted_data):
                title, body = self._extract_chapter_title(content, fallback_index=i + 1)
                f.write(f"\n{'='*40}\n")
                f.write(f"  {title}\n")
                f.write(f"{'='*40}\n\n")
                f.write(body)
                f.write("\n\n")

    def _write_epub(self, output_path: str, chapters_data: list):
        """生成 EPUB 输出。

        如果有原始 EPUB 源文件，将复制其 CSS/图片/字体/元数据，
        并将翻译结果注入对应章节的 HTML 中，保留原始样式。
        如果没有原始文件，回退到简单构建模式。
        """
        sorted_data = self._sort_chapters_data(chapters_data)

        # 尝试从原始 EPUB 复制资源
        source_book = None
        if self.config.input_file and os.path.exists(self.config.input_file):
            try:
                source_book = epub.read_epub(self.config.input_file)
            except Exception:
                pass

        book = epub.EpubBook()

        if source_book:
            # 复制元数据
            for meta in source_book.metadata.get('http://purl.org/dc/elements/1.1/', []):
                # meta 格式: (name, value, attrs)
                pass  # ebooklib 的 metadata API 较复杂，先设置基本信息
            src_name = os.path.splitext(os.path.basename(self.config.input_file))[0]
            book.set_identifier("novel-translator-output")
            book.set_title(f"{src_name} (中文翻译)")
            book.set_language("zh")
            book.add_author("AI Translation")

            # 复制所有非文档资源（CSS、图片、字体等）
            resource_items = []
            for item in source_book.get_items():
                item_type = item.get_type()
                if item_type == ebooklib.ITEM_DOCUMENT:
                    continue  # 章节文档单独处理
                if item_type in (ebooklib.ITEM_STYLE, ebooklib.ITEM_IMAGE,
                                 ebooklib.ITEM_FONT, ebooklib.ITEM_COVER):
                    book.add_item(item)
                    resource_items.append(item)
                elif item_type not in (ebooklib.ITEM_NAVIGATION,):
                    # 其他资源（如嵌入字体、音频等）也复制
                    try:
                        book.add_item(item)
                    except Exception:
                        pass
            if resource_items:
                self.log(f"📂 已复制 {len(resource_items)} 个原始资源（CSS/图片/字体）")
        else:
            book.set_identifier("novel-translator-output")
            src_name = os.path.splitext(os.path.basename(self.config.output_file))[0]
            book.set_title(f"{src_name}")
            book.set_language("zh")
            book.add_author("AI Translation")

        spine = ["nav"]
        toc = []

        # 构建章节名到翻译内容的映射
        translated_map = {name: content for name, content in sorted_data}

        # 如果有原始书籍，尝试保留原始章节结构
        if source_book:
            try:
                source_docs = list(source_book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
            except (KeyError, AttributeError):
                source_docs = [x for x in source_book.get_items() if x.get_type() == ebooklib.ITEM_DOCUMENT]

            chapter_idx = 0
            for item in source_docs:
                name = item.get_name()
                if name in translated_map:
                    chapter_idx += 1
                    translated_content = translated_map[name]
                    display_title, body = self._extract_chapter_title(translated_content, fallback_index=chapter_idx)

                    # 尝试在原始 HTML 结构中替换文本
                    raw = item.get_content()
                    html_str = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else str(raw)
                    _, segments = self.parse_html_structured(html_str)

                    if segments:
                        # 结构保留模式：将翻译文本回注到原始 HTML 结构
                        translated_body_html = self.rebuild_chapter_html(segments, translated_content)
                    else:
                        # 无法解析结构，回退到简单包装
                        translated_body_html = self._text_to_html_paragraphs(body)

                    # 从原始 HTML 提取 <head> 部分（保留 CSS 链接）
                    orig_soup = BeautifulSoup(html_str, "html.parser")
                    head_tag = orig_soup.find("head")
                    if head_tag:
                        head_html = str(head_tag)
                    else:
                        safe_title = display_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        head_html = f"<head><title>{safe_title}</title></head>"

                    full_html = (
                        f'<?xml version="1.0" encoding="utf-8"?>\n'
                        f'<!DOCTYPE html>\n'
                        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="zh">\n'
                        f'{head_html}\n'
                        f'<body>\n{translated_body_html}\n</body>\n</html>'
                    )

                    ch = epub.EpubHtml(
                        title=display_title,
                        file_name=name,  # 保留原始文件名
                        lang="zh",
                    )
                    ch.set_content(full_html.encode("utf-8"))
                    book.add_item(ch)
                    spine.append(ch)
                    toc.append(ch)
                # 跳过未翻译的章节（如封面、目录等）
        else:
            # 无原始文件，简单构建模式
            for i, (filename, content) in enumerate(sorted_data):
                display_title, body = self._extract_chapter_title(content, fallback_index=i + 1)
                html_body = self._text_to_html_paragraphs(body)
                safe_title = display_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

                ch = epub.EpubHtml(
                    title=display_title,
                    file_name=f"chapter_{i+1:04d}.xhtml",
                    lang="zh",
                )
                html_str = (
                    f'<?xml version="1.0" encoding="utf-8"?>\n'
                    f"<!DOCTYPE html>\n"
                    f'<html xmlns="http://www.w3.org/1999/xhtml" lang="zh">\n'
                    f"<head><title>{safe_title}</title>\n"
                    f'<link rel="stylesheet" href="style/default.css" type="text/css"/>\n'
                    f"</head>\n"
                    f"<body>\n<h2>{safe_title}</h2>\n{html_body}\n</body>\n</html>"
                )
                ch.set_content(html_str.encode("utf-8"))
                book.add_item(ch)
                spine.append(ch)
                toc.append(ch)

            # 添加默认样式
            style = epub.EpubItem(
                uid="style",
                file_name="style/default.css",
                media_type="text/css",
                content=(
                    b"body{font-family:serif;line-height:1.8;padding:1em;} "
                    b"p{text-indent:2em;margin:0.5em 0;} "
                    b"h2{text-align:center;margin:1em 0;}"
                ),
            )
            book.add_item(style)

        book.toc = toc
        book.spine = spine
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(output_path, book)

    @staticmethod
    def _text_to_html_paragraphs(text: str) -> str:
        """将纯文本转换为 HTML 段落"""
        paragraphs = text.split("\n")
        html_parts = []
        for p in paragraphs:
            p = p.strip()
            if p:
                p = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                html_parts.append(f"<p>{p}</p>")
        return "\n".join(html_parts)

    # ── 翻译主流程 ──

    def start_translation(self):
        thread = threading.Thread(target=self._run_translation, daemon=True)
        thread.start()
        return thread

    def _run_translation(self):
        try:
            self.progress = TranslationProgress()
            self.progress.is_running = True
            self.progress.start_time = time.time()

            self._init_provider()
            self.glossary = self.load_glossary()
            self.system_prompt = self.build_system_prompt()

            self.log(f"📖 正在读取: {os.path.basename(self.config.input_file)}")
            chapters = self.get_chapters()
            self.log(f"📚 共 {len(chapters)} 个有效章节")

            start = max(0, self.config.start_chapter - 1) if self.config.start_chapter > 0 else 0
            end = (
                self.config.end_chapter
                if 0 < self.config.end_chapter <= len(chapters)
                else len(chapters)
            )
            target_chapters = chapters[start:end]
            self.progress.total_chapters = len(target_chapters)
            self.log(f"🎯 范围: 第 {start+1} ~ {end} 章 (共 {len(target_chapters)} 章)")
            self.log(f"📄 输出格式: {self.config.output_format.upper()}")

            if self.config.chunk_size <= 0:
                self.log("📋 整章翻译模式: 每章作为一个整体发送")
            else:
                self.log(f"📋 分块大小: {self.config.chunk_size} 字")
            if self.config.context_lines > 0:
                self.log(f"🔗 上下文注入: 前文 {self.config.context_lines} 行")
            if self.config.concurrent_workers > 1:
                self.log(f"⚡ 并发: {self.config.concurrent_workers} 线程")

            if self.config.enable_checkpoint:
                self.checkpoint = CheckpointManager(self.config.input_file, self.config.output_file)
                self.checkpoint.load()
                done = self.checkpoint.get_completed_count()
                if done > 0:
                    self.log(f"📌 断点续传: 已完成 {done} 章，自动跳过")

            output_dir = os.path.dirname(self.config.output_file)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            chapters_data = []

            for i, chapter in enumerate(target_chapters):
                if self.progress.is_cancelled:
                    self.log("❌ 翻译已取消")
                    break

                self._pause_event.wait()

                self.progress.current_chapter = i + 1
                self.progress.current_chapter_name = chapter.name
                self.progress.current_chunk = 0

                if (
                    self.config.enable_checkpoint
                    and self.checkpoint
                    and self.checkpoint.is_chapter_done(chapter.name)
                ):
                    cached = self.checkpoint.get_chapter_result(chapter.name)
                    chapters_data.append((chapter.name, cached))
                    self.log(f"⏩ [{i+1}/{len(target_chapters)}] {chapter.name} (已缓存)")
                    self.progress.translated_chars += len(cached)
                    self.progress.elapsed_time = time.time() - self.progress.start_time
                    if self.on_progress:
                        self.on_progress(self.progress)
                    continue

                if self.on_chapter_start:
                    self.on_chapter_start(chapter)
                self.log(f"📝 [{i+1}/{len(target_chapters)}] {chapter.name}")

                chunks = self.split_text(chapter.content)
                self.progress.total_chunks = len(chunks)
                translated_parts = self._translate_chunks(chunks)
                translated_content = "\n".join(translated_parts)
                chapters_data.append((chapter.name, translated_content))

                if self.config.enable_checkpoint and self.checkpoint:
                    self.checkpoint.mark_chapter_done(chapter.name, translated_content)

                self.progress.elapsed_time = time.time() - self.progress.start_time
                if self.on_progress:
                    self.on_progress(self.progress)

            if not self.progress.is_cancelled and chapters_data:
                fmt = self.config.output_format.lower()
                self.log(f"📦 正在生成 {fmt.upper()} 文件（共 {len(chapters_data)} 章）...")
                if fmt == "epub":
                    self._write_epub(self.config.output_file, chapters_data)
                else:
                    self._write_txt(self.config.output_file, chapters_data)
                self.log(f"✅ 已保存: {self.config.output_file}")

            self.progress.is_running = False
            self.progress.elapsed_time = time.time() - self.progress.start_time

            if not self.progress.is_cancelled:
                self.log(
                    f"✅ 完成! 用时 {self.progress.elapsed_time:.1f}s, "
                    f"共 {self.progress.translated_chars} 字"
                )
                if self.on_complete:
                    self.on_complete(self.progress)

        except Exception as e:
            self.progress.is_running = False
            self.log(f"❌ 翻译出错: {e}")
            import traceback
            self.log(traceback.format_exc())
            if self.on_error:
                self.on_error(str(e))

    # ── 控制 ──

    def pause(self):
        self._pause_event.clear()
        self.progress.is_paused = True
        self.log("⏸️ 已暂停")

    def resume(self):
        self._pause_event.set()
        self.progress.is_paused = False
        self.log("▶️ 已恢复")

    def cancel(self):
        self.progress.is_cancelled = True
        self._pause_event.set()
        self.log("🛑 正在取消...")

    # ── API 测试 ──

    def test_api_connection(self):
        try:
            self._init_provider()
            return self.provider.test_connection()
        except Exception as e:
            return False, f"连接失败: {e}"

    # ── 断点管理 ──

    @staticmethod
    def clear_checkpoint(output_file: str, input_file: str):
        cp = CheckpointManager(input_file, output_file)
        cp.clear()

    @staticmethod
    def load_checkpoint_info(checkpoint_path: str):
        if not checkpoint_path or not os.path.exists(checkpoint_path):
            return None
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("completed_chapters", {}), data.get("config_hash", "")
        except Exception:
            return None

    def restore_from_checkpoint(self, checkpoint_path: str, output_path: str, output_format: str = "epub"):
        info = self.load_checkpoint_info(checkpoint_path)
        if info is None:
            self.log("❌ 断点文件加载失败")
            return False
        completed, _ = info
        if not completed:
            self.log("❌ 断点文件中无已完成章节")
            return False

        self.log(f"📌 断点文件包含 {len(completed)} 个已翻译章节")

        chapters_data = []
        if self.config.input_file and os.path.exists(self.config.input_file):
            try:
                chapters = self.get_chapters()
                missing = []
                for ch in chapters:
                    if ch.name in completed:
                        chapters_data.append((ch.name, completed[ch.name]))
                    else:
                        missing.append(ch.name)
                if missing:
                    self.log(f"⚠️ 有 {len(missing)} 个章节未在断点中找到，将跳过")
            except Exception as ex:
                self.log(f"⚠️ 无法读取源文件，按文件名序号排序输出: {ex}")
                chapters_data = list(completed.items())
        else:
            chapters_data = list(completed.items())

        if not chapters_data:
            self.log("❌ 没有可输出的章节")
            return False

        fmt = output_format.lower()
        self.log(f"📦 正在生成 {fmt.upper()} 文件（共 {len(chapters_data)} 章）: {output_path}")
        if fmt == "epub":
            self._write_epub(output_path, chapters_data)
        else:
            self._write_txt(output_path, chapters_data)
        self.log(f"✅ 已保存: {output_path} ({os.path.getsize(output_path)} bytes)")
        return True

    # ============== 翻译修复 (Quality Scan & Retranslation) ==============

    def quality_scan(self, checkpoint_path: str, rules: dict | None = None) -> dict:
        """扫描断点文件中的翻译质量问题。

        Args:
            checkpoint_path: 断点文件路径
            rules: {关键词: 说明}，为 None 或空则不检查

        Returns:
            {chapter_name: [(关键词, 出现次数, 说明), ...]}
        """
        if not rules:
            self.log("ℹ️ 未提供检查规则，请在输入框中填写要检查的关键词")
            return {}

        info = self.load_checkpoint_info(checkpoint_path)
        if info is None:
            self.log("❌ 无法加载断点文件进行质量扫描")
            return {}

        completed, _ = info
        issues = {}
        for ch_name, text in completed.items():
            ch_issues = []
            for keyword, hint in rules.items():
                count = text.count(keyword)
                if count > 0:
                    ch_issues.append((keyword, count, hint))
            if ch_issues:
                issues[ch_name] = ch_issues
        return issues

    def retranslate_chapters(
        self,
        checkpoint_path: str,
        chapter_names: list[str],
        output_path: str | None = None,
        output_format: str = "epub",
        on_retranslate_progress=None,
    ) -> bool:
        """选择性重翻指定章节并更新断点"""
        if not checkpoint_path or not os.path.exists(checkpoint_path):
            self.log("❌ 断点文件不存在")
            return False

        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                cp_data = json.load(f)
        except Exception as e:
            self.log(f"❌ 加载断点失败: {e}")
            return False

        completed = cp_data.get("completed_chapters", {})
        if not completed:
            self.log("❌ 断点文件无已翻译章节")
            return False

        if not self.config.input_file or not os.path.exists(self.config.input_file):
            self.log("❌ 源 EPUB 文件未指定或不存在")
            return False

        all_chapters = self.get_chapters()
        name_to_chapter = {ch.name: ch for ch in all_chapters}

        valid_names = [n for n in chapter_names if n in name_to_chapter and n in completed]
        if not valid_names:
            self.log("❌ 指定的章节均不在断点中或源文件中找不到")
            return False

        skipped = set(chapter_names) - set(valid_names)
        if skipped:
            self.log(f"⚠️ 跳过 {len(skipped)} 个无效章节: {', '.join(skipped)}")

        self.log(f"🔄 开始重翻 {len(valid_names)} 个章节...")

        self._init_provider()
        self.glossary = self.load_glossary()
        self.system_prompt = self.build_system_prompt()
        self.progress.is_cancelled = False

        for idx, ch_name in enumerate(valid_names):
            if self.progress.is_cancelled:
                self.log("❌ 重翻已取消")
                break

            chapter = name_to_chapter[ch_name]
            self.log(f"📝 [{idx+1}/{len(valid_names)}] 重翻: {ch_name}")

            if on_retranslate_progress:
                on_retranslate_progress(idx + 1, len(valid_names), ch_name)

            chunks = self.split_text(chapter.content)
            translated_parts = self._translate_chunks(chunks)
            translated_content = "\n".join(translated_parts)
            completed[ch_name] = translated_content

        cp_data["completed_chapters"] = completed
        try:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(cp_data, f, ensure_ascii=False, indent=2)
            self.log(f"💾 断点已更新: {checkpoint_path}")
        except Exception as e:
            self.log(f"❌ 保存断点失败: {e}")
            return False

        if output_path:
            chapters_data = list(completed.items())
            fmt = output_format.lower()
            self.log(f"📦 正在生成 {fmt.upper()} 文件（共 {len(chapters_data)} 章）: {output_path}")
            if fmt == "epub":
                self._write_epub(output_path, chapters_data)
            else:
                self._write_txt(output_path, chapters_data)
            self.log(f"✅ 已保存: {output_path} ({os.path.getsize(output_path)} bytes)")

        self.log(f"✅ 重翻完成! 共 {len(valid_names)} 章")
        return True
