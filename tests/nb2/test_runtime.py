"""运行时与配置存储测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo.nb2.config_store import PluginConfig, build_defaults, schema_default
from repo.nb2.paths_bind import bind_module_paths


# ---------------------------------------------------------------- schema
def test_schema_default_nested_object():
    schema = {
        "type": "object",
        "items": {
            "enabled": {"type": "bool", "default": True},
            "nested": {
                "type": "object",
                "items": {"count": {"type": "int", "default": 3}},
            },
        },
    }
    assert schema_default(schema) == {"enabled": True, "nested": {"count": 3}}


def test_build_defaults_from_group_schema():
    schema = {
        "group": {
            "type": "object",
            "items": {"flag": {"type": "bool", "default": False}},
        }
    }
    assert build_defaults(schema) == {"group": {"flag": False}}


# ---------------------------------------------------------------- 配置读写
@pytest.fixture()
def config(tmp_path):
    schema = {
        "semantic": {
            "type": "object",
            "items": {
                "enabled": {"type": "bool", "default": False},
                "top_k": {"type": "int", "default": 5},
            },
        },
        "storage": {
            "type": "object",
            "items": {
                "provider": {
                    "type": "string",
                    "default": "stardots",
                    "options": ["stardots", "lsky"],
                },
                "providers": {"type": "object", "items": {}},
            },
        },
    }
    path = tmp_path / "config.json"
    return PluginConfig.load(path, schema=schema)


def test_config_uses_schema_defaults(config):
    assert config.read(("semantic", "enabled")) is False
    assert config.read(("semantic", "top_k")) == 5
    assert config.read(("storage", "provider")) == "stardots"


def test_config_missing_path_returns_default(config):
    assert config.read(("semantic", "missing"), "fallback") == "fallback"
    assert config.read(("unknown", "group"), None) is None


def test_config_ignores_unknown_keys_on_load(tmp_path):
    schema = {"a": {"type": "object", "items": {"x": {"type": "int", "default": 1}}}}
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"a": {"x": 9, "bogus": 1}, "top_level_bogus": True}), encoding="utf-8"
    )
    config = PluginConfig.load(path, schema=schema)
    assert config.read(("a", "x")) == 9
    assert "bogus" not in config["a"]
    assert "top_level_bogus" not in config


def test_config_options_violation_falls_back(tmp_path):
    schema = {
        "storage": {
            "type": "object",
            "items": {
                "provider": {
                    "type": "string",
                    "default": "stardots",
                    "options": ["stardots"],
                }
            },
        }
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"storage": {"provider": "invalid"}}), encoding="utf-8")
    config = PluginConfig.load(path, schema=schema)
    assert config.read(("storage", "provider")) == "stardots"


def test_config_write_and_save_roundtrip(config):
    assert config.update_path(("semantic", "enabled"), True) is True
    reloaded = PluginConfig.load(config.path, schema=config.schema)
    assert reloaded.read(("semantic", "enabled")) is True


def test_config_load_missing_schema_file(tmp_path):
    config = PluginConfig.load(tmp_path / "c.json", schema_path=tmp_path / "nope.json")
    assert dict(config) == {}
    assert config.path.is_file()


# ---------------------------------------------------------------- 路径绑定
def test_bind_module_paths_updates_upstream_constants(tmp_path):
    import importlib

    module = importlib.import_module("repo.config")
    assert bind_module_paths(tmp_path) is True
    assert module.PACKS_DIR == tmp_path / "packs"
    assert module.TEMP_DIR == tmp_path / "temp"
    assert module.SEMANTIC_INDEXES_DIR == tmp_path / "semantic_indexes"


# ---------------------------------------------------------------- 运行时
def test_runtime_bootstraps_data_layout(tmp_path):
    from repo.nb2.llm import LLMContext, ProviderConfig
    from repo.nb2.runtime import MemeRuntime

    runtime = MemeRuntime(data_dir=tmp_path, context=LLMContext([ProviderConfig(id="x")]))
    assert runtime.packs_dir.is_dir()
    assert (runtime.packs_dir / "builtin-default" / "manifest.json").is_file()
    assert runtime.registry_path.is_file()
    assert runtime.selection_rules_path.is_file()
    assert runtime.config.path.parent == tmp_path


def test_runtime_reads_config_values(tmp_path):
    from repo.nb2.config_store import PluginConfig
    from repo.nb2.runtime import MemeRuntime

    config = PluginConfig(tmp_path / "config.json", schema={}, data={})
    config.update_path(("semantic", "enabled"), True)
    config.update_path(("semantic", "top_k"), 8)
    runtime = MemeRuntime(plugin_config=config, data_dir=tmp_path)
    assert runtime.semantic_enabled is True
    assert runtime.semantic_top_k == 8
    assert runtime._read_config_value(("semantic", "missing"), "d") == "d"


def test_runtime_prompt_building(tmp_path):
    from repo.nb2.config_store import PluginConfig
    from repo.nb2.runtime import MemeRuntime

    config = PluginConfig(tmp_path / "config.json", schema={}, data={})
    config.update_path(("generation", "prompt", "head"), "HEAD\n")
    config.update_path(("generation", "prompt", "tail"), "\nTAIL")
    runtime = MemeRuntime(plugin_config=config, data_dir=tmp_path)
    prompt = runtime._build_meme_prompt("分类")
    assert prompt.startswith("HEAD\n分类\nTAIL")

    wrapped = runtime._wrap_meme_prompt("x")
    assert runtime._strip_meme_prompt(wrapped) == ""


def test_runtime_trigger_scope_normalization(tmp_path):
    from repo.nb2.runtime import MemeRuntime, normalize_trigger_scope

    assert normalize_trigger_scope("ALL_LLM") == "all_llm"
    assert normalize_trigger_scope("bogus") == "only_chat_llm"
    assert normalize_trigger_scope(None) == "only_chat_llm"
    runtime = MemeRuntime(data_dir=tmp_path)
    assert runtime.trigger_scope == "only_chat_llm"


def test_runtime_clamp_int():
    from repo.nb2.runtime import MemeRuntime

    assert MemeRuntime._clamp_int("7", 0, 20) == 7
    assert MemeRuntime._clamp_int("99", 0, 20) == 20
    assert MemeRuntime._clamp_int("bad", 3, 20) == 3


def test_runtime_categories_have_descriptions(tmp_path):
    from repo.nb2.runtime import MemeRuntime

    runtime = MemeRuntime(data_dir=tmp_path)
    assert "happy" in runtime.category_mapping
    assert runtime.category_mapping_string
