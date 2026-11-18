"""NoneBot2 matcher 集合。

导入本模块**不会**注册 matcher（注册需要已初始化的驱动）；由插件入口在
驱动启动阶段调用 :func:`register_all`。
"""

from __future__ import annotations

from typing import Any

from . import commands, pipeline, tools_cmds


def register_all() -> dict[str, Any]:
    """注册命令、AI 工具命令与管线 matcher。

    Returns:
        ``{"commands": {子命令: matcher}, "pipeline": [matcher]}``。
    """
    command_map = dict(commands.register_all())
    command_map.update(tools_cmds.register_all())
    return {
        "commands": command_map,
        "pipeline": pipeline.register(),
    }


__all__ = ["commands", "pipeline", "tools_cmds", "register_all"]
