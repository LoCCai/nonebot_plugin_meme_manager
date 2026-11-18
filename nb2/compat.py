"""把业务层的 AstrBot 依赖改写为 nb2 兼容层。

业务核心（``backend/``、``image_host/``）与 AstrBot 的耦合只有三类：

1. ``from astrbot.api import logger``（日志）；
2. ``from astrbot.api.message_components import Image, Reply`` 配合
   ``isinstance(component, Image)`` 在事件消息里挑图片 / 引用；
3. 少量内核类型（``FunctionTool`` / ``ToolSet`` / ``MessageChain`` /
   ``MessageSession`` 等）。

为了**不改写业务逻辑**即可在 NoneBot2 下运行，这里安装一个 **meta path
finder**：无论业务模块何时导入 ``astrbot.*``，都能拿到兼容实现，不依赖
``repo.config`` 是否已被导入。真实 AstrBot 存在时（例如在 AstrBot 内运行），
兼容层会让位——但仍以真实包优先。

消息组件采用元类 ``__instancecheck__`` 实现「虚拟子类」语义：
``isinstance(segment, Image)`` 等价于 ``segment.type == "image"``，因此业务层的
``isinstance`` 判断可以直接作用于 nb2 的 :class:`~nb2.platform.message.Segment`。
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import types
from typing import Any

from .log import logger

#: 本插件包的顶层包名（用于定位 ``<pkg>.astrbot_compat``）。
_PLUGIN_PACKAGE = __package__.rsplit(".", 1)[0] if "." in __package__ else __package__

_INSTALLED = False


class _VirtualComponent(type):
    """按 ``type`` 字段做虚拟 isinstance 判断的元类。"""

    segment_type: str = "unknown"

    def __instancecheck__(cls, instance: Any) -> bool:  # noqa: N805
        # 真实子类实例优先（避免再次触发虚拟判定导致误判）。
        if cls in type(instance).__mro__:
            return True
        return getattr(instance, "type", None) == cls.segment_type


class _BaseComponent(metaclass=_VirtualComponent):
    """兼容组件基类。"""

    segment_type = "unknown"

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        attrs = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"{type(self).__name__}({attrs})"


class Image(_BaseComponent):
    """图片组件（虚拟匹配 ``Segment.type == "image"``）。"""

    segment_type = "image"


class Reply(_BaseComponent):
    """引用组件（虚拟匹配 ``Segment.type == "reply"``）。"""

    segment_type = "reply"


class Plain(_BaseComponent):
    """文本组件（虚拟匹配 ``Segment.type == "text"``）。"""

    segment_type = "text"


class At(_BaseComponent):
    """提及组件（虚拟匹配 ``Segment.type == "at"``）。"""

    segment_type = "at"


# --------------------------------------------------------- LLM 工具与消息链


class _FunctionTool:
    """``astrbot.api.FunctionTool`` 的最小等价实现。

    仅用于让业务层（``backend/message_tool.py``、``backend/semantic/caption.py``）
    可导入并提供工具描述；NoneBot2 线路的工具注册见 ``nb2.tools``。
    """

    def __init__(
        self,
        name: str = "",
        description: str = "",
        parameters: dict[str, Any] | None = None,
        handler: Any = None,
        active: bool = True,
        **kwargs: Any,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.handler = handler
        self.active = active
        self.extra = kwargs

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"FunctionTool(name={self.name!r})"


class _ToolSet:
    """``astrbot.api.ToolSet`` 的最小等价实现。"""

    def __init__(self, tools: Any = None, **kwargs: Any) -> None:
        self.tools = list(tools or [])
        self.extra = kwargs

    def __bool__(self) -> bool:
        return bool(self.tools)

    def __iter__(self):
        return iter(self.tools)

    def __len__(self) -> int:
        return len(self.tools)


class _MessageChain:
    """``astrbot.api.event.MessageChain`` 的最小等价实现。"""

    def __init__(self, chain: Any = None) -> None:
        if chain is None:
            self.chain: list[Any] = []
        elif isinstance(chain, list):
            self.chain = list(chain)
        else:
            self.chain = [chain]

    def append(self, component: Any) -> None:
        self.chain.append(component)

    def message(self, component: Any) -> "_MessageChain":
        self.chain.append(component)
        return self

    def get_plain_text(self) -> str:
        parts = []
        for component in self.chain:
            if getattr(component, "type", None) == "text":
                data = getattr(component, "data", None)
                if isinstance(data, dict):
                    parts.append(str(data.get("text", "")))
                else:
                    parts.append(str(getattr(component, "text", "")))
            elif isinstance(component, str):
                parts.append(component)
        return "".join(parts)

    def __iter__(self):
        return iter(self.chain)

    def __len__(self) -> int:
        return len(self.chain)

    def __bool__(self) -> bool:
        return bool(self.chain)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"MessageChain({self.chain!r})"


class _AstrMessageEventStub:
    """``AstrMessageEvent`` 的类型占位（实际事件类型为 ``UnifiedEvent``）。"""


class _FilterStub:
    """``astrbot.api.event.filter`` 的占位实现。

    NoneBot2 用 matcher 注册处理器，因此这里的方法只用于让业务层代码可导入；
    若被实际调用则明确报错，避免静默失效。
    """

    def __getattr__(self, name: str) -> Any:
        def _unsupported(*args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError(
                f"astrbot filter.{name} 在 NoneBot2 线路不可用；"
                "请使用 nb2.matchers 中的 matcher 注册方式。"
            )

        return _unsupported


# ------------------------------------------------------------------ 模块构造


def _build_modules() -> dict[str, types.ModuleType]:
    """构造全部兼容模块。"""
    from . import astrbot_types

    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []  # type: ignore[attr-defined]

    api = types.ModuleType("astrbot.api")
    api.__path__ = []  # type: ignore[attr-defined]
    api.logger = logger
    api.FunctionTool = _FunctionTool
    api.ToolSet = _ToolSet

    message_components = types.ModuleType("astrbot.api.message_components")
    message_components.Image = Image
    message_components.Reply = Reply
    message_components.Plain = Plain
    message_components.At = At

    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = _MessageChain
    event_module.AstrMessageEvent = _AstrMessageEventStub
    event_module.filter = _FilterStub()

    provider_module = types.ModuleType("astrbot.api.provider")
    provider_module.LLMResponse = type("LLMResponse", (), {})
    provider_module.ProviderRequest = type("ProviderRequest", (), {})

    star_module = types.ModuleType("astrbot.api.star")
    star_module.Context = type("Context", (), {})
    star_module.Star = type("Star", (), {})

    platform_module = types.ModuleType("astrbot.api.platform")
    platform_module.MessageType = astrbot_types.MessageSession  # 占位，见下
    from .platform.event import MessageType as _NbMessageType

    platform_module.MessageType = _NbMessageType

    util_module = types.ModuleType("astrbot.api.util")
    util_module.session_waiter = _unsupported_factory("astrbot.api.util.session_waiter")

    api.message_components = message_components  # type: ignore[attr-defined]
    api.event = event_module  # type: ignore[attr-defined]
    api.provider = provider_module  # type: ignore[attr-defined]
    api.star = star_module  # type: ignore[attr-defined]
    api.platform = platform_module  # type: ignore[attr-defined]
    api.util = util_module  # type: ignore[attr-defined]
    astrbot.api = api  # type: ignore[attr-defined]

    # 插件内 ``astrbot_compat`` 的 NoneBot2 等价实现。
    compat_module = types.ModuleType(f"{_PLUGIN_PACKAGE}.astrbot_compat")
    compat_module.__doc__ = (
        "NoneBot2 线路的 astrbot_compat 等价实现（由 nb2.compat 注入）。"
    )
    for name in (
        "MessageSession",
        "CommandFilter",
        "CommandGroupFilter",
        "Message",
        "TextPart",
        "ImageURLPart",
        "ToolCall",
        "coerce_content_parts",
        "iter_tool_calls",
        "is_command_event",
    ):
        setattr(compat_module, name, getattr(astrbot_types, name))

    return {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.message_components": message_components,
        "astrbot.api.event": event_module,
        "astrbot.api.provider": provider_module,
        "astrbot.api.star": star_module,
        "astrbot.api.platform": platform_module,
        "astrbot.api.util": util_module,
        f"{_PLUGIN_PACKAGE}.astrbot_compat": compat_module,
    }


def _unsupported_factory(label: str) -> Any:
    def _unsupported(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            f"{label} 在 NoneBot2 线路不可用；请使用 nb2.platform.session 的实现。"
        )

    return _unsupported


class _AstrbotCompatFinder(importlib.abc.MetaPathFinder):
    """拦截 ``astrbot.*`` 与插件内 ``astrbot_compat`` 的导入。"""

    def __init__(self, modules: dict[str, types.ModuleType]) -> None:
        self._modules = modules

    def find_spec(self, fullname: str, path: Any = None, target: Any = None):
        module = self._modules.get(fullname)
        if module is None:
            return None
        # 真实 astrbot 已安装时让位（在 AstrBot 内运行时保持原行为）。
        return importlib.machinery.ModuleSpec(fullname, _PreloadedLoader(module))


class _PreloadedLoader(importlib.abc.Loader):
    """把已构造好的模块对象注册进 ``sys.modules``。"""

    def __init__(self, module: types.ModuleType) -> None:
        self._module = module

    def create_module(self, spec: Any) -> types.ModuleType:
        return self._module

    def exec_module(self, module: types.ModuleType) -> None:
        return None


def install() -> bool:
    """安装 ``astrbot`` 兼容层。

    Returns:
        是否执行了安装（``False`` 表示真实 astrbot 已存在，未做拦截）。
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if _astrbot_available():
        logger.debug("检测到真实 astrbot，跳过兼容层安装")
        return False

    modules = _build_modules()
    for name, module in modules.items():
        # 插件内的 astrbot_compat 允许被真实模块覆盖；astrbot.* 一律使用兼容实现。
        if name.startswith("astrbot"):
            sys.modules[name] = module
        else:
            sys.modules.setdefault(name, module)
    sys.meta_path.insert(0, _AstrbotCompatFinder(modules))
    _INSTALLED = True
    logger.debug("已安装 astrbot 兼容层（logger、消息组件、内核类型）")
    return True


def _astrbot_available() -> bool:
    """判断是否安装了真实的 astrbot（而非本兼容层）。"""
    if "astrbot" in sys.modules:
        module = sys.modules["astrbot"]
        origin = getattr(module, "__file__", None)
        # 兼容层构造的模块没有 __file__。
        if origin:
            return True
    try:
        import importlib.util

        spec = importlib.util.find_spec("astrbot")
    except (ImportError, ValueError):
        return False
    return bool(spec and spec.origin)


__all__ = ["install", "Image", "Reply", "Plain", "At"]
