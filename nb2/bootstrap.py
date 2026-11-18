"""兼容层安装的单一入口。

插件包 ``__init__.py`` 与 ``config.py`` 都会调用它，保证无论从哪个模块先进入，
``astrbot`` 兼容实现都已就位（meta path finder 使其与导入顺序无关）。
"""

from __future__ import annotations

from . import compat as _compat

_installed: bool | None = None


def ensure() -> bool:
    """确保兼容层已安装（幂等）。"""
    global _installed
    if _installed is None:
        _installed = _compat.install()
    return _installed


__all__ = ["ensure"]
