"""把上游 WebUI 处理器移植到 FastAPI 的适配器。

上游 ``mixins/web_api.py`` 的 87 个处理器全部依赖 Quart 的模块级 ``request`` /
``jsonify`` / ``send_file``，并返回 ``(响应, 状态码)`` 元组。与其逐个重写，这里：

1. 在导入上游模块前把 ``quart`` / ``werkzeug.exceptions`` 换成 :mod:`nb2.web.compat`
   （仅当未安装真实 Quart 时）；
2. 提供一个 ``WebAPIRuntime`` 门面，把上游处理器绑到 :class:`MemeRuntime` 上；
3. 用 ``APIRouter`` 逐条注册路由，处理器内部仍是上游实现。

这样前端与后端契约都不变，且上游更新 ``web_api.py`` 时无需重新移植。
"""

from __future__ import annotations

import sys
import types
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import Response

from ..log import logger

from . import compat

#: WebUI 挂载前缀（与 ``nb2.web`` 保持一致）。
ROUTER_PREFIX = "/meme_manager"

_INSTALLED = False


def install_quart_shim() -> None:
    """把 ``quart`` 与 ``werkzeug.exceptions`` 指向兼容实现（幂等）。

    仅在真实 Quart 未安装时生效——在 Quart 环境（如 AstrBot 宿主）中不会干扰。
    """
    global _INSTALLED
    if _INSTALLED:
        return
    if "quart" not in sys.modules:
        try:
            import quart  # noqa: F401
        except Exception:
            quart_module = types.ModuleType("quart")
            quart_module.request = compat.request  # type: ignore[attr-defined]
            quart_module.jsonify = compat.jsonify  # type: ignore[attr-defined]
            quart_module.make_response = compat.make_response  # type: ignore[attr-defined]
            quart_module.send_file = compat.send_file  # type: ignore[attr-defined]
            quart_module.Response = __import__(
                "starlette.responses", fromlist=["Response"]
            ).Response
            sys.modules["quart"] = quart_module

    try:
        import werkzeug.exceptions  # noqa: F401
    except Exception:
        werkzeug_module = types.ModuleType("werkzeug")
        werkzeug_module.__path__ = []  # type: ignore[attr-defined]
        exceptions_module = types.ModuleType("werkzeug.exceptions")
        exceptions_module.RequestEntityTooLarge = compat.RequestEntityTooLarge  # type: ignore[attr-defined]
        werkzeug_module.exceptions = exceptions_module  # type: ignore[attr-defined]
        sys.modules.setdefault("werkzeug", werkzeug_module)
        sys.modules["werkzeug.exceptions"] = exceptions_module

    _INSTALLED = True


class _StarManagerStub:
    """NoneBot2 没有 AstrBot 的插件管理器；用最小桩满足设置页的保存流程。

    上游设置页保存后调用 ``await manager.reload(PLUGIN_NAME)`` 让新配置生效。
    在 NoneBot2 线路里配置由 :class:`PluginConfig` 直接持有并已落盘，业务层
    每次读取都走 ``runtime._read_config_value``，因此「重载」是空操作。
    """

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    async def reload(self, name: str = "") -> tuple[bool, Any]:
        try:
            config = getattr(self._owner, "config", None)
            if config is not None and hasattr(config, "save"):
                config.save()
        except Exception as exc:  # pragma: no cover - 防御性
            logger.warning("保存配置失败：{}", exc)
            return False, exc
        return True, None


class _StarStub:
    """``get_registered_star`` 的返回值：需要 ``star_cls`` 与绑定实例一致。"""

    def __init__(self, star_cls: Any) -> None:
        self.star_cls = star_cls
        self.name = "meme_manager"


def import_webapi_mixin(runtime: Any) -> Any:
    """导入上游 ``WebAPIMixin``，并把运行时状态注入为 mixin 的 ``self``。

    Args:
        runtime: :class:`nb2.runtime.MemeRuntime` 实例。

    Returns:
        绑定后的 mixin 实例（属性读取转发到运行时，方法调用使用运行时状态）。
    """
    install_quart_shim()

    from ...mixins.web_api import WebAPIMixin

    class _BoundWebAPI(WebAPIMixin):
        """把运行时状态暴露成 mixin 期望的 ``self``。"""

        def __init__(self, owner: Any) -> None:
            self.__owner = owner

        def __getattr__(self, name: str) -> Any:
            # 仅在上游 mixin 自身找不到属性时触发。
            owner = object.__getattribute__(self, "_BoundWebAPI__owner")
            try:
                return getattr(owner, name)
            except AttributeError:
                raise AttributeError(name) from None

        # ---- 上游设置页依赖的 AstrBot 上下文接口（NoneBot2 无插件管理器）----
        def get_all_providers(self) -> list[Any]:
            owner = object.__getattribute__(self, "_BoundWebAPI__owner")
            return list(getattr(owner.context, "get_all_providers", list)())

        def get_all_embedding_providers(self) -> list[Any]:
            owner = object.__getattribute__(self, "_BoundWebAPI__owner")
            return list(getattr(owner.context, "get_all_embedding_providers", list)())

        def get_registered_star(self, name: str = "") -> Any:
            """NoneBot2 没有插件管理器；返回自身以便设置页走「已注册」分支。"""
            return _StarStub(self)

        async def reload_star(self, name: str = "") -> tuple[bool, Any]:
            """配置已由 ``PluginConfig.save()`` 直接落盘，无需重载插件。"""
            owner = object.__getattribute__(self, "_BoundWebAPI__owner")
            try:
                owner.config.save()
            except Exception as exc:  # pragma: no cover - 防御性
                return False, exc
            return True, None

    bound = _BoundWebAPI(runtime)

    # 上游设置页写的是 ``self.context._star_manager`` / ``self.context.get_registered_star``，
    # 而 ``self.context`` 解析到的是运行时持有的 LLM 上下文。因此把这两个接口挂在
    # **上下文对象**上（同一份 bound 实例贯穿全部请求，``star_cls is self`` 恒成立）。
    context = getattr(runtime, "context", None)
    if context is not None:
        try:
            setattr(context, "_star_manager", _StarManagerStub(runtime))
            setattr(context, "get_registered_star", lambda name="": _StarStub(bound))
        except Exception as exc:  # pragma: no cover - 防御性
            logger.debug("为 WebUI 注入插件管理器桩失败：{}", exc)
    runtime._star_manager = _StarManagerStub(runtime)  # type: ignore[attr-defined]

    return bound


def register_routes(router: Any, runtime: Any) -> int:
    """把上游 WebAPI 的全部路由注册到 FastAPI ``APIRouter``。

    Args:
        router: ``fastapi.APIRouter``。
        runtime: 插件运行时。

    Returns:
        成功注册的路由数量。
    """
    install_quart_shim()
    bound = import_webapi_mixin(runtime)

    # 上游通过 ``self._register_webui_api(route, handler, methods, desc)`` 注册，
    # 这里截获调用并转换为 FastAPI 路由，从而复用统一的日志与状态处理。
    registrations: list[tuple[str, Callable[..., Any], list[str]]] = []

    def _collect(route: str, handler: Callable[..., Any], methods: list[str], desc: str):
        methods_list = [method.upper() for method in methods or ["GET"]]
        registrations.append((route, handler, methods_list))
        logger.debug("WebUI 路由已登记：{} {}（{}）", "/".join(methods_list), route, desc)

    bound._register_webui_api = _collect  # type: ignore[method-assign]
    try:
        bound._register_web_apis()  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - 上游注册异常
        logger.error("收集 WebUI 路由失败：{}", exc, exc_info=True)
        return 0

    registered = 0
    for route, handler, methods in registrations:
        try:
            _register_one(router, runtime, route, handler, methods)
            registered += 1
        except Exception as exc:  # pragma: no cover - 单路由失败不阻断其余
            logger.error("注册 WebUI 路由失败：{} ({})", route, exc)
    logger.info("WebUI 已注册 {} 条路由（共 {} 条）", registered, len(registrations))
    return registered


def _convert_path(route: str) -> str:
    """把 Quart 的 ``<name>`` 路径参数转换为 FastAPI 的 ``{name}``。"""
    import re

    return re.sub(r"<(?:(?:[^:>]+):)?([^>]+)>", r"{\1}", route)


def _register_one(router: Any, runtime: Any, route: str, handler: Callable[..., Any], methods: list[str]) -> None:
    """把单个上游处理器注册为 FastAPI 端点。"""
    from .compat import normalize_result, reset_current_request, set_current_request

    path = _convert_path(f"{ROUTER_PREFIX}/api/{route.strip('/')}")
    # 上游处理器没有 ``Request`` 参数，FastAPI 需要知道路径参数的类型；
    # 全部按字符串处理（上游自身做校验与转换）。
    path_params = [
        name for name in _path_param_names(path)
    ]

    async def endpoint(request: Request) -> Response:
        token = set_current_request(request)
        try:
            kwargs: dict[str, Any] = dict(request.path_params)
            result = await handler(**kwargs)
            return normalize_result(result)
        finally:
            reset_current_request(token)

    router.add_api_route(
        path,
        endpoint,
        methods=methods,
        include_in_schema=False,
        name=f"meme_manager_{handler.__name__}",
    )


def _path_param_names(path: str) -> list[str]:
    import re

    return re.findall(r"\{([^}:]+)(?::[^}]+)?\}", path)


__all__ = ["install_quart_shim", "import_webapi_mixin", "register_routes"]
