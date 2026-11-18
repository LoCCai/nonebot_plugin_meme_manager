"""LLM 提供方适配层测试（不发起真实网络请求）。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from repo.nb2.llm import (
    LLMContext,
    ProviderConfig,
    ProviderManager,
    providers_from_config,
)


def test_provider_config_exposes_modalities():
    provider = ProviderConfig(id="v1", model="gpt-4o", modalities=["text", "image"])
    assert provider.provider_config["modalities"] == ["text", "image"]
    assert provider.supports_vision is True

    text_only = ProviderConfig(id="t1", model="gpt-4", modalities=["text"])
    assert text_only.supports_vision is False


def test_provider_manager_inst_map_is_shared():
    provider = ProviderConfig(id="p1")
    manager = ProviderManager([provider])
    assert manager.inst_map is manager._providers
    assert "p1" in manager
    assert manager.get("p1") is provider
    assert manager.get("missing") is None
    assert len(manager) == 1


def test_context_get_provider_by_id():
    context = LLMContext([ProviderConfig(id="vision", modalities=["text", "image"])])
    assert context.has_providers() is True
    assert context.get_provider_by_id("vision").supports_vision is True
    assert context.get_provider_by_id("nope") is None


def test_providers_from_config_variants():
    providers = providers_from_config(
        {
            "providers": [
                {"id": "vision", "model": "gpt-4o-mini", "api_key": "k", "modalities": ["image"]},
                {"name": "text-only", "model": "gpt-4"},
                "not-a-dict",
                {},
            ]
        }
    )
    ids = [provider.id for provider in providers]
    assert ids == ["vision", "text-only"]
    assert providers[0].api_key == "k"
    assert providers[1].modalities == ["text"]


def test_providers_from_config_mapping_form():
    providers = providers_from_config(
        {"providers": {"named": {"model": "m", "vision": True}}}
    )
    assert len(providers) == 1
    assert providers[0].id == "named"
    assert providers[0].modalities == ["text", "image"]


def test_providers_from_config_invalid_input():
    assert providers_from_config(None) == []
    assert providers_from_config({"providers": 42}) == []


def test_build_messages_with_local_image(tmp_path):
    image_path = tmp_path / "a.png"
    image_path.write_bytes(b"\x89PNG")
    context = LLMContext([ProviderConfig(id="v")])
    messages = context._build_messages(
        {
            "system_prompt": "系统",
            "prompt": "描述",
            "image_urls": [str(image_path), "https://example.com/b.png"],
        }
    )
    assert messages[0] == {"role": "system", "content": "系统"}
    content = messages[1]["content"]
    assert content[0]["text"] == "描述"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[2]["image_url"]["url"] == "https://example.com/b.png"


def test_build_messages_skips_missing_and_empty_images():
    context = LLMContext([ProviderConfig(id="v")])
    messages = context._build_messages(
        {"prompt": "p", "image_urls": [r"C:\nope\missing.png", ""]}
    )
    assert messages == [{"role": "user", "content": "p"}]


def test_build_messages_plain_text_without_images():
    context = LLMContext([ProviderConfig(id="v")])
    messages = context._build_messages({"prompt": "hello"})
    assert messages == [{"role": "user", "content": "hello"}]


def test_image_url_from_bytes():
    context = LLMContext([ProviderConfig(id="v")])
    url = context._to_image_url(b"\x01\x02")
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"\x01\x02"


def test_parse_response_variants():
    parse = LLMContext._parse_response
    assert parse({"choices": [{"message": {"content": "hi"}}]}).completion_text == "hi"
    parts = {"choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]}
    assert parse(parts).completion_text == "ab"
    assert parse({}).completion_text == ""
    assert parse({"usage": {"total_tokens": 3}}).usage == {"total_tokens": 3}


@pytest.mark.asyncio
async def test_llm_generate_unknown_provider_raises():
    context = LLMContext([ProviderConfig(id="known")])
    with pytest.raises(ValueError):
        await context.llm_generate(chat_provider_id="unknown", prompt="x")
