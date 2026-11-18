"""让插件包以固定别名 ``repo`` 导入（任意仓库目录名下都可用）。

仓库根目录本身就是插件包，名字取决于检出目录（本地是 ``repo``，CI 是
``nonebot_plugin_meme_manager``）。测试与开发脚本统一用 ``repo.nb2...`` 导入，
因此这里安装一个**转发式** meta path finder：

* ``repo`` → 真实包对象（同一个模块实例，不复制状态）；
* ``repo.nb2`` / ``repo.nb2.llm`` … → 真实包对应子模块的**同一实例**。

这样既不用改测试里的 import 路径，也不会出现「两份模块、两份注册表」的问题。
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from pathlib import Path

#: 测试与脚本使用的固定别名。
ALIAS = "repo"

#: 插件包的真实导入名（= 仓库目录名）。
PLUGIN_NAME = Path(__file__).resolve().parents[1].name


class _ForwardLoader(importlib.abc.Loader):
    """把别名模块转发到真实模块对象。"""

    def __init__(self, real_name: str) -> None:
        self.real_name = real_name

    def create_module(self, spec):  # type: ignore[override]
        return importlib.import_module(self.real_name)

    def exec_module(self, module) -> None:  # noqa: ARG002 - 真实模块已执行完毕
        return None


class _AliasFinder(importlib.abc.MetaPathFinder):
    """把 ``repo.*`` 转发到 ``<PLUGIN_NAME>.*``。"""

    def find_spec(self, fullname: str, path=None, target=None):  # noqa: ARG002
        if fullname != ALIAS and not fullname.startswith(ALIAS + "."):
            return None
        real_name = PLUGIN_NAME + fullname[len(ALIAS) :]
        try:
            spec = importlib.util.find_spec(real_name)
        except (ImportError, ValueError):
            return None
        if spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            _ForwardLoader(real_name),
            is_package=spec.submodule_search_locations is not None,
        )


def install() -> str:
    """安装别名并返回真实包名。

    注意：**不会预先导入插件包**。NoneBot 的 ``load_plugin`` 要求模块在加载前
    未被导入，否则会报 "not loaded as a plugin"；因此需要先用
    ``nonebot.load_plugin(_pkg_alias.PLUGIN_NAME)`` 加载，再调用本函数。
    """
    root = Path(__file__).resolve().parents[1]
    if str(root.parent) not in sys.path:
        sys.path.insert(0, str(root.parent))
    if PLUGIN_NAME == ALIAS:
        return PLUGIN_NAME
    if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _AliasFinder())
    return PLUGIN_NAME


__all__ = ["install", "ALIAS", "PLUGIN_NAME"]
