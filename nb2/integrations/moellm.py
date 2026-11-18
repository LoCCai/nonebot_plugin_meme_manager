"""MoEllmChats 工具适配（可选集成）。

`nonebot-plugin-moellmchats` 自带 LLM 运行时，对外扩展点只有强类型 ``ToolSpec``
（见 ``docs/moellmchats-compat.md``）。本模块把 :mod:`nb2.tools` 的表情工具注册为
它的工具：

* ``meme_search`` —— 只读，按语义检索候选；
* ``meme_send``   —— mutating，发送候选表情（走它自己的二阶段确认）。

未安装 MoEllmChats 时本模块**不做任何事**，也不会让插件加载失败。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..log import logger
from ..platform.bridge import to_unified_event
from ..state import get_runtime_or_none
from ..tools import build_default_registry

#: 注册的工具名（MoEllmChats 要求 ``[A-Za-z0-9_-]{1,64}``）。
SEARCH_TOOL_NAME = "meme_search"
SEND_TOOL_NAME = "meme_send"

_REGISTERED = False


def _search_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "想表达的情绪、态度或动作，例如“庆祝某人取得成就”",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 9,
                "description": "候选数量，默认取插件配置",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def _send_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "minItems": 1,
                "maxItems": 9,
                "description": "search 返回的候选 ID 列表（形如 meme:<前缀>）",
            }
        },
        "required": ["ids"],
        "additionalProperties": False,
    }


async def _resolve_event(tool_context: Any):
    """把 ``ToolContext`` 转换为统一事件。"""
    runtime = get_runtime_or_none()
    if runtime is None:
        return None, None
    event = getattr(tool_context, "event", None)
    bot = getattr(tool_context, "bot", None)
    if event is None:
        return runtime, None
    unified = to_unified_event(bot, event)
    return runtime, unified


def register() -> bool:
    """注册工具到 MoEllmChats（幂等）。

    Returns:
        是否完成注册（未安装依赖时返回 ``False``）。
    """
    global _REGISTERED
    if _REGISTERED:
        return True
    try:
        from nonebot_plugin_moellmchats import (  # type: ignore
            ToolEffect,
            ToolResult,
            ToolSpec,
            register_tool,
        )
        from nonebot_plugin_moellmchats.tool_contracts import (  # type: ignore
            tool_registry,
        )
    except Exception:
        logger.debug("未安装 nonebot-plugin-moellmchats，跳过工具注册")
        return False

    async def meme_search(
        *, query: str, top_k: int | None = None, _tool_context: Any
    ) -> Any:
        runtime, unified = await _resolve_event(_tool_context)
        if runtime is None or unified is None:
            return ToolResult(text="表情包管理器尚未初始化。")
        registry = build_default_registry(runtime)
        arguments: dict[str, Any] = {"query": query}
        if top_k is not None:
            arguments["top_k"] = top_k
        result = await registry.get("search_memes").call(unified, runtime, arguments)
        if not result.ok:
            return ToolResult(text=result.message)
        candidates = result.data.get("candidates") or []
        lines = [
            f"- {item.get('id')}｜{item.get('category', '')}｜"
            f"{str(item.get('caption') or '')[:40]}"
            for item in candidates
        ]
        return ToolResult(
            text="候选表情：\n" + "\n".join(lines) if lines else "没有找到匹配的表情。",
            structured={"candidates": candidates, "reason": result.data.get("reason", "")},
        )

    async def meme_send(*, ids: list[str], _tool_context: Any) -> Any:
        runtime, unified = await _resolve_event(_tool_context)
        if runtime is None or unified is None:
            return ToolResult(text="表情包管理器尚未初始化。")
        registry = build_default_registry(runtime)
        result = await registry.get("send_meme").call(unified, runtime, {"ids": ids})
        if not result.ok:
            return ToolResult(text=result.message)
        sent = int(result.data.get("sent") or 0)
        return ToolResult(text=f"已发送 {sent} 张表情。", structured={"sent": sent})

    specs = [
        ToolSpec(
            name=SEARCH_TOOL_NAME,
            description=(
                "按情绪、态度或动作语义检索表情包候选。返回候选 ID 供 meme_send 使用；"
                "同一轮内只应搜索一次。"
            ),
            parameters=_search_parameters(),
            handler=meme_search,
            effect=ToolEffect.READ_ONLY,
            permission="user",
            timeout_seconds=20,
            result_limit=4000,
        ),
        ToolSpec(
            name=SEND_TOOL_NAME,
            description=(
                "发送 memo_search 候选中选定的表情图片。ids 必须是本轮候选中出现的 ID；"
                "无法发送时会说明原因。"
            ),
            parameters=_send_parameters(),
            handler=meme_send,
            effect=ToolEffect.MUTATING,
            permission="user",
            timeout_seconds=30,
        ),
    ]

    try:
        for spec in specs:
            # 热重载时先撤销旧注册，保证幂等。
            if tool_registry.get(spec.name) is not None:
                tool_registry.unregister(spec.name)
            register_tool(spec)
    except Exception as exc:  # pragma: no cover - 对方版本差异
        logger.warning("注册 MoEllmChats 工具失败：{}", exc)
        return False

    _REGISTERED = True
    logger.info(
        "已向 MoEllmChats 注册表情工具：{} / {}", SEARCH_TOOL_NAME, SEND_TOOL_NAME
    )
    return True


def unregister() -> None:
    """撤销注册（插件卸载时调用）。"""
    global _REGISTERED
    if not _REGISTERED:
        return
    try:
        from nonebot_plugin_moellmchats.tool_contracts import tool_registry  # type: ignore
    except Exception:  # pragma: no cover - 依赖已卸载
        _REGISTERED = False
        return
    for name in (SEARCH_TOOL_NAME, SEND_TOOL_NAME):
        tool_registry.unregister(name)
    _REGISTERED = False


__all__ = ["register", "unregister", "SEARCH_TOOL_NAME", "SEND_TOOL_NAME"]
