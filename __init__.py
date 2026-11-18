"""NoneBot2 插件入口。

与原 AstrBot 入口（``main.py`` 的 ``MemeSender``）的差异：

* 用 ``__plugin_meta__`` 声明插件元数据；
* 用 matcher 注册命令与消息管线，而不是 ``filter`` 装饰器；
* 业务逻辑仍由 :class:`nb2.runtime.MemeRuntime` 承载（与上游一致）。

支持的协议：OneBot v11、OneBot v12、QQ 官方机器人。

matcher 的注册被推迟到驱动初始化之后（``register_matchers``），既符合 NoneBot2
的 ``get_driver().config`` 时序要求，也让纯后端测试可以在没有驱动的环境下导入
本插件包。
"""

from __future__ import annotations

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from .nb2 import bootstrap

# 业务层沿用 ``from astrbot.api import logger``，必须在导入业务模块前就位。
bootstrap.ensure()

from .nb2.log import logger  # noqa: E402
from .nb2.runtime import MemeRuntime  # noqa: E402
from .nb2.state import (  # noqa: E402
    get_runtime,
    get_runtime_or_none,
    reset_runtime,
    set_runtime,
)

__plugin_meta__ = PluginMetadata(
    name="表情包管理器",
    description=(
        "表情包仓库、资源广场、语义检索、自动收集与图床同步。"
        "支持 OneBot v11 / OneBot v12 / QQ 官方机器人。"
    ),
    usage=(
        "发送 /表情管理 查看图库 查看分类；\n"
        "/表情管理 添加分类 [名称] [描述] 创建分类；\n"
        "/表情管理 添加表情 [分类] 后发送图片；\n"
        "/表情管理 收集表情 收集引用图片；\n"
        "/表情管理 开始收集 / 结束收集 连续收集；\n"
        "/表情管理 图库统计 查看统计。\n"
        "WebUI：启动后访问 /meme_manager/ 。"
    ),
    type="application",
    homepage="https://github.com/LoCCai/nonebot_plugin_meme_manager",
    supported_adapters={"~onebot.v11", "~onebot.v12", "~qq"},
    extra={
        "author": "anka",
        "version": "5.4.1-nb2.1",
        "upstream": "https://github.com/LoCCai/nonebot_plugin_meme_manager",
    },
)

#: matcher 注册结果（首次调用 ``register_matchers`` 后填充）。
_matchers_registered = False


def _driver_or_none():
    """返回驱动；未初始化时返回 ``None``（便于纯后端测试导入本包）。"""
    try:
        return get_driver()
    except Exception:
        return None


def register_matchers() -> dict[str, list]:
    """注册全部 matcher（幂等）。

    Returns:
        ``{"commands": [...], "pipeline": [...]}``。
    """
    global _matchers_registered
    if _matchers_registered:
        return {"commands": [], "pipeline": []}
    from .nb2.matchers import register_all

    result = register_all()
    _matchers_registered = True
    return result


async def mount_webui(runtime: MemeRuntime) -> None:
    """挂载 WebUI（FastAPI 路由）。"""
    from .nb2.web import mount_webui as _mount

    _mount(runtime)


def _setup_driver_hooks() -> None:
    """注册驱动生命周期钩子（仅在驱动可用时）。"""
    driver = _driver_or_none()
    if driver is None:
        return

    @driver.on_startup
    async def _on_startup() -> None:
        """驱动启动后初始化运行时、matcher、WebUI 与可选集成。"""
        register_matchers()
        runtime = get_runtime()
        try:
            await mount_webui(runtime)
        except Exception as exc:  # pragma: no cover - WebUI 为可选能力
            logger.warning("挂载 WebUI 失败（不影响机器人指令）：{}", exc)
        # 可选集成：安装了 MoEllmChats 时把表情工具注册给它；未安装则静默跳过。
        try:
            from .nb2.integrations import moellm

            moellm.register()
        except Exception as exc:  # pragma: no cover - 防御性
            logger.debug("MoEllmChats 集成未启用：{}", exc)
        try:
            await runtime.startup()
        except Exception as exc:  # pragma: no cover - 防御性
            logger.error("插件启动失败：{}", exc)

    @driver.on_shutdown
    async def _on_shutdown() -> None:
        """驱动关闭时释放后台任务。"""
        try:
            from .nb2.integrations import moellm

            moellm.unregister()
        except Exception:  # pragma: no cover - 防御性
            pass
        runtime = get_runtime_or_none()
        if runtime is not None:
            try:
                await runtime.shutdown()
            except Exception as exc:  # pragma: no cover - 防御性
                logger.debug("插件关闭清理失败：{}", exc)
            reset_runtime()


_setup_driver_hooks()

__all__ = [
    "get_runtime",
    "get_runtime_or_none",
    "set_runtime",
    "reset_runtime",
    "register_matchers",
    "mount_webui",
    "MemeRuntime",
    "__plugin_meta__",
]
