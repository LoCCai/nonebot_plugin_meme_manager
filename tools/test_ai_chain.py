"""实测：AI 调用链路（视觉打标 / 语义检索 / 配图选拔）的协议契约。

不需要真实 API Key：用一个**本地伪造的 OpenAI 兼容服务**（aiohttp）验证
`nb2.llm.LLMContext.llm_generate` 发出的请求体与解析的响应是否符合上游
`backend/semantic/caption.py`、`backend/selection.py` 的预期协议。

运行：python tools/test_ai_chain.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / "tools"))
import _pkg_alias  # noqa: E402  （把本仓库插件包统一别名为 repo）
from _console import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()
_pkg_alias.install()

DATA_DIR = Path(tempfile.mkdtemp(prefix="meme_ai_"))
os.environ["MEME_MANAGER_DATA_DIR"] = str(DATA_DIR)

from repo.nb2 import bootstrap  # noqa: E402

bootstrap.ensure()

from repo.nb2.llm import LLMContext, ProviderConfig  # noqa: E402
from repo.nb2.runtime import MemeRuntime  # noqa: E402

# ---------------------------------------------------------------- 伪造服务
captured: list[dict[str, Any]] = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        captured.append(payload)
        messages = payload.get("messages") or []
        has_image = any(
            isinstance(item.get("content"), list)
            and any(part.get("type") == "image_url" for part in item["content"])
            for item in messages
        )
        has_tools = bool(payload.get("tools"))
        if has_tools:
            # 模拟模型调用「提交语义结果」工具
            arguments = {
                "caption": "开心地庆祝",
                "tags": ["开心", "庆祝"],
                "visible_text": "",
                "category_fit": "match",
                "category_review_reason": "",
                "suggested_category": "",
            }
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "submit_meme_caption",
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            }
        elif payload.get("response_format", {}).get("type") == "json_object":
            message = {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "caption": "开心地庆祝",
                        "tags": ["开心"],
                        "visible_text": "",
                        "category_fit": "match",
                        "category_review_reason": "",
                        "suggested_category": "",
                    },
                    ensure_ascii=False,
                ),
            }
        else:
            message = {"role": "assistant", "content": "你好呀"}
        body = json.dumps(
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": payload.get("model", "fake"),
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
                "_debug": {"has_image": has_image, "has_tools": has_tools},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # 静默
        return


def start_server() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


server, base_url = start_server()
print(f"[1] 伪造 OpenAI 兼容服务已启动：{base_url}")

divider = "=" * 60


async def main() -> None:
    context = LLMContext(
        [
            ProviderConfig(
                id="vision",
                model="fake-vision",
                base_url=base_url,
                api_key="test-key",
                modalities=["text", "image"],
            ),
            ProviderConfig(
                id="chat",
                model="fake-chat",
                base_url=base_url,
                api_key="test-key",
                modalities=["text"],
            ),
        ]
    )
    runtime = MemeRuntime(data_dir=DATA_DIR, context=context)
    print(f"[2] 运行时已构建，提供方：{[p.id for p in context.provider_manager.all()]}")

    # ---- 1) 纯文本生成
    print(divider)
    response = await context.llm_generate(
        chat_provider_id="chat", prompt="你好", temperature=0, max_tokens=50
    )
    assert response.completion_text == "你好呀", response.completion_text
    assert response.usage.get("total_tokens") == 18
    last = captured[-1]
    assert last["messages"][0]["content"] == "你好"
    assert last["model"] == "fake-chat"
    print(f"[3] 纯文本生成 OK：{response.completion_text!r}，usage={response.usage}")

    # ---- 2) 图片输入（data URL）
    print(divider)
    image_path = DATA_DIR / "sample.png"
    from PIL import Image as PILImage

    PILImage.new("RGB", (4, 4), (0, 128, 255)).save(image_path)
    response = await context.llm_generate(
        chat_provider_id="vision",
        prompt="描述这张图",
        image_urls=[str(image_path)],
        system_prompt="你是图片描述助手",
    )
    last = captured[-1]
    user_content = last["messages"][-1]["content"]
    assert isinstance(user_content, list), user_content
    assert user_content[0]["type"] == "text"
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert last["messages"][0]["role"] == "system"
    print(f"[4] 图片输入 OK：system={last['messages'][0]['content']!r}，"
          f"image 段={user_content[1]['image_url']['url'][:40]}…")

    # ---- 3) 视觉打标：工具模式协议（tools + tool_choice）
    print(divider)
    from repo.backend.semantic.caption import (
        CAPTION_TOOL_NAME,
        _build_caption_tool_set,
        _caption_tool_call_payload,
        build_caption_prompt,
        generate_caption,
    )

    tool_set = _build_caption_tool_set(["happy", "sad"])
    tool_names = [getattr(t, "name", "") for t in tool_set]
    print(f"[5] 工具集构造 OK：{tool_names}")
    assert CAPTION_TOOL_NAME in tool_names

    # 伪造服务对 tools 请求返回工具调用，验证上游解析链路
    raw = await context.llm_generate(
        chat_provider_id="vision",
        prompt=build_caption_prompt(1, "happy", "开心", {"happy": "开心"}),
        image_urls=[str(image_path)],
        tools=tool_set,
        tool_choice="required",
    )
    last = captured[-1]
    print(f"[6] 工具模式请求：tools={len(last.get('tools') or [])}，"
          f"tool_choice={last.get('tool_choice')}，含图={last['_debug'] if '_debug' in last else 'n/a'}")
    has_tool, payload = _caption_tool_call_payload(raw)
    assert has_tool, "上游应从 tool_calls 解析出参数"
    assert isinstance(payload, dict) and payload.get("caption") == "开心地庆祝"
    print(f"[7] 工具结果解析 OK：{payload}")

    # 完整 generate_caption（走 tool 模式）
    result = await generate_caption(
        context, image_path, "vision", category="happy",
        category_description="开心", available_categories={"happy": "开心"},
    )
    print(
        f"[8] generate_caption OK：caption={result['caption']!r} "
        f"tags={result['tags']} model={result['vision_model']} "
        f"tokens={result['token_usage']}"
    )
    assert result["caption"] and result["vision_model"] == "vision"

    # ---- 4) JSON 模式回退
    print(divider)
    from repo.backend.semantic.caption import _request_caption_json_response

    json_response = await _request_caption_json_response(
        context, "vision", "描述", [str(image_path)]
    )
    last = captured[-1]
    assert last.get("response_format", {}).get("type") == "json_object"
    from repo.backend.semantic.caption import _caption_response_payload, parse_caption_result_with_review

    parsed = parse_caption_result_with_review(_caption_response_payload(json_response))
    print(f"[9] JSON 回退模式 OK：response_format={last['response_format']}，解析={parsed[0]!r}")

    # ---- 5) 语义选拔（selection.py 的 chat 后端）
    print(divider)
    from repo.backend.selection import SelectionTask, select_candidates

    from repo.nb2.llm import LLMResponse

    async def generate(prompt: str) -> LLMResponse:
        return LLMResponse(completion_text=json.dumps({"selected_ids": ["aaa111"]}))

    selection = await select_candidates(
        SelectionTask(
            kind="semantic",
            reply="想表达开心",
            candidates=[{"id": "aaa111", "category": "happy", "caption": "开心"}],
        ),
        backend="chat",
        generate=generate,
        decision_client=None,
    )
    print(f"[10] 选拔链路 OK：status={selection.status} ids={selection.selected_ids}")
    assert selection.status == "selected" and selection.selected_ids == ["aaa111"]

    # ---- 6) 语义检索（无索引时应明确拒绝，而不是崩溃）
    print(divider)
    from repo.nb2.tools import build_default_registry
    from repo.nb2.platform.event import MessageObj, UnifiedEvent
    from repo.nb2.platform.message import Segment

    event = UnifiedEvent(
        platform_name="onebot11", session_type="group", session_id="555",
        sender_id="1001", message_str="开心", message_obj=MessageObj(message=[Segment.text("开心")]),
    )
    registry = build_default_registry(runtime)
    tool_result = await registry.get("search_memes").call(event, runtime, {"query": "开心"})
    print(f"[11] search_memes 无索引时：ok={tool_result.ok} msg={tool_result.message}")
    assert tool_result.ok is False and "语义索引" in tool_result.message

    print(divider)
    print(f"共捕获 {len(captured)} 次模型请求。")
    close = getattr(context, "close", None)
    if callable(close):
        await close()
    print("AI 调用链路实测通过。")


try:
    asyncio.run(main())
finally:
    server.shutdown()
    import asyncio as _aio
    _aio.run(asyncio.sleep(0)) if False else None
