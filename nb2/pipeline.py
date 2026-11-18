"""主动配图管线：把「LLM 回复后挑图」迁移为 NoneBot2 的独立决策流程。

AstrBot 的实现依赖宿主 Agent（``on_llm_response`` → ``on_decorating_result``）。
NoneBot2 没有宿主模型，因此在 ``nb2`` 中把同一套业务能力编排成显式管线：

1. 入站消息命中「被动配图」判定（概率门 + 范围门）；
2. 用配置的 LLM 提供方生成一条回复（可选；未配置则跳过生成）；
3. 语义检索候选 → ``backend.selection.select_candidates`` 选拔；
4. 发送文本与表情。

这条管线既可被 ``/表情管理 配图`` 命令触发，也可被可选的 LLM 插件集成层调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .llm import LLMContext
from .log import logger
from .platform.event import UnifiedEvent
from .platform.message import Segment, UnifiedMessage
from ..utils import probability_hit

#: 单条消息默认最多配图数（与上游 ``max_memes_per_message`` 语义一致）。
DEFAULT_MAX_MEMES = 1


@dataclass(slots=True)
class PipelinesResult:
    """一次配图流程的结果。"""

    status: str
    text: str = ""
    images: list[str] = None  # type: ignore[assignment]
    reason: str = ""

    def __post_init__(self) -> None:
        if self.images is None:
            self.images = []


def probability_gate(runtime: Any, roll: int | None = None) -> bool:
    """按插件配置判定本轮是否配图。"""
    return probability_hit(getattr(runtime, "emotions_probability", 100), roll)


def scope_allows(runtime: Any, event: UnifiedEvent) -> bool:
    """判断会话是否在配图触发范围内。

    上游 ``trigger_scope``：``only_chat_llm`` 表示仅普通聊天，``all_llm`` 表示
    包含插件触发的 LLM。NoneBot2 下没有「插件触发」概念，因此两种取值都允许。
    """
    return True


async def generate_reply(runtime: Any, event: UnifiedEvent, prompt: str) -> str:
    """用配置的聊天提供方生成回复文本（无提供方时返回空串）。"""
    context = getattr(runtime, "context", None)
    if not isinstance(context, LLMContext):
        return ""
    provider_id = str(getattr(runtime, "emotion_llm_provider_id", "") or "").strip()
    if not provider_id:
        providers = context.provider_manager.all()
        provider_id = providers[0].id if providers else ""
    if not provider_id or context.get_provider_by_id(provider_id) is None:
        return ""

    system_prompt = (
        "你是一个聊天助手。请根据用户消息自然回复，语气随和、简短，"
        "不要提及表情包或图片。"
    )
    try:
        response = await context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.8,
            max_tokens=300,
        )
    except Exception as exc:  # pragma: no cover - 网络异常
        logger.warning("生成回复失败：{}", exc)
        return ""
    return str(getattr(response, "completion_text", "") or "").strip()


async def search_candidates(runtime: Any, event: UnifiedEvent, query: str, top_k: int = 3) -> list[dict]:
    """检索候选（语义索引未就绪时返回空列表）。"""
    from ..backend.semantic.query import candidate_records_multi_pack, search_memes_multi_pack

    if not runtime._semantic_pack_ready(event=event, for_direct_search=True):
        return []
    pack_context = runtime._resolve_runtime_pack_context(event=event)
    contexts = pack_context.get("pack_contexts") or [
        {"pack_id": pack_context.get("pack_id"), "pack_dir": pack_context.get("pack_dir")}
    ]
    contexts = [ctx for ctx in contexts if ctx.get("pack_id") and ctx.get("pack_dir")]
    result = await search_memes_multi_pack(
        contexts,
        runtime.data_dir,
        query,
        runtime._resolve_embedding_provider,
        top_k=top_k,
        min_score=runtime.semantic_min_score,
    )
    candidates = list(result.get("candidates") or [])
    if not candidates:
        return []
    # 补齐 entry_id 等字段，供后续按 ID 解析本地文件。
    return candidate_records_multi_pack(contexts, candidates)


async def select_images(
    runtime: Any,
    event: UnifiedEvent,
    *,
    query: str,
    candidates: list[dict],
    kind: str = "semantic",
) -> list[dict]:
    """用配置的后端（chat / decision）从候选中选拔。"""
    if not candidates:
        return []
    from ..backend.selection import SelectionTask, select_candidates

    async def generate(prompt: str) -> Any:
        """调用聊天模型；返回响应对象本身（``selection`` 需要 ``completion_text``）。"""
        context = getattr(runtime, "context", None)
        if not isinstance(context, LLMContext):
            return None
        provider_id = str(getattr(runtime, "emotion_llm_provider_id", "") or "").strip()
        if not provider_id:
            providers = context.provider_manager.all()
            provider_id = providers[0].id if providers else ""
        if not provider_id:
            return None
        return await context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )

    task = SelectionTask(
        kind=kind,
        reply=query,
        candidates=candidates,
        reference=getattr(runtime, "category_mapping_string", ""),
        quantity_prompt=runtime._build_meme_quantity_prompt(),
    )
    result = await select_candidates(
        task,
        backend=str(getattr(runtime, "selection_backend", "chat") or "chat"),
        generate=generate,
        decision_client=getattr(runtime, "decision_client", None),
    )
    if result.status != "selected":
        logger.info("配图选拔未选中：{}（{}）", result.status, result.reason)
        return []
    by_id = {str(item.get("id")): item for item in candidates if item.get("id")}
    return [by_id[item] for item in result.selected_ids if item in by_id]


def resolve_image_path(runtime: Any, event: UnifiedEvent, candidate: dict) -> str | None:
    """把候选转换为本地文件路径。

    ``search_index`` 的候选只带 ``entry_id`` / ``category``，没有文件名；这里按
    ``entry_id`` 反查语义元数据得到 ``relative_path``（与上游 ``validate_selected_id``
    的做法一致）。
    """
    from pathlib import Path

    from ..backend.packs.resolver import get_pack_paths
    from ..backend.semantic.storage import load_metadata

    category = str(candidate.get("category") or "").strip()
    entry_id = str(candidate.get("entry_id") or "").strip()
    filename = str(candidate.get("filename") or "").strip()
    pack_id = str(candidate.get("_source_pack_id") or "").strip()

    roots: list[tuple[str, Path]] = []
    if pack_id:
        try:
            roots.append((pack_id, Path(get_pack_paths(pack_id)["memes_dir"])))
        except Exception:  # pragma: no cover - 防御性
            pass
    try:
        context = runtime._resolve_runtime_pack_context(event=event)
        roots.append((str(context.get("pack_id") or ""), Path(context["memes_dir"])))
    except Exception:  # pragma: no cover - 防御性
        pass

    # 语义元数据里记录了 relative_path，比按 category+filename 猜测更可靠。
    for root_pack_id, memes_dir in roots:
        if not entry_id:
            break
        try:
            pack_dir = memes_dir.parent
            metadata = load_metadata(pack_dir)
            item = (metadata.get("images") or {}).get(entry_id) or {}
            relative = str(item.get("relative_path") or "").strip()
            if relative:
                path = (pack_dir / relative).resolve()
                path.relative_to(pack_dir.resolve())
                if path.is_file():
                    return str(path)
        except Exception:  # pragma: no cover - 防御性
            continue

    if category and filename:
        for _pack_id, memes_dir in roots:
            path = memes_dir / category / filename
            if path.is_file():
                return str(path)
    return None


async def run_pipeline(
    runtime: Any,
    event: UnifiedEvent,
    *,
    user_text: str = "",
    force: bool = False,
) -> PipelinesResult:
    """执行一次完整配图流程。

    Args:
        runtime: 插件运行时。
        event: 统一事件（用于读取会话上下文与发送）。
        user_text: 用户消息文本；为空时回退到事件消息。
        force: 跳过概率门与范围门（命令触发时使用）。

    Returns:
        结果（状态、回复文本、已发送图片路径）。
    """
    text = (user_text or event.message_str or "").strip()
    if not text:
        return PipelinesResult(status="skipped", reason="empty_message")
    if not force:
        if not scope_allows(runtime, event):
            return PipelinesResult(status="skipped", reason="scope")
        if not probability_gate(runtime):
            return PipelinesResult(status="skipped", reason="probability")

    reply = await generate_reply(runtime, event, text)
    query = reply or text
    candidates = await search_candidates(runtime, event, query, top_k=max(3, runtime.semantic_top_k))
    selected = await select_images(runtime, event, query=query, candidates=candidates)
    limit = int(getattr(runtime, "max_memes_per_message", -1) or -1)
    if limit > 0:
        selected = selected[:limit]

    sent_paths: list[str] = []
    for candidate in selected:
        path = resolve_image_path(runtime, event, candidate)
        if not path:
            continue
        message = UnifiedMessage([Segment.image(path=path)])
        await event.send(message)
        sent_paths.append(path)

    if reply:
        await event.send(UnifiedMessage([Segment.text(reply)]))

    return PipelinesResult(
        status="sent" if sent_paths or reply else "abstained",
        text=reply,
        images=sent_paths,
    )


__all__ = [
    "PipelinesResult",
    "run_pipeline",
    "generate_reply",
    "search_candidates",
    "select_images",
    "resolve_image_path",
    "probability_gate",
    "scope_allows",
]
