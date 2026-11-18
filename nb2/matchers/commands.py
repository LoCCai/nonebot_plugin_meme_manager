"""表情包管理命令组（NoneBot2 matcher）。

原实现是 AstrBot 的 ``@filter.command_group("表情管理")`` + ``@xxx.command(...)``。
NoneBot2 用 ``on_command`` 注册，子命令由 ``CommandRule`` 解析：``/表情管理 查看图库``
会得到 ``("表情管理", "查看图库")`` 的命令元组，参数里只余命令后的文本。

业务逻辑调用 :class:`nb2.runtime.MemeRuntime`，事件在入口处统一转换为
:class:`nb2.platform.event.UnifiedEvent`。本模块同时是后续子命令的移植范式。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Callable

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.matcher import Matcher
from nonebot.params import Command, CommandArg, EventMessage
from nonebot.typing import T_State

from ..astrbot_types import CommandGroupFilter
from ..log import logger
from ..platform.bridge import to_unified_event
from ..platform.event import UnifiedEvent
from ..platform.session import SessionFilter, session_waiter
from ..runtime import MemeRuntime
from ..state import get_runtime_or_none

PREFIX = "表情管理"

#: 命令处理器签名：``(event, runtime, args) -> AsyncGenerator[UnifiedMessage]``。
CommandHandler = Callable[[UnifiedEvent, MemeRuntime, str], AsyncGenerator[Any, None]]


class CategoryCreationCancelled(Exception):
    """表示发送者取消了分类创建。"""


class ConfirmationCancelled(Exception):
    """表示发送者取消了待处理操作。"""


class SenderScopedSessionFilter(SessionFilter):
    """把交互命令限制在原始发送者与同一会话内。"""

    def __init__(self, event: UnifiedEvent | None = None) -> None:
        self._key = ""
        if event is not None:
            self.init(event)

    def init(self, event: Any) -> None:  # noqa: D401 - 兼容 AstrBot 钩子
        self._key = f"{event.unified_msg_origin}:{event.get_sender_id()}"

    def filter(self, event: Any) -> str:
        return self._key or f"{event.unified_msg_origin}:{event.get_sender_id()}"


# ---------------------------------------------------------------- 命令注册


def register_command(
    command: str,
    handler: CommandHandler,
    *,
    admin: bool = False,
    priority: int = 10,
) -> type[Matcher]:
    """注册 ``/表情管理 <command>`` matcher。

    Args:
        command: 子命令文本。
        handler: 业务协程。
        admin: 是否要求管理员。
        priority: matcher 优先级。

    Returns:
        注册出的 matcher 类型。
    """
    matcher = on_command(
        (PREFIX, command),
        priority=priority,
        block=True,
    )
    label = f"{PREFIX} {command}"

    @matcher.handle()
    async def _entry(
        matcher: Matcher,
        bot: Bot,
        state: T_State,
        cmd: tuple[str, ...] = Command(),
        event: Event = EventMessage(),
        args: Message = CommandArg(),
    ) -> None:
        runtime = get_runtime_or_none()
        if runtime is None:
            await matcher.finish("表情包管理器尚未初始化完成，请稍后再试。")
        unified = to_unified_event(bot, event)
        if unified is None:
            await matcher.finish()
        unified.set_extra("nb2_command", True)
        unified.set_extra("nb2_command_filter", CommandGroupFilter(PREFIX))
        if admin and not unified.is_admin:
            await matcher.finish("⚠️ 该命令仅限管理员使用。")

        raw_args = str(args).strip() if args is not None else ""
        try:
            async for result in handler(unified, runtime, raw_args):
                if result is not None:
                    await unified.send(result)
        except Exception as exc:  # pragma: no cover - 业务异常兜底
            logger.error("命令「{}」执行失败：{}", label, exc, exc_info=True)
            await unified.send(unified.plain_result(f"❌ 命令执行失败：{exc}"))

    return matcher


# ---------------------------------------------------------------- 子命令


async def list_emotions(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """查看图库：列出当前可用分类。"""
    pack_context = await asyncio.to_thread(runtime._resolve_runtime_pack_context, event)
    descriptions = pack_context.get("category_mapping") or runtime.category_mapping
    categories = "\n".join(f"- {tag}: {desc}" for tag, desc in descriptions.items())
    yield event.plain_result(f"🖼️ 当前图库：\n{categories}")


async def show_library_stats(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """图库统计：汇总各分类图片数量。"""
    from ...backend.packs.images import get_emoji_by_category
    from ...backend.packs.resolver import get_pack_paths

    pack_context = await asyncio.to_thread(runtime._resolve_runtime_pack_context, event)
    pack_id = str(pack_context.get("pack_id") or "").strip()
    if not pack_id:
        yield event.plain_result("⚠️ 当前没有可用的表情包。")
        return
    get_pack_paths(pack_id)
    descriptions = pack_context.get("category_mapping") or {}
    total = 0
    lines: list[str] = []
    for category in descriptions:
        try:
            files = await asyncio.to_thread(get_emoji_by_category, category, pack_id)
            count = len(files)
        except Exception:
            count = 0
        total += count
        if count:
            lines.append(f"- {category}: {count} 张")
    header = f"📊 表情包「{pack_id}」共 {total} 张图片"
    body = "\n".join(lines) if lines else "（还没有收集任何图片）"
    yield event.plain_result(f"{header}\n{body}")


async def add_category_command(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """添加分类：``/表情管理 添加分类 [类别] [描述]``。"""
    parts = args.split(maxsplit=1)
    if not parts:
        yield event.plain_result(
            "📌 若要添加分类，请按照此格式操作：\n"
            "/表情管理 添加分类 [类别名称] [描述]\n"
            "也可以只发送类别名称，随后按提示补充描述。"
        )
        return
    category = parts[0].strip()
    description = parts[1].strip() if len(parts) > 1 else ""

    available = await asyncio.to_thread(runtime._get_manageable_categories)
    if category in available:
        yield event.plain_result(f"ℹ️ 分类「{category}」已存在，无需重复创建。")
        return

    if not description:
        yield event.plain_result(f"请发送分类「{category}」的描述，或发送“返回”取消创建。")
        try:
            description = await _wait_for_category_description(event, category)
        except TimeoutError:
            yield event.plain_result("⌛ 等待描述超时，已取消创建分类。")
            return
        except CategoryCreationCancelled:
            return

    try:
        created = await runtime._run_default_pack_mutation(
            "创建表情分类",
            lambda: runtime.category_manager.create_category(category, description),
        )
    except RuntimeError as exc:
        yield event.plain_result(f"⚠️ {exc}")
        return
    if not created:
        yield event.plain_result(f"❌ 创建分类「{category}」失败，请稍后重试。")
        return
    await runtime.reload_emotions()
    yield event.plain_result(f"✅ 已创建分类「{category}」：{description}")


async def help_command(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """帮助：列出已移植的子命令。"""
    yield event.plain_result(
        "🖼️ 表情包管理器（NoneBot2）\n"
        "/表情管理 查看图库 — 查看分类\n"
        "/表情管理 图库统计 — 统计图片数量\n"
        "/表情管理 添加分类 [类别] [描述] — 新建分类（管理员）\n"
        "更多子命令（添加表情 / 收集表情 / 云同步）迁移中。"
    )


async def _wait_for_category_description(
    event: UnifiedEvent, category: str, timeout: int = 60
) -> str:
    """等待用户补充分类描述。

    Raises:
        TimeoutError: 超时未收到描述。
        CategoryCreationCancelled: 用户发送“返回”取消。
    """
    description = ""

    @session_waiter(timeout=timeout, record_history_chains=False)
    async def description_waiter(controller: Any, description_event: UnifiedEvent) -> None:
        nonlocal description
        reply = (description_event.message_str or "").strip()
        if reply == "返回":
            await description_event.send(description_event.plain_result("已取消创建分类。"))
            controller.stop(CategoryCreationCancelled())
            return
        if not reply:
            await description_event.send(
                description_event.plain_result(
                    f"请发送分类「{category}」的描述，或发送“返回”取消创建。"
                )
            )
            controller.keep(timeout=timeout, reset_timeout=True)
            return
        description = reply
        controller.stop()

    await description_waiter(event, SenderScopedSessionFilter(event))
    return description


async def _wait_for_command_confirmation(
    event: UnifiedEvent, timeout: int = 30
) -> bool:
    """等待用户回复「确认」/「取消」。"""

    @session_waiter(timeout=timeout, record_history_chains=False)
    async def confirmation_waiter(controller: Any, confirm_event: UnifiedEvent) -> None:
        reply = (confirm_event.message_str or "").strip()
        if reply in {"确认", "确定"}:
            controller.stop()
            return
        if reply in {"取消", "退出"}:
            await confirm_event.send(confirm_event.plain_result("已取消本次操作。"))
            controller.stop(ConfirmationCancelled())
            return
        await confirm_event.send(
            confirm_event.plain_result("请回复“确认”继续执行，或回复“取消”终止本次操作。")
        )
        controller.keep(timeout=timeout, reset_timeout=True)

    try:
        await confirmation_waiter(event, SenderScopedSessionFilter(event))
        return True
    except TimeoutError:
        await event.send(event.plain_result("⌛ 等待确认超时，操作已取消。"))
        return False
    except ConfirmationCancelled:
        return False


def register_all() -> dict[str, type[Matcher]]:
    """注册全部已移植子命令，返回「子命令 → matcher」映射。"""
    return {
        "查看图库": register_command("查看图库", list_emotions),
        "图库统计": register_command("图库统计", show_library_stats),
        "添加分类": register_command("添加分类", add_category_command, admin=True),
        "帮助": register_command("帮助", help_command),
    }


__all__ = [
    "register_all",
    "register_command",
    "list_emotions",
    "show_library_stats",
    "add_category_command",
    "help_command",
    "CategoryCreationCancelled",
    "ConfirmationCancelled",
    "SenderScopedSessionFilter",
    "PREFIX",
]
