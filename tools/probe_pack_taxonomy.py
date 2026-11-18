"""调研：社区资源包里的分类/语义信息到底是「已分类」还是「需 AI 识图」。

做法：直接下载各表情包仓库的 zip，只读取目录结构与关键 JSON，不落盘安装。

运行：python tools/probe_pack_taxonomy.py
"""

from __future__ import annotations

import io
import json
import os
import sys
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


def archive_url(repo: str, ref: str) -> str:
    return f"https://codeload.github.com/{repo}/zip/{ref}"


def probe(repo: str, ref: str) -> dict:
    """下载并分析一个表情包仓库。"""
    url = archive_url(repo, ref)
    resp = requests.get(url, timeout=(10, 120), stream=True)
    resp.raise_for_status()
    data = resp.content
    result: dict = {"repo": repo, "ref": ref, "zip_bytes": len(data)}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        root = names[0].split("/")[0]
        rel = [n[len(root) + 1 :] for n in names if n.startswith(root + "/")]

        # manifest
        manifest_name = next((n for n in rel if n.endswith("manifest.json")), None)
        manifest = {}
        if manifest_name:
            manifest = json.loads(zf.read(f"{root}/{manifest_name}").decode("utf-8-sig"))
        result["manifest"] = {
            "id": manifest.get("id"),
            "name": manifest.get("name"),
            "version": manifest.get("version"),
            "category_count": len(manifest.get("categories") or {}),
            "categories": list((manifest.get("categories") or {}).keys())[:30],
        }

        # 分类目录结构：memes/<category>/<file>
        cat_dirs: dict[str, int] = {}
        images = []
        for name in rel:
            parts = name.split("/")
            if len(parts) >= 3 and parts[0] == "memes" and not name.endswith("/"):
                cat_dirs[parts[1]] = cat_dirs.get(parts[1], 0) + 1
                images.append(name)
        result["memes_dir"] = {
            "file_count": len(images),
            "category_dirs": len(cat_dirs),
            "per_category": dict(sorted(cat_dirs.items(), key=lambda kv: -kv[1])[:12]),
        }

        # 语义元数据
        sem_name = next((n for n in rel if n.endswith("semantic_metadata.json")), None)
        result["has_semantic_metadata"] = bool(sem_name)
        if sem_name:
            meta = json.loads(zf.read(f"{root}/{sem_name}").decode("utf-8-sig"))
            imgs = meta.get("images") or {}
            done = sum(
                1
                for item in imgs.values()
                if isinstance(item, dict) and item.get("caption_status") == "done"
            )
            with_tags = sum(
                1 for item in imgs.values() if isinstance(item, dict) and item.get("tags")
            )
            result["semantic"] = {
                "entries": len(imgs),
                "caption_done": done,
                "with_tags": with_tags,
                "sample": list(
                    (
                        k[:12],
                        {
                            "caption": (v.get("caption") or "")[:40],
                            "tags": (v.get("tags") or [])[:5],
                            "category": v.get("category"),
                        },
                    )
                    for k, v in list(imgs.items())[:3]
                ),
            }
        # 其他元数据文件
        result["metadata_files"] = [n for n in rel if n.endswith(".json") and "memes/" not in n][:8]
        # 图片是否带额外旁注（如 captions.txt / tags）
        result["readme_like"] = [n for n in rel if n.lower().endswith((".md", ".txt"))][:5]
    return result


def main() -> int:
    index = json.loads(requests.get(INDEX_URL, timeout=30).text)
    packs = index["packs"]
    safe_print(f"社区索引共 {len(packs)} 个包\n")

    # 抽样：官方包 + 一个「语义化增强版」+ 一个第三方包
    wanted = ["official-001", "ddzs987-semantic-001"]
    selected = [p for p in packs if p["id"] in wanted]
    if len(selected) < len(wanted):
        selected += [p for p in packs if p["id"] not in wanted][:2]

    for entry in selected:
        source = entry.get("source") or {}
        try:
            info = probe(source["repo"], source.get("ref", "main"))
        except Exception as exc:
            safe_print(f"!! {entry['id']} 探测失败：{type(exc).__name__}: {exc}")
            continue
        safe_print("=" * 70)
        safe_print(f"包 {entry['id']} | {entry.get('name')} | {source['repo']}@{source.get('ref')}")
        safe_print(f"  zip: {info['zip_bytes'] / 1024 / 1024:.1f} MB")
        safe_print(
            f"  manifest: id={info['manifest']['id']} 分类数={info['manifest']['category_count']}"
        )
        safe_print(f"  分类：{info['manifest']['categories']}")
        safe_print(
            f"  memes 目录：{info['memes_dir']['file_count']} 个文件 / "
            f"{info['memes_dir']['category_dirs']} 个分类目录"
        )
        safe_print(f"  每类数量：{info['memes_dir']['per_category']}")
        safe_print(f"  语义元数据文件：{info['has_semantic_metadata']}")
        if info.get("semantic"):
            sem = info["semantic"]
            safe_print(
                f"    条目 {sem['entries']}｜已打标 {sem['caption_done']}｜有 tags {sem['with_tags']}"
            )
            for key, sample in sem["sample"]:
                safe_print(f"    样本 {key}: {sample}")
        safe_print(f"  其他 json：{info['metadata_files']}")
        safe_print(f"  说明文件：{info['readme_like']}")
        safe_print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
