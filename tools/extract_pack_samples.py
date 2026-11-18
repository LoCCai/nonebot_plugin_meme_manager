"""从已下载的官方包中提取样本图片，用于人工核对分类是否合理。

说明：``semantic_metadata.json`` 的**键是 entry_id**，并不是文件内容哈希；
与图片文件对应需要靠每条记录里的 ``content_sha256`` 字段。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _console import ensure_utf8_stdout, safe_print  # noqa: E402

ensure_utf8_stdout()

CACHE = pathlib.Path(__file__).resolve().parents[2] / "packs_cache"
ZIP = CACHE / "official-001.zip"
META = CACHE / "ddzs987_semantic_metadata.json"
OUT = CACHE / "samples"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads(META.read_text(encoding="utf-8-sig"))["images"]

    # content_sha256 -> 记录（注意：键是 entry_id，不是哈希）
    by_sha: dict[str, dict] = {}
    for record in meta.values():
        sha = str(record.get("content_sha256") or "").lower()
        if sha:
            by_sha.setdefault(sha, record)

    zf = zipfile.ZipFile(ZIP)
    chosen: dict[str, tuple[str, dict, bytes]] = {}
    matched = 0
    for name in zf.namelist():
        leaf = name.rsplit("/", 1)[-1]
        if "/memes/" not in name or "." not in leaf:
            continue
        data = zf.read(name)
        record = by_sha.get(hashlib.sha256(data).hexdigest())
        if not record:
            continue
        matched += 1
        category = str(record.get("category") or "")
        if category and category not in chosen:
            chosen[category] = (name, record, data)

    safe_print(f"语义记录 {len(meta)} 条，按 content_sha256 命中图片 {matched} 张")
    safe_print(f"覆盖分类 {len(chosen)} 个：{sorted(chosen)}")
    for category, (name, record, data) in sorted(chosen.items()):
        suffix = pathlib.Path(name).suffix or ".png"
        target = OUT / f"{category}{suffix}"
        target.write_bytes(data)
        safe_print(f"\n[{category}] {target.name} ({len(data)} bytes)")
        safe_print(f"  caption: {(record.get('caption') or '')[:140]}")
        safe_print(f"  tags: {(record.get('tags') or [])[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
