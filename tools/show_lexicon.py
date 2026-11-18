"""打印生成后的词库，便于人工检查质量。"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _console import ensure_utf8_stdout, safe_print  # noqa: E402

ensure_utf8_stdout()

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEXICON_DIR = ROOT / "assets" / "category_lexicon"


def show(pack_id: str, categories: list[str] | None = None, limit: int = 6) -> None:
    path = LEXICON_DIR / f"{pack_id}.json"
    if not path.is_file():
        safe_print(f"!! 词库不存在：{path}")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    cats = payload.get("categories") or {}
    safe_print(f"== {pack_id}：{len(cats)} 个分类 ==")
    names = categories or list(cats)
    for name in names:
        entry = cats.get(name)
        if not entry:
            continue
        source = entry.get("source", "")
        safe_print(f"\n--- {name}（{source}）")
        safe_print(f"    描述：{str(entry.get('description') or '')[:60]}")
        for key, label in (
            ("mood", "心情"),
            ("tone", "语气"),
            ("context", "语境"),
            ("intent", "用途"),
        ):
            words = entry.get(key) or []
            safe_print(f"    {label}：{'、'.join(words[:limit])}")


def main() -> int:
    if not LEXICON_DIR.is_dir():
        safe_print("尚未生成任何词库")
        return 1
    for path in sorted(LEXICON_DIR.glob("*.json")):
        show(path.stem)
        safe_print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
