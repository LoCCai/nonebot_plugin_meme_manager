"""MoEllmChats 集成模块的离线测试（未安装依赖时也应安全）。"""

from __future__ import annotations

import sys
import types

import pytest

from repo.nb2.integrations import moellm


def test_register_without_dependency_is_noop(monkeypatch):
    """未安装 MoEllmChats 时注册应返回 False 且不抛异常。"""
    monkeypatch.setitem(sys.modules, "nonebot_plugin_moellmchats", None)
    moellm._REGISTERED = False
    assert moellm.register() is False
    assert moellm._REGISTERED is False


class _FakeToolRegistry:
    def __init__(self) -> None:
        self.registered: dict[str, object] = {}

    def get(self, name: str):
        return self.registered.get(name)

    def register(self, spec):
        self.registered[spec.name] = spec
        return spec

    def unregister(self, name: str) -> None:
        self.registered.pop(name, None)


@pytest.fixture()
def fake_moellm(monkeypatch):
    """注入一个最小的 MoEllmChats 模块表面。"""
    registry = _FakeToolRegistry()

    class ToolEffect:
        READ_ONLY = "read_only"
        MUTATING = "mutating"

    class ToolResult:
        def __init__(self, text: str = "", structured=None, **kwargs):
            self.text = text
            self.structured = structured or {}

    class ToolSpec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    module = types.ModuleType("nonebot_plugin_moellmchats")
    module.ToolEffect = ToolEffect
    module.ToolResult = ToolResult
    module.ToolSpec = ToolSpec
    module.register_tool = registry.register

    contracts = types.ModuleType("nonebot_plugin_moellmchats.tool_contracts")
    contracts.tool_registry = registry

    monkeypatch.setitem(sys.modules, "nonebot_plugin_moellmchats", module)
    monkeypatch.setitem(sys.modules, "nonebot_plugin_moellmchats.tool_contracts", contracts)
    moellm._REGISTERED = False
    yield registry, ToolSpec
    moellm._REGISTERED = False


def test_register_creates_two_tools(fake_moellm):
    registry, _ = fake_moellm
    assert moellm.register() is True
    assert set(registry.registered) == {moellm.SEARCH_TOOL_NAME, moellm.SEND_TOOL_NAME}

    search = registry.registered[moellm.SEARCH_TOOL_NAME]
    send = registry.registered[moellm.SEND_TOOL_NAME]
    assert search.effect == "read_only"
    assert send.effect == "mutating"
    assert search.parameters["additionalProperties"] is False
    assert search.parameters["required"] == ["query"]
    assert send.parameters["required"] == ["ids"]


def test_register_is_idempotent_and_unregister_cleans(fake_moellm):
    registry, _ = fake_moellm
    assert moellm.register() is True
    first = dict(registry.registered)
    assert moellm.register() is True  # 幂等：不新增也不报错
    assert set(registry.registered) == set(first)

    moellm.unregister()
    assert registry.registered == {}


@pytest.mark.asyncio
async def test_search_handler_requires_runtime(fake_moellm, monkeypatch):
    """运行时未初始化时工具应返回明确的提示文本，而不是抛异常。"""
    registry, _ = fake_moellm
    moellm.register()
    spec = registry.registered[moellm.SEARCH_TOOL_NAME]

    from repo.nb2 import state

    monkeypatch.setattr(state, "_runtime", None)
    result = await spec.handler(
        query="开心", _tool_context=types.SimpleNamespace(event=None, bot=None)
    )
    assert "尚未初始化" in result.text
