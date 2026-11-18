"""AI 表情工具的命令入口。

NoneBot2 没有宿主 Agent，为了让普通用户也能用上语义检索/发送能力，这里提供两条
命令；同时 :func:`get_tools` 把同一批工具暴露给可选的 LLM 插件集成层。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from nonebot.matcher import Matcher

from ..state import get_runtime_or_none
from ..tools import build_default_registry
from .commands import PREFIX, register_command


async def search_memes_command(
    event: Any, runtime: Any, args: str
) -> AsyncGenerator[Any, None]:
    """``/表情管理 搜索 <描述>``：语义检索候选表情。"""
    query = (args or "").strip()
    if not query:
        yield event.plain_result(
            "🔍 用法：/表情管理 搜索 [想表达的情绪或动作]\n"
            "例如：/表情管理 搜索 庆祝一下"
        )
        return
    registry = build_default_registry(runtime)
    result = await registry.get("search_memes").call(event, runtime, {"query": query})
    if not result.ok:
        yield event.plain_result(f"⚠️ {result.message}")
        return
    candidates = result.data.get("candidates") or []
    if not candidates:
        yield event.plain_result("🔍 没有找到匹配的表情。")
        return
    lines = ["🔍 候选表情："]
    for item in candidates:
        caption = str(item.get("caption") or item.get("description") or "").strip()
        lines.append(f"- {item.get('id')}｜{item.get('category', '')}｜{caption[:40]}")
    lines.append("发送 /表情管理 发送 [ID] 即可发送。")
    yield event.plain_result("\n".join(lines))


async def send_meme_command(
    event: Any, runtime: Any, args: str
) -> AsyncGenerator[Any, None]:
    """``/表情管理 发送 <ID>``：发送本轮候选中的表情。"""
    ids = [item for item in (args or "").replace(",", " ").split() if item.strip()]
    if not ids:
        yield event.plain_result("📮 用法：/表情管理 发送 [候选ID]")
        return
    registry = build_default_registry(runtime)
    result = await registry.get("send_meme").call(event, runtime, {"ids": ids})
    if not result.ok and result.message:
        yield event.plain_result(f"⚠️ {result.message}")


def register_all() -> dict[str, type[Matcher]]:
    """注册 AI 工具相关的命令。"""
    return {
        "搜索": register_command("搜索", search_memes_command),
        "发送": register_command("发送", send_meme_command),
    }


def get_tools(runtime: Any = None) -> list[dict[str, Any]]:
    """返回可注册给 LLM 插件的工具 schema 列表。"""
    return build_default_registry(runtime).schemas()


async def call_tool(name: str, event: Any, arguments: dict[str, Any]) -> Any:
    """供外部 LLM 插件调用的统一入口。"""
    runtime = get_runtime_or_none()
    if runtime is None:
        return {"ok": False, "message": "表情包管理器尚未初始化"}
    tool = build_default_registry(runtime).get(name)
    if tool is None:
        return {"ok": False, "message": f"未知工具：{name}"}
    result = await tool.call(event, runtime, arguments)
    return {"ok": result.ok, "message": result.message, **result.data}


__all__ = [
    "register_all",
    "get_tools",
    "call_tool",
    "search_memes_command",
    "send_meme_command",
]
