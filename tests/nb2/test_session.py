"""多轮交互等待器测试。"""

from __future__ import annotations

import asyncio

import pytest

from repo.nb2.platform import session as session_module
from repo.nb2.platform.event import MessageObj, UnifiedEvent
from repo.nb2.platform.message import Segment
from repo.nb2.platform.session import (
    SessionFilter,
    SenderScopedSessionFilter,
    clear_waiters,
    dispatch,
    is_pending,
    pending_keys,
    session_waiter,
)


def make_event(text: str = "", sender: str = "1001") -> UnifiedEvent:
    return UnifiedEvent(
        platform_name="onebot11",
        session_type="group",
        session_id="555",
        sender_id=sender,
        message_str=text,
        message_obj=MessageObj(message=[Segment.text(text)], message_str=text),
    )


@pytest.fixture(autouse=True)
def _clean_waiters():
    clear_waiters()
    yield
    clear_waiters()


def test_filter_keys_are_scoped():
    event = make_event()
    default_key = SessionFilter().filter(event)
    assert default_key == "onebot11:group:555:1001"

    scoped = SenderScopedSessionFilter()
    scoped.init(event)
    assert scoped.filter(make_event(sender="1001")) == default_key
    assert scoped.filter(make_event(sender="2002")) == default_key  # 固定原始发送者


@pytest.mark.asyncio
async def test_waiter_receives_next_message_and_stops():
    """等待器拿到下一条消息后通过 controller.stop() 结束。"""
    received: list[str] = []

    @session_waiter(timeout=5, record_history_chains=False)
    async def waiter(controller, event):
        received.append(event.message_str)
        controller.stop()

    task = asyncio.create_task(waiter(make_event("第一条"), SessionFilter()))
    await asyncio.sleep(0.05)

    keys = pending_keys()
    assert len(keys) == 1
    assert await dispatch(keys[0], make_event("第二条")) is True

    result = await asyncio.wait_for(task, timeout=2)
    assert result is None
    assert received == ["第二条"]
    assert pending_keys() == []


@pytest.mark.asyncio
async def test_waiter_timeout_raises_timeout_error():
    @session_waiter(timeout=0.1)
    async def waiter(controller, event):  # pragma: no cover - 不应被调用
        controller.stop()

    with pytest.raises(TimeoutError):
        await waiter(make_event(), SessionFilter())
    assert pending_keys() == []


@pytest.mark.asyncio
async def test_waiter_keep_resets_timeout():
    """controller.keep(reset_timeout=True) 后续等待新消息。"""
    messages: list[str] = []

    @session_waiter(timeout=2)
    async def waiter(controller, event):
        messages.append(event.message_str)
        if len(messages) == 1:
            controller.keep(timeout=2, reset_timeout=True)
            return
        controller.stop()

    task = asyncio.create_task(waiter(make_event("a"), SessionFilter()))
    await asyncio.sleep(0.05)
    await dispatch(pending_keys()[0], make_event("b"))
    await asyncio.sleep(0.05)
    # keep 之后会重新登记等待键
    assert len(pending_keys()) == 1
    await dispatch(pending_keys()[0], make_event("c"))
    await asyncio.wait_for(task, timeout=2)
    assert messages == ["b", "c"]


@pytest.mark.asyncio
async def test_waiter_stop_with_exception_propagates():
    class Cancelled(Exception):
        pass

    @session_waiter(timeout=2)
    async def waiter(controller, event):
        controller.stop(Cancelled())

    task = asyncio.create_task(waiter(make_event("x"), SessionFilter()))
    await asyncio.sleep(0.05)
    await dispatch(pending_keys()[0], make_event("y"))
    with pytest.raises(Cancelled):
        await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_dispatch_without_waiter_returns_false():
    assert await dispatch("nobody", make_event()) is False


@pytest.mark.asyncio
async def test_duplicate_waiter_for_same_key_rejected():
    @session_waiter(timeout=5)
    async def waiter(controller, event):
        await asyncio.sleep(0.1)
        controller.stop()

    first = asyncio.create_task(waiter(make_event(), SessionFilter()))
    await asyncio.sleep(0.05)
    with pytest.raises(RuntimeError):
        await waiter(make_event(), SessionFilter())
    await dispatch(pending_keys()[0], make_event())
    await asyncio.wait_for(first, timeout=2)


def test_is_pending_and_clear():
    async def scenario():
        @session_waiter(timeout=5)
        async def waiter(controller, event):  # pragma: no cover
            controller.stop()

        task = asyncio.create_task(waiter(make_event(), SessionFilter()))
        await asyncio.sleep(0.05)
        key = pending_keys()[0]
        assert is_pending(key) is True
        clear_waiters()
        assert is_pending(key) is False
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, TimeoutError):
            pass

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_waiter_decorator_requires_coroutine():
    with pytest.raises(TypeError):

        @session_waiter(timeout=1)
        def not_a_coroutine(controller, event):  # pragma: no cover
            return None


@pytest.mark.asyncio
async def test_waiter_direct_decorator_usage():
    """``@session_waiter`` 直接装饰（不传参）也应可用。"""

    received: list[str] = []

    @session_waiter
    async def waiter(controller, event):
        received.append(event.message_str)
        controller.stop()

    task = asyncio.create_task(waiter(make_event("a"), SessionFilter()))
    await asyncio.sleep(0.05)
    await dispatch(pending_keys()[0], make_event("b"))
    await asyncio.wait_for(task, timeout=2)
    assert received == ["b"]


def test_clear_waiter_cancels_pending_future():
    async def scenario():
        @session_waiter(timeout=5)
        async def waiter(controller, event):  # pragma: no cover
            controller.stop()

        task = asyncio.create_task(waiter(make_event(), SessionFilter()))
        await asyncio.sleep(0.05)
        clear_waiters()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
