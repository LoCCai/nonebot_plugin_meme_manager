"""主动配图管线测试（不发起真实网络请求）。"""

from __future__ import annotations

import pytest

from repo.nb2 import pipeline as pipe
from repo.nb2.platform.event import MessageObj, UnifiedEvent
from repo.nb2.platform.message import Segment, UnifiedMessage


class _Transport:
    def __init__(self):
        self.sent: list[UnifiedMessage] = []

    class _Caps:
        name = "test"

    capabilities = _Caps()

    async def send(self, message):
        self.sent.append(message)


def make_event(text: str = "你好") -> UnifiedEvent:
    return UnifiedEvent(
        platform_name="onebot11",
        session_type="group",
        session_id="555",
        sender_id="1001",
        message_str=text,
        message_obj=MessageObj(message=[Segment.text(text)], message_str=text),
        transport=_Transport(),
    )


@pytest.fixture()
def runtime(tmp_path):
    from repo.nb2.runtime import MemeRuntime

    return MemeRuntime(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_pipeline_skips_empty_message(runtime):
    event = make_event("")
    result = await pipe.run_pipeline(runtime, event, force=True)
    assert result.status == "skipped"
    assert result.reason == "empty_message"


@pytest.mark.asyncio
async def test_pipeline_probability_zero_skips(runtime, monkeypatch):
    runtime.emotions_probability = 0
    event = make_event()
    result = await pipe.run_pipeline(runtime, event)
    assert result.status == "skipped"
    assert result.reason == "probability"


def test_probability_gate_uses_roll(runtime):
    runtime.emotions_probability = 50
    assert pipe.probability_gate(runtime, roll=1) is True
    assert pipe.probability_gate(runtime, roll=100) is False


def test_scope_allows_always_true(runtime):
    assert pipe.scope_allows(runtime, make_event()) is True


@pytest.mark.asyncio
async def test_pipeline_without_providers_abstains(runtime):
    """没有 LLM 与语义索引时，管线不应发送任何内容。"""
    event = make_event()
    result = await pipe.run_pipeline(runtime, event, force=True)
    assert result.status == "abstained"
    assert result.images == []
    assert event.transport.sent == []


@pytest.mark.asyncio
async def test_generate_reply_without_provider(runtime):
    assert await pipe.generate_reply(runtime, make_event(), "hi") == ""


@pytest.mark.asyncio
async def test_search_candidates_without_index(runtime):
    assert await pipe.search_candidates(runtime, make_event(), "开心") == []


@pytest.mark.asyncio
async def test_select_images_empty_candidates(runtime):
    assert await pipe.select_images(runtime, make_event(), query="q", candidates=[]) == []


def test_resolve_image_path_missing(runtime):
    event = make_event()
    assert pipe.resolve_image_path(runtime, event, {"category": "happy", "filename": "x.png"}) is None


def test_resolve_image_path_found(runtime):
    """候选文件存在时应能解析为本地路径。"""
    event = make_event()
    context = runtime._resolve_runtime_pack_context(event=event)
    category = "happy"
    target_dir = context["memes_dir"] / category
    target_dir.mkdir(parents=True, exist_ok=True)
    image = target_dir / "meme.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    path = pipe.resolve_image_path(
        runtime, event, {"category": category, "filename": "meme.png"}
    )
    assert path == str(image)
