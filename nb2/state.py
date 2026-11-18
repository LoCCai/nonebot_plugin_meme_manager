"""运行时单例访问。

matcher 与 WebUI 都需要运行时，但不能在模块导入期构造（插件可能在无驱动、
无适配器配置的环境下被导入）。这里提供惰性访问器。
"""

from __future__ import annotations

from .runtime import MemeRuntime

_runtime: MemeRuntime | None = None


def set_runtime(runtime: MemeRuntime | None) -> None:
    """注入运行时（测试或驱动启动时调用）。"""
    global _runtime
    _runtime = runtime


def get_runtime_or_none() -> MemeRuntime | None:
    """返回运行时；尚未构造时返回 ``None``。"""
    return _runtime


def get_runtime() -> MemeRuntime:
    """返回运行时，必要时构造。"""
    global _runtime
    if _runtime is None:
        _runtime = MemeRuntime()
    return _runtime


def reset_runtime() -> None:
    """清空运行时（测试用）。"""
    global _runtime
    _runtime = None


__all__ = ["set_runtime", "get_runtime", "get_runtime_or_none", "reset_runtime"]
