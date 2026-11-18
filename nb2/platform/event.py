"""统一事件模型。

``UnifiedEvent`` 刻意保持与 AstrBot ``AstrMessageEvent`` 相同的常用表面
（``message_str`` / ``get_sender_id`` / ``plain_result`` / ``send`` /
``set_extra`` / ``get_extra`` / ``unified_msg_origin`` …），这样 `mixins/`
中的业务逻辑移植时只需替换 import 与少数返回约定，而不必重写。
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .capabilities import Capabilities, degrade_segments
from .message import Segment, UnifiedMessage


class MessageType(enum.Enum):
    """会话类型（对应 AstrBot ``MessageType``）。"""

    GROUP_MESSAGE = "group"
    PRIVATE_MESSAGE = "private"
    GUILD_MESSAGE = "guild"
    CHANNEL_MESSAGE = "channel"
    OTHER_MESSAGE = "other"

    @classmethod
    def from_session_type(cls, session_type: str) -> "MessageType":
        mapping = {
            "group": cls.GROUP_MESSAGE,
            "private": cls.PRIVATE_MESSAGE,
            "guild": cls.GUILD_MESSAGE,
            "channel": cls.CHANNEL_MESSAGE,
        }
        return mapping.get(str(session_type or "").lower(), cls.OTHER_MESSAGE)


@dataclass(slots=True)
class MessageObj:
    """统一后的入站消息对象（对应 AstrBot ``message_obj``）。"""

    message: list[Segment] = field(default_factory=list)
    message_str: str = ""
    message_id: str = ""
    self_id: str = ""
    raw_message: Any = None
    session_id: str = ""
    sender_id: str = ""
    sender_name: str = ""

    @classmethod
    def from_unified(
        cls,
        unified: UnifiedMessage,
        *,
        message_id: str = "",
        self_id: str = "",
        session_id: str = "",
        sender_id: str = "",
        sender_name: str = "",
        raw_message: Any = None,
    ) -> "MessageObj":
        return cls(
            message=list(unified.segments),
            message_str=unified.plain_text,
            message_id=str(message_id or ""),
            self_id=str(self_id or ""),
            raw_message=raw_message,
            session_id=str(session_id or ""),
            sender_id=str(sender_id or ""),
            sender_name=str(sender_name or ""),
        )


@runtime_checkable
class Transport(Protocol):
    """出站传输抽象：由各协议适配器实现。"""

    capabilities: Capabilities

    async def send(self, message: UnifiedMessage) -> Any:
        """发送一条统一消息，返回平台侧结果。"""


@dataclass(slots=True)
class UnifiedEvent:
    """协议无关的消息事件。"""

    platform_name: str
    session_type: str
    session_id: str
    sender_id: str
    message_str: str
    message_obj: MessageObj
    raw_event: Any = None
    transport: Transport | None = None
    is_admin: bool = False
    sender_name: str = ""
    self_id: str = ""
    #: 出站能力（由适配器提供；无 transport 时用于测试断言）。
    capabilities: Capabilities | None = None
    _extras: dict[str, Any] = field(default_factory=dict, repr=False)
    _outbox: list[UnifiedMessage] = field(default_factory=list, repr=False)
    _result: UnifiedMessage | None = field(default=None, repr=False)
    #: AstrBot 兼容字段：允许业务层赋值。
    send_streaming: Any = None
    persona_id: str = ""
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------ 标识
    @property
    def unified_msg_origin(self) -> str:
        """``平台:会话类型:会话ID``，与 AstrBot 语义一致。"""
        return f"{self.platform_name}:{self.session_type}:{self.session_id}"

    @property
    def umo(self) -> str:
        return self.unified_msg_origin

    @property
    def source(self) -> "UnifiedEvent":
        """AstrBot 兼容：原始事件自身（extras 共享）。"""
        return self

    @property
    def session(self) -> str:
        return self.session_id

    def get_platform_name(self) -> str:
        return self.platform_name

    def get_platform_id(self) -> str:
        return self.platform_name

    def get_sender_id(self) -> str:
        return str(self.sender_id)

    def get_sender_name(self) -> str:
        return self.sender_name

    def get_self_id(self) -> str:
        return self.self_id

    def get_message_str(self) -> str:
        return self.message_str

    def get_messages(self) -> list[Segment]:
        return list(self.message_obj.message)

    def get_message_type(self) -> MessageType:
        return MessageType.from_session_type(self.session_type)

    def is_group_message(self) -> bool:
        return self.get_message_type() is MessageType.GROUP_MESSAGE

    def is_private_message(self) -> bool:
        return self.get_message_type() is MessageType.PRIVATE_MESSAGE

    @property
    def message(self) -> UnifiedMessage:
        """入站消息的统一视图。"""
        return UnifiedMessage(list(self.message_obj.message), self.message_obj.raw_message)

    @property
    def images(self) -> list[Segment]:
        return [seg for seg in self.message_obj.message if seg.is_image]

    # ------------------------------------------------------------ 事件级状态
    def set_extra(self, key: str, value: Any) -> None:
        self._extras[str(key)] = value

    def get_extra(self, key: str, default: Any = None) -> Any:
        return self._extras.get(str(key), default)

    def clear_extra(self, key: str) -> None:
        self._extras.pop(str(key), None)

    # ------------------------------------------------------------ 出站
    def plain_result(self, text: str) -> UnifiedMessage:
        """构造纯文本出站消息（兼容 AstrBot ``plain_result``）。"""
        return UnifiedMessage.from_text(text)

    def image_result(self, source: str, value: Any = None, **extra: Any) -> UnifiedMessage:
        return UnifiedMessage([Segment.image(source, value, **extra)])

    def make_result(self, segments: list[Segment]) -> UnifiedMessage:
        return UnifiedMessage(list(segments))

    async def send(self, message: UnifiedMessage | str | list[Segment]) -> Any:
        """立即发送消息。

        无 ``transport``（例如离线单测）时记入 ``outbox``，便于断言。
        """
        unified = self._coerce(message)
        if self.transport is None:
            self._outbox.append(unified)
            return None
        return await self.transport.send(unified)

    def send_nowait(self, message: UnifiedMessage | str | list[Segment]) -> None:
        """记录待发送消息（供无事件循环环境使用）。"""
        self._outbox.append(self._coerce(message))

    def set_result(self, message: UnifiedMessage) -> None:
        self._result = message

    def get_result(self) -> UnifiedMessage | None:
        """返回本轮出站结果（NoneBot2 无 ``on_decorating_result`` 钩子）。"""
        return self._result

    @property
    def outbox(self) -> list[UnifiedMessage]:
        return list(self._outbox)

    def pop_outbox(self) -> list[UnifiedMessage]:
        messages, self._outbox = self._outbox, []
        return messages

    def _coerce(self, message: UnifiedMessage | str | list[Segment]) -> UnifiedMessage:
        if isinstance(message, UnifiedMessage):
            return message
        if isinstance(message, str):
            return UnifiedMessage.from_text(message)
        return UnifiedMessage(list(message))

    def degrade(self, message: UnifiedMessage) -> tuple[UnifiedMessage, list[str]]:
        """按当前协议能力降级出站消息。"""
        capabilities = self.capabilities
        if capabilities is None and self.transport is not None:
            capabilities = self.transport.capabilities
        if capabilities is None:
            return message, []
        kept, notes = degrade_segments(list(message.segments), capabilities)
        return UnifiedMessage(kept, message.raw), notes

    def stop_event(self) -> None:
        """兼容占位：NoneBot2 通过 matcher 阻断控制流程。"""


__all__ = ["MessageType", "MessageObj", "Transport", "UnifiedEvent"]
