"""业务层日志适配。

原插件通过 ``from astrbot.api import logger`` 获取 loguru logger。NoneBot2 同样
使用 loguru，但业务层代码混用了 ``{}`` 与 ``%s`` 两种占位风格，这里做统一兜底，
避免迁移后因格式化失败丢日志。
"""

from __future__ import annotations

import logging
from typing import Any

try:  # pragma: no cover - 取决于环境是否安装 NoneBot2
    from nonebot.log import logger as _nb_logger
except Exception:  # pragma: no cover - 未安装 NoneBot2 时回退标准库
    _nb_logger = logging.getLogger("nonebot_plugin_meme_manager")


def _render(message: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    if not args and not kwargs:
        return str(message)
    try:
        return str(message).format(*args, **kwargs)
    except Exception:
        pass
    try:
        return str(message) % args
    except Exception:
        pass
    parts = [str(message), *(str(arg) for arg in args)]
    return " ".join(parts)


class _Logger:
    """将日志调用转发给 NoneBot2 的 loguru logger。"""

    def _emit(self, level: str, message: Any, *args: Any, **kwargs: Any) -> None:
        text = _render(message, args, kwargs)
        getattr(_nb_logger, level)(text)

    def debug(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("debug", message, *args, **kwargs)

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("info", message, *args, **kwargs)

    def success(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("success", message, *args, **kwargs)

    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("warning", message, *args, **kwargs)

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("error", message, *args, **kwargs)

    def critical(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("critical", message, *args, **kwargs)

    def exception(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("exception", message, *args, **kwargs)


logger = _Logger()

__all__ = ["logger", "_Logger"]
