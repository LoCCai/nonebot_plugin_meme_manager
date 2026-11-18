"""实测：从官方社区索引下载并加载表情包（需要网络）。

运行：python tools/test_pack_download.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / "tools"))
import _pkg_alias  # noqa: E402  （把本仓库插件包统一别名为 repo）

_pkg_alias.install()

DATA_DIR = Path(tempfile.mkdtemp(prefix="meme_pack_dl_"))
os.environ["MEME_MANAGER_DATA_DIR"] = str(DATA_DIR)

from repo.nb2 import bootstrap  # noqa: E402

bootstrap.ensure()

from repo.nb2.runtime import MemeRuntime  # noqa: E402

runtime = MemeRuntime(data_dir=DATA_DIR)
print(f"[1] 数据目录：{DATA_DIR}")

# 社区索引拉取
from repo.config import COMMUNITY_INDEX_URL  # noqa: E402
from repo.backend.packs.storage import (  # noqa: E402
    fetch_and_cache_community_index,
    install_first_official_pack_from_index,
    list_installed_packs,
    load_cached_community_index,
    get_pack_detail,
)

started = time.monotonic()
cached = fetch_and_cache_community_index(COMMUNITY_INDEX_URL)
packs = cached.get("index", {}).get("packs", [])
print(
    f"[2] 索引拉取成功：{len(packs)} 个表情包，耗时 {time.monotonic() - started:.1f}s，"
    f"缓存文件 {runtime.data_dir / 'community_cache.json'}"
)

# 安装首个官方包
started = time.monotonic()
result = install_first_official_pack_from_index(
    COMMUNITY_INDEX_URL, overwrite=False, set_as_default=True
)
elapsed = time.monotonic() - started
pack_id = str(result.get("pack_id") or result.get("selected_pack_id") or "")
print(f"[3] 安装完成：pack_id={pack_id} 名称={result.get('selected_pack_name') or result.get('name')}")
print(f"    耗时 {elapsed:.1f}s，结果键：{sorted(result)}")

# 校验落盘
detail = get_pack_detail(pack_id)
print(
    f"[4] 表情包加载：分类 {len(detail.get('manifest', {}).get('categories', {}))} 个，"
    f"图片 {detail.get('total_images')} 张"
)
installed = list_installed_packs()
print(f"[5] 已安装列表：{[(item.get('id'), item.get('enabled')) for item in installed]}")

# 运行时视角
runtime._reload_personas()
print(f"[6] 运行时分类映射：{len(runtime.category_mapping)} 个，prompt 长度 {len(runtime._build_meme_prompt())}")

# 缓存命中
started = time.monotonic()
cached2 = load_cached_community_index()
print(
    f"[7] 缓存读取：{len(cached2.get('index', {}).get('packs', []))} 个，"
    f"{time.monotonic() - started:.2f}s"
)

print("\n实测通过。")
