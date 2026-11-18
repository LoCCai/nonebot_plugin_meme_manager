"""LLM 提供方适配层。

AstrBot 通过 ``context.get_provider_by_id()`` / ``context.llm_generate()`` /
``context.provider_manager`` 提供模型能力，插件的语义化与自动收集依赖它。
NoneBot2 没有内置 LLM 运行时，因此这里提供等价实现：

* 提供方在配置的 ``providers`` 列表里声明（OpenAI 兼容接口优先）；
* ``get_provider_by_id`` 返回带 ``provider_config`` 的描述对象，供业务层做模态判断；
* ``llm_generate`` 走 OpenAI 兼容的 ``/chat/completions``，支持 ``image_urls``
  （本地路径自动转 base64）与 ``response_format``（结构化输出）。

这样 ``backend/semantic`` 与 ``backend/auto_collect`` 无需改动即可工作。
"""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from .log import logger

#: 判定「模型不支持结构化输出」的错误关键字（业务层据此回退）。
STRUCTURED_OUTPUT_ERROR_HINTS = (
    "response_format",
    "json_object",
    "structured",
    "unsupported",
    "invalid_request",
)


@dataclass
class ProviderConfig:
    """单个提供方的配置描述。"""

    id: str
    name: str = ""
    provider_type: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    modalities: list[str] = field(default_factory=lambda: ["text"])
    timeout: int = 60
    max_retries: int = 1

    @property
    def provider_config(self) -> dict[str, Any]:
        """业务层通过该属性读取 ``modalities`` 等能力声明。"""
        return {
            "id": self.id,
            "name": self.name or self.id,
            "type": self.provider_type,
            "model": self.model,
            "modalities": list(self.modalities),
            "base_url": self.base_url,
        }

    @property
    def supports_vision(self) -> bool:
        return "image" in {str(item).lower() for item in self.modalities}


@dataclass
class LLMResponse:
    """``llm_generate`` 的返回结构（对齐 AstrBot 的用法）。"""

    completion_text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.completion_text

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return self.completion_text


class ProviderManager:
    """提供方注册表（对齐 ``context.provider_manager`` 的最小表面）。"""

    def __init__(self, providers: list[ProviderConfig] | None = None) -> None:
        self._providers: dict[str, ProviderConfig] = {}
        self.inst_map: dict[str, ProviderConfig] = self._providers
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: ProviderConfig) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> ProviderConfig | None:
        return self._providers.get(str(provider_id or ""))

    def all(self) -> list[ProviderConfig]:
        return list(self._providers.values())

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, provider_id: object) -> bool:
        return str(provider_id) in self._providers


class LLMContext:
    """替代 AstrBot ``Context`` 的 LLM 能力实现。

    只实现业务层实际使用的接口，避免伪装成 AstrBot 内核。
    """

    def __init__(self, providers: list[ProviderConfig] | None = None) -> None:
        self.provider_manager = ProviderManager(providers)
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------ 注册与查询
    def register_provider(self, provider: ProviderConfig) -> None:
        self.provider_manager.register(provider)

    def get_provider_by_id(self, provider_id: str) -> ProviderConfig | None:
        """按 ID 取提供方（业务层用它判断模态能力）。"""
        return self.provider_manager.get(provider_id)

    def has_providers(self) -> bool:
        return bool(len(self.provider_manager))

    # ------------------------------------------------------------ 生成
    async def llm_generate(self, **kwargs: Any) -> LLMResponse:
        """调用 OpenAI 兼容接口生成内容。

        支持的关键字（与 AstrBot 用法一致）：
        ``chat_provider_id`` / ``prompt`` / ``system_prompt`` / ``image_urls`` /
        ``temperature`` / ``max_tokens`` / ``response_format``。
        """
        provider_id = str(kwargs.get("chat_provider_id") or "")
        provider = self.provider_manager.get(provider_id)
        if provider is None:
            raise ValueError(f"未配置的 LLM 提供方：{provider_id}")

        messages = self._build_messages(kwargs)
        payload: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
        }
        if kwargs.get("temperature") is not None:
            payload["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens") is not None:
            payload["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("response_format") is not None:
            payload["response_format"] = kwargs["response_format"]

        data = await self._post(provider, payload)
        return self._parse_response(data)

    async def _post(self, provider: ProviderConfig, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        timeout = aiohttp.ClientTimeout(total=provider.timeout)
        session = await self._get_session()
        async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"LLM 请求失败（HTTP {response.status}）：{text[:500]}")
            import json

            return json.loads(text)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------ 内部工具
    def _build_messages(self, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        system_prompt = kwargs.get("system_prompt")
        if system_prompt:
            messages.append({"role": "system", "content": str(system_prompt)})

        prompt = str(kwargs.get("prompt") or "")
        image_urls = list(kwargs.get("image_urls") or [])
        resolved = [url for url in (self._to_image_url(item) for item in image_urls) if url]
        if resolved:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            for url in resolved:
                content.append({"type": "image_url", "image_url": {"url": url}})
            messages.append({"role": "user", "content": content})
        else:
            # 没有任何可用图片时退回纯文本，避免部分模型拒绝多模态结构。
            messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _to_image_url(item: Any) -> str | None:
        """把本地路径/bytes 转为 data URL；URL 原样返回。"""
        if isinstance(item, (bytes, bytearray)):
            encoded = base64.b64encode(bytes(item)).decode("ascii")
            return f"data:image/png;base64,{encoded}"
        text = str(item or "")
        if not text:
            return None
        if text.startswith(("http://", "https://", "data:")):
            return text
        path = Path(text)
        if not path.is_file():
            logger.debug("LLM 图片不存在，已跳过：{}", path)
            return None
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices") or []
        text = ""
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                text = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict)
                )
            else:
                text = str(content or "")
        return LLMResponse(
            completion_text=text,
            raw=data,
            usage=dict(data.get("usage") or {}),
        )


def providers_from_config(config: Any) -> list[ProviderConfig]:
    """从插件配置构造提供方列表。

    配置形状（与 ``_conf_schema.json`` 的 ``storage.providers`` 类似）::

        {
          "llm": {
            "providers": [
              {"id": "vision", "model": "gpt-4o-mini", "api_key": "...",
               "base_url": "...", "modalities": ["text", "image"]}
            ]
          }
        }
    """
    providers: list[ProviderConfig] = []
    if not isinstance(config, dict):
        return providers

    entries: list[Any] = []
    for key in ("providers", "llm_providers"):
        value = config.get(key)
        if isinstance(value, list):
            entries.extend(value)
        elif isinstance(value, dict):
            entries.extend(
                dict(value=item, id=name) if isinstance(item, dict) else {}
                for name, item in value.items()
            )

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # 映射形式会被包装成 ``{"id": name, "value": {...}}``，这里先解包。
        inner = entry.get("value")
        spec = dict(inner) if isinstance(inner, dict) else entry
        if inner is not None and not spec.get("id"):
            spec["id"] = entry.get("id") or entry.get("name")
        provider_id = str(spec.get("id") or spec.get("name") or "").strip()
        if not provider_id:
            continue
        modalities = spec.get("modalities")
        if not isinstance(modalities, list) or not modalities:
            modalities = ["text", "image"] if spec.get("vision") else ["text"]
        providers.append(
            ProviderConfig(
                id=provider_id,
                name=str(spec.get("name") or provider_id),
                provider_type=str(spec.get("type") or "openai"),
                base_url=str(
                    spec.get("base_url")
                    or spec.get("api_base")
                    or os.environ.get("OPENAI_BASE_URL")
                    or "https://api.openai.com/v1"
                ),
                api_key=str(
                    spec.get("api_key")
                    or spec.get("token")
                    or os.environ.get("OPENAI_API_KEY")
                    or ""
                ),
                model=str(spec.get("model") or ""),
                modalities=[str(item) for item in modalities],
                timeout=int(spec.get("timeout") or 60),
            )
        )
    return providers


__all__ = [
    "ProviderConfig",
    "ProviderManager",
    "LLMContext",
    "LLMResponse",
    "providers_from_config",
    "STRUCTURED_OUTPUT_ERROR_HINTS",
]
