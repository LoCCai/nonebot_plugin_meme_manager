"""收集器使用生产 LLM 适配层，仅替换 HTTP 传输。"""

import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from repo.backend import auto_collect
from repo.nb2.llm import LLMContext, ProviderConfig


@pytest.fixture
def collector(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_collect, "AUTO_COLLECT_TEMP_DIR", tmp_path)
    monkeypatch.setattr(auto_collect, "AUTO_COLLECT_STATE_PATH", tmp_path / "state.json")
    context = LLMContext([ProviderConfig(id="vision", model="deepseek-v4.1-flash")])
    manager = auto_collect.AutoCollectManager(
        SimpleNamespace(context=context), {"vision_provider_id": "vision"}
    )
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buffer, format="PNG")
    return manager, context, buffer.getvalue()


@pytest.mark.parametrize("model", ["deepseek-v4.1-flash", "another-vision-model"])
async def test_classification_and_understanding_request_settings(collector, model):
    manager, context, image = collector
    context.get_provider_by_id("vision").model = model
    answer = json.dumps({
        "is_meme": True, "meme_confidence": 0.9, "category": "happy",
        "category_confidence": 0.9, "tags": ["joy"], "caption": "A smile",
        "chat_use_evidence": "Expresses joy",
    })
    context._post = AsyncMock(return_value={"choices": [{"message": {"content": answer}}]})
    classification = await manager._classify(image, ".png", {"happy": "smiling"})
    assert classification["category"] == "happy"
    understanding = await manager._understand(image, ".png")
    assert understanding["tags"] == ["joy"]
    for call in context._post.await_args_list:
        wire = call.args[1]
        assert wire.get("thinking") == ({"type": "disabled"} if model == "deepseek-v4.1-flash" else None)
        assert wire["response_format"] == {"type": "json_object"}
    await context.llm_generate(chat_provider_id="vision", prompt="hello")
    assert "thinking" not in context._post.await_args.args[1]


@pytest.mark.parametrize(("content", "reason", "error"), [
    ('{"is_meme":false}', "length", "finish_reason=length, completion_tokens=1400"),
    (None, "stop", "未返回完整 JSON"),
    ([{"type": "reasoning", "text": '{"is_meme":false}'}], "stop", "未返回完整 JSON"),
    ("{}", "stop", "is_meme"),
    ('{"is_meme":true}', "stop", "meme_confidence"),
])
async def test_incomplete_decision_is_retryable(collector, content, reason, error, tmp_path, monkeypatch):
    manager, context, image = collector
    context._post = AsyncMock(return_value={
        "choices": [{"message": {"content": content, "reasoning_content": '{"is_meme":false}'},
                     "finish_reason": reason}],
        "usage": {"completion_tokens": 1400},
    })
    monkeypatch.setattr(auto_collect, "AUTO_COLLECT_INBOX_METADATA_PATH", tmp_path / "inbox.json")
    monkeypatch.setattr(manager, "_pack_contains_digest", lambda *_: False)
    path = tmp_path / "image.png"
    path.write_bytes(image)
    job = auto_collect.AutoCollectJob(path, "sample", {"happy": "smiling"}, "user", "test")
    with pytest.raises(ValueError, match=error):
        await manager._process_job(job)
    assert not manager._state["decisions"]
    assert context._post.await_count == (2 if reason == "length" else 1)

@pytest.mark.parametrize("operation", ["classify", "understand"])
async def test_truncation_increases_budget_once_and_preserves_request(collector, operation):
    manager, context, image = collector
    answer = {"is_meme": False, "caption": "blue", "tags": ["square"], "chat_use_evidence": "plain"}
    context._post = AsyncMock(side_effect=[
        {"choices": [{"finish_reason": "length", "message": {"content": ""}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]},
    ])
    if operation == "classify":
        assert not (await manager._classify(image, ".png", {}))["is_meme"]
    else:
        assert (await manager._understand(image, ".png"))["tags"] == ["square"]
    first, retry = (call.args[1] for call in context._post.await_args_list)
    assert retry == {**first, "max_tokens": first["max_tokens"] * 2}
    assert manager._state["recognition_count"] == 1  # reservation for the extra call


async def test_truncation_respects_daily_quota(collector):
    manager, context, image = collector
    manager.daily_recognition_limit = 1
    assert await manager._daily_call_available()
    context._post = AsyncMock(return_value={"choices": [{"finish_reason": "length"}]})
    with pytest.raises(ValueError, match="finish_reason=length"):
        await manager._classify(image, ".png", {})
    assert context._post.await_count == 1
