"""指令兼容层与虚拟组件测试。"""

from __future__ import annotations

from repo.nb2 import compat
from repo.nb2.platform.message import Segment


def test_install_injects_logger_and_components():
    injected = compat.install()
    assert injected is True  # 测试环境未安装真实 astrbot

    from astrbot.api import logger as astrbot_logger
    from astrbot.api.message_components import Image, Plain, Reply

    assert astrbot_logger is not None
    assert Image.segment_type == "image"
    assert Reply.segment_type == "reply"
    assert Plain.segment_type == "text"


def test_isinstance_matches_nb2_segments():
    """业务层用 isinstance(component, Image) 挑图，必须对 nb2 Segment 生效。"""
    compat.install()
    from astrbot.api.message_components import Image, Reply

    image = Segment.image(url="https://example.com/a.png")
    text = Segment.text("hi")
    reply = Segment.reply("123")

    assert isinstance(image, Image)
    assert not isinstance(text, Image)
    assert isinstance(reply, Reply)
    assert isinstance(reply, Reply) and not isinstance(reply, Image)


def test_component_instances_are_usable():
    compat.install()
    from astrbot.api.message_components import Image

    component = Image(file="C:/a.png")
    assert component.file == "C:/a.png"


def test_reply_chain_helper_reads_segments():
    from repo.nb2.platform.message import Segment as Seg

    inner = [Seg.image(url="https://example.com/inner.png")]
    reply = Seg.reply("1")
    reply.data["chain"] = inner
    assert reply.chain == inner
    assert Seg.text("x").chain is None
