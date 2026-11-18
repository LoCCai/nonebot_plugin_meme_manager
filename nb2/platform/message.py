"""协议无关的统一消息模型。

图片等媒体统一使用 ``data["source"]`` 描述来源，取值：

``url`` / ``file`` / ``path`` / ``base64`` / ``bytes``

各协议适配器负责在出站时把统一模型转换为平台消息段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

TEXT = "text"
IMAGE = "image"
AT = "at"
REPLY = "reply"
FACE = "face"
VOICE = "voice"
VIDEO = "video"
FILE = "file"
MARKDOWN = "markdown"
KEYBOARD = "keyboard"
ARK = "ark"
UNKNOWN = "unknown"

#: 所有规范消息段类型。
CANONICAL_TYPES = frozenset(
    {TEXT, IMAGE, AT, REPLY, FACE, VOICE, VIDEO, FILE, MARKDOWN, KEYBOARD, ARK, UNKNOWN}
)

#: 各平台消息段类型到规范类型的映射。
SEGMENT_ALIASES = {
    "text": TEXT,
    "plain": TEXT,
    "image": IMAGE,
    "img": IMAGE,
    "picture": IMAGE,
    "photo": IMAGE,
    "at": AT,
    "mention": AT,
    "atall": AT,
    "at_all": AT,
    "reply": REPLY,
    "quote": REPLY,
    "face": FACE,
    "emoji": FACE,
    "mface": FACE,
    "record": VOICE,
    "voice": VOICE,
    "audio": VOICE,
    "video": VIDEO,
    "file": FILE,
    "markdown": MARKDOWN,
    "keyboard": KEYBOARD,
    "buttons": KEYBOARD,
    "button": KEYBOARD,
    "ark": ARK,
    "json": ARK,
    "xml": ARK,
}


def canonical_type(raw_type: str | None) -> str:
    """把平台消息段类型归一为规范类型。"""
    if not raw_type:
        return UNKNOWN
    return SEGMENT_ALIASES.get(str(raw_type).strip().lower(), str(raw_type).strip().lower())


@dataclass(slots=True)
class Segment:
    """协议无关的消息段。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    @classmethod
    def text(cls, text: str) -> "Segment":
        return cls(TEXT, {"text": str(text)})

    @classmethod
    def image(
        cls,
        source: str | None = None,
        value: Any = None,
        *,
        url: str | None = None,
        path: str | None = None,
        **extra: Any,
    ) -> "Segment":
        """构造图片段。

        Args:
            source: ``url`` / ``file`` / ``path`` / ``base64`` / ``bytes``；
                省略时由 ``url`` / ``path`` 快捷参数推断。
            value: 与 ``source`` 对应的取值。
            url: 便捷参数，等价于 ``source="url"``。
            path: 便捷参数，等价于 ``source="path"``。
            extra: 附加字段（如 ``name``、``file_id``）。
        """
        if url is not None:
            source, value = "url", url
        elif path is not None:
            source, value = "path", path
        elif source is None:
            raise TypeError("Segment.image() 需要 source、url 或 path 之一")
        payload: dict[str, Any] = {"source": source, "value": value}
        payload.update(extra)
        return cls(IMAGE, payload)

    @classmethod
    def at(cls, user_id: Any) -> "Segment":
        return cls(AT, {"user_id": str(user_id)})

    @classmethod
    def at_all(cls) -> "Segment":
        return cls(AT, {"user_id": "all"})

    @classmethod
    def reply(cls, message_id: Any) -> "Segment":
        return cls(REPLY, {"message_id": str(message_id)})

    @classmethod
    def voice(cls, source: str, value: Any = None) -> "Segment":
        return cls(VOICE, {"source": source, "value": value})

    @classmethod
    def video(cls, source: str, value: Any = None) -> "Segment":
        return cls(VIDEO, {"source": source, "value": value})

    @classmethod
    def file(cls, source: str, value: Any = None, *, name: str | None = None) -> "Segment":
        data: dict[str, Any] = {"source": source, "value": value}
        if name:
            data["name"] = name
        return cls(FILE, data)

    @classmethod
    def markdown(cls, content: str) -> "Segment":
        return cls(MARKDOWN, {"content": str(content)})

    @classmethod
    def keyboard(cls, rows: Any) -> "Segment":
        return cls(KEYBOARD, {"rows": rows})

    @classmethod
    def ark(cls, payload: Any) -> "Segment":
        return cls(ARK, {"payload": payload})

    @property
    def is_text(self) -> bool:
        return self.type == TEXT

    @property
    def is_image(self) -> bool:
        return self.type == IMAGE

    @property
    def text_value(self) -> str:
        if self.type == TEXT:
            return str(self.data.get("text", ""))
        return ""

    @property
    def chain(self) -> list["Segment"] | None:
        """引用消息段携带的被引用消息内容（协议支持时可用）。

        对应 AstrBot ``Reply.chain``：业务层用它从「回复的图片消息」中收集表情。
        """
        chain = self.data.get("chain")
        if chain is None:
            return None
        return list(chain)

    @property
    def url(self) -> str:
        """图片段的 URL（若来源为 URL）。"""
        if self.data.get("source") == "url":
            return str(self.data.get("value") or "")
        return str(self.data.get("url") or "")

    @property
    def file(self) -> str:
        """图片段的本地文件路径（若来源为路径/文件）。"""
        value = self.data.get("value")
        if self.data.get("source") in {"path", "file"} and value is not None:
            return str(value)
        return str(self.data.get("file") or "")

    def media_source(self) -> tuple[str | None, Any]:
        """返回 ``(source, value)``，非媒体段返回 ``(None, None)``。"""
        source = self.data.get("source")
        if source is None:
            return None, None
        return str(source), self.data.get("value")

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的字典（供 LLM 工具与 WebUI 使用）。"""
        payload: dict[str, Any] = {"type": self.type}
        for key, value in self.data.items():
            if isinstance(value, (bytes, bytearray)):
                payload[key] = f"<{len(value)} bytes>"
            else:
                payload[key] = value
        return payload


@dataclass(slots=True)
class UnifiedMessage:
    """协议无关的消息（消息段的序列）。"""

    segments: list[Segment] = field(default_factory=list)
    raw: Any = None

    @classmethod
    def from_text(cls, text: str) -> "UnifiedMessage":
        return cls([Segment.text(text)])

    @classmethod
    def from_segments(cls, segments: Iterable[Segment]) -> "UnifiedMessage":
        return cls(list(segments))

    @classmethod
    def empty(cls) -> "UnifiedMessage":
        return cls([])

    def __iter__(self) -> Iterator[Segment]:
        return iter(self.segments)

    def __len__(self) -> int:
        return len(self.segments)

    def __bool__(self) -> bool:
        return bool(self.segments)

    def append(self, segment: Segment) -> None:
        self.segments.append(segment)

    def extend(self, segments: Iterable[Segment]) -> None:
        self.segments.extend(segments)

    @property
    def plain_text(self) -> str:
        """拼接所有文本段。"""
        return "".join(seg.text_value for seg in self.segments if seg.is_text)

    def text(self) -> str:
        return self.plain_text

    def images(self) -> list[Segment]:
        return [seg for seg in self.segments if seg.is_image]

    def first_image(self) -> Segment | None:
        for seg in self.segments:
            if seg.is_image:
                return seg
        return None

    def has_image(self) -> bool:
        return any(seg.is_image for seg in self.segments)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [seg.to_dict() for seg in self.segments]

    def copy(self) -> "UnifiedMessage":
        return UnifiedMessage(
            [Segment(seg.type, dict(seg.data), seg.raw) for seg in self.segments],
            self.raw,
        )

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return f"UnifiedMessage({self.to_dicts()})"

    __repr__ = __str__


__all__ = [
    "Segment",
    "UnifiedMessage",
    "canonical_type",
    "CANONICAL_TYPES",
    "SEGMENT_ALIASES",
    "TEXT",
    "IMAGE",
    "AT",
    "REPLY",
    "FACE",
    "VOICE",
    "VIDEO",
    "FILE",
    "MARKDOWN",
    "KEYBOARD",
    "ARK",
    "UNKNOWN",
]
