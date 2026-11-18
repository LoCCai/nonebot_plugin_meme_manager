"""实测：不同资源包在插件里分别处于哪种「可用状态」。

验证：
1. 官方包（无 semantic_metadata）→ 分类映射可用、语义检索不可用；
2. 语义增强包（自带 semantic_metadata）→ 语义记录齐全，但**向量索引仍需构建**
   （需要 embedding 提供方），在此之前语义检索仍不可用。

运行：python tools/probe_pack_states.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_sys_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _sys_dir)
from _console import ensure_utf8_stdout, safe_print  # noqa: E402

ensure_utf8_stdout()

DATA_DIR = tempfile.mkdtemp(prefix="meme_states_")
os.environ["MEME_MANAGER_DATA_DIR"] = DATA_DIR

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pkg_alias  # noqa: E402

_pkg_alias.install()

from repo.nb2 import bootstrap  # noqa: E402

bootstrap.ensure()

from repo.backend.packs.storage import (  # noqa: E402
    get_pack_detail,
    install_pack_from_github_source,
)
from repo.backend.semantic.index import index_is_ready  # noqa: E402
from repo.backend.semantic.storage import load_metadata  # noqa: E402
from repo.nb2.runtime import MemeRuntime  # noqa: E402
from repo.config import COMMUNITY_INDEX_URL  # noqa: E402

PACKS = [
    ("official-001", "anka-afk/astrbot-meme-pack-official-01", "main"),
    ("ddzs987-semantic-001", "DDZS987/astrbot-meme-pack-semantic-01", "v1.1.0"),
]


def main() -> int:
    runtime = MemeRuntime()
    safe_print(f"数据目录：{DATA_DIR}\n")

    for pack_id, repo, ref in PACKS:
        safe_print("=" * 72)
        safe_print(f"安装 {pack_id} …")
        try:
            result = install_pack_from_github_source(
                source={"type": "github", "repo": repo, "ref": ref, "subpath": "."},
                set_as_default=True,
                preserve_existing_selection=False,
            )
        except Exception as exc:
            safe_print(f"  安装失败：{type(exc).__name__}: {exc}")
            continue
        safe_print(f"  安装结果：pack_id={result.get('pack_id')} 图片数见下")

        detail = get_pack_detail(pack_id)
        categories = detail.get("manifest", {}).get("categories", {})
        safe_print(f"  图片：{detail.get('total_images')} 张，分类 {len(categories)} 个")

        # 运行时视角：分类映射（用于「分类模式」）
        context = runtime._resolve_runtime_pack_context()
        mapping = context.get("category_mapping") or {}
        safe_print(f"  运行时分类映射：{len(mapping)} 个（分类模式可直接用）")
        sample = list(mapping.items())[:2]
        for name, desc in sample:
            safe_print(f"    - {name}: {desc[:40]}")

        # 语义视角（用于「语义检索模式」）
        metadata = load_metadata(context["pack_dir"])
        images = metadata.get("images") or {}
        done = sum(
            1
            for item in images.values()
            if isinstance(item, dict) and item.get("caption_status") == "done"
        )
        safe_print(f"  语义记录：{len(images)} 条，已打标 {done} 条")

        ready = index_is_ready(
            runtime.data_dir, pack_id, metadata, None, None, None, allow_partial=True
        )
        safe_print(f"  向量索引就绪：{ready}")
        safe_print(
            "  结论："
            + (
                "语义检索可用"
                if ready
                else (
                    "已有打标，但需配置 embedding 模型并「重建索引」后语义检索才可用"
                    if done
                    else "无打标：需先在语义化页面用视觉模型识图打标，再建索引"
                )
            )
        )
        safe_print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
