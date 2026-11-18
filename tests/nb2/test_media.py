"""媒体能力与事件桥接测试。"""

from __future__ import annotations

import base64

import pytest

from repo.nb2 import media
from repo.nb2.platform.bridge import strip_mentions, to_unified_event
from repo.nb2.platform.message import Segment


@pytest.fixture(autouse=True)
def _media_installed():
    media.install()
    yield


def test_segment_has_media_methods():
    segment = Segment.image(url="https://example.com/a.png")
    assert callable(segment.convert_to_base64)
    assert callable(segment.convert_to_file_path)


@pytest.mark.asyncio
async def test_convert_to_base64_from_path(tmp_path):
    path = tmp_path / "a.png"
    payload = b"\x89PNG\r\n\x1a\nDATA"
    path.write_bytes(payload)
    segment = Segment.image(path=str(path))
    encoded = await segment.convert_to_base64()
    assert base64.b64decode(encoded) == payload


@pytest.mark.asyncio
async def test_convert_to_base64_from_base64_source():
    segment = Segment.image("base64", base64.b64encode(b"abc").decode())
    assert await segment.convert_to_base64() == base64.b64encode(b"abc").decode()


@pytest.mark.asyncio
async def test_convert_to_file_path_creates_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_CACHE_DIR", tmp_path / "cache")
    segment = Segment.image("bytes", b"GIF89a-data")
    path = await segment.convert_to_file_path()
    assert path.is_file()
    assert path.suffix == ".gif"
    assert path.read_bytes() == b"GIF89a-data"
    # 相同内容复用同一缓存文件。
    again = await segment.convert_to_file_path()
    assert again == path


@pytest.mark.asyncio
async def test_convert_to_file_path_rejects_empty():
    segment = Segment.image("bytes", b"")
    with pytest.raises(ValueError):
        await segment.convert_to_file_path()


@pytest.mark.asyncio
async def test_convert_to_file_path_unresolvable():
    segment = Segment("image", {})
    with pytest.raises(ValueError):
        await segment.convert_to_file_path()


@pytest.mark.asyncio
async def test_convert_to_file_path_from_onebot_file_field(tmp_path):
    path = tmp_path / "b.jpg"
    path.write_bytes(b"\xff\xd8\xffdata")
    segment = Segment("image", {"file": str(path)})
    resolved = await segment.convert_to_file_path()
    assert resolved == path


def test_strip_mentions_removes_cq_at():
    assert strip_mentions("[CQ:at,qq=123] 你好") == "你好"
    assert strip_mentions("无提及") == "无提及"


def test_to_unified_event_unknown_event_returns_none():
    class Unknown:
        __module__ = "some.unknown.module"

    assert to_unified_event(None, Unknown()) is None


def test_to_unified_event_onebot11():
    from nonebot.adapters.onebot import v11

    class Sender:
        user_id = 42
        nickname = "n"
        card = ""
        role = "member"

    class Ev:
        __module__ = "nonebot.adapters.onebot.v11.event"
        group_id = 7
        user_id = 42
        message_id = 9
        self_id = 1
        sender = Sender()
        message = v11.Message([v11.MessageSegment.text("hi")])

    class Bot:
        self_id = 1

        class config:
            superusers = set()

    unified = to_unified_event(Bot(), Ev())
    assert unified is not None
    assert unified.unified_msg_origin == "onebot11:group:7"
    assert unified.message_str == "hi"
