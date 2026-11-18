"""OneBot v11 协议适配（go-cqhttp / NapCat / Lagrange 等实现）。

已核对的适配器事实：

* ``MessageSegment.at(user_id)`` —— 工厂参数是 ``user_id``，内部存成 ``{"qq": ...}``；
* ``MessageSegment.reply(id_)``；
* ``MessageSegment.face(id_)``；
* ``MessageSegment.image(file, cache=...)``；本地路径需 ``file:///`` 前缀。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...log import logger
from ..capabilities import Capabilities
from ..message import Segment, UnifiedMessage
from .base import PlatformAdapter

ONEBOT11_CAPABILITIES = Capabilities(
    name="onebot11",
    supports_reply=True,
    supports_at=True,
    supports_at_all=True,
    supports_image=True,
    supports_image_url=True,
    supports_image_base64=True,
    supports_local_file=True,
    supports_markdown=False,
    supports_keyboard=False,
    supports_voice=True,
    supports_video=True,
    supports_proactive=True,
)


class OneBotV11Adapter(PlatformAdapter):
    """OneBot v11 协议。"""

    name = "onebot11"
    capabilities = ONEBOT11_CAPABILITIES

    # ------------------------------------------------------------ 入站
    def get_session(self, event: Any) -> tuple[str, str]:
        group_id = getattr(event, "group_id", None)
        if group_id not in (None, ""):
            return "group", str(group_id)
        user_id = self.get_sender_id(event)
        return "private", str(user_id)

    def is_admin(self, bot: Any, event: Any) -> bool:
        if super().is_admin(bot, event):
            return True
        sender = getattr(event, "sender", None)
        role = str(getattr(sender, "role", "") or "").lower()
        return role in {"admin", "owner"}

    # ------------------------------------------------------------ 出站
    async def send(self, bot: Any, event: Any, message: UnifiedMessage) -> Any:
        module = self._adapter_module()
        if module is None:
            raise RuntimeError("nonebot-adapter-onebot 未安装")
        payload = self.build_platform_message(module, message)
        if not payload:
            payload = message.plain_text
        try:
            return await bot.send(event, payload)
        except Exception as exc:
            logger.error("OneBot v11 发送失败：{}", exc)
            raise

    def build_platform_segment(
        self, segment_cls: Any, segment: Segment, module: Any = None
    ) -> Any:
        if segment.type == "image":
            source, value = segment.media_source()
            return self._make(
                segment_cls,
                "image",
                file=self._image_ref(source, value),
                cache=False,
            )
        if segment.type == "at":
            user_id = str(segment.data.get("user_id") or "")
            # v11 的 at() 只接受 user_id 参数，@全体成员用 qq=all。
            if user_id == "all":
                return self._make(segment_cls, "at", user_id="all")
            return self._make(segment_cls, "at", user_id=user_id)
        if segment.type == "reply":
            return self._make(segment_cls, "reply", id_=segment.data.get("message_id"))
        if segment.type == "face":
            return self._make(segment_cls, "face", id_=segment.data.get("id"))
        if segment.type == "voice":
            source, value = segment.media_source()
            return self._make(segment_cls, "record", file=self._image_ref(source, value))
        if segment.type == "video":
            source, value = segment.media_source()
            return self._make(segment_cls, "video", file=self._image_ref(source, value))
        return super().build_platform_segment(segment_cls, segment, module)

    @staticmethod
    def _image_ref(source: str | None, value: Any) -> Any:
        """把统一媒体来源转换为 v11 ``file`` 字段。"""
        if source == "base64" and value:
            return f"base64://{value}"
        if source in {"path", "file"} and value:
            text = str(value)
            if text.startswith(("http://", "https://", "base64://", "file://")):
                return text
            path = Path(text)
            if path.is_file() or text:
                return f"file:///{text.replace(chr(92), '/').lstrip('/')}"
        return value

    @staticmethod
    def _adapter_module() -> Any:
        try:
            from nonebot.adapters.onebot import v11  # type: ignore

            return v11
        except Exception:  # pragma: no cover - 未安装适配器时
            return None


__all__ = ["OneBotV11Adapter", "ONEBOT11_CAPABILITIES"]
