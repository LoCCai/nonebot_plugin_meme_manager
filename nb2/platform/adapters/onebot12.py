"""OneBot v12 协议适配。

v12 与 v11 的关键差异：

* 消息段统一为 ``Segment(type, data)``，没有 v11 的工厂方法约定；
* 事件按 ``detail_type`` 区分 ``private`` / ``group`` / ``channel``；
* 图片需要先上传换取 ``file_id``（``bot.upload_file``）才能发送。
"""

from __future__ import annotations

import base64
import inspect
from typing import Any

from ...log import logger
from ..capabilities import Capabilities
from ..message import Segment, UnifiedMessage
from .base import PlatformAdapter

ONEBOT12_CAPABILITIES = Capabilities(
    name="onebot12",
    supports_reply=True,
    supports_at=True,
    supports_at_all=False,
    supports_image=True,
    supports_image_url=False,
    supports_image_base64=False,
    supports_local_file=True,
    supports_markdown=False,
    supports_keyboard=True,
    supports_voice=True,
    supports_video=True,
    supports_proactive=True,
)


class OneBotV12Adapter(PlatformAdapter):
    """OneBot v12 通用协议。"""

    name = "onebot12"
    capabilities = ONEBOT12_CAPABILITIES

    def get_session(self, event: Any) -> tuple[str, str]:
        detail_type = str(getattr(event, "detail_type", "") or "").lower()
        if detail_type == "group":
            return "group", str(getattr(event, "group_id", "") or "")
        if detail_type in {"channel", "guild"}:
            guild_id = str(getattr(event, "guild_id", "") or "")
            channel_id = str(getattr(event, "channel_id", "") or "")
            return "channel", f"{guild_id}:{channel_id}" if guild_id else channel_id
        if detail_type:
            return detail_type, str(getattr(event, "user_id", "") or self.get_sender_id(event))
        # 兜底：按存在字段推断
        if getattr(event, "group_id", None):
            return "group", str(event.group_id)
        if getattr(event, "channel_id", None):
            return "channel", str(event.channel_id)
        return "private", str(getattr(event, "user_id", "") or self.get_sender_id(event))

    def get_sender_id(self, event: Any) -> str:
        value = getattr(event, "user_id", None)
        if value not in (None, ""):
            return str(value)
        sender = getattr(event, "sender", None)
        inner = getattr(sender, "user_id", None)
        if inner not in (None, ""):
            return str(inner)
        return super().get_sender_id(event)

    def get_message_id(self, event: Any) -> str:
        for attr in ("message_id", "id"):
            value = getattr(event, attr, None)
            if value:
                return str(value)
        return ""

    async def send(self, bot: Any, event: Any, message: UnifiedMessage) -> Any:
        module = self._adapter_module()
        prepared = await self._prepare_media(bot, event, message)
        payload = self.build_platform_message(module, prepared) if module else None
        if payload is None:
            payload = prepared.plain_text
        try:
            return await bot.send(event, payload)
        except Exception as exc:
            logger.error("OneBot v12 发送失败：{}", exc)
            raise

    async def _prepare_media(
        self, bot: Any, event: Any, message: UnifiedMessage
    ) -> UnifiedMessage:
        """把本地/base64 图片上传为 v12 ``file_id``。"""
        uploader = getattr(bot, "upload_file", None)
        if not callable(uploader):
            return message
        prepared = message.copy()
        for segment in prepared.segments:
            if segment.type != "image":
                continue
            source, value = segment.media_source()
            if source in {"url", "file"} and str(value or "").startswith(("http", "file_id")):
                continue
            file_id = await self._upload(bot, uploader, source, value)
            if file_id:
                segment.data["source"] = "file"
                segment.data["value"] = file_id
                segment.data["file_id"] = file_id
        return prepared

    async def _upload(
        self, bot: Any, uploader: Any, source: str | None, value: Any
    ) -> str | None:
        try:
            if source == "base64":
                raw = base64.b64decode(str(value))
                result = uploader(type="image", data=raw)
            elif source == "bytes":
                result = uploader(type="image", data=value)
            elif source in {"path", "file"}:
                result = uploader(type="image", url=f"file:///{str(value).replace(chr(92), '/').lstrip('/')}")
            elif source == "url":
                result = uploader(type="image", url=str(value))
            else:  # pragma: no cover - 防御性
                return None
            if inspect.isawaitable(result):
                result = await result
            payload = result if isinstance(result, dict) else getattr(result, "data", {})
            return str((payload or {}).get("file_id") or "") or None
        except Exception as exc:
            logger.warning("OneBot v12 媒体上传失败，回退原始来源：{}", exc)
            return None

    def build_platform_segment(
        self, segment_cls: Any, segment: Segment, module: Any = None
    ) -> Any:
        maker = self._segment_maker(segment_cls)
        if segment.type == "text":
            return maker("text", {"text": str(segment.data.get("text", ""))})
        if segment.type == "image":
            source, value = segment.media_source()
            if source == "url" and not str(value or "").startswith(("http://", "https://")):
                pass
            return maker("image", {"file_id": segment.data.get("file_id") or value})
        if segment.type == "at":
            user_id = str(segment.data.get("user_id") or "")
            return maker("mention", {"user_id": user_id})
        if segment.type == "reply":
            return maker("reply", {"message_id": str(segment.data.get("message_id") or "")})
        if segment.type == "keyboard":
            return maker("keyboard", {"rows": segment.data.get("rows") or []})
        return super().build_platform_segment(segment_cls, segment, module)

    @staticmethod
    def _segment_maker(segment_cls: Any) -> Any:
        """v12 的 ``MessageSegment`` 一般不可直接实例化，走工厂方法。"""

        def make(seg_type: str, data: dict[str, Any]) -> Any:
            for candidate in (getattr(segment_cls, seg_type, None), segment_cls):
                if candidate is None:
                    continue
                try:
                    if candidate is segment_cls:
                        return candidate(type=seg_type, data=data)
                    return candidate(**data)
                except Exception:
                    continue
            try:
                return segment_cls(type=seg_type, data=data)
            except Exception:  # pragma: no cover - 防御性
                logger.debug("OneBot v12 消息段构造失败：{}", seg_type)
                return None

        return make

    @staticmethod
    def _adapter_module() -> Any:
        try:
            from nonebot.adapters.onebot import v12  # type: ignore

            return v12
        except Exception:  # pragma: no cover - 未安装适配器时
            return None


__all__ = ["OneBotV12Adapter", "ONEBOT12_CAPABILITIES"]
