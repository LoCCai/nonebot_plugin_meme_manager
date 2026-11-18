"""NoneBot2 事件到统一事件的桥接。

matcher 里拿到的是原生 nonebot 事件对象；这里负责解析协议、转换消息、绑定
传输对象，并统一管理员判定与日志。
"""

from __future__ import annotations

import re
from typing import Any

from .event import UnifiedEvent
from ..log import logger
from .adapters import get_adapter, resolve_adapter


def to_unified_event(bot: Any, event: Any) -> UnifiedEvent | None:
    """把 nonebot 事件转换为统一事件。

    Args:
        bot: 当前 ``Bot`` 实例。
        event: 当前 nonebot 事件对象。

    Returns:
        统一事件；协议不支持时返回 ``None``（调用方应优雅跳过）。
    """
    adapter = resolve_adapter(event)
    if adapter is None:
        adapter = get_adapter("onebot11") if _looks_like_onebot11(event) else None
    if adapter is None:
        logger.debug("跳过不支持的协议事件：{}", type(event).__name__)
        return None
    try:
        return adapter.to_unified_event(bot, event)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.error(
            "转换事件失败（{} / {}）：{}",
            adapter.name,
            type(event).__name__,
            exc,
            exc_info=True,
        )
        return None


def _looks_like_onebot11(event: Any) -> bool:
    """少数实现会把事件类动态生成，模块名判断失效时按字段兜底。"""
    return hasattr(event, "post_type") and hasattr(event, "message_id")


def strip_mentions(text: str) -> str:
    """移除文本中的 CQ 码 @ 提及（部分实现会保留在 message_str 中）。"""
    return re.sub(r"\[CQ:at,qq=\d+\]", "", text).strip()


__all__ = ["to_unified_event", "strip_mentions"]
