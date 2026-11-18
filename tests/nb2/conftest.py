"""nb2 适配层测试的公共夹具。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

#: 仓库根目录（插件包根）。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

PLUGIN_NAME = "repo"

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
    import sys

    from repo.nb2.paths_bind import bind_module_paths

    bind_module_paths(data_dir)
    import repo.config as config_module

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
