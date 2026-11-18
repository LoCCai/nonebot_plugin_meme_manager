"""平台抽象层：消息模型与能力降级。"""

from __future__ import annotations

import pytest

from repo.nb2.platform.capabilities import Capabilities, degrade_segments
from repo.nb2.platform.message import (
    Segment,
    UnifiedMessage,
    canonical_type,
)


def test_canonical_type_aliases():
    assert canonical_type("plain") == "text"
    assert canonical_type("mention") == "at"
    assert canonical_type("quote") == "reply"
    assert canonical_type("Record") == "voice"
    assert canonical_type(None) == "unknown"
    assert canonical_type("custom_thing") == "custom_thing"


def test_segment_factories():
    text = Segment.text("你好")
    assert text.is_text and text.text_value == "你好"

    url_image = Segment.image(url="https://example.com/a.png")
    assert url_image.is_image
    assert url_image.data["source"] == "url"

    path_image = Segment.image(path=r"C:\pics\a.png")
    assert path_image.data["source"] == "path"

    at_all = Segment.at_all()
    assert at_all.data["user_id"] == "all"


def test_unified_message_text_and_images():
    message = UnifiedMessage(
        [
            Segment.text("前缀"),
            Segment.image(url="https://example.com/a.png"),
            Segment.text("后缀"),
        ]
    )
    assert message.plain_text == "前缀后缀"
    assert len(message.images()) == 1
    assert message.has_image()
    assert message.first_image() is not None


def test_unified_message_copy_is_deep_enough():
    original = UnifiedMessage([Segment.image(url="https://example.com/a.png")])
    clone = original.copy()
    clone.segments[0].data["source"] = "file"
    assert original.segments[0].data["source"] == "url"


def test_segment_to_dict_hides_raw_bytes():
    segment = Segment.image("bytes", b"\x00\x01\x02")
    assert segment.to_dict()["value"] == "<3 bytes>"
    assert Segment.text("x").to_dict() == {"type": "text", "text": "x"}


def test_degrade_drops_reply_when_unsupported():
    capabilities = Capabilities(name="qq", supports_reply=False)
    kept, notes = degrade_segments(
        [Segment.reply("123"), Segment.text("hi")], capabilities
    )
    assert [seg.type for seg in kept] == ["text"]
    assert any("引用" in note for note in notes)


def test_degrade_reports_at_all_unsupported():
    capabilities = Capabilities(name="v12", supports_at_all=False)
    kept, notes = degrade_segments([Segment.at_all(), Segment.text("x")], capabilities)
    assert [seg.type for seg in kept] == ["text"]
    assert any("@全体" in note for note in notes)


def test_degrade_drops_markdown_and_keyboard():
    capabilities = Capabilities(name="v11", supports_markdown=False, supports_keyboard=False)
    kept, notes = degrade_segments(
        [Segment.markdown("# hi"), Segment.keyboard([[]]), Segment.text("ok")],
        capabilities,
    )
    assert [seg.type for seg in kept] == ["text"]
    assert len(notes) == 2


def test_degrade_keeps_supported_segments_untouched():
    capabilities = Capabilities(name="v11")
    original = [Segment.text("a"), Segment.at("1"), Segment.image(url="https://e/a.png")]
    kept, notes = degrade_segments(original, capabilities)
    assert kept == original
    assert notes == []


@pytest.mark.parametrize(
    ("segment_type", "supported"),
    [
        ("reply", True),
        ("at", True),
        ("image", True),
        ("markdown", False),
        ("keyboard", False),
        ("text", True),
    ],
)
def test_capability_supports_segment(segment_type, supported):
    assert Capabilities(name="x").supports_segment(segment_type) is supported
