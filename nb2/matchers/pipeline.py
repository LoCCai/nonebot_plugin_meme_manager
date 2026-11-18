"""消息管线 matcher（入站统一入口）。

AstrBot 用 ``event_message_type(ALL)`` 接管所有消息。NoneBot2 用低优先级
``on_message`` 复刻同一顺序：

1. 手动/连续收集会话优先消费消息；
2. 「添加表情」留下的待上传状态把图片写入表情包；
3. 其余带图消息交给自动收集器。

注意：matcher 注册依赖 NoneBot 驱动，因此由插件入口在驱动初始化后调用
:func:`register`。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from nonebot import on_message
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ..log import logger
from ..platform.bridge import to_unified_event
from ..state import get_runtime_or_none

#: 消息管线优先级（数值越小越先执行；命令 matcher 为 10）。
PIPELINE_PRIORITY = 50


async def handle_message(matcher: Matcher, bot: Bot, event: Event) -> None:
    """统一消息入口。"""
    runtime = get_runtime_or_none()
    if runtime is None:
        return
    unified = to_unified_event(bot, event)
    if unified is None:
        return
    # 命令消息已由命令 matcher 处理，避免重复消费其中的图片。
    if unified.get_extra("nb2_command") or looks_like_command(unified.message_str):
        return

    try:
        if await handle_manual_collection(runtime, unified):
            return
        if await handle_pending_upload(runtime, unified):
            return
        await submit_auto_collect(runtime, unified)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.error("消息管线处理失败：{}", exc, exc_info=True)


async def handle_manual_collection(runtime: Any, event: Any) -> bool:
    """手动/连续收集会话优先消费消息，返回是否消费。"""
    manager = getattr(runtime, "manual_collect_manager", None)
    if manager is None:
        return False
    try:
        response = await manager.consume(event)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("手动收集处理失败：{}", exc)
        return False
    if response is None:
        return False
    if response:
        await event.send(event.plain_result(str(response)))
    return True


async def handle_pending_upload(runtime: Any, event: Any) -> bool:
    """处理「添加表情」命令留下的待上传状态。"""
    from ..platform.message import Segment

    user_key = f"{event.session_id}_{event.get_sender_id()}"
    states = getattr(runtime, "upload_states", None) or {}
    state = states.get(user_key)
    if not state:
        return False
    images = [
        segment
        for segment in event.message_obj.message
        if isinstance(segment, Segment) and segment.is_image
    ]
    if not images:
        return False

    try:
        expired = time.time() > float(state.get("expire_time") or 0)
    except (TypeError, ValueError):
        expired = True
    if expired:
        if states.get(user_key) is state:
            states.pop(user_key, None)
        return False

    category = str(state.get("category") or "").strip()
    pack_id = str(state.get("pack_id") or "").strip()
    recorded = str(state.get("memes_dir") or "").strip()
    if not (category and pack_id and recorded):
        if states.get(user_key) is state:
            states.pop(user_key, None)
        await event.send(
            event.plain_result("⚠️ 上传状态无效，请重新执行 /表情管理 添加表情 [类别名称]。")
        )
        return True

    try:
        from ...backend.packs.resolver import get_pack_paths

        paths = get_pack_paths(pack_id)
        pack_dir = Path(paths["pack_dir"]).resolve()
        memes_dir = Path(paths["memes_dir"]).resolve()
        if memes_dir == pack_dir or memes_dir != Path(recorded).resolve():
            raise ValueError("表情包路径不一致")
    except (OSError, RuntimeError, ValueError) as exc:
        if states.get(user_key) is state:
            states.pop(user_key, None)
        await event.send(event.plain_result(f"⚠️ 上传目标失效：{exc}"))
        return True

    if states.get(user_key) is state:
        states.pop(user_key, None)

    from ...backend.packs.images import DuplicateEmojiError, save_chat_upload_image
    from ...backend.semantic.storage import invalidate_semantic_metadata

    saved = duplicate = failed = 0
    for index, segment in enumerate(images, start=1):
        try:
            path = await asyncio.wait_for(segment.convert_to_file_path(), timeout=30)
            content = await asyncio.to_thread(path.read_bytes)
            await asyncio.to_thread(save_chat_upload_image, memes_dir, category, content)
            saved += 1
        except DuplicateEmojiError:
            duplicate += 1
        except Exception as exc:
            failed += 1
            logger.warning(
                "聊天上传第 {}/{} 张图片失败：表情包={} 分类={} 错误={}",
                index,
                len(images),
                pack_id,
                category,
                exc,
            )
    try:
        await asyncio.to_thread(invalidate_semantic_metadata, pack_dir)
    except Exception:  # pragma: no cover - 防御性
        pass
    try:
        await runtime.reload_emotions()
    except Exception:  # pragma: no cover - 防御性
        pass

    lines = [f"✅ 主表情包「{pack_id}」分类「{category}」："]
    if saved:
        lines.append(f"- 已保存 {saved} 张")
    if duplicate:
        lines.append(f"- 跳过重复 {duplicate} 张")
    if failed:
        lines.append(f"- 失败 {failed} 张")
    await event.send(event.plain_result("\n".join(lines)))
    return True


async def submit_auto_collect(runtime: Any, event: Any) -> None:
    """把带图消息交给自动收集器。"""
    manager = getattr(runtime, "auto_collect_manager", None)
    if manager is None or not event.images:
        return
    try:
        await manager.submit(event)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("提交自动收集失败：{}", exc)


def looks_like_command(text: str) -> bool:
    """粗略识别命令消息，避免把管理命令里的图片当成收集素材。"""
    return (text or "").strip().startswith(("/", "／"))


def register() -> list[type[Matcher]]:
    """注册消息管线 matcher（需已初始化 NoneBot 驱动）。"""
    matcher = on_message(priority=PIPELINE_PRIORITY, block=False)
    matcher.append_handler(handle_message)
    return [matcher]


__all__ = [
    "register",
    "handle_message",
    "PIPELINE_PRIORITY",
    "looks_like_command",
]
