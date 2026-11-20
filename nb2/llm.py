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
import json
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
class _ProviderMeta:
    """上游设置页需要的提供方元信息形状（``provider.meta()``）。"""

    id: str = ""
    model: str = ""
    type: str = "openai"

    def model_dump(self) -> dict[str, Any]:
        return {"id": self.id, "model": self.model, "type": self.type}


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

    def meta(self) -> _ProviderMeta:
        """返回元信息对象（上游设置页调用 ``provider.meta()``）。"""
        return _ProviderMeta(id=self.id, model=self.model, type=self.provider_type)

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
    #: 模型请求的工具名列表（AstrBot 归一化字段，业务层据此判断是否走了工具模式）。
    tools_call_name: list[str] = field(default_factory=list)
    #: 与 ``tools_call_name`` 一一对应的参数（已解析为 dict 或原始字符串）。
    tools_call_args: list[Any] = field(default_factory=list)
    #: 原始 tool_calls（保留 id 与 function 结构）。
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.completion_text

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return self.completion_text


class ProviderManager:
    """提供方注册表（对齐 ``context.provider_manager`` 的最小表面）。

    既接受本模块的 :class:`ProviderConfig`，也接受任意外部提供方对象
    （例如只实现 ``get_embedding`` 的向量模型），以 ID 为键索引。
    """

    def __init__(self, providers: list[Any] | None = None) -> None:
        self._providers: dict[str, Any] = {}
        self.inst_map: dict[str, Any] = self._providers
        for provider in providers or []:
            self.register(provider)

    @staticmethod
    def _provider_id(provider: Any) -> str:
        provider_id = str(getattr(provider, "id", "") or "").strip()
        if provider_id:
            return provider_id
        config = getattr(provider, "provider_config", None)
        if isinstance(config, dict) and config.get("id"):
            return str(config["id"])
        meta = getattr(provider, "meta", None)
        if callable(meta):
            try:
                return str(getattr(meta(), "id", "") or "")
            except Exception:
                return ""
        return ""

    def register(self, provider: Any) -> None:
        provider_id = self._provider_id(provider)
        if not provider_id:
            raise ValueError("提供方缺少 id，无法注册")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> Any:
        return self._providers.get(str(provider_id or ""))

    def all(self) -> list[Any]:
        return list(self._providers.values())

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, provider_id: object) -> bool:
        return str(provider_id) in self._providers


class LLMContext:
    """替代 AstrBot ``Context`` 的 LLM 能力实现。

    只实现业务层实际使用的接口，避免伪装成 AstrBot 内核。
    """

    def __init__(self, providers: list[Any] | None = None) -> None:
        self.provider_manager = ProviderManager(providers)
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------ 注册与查询
    def register_provider(self, provider: Any) -> None:
        self.provider_manager.register(provider)

    def get_provider_by_id(self, provider_id: str) -> Any:
        """按 ID 取提供方（业务层用它判断模态能力）。"""
        return self.provider_manager.get(provider_id)

    def get_all_embedding_providers(self) -> list[Any]:
        """列出可用的向量提供方（上游语义任务在未配置时自动挑选）。

        判定标准：声明了 ``embedding`` 模态，或提供 ``get_embedding`` /
        ``get_embeddings`` 方法。
        """
        candidates: list[Any] = []
        for provider in self.provider_manager.all():
            config = getattr(provider, "provider_config", {}) or {}
            modalities = config.get("modalities") if isinstance(config, dict) else None
            declared = {
                str(item).lower() for item in (modalities or [])
            } if isinstance(modalities, list) else set()
            if (
                "embedding" in declared
                or callable(getattr(provider, "get_embedding", None))
                or callable(getattr(provider, "get_embeddings", None))
            ):
                candidates.append(provider)
        return candidates

    def get_using_provider(self, umo: str = "") -> Any:
        """返回默认聊天提供方（NoneBot2 路线没有会话级路由）。"""
        for provider in self.provider_manager.all():
            config = getattr(provider, "provider_config", {}) or {}
            modalities = config.get("modalities") if isinstance(config, dict) else None
            if isinstance(modalities, list) and "image" in {
                str(item).lower() for item in modalities
            }:
                return provider
        providers = self.provider_manager.all()
        return providers[0] if providers else None

    def get_all_providers(self) -> list[Any]:
        """列出全部聊天提供方（上游设置页用它填充模型下拉框）。"""
        return self.provider_manager.all()

    def provider_meta(self, provider: Any) -> Any:
        """返回带 ``id`` / ``model`` 的元信息对象（设置页序列化用）。"""
        config = getattr(provider, "provider_config", {}) or {}
        config = config if isinstance(config, dict) else {}
        return _ProviderMeta(
            id=str(config.get("id") or getattr(provider, "id", "") or ""),
            model=str(config.get("model") or getattr(provider, "model", "") or ""),
        )

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
        if (
            kwargs.get("disable_thinking")
            and provider.model.casefold() == "deepseek-v4.1-flash"
        ):
            payload["thinking"] = {"type": "disabled"}
        if kwargs.get("response_format") is not None:
            payload["response_format"] = kwargs["response_format"]

        # 工具调用：把上游的 ToolSet / 列表 / 单工具统一转换为 OpenAI tools 协议。
        tools = self._build_tools(kwargs.get("tools"))
        if tools:
            payload["tools"] = tools
            tool_choice = kwargs.get("tool_choice")
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

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
    def _build_tools(tools: Any) -> list[dict[str, Any]]:
        """把上游 ToolSet / 工具列表转换为 OpenAI ``tools`` 协议。

        支持三种输入：
        * ``None``（无工具）；
        * 带 ``.tools`` 属性的 ``ToolSet``（上游 ``_build_caption_tool_set``）；
        * 单个工具或工具列表（``FunctionTool`` 实例或已就绪的 schema dict）。
        """

        def _to_schema(tool: Any) -> dict[str, Any] | None:
            if isinstance(tool, dict):
                if tool.get("type") == "function" and "function" in tool:
                    return tool
                name = tool.get("name")
                if name:
                    return {
                        "type": "function",
                        "function": {
                            "name": str(name),
                            "description": str(tool.get("description") or ""),
                            "parameters": tool.get("parameters") or {},
                        },
                    }
                return None
            name = getattr(tool, "name", None)
            if not name:
                return None
            parameters = getattr(tool, "parameters", None) or {}
            if hasattr(parameters, "model_dump"):
                parameters = parameters.model_dump()
            elif not isinstance(parameters, dict):
                parameters = {}
            return {
                "type": "function",
                "function": {
                    "name": str(name),
                    "description": str(getattr(tool, "description", "") or ""),
                    "parameters": parameters,
                },
            }

        if tools is None:
            return []
        candidates: list[Any]
        if isinstance(tools, dict):
            candidates = [tools]
        elif isinstance(tools, (list, tuple, set)):
            candidates = list(tools)
        else:
            inner = getattr(tools, "tools", None)
            candidates = list(inner) if isinstance(inner, (list, tuple, set)) else [tools]

        schemas = [_to_schema(tool) for tool in candidates]
        return [schema for schema in schemas if schema]

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices") or []
        text = ""
        tool_calls: list[dict[str, Any]] = []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                text = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") not in ("reasoning", "reasoning_content", "thinking")
                )
            else:
                text = str(content or "")
            raw_calls = message.get("tool_calls") or []
            if isinstance(raw_calls, list):
                tool_calls = [call for call in raw_calls if isinstance(call, dict)]

        # 归一化成 AstrBot 的 tools_call_name / tools_call_args 结构，
        # 上游 backend/semantic/caption.py 依赖这两个字段。
        names: list[str] = []
        arguments: list[Any] = []
        for call in tool_calls:
            function = call.get("function") or {}
            names.append(str(function.get("name") or ""))
            raw_args = function.get("arguments")
            if isinstance(raw_args, str):
                try:
                    arguments.append(json.loads(raw_args))
                except Exception:
                    arguments.append(raw_args)
            else:
                arguments.append(raw_args if raw_args is not None else {})

        return LLMResponse(
            completion_text=text,
            raw=data,
            usage=dict(data.get("usage") or {}),
            tools_call_name=names,
            tools_call_args=arguments,
            tool_calls=tool_calls,
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
