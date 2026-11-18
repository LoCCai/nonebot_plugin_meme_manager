"""结论性调研：资源包里的「分类」和「语义」到底处于什么状态。

回答两个问题：
1. 下载到的表情包是否已经按用途分好类？（分类目录 + manifest 描述）
2. 语义检索（按含义找图）是否需要自己跑 AI 识图？

运行：python tools/probe_taxonomy_status.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

_sys_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _sys_dir)
from _console import ensure_utf8_stdout, safe_print  # noqa: E402

ensure_utf8_stdout()

import requests  # noqa: E402

INDEX_URL = (
    "https://raw.githubusercontent.com/anka-afk/astrbot-meme-pack-index/main/community-index.json"
)


def fetch_pack(repo: str, ref: str) -> zipfile.ZipFile:
    resp = requests.get(
        f"https://codeload.github.com/{repo}/zip/{ref}", timeout=(10, 180)
    )
    resp.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(resp.content))


def analyze(zf: zipfile.ZipFile) -> dict:
    names = zf.namelist()
    root = names[0].split("/")[0]
    rel = [n[len(root) + 1 :] for n in names if n.startswith(root + "/")]

    manifest = {}
    if "manifest.json" in rel:
        manifest = json.loads(zf.read(f"{root}/manifest.json").decode("utf-8-sig"))

    # 分类目录 + manifest 描述是否齐全
    cat_dirs = sorted(
        {n.split("/")[1] for n in rel if n.startswith("memes/") and len(n.split("/")) >= 3}
    )
    manifest_cats = manifest.get("categories") or {}
    described = [c for c in cat_dirs if str(manifest_cats.get(c, "")).strip() not in ("", "None")]

    # memes_data.json 是否带条目（分类索引）
    memes_data = {}
    if "memes_data.json" in rel:
        memes_data = json.loads(zf.read(f"{root}/memes_data.json").decode("utf-8-sig"))

    # 语义元数据
    semantic = {}
    if "semantic_metadata.json" in rel:
        semantic = json.loads(zf.read(f"{root}/semantic_metadata.json").decode("utf-8-sig"))

    images = [n for n in rel if n.startswith("memes/") and not n.endswith("/")]
    return {
        "manifest_categories": len(manifest_cats),
        "category_dirs": cat_dirs,
        "dirs_with_description": described,
        "image_count": len(images),
        "memes_data_entries": len(memes_data.get("categories") or memes_data.get("emojis") or memes_data),
        "semantic_entries": len((semantic.get("images") or {})),
        "semantic_done": sum(
            1
            for item in (semantic.get("images") or {}).values()
            if isinstance(item, dict) and item.get("caption_status") == "done"
        ),
    }


def main() -> int:
    index = json.loads(requests.get(INDEX_URL, timeout=30).text)
    by_id = {p["id"]: p for p in index["packs"]}

    for pack_id in ("official-001", "ddzs987-semantic-001"):
        entry = by_id[pack_id]
        source = entry["source"]
        safe_print(f"下载 {pack_id}（{entry['name']}）…")
        with fetch_pack(source["repo"], source.get("ref", "main")) as zf:
            info = analyze(zf)
        safe_print(f"  图片总数：{info['image_count']}")
        safe_print(
            f"  分类目录 {len(info['category_dirs'])} 个，其中 manifest 有描述 "
            f"{len(info['dirs_with_description'])} 个"
        )
        safe_print(f"  分类：{info['category_dirs']}")
        safe_print(f"  memes_data 条目：{info['memes_data_entries']}")
        safe_print(
            f"  semantic_metadata：{info['semantic_entries']} 条（已完成 {info['semantic_done']}）"
        )
        safe_print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
