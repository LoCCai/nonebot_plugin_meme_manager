"""生成/补全表情包分类词库。

两条数据来源，都不需要额外调用视觉模型：

1. **语义元数据**（``semantic_metadata.json``）：作者已用视觉模型逐张打标，
   这里把每张图的 tags/caption 归并到「心情 / 语气 / 语境 / 用途」四类。
2. **manifest 分类描述**：人工撰写的一句话说明，用于兜底与语义校验。

输出：``assets/category_lexicon/<pack_id>.json``（随仓库分发）或
``<data_dir>/lexicon/<pack_id>.json``（运行期覆盖）。

运行：
    python tools/build_lexicon.py --pack ocal-001 --zip ../packs_cache/official-001.zip \
        --metadata ../packs_cache/ddzs987_semantic_metadata.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_sys_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _sys_dir)
from _console import ensure_utf8_stdout, safe_print  # noqa: E402

ensure_utf8_stdout()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import _pkg_alias  # noqa: E402

_pkg_alias.install()

from repo.nb2.lexicon import BASE_LEXICON, merge_category  # noqa: E402

#: 词太长、太短都不适合做提示词。
MIN_LEN, MAX_LEN = 2, 12

#: 明显是「画面描述」而非「对话语境」的词，放进提示词会干扰选图。
VISUAL_NOISE = re.compile(
    r"(少女|男孩|女孩|角色|动漫|二次元|背景|画风|立绘|头像|手|脸|眼|嘴|眉|头发|姿势|构图|表情包|气泡|画面|立绘)$"
)

#: 过于宽泛、放进提示词没有区分度的词。
GENERIC_NOISE = {
    "聊天",
    "对话",
    "聊天回复",
    "聊天反问",
    "聊天反应",
    "聊天接话",
    "聊天接话失败",
    "对话吐槽",
    "对话反击",
    "对话反驳",
    "对话气泡",
    "表情",
    "反应",
    "语气",
    "场景",
    "二次元",
    "动漫",
    "萌",
}

#: 不适合进入提示词的露骨词（资源包内确实存在）。
UNSAFE_NOISE = re.compile(r"(射精|精液|射爆|早泄|色情|脱衣|裸|性交|做爱|高潮)")

#: 语气类线索词。
TONE_HINTS = re.compile(
    r"(语气|口吻|吐槽|调侃|讽刺|挖苦|撒娇|卖萌|抱怨|质问|指责|夸|赞|吼|喊|嘟囔|安慰|劝|催|提醒|道谢|道歉|招呼|问候|自嘲|耍赖|威胁|怼)"
)
#: 语境类线索词。
CONTEXT_HINTS = re.compile(
    r"(开会|加班|工作|游戏|追番|考试|作业|外卖|吃饭|熬夜|深夜|尴尬|社死|被夸|被拒绝|被坑|被冒犯|出糗|翻车|闯祸|麻烦|隐私|亲密|公开场合|群里|私聊)"
)
#: 用途/意图类线索词。
INTENT_HINTS = re.compile(
    r"(表示|表达|用于|用来|回应|回击|提醒|请求|拒绝|答应|确认|催促|安慰|庆祝|分享|反问|要钱|讨要|道歉|道谢|打招呼|转移话题|缓和|澄清)"
)
#: 心情类线索词。
MOOD_HINTS = re.compile(
    r"(开心|高兴|快乐|愉快|兴奋|激动|得意|满足|幸福|难过|伤心|失落|委屈|低落|心累|伤感|生气|愤怒|恼火|不爽|烦躁|火大|不满|无语|无奈|困惑|懵|疑惑|不解|惊讶|震惊|意外|害羞|脸红|不好意思|尴尬|害怕|紧张|焦虑|期待|疲惫|困|好奇|喜欢|欣赏|认可|感动|暖心|呆|萌)"
)


def read_manifest(zip_path: Path) -> dict:
    import zipfile

    zf = zipfile.ZipFile(zip_path)
    name = next((n for n in zf.namelist() if n.endswith("manifest.json")), None)
    if not name:
        return {}
    return json.loads(zf.read(name).decode("utf-8-sig"))


def bucket_word(word: str, caption: str = "") -> str | None:
    """把单个词分到**唯一**维度（避免同一词在四个维度重复出现）。

    优先级：语境 > 用途 > 语气 > 心情。越具体的维度优先，这样
    「加班」不会既算语境又算心情。
    """
    if CONTEXT_HINTS.search(word):
        return "context"
    if INTENT_HINTS.search(word):
        return "intent"
    if TONE_HINTS.search(word):
        return "tone"
    if MOOD_HINTS.search(word):
        return "mood"
    return None


def clean_word(word: str) -> str:
    text = re.sub(r"\s+", "", str(word or ""))
    text = text.replace("category:", "")
    return text.strip("，。,.、；;：:!?！？\"'“”")


def is_usable(word: str) -> bool:
    if not (MIN_LEN <= len(word) <= MAX_LEN):
        return False
    if VISUAL_NOISE.search(word):
        return False
    if word in GENERIC_NOISE:
        return False
    if UNSAFE_NOISE.search(word):
        return False
    if re.fullmatch(r"[0-9a-zA-Z_\-]+", word):
        return False
    return True


def build_from_metadata(metadata: dict, categories: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """从语义元数据里抽取每个分类的词。

    每个词只进入一个维度（:func:`bucket_word` 的优先级规则），避免提示词里
    同一句话重复四遍；同时按「该分类内出现次数」排序，取前若干个。
    """
    per_category: dict[str, Counter] = defaultdict(Counter)
    per_bucket: dict[str, dict[str, Counter]] = defaultdict(
        lambda: {key: Counter() for key in ("mood", "tone", "context", "intent")}
    )

    for record in metadata.values():
        if not isinstance(record, dict):
            continue
        category = str(record.get("category") or "").strip()
        if category not in categories:
            continue
        caption = str(record.get("caption") or "")
        for raw in record.get("tags") or []:
            word = clean_word(raw)
            if not word or not is_usable(word):
                continue
            per_category[category][word] += 1
            bucket = bucket_word(word, caption)
            if bucket:
                per_bucket[category][bucket][word] += 1

    result: dict[str, dict[str, list[str]]] = {}
    for category in categories:
        entry: dict[str, list[str]] = {}
        for key in ("mood", "tone", "context", "intent"):
            entry[key] = [word for word, _ in per_bucket[category][key].most_common(6)]
        if not any(entry.values()):
            entry["mood"] = [word for word, _ in per_category[category].most_common(5)]
        result[category] = entry
    return result


def build_pack_lexicon(
    pack_id: str,
    zip_path: Path,
    metadata_path: Path | None = None,
    *,
    out_dir: Path | None = None,
) -> Path:
    manifest = read_manifest(zip_path)
    categories = manifest.get("categories") or {}
    descriptions = {
        str(name): str((meta or {}).get("description") or "")
        if isinstance(meta, dict)
        else str(meta)
        for name, meta in categories.items()
    }

    observed: dict[str, dict[str, list[str]]] = {}
    if metadata_path and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        images = metadata.get("images") if isinstance(metadata, dict) else None
        if isinstance(images, dict):
            observed = build_from_metadata(images, descriptions)
            safe_print(f"从语义元数据提取：{len(images)} 条记录")

    categories_out: dict[str, dict] = {}
    for name, description in descriptions.items():
        merged = merge_category(name, observed.get(name))
        categories_out[name] = {
            "description": description,
            "mood": merged["mood"],
            "tone": merged["tone"],
            "context": merged["context"],
            "intent": merged["intent"],
            "source": "semantic_metadata+base" if observed.get(name) else "base",
        }

    payload = {
        "schema_version": 1,
        "pack_id": pack_id,
        "generated_from": {
            "manifest": str(zip_path.name),
            "semantic_metadata": str(metadata_path.name) if metadata_path else "",
        },
        "categories": categories_out,
    }

    target_dir = out_dir or (ROOT / "assets" / "category_lexicon")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{pack_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(f"写出词库：{target}（{len(categories_out)} 个分类）")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="生成表情包分类词库")
    parser.add_argument("--pack", required=True, help="表情包 ID（用于输出文件名）")
    parser.add_argument("--zip", required=True, type=Path, help="表情包 zip 路径")
    parser.add_argument("--metadata", type=Path, default=None, help="semantic_metadata.json 路径")
    parser.add_argument("--out", type=Path, default=None, help="输出目录")
    args = parser.parse_args()

    build_pack_lexicon(args.pack, args.zip, args.metadata, out_dir=args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
