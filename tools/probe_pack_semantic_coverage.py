"""统计社区索引里各表情包「是否带语义元数据」（不加下载整包）。

用 GitHub Trees API 只读文件列表（每个包 1 次请求）。

运行：python tools/probe_pack_semantic_coverage.py
"""

from __future__ import annotations

import json
import os
import sys
import time

_sys_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _sys_dir)
from _console import ensure_utf8_stdout, safe_print  # noqa: E402

ensure_utf8_stdout()

import requests  # noqa: E402

INDEX_URL = (
    "https://raw.githubusercontent.com/anka-afk/astrbot-meme-pack-index/main/community-index.json"
)


def repo_tree(repo: str, ref: str) -> list[str] | None:
    url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    resp = requests.get(url, timeout=30, headers={"Accept": "application/vnd.github+json"})
    if resp.status_code != 200:
        return None
    data = resp.json()
    return [item["path"] for item in data.get("tree", []) if item.get("type") == "blob"]


def main() -> int:
    index = json.loads(requests.get(INDEX_URL, timeout=30).text)
    packs = index["packs"]
    safe_print(f"{'包 ID':34} {'分类目录':>6} {'图片':>5} {'语义元数据':>8}  manifest 分类数")
    safe_print("-" * 92)

    semantic_packs = 0
    for entry in packs:
        source = entry.get("source") or {}
        repo, ref = source.get("repo"), source.get("ref", "main")
        tree = repo_tree(repo, ref)
        if tree is None:
            safe_print(f"{entry['id']:34}  （Trees API 不可用，跳过）")
            time.sleep(1)
            continue
        memes = [p for p in tree if p.startswith("memes/") and not p.endswith("/")]
        dirs = {p.split("/")[1] for p in memes if len(p.split("/")) >= 3}
        has_sem = "semantic_metadata.json" in tree
        manifest = next((p for p in tree if p.endswith("manifest.json")), None)
        category_count = "?"
        if manifest:
            try:
                raw = requests.get(
                    f"https://raw.githubusercontent.com/{repo}/{ref}/{manifest}", timeout=30
                ).json()
                category_count = len(raw.get("categories") or {})
            except Exception:
                pass
        if has_sem:
            semantic_packs += 1
        safe_print(
            f"{entry['id']:34} {len(dirs):>6} {len(memes):>5} "
            f"{('有' if has_sem else '无'):>8}  {category_count}"
        )
        time.sleep(0.3)

    safe_print()
    safe_print(f"合计 {len(packs)} 个包，其中 {semantic_packs} 个自带 semantic_metadata.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
