"""运行时内部路径与上游常量的边界。

上游业务模块使用 ``from ...config import PACKS_DIR`` 这类**导入期取值**的写法，
一旦 ``<pkg>.config`` 的常量改变，已导入模块里的副本不会跟着变。NoneBot2 线路
用 :class:`MemeRuntime` 持有数据目录，因此这里在运行时构造时：

1. 更新 ``<pkg>.config`` 的模块级常量；
2. 同步已导入业务模块中同名常量的副本。

这样业务层、WebUI 与运行时始终指向同一个数据目录，且无需改写业务代码。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .log import logger

#: 需要与运行时数据目录对齐的常量名。
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

#: 已知会以 ``from ...config import X`` 形式复制常量的业务模块。
BUSINESS_MODULES = (
    "backend.packs.resolver",
    "backend.packs.storage",
    "backend.packs.images",
    "backend.packs.categories",
    "backend.packs.updates",
    "backend.packs.transfer",
    "backend.auto_collect",
    "backend.semantic.storage",
    "backend.semantic.index",
    "image_host.img_sync",
)


def build_path_values(data_dir: Path) -> dict[str, Path]:
    """按数据目录推导全部路径常量。"""
    data_dir = Path(data_dir).resolve()
    return {
        "PLUGIN_DATA_DIR": data_dir,
        "PACKS_DIR": data_dir / "packs",
        "REGISTRY_PATH": data_dir / "registry.json",
        "SELECTION_RULES_PATH": data_dir / "selection_rules.json",
        "COMMUNITY_CACHE_PATH": data_dir / "community_cache.json",
        "BACKUP_DIR": data_dir / "backup",
        "TEMP_DIR": data_dir / "temp",
        "SEMANTIC_INDEXES_DIR": data_dir / "semantic_indexes",
    }


def bind_module_paths(data_dir: Path) -> bool:
    """把路径常量指向给定数据目录（含已导入业务模块中的副本）。

    Args:
        data_dir: 运行时数据目录（``data/meme_manager``）。

    Returns:
        是否完成绑定（配置模块导入失败时返回 ``False``）。
    """
    package = __package__.rsplit(".", 1)[0]
    module_name = f"{package}.config"
    try:
        import importlib

        module = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - 配置模块异常
        logger.error("无法导入 {}：{}", module_name, exc)
        return False

    values = build_path_values(data_dir)
    for name, value in values.items():
        if hasattr(module, name):
            setattr(module, name, value)

    # 同步已导入的业务模块副本（仅当属性确实存在时才覆盖）。
    for suffix in BUSINESS_MODULES:
        target = sys.modules.get(f"{package}.{suffix}")
        if target is None:
            continue
        for name, value in values.items():
            if name in vars(target):
                setattr(target, name, value)
    return True


__all__ = ["bind_module_paths", "build_path_values", "CONSTANTS", "BUSINESS_MODULES"]
