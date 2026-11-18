"""WebUI 的 FastAPI 集成：路由、静态页面与宿主桥。

NoneBot2 侧没有 AstrBot 的「插件页面」机制，因此这里：

* 用 ``APIRouter`` 挂载上游 ``mixins/web_api.py`` 的全部 ``/api/*`` 处理器；
* 静态托管 ``pages/``（前端零改动），并把 ``bridge.js`` 注入页面，提供
  ``window.AstrBotPluginPage`` 的 NoneBot2 实现；
* 提供 ``/meme_manager/`` 入口页与鉴权 token 约定（与上游一致）。
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from ..log import logger
from ..paths import PLUGIN_DIR

ROUTER_PREFIX = "/meme_manager"
PAGES_DIR = PLUGIN_DIR / "pages"
BRIDGE_PATH = Path(__file__).resolve().parent / "static" / "bridge.js"

#: 静态资源鉴权参数（与上游前端约定一致）。
ASSET_TOKEN_PARAM = "asset_token"

_asset_token = secrets.token_urlsafe(24)


def asset_token() -> str:
    """返回本次进程的静态资源 token。"""
    return _asset_token


def get_router() -> Any:
    """构造 WebUI 路由（供 ``mount_webui`` 与测试使用）。"""
    from fastapi import APIRouter

    router = APIRouter()
    _register_pages(router)
    return router


def _register_pages(router: Any) -> None:
    """注册静态页面与入口重定向。"""
    from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

    @router.get(f"{ROUTER_PREFIX}", include_in_schema=False)
    async def _root() -> Any:
        return RedirectResponse(url=f"{ROUTER_PREFIX}/index.html")

    @router.get(f"{ROUTER_PREFIX}/", include_in_schema=False)
    async def _root_slash() -> Any:
        return RedirectResponse(url=f"{ROUTER_PREFIX}/index.html")

    @router.get(f"{ROUTER_PREFIX}/bridge.js", include_in_schema=False)
    async def _bridge_js() -> Any:
        if BRIDGE_PATH.is_file():
            return FileResponse(BRIDGE_PATH, media_type="application/javascript")
        return HTMLResponse("bridge.js 缺失", status_code=404)

    @router.get(f"{ROUTER_PREFIX}/index.html", include_in_schema=False)
    async def _index() -> Any:
        if PAGES_DIR.is_dir():
            return FileResponse(PAGES_DIR / "index.html", media_type="text/html")
        return HTMLResponse(
            "<h1>表情包管理器</h1><p>未找到 pages/ 目录。</p>", status_code=404
        )

    @router.get(f"{ROUTER_PREFIX}/app/{{path:path}}", include_in_schema=False)
    async def _static(path: str) -> Any:
        target = (PAGES_DIR / "app" / path).resolve()
        try:
            target.relative_to((PAGES_DIR / "app").resolve())
        except ValueError:
            return HTMLResponse("非法路径", status_code=400)
        if not target.is_file():
            return HTMLResponse("页面不存在", status_code=404)
        media_type = _guess_media_type(target.suffix)
        return FileResponse(target, media_type=media_type)


def _guess_media_type(suffix: str) -> str:
    mapping = {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".ico": "image/x-icon",
    }
    return mapping.get(suffix.lower(), "application/octet-stream")


def mount_webui(runtime: Any) -> bool:
    """把 WebUI 挂载到 NoneBot2 的 FastAPI driver。

    Args:
        runtime: 插件运行时。

    Returns:
        是否成功挂载。
    """
    try:
        from .bridge import register_routes

        router = get_router()
        count = 0
        try:
            count = register_routes(router, runtime)
        except Exception as exc:
            logger.error("注册 WebUI API 失败：{}", exc, exc_info=True)
        _mount_router(router)
        logger.info("WebUI 已挂载到 {}（{} 条 API 路由）", ROUTER_PREFIX, count)
        return True
    except Exception as exc:  # pragma: no cover - 防御性
        logger.error("挂载 WebUI 失败：{}", exc, exc_info=True)
        return False


def _mount_router(router: Any) -> None:
    """把路由挂到 driver 的 FastAPI 应用上（幂等）。"""
    import nonebot

    driver = nonebot.get_driver()
    server_app = getattr(driver, "server_app", None)
    if server_app is None:  # pragma: no cover - 非 FastAPI driver
        raise RuntimeError("当前 driver 不支持 FastAPI，无法挂载 WebUI")
    server_app.include_router(router)


__all__ = [
    "mount_webui",
    "get_router",
    "asset_token",
    "ROUTER_PREFIX",
    "PAGES_DIR",
    "BRIDGE_PATH",
]
