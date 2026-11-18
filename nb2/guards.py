"""运行时上的业务便捷方法（移植自 WebAPI/命令 mixin 的公共逻辑）。

这些方法原本散落在 ``mixins/web_api.py`` 与 ``mixins/commands.py`` 的同名 helper 里，
NoneBot2 线路统一收敛到运行时单例，避免每个 matcher / 路由重复实现。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from .log import logger


async def _run_guarded_pack_file_operation(
    runtime: Any,
    pack_id: str,
    operation: str,
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """在目标表情包的语义锁内执行文件操作（移植自上游）。"""
    manager = getattr(runtime, "semantic_task_manager", None)
    locked = False
    if manager is not None and pack_id:
        manager.begin_external_pack_operation(pack_id, operation)
        locked = True
    kwargs["operation_guard"] = (
        None if locked else (lambda pid, op: _semantic_operation_guard(runtime, pid, op))
    )
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # 磁盘操作不会随取消自动停止，必须等它收尾后再释放锁。
        try:
            await asyncio.shield(worker)
        except Exception:
            pass
        raise
    finally:
        if locked:
            manager.end_external_pack_operation(pack_id)


async def _run_guarded_runtime_file_operation(
    runtime: Any,
    operation: str,
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """在全部已安装表情包的锁内执行全局文件操作。"""
    if getattr(runtime, "_runtime_file_lock", None) is None:
        runtime._runtime_file_lock = asyncio.Lock()
    async with runtime._runtime_file_lock:
        manager = getattr(runtime, "semantic_task_manager", None)
        packs_dir = Path(getattr(runtime, "packs_dir", ""))
        pack_ids = (
            sorted(path.name for path in packs_dir.iterdir() if path.is_dir())
            if packs_dir.is_dir()
            else []
        )
        locked_pack_ids: list[str] = []
        if manager is not None:
            try:
                for pack_id in pack_ids:
                    manager.begin_external_pack_operation(pack_id, operation)
                    locked_pack_ids.append(pack_id)
            except Exception:
                for pack_id in reversed(locked_pack_ids):
                    manager.end_external_pack_operation(pack_id)
                raise

        kwargs["operation_guard"] = (
            None if manager is not None else (lambda pid, op: _semantic_operation_guard(runtime, pid, op))
        )
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(worker)
            except Exception:
                pass
            raise
        finally:
            for pack_id in reversed(locked_pack_ids):
                manager.end_external_pack_operation(pack_id)


def _semantic_operation_guard(runtime: Any, pack_id: str, operation: str) -> None:
    """在变更前校验语义任务是否允许写入该表情包。"""
    manager = getattr(runtime, "semantic_task_manager", None)
    if manager is not None:
        manager.assert_pack_mutation_allowed(pack_id, operation)


async def _run_pack_mutation(runtime: Any, pack_id: str, operation: str, mutation: Callable[[], Any]) -> Any:
    """在目标表情包的语义锁内执行同步变更。"""
    explicit_pack_id = str(pack_id or "").strip()
    locked_pack_id = explicit_pack_id or str(
        runtime._resolve_runtime_pack_context().get("pack_id") or ""
    ).strip()
    manager = getattr(runtime, "semantic_task_manager", None)
    if manager is None or not locked_pack_id:
        return mutation()

    def guarded_mutation() -> Any:
        if not explicit_pack_id:
            current = str(
                runtime._resolve_runtime_pack_context().get("pack_id") or ""
            ).strip()
            if current != locked_pack_id:
                raise RuntimeError("默认资源包已切换，请重新执行当前操作")
        return mutation()

    return await manager.run_locked_pack_mutation(
        locked_pack_id, operation, guarded_mutation
    )


async def _run_default_pack_mutation(runtime: Any, operation: str, mutation: Callable[[], Any]) -> Any:
    """默认表情包上的变更（供命令与 WebUI 复用）。"""
    return await _run_pack_mutation(runtime, "", operation, mutation)


def _invalidate_pack_semantics(runtime: Any, pack_id: str | None = None) -> None:
    """图片变更后让语义元数据失效。"""
    from ..backend.packs.resolver import resolve_pack_paths
    from ..backend.semantic.storage import invalidate_semantic_metadata

    if pack_id:
        try:
            pack_dir = Path(resolve_pack_paths(pack_id)["pack_dir"]).resolve()
        except (ValueError, FileNotFoundError):
            return
    else:
        pack_dir = Path(runtime._resolve_runtime_pack_context()["pack_dir"]).resolve()
    if not (pack_dir / "semantic_metadata.json").is_file():
        return
    try:
        invalidate_semantic_metadata(pack_dir)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.error("图片变更后刷新语义元数据失败：{}", exc, exc_info=True)


def install(runtime_cls: type) -> None:
    """把上述方法挂到 :class:`MemeRuntime` 上（保持调用形状与上游一致）。"""
    runtime_cls._run_guarded_pack_file_operation = (  # type: ignore[attr-defined]
        lambda self, pack_id, operation, function, *a, **kw: _run_guarded_pack_file_operation(
            self, pack_id, operation, function, *a, **kw
        )
    )
    runtime_cls._run_guarded_runtime_file_operation = (  # type: ignore[attr-defined]
        lambda self, operation, function, *a, **kw: _run_guarded_runtime_file_operation(
            self, operation, function, *a, **kw
        )
    )
    runtime_cls._semantic_operation_guard = (  # type: ignore[attr-defined]
        lambda self, pack_id, operation: _semantic_operation_guard(self, pack_id, operation)
    )
    runtime_cls._run_pack_mutation = (  # type: ignore[attr-defined]
        lambda self, pack_id, operation, mutation: _run_pack_mutation(
            self, pack_id, operation, mutation
        )
    )
    runtime_cls._run_default_pack_mutation = (  # type: ignore[attr-defined]
        lambda self, operation, mutation: _run_default_pack_mutation(
            self, operation, mutation
        )
    )
    runtime_cls._invalidate_pack_semantics = (  # type: ignore[attr-defined]
        lambda self, pack_id=None: _invalidate_pack_semantics(self, pack_id)
    )
    runtime_cls._invalidate_default_pack_semantics = (  # type: ignore[attr-defined]
        lambda self: _invalidate_pack_semantics(self, "")
    )


__all__ = [
    "install",
    "_run_guarded_pack_file_operation",
    "_run_guarded_runtime_file_operation",
    "_run_pack_mutation",
    "_run_default_pack_mutation",
    "_invalidate_pack_semantics",
]
