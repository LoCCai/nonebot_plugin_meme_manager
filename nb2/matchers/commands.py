"""表情包管理命令组（NoneBot2 matcher）。

原实现是 AstrBot 的 ``@filter.command_group("表情管理")`` + ``@xxx.command(...)``。
NoneBot2 用 ``on_command`` 注册，子命令由 ``CommandRule`` 解析：``/表情管理 查看图库``
会得到 ``("表情管理", "查看图库")`` 的命令元组，参数里只余命令后的文本。

业务逻辑调用 :class:`nb2.runtime.MemeRuntime`，事件在入口处统一转换为
:class:`nb2.platform.event.UnifiedEvent`。本模块同时是后续子命令的移植范式。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
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
        "/表情管理 添加表情 [类别] — 30 秒内发送图片入库（管理员）\n"
        "/表情管理 收集表情 — 收集引用/附带图片（管理员）\n"
        "/表情管理 开始收集 / 结束收集 — 连续收集图片（管理员）\n"
        "/表情管理 恢复默认表情包 — 从官方仓库安装默认包（管理员）\n"
        "/表情管理 清空指定类型 [类别] — 清空分类图片（管理员）\n"
        "/表情管理 清空全部 — 清空全部图片（管理员）\n"
        "/表情管理 删除类型本身 [类别] — 删除分类（管理员）\n"
        "/表情管理 同步状态 — 查看图床同步状态\n"
        "/表情管理 同步到云端 / 从云端同步 — 增量同步（管理员）\n"
        "/表情管理 覆盖到云端 / 从云端覆盖 — 全量覆盖（管理员）"
    )


# ------------------------------------------------------------ 收集相关


async def collect_memes(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """收集表情：通过分类管线收集引用或附带图片。"""
    event.set_extra("meme_manager_manual_collection_command", True)
    response = await runtime.manual_collect_manager.quick(event)
    if response:
        yield event.plain_result(response)


async def start_collecting_memes(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """开始收集：进入按发送者隔离的连续图片收集会话。"""
    event.set_extra("meme_manager_manual_collection_command", True)
    response = await runtime.manual_collect_manager.start(event)
    manager = runtime.manual_collect_manager
    if manager.key(event) in manager.sessions:
        runtime.upload_states.pop(f"{event.session_id}_{event.get_sender_id()}", None)
    yield event.plain_result(response)


async def stop_collecting_memes(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """结束收集：停止接收图片并报告结果。"""
    event.set_extra("meme_manager_manual_collection_command", True)
    yield event.plain_result(runtime.manual_collect_manager.stop(event))


# ------------------------------------------------------------ 添加表情


async def upload_meme(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """添加表情：``/表情管理 添加表情 [类别]``，随后 30 秒内发图。"""
    from ...backend.packs.categories import resolve_safe_category_directory
    from ...backend.packs.resolver import get_pack_paths, load_pack_category_mapping

    manual_manager = getattr(runtime, "manual_collect_manager", None)
    if manual_manager and manual_manager.key(event) in manual_manager.sessions:
        yield event.plain_result("当前正在连续收集，请先发送 /表情管理 结束收集。")
        return
    category = str(args or "").strip()
    if not category:
        yield event.plain_result(
            "📌 若要添加表情，请按照此格式操作：\n"
            "/表情管理 添加表情 [类别名称]\n"
            "（/表情管理 查看图库 会列出当前可用类别；"
            "聊天上传仅写入当前会话规则识别出的主表情包）"
        )
        return

    pack_context = await asyncio.to_thread(runtime._resolve_runtime_pack_context, event)
    pack_id = str(pack_context.get("pack_id") or "").strip()
    context_memes_dir = str(pack_context.get("memes_dir") or "").strip()
    if not pack_id or not context_memes_dir:
        yield event.plain_result(
            f"⚠️ 当前没有可用的表情包，无法将表情添加到分类「{category}」。"
        )
        return
    try:
        pack_paths = get_pack_paths(pack_id)
        pack_dir = Path(pack_paths["pack_dir"]).resolve()
        memes_dir = Path(pack_paths["memes_dir"]).resolve()
        runtime_memes_dir = Path(context_memes_dir).resolve()
        if memes_dir == pack_dir:
            raise ValueError("memes directory must be a child of the pack")
        memes_dir.relative_to(pack_dir)
    except (OSError, RuntimeError, ValueError):
        pack_dir = None
        memes_dir = None
        runtime_memes_dir = None
    if (
        pack_dir is None
        or memes_dir is None
        or not pack_dir.is_dir()
        or memes_dir != runtime_memes_dir
    ):
        yield event.plain_result(
            f"⚠️ 主表情包「{pack_id}」分类「{category}」的路径"
            "与当前使用规则不一致，请刷新使用规则后重试。"
        )
        return
    try:
        resolve_safe_category_directory(memes_dir, category)
    except ValueError:
        yield event.plain_result(
            f"您输入的表情包类别「{category}」无效。\n"
            f"聊天上传只接受主表情包「{pack_id}」内的安全类别名称，"
            "可使用 /表情管理 查看图库 查看当前可用类别。"
        )
        return

    primary_mapping = await asyncio.to_thread(load_pack_category_mapping, pack_id)
    if category not in primary_mapping and not (memes_dir / category).is_dir():
        merged_mapping = pack_context.get("category_mapping")
        if isinstance(merged_mapping, dict) and category in merged_mapping:
            yield event.plain_result(
                f"分类「{category}」属于其他绑定的表情包；"
                f"聊天上传只会写入当前主表情包「{pack_id}」。\n"
                f"请先在主表情包「{pack_id}」中添加该分类。"
            )
        else:
            yield event.plain_result(
                f"主表情包「{pack_id}」中，您输入的表情包类别「{category}」无效。\n"
                "可使用 /表情管理 查看图库 查看当前可用类别。"
            )
        return

    user_key = f"{event.session_id}_{event.get_sender_id()}"
    runtime.upload_states[user_key] = {
        "category": category,
        "pack_id": pack_id,
        "memes_dir": str(memes_dir),
        "expire_time": time.time() + 30,
    }
    yield event.plain_result(
        f"请在30秒内发送要添加到主表情包「{pack_id}」"
        f"【{category}】类别的图片（可发送多张图片）。\n"
        "图片只会写入当前主表情包，不会写入其他绑定表情包。"
    )


# ------------------------------------------------------------ 恢复默认包


async def restore_default_memes_command(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """恢复默认表情包：从社区索引安装首个官方表情包并设为默认。"""
    from ...backend.packs.storage import install_first_official_pack_from_index
    from ...config import COMMUNITY_INDEX_URL

    if str(args or "").strip():
        yield event.plain_result(
            "ℹ️ 该命令已改为从官方仓库安装默认包，不再支持按类别恢复。"
        )

    try:
        result = await runtime._run_guarded_runtime_file_operation(
            "安装官方资源包",
            install_first_official_pack_from_index,
            index_url=COMMUNITY_INDEX_URL,
            overwrite=False,
            set_as_default=True,
            github_accelerator_url=runtime._get_github_accelerator_url(),
        )
        selected_name = str(
            result.get("selected_pack_name")
            or result.get("name")
            or result.get("pack_id")
            or ""
        )
        selected_pack_id = str(
            result.get("pack_id") or result.get("selected_pack_id") or ""
        )
        runtime._reload_personas()
        yield event.plain_result(
            f"✅ 已从官方仓库安装默认表情包：{selected_name} ({selected_pack_id})。"
        )
    except FileExistsError:
        yield event.plain_result(
            "⚠️ 目标表情包已存在。请先在广场或管理页卸载同名包后重试。"
        )
    except RuntimeError as exc:
        yield event.plain_result(f"⚠️ {exc}")
    except Exception as exc:
        logger.error("从官方仓库安装默认表情包失败: %s", exc, exc_info=True)
        yield event.plain_result(f"❌ 安装默认表情包失败：{exc}")


# ------------------------------------------------------------ 清空与删除


def _format_category_counts(category_counts: dict[str, int], limit: int = 8) -> str:
    """格式化分类计数（移植自上游命令 mixin）。"""
    non_empty_items = [
        (c, cnt) for c, cnt in sorted(category_counts.items()) if cnt > 0
    ]
    if not non_empty_items:
        return "无可删除的表情包文件。"
    lines = [f"- {c}: {cnt} 个" for c, cnt in non_empty_items[:limit]]
    if len(non_empty_items) > limit:
        lines.append(f"- 其余 {len(non_empty_items) - limit} 个类型已省略")
    return "\n".join(lines)


async def clear_category_command(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """清空指定类型：保留分类本身，仅删除图片。"""
    from ...backend.packs.images import clear_category_emojis, get_emoji_by_category

    category = str(args or "").strip()
    if not category:
        yield event.plain_result(
            "📌 若要清空指定类型，请按照此格式操作：\n/表情管理 清空指定类型 [类别名称]"
        )
        return

    available_categories = await asyncio.to_thread(runtime._get_manageable_categories)
    if category not in available_categories:
        yield event.plain_result(
            f"⚠️ 未找到类型「{category}」。\n可先使用 /表情管理 查看图库 查看当前类型。"
        )
        return

    emoji_count = len(await asyncio.to_thread(get_emoji_by_category, category))
    if emoji_count == 0:
        yield event.plain_result(f"📭 类型「{category}」当前没有可清空的表情包。")
        return

    yield event.plain_result(
        f"⚠️ 即将清空类型「{category}」下的 {emoji_count} 个表情包，但会保留类型本身。\n"
        "请在 30 秒内回复“确认”继续执行，或回复“取消”终止本次操作。"
    )
    if not await _wait_for_command_confirmation(event):
        return

    def mutate():
        clear_result = clear_category_emojis(category)
        if clear_result.get("deleted_files"):
            runtime._invalidate_default_pack_semantics()
        return clear_result

    try:
        result = await runtime._run_default_pack_mutation("清空表情分类", mutate)
    except RuntimeError as exc:
        yield event.plain_result(f"⚠️ {exc}")
        return
    deleted_count = len(result["deleted_files"])
    yield event.plain_result(
        f"✅ 已清空类型「{category}」，共删除 {deleted_count} 个表情包。"
    )


async def clear_all_emojis_command(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """清空全部：删除所有分类下的图片，保留分类与描述配置。"""
    from ...backend.packs.images import clear_all_emojis, get_emoji_by_category

    available_categories = sorted(
        await asyncio.to_thread(runtime._get_manageable_categories)
    )
    category_counts = await asyncio.to_thread(
        lambda: {
            category: len(get_emoji_by_category(category))
            for category in available_categories
        }
    )
    total_count = sum(category_counts.values())

    if total_count == 0:
        yield event.plain_result("📭 当前没有可清空的表情包文件。")
        return

    category_count = sum(1 for count in category_counts.values() if count > 0)
    summary = _format_category_counts(category_counts)
    yield event.plain_result(
        f"⚠️ 即将清空全部表情包，共 {total_count} 个文件，涉及 {category_count} 个类型。\n"
        "该操作会保留所有类型名称和描述配置。\n"
        f"{summary}\n"
        "请在 30 秒内回复“确认”继续执行，或回复“取消”终止本次操作。"
    )
    if not await _wait_for_command_confirmation(event):
        return

    def mutate():
        clear_result = clear_all_emojis()
        if any(clear_result.get("deleted_by_category", {}).values()):
            runtime._invalidate_default_pack_semantics()
        return clear_result

    try:
        result = await runtime._run_default_pack_mutation("清空全部表情图片", mutate)
    except RuntimeError as exc:
        yield event.plain_result(f"⚠️ {exc}")
        return
    deleted_total = sum(result["deleted_by_category"].values())
    yield event.plain_result(
        f"✅ 已清空全部表情包，共删除 {deleted_total} 个文件，类型配置已保留。"
    )


async def delete_category_command(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """删除类型本身：移除描述配置与本地目录。"""
    from ...backend.packs.images import get_emoji_by_category

    category = str(args or "").strip()
    if not category:
        yield event.plain_result(
            "📌 若要删除类型本身，请按照此格式操作：\n/表情管理 删除类型本身 [类别名称]"
        )
        return

    available_categories = await asyncio.to_thread(runtime._get_manageable_categories)
    if category not in available_categories:
        yield event.plain_result(
            f"⚠️ 未找到类型「{category}」。\n可先使用 /表情管理 查看图库 查看当前类型。"
        )
        return

    emoji_count = len(await asyncio.to_thread(get_emoji_by_category, category))
    yield event.plain_result(
        f"⚠️ 即将删除类型「{category}」本身，并移除其描述配置"
        f"{f'，同时删除其中的 {emoji_count} 个表情包' if emoji_count > 0 else ''}。\n"
        "该操作不可恢复。\n"
        "请在 30 秒内回复“确认”继续执行，或回复“取消”终止本次操作。"
    )
    if not await _wait_for_command_confirmation(event):
        return

    try:
        deleted = await runtime._run_default_pack_mutation(
            "删除表情分类",
            lambda: runtime.category_manager.delete_category(category),
        )
    except RuntimeError as exc:
        yield event.plain_result(f"⚠️ {exc}")
        return
    if not deleted:
        yield event.plain_result(f"❌ 删除类型「{category}」失败，请稍后重试。")
        return

    runtime._reload_personas()
    yield event.plain_result(
        f"✅ 已删除类型「{category}」"
        f"{f'，并移除 {emoji_count} 个表情包。' if emoji_count > 0 else '。'}"
    )


# ------------------------------------------------------------ 图床同步


def _local_category_stats(memes_dir: str) -> tuple[dict[str, int], int]:
    """统计本地分类图片数量（只计支持的扩展名）。"""
    stats: dict[str, int] = {}
    total = 0
    if not os.path.isdir(memes_dir):
        return stats, total
    try:
        entries = os.listdir(memes_dir)
    except OSError:
        return stats, total
    for category in entries:
        category_path = os.path.join(memes_dir, category)
        if not os.path.isdir(category_path):
            continue
        try:
            files = [
                name
                for name in os.listdir(category_path)
                if name.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
            ]
        except OSError:
            continue
        count = len(files)
        stats[category] = count
        total += count
    return stats, total


async def check_sync_status(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """同步状态：检查表情包与图床的差异。"""
    sync_client = runtime._ensure_img_sync_for_pack()
    if not sync_client:
        yield event.plain_result(
            "图床服务尚未配置，请先在插件页面的配置中完成图床配置哦。"
        )
        return

    detail = str(args or "").strip()
    try:
        provider_name = sync_client.provider.__class__.__name__
        if hasattr(sync_client.provider, "bucket_name"):
            storage_info = f"存储桶: {sync_client.provider.bucket_name}"
        elif hasattr(sync_client.provider, "album_id"):
            storage_info = f"相册ID: {sync_client.provider.album_id}"
        else:
            storage_info = "未知存储类型"

        status = await asyncio.to_thread(sync_client.check_status)
        to_upload = status.get("to_upload", [])
        to_download = status.get("to_download", [])

        result = [
            "📊 图床同步状态报告",
            "",
            f"🔧 图床服务: {provider_name}",
            f"📁 {storage_info}",
            "",
            "📈 文件统计:",
            f"  • 需要上传: {len(to_upload)} 个文件",
            f"  • 需要下载: {len(to_download)} 个文件",
            f"  • 同名冲突或待确认: {status.get('conflict_count', 0)} 个文件",
            "",
        ]

        upload_categories: dict[str, int] = {}
        download_categories: dict[str, int] = {}
        for file in to_upload:
            cat = file.get("category", "未分类")
            upload_categories[cat] = upload_categories.get(cat, 0) + 1
        for file in to_download:
            cat = file.get("category", "未分类")
            download_categories[cat] = download_categories.get(cat, 0) + 1

        if upload_categories:
            result.append("📤 待上传文件分类:")
            for cat, count in sorted(
                upload_categories.items(), key=lambda x: x[1], reverse=True
            ):
                result.append(f"  • {cat}: {count} 个")
            result.append("")

        if download_categories:
            result.append("📥 待下载文件分类:")
            for cat, count in sorted(
                download_categories.items(), key=lambda x: x[1], reverse=True
            ):
                result.append(f"  • {cat}: {count} 个")
            result.append("")

        if to_upload:
            result.append("📤 待上传文件示例（前5个）:")
            for file in to_upload[:5]:
                result.append(f"  • {file.get('category', '未分类')}/{file['filename']}")
            if len(to_upload) > 5:
                result.append(f"  • ...还有 {len(to_upload) - 5} 个文件")
            result.append("")

        if to_download:
            result.append("📥 待下载文件示例（前5个）:")
            for file in to_download[:5]:
                result.append(f"  • {file.get('category', '未分类')}/{file['filename']}")
            if len(to_download) > 5:
                result.append(f"  • ...还有 {len(to_download) - 5} 个文件")
            result.append("")

        if status.get("is_synced"):
            result.append("✅ 云端与本地图库已经完全同步啦！")

            if detail == "详细":
                result.append("")
                result.append("📋 详细信息:")

                try:
                    if hasattr(sync_client.provider, "get_image_list"):
                        remote_images = await asyncio.to_thread(
                            sync_client.provider.get_image_list
                        )
                        remote_stats: dict[str, int] = {}
                        for img in remote_images:
                            cat = img.get("category", "未分类")
                            remote_stats[cat] = remote_stats.get(cat, 0) + 1

                        if remote_stats:
                            result.append("📂 云端文件分类详情:")
                            for cat, count in sorted(
                                remote_stats.items(), key=lambda x: x[1], reverse=True
                            ):
                                result.append(f"  • {cat}: {count} 个")
                            result.append(f"📊 云端总计: {len(remote_images)} 个文件")
                        else:
                            result.append("📂 云端无文件")
                except Exception as exc:
                    result.append(f"⚠️ 获取云端详情失败: {str(exc)}")

                local_memes_dir = str(
                    getattr(
                        sync_client,
                        "local_dir",
                        runtime._resolve_runtime_pack_context().get("memes_dir"),
                    )
                )
                local_stats, local_total = _local_category_stats(local_memes_dir)
                if local_stats:
                    result.append("📂 本地文件分类详情:")
                    for cat, count in sorted(
                        local_stats.items(), key=lambda x: x[1], reverse=True
                    ):
                        result.append(f"  • {cat}: {count} 个")
                    result.append(f"📊 本地总计: {local_total} 个文件")
                else:
                    result.append("")
                    result.append("📂 本地无文件")
        else:
            result.append("⏳ 需要同步以保持云端与本地图库一致")
            if status.get("conflicts"):
                result.append(
                    "⚠️ 同名冲突会保留，请检查后选择“覆盖到云端”或“从云端覆盖”。"
                )
                for item in status["conflicts"][:5]:
                    result.append(f"  • {item['relative_path']}")
            result.append(
                "💡 使用 '/表情管理 同步到云端' 或 '/表情管理 从云端同步' 进行同步"
            )

        sync_manager = getattr(sync_client, "sync_manager", None)
        tracker = getattr(sync_manager, "upload_tracker", None)
        if tracker is not None:
            try:
                get_uploaded = getattr(tracker, "get_uploaded_files", None)
                if callable(get_uploaded):
                    uploaded_files = get_uploaded()
                    result.append("")
                    result.append(f"📝 上传记录: 已记录 {len(uploaded_files)} 个文件")
            except Exception:
                pass

        yield event.plain_result("\n".join(result))
    except Exception as exc:
        logger.error(f"检查同步状态失败: {str(exc)}")
        yield event.plain_result(f"检查同步状态失败: {str(exc)}")


async def _invalidate_after_sync(sync_client: Any) -> None:
    """同步完成后让语义元数据失效（移植自上游 finally 分支）。"""
    from ...backend.semantic.storage import invalidate_semantic_metadata

    try:
        pack_dir = Path(sync_client.local_dir).parent
        if (pack_dir / "semantic_metadata.json").is_file():
            await asyncio.to_thread(invalidate_semantic_metadata, pack_dir)
    except Exception as exc:
        logger.warning("图片同步后无法使语义元数据失效：%s", exc)


async def sync_to_remote(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """同步到云端：把本地新增图片上传。"""
    sync_client = runtime._ensure_img_sync_for_pack()
    if not sync_client:
        yield event.plain_result(
            "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
        )
        return

    try:
        yield event.plain_result("⚡ 正在开启云端同步任务...")
        success = await sync_client.start_sync("upload")
        if success:
            yield event.plain_result("云端同步已完成！")
        else:
            yield event.plain_result("云端同步失败，请查看日志哦。")
    except Exception as exc:
        logger.error(f"同步到云端失败: {str(exc)}")
        yield event.plain_result(f"同步到云端失败: {str(exc)}")


async def sync_from_remote(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """从云端同步：拉取云端新增图片。"""
    sync_client = runtime._ensure_img_sync_for_pack()
    if not sync_client:
        yield event.plain_result(
            "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
        )
        return

    pack_id = str(
        getattr(runtime, "_img_sync_pack_id", "")
        or runtime._resolve_runtime_pack_context().get("pack_id")
    ).strip()
    try:
        runtime.semantic_task_manager.begin_external_pack_operation(
            pack_id, "从图床下载表情包"
        )
    except RuntimeError as exc:
        yield event.plain_result(f"⚠️ {exc}")
        return
    try:
        yield event.plain_result("开始从云端进行同步...")
        success = await sync_client.start_sync("download")
        if success:
            yield event.plain_result("从云端同步已完成！")
            await runtime.reload_emotions()
        else:
            yield event.plain_result("从云端同步失败，请查看日志哦。")
    except Exception as exc:
        logger.error(f"从云端同步失败: {str(exc)}")
        yield event.plain_result(f"从云端同步失败: {str(exc)}")
    finally:
        await _invalidate_after_sync(sync_client)
        runtime.semantic_task_manager.end_external_pack_operation(pack_id)


async def overwrite_to_remote(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """覆盖到云端：让云端与本地完全一致（会删除云端多余文件）。"""
    sync_client = runtime._ensure_img_sync_for_pack()
    if not sync_client:
        yield event.plain_result(
            "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
        )
        return

    try:
        yield event.plain_result(
            "⚠️ 正在执行覆盖到云端任务（将清理云端多余文件）..."
        )
        success = await sync_client.start_sync("overwrite_to_remote")
        if success:
            yield event.plain_result(
                "覆盖到云端任务已完成！云端现在与本地完全一致。"
            )
        else:
            yield event.plain_result("任务失败，请查看日志。")
    except Exception as exc:
        logger.error(f"覆盖到云端失败: {str(exc)}")
        yield event.plain_result(f"覆盖到云端失败: {str(exc)}")


async def overwrite_from_remote(
    event: UnifiedEvent, runtime: MemeRuntime, args: str
) -> AsyncGenerator[Any, None]:
    """从云端覆盖：让本地与云端完全一致（会删除本地多余文件）。"""
    sync_client = runtime._ensure_img_sync_for_pack()
    if not sync_client:
        yield event.plain_result(
            "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
        )
        return

    pack_id = str(
        getattr(runtime, "_img_sync_pack_id", "")
        or runtime._resolve_runtime_pack_context().get("pack_id")
    ).strip()
    try:
        runtime.semantic_task_manager.begin_external_pack_operation(
            pack_id, "从远端覆盖本地表情包"
        )
    except RuntimeError as exc:
        yield event.plain_result(f"⚠️ {exc}")
        return
    try:
        yield event.plain_result(
            "⚠️ 正在执行从云端覆盖任务（将清理本地多余文件）..."
        )
        success = await sync_client.start_sync("overwrite_from_remote")
        if success:
            yield event.plain_result(
                "从云端覆盖任务已完成！本地现在与云端完全一致。"
            )
        else:
            yield event.plain_result("任务失败，请查看日志。")
    except Exception as exc:
        logger.error(f"从云端覆盖失败: {str(exc)}")
        yield event.plain_result(f"从云端覆盖失败: {str(exc)}")
    finally:
        await _invalidate_after_sync(sync_client)
        runtime.semantic_task_manager.end_external_pack_operation(pack_id)


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
        "添加表情": register_command("添加表情", upload_meme, admin=True),
        "收集表情": register_command("收集表情", collect_memes, admin=True),
        "开始收集": register_command("开始收集", start_collecting_memes, admin=True),
        "结束收集": register_command("结束收集", stop_collecting_memes, admin=True),
        "恢复默认表情包": register_command(
            "恢复默认表情包", restore_default_memes_command, admin=True
        ),
        "清空指定类型": register_command("清空指定类型", clear_category_command, admin=True),
        "清空全部": register_command("清空全部", clear_all_emojis_command, admin=True),
        "删除类型本身": register_command(
            "删除类型本身", delete_category_command, admin=True
        ),
        "同步状态": register_command("同步状态", check_sync_status),
        "同步到云端": register_command("同步到云端", sync_to_remote, admin=True),
        "从云端同步": register_command("从云端同步", sync_from_remote, admin=True),
        "覆盖到云端": register_command("覆盖到云端", overwrite_to_remote, admin=True),
        "从云端覆盖": register_command("从云端覆盖", overwrite_from_remote, admin=True),
        "帮助": register_command("帮助", help_command),
    }


__all__ = [
    "register_all",
    "register_command",
    "list_emotions",
    "show_library_stats",
    "add_category_command",
    "collect_memes",
    "start_collecting_memes",
    "stop_collecting_memes",
    "upload_meme",
    "restore_default_memes_command",
    "clear_category_command",
    "clear_all_emojis_command",
    "delete_category_command",
    "check_sync_status",
    "sync_to_remote",
    "sync_from_remote",
    "overwrite_to_remote",
    "overwrite_from_remote",
    "help_command",
    "CategoryCreationCancelled",
    "ConfirmationCancelled",
    "SenderScopedSessionFilter",
    "PREFIX",
]
