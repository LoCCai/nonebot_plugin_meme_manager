"""命令处理器测试（直接调用，不依赖 nonebug 的假 bot）。"""

from __future__ import annotations

import pytest

from repo.nb2.platform.event import MessageObj, UnifiedEvent
from repo.nb2.platform.message import Segment, UnifiedMessage
from repo.nb2.matchers import commands as cmd


class _Transport:
    def __init__(self):
        self.sent: list[UnifiedMessage] = []

    class _Caps:
        name = "test"

    capabilities = _Caps()

    async def send(self, message):
        self.sent.append(message)


def make_event(text: str = "", *, is_admin: bool = False, session_id: str = "555") -> UnifiedEvent:
    return UnifiedEvent(
        platform_name="onebot11",
        session_type="group",
        session_id=session_id,
        sender_id="1001",
        message_str=text,
        message_obj=MessageObj(message=[Segment.text(text)], message_str=text),
        transport=_Transport(),
        is_admin=is_admin,
    )


def texts(event: UnifiedEvent) -> list[str]:
    return [m.plain_text for m in event.transport.sent]


@pytest.fixture()
def runtime(tmp_path):
    from repo.nb2.runtime import MemeRuntime

    return MemeRuntime(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_list_emotions_lists_categories(runtime):
    event = make_event()
    results = [r async for r in cmd.list_emotions(event, runtime, "")]
    assert results and "当前图库" in results[0].plain_text
    assert "happy" in results[0].plain_text


@pytest.mark.asyncio
async def test_show_library_stats_reports_pack(runtime):
    event = make_event()
    results = [r async for r in cmd.show_library_stats(event, runtime, "")]
    assert results and "图片" in results[0].plain_text
    assert "builtin-default" in results[0].plain_text


@pytest.mark.asyncio
async def test_add_category_without_args_shows_usage(runtime):
    event = make_event()
    results = [r async for r in cmd.add_category_command(event, runtime, "")]
    assert "添加分类" in results[0].plain_text


@pytest.mark.asyncio
async def test_add_category_existing_reports_exists(runtime):
    event = make_event()
    results = [r async for r in cmd.add_category_command(event, runtime, "happy 已存在")]
    assert "已存在" in results[0].plain_text


@pytest.mark.asyncio
async def test_add_category_creates_new(runtime):
    event = make_event()
    results = [r async for r in cmd.add_category_command(event, runtime, "nb2cat 新分类描述")]
    assert "已创建分类" in results[0].plain_text
    assert "nb2cat" in runtime.category_manager.get_descriptions()


@pytest.mark.asyncio
async def test_add_category_waits_for_description(runtime, monkeypatch):
    """未提供描述时进入等待；模拟用户下一条消息。"""
    from repo.nb2.platform import session as session_module

    event = make_event()

    async def fake_waiter(e, filter_obj=None):
        # 模拟用户发送描述：直接投递给等待中的协程。
        waiter_event = make_event("这是一个等待补充的描述")
        # 找到刚注册的等待者并完成它。
        for key in session_module.pending_keys():
            await session_module.dispatch(key, waiter_event)
            break

    monkeypatch.setattr(cmd, "_wait_for_category_description", fake_waiter)
    results = [r async for r in cmd.add_category_command(event, runtime, "waitedcat")]
    joined = "\n".join(r.plain_text for r in results)
    assert "请发送分类" in joined or "已创建分类" in joined


@pytest.mark.asyncio
async def test_help_command_lists_migrated_commands():
    event = make_event()
    results = [r async for r in cmd.help_command(event, None, "")]
    assert "查看图库" in results[0].plain_text


async def _collect(generator):
    return [item async for item in generator]


def test_sender_scoped_filter_key():
    event = make_event()
    scope = cmd.SenderScopedSessionFilter(event)
    assert scope.filter(event) == f"{event.unified_msg_origin}:1001"
