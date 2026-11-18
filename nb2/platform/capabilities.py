"""协议能力矩阵与出站降级策略。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .message import AT, IMAGE, KEYBOARD, MARKDOWN, REPLY, Segment


@dataclass(frozen=True, slots=True)
class Capabilities:
    """描述某个协议/适配器支持的消息能力。"""

    name: str
    supports_reply: bool = True
    supports_at: bool = True
    supports_at_all: bool = True
    supports_image: bool = True
    supports_image_url: bool = True
    supports_image_base64: bool = False
    supports_local_file: bool = True
    supports_markdown: bool = False
    supports_keyboard: bool = False
    supports_voice: bool = False
    supports_video: bool = False
    supports_proactive: bool = True
    #: 主动消息窗口（秒）。``None`` 表示不受限。
    passive_window: float | None = None
    #: 未声明能力的消息段类型集合，交由适配器自行处理。
    extra_segment_types: frozenset[str] = field(default_factory=frozenset)

    def supports_segment(self, segment_type: str) -> bool:
        mapping = {
            REPLY: self.supports_reply,
            AT: self.supports_at,
            IMAGE: self.supports_image,
            MARKDOWN: self.supports_markdown,
            KEYBOARD: self.supports_keyboard,
        }
        if segment_type in mapping:
            return mapping[segment_type]
        return True


def degrade_segments(
    segments: list[Segment],
    capabilities: Capabilities,
) -> tuple[list[Segment], list[str]]:
    """按能力矩阵降级出站消息段。

    返回 ``(保留的消息段, 降级说明列表)``。无法表达时保留文本兜底，
    绝不因为单个不支持的消息段而让整条消息发送失败。
    """
    kept: list[Segment] = []
    notes: list[str] = []
    dropped_at_all = False

    for segment in segments:
        if segment.type == AT and segment.data.get("user_id") == "all":
            if capabilities.supports_at_all:
                kept.append(segment)
            else:
                dropped_at_all = True
            continue

        if segment.type == REPLY and not capabilities.supports_reply:
            notes.append("该协议不支持引用回复，已省略引用")
            continue

        if segment.type in (MARKDOWN, KEYBOARD) and not capabilities.supports_segment(
            segment.type
        ):
            notes.append(f"该协议不支持 {segment.type} 消息段，已省略")
            continue

        if segment.type == IMAGE:
            source = str(segment.data.get("source") or "")
            if source == "url" and not capabilities.supports_image_url:
                notes.append("该协议不支持图片 URL，已尝试回退其他来源")
                fallback = segment.data.get("value")
                if not capabilities.supports_image_base64 and not capabilities.supports_local_file:
                    if isinstance(fallback, str):
                        kept.append(Segment.text(fallback))
                    notes.append("图片无法发送，已改为文本链接")
                    continue
            if source in {"path", "file"} and not capabilities.supports_local_file:
                if capabilities.supports_image_base64:
                    notes.append("该协议不支持本地路径，已改为 base64 发送")
                else:
                    notes.append("该协议不支持本地图片文件")
                    continue

        kept.append(segment)

    if dropped_at_all:
        notes.append("该协议不支持 @全体成员，已省略")
    return kept, notes


__all__ = ["Capabilities", "degrade_segments"]
