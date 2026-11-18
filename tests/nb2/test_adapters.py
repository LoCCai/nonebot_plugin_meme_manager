"""三种协议适配器的入站解析与出站转换。"""

from __future__ import annotations

import pytest

from repo.nb2.platform.adapters import get_adapter, resolve_adapter
from repo.nb2.platform.adapters.onebot11 import OneBotV11Adapter
from repo.nb2.platform.adapters.onebot12 import OneBotV12Adapter
from repo.nb2.platform.adapters.qq_official import QQOfficialAdapter
from repo.nb2.platform.event import MessageType
from repo.nb2.platform.message import Segment, UnifiedMessage


# ---------------------------------------------------------------- 注册表
def test_registry_exposes_supported_protocols():
    names = {"onebot11", "onebot12", "qq_official"}
    assert names <= set(get_adapter(name).name for name in names)


def test_resolve_adapter_detects_module():
    class FakeV11Event:
        __module__ = "nonebot.adapters.onebot.v11.event"

    class FakeV12Event:
        __module__ = "nonebot.adapters.onebot.v12.event"

    class FakeQQEvent:
        __module__ = "nonebot.adapters.qq.event"

    class UnknownEvent:
        __module__ = "some.other.thing"

    assert resolve_adapter(FakeV11Event()).name == "onebot11"
    assert resolve_adapter(FakeV12Event()).name == "onebot12"
    assert resolve_adapter(FakeQQEvent()).name == "qq_official"
    assert resolve_adapter(UnknownEvent()) is None


# ------------------------------------------------------------ OneBot v11
class _V11Sender:
    def __init__(self, role="member"):
        self.user_id = 1001
        self.card = "群名片"
        self.nickname = "昵称"
        self.role = role


class _V11GroupEvent:
    __module__ = "nonebot.adapters.onebot.v11.event"

    def __init__(self, message, role="member"):
        self.group_id = 555
        self.user_id = 1001
        self.message_id = 777
        self.self_id = 999
        self.sender = _V11Sender(role)
        self.message_type = "group"
        self.message = message
        self.original_message = None


class _V11PrivateEvent:
    __module__ = "nonebot.adapters.onebot.v11.event"

    def __init__(self, message):
        self.user_id = 1001
        self.message_id = 778
        self.self_id = 999
        self.sender = _V11Sender()
        self.message_type = "private"
        self.message = message
        self.original_message = None


class _FakeBot:
    def __init__(self, self_id=999, superusers=()):
        self.self_id = self_id
        self.sent = []

        class _Config:
            pass

        self.config = _Config()
        self.config.superusers = set(superusers)

    async def send(self, event, message):
        self.sent.append((event, message))
        return {"message_id": 1}


def _v11_message():
    from nonebot.adapters.onebot import v11

    return v11.Message(
        [
            v11.MessageSegment.at(1001),
            v11.MessageSegment.text(" 你好世界"),
            v11.MessageSegment.image(file="https://example.com/a.png"),
        ]
    )


def test_onebot11_inbound_group_event():
    adapter = OneBotV11Adapter()
    event = _V11GroupEvent(_v11_message())
    unified = adapter.to_unified_event(_FakeBot(), event)

    assert unified.platform_name == "onebot11"
    assert unified.session_type == "group"
    assert unified.session_id == "555"
    assert unified.unified_msg_origin == "onebot11:group:555"
    assert unified.get_message_type() is MessageType.GROUP_MESSAGE
    assert unified.sender_id == "1001"
    assert "你好世界" in unified.message_str
    assert len(unified.images) == 1
    assert unified.is_admin is False


def test_onebot11_inbound_private_and_admin_role():
    adapter = OneBotV11Adapter()
    event = _V11PrivateEvent(_v11_message())
    event.sender.role = "owner"
    unified = adapter.to_unified_event(_FakeBot(), event)

    assert unified.session_type == "private"
    assert unified.session_id == "1001"
    assert unified.get_message_type() is MessageType.PRIVATE_MESSAGE
    assert unified.is_admin is True


def test_onebot11_superuser_is_admin():
    adapter = OneBotV11Adapter()
    unified = adapter.to_unified_event(
        _FakeBot(superusers={"1001"}), _V11GroupEvent(_v11_message())
    )
    assert unified.is_admin is True


@pytest.mark.asyncio
async def test_onebot11_outbound_conversion():
    from nonebot.adapters.onebot import v11

    adapter = OneBotV11Adapter()
    bot = _FakeBot()
    event = _V11GroupEvent(_v11_message())
    message = UnifiedMessage(
        [
            Segment.reply("42"),
            Segment.text("看这个"),
            Segment.image(path=r"C:\pics\a.png"),
            Segment.at("1001"),
            Segment.at_all(),
        ]
    )
    await adapter.send(bot, event, message)

    sent_event, sent_message = bot.sent[-1]
    assert sent_event is event
    types = [segment.type for segment in sent_message]
    assert types == ["reply", "text", "image", "at", "at"]
    assert sent_message[0].data["id"] == "42"
    assert sent_message[2].data["file"].startswith("file:///")
    assert sent_message[4].data["qq"] == "all"


# ------------------------------------------------------------ OneBot v12
class _V12GroupEvent:
    __module__ = "nonebot.adapters.onebot.v12.event"

    def __init__(self, message):
        self.detail_type = "group"
        self.group_id = "g-1"
        self.user_id = "u-1"
        self.message_id = "m-1"
        self.self_id = "bot-1"
        self.message = message
        self.alt_message = "alt"


class _V12ChannelEvent:
    __module__ = "nonebot.adapters.onebot.v12.event"

    def __init__(self, message):
        self.detail_type = "channel"
        self.guild_id = "guild-1"
        self.channel_id = "chan-1"
        self.user_id = "u-1"
        self.message_id = "m-2"
        self.message = message


class _V12PrivateEvent:
    __module__ = "nonebot.adapters.onebot.v12.event"

    def __init__(self, message):
        self.detail_type = "private"
        self.user_id = "u-9"
        self.message_id = "m-3"
        self.message = message


def _v12_message():
    from nonebot.adapters.onebot import v12

    return v12.Message(
        [
            v12.MessageSegment(type="text", data={"text": "hi"}),
            v12.MessageSegment(type="image", data={"file_id": "f-1"}),
        ]
    )


def test_onebot12_group_session():
    adapter = OneBotV12Adapter()
    unified = adapter.to_unified_event(_FakeBot(), _V12GroupEvent(_v12_message()))
    assert unified.session_type == "group"
    assert unified.session_id == "g-1"
    assert unified.sender_id == "u-1"
    assert unified.unified_msg_origin == "onebot12:group:g-1"


def test_onebot12_channel_session_combines_guild_and_channel():
    adapter = OneBotV12Adapter()
    unified = adapter.to_unified_event(_FakeBot(), _V12ChannelEvent(_v12_message()))
    assert unified.session_type == "channel"
    assert unified.session_id == "guild-1:chan-1"
    assert unified.get_message_type() is MessageType.CHANNEL_MESSAGE


def test_onebot12_private_session_uses_user_id():
    adapter = OneBotV12Adapter()
    unified = adapter.to_unified_event(_FakeBot(), _V12PrivateEvent(_v12_message()))
    assert unified.session_type == "private"
    assert unified.session_id == "u-9"


@pytest.mark.asyncio
async def test_onebot12_outbound_uses_mention_and_file_id():
    from nonebot.adapters.onebot import v12

    adapter = OneBotV12Adapter()
    bot = _FakeBot()
    event = _V12GroupEvent(_v12_message())
    message = UnifiedMessage(
        [
            Segment.text("hi"),
            Segment.at("u-2"),
            Segment.image("file", "f-2"),
            Segment.reply("m-9"),
        ]
    )
    await adapter.send(bot, event, message)

    _, sent = bot.sent[-1]
    assert [segment.type for segment in sent] == ["text", "mention", "image", "reply"]
    assert sent[1].data["user_id"] == "u-2"
    assert sent[2].data["file_id"] == "f-2"


# --------------------------------------------------------- QQ 官方机器人
class _QQAuthor:
    def __init__(self, user_id="openid-1", role="member"):
        self.id = user_id
        self.username = "用户"
        self.role = role


class _QQGroupAtEvent:
    __module__ = "nonebot.adapters.qq.event"

    def __init__(self, message):
        self.group_openid = "grp-1"
        self.author = _QQAuthor()
        self.id = "msg-1"
        self.content = "hi"
        self.message = message


class _QQC2CEvent:
    __module__ = "nonebot.adapters.qq.event"

    def __init__(self, message):
        self.author = _QQAuthor("openid-2")
        self.id = "msg-2"
        self.content = "hi"
        self.message = message


class _QQGuildEvent:
    __module__ = "nonebot.adapters.qq.event"

    def __init__(self, message):
        self.guild_id = "guild-1"
        self.channel_id = "chan-1"
        self.author = _QQAuthor("member-1")
        self.id = "msg-3"
        self.content = "hi"
        self.message = message


def _qq_message():
    from nonebot.adapters import qq

    return qq.Message([qq.MessageSegment.text("hi")])


def test_qq_group_session():
    adapter = QQOfficialAdapter()
    unified = adapter.to_unified_event(_FakeBot(), _QQGroupAtEvent(_qq_message()))
    assert unified.session_type == "group"
    assert unified.session_id == "grp-1"
    assert unified.sender_id == "openid-1"
    assert unified.unified_msg_origin == "qq_official:group:grp-1"


def test_qq_c2c_session_is_prefixed():
    adapter = QQOfficialAdapter()
    unified = adapter.to_unified_event(_FakeBot(), _QQC2CEvent(_qq_message()))
    assert unified.session_type == "private"
    assert unified.session_id == "c2c:openid-2"


def test_qq_guild_session_combines_ids():
    adapter = QQOfficialAdapter()
    unified = adapter.to_unified_event(_FakeBot(), _QQGuildEvent(_qq_message()))
    assert unified.session_type == "channel"
    assert unified.session_id == "guild-1:chan-1"
    assert unified.get_message_type() is MessageType.CHANNEL_MESSAGE


def test_qq_admin_role_detected():
    adapter = QQOfficialAdapter()
    event = _QQGroupAtEvent(_qq_message())
    event.author.role = "admin"
    assert adapter.to_unified_event(_FakeBot(), event).is_admin is True


@pytest.mark.asyncio
async def test_qq_outbound_uses_file_image_for_local_path(tmp_path):
    adapter = QQOfficialAdapter()
    bot = _FakeBot()
    event = _QQGroupAtEvent(_qq_message())

    image_path = tmp_path / "meme.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    message = UnifiedMessage(
        [
            Segment.text("看"),
            Segment.image(path=str(image_path)),
            Segment.at("openid-2"),
        ]
    )
    await adapter.send(bot, event, message)

    _, sent = bot.sent[-1]
    types = [segment.type for segment in sent]
    assert types[0] == "text"
    assert "file_image" in types
    assert types[-1] == "mention_user"


@pytest.mark.asyncio
async def test_qq_outbound_missing_file_is_skipped():
    adapter = QQOfficialAdapter()
    bot = _FakeBot()
    event = _QQGroupAtEvent(_qq_message())
    message = UnifiedMessage([Segment.text("x"), Segment.image(path=r"C:\nope\missing.png")])
    await adapter.send(bot, event, message)
    _, sent = bot.sent[-1]
    assert [segment.type for segment in sent] == ["text"]


@pytest.mark.parametrize("adapter_name", ["onebot11", "onebot12", "qq_official"])
def test_transport_bound_to_event(adapter_name):
    adapter = get_adapter(adapter_name)
    event = {
        "onebot11": lambda: _V11GroupEvent(_v11_message()),
        "onebot12": lambda: _V12GroupEvent(_v12_message()),
        "qq_official": lambda: _QQGroupAtEvent(_qq_message()),
    }[adapter_name]()
    unified = adapter.to_unified_event(_FakeBot(), event)
    assert unified.transport is not None
    assert unified.transport.capabilities.name == adapter_name
