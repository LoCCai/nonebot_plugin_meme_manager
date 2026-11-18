"""AI 表情工具：``search_memes`` / ``send_meme`` / ``preview``。

AstrBot 用 ``@llm_tool`` 装饰器把方法暴露给宿主 Agent；NoneBot2 没有宿主 Agent，
因此这里把同样的能力抽成**协议无关的工具注册表**：

* 工具定义：名称、描述、JSON Schema、处理函数；
* 处理函数接收 ``UnifiedEvent`` + 参数，返回结构化结果（候选列表 / 发送结果）；
* 可选的 LLM 集成：若机器人工程安装了常见 LLM 插件，可由集成层把它们注册进去；
  未安装时，命令/规则路径仍可独立调用（例如关键词触发配图）。

工具语义与上游一致：搜索结果缓存到事件 extras，``send_meme`` 校验候选后才发送，
避免模型伪造图片 ID。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .log import logger
from .platform.event import UnifiedEvent
from .platform.message import Segment, UnifiedMessage

#: 事件 extras 中缓存候选的键（与上游保持一致）。
CANDIDATE_EXTRA_KEY = "meme_manager_semantic_selected_ids"
CANDIDATES_CACHE_KEY = "nb2_meme_candidates"
SEARCH_USED_KEY = "nb2_meme_search_used"

MAX_TOP_K = 9


@dataclass(slots=True)
class ToolResult:
    """工具调用结果。"""

    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        if self.data:
            return json.dumps({"ok": self.ok, "message": self.message, **self.data}, ensure_ascii=False)
        return self.message or ("ok" if self.ok else "failed")


@dataclass(slots=True)
class MemeTool:
    """一个可被 LLM 或命令调用的工具。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[UnifiedEvent, Any, dict[str, Any]], Awaitable[ToolResult]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def call(self, event: UnifiedEvent, runtime: Any, arguments: dict[str, Any]) -> ToolResult:
        try:
            return await self.handler(event, runtime, dict(arguments or {}))
        except Exception as exc:  # pragma: no cover - 工具异常兜底
            logger.error("工具 {} 执行失败：{}", self.name, exc, exc_info=True)
            return ToolResult(ok=False, message=f"工具执行失败：{exc}")


class ToolRegistry:
    """工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, MemeTool] = {}

    def register(self, tool: MemeTool) -> MemeTool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> MemeTool | None:
        return self._tools.get(str(name or ""))

    def all(self) -> list[MemeTool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)


REGISTRY = ToolRegistry()


async def _resolve_search_context(event: UnifiedEvent, runtime: Any) -> dict[str, Any]:
    """解析当前会话绑定的表情包上下文。"""
    import asyncio

    return await asyncio.to_thread(runtime._resolve_runtime_pack_context, event)


def _is_semantic_ready(runtime: Any, event: UnifiedEvent) -> bool:
    try:
        return bool(runtime._semantic_pack_ready(event=event, for_direct_search=True))
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("语义索引检查失败：{}", exc)
        return False


async def _search_memes(event: UnifiedEvent, runtime: Any, args: dict[str, Any]) -> ToolResult:
    """语义检索候选表情。"""
    from ..backend.semantic.query import dumps_result, remember_candidates, search_memes_multi_pack

    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, message="query 不能为空")
    if event.get_extra(SEARCH_USED_KEY):
        return ToolResult(ok=False, message="本轮已搜索过表情，请直接从已有候选中选择。")
    if not _is_semantic_ready(runtime, event):
        return ToolResult(ok=False, message="当前表情包尚未建立可用的语义索引。")

    top_k = args.get("top_k")
    try:
        top_k = max(1, min(MAX_TOP_K, int(top_k))) if top_k is not None else runtime.semantic_top_k
    except (TypeError, ValueError):
        top_k = runtime.semantic_top_k

    pack_context = await _resolve_search_context(event, runtime)
    pack_contexts = pack_context.get("pack_contexts") or [
        {
            "pack_id": pack_context.get("pack_id"),
            "pack_dir": pack_context.get("pack_dir"),
        }
    ]
    result = await search_memes_multi_pack(
        [ctx for ctx in pack_contexts if ctx.get("pack_id") and ctx.get("pack_dir")],
        runtime.data_dir,
        query,
        runtime._resolve_embedding_provider,
        top_k=top_k,
        min_score=runtime.semantic_min_score,
    )
    candidates = result.get("candidates") or []
    remember_candidates(event, candidates)
    event.set_extra(CANDIDATES_CACHE_KEY, candidates)
    event.set_extra(SEARCH_USED_KEY, True)
    return ToolResult(
        ok=True,
        message=dumps_result(result),
        data={"candidates": candidates, "reason": result.get("reason", "")},
    )


async def _send_meme(event: UnifiedEvent, runtime: Any, args: dict[str, Any]) -> ToolResult:
    """发送候选中的表情（校验 ID 后发送）。"""
    from ..backend.semantic.query import validate_selected_ids

    candidates = event.get_extra(CANDIDATES_CACHE_KEY) or event.get_extra(CANDIDATE_EXTRA_KEY) or []
    if not candidates:
        return ToolResult(ok=False, message="本轮没有可发送的候选表情，请先调用 search_memes。")

    ids = args.get("ids") or args.get("id")
    if isinstance(ids, str):
        ids = [ids]
    ids = [str(item) for item in (ids or []) if str(item).strip()]
    if not ids:
        return ToolResult(ok=False, message="ids 不能为空")

    pack_context = await _resolve_search_context(event, runtime)
    resolved = validate_selected_ids(
        event,
        ids,
        {
            "pack_dir": pack_context.get("pack_dir"),
            "pack_contexts": pack_context.get("pack_contexts") or [],
        },
    )
    if not resolved:
        return ToolResult(ok=False, message="给出的 ID 不在本轮候选中，或无法唯一定位。")

    items = list(resolved.items())
    limit = int(runtime.max_memes_per_message)
    if limit > 0:
        items = items[:limit]

    sent = 0
    for _raw_id, (entry_id, path) in items:
        if not path or not path.is_file():
            logger.warning("候选表情文件不存在：{}", path)
            continue
        await event.send(UnifiedMessage([Segment.image(path=str(path))]))
        sent += 1
        event.set_extra(CANDIDATE_EXTRA_KEY, {"id": entry_id})
    if not sent:
        return ToolResult(ok=False, message="没有成功发送任何表情（文件可能已被删除）。")
    return ToolResult(ok=True, message=f"已发送 {sent} 张表情。", data={"sent": sent})


def _candidate_path(item: dict[str, Any], runtime: Any, event: UnifiedEvent):
    """把候选记录解析为本地图片路径（供预览等场景使用）。"""
    from pathlib import Path

    from ..backend.packs.resolver import get_pack_paths

    pack_id = str(item.get("_source_pack_id") or "").strip()
    category = str(item.get("category") or "").strip()
    filename = str(item.get("filename") or item.get("file") or "").strip()
    if not filename:
        return None
    if pack_id and category:
        try:
            memes_dir = Path(get_pack_paths(pack_id)["memes_dir"])
            candidate = memes_dir / category / filename
            if candidate.is_file():
                return candidate
        except Exception:  # pragma: no cover - 防御性
            pass
    try:
        memes_dir = Path(runtime._resolve_runtime_pack_context(event=event)["memes_dir"])
    except Exception:  # pragma: no cover - 防御性
        return None
    candidate = memes_dir / category / filename
    return candidate if candidate.is_file() else None


async def _preview_memes(event: UnifiedEvent, runtime: Any, args: dict[str, Any]) -> ToolResult:
    """生成候选预览（文字版：ID + 分类 + 描述）。"""
    candidates = event.get_extra(CANDIDATES_CACHE_KEY) or []
    if not candidates:
        return ToolResult(ok=False, message="当前没有候选，请先调用 search_memes。")
    lines = []
    for item in candidates:
        lines.append(
            f"- {item.get('id')}｜{item.get('category', '')}｜"
            f"{str(item.get('caption') or item.get('description') or '')[:60]}"
        )
    return ToolResult(ok=True, message="\n".join(lines), data={"count": len(candidates)})


def build_default_registry(runtime: Any = None) -> ToolRegistry:
    """构造默认工具集合（模块导入即注册）。"""
    if not REGISTRY.all():
        REGISTRY.register(
            MemeTool(
                name="search_memes",
                description=(
                    "查找符合助手表达意图的表情图片。使用简短的自然语言描述想表达的情绪、"
                    "态度或动作，并说明其指代对象。每条回复最多搜索一次，返回的候选可供本轮选择。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "想表达的含义，例如庆祝某人取得成就，或带着玩笑意味表达失望",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "返回候选数量（1-9，默认取插件配置）",
                            "minimum": 1,
                            "maximum": MAX_TOP_K,
                        },
                    },
                    "required": ["query"],
                },
                handler=_search_memes,
            )
        )
        REGISTRY.register(
            MemeTool(
                name="send_meme",
                description=(
                    "发送本轮 search_memes 候选中选定的表情图片。ids 必须是候选中的 ID；"
                    "未搜索过或 ID 不在候选中会被拒绝。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "候选表情 ID 列表",
                        }
                    },
                    "required": ["ids"],
                },
                handler=_send_meme,
            )
        )
        REGISTRY.register(
            MemeTool(
                name="preview_memes",
                description="以文字形式预览本轮候选表情（ID 与描述），便于选择。",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=_preview_memes,
            )
        )
    return REGISTRY


__all__ = [
    "MemeTool",
    "ToolResult",
    "ToolRegistry",
    "REGISTRY",
    "build_default_registry",
    "CANDIDATES_CACHE_KEY",
    "SEARCH_USED_KEY",
    "MAX_TOP_K",
]
