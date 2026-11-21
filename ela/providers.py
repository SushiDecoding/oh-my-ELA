"""Endpoint presets offered by /login.

Every entry speaks the OpenAI Chat Completions protocol, so they all work with
the same client. Local servers do not need an API key.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    needs_key: bool = True


PROVIDERS: tuple[Provider, ...] = (
    Provider("openai", "OpenAI", "https://api.openai.com/v1"),
    Provider("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
    Provider("groq", "Groq", "https://api.groq.com/openai/v1"),
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1"),
    Provider("xai", "xAI (Grok)", "https://api.x.ai/v1"),
    Provider("together", "Together", "https://api.together.xyz/v1"),
    Provider(
        "siliconflow", "SiliconFlow", "https://api.siliconflow.cn/v1"
    ),
    Provider("moonshot", "Moonshot (Kimi)", "https://api.moonshot.cn/v1"),
    Provider(
        "zhipu", "Zhipu (GLM)", "https://open.bigmodel.cn/api/paas/v4"
    ),
    Provider(
        "dashscope",
        "DashScope (Qwen)",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    Provider("ollama", "Ollama (local)", "http://127.0.0.1:11434/v1", False),
    Provider("lmstudio", "LM Studio (local)", "http://127.0.0.1:1234/v1", False),
    Provider("llamacpp", "llama.cpp (local)", "http://127.0.0.1:8080/v1", False),
    Provider("custom", "Custom endpoint", "", False),
)
