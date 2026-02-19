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
from bs4 import BeautifulSoup, NavigableString

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
    output_file: str = "novel.txt"
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

    # ── DeepSeek Beta 功能（仅 provider="openai" + 官方 deepseek.com API Key 时生效）──
    # 启用后 base_url 自动切换至 https://api.deepseek.com/beta
    deepseek_beta: bool = False
    # 对话前缀续写（Beta）：注入空 assistant prefix，强制模型直接输出翻译正文，不输出废话前置
    use_prefix_completion: bool = False
    # FIM 补全（Beta）：Fill In the Middle，仅 deepseek-chat 支持，deepseek-reasoner 不支持
    use_fim_completion: bool = False
    # 是否开启翻译过程的流式日志输出（逐块/逐 token 回调）
    stream_logs: bool = False


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
        # 流式回调（接收流式 chunk）
        self.on_stream: Optional[Callable] = None
        # 暂停后下次启动时是否重新加载外部配置
        self._pending_reload_on_start: bool = False

    # ── 术语表/回显清理辅助 ──

    @staticmethod
    def _is_glossary_line(line: str) -> bool:
        return bool(re.match(r'^\s*[-•]?\s*.+\s*(?:->|→|＝|=)\s*.+$', line))

    @staticmethod
    def _is_prompt_header_line(line: str) -> bool:
        return bool(re.match(
            r'^[\s\[【]*'
            r'(?:待翻译(?:原文|文本|内容)?|原文|源文|译文(?:本)?|翻译(?:文本|结果|内容)?)'
            r'[\s\]】]*[:：]?\s*$',
            line
        ))

    @staticmethod
    def _is_non_story_meta_line(line: str) -> bool:
        text = (line or "").strip()
        if not text:
            return False
        lower = text.lower()
        meta_keywords = (
            "评价", "感想", "错别字", "反馈", "收藏", "点赞", "打赏", "评论",
            "レビュー", "感想", "誤字", "評価", "ブックマーク", "ポイント", "いいね",
        )
        hit_count = sum(1 for kw in meta_keywords if kw in text)
        if hit_count >= 2:
            return True
        if lower.startswith("如果您能给予评价") or lower.startswith("よろしければ評価"):
            return True
        if "http://" in lower or "https://" in lower:
            return True
        return False

    def _looks_like_prompt_echo(self, text: str, original_text: str) -> bool:
        if not text or not text.strip():
            return True

        # 明显提示词/术语表回显
        if re.search(r'(强制术语表|术语表|待翻译|原文|译文|翻译文本|翻译结果)', text):
            return True

        # 术语表样式行过多
        glossary_hits = sum(1 for ln in text.splitlines() if self._is_glossary_line(ln))
        if glossary_hits >= 3:
            return True

        # 输出包含原文片段
        ot = (original_text or "").strip()
        if ot:
            sample = ot[:80].strip()
            if sample and sample in text:
                return True
            for ln in ot.splitlines():
                ln = ln.strip()
                if len(ln) >= 8 and ln in text:
                    return True

        # 日文假名占比过高，疑似原文回显
        kana = sum(1 for ch in text if '\u3040' <= ch <= '\u30ff')
        alpha = sum(1 for ch in text if ch.isalpha())
        if alpha > 0 and (kana / alpha) > 0.10:
            return True

        return False

    def _fallback_translate_without_prefix(self, user_content: str) -> str:
        """检测到回显时的回退策略：临时关闭 prefix 续写并重试一次。"""
        if not self.provider:
            return ""
        # 尝试关闭 prefix 续写
        if hasattr(self.provider, "use_prefix_completion"):
            orig = getattr(self.provider, "use_prefix_completion", False)
            try:
                if orig:
                    setattr(self.provider, "use_prefix_completion", False)
                    return self.provider.translate(self.system_prompt, user_content, assistant_prefix="")
            finally:
                try:
                    setattr(self.provider, "use_prefix_completion", orig)
                except Exception:
                    pass
        # 普通重试（不带 assistant_prefix）
        try:
            return self.provider.translate(self.system_prompt, user_content, assistant_prefix="")
        except Exception:
            return ""

    def _get_assistant_prefix(self) -> str:
        # DeepSeek Beta 前缀续写更容易回显，术语表已并入 system_prompt，避免重复注入
        if self.config.deepseek_beta and self.config.use_prefix_completion:
            return ""
        return self.build_assistant_glossary()

    # ── 日志 ──

    def log(self, message: str):
        if self.on_log:
            try:
                self.on_log(message)
            except UnicodeEncodeError:
                # 某些控制台编码不支持 emoji/特殊字符，降级输出为可编码文本
                import sys
                enc = getattr(sys.stdout, "encoding", None) or "utf-8"
                safe = message.encode(enc, errors="ignore").decode(enc, errors="ignore")
                try:
                    self.on_log(safe)
                except Exception:
                    pass

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
            deepseek_beta=self.config.deepseek_beta,
            use_prefix_completion=self.config.use_prefix_completion,
            use_fim_completion=self.config.use_fim_completion,
        )
        if self.config.deepseek_beta:
            mode = "FIM补全" if self.config.use_fim_completion else ("前缀续写" if self.config.use_prefix_completion else "Beta模式")
            self.log(f"✅ {self.provider.provider_name} 已初始化 ({self.config.model_name}) [DeepSeek Beta · {mode}]")
        else:
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
                "グリージャー的中文名始终为\u201c安涅莉丝\u201d（不可出现\u201c格里杰尔\u201c格里杰\u201d等音译变体）。"
                "当原文出现全名时（如アネスト・グリージャー），译为\u201c安涅莉丝·格里杰尔\u201d。\n\n"
                "翻译预设：简洁准确，紧贴原文，语意连贯的短句合并为流畅长句，不添加原文没有的修辞和语气。\n"
            )
        # 将术语表合并到 system prompt，确保各类模型/接口均能稳定获取术语约束。
        g = glossary_dict if glossary_dict is not None else self.glossary
        glossary_block = self.build_assistant_glossary(g)
        if glossary_block:
            base_prompt = base_prompt.rstrip()
            base_prompt = f"{base_prompt}\n\n{glossary_block.strip()}"
        return base_prompt

    def build_assistant_glossary(self, glossary_dict: dict | None = None) -> str:
        """构建放在 assistant 前缀中的术语表文本（返回空字符串表示无术语表）"""
        g = glossary_dict if glossary_dict is not None else self.glossary
        if not g:
            return ""
        glossary_text = "【强制术语表】\n"
        for k, v in g.items():
            glossary_text += f"- {k} -> {v}\n"
        return glossary_text

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
        media_tags = {'img', 'image', 'svg'}

        for element in body.children:
            if isinstance(element, str):
                # 裸文本节点
                stripped = element.strip()
                if stripped:
                    segments.append({
                        "type": "text",
                        "tag": "",
                        "text": stripped,
                        "html": stripped,
                        "attrs": {},
                        "translate": True,
                        "contains_media": False,
                    })
                    text_parts.append(stripped)
                continue

            tag_name = getattr(element, 'name', None)
            if not tag_name:
                continue

            if tag_name in TranslatorEngine._SKIP_TAGS:
                # 图片、表格等不翻译，原样保留
                seg_type = "image" if tag_name in media_tags else "skip"
                segments.append({
                    "type": seg_type,
                    "tag": tag_name,
                    "text": "",
                    "html": str(element),
                    "attrs": dict(element.attrs) if hasattr(element, 'attrs') else {},
                    "translate": False,
                    "contains_media": tag_name in media_tags,
                })
                continue

            if tag_name in TranslatorEngine._BLOCK_TAGS or tag_name.startswith('h'):
                # 块级元素——提取文本用于翻译，保留内联标签结构
                inner_text = element.get_text(strip=True)
                contains_media = bool(element.find(media_tags))
                if not inner_text:
                    # 空块级元素（可能含图片），保留原样
                    segments.append({
                        "type": "skip",
                        "tag": tag_name,
                        "text": "",
                        "html": str(element),
                        "attrs": {},
                        "translate": False,
                        "contains_media": contains_media,
                    })
                    continue
                seg_type = "heading" if tag_name.startswith('h') else "text"
                # heading 保持原样；正文段落可翻译（即使包含插图）
                translatable_text = TranslatorEngine._extract_translatable_text_from_node(element)
                can_translate = (seg_type == "text") and bool(translatable_text)
                segments.append({
                    "type": seg_type,
                    "tag": tag_name,
                    "text": translatable_text if can_translate else inner_text,
                    "html": str(element),
                    "attrs": dict(element.attrs) if hasattr(element, 'attrs') else {},
                    "translate": can_translate,
                    "contains_media": contains_media,
                })
                if can_translate:
                    text_parts.append(translatable_text)
                continue

            # 其他元素（如 section, article, div 嵌套）——递归提取
            inner_text = element.get_text(separator="\n", strip=True)
            contains_media = bool(element.find(media_tags))
            if inner_text:
                translatable_text = TranslatorEngine._extract_translatable_text_from_node(element)
                can_translate = bool(translatable_text)
                segments.append({
                    "type": "text",
                    "tag": tag_name,
                    "text": translatable_text if can_translate else inner_text,
                    "html": str(element),
                    "attrs": dict(element.attrs) if hasattr(element, 'attrs') else {},
                    "translate": can_translate,
                    "contains_media": contains_media,
                })
                if can_translate:
                    text_parts.append(translatable_text)
            else:
                segments.append({
                    "type": "skip",
                    "tag": tag_name,
                    "text": "",
                    "html": str(element),
                    "attrs": dict(element.attrs) if hasattr(element, 'attrs') else {},
                    "translate": False,
                    "contains_media": contains_media,
                })

        plain_text = "\n".join(text_parts)
        return plain_text, segments

    @staticmethod
    def _is_heading_tag(tag_name: str) -> bool:
        return bool(tag_name and re.fullmatch(r"h[1-6]", str(tag_name).lower()))

    @staticmethod
    def _has_heading_ancestor(node) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            pname = getattr(parent, "name", None)
            if TranslatorEngine._is_heading_tag(pname):
                return True
            parent = getattr(parent, "parent", None)
        return False

    @staticmethod
    def _iter_translatable_text_nodes(root):
        for node in root.descendants:
            if not isinstance(node, NavigableString):
                continue
            raw = str(node)
            if not raw or not raw.strip():
                continue
            parent = getattr(node, "parent", None)
            pname = getattr(parent, "name", "") if parent else ""
            pname = pname.lower() if isinstance(pname, str) else str(pname).lower()
            if pname in TranslatorEngine._SKIP_TAGS:
                continue
            # Ruby标签内的文本也需要翻译，所以不跳过Ruby标签
            # 章节/小节标题保持原样，不参与翻译替换
            if TranslatorEngine._has_heading_ancestor(node):
                continue
            yield node

    @staticmethod
    def _extract_translatable_text_from_node(root) -> str:
        lines = [str(n).strip() for n in TranslatorEngine._iter_translatable_text_nodes(root)]
        return "\n".join(x for x in lines if x)

    @staticmethod
    def _split_text_by_lengths(text: str, lengths: list[int]) -> list[str]:
        if not lengths:
            return []
        if len(lengths) == 1:
            return [text]
        total_chars = len(text)
        if total_chars <= 0:
            return [""] * len(lengths)

        weights = [max(int(l), 1) for l in lengths]
        weight_sum = sum(weights)
        raw = [(total_chars * w) / weight_sum for w in weights]
        base = [int(x) for x in raw]
        remain = total_chars - sum(base)
        if remain > 0:
            order = sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)
            for i in order[:remain]:
                base[i] += 1

        chunks = []
        cursor = 0
        for size in base[:-1]:
            chunks.append(text[cursor:cursor + size])
            cursor += size
        chunks.append(text[cursor:])
        return chunks

    @staticmethod
    def _inject_translation_into_segment_html(segment_html: str, translated_text: str) -> str:
        if not segment_html:
            return ""

        normalized = re.sub(r"\s*\n\s*", "", (translated_text or "").strip())
        if not normalized:
            return segment_html

        wrapper = BeautifulSoup(f"<div>{segment_html}</div>", "html.parser")
        container = wrapper.find("div")
        if not container:
            return segment_html

        text_nodes = list(TranslatorEngine._iter_translatable_text_nodes(container))
        if not text_nodes:
            return segment_html

        lengths = [len(str(n).strip()) for n in text_nodes]
        chunks = TranslatorEngine._split_text_by_lengths(normalized, lengths)
        for node, chunk in zip(text_nodes, chunks):
            original = str(node)
            prefix = re.match(r"^\s*", original).group(0) if original else ""
            suffix = re.search(r"\s*$", original).group(0) if original else ""
            node.replace_with(NavigableString(f"{prefix}{chunk}{suffix}"))

        return "".join(str(x) for x in container.contents)

    @staticmethod
    def _preserve_ruby_annotations(original_html: str, translated_text: str) -> str:
        """
        在翻译文本中保留Ruby注音标签
        
        Args:
            original_html: 包含Ruby标签的原始HTML
            translated_text: 翻译后的纯文本
            
        Returns:
            保留了Ruby标签结构的HTML
        """
        if not original_html or not translated_text:
            return original_html
        
        # 解析原始HTML以保留Ruby结构
        soup = BeautifulSoup(original_html, "html.parser")
        
        # 查找所有的ruby标签及其内容
        ruby_elements = soup.find_all('ruby')
        
        if not ruby_elements:
            # 如果没有Ruby标签，直接返回原始处理结果
            return TranslatorEngine._inject_translation_into_segment_html(original_html, translated_text)
        
        # 提取Ruby标签的结构信息
        ruby_mappings = {}
        for ruby in ruby_elements:
            rb_text = ""
            rt_text = ""
            
            # 获取ruby标签内的文本和注音
            for child in ruby.children:
                if hasattr(child, 'name'):
                    if child.name == 'rb':
                        rb_text = child.get_text(strip=True)
                    elif child.name == 'rt':
                        rt_text = child.get_text(strip=True)
                    elif child.name == 'rp':  # 可选的括号标签
                        continue
                elif isinstance(child, NavigableString) and child.strip():
                    rb_text += str(child).strip()
            
            if rb_text and rt_text:
                # 存储Ruby基础文本到注音的映射
                ruby_mappings[rb_text] = rt_text
        
        if not ruby_mappings:
            return TranslatorEngine._inject_translation_into_segment_html(original_html, translated_text)
        
        # 使用基础的文本注入方法先填充翻译文本
        basic_result = TranslatorEngine._inject_translation_into_segment_html(original_html, translated_text)
        
        # 重新解析结果，以便我们可以安全地修改它
        result_soup = BeautifulSoup(basic_result, "html.parser")
        
        # 为result_soup中的每个ruby标签找到对应的注音
        result_rubies = result_soup.find_all('ruby')
        for ruby in result_rubies:
            # 检查是否已经有rb和rt标签
            rb_tag = ruby.find('rb')
            if rb_tag:
                rb_text = rb_tag.get_text(strip=True)
                # 检查原始映射中是否有对应的注音
                if rb_text in ruby_mappings:
                    rt_text = ruby_mappings[rb_text]
                    # 确保rt标签存在且内容正确
                    rt_tag = ruby.find('rt')
                    if rt_tag:
                        rt_tag.string = rt_text
                    else:
                        # 如果没有rt标签，创建一个
                        new_rt = result_soup.new_tag('rt')
                        new_rt.string = rt_text
                        ruby.append(new_rt)
        
        return str(result_soup)

    @staticmethod
    def _attrs_to_html(attrs: dict) -> str:
        if not attrs:
            return ""
        rendered = []
        for key, value in attrs.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                value = " ".join(str(x) for x in value if x is not None)
            elif isinstance(value, bool):
                if value:
                    rendered.append(str(key))
                continue
            else:
                value = str(value)
            escaped = (
                value.replace("&", "&amp;")
                .replace("\"", "&quot;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            rendered.append(f'{key}="{escaped}"')
        if not rendered:
            return ""
        return " " + " ".join(rendered)

    @staticmethod
    def rebuild_chapter_html(segments: list[dict], translated_text: str, original_html: str = "") -> str:
        """将翻译结果回注到原始 HTML 结构中。

        策略：按段落顺序将翻译文本填回对应的 segment，
        保留非文本 segment（图片、表格等）原样不动。
        """
        trans_paragraphs = [p.strip() for p in translated_text.split("\n") if p.strip()]
        expected_segments = sum(
            1
            for seg in segments
            if seg.get("type") in ("text", "heading") and seg.get("translate", True)
        )
        if expected_segments <= 0:
            return "\n".join(seg.get("html", "") for seg in segments)

        if len(trans_paragraphs) > expected_segments:
            if expected_segments == 1:
                trans_paragraphs = ["\n".join(trans_paragraphs)]
            else:
                head = trans_paragraphs[: expected_segments - 1]
                tail = "\n".join(trans_paragraphs[expected_segments - 1 :])
                trans_paragraphs = head + [tail]
        trans_idx = 0
        result_parts = []

        for seg in segments:
            if seg["type"] in ("image", "skip"):
                # 非文本元素原样保留，但确保图片路径正确
                html_content = seg["html"]
                # 修复图片路径引用（确保相对路径正确）
                if seg["type"] == "image" and 'src="' in html_content:
                    # 保持原始图片路径不变，但验证路径格式
                    pass
                result_parts.append(html_content)
            elif seg["type"] in ("text", "heading"):
                if not seg.get("translate", True):
                    result_parts.append(seg["html"])
                    continue
                if trans_idx < len(trans_paragraphs):
                    trans_content = trans_paragraphs[trans_idx]
                    # 使用新的Ruby标签保留功能
                    rebuilt = TranslatorEngine._preserve_ruby_annotations(
                        seg.get("html", ""), trans_content
                    )
                    result_parts.append(rebuilt if rebuilt else seg["html"])
                    trans_idx += 1
                else:
                    # 翻译段落不足，保留原文
                    result_parts.append(seg["html"])
            else:
                result_parts.append(seg["html"])

        # 兼容兜底：若仍有剩余段落，追加到末尾
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
            # 去除开头的 xx. 前缀（如 01. / Vol.1. / AB. 等英文/数字前缀）
            title = TranslatorEngine._strip_leading_xx_dot(title)
            return title, body

        title = f"第{fallback_index}章" if fallback_index is not None else first_line[:20]
        title = TranslatorEngine._strip_leading_xx_dot(title)
        return title, content.strip()

    @staticmethod
    def _strip_leading_xx_dot(title: str) -> str:
        """移除标题开头的 ASCII/数字/连字符等前缀并随 dot 的模式，例如: '01. ', 'Vol.01-', 'AB.' 等。

        只匹配英文/数字/连字符/下划线前缀，以避免误删中文开头的有效词。
        """
        if not title:
            return title
        # 匹配一个或多个以字母/数字/连字符/下划线组成的段，后接点或点与连字符，然后删除
        new = re.sub(r'^\s*(?:[A-Za-z0-9_\-]{1,12}[\.．\-])+\s*', '', title)
        return new.strip()

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
                # 支持可选的流式输出：通过 on_stream 回调逐块接收模型输出
                if hasattr(self.provider, 'translate') and self.config.stream_logs:
                    acc: list[str] = []

                    def _stream_cb(chunk: str):
                        try:
                            acc.append(chunk)
                            if self.on_stream:
                                self.on_stream(chunk)
                            else:
                                # 仍然输出至普通日志回调，便于兼容 UI
                                self.log(chunk)
                        except Exception:
                            pass

                    assistant_pref = self._get_assistant_prefix()
                    result = self.provider.translate(self.system_prompt, user_content, assistant_prefix=assistant_pref, stream=True, stream_callback=_stream_cb)
                    # 如果 provider 返回了最终合并结果，优先使用；否则合并 acc
                    if not result and acc:
                        result = "".join(acc)
                else:
                    assistant_pref = self._get_assistant_prefix()
                    result = self.provider.translate(self.system_prompt, user_content, assistant_prefix=assistant_pref)
                # 尝试清理模型可能回显的提示词/术语表/原文，防止注入到最终译文中
                try:
                    cleaned = self._clean_model_output(result, text)
                except Exception:
                    cleaned = result
                # 若检测到回显，回退到非前缀续写再试一次
                if self._looks_like_prompt_echo(cleaned, text):
                    self.log("⚠️ 检测到提示词/术语表回显，尝试回退模式重试一次")
                    fallback = self._fallback_translate_without_prefix(user_content)
                    if fallback:
                        try:
                            cleaned_fb = self._clean_model_output(fallback, text)
                        except Exception:
                            cleaned_fb = fallback
                        if not self._looks_like_prompt_echo(cleaned_fb, text):
                            return cleaned_fb
                return cleaned
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

    def _clean_model_output(self, result: str, original_text: str) -> str:
        """
        清理模型输出中可能被回显的提示词、术语表或原文。
        使用若干启发式规则：
        - 若包含 '【译文】' 标记，则取其之后内容；
        - 删除显式的术语表块（如以 '【强制术语表】' 开头的列表）；
        - 若输出包含原文片段，则尝试移除原文；
        - 去除常见的提示头（如 '翻译要求'）以及多余的前缀分隔符。
        - 修复字符编码问题和中日文混杂问题
        这些规则为防护性处理，避免将 meta 信息写入 checkpoint 或最终文件。
        """
        if not result:
            return result
        text = result.replace("\r\n", "\n").replace("\r", "\n")
        
        # 保存原始结果作为备选，以防清理后内容为空
        original_result = text
    
        # 字符编码规范化处理
        # 确保统一使用UTF-8编码，处理可能的编码问题
        try:
            # 如果文本包含异常字符，尝试重新编码
            text = text.encode('utf-8', errors='ignore').decode('utf-8')
        except:
            pass
    
        # 检测并修复中日文混杂问题
        # 如果检测到大量日文字符，记录日文比例但不过度干预
        japanese_chars = sum(1 for c in text if '\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff')
        total_chars = len(text.strip())
            
        if total_chars > 0 and japanese_chars / total_chars > 0.3:
            # 如果日文字符占比超过30%，记录警告但保留内容
            self.log(f"⚠️ 检测到高比例日文字符 ({japanese_chars/total_chars:.1%})，可能是翻译不完整或包含原文")
    
        # 优先截取最后一个"译文"标记之后的内容（兼容多种写法）
        m_last = None
        for m in re.finditer(r"(?:^|\n)\s*[\[【]?\s*译文\s*[\]】]?\s*[:：]?\s*", text):
            m_last = m
        if m_last:
            text = text[m_last.end():]
    
        # 删除强制术语表块（显式标题）
        if "术语表" in text:
            lines = text.splitlines()
            out_lines = []
            skip = False
            for ln in lines:
                if "术语表" in ln:
                    skip = True
                    continue
                if skip:
                    if ln.strip() == "":
                        skip = False
                        continue
                    if self._is_glossary_line(ln):
                        continue
                    skip = False
                out_lines.append(ln)
            text = "\n".join(out_lines)
    
        # 删除散落的术语表行（无标题回显）
        lines = text.splitlines()
        glossary_hits = [i for i, ln in enumerate(lines) if self._is_glossary_line(ln)]
        if glossary_hits:
            near_head = sum(1 for i in glossary_hits if i < 30)
            if near_head >= 2 or len(glossary_hits) >= 4:
                lines = [ln for ln in lines if not self._is_glossary_line(ln)]
                text = "\n".join(lines)
    
        # 删除常见提示头行
        lines = [ln for ln in text.splitlines() if not self._is_prompt_header_line(ln)]
        text = "\n".join(lines)
    
        # 若包含原文（或前文标记），尝试逐行移除原文片段
        try:
            ot = (original_text or "").strip()
            if ot:
                for ln in [l.strip() for l in ot.splitlines() if l.strip()]:
                    if len(ln) >= 4 and ln in text:
                        text = text.replace(ln, "")
        except Exception:
            pass
    
        # 删除常见提示区域（例如以 '翻译要求' 开头的一段）
        text = re.sub(r"翻译要求[:：\s\S]*?(?:\n\s*\n)", "", text)
    
        # 去除前导分割符与多余符号
        text = re.sub(r'^[\s\-_=#\*\[\]]+', '', text).strip()
    
        # 收敛多余空行
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    
        # 检查清理后的文本是否为空，如果为空则返回原始结果
        if not text.strip():
            self.log("⚠️ 清理后文本为空，返回原始结果以避免丢失内容")
            text = original_result
        
        # 最终编码验证
        try:
            text = text.encode('utf-8').decode('utf-8')
        except:
            # 如果仍有编码问题，使用更宽松的处理
            text = text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
        
        return text

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
            lower_name = name.lower() if isinstance(name, str) else str(name).lower()
            base_name = os.path.basename(lower_name)
            if base_name in {
                "nav.xhtml", "nav.html",
                "toc.xhtml", "toc.html",
                "cover.xhtml", "cover.html",
                "titlepage.xhtml", "titlepage.html",
                "copyright.xhtml", "copyright.html",
            }:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            raw_content = item.get_content()
            html_str = raw_content.decode('utf-8', errors='replace') if isinstance(raw_content, bytes) else str(raw_content)
            clean_text, _ = self.parse_html_structured(html_str)
            if len(clean_text) >= 50:
                # 同时存储原始 HTML 以便后续结构保留
                chapters.append(ChapterInfo(idx + 1, name, clean_text, item, html_content=html_str))
        return chapters

    # ── 上下文注入 ──

    def _get_context_tail(self, text: str, n_lines: int | None = None) -> str:
        if n_lines is None:
            n_lines = self.config.context_lines
        if not text or n_lines <= 0:
            return ""
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        filtered = [l for l in lines if not self._is_non_story_meta_line(l)]
        if filtered:
            lines = filtered
        tail = lines[-n_lines:] if len(lines) > n_lines else lines
        return "\n".join(tail)

    # ── 分块翻译 ──

    def _translate_chunks(self, chunks: list[str], initial_prev_ctx: str = "") -> list[str]:
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
            prev_ctx = initial_prev_ctx if context_lines > 0 else ""
            for i, chunk in enumerate(chunks):
                if self.progress.is_cancelled:
                    break
                idx, result = _do(i, chunk, prev_ctx)
                results[idx] = result
                prev_ctx = self._get_context_tail(result, context_lines)
        else:
            if context_lines > 0:
                self.log("💡 并发模式下上下文注入仅在批次间生效")
            batch_prev_ctx = initial_prev_ctx if context_lines > 0 else ""
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
            src_name = os.path.splitext(os.path.basename(self.config.input_file))[0]
            source_identifier = "novel-translator-output"
            source_title = src_name
            source_language = "zh"
            source_creators = []

            try:
                meta_id = source_book.get_metadata("DC", "identifier")
                if meta_id and meta_id[0] and meta_id[0][0]:
                    source_identifier = str(meta_id[0][0])
            except Exception:
                pass

            try:
                meta_title = source_book.get_metadata("DC", "title")
                if meta_title and meta_title[0] and meta_title[0][0]:
                    source_title = str(meta_title[0][0])
            except Exception:
                pass

            try:
                meta_lang = source_book.get_metadata("DC", "language")
                if meta_lang and meta_lang[0] and meta_lang[0][0]:
                    source_language = str(meta_lang[0][0])
            except Exception:
                pass

            try:
                meta_creator = source_book.get_metadata("DC", "creator")
                for creator in meta_creator or []:
                    if isinstance(creator, (list, tuple)) and creator and creator[0]:
                        source_creators.append(str(creator[0]))
            except Exception:
                pass

            # 书名与作者沿用原始元数据，避免改变导入样式
            book.set_identifier(source_identifier)
            book.set_title(source_title)
            book.set_language(source_language)
            if source_creators:
                for creator in source_creators:
                    book.add_author(creator)
            else:
                book.add_author("AI Translation")

            # 复制所有非文档资源（CSS、图片、字体等）
            resource_items = []
            copied_items = set()  # 跟踪已复制的项目ID
            copied_names = set()  # 跟踪已复制的文件名，避免重复
            
            for item in source_book.get_items():
                item_type = item.get_type()
                item_id = item.get_id()
                item_name = item.get_name()
                
                # 避免重复添加相同ID或文件名的项目
                if item_id in copied_items or item_name in copied_names:
                    continue
                    
                if item_type == ebooklib.ITEM_DOCUMENT:
                    continue  # 章节文档单独处理
                    
                try:
                    # 检查是否是有效的资源类型
                    if item_type in (ebooklib.ITEM_STYLE, ebooklib.ITEM_IMAGE,
                                   ebooklib.ITEM_FONT, ebooklib.ITEM_COVER):
                        # 确保资源内容有效
                        content = item.get_content()
                        if content:  # 只有当内容不为空时才复制
                            book.add_item(item)
                            resource_items.append(item)
                            copied_items.add(item_id)
                            copied_names.add(item_name)
                    elif item_type not in (ebooklib.ITEM_NAVIGATION,):
                        # 其他资源（如嵌入字体、音频等）也尝试复制
                        content = item.get_content()
                        if content:
                            book.add_item(item)
                            resource_items.append(item)
                            copied_items.add(item_id)
                            copied_names.add(item_name)
                except Exception as e:
                    self.log(f"⚠️ 跳过资源 {item_id} ({item_name}): {str(e)[:50]}")
                    continue
                    
            if resource_items:
                self.log(f"📂 已复制 {len(resource_items)} 个原始资源（CSS/图片/字体）")
                # 验证关键资源是否成功复制
                image_count = sum(1 for item in resource_items if item.get_type() == ebooklib.ITEM_IMAGE)
                style_count = sum(1 for item in resource_items if item.get_type() == ebooklib.ITEM_STYLE)
                font_count = sum(1 for item in resource_items if item.get_type() == ebooklib.ITEM_FONT)
                cover_count = sum(1 for item in resource_items if item.get_type() == ebooklib.ITEM_COVER)
                self.log(f"   - 图片: {image_count} 个")
                self.log(f"   - 样式: {style_count} 个") 
                self.log(f"   - 字体: {font_count} 个")
                self.log(f"   - 封面: {cover_count} 个")
                
                # 验证图片资源的完整性
                if image_count > 0:
                    valid_images = 0
                    for item in resource_items:
                        if item.get_type() == ebooklib.ITEM_IMAGE:
                            try:
                                content = item.get_content()
                                if content and len(content) > 0:
                                    valid_images += 1
                            except:
                                pass
                    self.log(f"   - 有效图片: {valid_images} 个")
        else:
            book.set_identifier("novel-translator-output")
            src_name = os.path.splitext(os.path.basename(self.config.output_file))[0]
            # 去除可能的前缀/尾部注记
            def _sanitize_simple(t: str) -> str:
                if not t:
                    return t
                t = re.sub(r'^\s*[\dA-Za-z一二三四五六七八九零十]+[\.\-_\s]+', '', t)
                t = re.sub(r'[\s　]*[（(]\s*中文翻译\s*[)）]\s*$', '', t)
                return t.strip()

            book.set_title(_sanitize_simple(src_name))
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

                    # 尝试在原始 HTML 结构中替换文本
                    raw = item.get_content()
                    html_str = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else str(raw)
                    orig_soup = BeautifulSoup(html_str, "html.parser")
                    original_doc_title = getattr(item, "title", None) or ""
                    if not original_doc_title:
                        title_tag = orig_soup.find("title")
                        if title_tag:
                            original_doc_title = title_tag.get_text(strip=True)
                    if not original_doc_title:
                        original_doc_title = os.path.splitext(os.path.basename(name))[0]

                    _, segments = self.parse_html_structured(html_str)

                    if segments:
                        # 结构保留模式：将翻译文本回注到原始 HTML 结构
                        translated_body_html = self.rebuild_chapter_html(
                            segments, translated_content, original_html=html_str
                        )
                    else:
                        # 无法解析结构，回退到简单包装
                        translated_body_html = self._text_to_html_paragraphs(translated_content)

                    # 从原始 HTML 提取 <head> 部分（保留 CSS 链接和元数据）
                    head_tag = orig_soup.find("head")
                    if head_tag:
                        # 保留原始head中的所有内容，包括CSS链接、meta标签等
                        head_html = str(head_tag)
                        # 确保语言设置为中文
                        if 'lang=' not in head_html:
                            head_html = head_html.replace('<head>', '<head lang="zh">')
                    else:
                        safe_title = original_doc_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        head_html = f'<head lang="zh"><meta charset="utf-8"/><title>{safe_title}</title></head>'

                    # 确保HTML结构完整且编码正确
                    full_html = (
                        f'<?xml version="1.0" encoding="utf-8"?>\n'
                        f'<!DOCTYPE html>\n'
                        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="zh">\n'
                        f'{head_html}\n'
                        f'<body>\n{translated_body_html}\n</body>\n</html>'
                    )
                    
                    # 验证生成的HTML是否包含关键元素
                    if '<img' in full_html.lower():
                        img_count = full_html.lower().count('<img')
                        self.log(f"   🖼️ 章节包含 {img_count} 个图片引用")

                    ch = epub.EpubHtml(
                        title=original_doc_title,
                        file_name=name,  # 保留原始文件名
                        lang=getattr(item, "lang", None) or source_language,
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
        
        # 写入EPUB文件
        epub.write_epub(output_path, book)
        
        # 验证输出文件
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            self.log(f"✅ EPUB文件已生成: {output_path} ({file_size:,} bytes)")
            
            # 检查是否包含图片资源
            try:
                output_book = epub.read_epub(output_path)
                image_items = [item for item in output_book.get_items() if item.get_type() == ebooklib.ITEM_IMAGE]
                if image_items:
                    self.log(f"   🖼️ 包含 {len(image_items)} 个图片资源")
                    # 验证图片完整性
                    valid_images = 0
                    for img_item in image_items:
                        try:
                            content = img_item.get_content()
                            if content and len(content) > 0:
                                valid_images += 1
                        except:
                            pass
                    self.log(f"   ✅ 有效图片: {valid_images} 个")
                else:
                    self.log("   ⚠️ 未检测到图片资源")
                
                # 检查文档结构
                doc_items = [item for item in output_book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT]
                self.log(f"   📄 文档章节: {len(doc_items)} 个")
                
                # 验证文档中的图片引用
                img_references = 0
                for doc_item in doc_items[:5]:  # 检查前5个文档
                    try:
                        content = doc_item.get_content()
                        if isinstance(content, bytes):
                            content_str = content.decode('utf-8', errors='replace')
                        else:
                            content_str = str(content)
                        img_refs = content_str.lower().count('<img')
                        img_references += img_refs
                    except:
                        pass
                
                if img_references > 0:
                    self.log(f"   🔗 图片引用: {img_references} 个")
                else:
                    self.log("   ⚠️ 未检测到图片引用")
                    
            except Exception as e:
                self.log(f"   ⚠️ 无法验证EPUB内容: {str(e)[:50]}")
        else:
            self.log("❌ EPUB文件生成失败")

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
        # 如果之前暂停并可能修改了外部配置文件，则在新启动前尝试重新加载配置
        if getattr(self, '_pending_reload_on_start', False):
            try:
                self.reload_config_from_file()
            except Exception:
                pass
            self._pending_reload_on_start = False

        thread = threading.Thread(target=self._run_translation, daemon=True)
        thread.start()
        return thread

    def reload_config_from_file(self, path: str = "translator_config.json"):
        """从 JSON 配置文件读取允许更新的翻译参数并应用到当前 `self.config`。

        仅覆盖在 JSON 中出现且列入白名单的字段，避免意外重置。
        """
        if not os.path.exists(path):
            self.log(f"ℹ️ 未找到配置文件: {path}")
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.log(f"⚠️ 读取配置文件失败: {e}")
            return False

        allowed = {
            'provider', 'api_key', 'base_url', 'model_name', 'model_type',
            'temperature', 'top_p', 'frequency_penalty', 'presence_penalty', 'max_tokens',
            'chunk_size', 'concurrent_workers', 'retry_count',
            'output_file', 'output_format', 'glossary_file',
            'start_chapter', 'end_chapter', 'custom_prompt', 'context_lines',
            'deepseek_beta', 'use_prefix_completion', 'use_fim_completion', 'stream_logs'
        }

        changed = []
        for k, v in data.items():
            if k in allowed and hasattr(self.config, k):
                try:
                    setattr(self.config, k, v)
                    changed.append(k)
                except Exception:
                    pass

        if changed:
            self.log(f"✅ 已从 {path} 重新加载配置，更新项: {', '.join(changed)}")
            return True
        else:
            self.log(f"ℹ️ 配置文件 {path} 无可更新项")
            return False

    def _run_translation(self):
        try:
            self.progress = TranslationProgress()
            self.progress.is_running = True
            self.progress.start_time = time.time()

            self._init_provider()
            self.glossary = self.load_glossary()
            self.system_prompt = self.build_system_prompt(self.glossary)

            self.log(f"📖 正在读取: {os.path.basename(self.config.input_file)}")
            chapters = self.get_chapters()
            self.log(f"📚 共 {len(chapters)} 个有效章节")

            start = max(0, self.config.start_chapter - 1) if self.config.start_chapter > 0 else 0
            end = (
                self.config.end_chapter
                if 0 < self.config.end_chapter <= len(chapters)
                else len(chapters)
            )
            if not chapters:
                raise ValueError("未找到可翻译章节：EPUB 可能仅包含目录/封面/插图页")
            if start >= len(chapters):
                raise ValueError(
                    f"章节范围无效：起始章节 {self.config.start_chapter} 超出有效范围 1~{len(chapters)}"
                )
            if end <= start:
                req_start = self.config.start_chapter if self.config.start_chapter > 0 else 1
                req_end = self.config.end_chapter if self.config.end_chapter > 0 else len(chapters)
                raise ValueError(
                    f"章节范围无效：起始 {req_start}，结束 {req_end}。有效范围为 1~{len(chapters)}"
                )
            target_chapters = chapters[start:end]
            self.progress.total_chapters = len(target_chapters)
            if not target_chapters:
                raise ValueError(
                    f"章节范围为空：请求 {start+1}~{end}，有效章节共 {len(chapters)} 章"
                )
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
            chapter_prev_ctx = ""

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
                    if self.config.context_lines > 0 and cached:
                        chapter_prev_ctx = self._get_context_tail(cached, self.config.context_lines)
                    self.progress.elapsed_time = time.time() - self.progress.start_time
                    if self.on_progress:
                        self.on_progress(self.progress)
                    continue

                if self.on_chapter_start:
                    self.on_chapter_start(chapter)
                self.log(f"📝 [{i+1}/{len(target_chapters)}] {chapter.name}")

                chunks = self.split_text(chapter.content)
                self.progress.total_chunks = len(chunks)
                translated_parts = self._translate_chunks(chunks, initial_prev_ctx=chapter_prev_ctx)
                # 过滤掉空的翻译部分，但保留非空部分
                filtered_parts = [part for part in translated_parts if part and part.strip()]
                
                if filtered_parts:
                    # 如果有非空的翻译部分，连接它们
                    translated_content = "\n".join(filtered_parts)
                else:
                    # 如果所有部分都是空的，至少记录一个警告信息
                    self.log(f"⚠️ 章节 '{chapter.name}' 的所有翻译块都为空，保留原始内容以避免数据丢失")
                    # 使用原始内容作为占位符，避免完全空白
                    translated_content = f"[翻译失败或为空 - 章节: {chapter.name}]\n{chapter.content[:200]}..." if chapter.content else f"[翻译失败或为空 - 章节: {chapter.name}]"
                
                chapters_data.append((chapter.name, translated_content))
                if self.config.context_lines > 0 and translated_content:
                    chapter_prev_ctx = self._get_context_tail(translated_content, self.config.context_lines)

                if self.config.enable_checkpoint and self.checkpoint:
                    self.checkpoint.mark_chapter_done(chapter.name, translated_content)

                self.progress.elapsed_time = time.time() - self.progress.start_time
                if self.on_progress:
                    self.on_progress(self.progress)

            # 检查是否实际有内容被翻译和写入文件
            output_written = False
            if not self.progress.is_cancelled and chapters_data:
                fmt = self.config.output_format.lower()
                self.log(f"📦 正在生成 {fmt.upper()} 文件（共 {len(chapters_data)} 章）...")
                # 记录章节数据的详细信息
                for i, (filename, content) in enumerate(chapters_data):
                    content_len = len(content) if content else 0
                    japanese_chars = sum(1 for c in content if '\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff') if content else 0
                    ratio = japanese_chars / content_len if content_len > 0 else 0
                    self.log(f"   章节 {i+1}: '{filename}' - 长度 {content_len}, 日文字符比例 {ratio:.2%}")
                
                if fmt == "epub":
                    self._write_epub(self.config.output_file, chapters_data)
                else:
                    self._write_txt(self.config.output_file, chapters_data)
                self.log(f"✅ 已保存: {self.config.output_file}")
                
                # 检查输出文件是否真的被创建且有内容
                if os.path.exists(self.config.output_file):
                    output_size = os.path.getsize(self.config.output_file)
                    self.log(f"📊 输出文件大小: {output_size} 字节")
                    if output_size > 0:
                        output_written = True
                    else:
                        self.log("⚠️ 输出文件已创建但为空")
                else:
                    self.log("❌ 输出文件未创建")
            else:
                self.log(f"⚠️ 未写入输出文件 - 翻译取消: {self.progress.is_cancelled}, 章节数据: {len(chapters_data) if chapters_data else 0}")

            self.progress.is_running = False
            self.progress.elapsed_time = time.time() - self.progress.start_time

            # 仅当实际有内容翻译并写入文件时才触发完成回调
            if not self.progress.is_cancelled and output_written and self.progress.translated_chars > 0:
                self.log(
                    f"✅ 完成! 用时 {self.progress.elapsed_time:.1f}s, "
                    f"共 {self.progress.translated_chars} 字"
                )
                if self.on_complete:
                    self.on_complete(self.progress)
            elif not self.progress.is_cancelled and not output_written:
                self.log("⚠️ 未生成输出文件：指定范围内无有效章节需要翻译或翻译后内容为空")
                # 即使没有输出也要检查是否应该触发完成回调
                if self.on_complete and self.progress.translated_chars > 0:
                    self.log("ℹ️ 存在翻译字符数但未触发完成回调，可能存在输出问题")
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
        # 标记在下次 start_translation 时重新加载外部配置（如 GUI 修改了 translator_config.json）
        self._pending_reload_on_start = True

    def resume(self):
        self._pause_event.set()
        self.progress.is_paused = False
        self.log("▶️ 已恢复")

    def cancel(self):
        self.progress.is_cancelled = True
        self._pause_event.set()
        self.log("🛑 正在取消...")

    def disable_streaming(self):
        """关闭流式输出：

        - 关闭 `config.stream_logs`，停止以流回调输出部分结果。
        - 暂存并清除 `on_stream` 回调，恢复为普通日志输出。
        """
        try:
            # 备份现有回调以便恢复
            self._saved_on_stream = getattr(self, "on_stream", None)
            self.on_stream = None
        except Exception:
            self._saved_on_stream = None
        self.config.stream_logs = False
        self.log("ℹ️ 已关闭流式输出，日志恢复为普通模式")

    def restore_streaming(self):
        """恢复先前的流式输出设置：

        - 如果之前使用 `disable_streaming()` 暂存了回调，则恢复该回调。
        - 将 `config.stream_logs` 置回 True（如需仅恢复回调而不启用流式，请手动调整）。
        """
        prev = getattr(self, "_saved_on_stream", None)
        if prev:
            self.on_stream = prev
            try:
                delattr(self, "_saved_on_stream")
            except Exception:
                pass
        self.config.stream_logs = True
        self.log("ℹ️ 已恢复流式输出（已启用 stream_logs=True 且恢复了先前的 on_stream 回调）")

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
        self.system_prompt = self.build_system_prompt(self.glossary)
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
            # 如果 provider 支持 DeepSeek FIM 补全且配置启用，则对每个 chunk 使用 FIM 模式进行重翻
            translated_parts = []
            if getattr(self.provider, 'deepseek_beta', False) and getattr(self.provider, 'use_fim_completion', False):
                self.log("💡 使用 FIM 补全进行选择性重翻（若模型支持）")
                assistant_pref = self._get_assistant_prefix()
                for c in chunks:
                    if self.progress.is_cancelled:
                        break
                    try:
                        part = self.provider.translate(self.system_prompt, c, assistant_prefix=assistant_pref, stream=False)
                    except Exception as e:
                        self.log(f"⚠️ FIM 重翻失败，回退到标准翻译: {e}")
                        part = self.provider.translate(self.system_prompt, c, assistant_prefix=assistant_pref)
                    translated_parts.append(part)
            else:
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
