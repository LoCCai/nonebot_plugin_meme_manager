"""多轮交互等待器（AstrBot ``session_waiter`` 的 NoneBot2 实现）。

原插件用 ``@session_waiter(timeout=..., record_history_chains=False)`` 装饰
协程，然后以 ``await waiter(event, filter)`` 的形式等待下一条消息。NoneBot2
没有对应机制，这里实现等价语义：

* 装饰器把协程包装成可调用对象，签名保持 ``(event, session_filter)``；
* 等待中的会话登记在全局注册表（键为 ``filter(event)``）；
* 入站消息由高优先级 matcher 调用 :func:`dispatch` 投递给等待中的会话；
* ``controller.stop()`` / ``controller.keep()`` 控制等待状态。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..log import logger

#: 等待超时抛出（与 AstrBot 一致，使用内置 ``TimeoutError``）。
WAIT_TIMEOUT = TimeoutError


class SessionController:
    """等待控制器：``stop()`` 结束等待，``keep()`` 继续等待。"""

    def __init__(self, state: "_WaiterState") -> None:
        self._state = state

    def stop(self, exception: BaseException | None = None) -> None:
        """结束等待；传入异常时该异常会从等待中抛出。"""
        self._state.finished = True
        self._state.exception = exception

    def keep(self, timeout: int | None = None, reset_timeout: bool = False) -> None:
        """保持等待，可选重置超时。"""
        self._state.finished = False
        if reset_timeout:
            self._state.deadline = time.monotonic() + (
                float(timeout) if timeout else self._state.timeout
            )

    @property
    def stopped(self) -> bool:
        return self._state.finished


@dataclass(slots=True)
class _WaiterState:
    key: str
    timeout: float
    deadline: float
    future: asyncio.Future
    finished: bool = False
    exception: BaseException | None = None
    queue: list[Any] = field(default_factory=list)


_WAITERS: dict[str, _WaiterState] = {}


def pending_keys() -> list[str]:
    """返回当前等待中的会话键（测试用）。"""
    return list(_WAITERS)


def is_pending(key: str) -> bool:
    return str(key) in _WAITERS


def clear_waiters() -> None:
    """清空所有等待（测试与插件重载用）。"""
    for state in list(_WAITERS.values()):
        if not state.future.done():
            state.future.cancel()
    _WAITERS.clear()


async def dispatch(key: str, event: Any) -> bool:
    """把入站事件投递给等待中的会话。

    Returns:
        是否被某个等待者消费。
    """
    state = _WAITERS.get(str(key))
    if state is None:
        return False
    if state.deadline and time.monotonic() > state.deadline:
        _WAITERS.pop(state.key, None)
        if not state.future.done():
            state.future.set_exception(WAIT_TIMEOUT())
        return False
    if state.future.done():
        _WAITERS.pop(state.key, None)
        return False
    state.future.set_result(event)
    return True


async def _run_waiter(
    func: Callable[..., Awaitable[Any]],
    event: Any,
    session_filter: Any,
    timeout: float,
) -> Any:
    if session_filter is None:
        key = _default_key(event)
    else:
        key = str(session_filter.filter(event))

    loop = asyncio.get_running_loop()
    while True:
        existing = _WAITERS.get(key)
        if existing is not None and not existing.future.done():
            raise RuntimeError(f"会话 {key} 已有等待中的交互")
        state = _WaiterState(
            key=key,
            timeout=float(timeout),
            deadline=time.monotonic() + float(timeout),
            future=loop.create_future(),
        )
        _WAITERS[key] = state
        controller = SessionController(state)
        try:
            next_event = await asyncio.wait_for(
                asyncio.shield(state.future), timeout=float(timeout)
            )
        except asyncio.TimeoutError as exc:
            _WAITERS.pop(key, None)
            raise WAIT_TIMEOUT() from exc
        except asyncio.CancelledError:
            _WAITERS.pop(key, None)
            raise
        finally:
            if _WAITERS.get(key) is state and (
                state.finished or state.future.done() or state.future.cancelled()
            ):
                _WAITERS.pop(key, None)

        await func(controller, next_event)
        if state.finished:
            if state.exception is not None:
                raise state.exception
            return None
        # controller.keep(...)：继续等待下一条消息
        if state.deadline and time.monotonic() > state.deadline:
            raise WAIT_TIMEOUT()


def _default_key(event: Any) -> str:
    return f"{event.unified_msg_origin}:{event.get_sender_id()}"


def session_waiter(
    func: Callable[..., Awaitable[Any]] | None = None,
    *,
    timeout: float = 30,
    record_history_chains: bool = False,  # noqa: ARG001 - 兼容 AstrBot 签名
    session_filter: Any = None,
):
    """AstrBot ``session_waiter`` 的兼容装饰器。

    支持两种用法::

        @session_waiter(timeout=60)
        async def waiter(controller, event): ...

        await waiter(event, SenderScopedSessionFilter())
    """

    def decorator(inner: Callable[..., Awaitable[Any]]):
        if not inspect.iscoroutinefunction(inner):
            raise TypeError("session_waiter 只能装饰协程函数")

        async def wrapper(event: Any, filter_obj: Any = None) -> Any:
            return await _run_waiter(
                inner,
                event,
                filter_obj if filter_obj is not None else session_filter,
                timeout,
            )

        wrapper.__name__ = getattr(inner, "__name__", "session_waiter")
        wrapper.__doc__ = inner.__doc__
        wrapper.__wrapped__ = inner  # type: ignore[attr-defined]
        wrapper._session_timeout = timeout  # type: ignore[attr-defined]
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


class SessionFilter:
    """等待键过滤器基类（对应 AstrBot ``SessionFilter``）。"""

    def filter(self, event: Any) -> str:
        return _default_key(event)


class SenderScopedSessionFilter(SessionFilter):
    """把等待限制在原始发送者与同一会话内。"""

    def init(self, event: Any) -> None:  # noqa: D401 - 兼容 AstrBot 钩子
        self._key = _default_key(event)

    def filter(self, event: Any) -> str:
        return getattr(self, "_key", None) or _default_key(event)


__all__ = [
    "session_waiter",
    "SessionFilter",
    "SenderScopedSessionFilter",
    "SessionController",
    "dispatch",
    "is_pending",
    "pending_keys",
    "clear_waiters",
    "WAIT_TIMEOUT",
]


def _log_debug(message: str) -> None:  # pragma: no cover - 调试辅助
    logger.debug(message)
