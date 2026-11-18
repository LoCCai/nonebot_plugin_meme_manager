"""业务层用到的 AstrBot 内部依赖在 NoneBot2 下的等价实现。

原插件的 ``astrbot_compat.py`` 依赖 AstrBot 内核（``astrbot.core.*``）来做三件事：

1. **LLM 消息模型**（``Message`` / ``TextPart`` / ``ImageURLPart`` / ``ToolCall``）
   —— 用于在 Agent 消息历史里识别与清理表情工具痕迹；
2. **会话对象**（``MessageSession``）—— 表示「平台 + 消息类型 + 会话 ID」；
3. **命令过滤器类型**（``CommandFilter`` / ``CommandGroupFilter``）—— 用于判断事件
   是否由命令触发。

NoneBot2 没有 Agent 概念，这里给出**语义等价但独立**的实现：LLM 消息模型用轻量
数据类（保留 ``model_validate`` / ``model_copy`` 协议以兼容调用方），会话对象用同一
套 ``platform:type:id`` 三元组，命令过滤器用「事件是否带命令标记」判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# --------------------------------------------------------------------- 会话


@dataclass(slots=True)
class MessageSession:
    """会话描述（对应 AstrBot ``MessageSession``）。"""

    platform_name: str = ""
    message_type: str = ""
    session_id: str = ""

    @classmethod
    def from_str(cls, value: str) -> "MessageSession":
        """解析 ``平台:会话类型:会话ID`` 字符串。"""
        text = str(value or "")
        parts = text.split(":", 2)
        if len(parts) == 3:
            return cls(parts[0], parts[1], parts[2])
        if len(parts) == 2:
            return cls(parts[0], parts[1], "")
        return cls("", "", text)

    @classmethod
    def from_event(cls, event: Any) -> "MessageSession":
        """从统一事件构造会话。"""
        return cls(
            str(getattr(event, "platform_name", "") or ""),
            str(getattr(event, "session_type", "") or ""),
            str(getattr(event, "session_id", "") or ""),
        )

    def __str__(self) -> str:
        return f"{self.platform_name}:{self.message_type}:{self.session_id}"

    @property
    def unified_msg_origin(self) -> str:
        return str(self)

    def to_dict(self) -> dict[str, str]:
        return {
            "platform_name": self.platform_name,
            "message_type": self.message_type,
            "session_id": self.session_id,
        }


# --------------------------------------------------------------- 命令过滤器


class CommandFilter:
    """命令过滤器标记类型（对应 AstrBot ``CommandFilter``）。

    业务层只用它做 ``isinstance`` 判断，因此这里只需要一个可辨识的类型。
    """

    __slots__ = ("command",)

    def __init__(self, command: str = "") -> None:
        self.command = command


class CommandGroupFilter(CommandFilter):
    """命令组过滤器标记类型。"""

    __slots__ = ()


def is_command_event(event: Any) -> bool:
    """判断事件是否由命令触发（NoneBot2 由 matcher 在 extra 上打标）。"""
    return bool(getattr(event, "get_extra", lambda *_: None)("nb2_command", False))


# ------------------------------------------------------------- LLM 消息模型


@dataclass
class TextPart:
    """文本内容块（对应 AstrBot ``TextPart``）。"""

    text: str = ""
    type: str = "text"

    def model_dump(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}

    def dict(self) -> dict[str, Any]:  # pragma: no cover - pydantic 兼容
        return self.model_dump()


@dataclass
class ImageURLPart:
    """图片内容块（对应 AstrBot ``ImageURLPart``）。"""

    image_url: Any = None
    type: str = "image_url"

    def model_dump(self) -> dict[str, Any]:
        url = self.image_url
        if hasattr(url, "url"):
            url = url.url
        elif isinstance(url, dict):
            url = url.get("url")
        return {"type": self.type, "image_url": {"url": url}}

    def dict(self) -> dict[str, Any]:  # pragma: no cover - pydantic 兼容
        return self.model_dump()


@dataclass
class ToolCall:
    """工具调用（对应 AstrBot ``ToolCall``）。"""

    id: str = ""
    type: str = "function"
    function: Any = None

    @classmethod
    def model_validate(cls, value: Any) -> "ToolCall":
        """兼容 pydantic 的 ``model_validate`` 调用。"""
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                id=str(value.get("id") or ""),
                type=str(value.get("type") or "function"),
                function=value.get("function"),
            )
        return cls(id=str(getattr(value, "id", "") or ""), function=getattr(value, "function", None))

    def model_dump(self) -> dict[str, Any]:
        function = self.function
        if hasattr(function, "model_dump"):
            function = function.model_dump()
        elif hasattr(function, "dict"):
            function = function.dict()
        return {"id": self.id, "type": self.type, "function": function}

    def model_copy(self, *, update: dict[str, Any] | None = None) -> "ToolCall":
        payload = self.model_dump()
        payload.update(update or {})
        return ToolCall.model_validate(payload)

    @property
    def function_name(self) -> str:
        function = self.function
        if isinstance(function, dict):
            return str(function.get("name") or "")
        return str(getattr(function, "name", "") or "")

    def __getitem__(self, key: str) -> Any:  # 兼容 dict 风格访问
        if key == "id":
            return self.id
        if key == "type":
            return self.type
        return getattr(self, key)


@dataclass
class Message:
    """LLM 会话消息（对应 AstrBot ``Message``）。"""

    role: str = "user"
    content: Any = ""
    tool_calls: list[Any] | None = None
    tool_call_id: str | None = None
    _no_save: bool = False

    @classmethod
    def model_validate(cls, value: Any) -> "Message":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                role=str(value.get("role") or "user"),
                content=value.get("content") or "",
                tool_calls=value.get("tool_calls"),
                tool_call_id=value.get("tool_call_id"),
                _no_save=bool(value.get("_no_save", False)),
            )
        return cls(
            role=str(getattr(value, "role", "user") or "user"),
            content=getattr(value, "content", "") or "",
            tool_calls=getattr(value, "tool_calls", None),
            tool_call_id=getattr(value, "tool_call_id", None),
        )

    def model_dump(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
        }

    def model_copy(self, *, update: dict[str, Any] | None = None) -> "Message":
        payload = self.model_dump()
        payload.update(update or {})
        return Message.model_validate(payload)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


def coerce_content_parts(content: Any) -> list[Any]:
    """把消息内容规范为「内容块列表」。"""
    if isinstance(content, list):
        return list(content)
    if content in (None, ""):
        return []
    return [TextPart(text=str(content))]


def iter_tool_calls(messages: Iterable[Any]) -> list[Any]:
    """遍历消息列表中的全部工具调用。"""
    calls: list[Any] = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or ():
            calls.append(call)
    return calls


__all__ = [
    "MessageSession",
    "CommandFilter",
    "CommandGroupFilter",
    "is_command_event",
    "Message",
    "TextPart",
    "ImageURLPart",
    "ToolCall",
    "coerce_content_parts",
    "iter_tool_calls",
]
