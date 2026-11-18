"""QQ 官方机器人协议适配（``nonebot-adapter-qq``）。

关键事实（经源码核对）：

* ``Bot.send()`` 已按事件类型自动回填 ``msg_id`` / ``msg_seq``，即被动回复窗口
  由适配器负责，本层无需手工处理；
* 富媒体走 ``MessageSegment.file_image(bytes|Path)``，适配器内部会自动
  ``post_group_files`` / ``post_c2c_files`` 上传并组装 ``msg_type=7`` 消息；
* 引用由消息段 ``reference`` 表达，且仅部分场景可用，默认不启用；
* 频道（Guild）与群/C2C 的会话标识字段不同，需要分别归一化。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from ...log import logger
from ..capabilities import Capabilities
from ..message import Segment, UnifiedMessage
from .base import PlatformAdapter

QQ_CAPABILITIES = Capabilities(
    name="qq_official",
    supports_reply=False,
    supports_at=True,
    supports_at_all=False,
    supports_image=True,
    supports_image_url=True,
    supports_image_base64=True,
    supports_local_file=True,
    supports_markdown=True,
    supports_keyboard=True,
    supports_voice=False,
    supports_video=False,
    supports_proactive=False,
    passive_window=300.0,
)

#: 媒体消息类型（与适配器内部 ``msg_type`` 对应）。
MSG_TYPE_TEXT = 0
MSG_TYPE_MARKDOWN = 2
MSG_TYPE_ARK = 3
MSG_TYPE_MEDIA = 7


class QQOfficialAdapter(PlatformAdapter):
    """QQ 官方机器人（群聊 / 单聊 / 频道）。"""

    name = "qq_official"
    capabilities = QQ_CAPABILITIES

    # ------------------------------------------------------------ 入站
    def get_session(self, event: Any) -> tuple[str, str]:
        class_name = type(event).__name__
        if class_name in {"C2CMessageCreateEvent", "C2CMsgReceiveEvent"}:
            return "private", f"c2c:{self._openid(event)}"
        if class_name in {"GroupAtMessageCreateEvent", "GroupMessageCreateEvent"}:
            return "group", self._group_openid(event)
        if "Guild" in class_name or getattr(event, "channel_id", None):
            return "channel", f"{self._guild_id(event)}:{self._channel_id(event)}"
        if getattr(event, "group_openid", None):
            return "group", self._group_openid(event)
        if getattr(event, "openid", None):
            return "private", f"c2c:{self._openid(event)}"
        # C2C 事件的真实字段是 ``author.id``（openid），没有顶层 openid。
        if getattr(getattr(event, "author", None), "id", None):
            return "private", f"c2c:{self._openid(event)}"
        return "other", str(getattr(event, "id", "") or "")

    def get_sender_id(self, event: Any) -> str:
        author = getattr(event, "author", None)
        for attr in ("id", "user_openid", "member_openid"):
            value = getattr(author, attr, None)
            if value:
                return str(value)
        for attr in ("user_openid", "openid", "member_openid"):
            value = getattr(event, attr, None)
            if value:
                return str(value)
        return super().get_sender_id(event)

    def get_sender_name(self, event: Any) -> str:
        author = getattr(event, "author", None)
        for attr in ("username", "nick", "name"):
            value = getattr(author, attr, None)
            if value:
                return str(value)
        return ""

    def get_message_id(self, event: Any) -> str:
        for attr in ("id", "msg_id", "message_id"):
            value = getattr(event, attr, None)
            if value:
                return str(value)
        return ""

    def is_admin(self, bot: Any, event: Any) -> bool:
        if super().is_admin(bot, event):
            return True
        author = getattr(event, "author", None)
        member = getattr(event, "member", None)
        roles = {
            str(getattr(author, "role", "") or "").lower(),
            str(getattr(member, "roles", "") or "").lower(),
        }
        return bool(roles & {"admin", "owner", "administrator", "5", "4"})

    @staticmethod
    def _openid(event: Any) -> str:
        author = getattr(event, "author", None)
        return str(
            getattr(event, "user_openid", None)
            or getattr(event, "openid", None)
            or getattr(author, "user_openid", None)
            or getattr(author, "id", None)
            or ""
        )

    @staticmethod
    def _group_openid(event: Any) -> str:
        return str(
            getattr(event, "group_openid", None) or getattr(event, "group_id", None) or ""
        )

    @staticmethod
    def _guild_id(event: Any) -> str:
        return str(getattr(event, "guild_id", None) or "")

    @staticmethod
    def _channel_id(event: Any) -> str:
        return str(getattr(event, "channel_id", None) or "")

    # ------------------------------------------------------------ 出站
    async def send(self, bot: Any, event: Any, message: UnifiedMessage) -> Any:
        module = self._adapter_module()
        if module is None:
            raise RuntimeError("nonebot-adapter-qq 未安装")
        payload = self.build_platform_message(module, message)
        if payload is None or (hasattr(payload, "__len__") and len(payload) == 0):
            payload = message.plain_text or "（无内容）"
        try:
            return await bot.send(event, payload)
        except Exception as exc:
            logger.error("QQ 官方消息发送失败：{}", exc)
            raise

    def build_platform_segment(
        self, segment_cls: Any, segment: Segment, module: Any = None
    ) -> Any:
        if segment.type == "text":
            return self._make(segment_cls, "text", content=str(segment.data.get("text", "")))
        if segment.type == "image":
            return self._image_segment(segment_cls, segment, module)
        if segment.type == "at":
            user_id = str(segment.data.get("user_id") or "")
            if user_id == "all":
                maker = getattr(segment_cls, "mention_everyone", None)
                return maker() if callable(maker) else None
            maker = getattr(segment_cls, "mention_user", None)
            if callable(maker):
                try:
                    return maker(user_id=user_id)
                except Exception:
                    pass
            return self._make(segment_cls, "mention_user", user_id=user_id)
        if segment.type == "markdown":
            maker = getattr(segment_cls, "markdown", None)
            content = str(segment.data.get("content") or "")
            if callable(maker):
                try:
                    return maker(content)
                except Exception:
                    pass
            return None
        if segment.type == "keyboard":
            maker = getattr(segment_cls, "keyboard", None)
            keyboard = segment.data.get("rows")
            if callable(maker) and keyboard is not None:
                try:
                    return maker(keyboard)
                except Exception:
                    pass
            return None
        if segment.type == "reply":
            # 被动回复由 Bot.send 的 msg_id 承担，无需 reference 消息段。
            return None
        return super().build_platform_segment(segment_cls, segment, module)

    def _image_segment(self, segment_cls: Any, segment: Segment, module: Any = None) -> Any:
        """把图片转换为 QQ 消息段。

        QQ 官方不允许直接发送本地路径，必须走 ``file_image``（适配器内部会上传）。
        URL 图片可直接用 ``image`` 段。
        """
        source, value = segment.media_source()
        maker = getattr(segment_cls, "file_image", None)
        if source == "url":
            return self._make(segment_cls, "image", url=str(value))
        if source == "base64":
            try:
                data = base64.b64decode(str(value))
            except Exception:
                logger.warning("QQ 官方图片 base64 解码失败，已跳过")
                return None
            if callable(maker):
                try:
                    return maker(data)
                except Exception as exc:  # pragma: no cover - 防御性
                    logger.warning("QQ 官方 file_image 构造失败：{}", exc)
                    return None
            return None
        if source == "bytes":
            if callable(maker):
                try:
                    return maker(value)
                except Exception as exc:  # pragma: no cover - 防御性
                    logger.warning("QQ 官方 file_image 构造失败：{}", exc)
                    return None
            return None
        if source in {"path", "file"}:
            path = Path(str(value))
            if not path.is_file():
                logger.warning("QQ 官方图片文件不存在，已跳过：{}", path)
                return None
            if callable(maker):
                try:
                    return maker(path)
                except Exception as exc:  # pragma: no cover - 防御性
                    logger.warning("QQ 官方 file_image 构造失败：{}", exc)
                    return None
            return None
        return None

    @staticmethod
    def _adapter_module() -> Any:
        try:
            from nonebot.adapters import qq  # type: ignore

            return qq
        except Exception:  # pragma: no cover - 未安装适配器时
            return None


__all__ = ["QQOfficialAdapter", "QQ_CAPABILITIES"]
