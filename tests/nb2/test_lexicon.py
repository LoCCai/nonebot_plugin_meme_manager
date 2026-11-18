"""词库功能测试：加载、合并、渲染、生成。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo.nb2 import lexicon as lx


def test_base_lexicon_has_all_standard_categories():
    expected = {
        "angry",
        "happy",
        "sad",
        "surprised",
        "confused",
        "color",
        "cpu",
        "fool",
        "givemoney",
        "like",
        "see",
        "shy",
        "work",
        "reply",
        "meow",
        "baka",
        "morning",
        "sleep",
        "sigh",
    }
    assert expected <= set(lx.BASE_LEXICON)
    for category, entry in lx.BASE_LEXICON.items():
        assert entry["mood"], category
        assert entry["intent"], category


def test_base_lexicon_returns_copy():
    first = lx.base_lexicon_for("angry")
    first["mood"].append("被篡改")
    assert "被篡改" not in lx.BASE_LEXICON["angry"]["mood"]


def test_merge_category_prefers_observed_words():
    merged = lx.merge_category(
        "happy", {"mood": ["狂喜", "开心"], "tone": [], "context": [], "intent": []}
    )
    assert merged["mood"][0] == "狂喜"
    assert merged["mood"].count("开心") == 1
    # 基础词表里 tone/context/intent 仍然可用
    assert merged["intent"]


def test_merge_category_without_observed_returns_base():
    merged = lx.merge_category("sad", None)
    assert merged["mood"] == lx.BASE_LEXICON["sad"]["mood"]


def test_render_hint_lines_shape():
    lines = lx.render_hint_lines(
        {"happy": "开心", "angry": "生气"},
        {"happy": {"mood": ["狂喜"], "tone": [], "context": [], "intent": ["庆祝"]}},
        max_items=3,
    )
    assert "happy" in lines
    assert "狂喜" in lines
    assert "心情：狂喜" in lines
    assert lines.count("\n") >= 1


def test_render_hint_lines_skips_unknown_categories():
    assert lx.render_hint_lines({"nonexistent": "无"}, {}, max_items=3) == ""


def test_load_pack_lexicon_missing_returns_empty(tmp_path):
    assert lx.load_pack_lexicon("no-such-pack", tmp_path) == {}


def test_load_pack_lexicon_runtime_override(tmp_path):
    target = lx.runtime_path(tmp_path, "demo-pack")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "categories": {
                    "happy": {
                        "mood": ["狂喜"],
                        "tone": ["欢呼"],
                        "context": ["中奖"],
                        "intent": ["庆祝"],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = lx.load_pack_lexicon("demo-pack", tmp_path)
    assert loaded["happy"]["mood"] == ["狂喜"]
    assert loaded["happy"]["context"] == ["中奖"]


def test_load_for_packs_merges(tmp_path):
    for pack_id, word in (("pack-a", "甲"), ("pack-b", "乙")):
        target = lx.runtime_path(tmp_path, pack_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"categories": {"same": {"mood": [word]}}}, ensure_ascii=False),
            encoding="utf-8",
        )
    merged = lx.load_for_packs(["pack-a", "pack-b"], tmp_path)
    assert merged["same"]["mood"] == ["乙"]  # 后者覆盖


def test_asset_lexicon_ships_with_official_pack():
    """仓库内应随附官方包词库（由 tools/build_lexicon.py 生成）。"""
    path = lx.asset_path("official-001")
    if not path.is_file():  # pragma: no cover - 允许未生成的开发环境
        pytest.skip("未生成 official-001 词库")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pack_id"] == "official-001"
    categories = payload["categories"]
    assert len(categories) >= 19
    for name, entry in categories.items():
        assert entry["mood"] or entry["intent"], name


def test_lexicon_enabled_defaults_true():
    class _Runtime:
        def _read_config_value(self, path, default=None):
            return default

    assert lx.lexicon_enabled(_Runtime()) is True
