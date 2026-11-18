"""nb2 适配层测试的公共夹具。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

#: 仓库根目录（插件包根）。conftest 位于 <root>/tests/nb2/，需上溯三层。
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import _pkg_alias  # noqa: E402  （测试辅助：把插件包统一别名为 repo）

#: 插件包真实导入名（= 仓库目录名）。本地为 repo，CI 为 nonebot_plugin_meme_manager。
PLUGIN_NAME = _pkg_alias.install()

#: 测试统一使用 ``repo.nb2...`` 导入别名（首次访问时才真正导入插件包，
#: 避免影响 NoneBot 的插件加载语义）。
import repo  # noqa: E402,F401  （触发别名转发）

#: 测试期间把插件数据目录固定到仓库内，避免污染用户环境。
os.environ.setdefault("MEME_MANAGER_DATA_DIR", str(ROOT / ".pytest_data"))


@pytest.fixture(scope="session", autouse=True)
def _compat_installed():
    """确保 astrbot 兼容层已安装（业务模块导入的前提）。"""
    from repo.nb2 import bootstrap

    bootstrap.ensure()
    yield


@pytest.fixture(scope="session")
def plugin_package():
    """导入插件包（仓库根目录）。"""
    import importlib

    return importlib.import_module(PLUGIN_NAME)


def rebind_business_paths(data_dir) -> None:
    """把已导入业务模块中的路径常量重定向到 ``data_dir``。

    上游业务模块以 ``from ...config import PACKS_DIR`` 的形式在**导入时**取常量值，
    因此多个运行时（每个测试一个数据目录）之间需要重新绑定，否则后一个运行时
    仍会读写前一个测试的目录。
    """
    from repo.nb2.paths_bind import bind_module_paths

    bind_module_paths(data_dir)
    import importlib

    config_module = importlib.import_module(f"{PLUGIN_NAME}.config")

    for module in list(sys.modules.values()):
        if module is None or module is config_module:
            continue
        for name in (
            "PLUGIN_DATA_DIR",
            "PACKS_DIR",
            "REGISTRY_PATH",
            "SELECTION_RULES_PATH",
            "COMMUNITY_CACHE_PATH",
            "BACKUP_DIR",
            "TEMP_DIR",
            "SEMANTIC_INDEXES_DIR",
        ):
            if name in getattr(module, "__dict__", {}):
                setattr(module, name, getattr(config_module, name))
