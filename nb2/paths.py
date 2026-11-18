"""插件数据目录与路径解析。

AstrBot 用 ``get_astrbot_plugin_data_path()``；NoneBot2 没有等价概念，因此在
``localstore`` 可用时优先使用它，否则回退到插件包内的 ``data/`` 目录。
两者都保证目录结构稳定，业务层不感知差异。
"""

from __future__ import annotations

import os
from pathlib import Path

from .log import logger

#: 插件包根目录。
PLUGIN_DIR = Path(__file__).resolve().parent.parent
#: 插件名（数据目录名与配置文件名）。
PLUGIN_NAME = "meme_manager"


def _localstore_dir() -> Path | None:
    """优先使用 nonebot-plugin-localstore 的插件数据目录。

    这里用 ``find_spec`` 探测而不是 ``nonebot.require``：后者在非 NoneBot2
    运行环境（例如纯后端单测）会打印加载失败的噪音日志。
    """
    try:
        import importlib.util

        if importlib.util.find_spec("nonebot_plugin_localstore") is None:
            return None
        from nonebot_plugin_localstore import get_plugin_data_dir  # type: ignore

        return Path(get_plugin_data_dir())
    except Exception:
        return None


def resolve_data_dir(plugin_name: str = PLUGIN_NAME) -> Path:
    """返回插件数据目录，必要时创建。

    解析优先级：环境变量覆盖 → localstore → 运行目录 ``data/<插件名>``。
    最后一项在 NoneBot2 机器人工程里即「工程根/data/插件名」，可写且稳定。
    """
    env_override = os.environ.get("MEME_MANAGER_DATA_DIR")
    if env_override:
        path = Path(env_override).expanduser().resolve()
    else:
        localstore = _localstore_dir()
        if localstore is not None:
            path = Path(localstore) / plugin_name
        else:
            path = (Path.cwd() / "data" / plugin_name).resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.error("创建插件数据目录失败：{} ({})", path, exc)
    return path


def resolve_config_path(plugin_name: str = PLUGIN_NAME) -> Path:
    """返回配置文件路径。"""
    return resolve_data_dir(plugin_name) / "config.json"


def resolve_schema_path() -> Path:
    """返回插件自带的 ``_conf_schema.json`` 路径。"""
    return PLUGIN_DIR / "_conf_schema.json"


__all__ = [
    "PLUGIN_DIR",
    "PLUGIN_NAME",
    "resolve_data_dir",
    "resolve_config_path",
    "resolve_schema_path",
]
