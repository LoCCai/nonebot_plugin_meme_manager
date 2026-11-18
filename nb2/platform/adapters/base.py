"""协议适配器基类与公共转换工具。

适配器只依赖 NoneBot2 适配器对象的**鸭子类型**（``event.get_type()``、
``message`` 可迭代、消息段具备 ``type``/``data``），因此：

* 不同 nonebot 适配器版本差异不会导致导入失败；
* 转换逻辑可以在没有安装对应适配器的情况下单测。
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any, Iterable

from ...log import logger
from ..capabilities import Capabilities
from ..event import MessageObj, UnifiedEvent
from ..message import Segment, UnifiedMessage, canonical_type


def module_of(obj: Any) -> str:
    """返回对象所属模块路径，用于识别适配器。"""
    return type(obj).__module__ or ""


def adapter_name_of(event: Any) -> str:
    """推断事件来自哪个 nonebot 适配器。"""
    module = module_of(event)
    if "onebot.v11" in module:
        return "onebot11"
    if "onebot.v12" in module:
        return "onebot12"
    if ".qq" in module or module.endswith("qq"):
        return "qq_official"
    adapter = getattr(event, "adapter", None)
    if adapter is not None:
        module = type(adapter).__module__ or ""
        if "onebot.v11" in module:
            return "onebot11"
        if "onebot.v12" in module:
            return "onebot12"
        if ".qq" in module:
            return "qq_official"
        try:
            name = str(adapter.get_name()).lower().replace(" ", "")
        except Exception:  # pragma: no cover - 防御性
            name = ""
        if name in {"onebotv11", "onebot11"}:
            return "onebot11"
        if name in {"onebotv12", "onebot12"}:
            return "onebot12"
        if name in {"qq", "qqguild", "qqofficial"}:
            return "qq_official"
    return "unknown"


def iter_raw_segments(message: Any) -> list[Any]:
    """把平台消息规范为「消息段列表」或「纯文本」的统一形式。"""
    if message is None:
        return []
    if isinstance(message, str):
        return [message]
    if isinstance(message, (list, tuple)):
        return list(message)
    if hasattr(message, "__iter__"):
        try:
            return list(message)
        except TypeError:  # pragma: no cover - 防御性
            return []
    return [message]


def raw_segment_type(segment: Any) -> str:
    if isinstance(segment, str):
        return "text"
    return str(getattr(segment, "type", "") or "")


def raw_segment_data(segment: Any) -> dict[str, Any]:
    if isinstance(segment, str):
        return {"text": segment}
    data = getattr(segment, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if data is not None:
        try:
            return dict(data)
        except Exception:  # pragma: no cover - 防御性
            return {}
    return {}


def to_unified_message(message: Any) -> UnifiedMessage:
    """把任意平台消息转换为统一消息。

    未知消息段保留为 ``Segment(type=原类型, data=原数据, raw=原件)``，
    以免丢失协议特有的内容（合并转发、按钮、ark 等）。
    """
    segments: list[Segment] = []
    for raw in iter_raw_segments(message):
        raw_type = raw_segment_type(raw)
        data = raw_segment_data(raw)
        seg_type = canonical_type(raw_type)
        if seg_type == "text":
            segments.append(Segment.text(str(data.get("text", ""))))
        else:
            segments.append(Segment(seg_type, data, raw))
    return UnifiedMessage(segments, message)


def _media_from_data(data: dict[str, Any]) -> tuple[str, Any] | None:
    """从平台消息段数据推断媒体来源。"""
    for key, source in (
        ("url", "url"),
        ("file", "file"),
        ("file_id", "file"),
        ("path", "path"),
        ("base64", "base64"),
    ):
        value = data.get(key)
        if value:
            return source, value
    return None


class PlatformAdapter(ABC):
    """协议适配器接口。"""

    name: str = "unknown"
    capabilities: Capabilities = Capabilities(name="unknown")

    def matches(self, event: Any) -> bool:
        """判断该适配器是否处理给定事件。"""
        return adapter_name_of(event) == self.name

    # ------------------------------------------------------------ 入站转换
    @abstractmethod
    def get_session(self, event: Any) -> tuple[str, str]:
        """返回 ``(session_type, session_id)``。"""

    def get_sender_id(self, event: Any) -> str:
        for attr in ("get_user_id", "user_id", "sender"):
            value = getattr(event, attr, None)
            if callable(value):
                try:
                    value = value()
                except Exception:  # pragma: no cover - 防御性
                    continue
            if value is None:
                continue
            if isinstance(value, str):
                return value
            inner = getattr(value, "user_id", None)
            if inner is not None:
                return str(inner)
        return ""

    def get_sender_name(self, event: Any) -> str:
        sender = getattr(event, "sender", None)
        for attr in ("card", "nickname", "name"):
            value = getattr(sender, attr, None)
            if value:
                return str(value)
        return ""

    def get_message_id(self, event: Any) -> str:
        for attr in ("message_id", "id", "msg_id"):
            value = getattr(event, attr, None)
            if value:
                return str(value)
        return ""

    def get_self_id(self, bot: Any, event: Any) -> str:
        return str(getattr(bot, "self_id", "") or getattr(event, "self_id", "") or "")

    def is_admin(self, bot: Any, event: Any) -> bool:
        """默认实现：NoneBot2 ``SUPERUSERS`` 命中即为管理员。"""
        try:
            superusers = set(getattr(bot.config, "superusers", set()) or set())
        except Exception:  # pragma: no cover - 防御性
            superusers = set()
        sender_id = self.get_sender_id(event)
        return bool(sender_id) and sender_id in {str(item) for item in superusers}

    def to_unified_event(self, bot: Any, event: Any) -> UnifiedEvent:
        session_type, session_id = self.get_session(event)
        raw_message = getattr(event, "message", None)
        if raw_message is None:
            raw_message = getattr(event, "original_message", None)
        if isinstance(raw_message, str):
            unified = UnifiedMessage.from_text(raw_message)
        else:
            unified = to_unified_message(raw_message)
        message_id = self.get_message_id(event)
        sender_id = self.get_sender_id(event)
        unified_event = UnifiedEvent(
            platform_name=self.name,
            session_type=session_type,
            session_id=session_id,
            sender_id=sender_id,
            sender_name=self.get_sender_name(event),
            self_id=self.get_self_id(bot, event),
            message_str=unified.plain_text,
            message_obj=MessageObj.from_unified(
                unified,
                message_id=message_id,
                self_id=self.get_self_id(bot, event),
                session_id=session_id,
                sender_id=sender_id,
                sender_name=self.get_sender_name(event),
                raw_message=raw_message,
            ),
            raw_event=event,
            is_admin=self.is_admin(bot, event),
            capabilities=self.capabilities,
        )
        unified_event.transport = self.make_transport(bot, event, unified_event)
        return unified_event

    # ------------------------------------------------------------ 出站
    def make_transport(self, bot: Any, event: Any, unified_event: UnifiedEvent) -> Any:
        """构造绑定到 ``(bot, event)`` 的传输对象。"""
        adapter = self

        class _BoundTransport:
            capabilities = adapter.capabilities

            async def send(self, message: UnifiedMessage) -> Any:
                return await adapter.send(bot, event, message)

        return _BoundTransport()

    @abstractmethod
    async def send(self, bot: Any, event: Any, message: UnifiedMessage) -> Any:
        """发送统一消息。"""

    # ------------------------------------------------------------ 工具
    def build_platform_message(self, module: Any, message: UnifiedMessage) -> Any:
        """把统一消息转换为平台消息对象。

        Args:
            module: 平台适配器模块（如 ``nonebot.adapters.onebot.v11``）。
            message: 统一消息。

        Returns:
            平台消息对象（一般是 ``module.Message``）。
        """
        segments = self.build_platform_segments(module, message)
        message_cls = getattr(module, "Message", None)
        if message_cls is None:  # pragma: no cover - 防御性
            return segments
        try:
            return message_cls(segments)
        except Exception:
            built = message_cls()
            for segment in segments:
                try:
                    built.append(segment)
                except Exception:  # pragma: no cover - 防御性
                    logger.debug("追加消息段失败：{}", segment)
            return built

    def build_platform_segments(self, module: Any, message: UnifiedMessage) -> list[Any]:
        """逐段转换为平台消息段（默认实现覆盖三种协议的公共部分）。"""
        segment_cls = getattr(module, "MessageSegment", None)
        if segment_cls is None:  # pragma: no cover - 防御性
            return []
        built: list[Any] = []
        for segment in message.segments:
            platform = self.build_platform_segment(segment_cls, segment, module)
            if platform is not None:
                built.append(platform)
        return built

    def build_platform_segment(
        self, segment_cls: Any, segment: Segment, module: Any = None
    ) -> Any:
        """转换单个消息段；返回 ``None`` 表示跳过。"""
        if segment.type == "text":
            return self._make(segment_cls, "text", text=str(segment.data.get("text", "")))
        if segment.type == "image":
            return self._image_segment(segment_cls, segment, module)
        if segment.type == "at":
            user_id = str(segment.data.get("user_id") or "")
            if user_id == "all":
                return self._make(segment_cls, "at", user_id="all", qq="all")
            return self._make(segment_cls, "at", user_id=user_id, qq=user_id)
        if segment.type == "reply":
            message_id = segment.data.get("message_id")
            return self._make(
                segment_cls,
                "reply",
                id_=message_id,
                id=message_id,
                message_id=message_id,
            )
        if segment.type == "face":
            face_id = segment.data.get("id")
            return self._make(segment_cls, "face", id_=face_id, id=face_id)
        if segment.type == "voice":
            source, value = segment.media_source()
            return self._make(
                segment_cls,
                "record",
                file=self._file_ref(source, value),
                voice=self._file_ref(source, value),
            )
        if segment.type == "video":
            source, value = segment.media_source()
            return self._make(segment_cls, "video", file=self._file_ref(source, value))
        if segment.type == "file":
            source, value = segment.media_source()
            return self._make(
                segment_cls,
                "file",
                file=self._file_ref(source, value),
            )
        logger.debug("未处理的出站消息段类型：{}", segment.type)
        return None

    def _image_segment(self, segment_cls: Any, segment: Segment, module: Any = None) -> Any:
        source, value = segment.media_source()
        return self._make(segment_cls, "image", file=self._file_ref(source, value))

    @staticmethod
    def _file_ref(source: str | None, value: Any) -> Any:
        """把统一来源转换为平台 ``file`` 字段。"""
        if source == "path" and value:
            return f"file:///{str(value).replace(chr(92), '/').lstrip('/')}"
        if source == "base64" and value:
            return f"base64://{value}"
        return value

    @staticmethod
    def _make(segment_cls: Any, seg_type: str, **kwargs: Any) -> Any:
        """构造平台消息段，兼容工厂方法与直接实例化两种风格。

        不同适配器的工厂签名差异很大（``at(user_id=)`` / ``mention(user_id=)`` /
        ``reply(id_)`` / ``face(id_)``），这里按候选调用逐个尝试，全部失败再退回
        ``Segment(type=..., data=...)`` 的构造方式。
        """
        cleaned = {key: value for key, value in kwargs.items() if value is not None}
        candidates: list[dict[str, Any]] = []
        if seg_type in {"reply", "face"}:
            for key in ("id_", "id", "message_id"):
                if key in cleaned:
                    candidates.append({key: cleaned[key]})
        if seg_type == "at":
            for key in ("user_id", "qq"):
                if key in cleaned:
                    candidates.append({key: cleaned[key]})
        candidates.append(cleaned)
        candidates.append({})

        factory = getattr(segment_cls, seg_type, None)
        if callable(factory):
            seen: set[tuple[str, ...]] = set()
            for attempt in candidates:
                signature = tuple(sorted(attempt))
                if signature in seen:
                    continue
                seen.add(signature)
                try:
                    return factory(**attempt)
                except Exception:
                    continue
        for attempt in candidates:
            try:
                return segment_cls(type=seg_type, data=attempt)
            except Exception:
                continue
        logger.debug("构造消息段失败：type={} data={}", seg_type, cleaned)
        return None


async def call_maybe_async(func: Any, *args: Any, **kwargs: Any) -> Any:
    """调用可能是协程的函数。"""
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def extract_bot_api_names(bot: Any) -> Iterable[str]:
    """列出 bot 暴露的 API 名称（调试用）。"""
    return sorted(
        name for name in dir(bot) if not name.startswith("_") and callable(getattr(bot, name, None))
    )


__all__ = [
    "PlatformAdapter",
    "adapter_name_of",
    "to_unified_message",
    "iter_raw_segments",
    "raw_segment_type",
    "raw_segment_data",
    "call_maybe_async",
    "module_of",
    "_media_from_data",
]
