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
