"""协议适配器注册表。"""

from __future__ import annotations

from typing import Any

from ...log import logger
from .base import PlatformAdapter, adapter_name_of
from .onebot11 import OneBotV11Adapter
from .onebot12 import OneBotV12Adapter
from .qq_official import QQOfficialAdapter

_ADAPTERS: dict[str, PlatformAdapter] = {}


def _register(adapter: PlatformAdapter) -> PlatformAdapter:
    _ADAPTERS[adapter.name] = adapter
    return adapter


onebot11 = _register(OneBotV11Adapter())
onebot12 = _register(OneBotV12Adapter())
qq_official = _register(QQOfficialAdapter())

#: 适配器名 → ``nonebot`` 适配器包名（用于 ``require`` 与错误提示）。
ADAPTER_MODULES = {
    "onebot11": "nonebot.adapters.onebot.v11",
    "onebot12": "nonebot.adapters.onebot.v12",
    "qq_official": "nonebot.adapters.qq",
}

#: 支持的协议清单。
SUPPORTED_PROTOCOLS = ("onebot11", "onebot12", "qq_official")


def get_adapter(name: str) -> PlatformAdapter | None:
    """按名称获取适配器。"""
    return _ADAPTERS.get(str(name or "").strip())


def all_adapters() -> dict[str, PlatformAdapter]:
    return dict(_ADAPTERS)


def resolve_adapter(event: Any) -> PlatformAdapter | None:
    """根据事件解析适配器。"""
    name = adapter_name_of(event)
    adapter = _ADAPTERS.get(name)
    if adapter is None:
        logger.debug("未识别的协议事件：{}（模块 {}）", name, type(event).__module__)
    return adapter


def is_supported_event(event: Any) -> bool:
    return adapter_name_of(event) in _ADAPTERS


__all__ = [
    "get_adapter",
    "all_adapters",
    "resolve_adapter",
    "is_supported_event",
    "onebot11",
    "onebot12",
    "qq_official",
    "ADAPTER_MODULES",
    "SUPPORTED_PROTOCOLS",
    "PlatformAdapter",
]
