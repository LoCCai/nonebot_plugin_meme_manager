"""运行时内部路径与上游常量的边界。

上游业务层从 ``<pkg>.config`` 导入 ``PACKS_DIR`` / ``PLUGIN_DATA_DIR`` 等模块级常量，
这些常量在导入时就绑定到 AstrBot 数据目录。NoneBot2 线路用 :class:`MemeRuntime`
持有的目录，因此这里在导入插件时**重定向**这些常量，保证业务层与 WebUI 使用同一
套路径，无需改写业务代码。
"""

from __future__ import annotations

from pathlib import Path

from .log import logger

#: 需要与运行时数据目录对齐的模块级常量。
CONSTANTS = (
    "PLUGIN_DATA_DIR",
    "PACKS_DIR",
    "REGISTRY_PATH",
    "SELECTION_RULES_PATH",
    "COMMUNITY_CACHE_PATH",
    "BACKUP_DIR",
    "TEMP_DIR",
    "SEMANTIC_INDEXES_DIR",
)


def bind_module_paths(data_dir: Path) -> bool:
    """把 ``<pkg>.config`` 的路径常量指向给定数据目录。

    Args:
        data_dir: 运行时数据目录（``data/meme_manager``）。

    Returns:
        是否完成绑定（导入失败时返回 ``False``）。
    """
    package = __package__.rsplit(".", 1)[0]
    module_name = f"{package}.config"
    try:
        import importlib

        module = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - 配置模块异常
        logger.error("无法导入 {}：{}", module_name, exc)
        return False

    data_dir = Path(data_dir).resolve()
    values = {
        "PLUGIN_DATA_DIR": data_dir,
        "PACKS_DIR": data_dir / "packs",
        "REGISTRY_PATH": data_dir / "registry.json",
        "SELECTION_RULES_PATH": data_dir / "selection_rules.json",
        "COMMUNITY_CACHE_PATH": data_dir / "community_cache.json",
        "BACKUP_DIR": data_dir / "backup",
        "TEMP_DIR": data_dir / "temp",
        "SEMANTIC_INDEXES_DIR": data_dir / "semantic_indexes",
    }
    for name, value in values.items():
        if hasattr(module, name):
            setattr(module, name, value)
    return True


__all__ = ["bind_module_paths", "CONSTANTS"]
