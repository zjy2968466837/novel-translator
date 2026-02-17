# -*- coding: utf-8 -*-
"""
novel_translator.providers - AI Provider 抽象层

支持的 Provider:
- OpenAI 兼容 (DeepSeek / Qwen / GPT / SiliconFlow 等)
- Anthropic (Claude)
- Google (Gemini)
- Ollama (本地模型，复用 OpenAI 兼容接口)
"""

import re
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ===== Provider 注册表 =====
PROVIDER_PRESETS = {
    "openai": {
        "name": "OpenAI 兼容",
        "default_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3.2",
        "models": [
            {"name": "DeepSeek V3.2", "model": "deepseek-ai/DeepSeek-V3.2", "url": "https://api.siliconflow.cn/v1"},
            {"name": "DeepSeek R1", "model": "deepseek-ai/DeepSeek-R1-0528", "url": "https://api.siliconflow.cn/v1"},
            {"name": "Qwen3 32B", "model": "Qwen/Qwen3-32B", "url": "https://api.siliconflow.cn/v1"},
            {"name": "Qwen3 8B", "model": "Qwen/Qwen3-8B", "url": "https://api.siliconflow.cn/v1"},
            {"name": "GPT-4o", "model": "gpt-4o", "url": "https://api.openai.com/v1"},
            {"name": "GPT-4o mini", "model": "gpt-4o-mini", "url": "https://api.openai.com/v1"},
        ],
    },
    "anthropic": {
        "name": "Anthropic",
        "default_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-20250514",
        "models": [
            {"name": "Claude Sonnet 4", "model": "claude-sonnet-4-20250514", "url": "https://api.anthropic.com"},
            {"name": "Claude Opus 4", "model": "claude-opus-4-20250514", "url": "https://api.anthropic.com"},
            {"name": "Claude Haiku 3.5", "model": "claude-3-5-haiku-20241022", "url": "https://api.anthropic.com"},
        ],
    },
    "google": {
        "name": "Google Gemini",
        "default_url": "",
        "default_model": "gemini-2.5-pro",
        "models": [
            {"name": "Gemini 2.5 Pro", "model": "gemini-2.5-pro", "url": ""},
            {"name": "Gemini 2.5 Flash", "model": "gemini-2.5-flash", "url": ""},
            {"name": "Gemini 2.0 Flash", "model": "gemini-2.0-flash", "url": ""},
        ],
    },
    "ollama": {
        "name": "Ollama 本地",
        "default_url": "http://localhost:11434/v1",
        "default_model": "qwen3:8b",
        "models": [
            {"name": "Qwen3 8B", "model": "qwen3:8b", "url": "http://localhost:11434/v1"},
            {"name": "Qwen3 32B", "model": "qwen3:32b", "url": "http://localhost:11434/v1"},
            {"name": "Llama 3.1 8B", "model": "llama3.1:8b", "url": "http://localhost:11434/v1"},
            {"name": "Gemma 3 12B", "model": "gemma3:12b", "url": "http://localhost:11434/v1"},
        ],
    },
}

# OpenAI 兼容 Completion 模型关键词
_COMPLETION_KEYWORDS = [
    "base", "completions", "davinci", "curie", "babbage", "ada",
]

# OpenAI 兼容 Chat 模型关键词
_CHAT_KEYWORDS = [
    "chat", "gpt", "turbo", "deepseek", "qwen", "glm", "yi-",
    "mistral", "mixtral", "llama", "gemma", "instruct",
]


# ===== 基类 =====

class AIProvider(ABC):
    """AI Provider 基类"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        model_name: str = "",
        model_type: str = "auto",
        temperature: float = 0.7,
        top_p: float = 0.9,
        frequency_penalty: float = 0.1,
        presence_penalty: float = 0.0,
        max_tokens: int = 8192,
        few_shot_examples: str = "",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.model_type = model_type  # "auto" / "chat" / "completion"
        self.temperature = temperature
        self.top_p = top_p
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty
        self.max_tokens = max_tokens
        self.few_shot_examples = few_shot_examples

    @abstractmethod
    def translate(self, system_prompt: str, user_content: str) -> str:
        """
        发送翻译请求，返回翻译结果文本。
        出错时应抛出异常。
        """
        ...

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        测试 API 连接。
        返回 (成功与否, 消息描述)。
        """
        ...

    @property
    def provider_name(self) -> str:
        return self.__class__.__name__


# ===== OpenAI 兼容 Provider =====

class OpenAIProvider(AIProvider):
    """
    OpenAI 兼容 API Provider。
    支持 DeepSeek / Qwen / GPT / SiliconFlow / 任意 OpenAI 兼容端点。
    同时支持 Chat 模式和 Completion 模式。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "使用 OpenAI 兼容 Provider 需要安装 openai 库：pip install openai"
            )
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url or "https://api.siliconflow.cn/v1",
        )
        self._resolved_type = self._resolve_model_type()

    def _resolve_model_type(self) -> str:
        """解析实际模型类型 (chat / completion)"""
        if self.model_type in ("chat", "completion"):
            return self.model_type

        model_lower = self.model_name.lower()

        # 关键词匹配
        for kw in _COMPLETION_KEYWORDS:
            if kw in model_lower:
                return "completion"
        for kw in _CHAT_KEYWORDS:
            if kw in model_lower:
                return "chat"

        # API 探测
        try:
            self._client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            return "chat"
        except Exception:
            pass

        try:
            self._client.completions.create(
                model=self.model_name,
                prompt="Hi",
                max_tokens=5,
            )
            return "completion"
        except Exception:
            pass

        return "chat"  # 默认

    def translate(self, system_prompt: str, user_content: str) -> str:
        if self._resolved_type == "completion":
            return self._translate_completion(system_prompt, user_content)
        else:
            return self._translate_chat(system_prompt, user_content)

    def _translate_chat(self, system_prompt: str, user_content: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        resp = self._client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=self.temperature,
            top_p=self.top_p,
            frequency_penalty=self.frequency_penalty,
            presence_penalty=self.presence_penalty,
            max_tokens=self.max_tokens,
        )
        text = resp.choices[0].message.content or ""
        # 清理 <think> 标签 (DeepSeek R1 等推理模型)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return text

    def _translate_completion(self, system_prompt: str, user_content: str) -> str:
        prompt_parts = [system_prompt, ""]
        if self.few_shot_examples:
            prompt_parts.append(self.few_shot_examples)
            prompt_parts.append("")
        prompt_parts.append(f"原文:\n{user_content}\n\n译文:\n")
        full_prompt = "\n".join(prompt_parts)

        resp = self._client.completions.create(
            model=self.model_name,
            prompt=full_prompt,
            temperature=self.temperature,
            top_p=self.top_p,
            frequency_penalty=self.frequency_penalty,
            presence_penalty=self.presence_penalty,
            max_tokens=self.max_tokens,
        )
        return resp.choices[0].text.strip()

    def test_connection(self) -> Tuple[bool, str]:
        try:
            if self._resolved_type == "completion":
                resp = self._client.completions.create(
                    model=self.model_name,
                    prompt="请回复OK",
                    max_tokens=10,
                )
                text = resp.choices[0].text.strip()
            else:
                resp = self._client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": "请回复OK"}],
                    max_tokens=10,
                )
                text = resp.choices[0].message.content or ""
            return True, f"连接成功 [{self._resolved_type}] 回复: {text[:50]}"
        except Exception as ex:
            return False, f"连接失败: {ex}"


# ===== Anthropic Provider =====

class AnthropicProvider(AIProvider):
    """
    Anthropic Claude API Provider。
    使用 anthropic SDK 的 messages API。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "使用 Anthropic Provider 需要安装 anthropic 库：pip install anthropic"
            )
        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self._client = anthropic.Anthropic(**client_kwargs)

    def translate(self, system_prompt: str, user_content: str) -> str:
        resp = self._client.messages.create(
            model=self.model_name,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_content},
            ],
            temperature=min(self.temperature, 1.0),  # Anthropic max temperature = 1.0
            top_p=self.top_p,
        )
        # 提取文本块
        text_parts = []
        for block in resp.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        result = "\n".join(text_parts)
        # 清理 thinking 标签
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
        return result

    def test_connection(self) -> Tuple[bool, str]:
        try:
            resp = self._client.messages.create(
                model=self.model_name,
                max_tokens=20,
                messages=[{"role": "user", "content": "请回复OK"}],
            )
            text = ""
            for block in resp.content:
                if hasattr(block, "text"):
                    text += block.text
            return True, f"连接成功 [Anthropic] 回复: {text[:50]}"
        except Exception as ex:
            return False, f"连接失败: {ex}"


# ===== Google Gemini Provider =====

class GoogleProvider(AIProvider):
    """
    Google Gemini API Provider。
    使用 google-generativeai SDK。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "使用 Google Provider 需要安装 google-generativeai 库：pip install google-generativeai"
            )
        genai.configure(api_key=self.api_key)
        self._genai = genai
        self._model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=None,  # 在 translate 时动态设置
            generation_config=genai.types.GenerationConfig(
                temperature=self.temperature,
                top_p=self.top_p,
                max_output_tokens=self.max_tokens,
            ),
        )

    def translate(self, system_prompt: str, user_content: str) -> str:
        # 动态创建带 system_instruction 的模型
        model = self._genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system_prompt,
            generation_config=self._genai.types.GenerationConfig(
                temperature=self.temperature,
                top_p=self.top_p,
                max_output_tokens=self.max_tokens,
            ),
        )
        resp = model.generate_content(user_content)
        return resp.text.strip()

    def test_connection(self) -> Tuple[bool, str]:
        try:
            resp = self._model.generate_content("请回复OK")
            return True, f"连接成功 [Gemini] 回复: {resp.text.strip()[:50]}"
        except Exception as ex:
            return False, f"连接失败: {ex}"


# ===== Ollama Provider (复用 OpenAI 兼容) =====

class OllamaProvider(OpenAIProvider):
    """
    Ollama 本地模型 Provider。
    Ollama 提供 OpenAI 兼容 API，复用 OpenAIProvider。
    默认地址: http://localhost:11434/v1
    API Key 可为任意值 (Ollama 不验证)。
    """

    def __init__(self, **kwargs):
        if not kwargs.get("base_url"):
            kwargs["base_url"] = "http://localhost:11434/v1"
        if not kwargs.get("api_key"):
            kwargs["api_key"] = "ollama"  # Ollama 不需要真实 key
        super().__init__(**kwargs)

    def _resolve_model_type(self) -> str:
        """Ollama 模型始终使用 Chat 模式"""
        if self.model_type in ("chat", "completion"):
            return self.model_type
        return "chat"


# ===== 工厂函数 =====

_PROVIDER_MAP = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "ollama": OllamaProvider,
}


def create_provider(
    provider_type: str,
    api_key: str,
    base_url: str = "",
    model_name: str = "",
    model_type: str = "auto",
    temperature: float = 0.7,
    top_p: float = 0.9,
    frequency_penalty: float = 0.1,
    presence_penalty: float = 0.0,
    max_tokens: int = 8192,
    few_shot_examples: str = "",
) -> AIProvider:
    """
    根据 provider_type 创建对应的 AIProvider 实例。

    Args:
        provider_type: "openai" / "anthropic" / "google" / "ollama"
        其他参数传递给 Provider 构造器
    Returns:
        AIProvider 实例
    Raises:
        ValueError: 不支持的 provider_type
        ImportError: 缺少必要的 SDK
    """
    provider_type = provider_type.lower().strip()
    cls = _PROVIDER_MAP.get(provider_type)
    if cls is None:
        available = ", ".join(_PROVIDER_MAP.keys())
        raise ValueError(f"不支持的 Provider: {provider_type}（可选: {available}）")

    return cls(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        model_type=model_type,
        temperature=temperature,
        top_p=top_p,
        frequency_penalty=frequency_penalty,
        presence_penalty=presence_penalty,
        max_tokens=max_tokens,
        few_shot_examples=few_shot_examples,
    )


def get_provider_names() -> dict:
    """返回所有可用 Provider 的 {key: display_name} 字典"""
    return {k: v["name"] for k, v in PROVIDER_PRESETS.items()}


def get_provider_models(provider_type: str) -> list:
    """返回指定 Provider 的预设模型列表"""
    preset = PROVIDER_PRESETS.get(provider_type, {})
    return preset.get("models", [])


def get_provider_default_url(provider_type: str) -> str:
    """返回指定 Provider 的默认 API 地址"""
    preset = PROVIDER_PRESETS.get(provider_type, {})
    return preset.get("default_url", "")


def get_provider_default_model(provider_type: str) -> str:
    """返回指定 Provider 的默认模型名称"""
    preset = PROVIDER_PRESETS.get(provider_type, {})
    return preset.get("default_model", "")
