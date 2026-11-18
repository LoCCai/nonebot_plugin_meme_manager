"""AI 工具注册表与命令测试。"""

from __future__ import annotations

import pytest

from repo.nb2.platform.event import MessageObj, UnifiedEvent
from repo.nb2.platform.message import Segment, UnifiedMessage
from repo.nb2.tools import (
    CANDIDATES_CACHE_KEY,
    build_default_registry,
)


class _Transport:
    def __init__(self):
        self.sent: list[UnifiedMessage] = []

    class _Caps:
        name = "test"

    capabilities = _Caps()

    async def send(self, message):
        self.sent.append(message)


def make_event() -> UnifiedEvent:
    return UnifiedEvent(
        platform_name="onebot11",
        session_type="group",
        session_id="555",
        sender_id="1001",
        message_str="",
        message_obj=MessageObj(message=[Segment.text("")], message_str=""),
        transport=_Transport(),
    )


@pytest.fixture()
def runtime(tmp_path):
    from repo.nb2.runtime import MemeRuntime

    return MemeRuntime(data_dir=tmp_path)


def test_registry_exposes_three_tools(runtime):
    registry = build_default_registry(runtime)
    names = {tool.name for tool in registry.all()}
    assert names == {"search_memes", "send_meme", "preview_memes"}
    schemas = registry.schemas()
    assert all(schema["type"] == "function" for schema in schemas)
    assert any(schema["function"]["name"] == "send_meme" for schema in schemas)


def test_search_requires_query(runtime):
    event = make_event()
    registry = build_default_registry(runtime)
    result = run(registry.get("search_memes").call(event, runtime, {}))
    assert result.ok is False
    assert "query" in result.message


def test_search_rejects_without_index(runtime):
    event = make_event()
    registry = build_default_registry(runtime)
    result = run(registry.get("search_memes").call(event, runtime, {"query": "开心"}))
    assert result.ok is False
    assert "语义索引" in result.message


def test_send_without_candidates_rejected(runtime):
    event = make_event()
    registry = build_default_registry(runtime)
    result = run(registry.get("send_meme").call(event, runtime, {"ids": ["abc"]}))
    assert result.ok is False
    assert "候选" in result.message


def test_send_rejects_unknown_ids(runtime, tmp_path):
    event = make_event()
    event.set_extra(CANDIDATES_CACHE_KEY, [{"id": "a1b2c3", "entry_id": "a1b2c3"}])
    registry = build_default_registry(runtime)
    result = run(registry.get("send_meme").call(event, runtime, {"ids": ["zzz999"]}))
    assert result.ok is False


def test_preview_requires_candidates(runtime):
    event = make_event()
    registry = build_default_registry(runtime)
    result = run(registry.get("preview_memes").call(event, runtime, {}))
    assert result.ok is False

    event.set_extra(
        CANDIDATES_CACHE_KEY,
        [{"id": "aaa111", "category": "happy", "caption": "庆祝"}],
    )
    result = run(registry.get("preview_memes").call(event, runtime, {}))
    assert result.ok is True
    assert "aaa111" in result.message
    assert "happy" in result.message


def test_search_command_usage_without_args(runtime):
    import asyncio

    from repo.nb2.matchers import tools_cmds

    event = make_event()
    results = asyncio.run(_collect(tools_cmds.search_memes_command(event, runtime, "")))
    assert "用法" in results[0].plain_text


def test_send_command_usage_without_args(runtime):
    import asyncio

    from repo.nb2.matchers import tools_cmds

    event = make_event()
    results = asyncio.run(_collect(tools_cmds.send_meme_command(event, runtime, "")))
    assert "用法" in results[0].plain_text


def test_tool_schemas_are_json_serializable(runtime):
    import json

    schemas = build_default_registry(runtime).schemas()
    assert json.loads(json.dumps(schemas, ensure_ascii=False)) == schemas


async def _collect(generator):
    return [item async for item in generator]


def run(coro):
    import asyncio

    return asyncio.run(coro)
