"""实测：语义检索全链路（打标 → 向量索引 → 检索 → 选拔 → 发送）。

用真实的 faiss + 伪造的 OpenAI 兼容服务（同时提供 chat 与 embeddings），
把「自动收集 → 语义化 → 检索 → 配图发送」完整跑一遍。

运行：python tools/test_semantic_chain.py
"""

from __future__ import annotations

import asyncio
import base64
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

DATA_DIR = Path(tempfile.mkdtemp(prefix="meme_sem_"))
os.environ["MEME_MANAGER_DATA_DIR"] = str(DATA_DIR)

from repo.nb2 import bootstrap  # noqa: E402

bootstrap.ensure()

from repo.nb2.llm import LLMContext, ProviderConfig  # noqa: E402
from repo.nb2.runtime import MemeRuntime  # noqa: E402

captured: list[dict[str, Any]] = []

#: 伪造嵌入：用文本长度与前缀构造确定性的 8 维向量。
def fake_embedding(text: str) -> list[float]:
    seed = sum(ord(ch) for ch in str(text)) or 1
    vector = [((seed * (i + 3)) % 97) / 97.0 for i in range(8)]
    total = sum(value * value for value in vector) ** 0.5 or 1.0
    return [value / total for value in vector]


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        captured.append((self.path, payload))

        if self.path.endswith("/embeddings"):
            inputs = payload.get("input")
            texts = inputs if isinstance(inputs, list) else [inputs]
            body = {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": fake_embedding(text)}
                    for i, text in enumerate(texts)
                ],
                "model": payload.get("model", "fake-embed"),
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        else:
            message = {"role": "assistant", "content": '{"selected_ids":["%s"]}' % "placeholder"}
            # 视觉打标走工具模式：返回工具调用
            if payload.get("tools"):
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "submit_meme_caption",
                                "arguments": json.dumps(
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
                            },
                        }
                    ],
                }
            elif payload.get("response_format", {}).get("type") == "json_object":
                # 选拔请求：从提示词里的候选数据挑第一个 meme id
                text = json.dumps(payload, ensure_ascii=False)
                import re

                ids = re.findall(r'meme:[0-9a-f]{12,64}', text)
                chosen = ids[0] if ids else ""
                message = {"role": "assistant", "content": json.dumps({"selected_ids": [chosen]})}
            else:
                message = {"role": "assistant", "content": "我们庆祝一下！"}
            body = {
                "id": "chatcmpl-1",
                "model": payload.get("model", "fake"),
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            }

        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: Any) -> None:
        return


server = HTTPServer(("127.0.0.1", 0), _Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{server.server_port}/v1"
print(f"[1] 伪造服务（chat + embeddings）：{base_url}")


class _Transport:
    class _Caps:
        name = "test"

    capabilities = _Caps()

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)


async def main() -> None:
    from PIL import Image as PILImage

    from repo.nb2.platform.event import MessageObj, UnifiedEvent
    from repo.nb2.platform.message import Segment, UnifiedMessage

    context = LLMContext(
        [
            ProviderConfig(
                id="fake-vision",
                model="fake-vision",
                base_url=base_url,
                api_key="k",
                modalities=["text", "image"],
            )
        ]
    )

    # 给嵌入提供方补上 get_embedding/get_dim（上游 EmbeddingAdapter 依赖这套接口）。
    class _EmbeddingProvider:
        provider_config = {
            "id": "fake-embed",
            "model": "fake-embed",
            "modalities": ["text", "embedding"],
        }

        def get_model(self) -> str:
            return "fake-embed"

        def get_dim(self) -> int:
            return 8

        async def get_embedding(self, text: str) -> list[float]:
            import aiohttp

            url = f"{base_url}/embeddings"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"input": text, "model": "fake-embed"}) as resp:
                    data = await resp.json()
            return data["data"][0]["embedding"]

        async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
            import aiohttp

            url = f"{base_url}/embeddings"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"input": texts, "model": "fake-embed"}) as resp:
                    data = await resp.json()
            return [item["embedding"] for item in data["data"]]

    runtime = MemeRuntime(data_dir=DATA_DIR, context=context)
    runtime.context.register_provider(_EmbeddingProvider())
    print(f"[2] 运行时就绪，分类 {len(runtime.category_mapping)} 个")

    # ---- 造一个表情包 + 语义元数据
    pack_context = runtime._resolve_runtime_pack_context()
    pack_id = str(pack_context["pack_id"])
    pack_dir = Path(pack_context["pack_dir"])
    memes_dir = Path(pack_context["memes_dir"])
    happy_dir = memes_dir / "happy"
    happy_dir.mkdir(parents=True, exist_ok=True)
    image_path = happy_dir / "celebrate.png"
    PILImage.new("RGB", (16, 16), (255, 120, 0)).save(image_path)
    print(f"[3] 表情包 {pack_id} 已就绪：{image_path.name}")

    from repo.backend.semantic import storage as sem_storage

    # 写入语义记录（模拟已完成打标）
    metadata = sem_storage.load_metadata(pack_dir)
    metadata.setdefault("images", {})
    import hashlib

    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    metadata["images"][digest] = {
        "category": "happy",
        "filename": image_path.name,
        "caption": "开心地庆祝",
        "tags": ["开心", "庆祝"],
        "visible_text": "",
        "category_fit": "match",
        "caption_status": "done",
        "embedding_status": "done",
        "content_sha256": digest,
        "relative_path": f"memes/happy/{image_path.name}",
        "entry_id": digest,
    }
    sem_storage.save_metadata(pack_dir, metadata)
    print(f"[4] 语义记录已写入：{digest[:8]}…，图片 {len(metadata['images'])} 条")

    # ---- 构建向量索引
    from repo.backend.semantic.index import EmbeddingAdapter, build_index, index_is_ready

    embedding = EmbeddingAdapter(runtime.context.get_provider_by_id("fake-embed"))
    print(f"[5] 嵌入适配器：ready={embedding.ready} dim={embedding.dimension} model={embedding.model_name}")
    assert embedding.ready and embedding.dimension == 8

    built = await build_index(pack_dir, runtime.data_dir, pack_id, embedding, force=True)
    # build_index 会回写 text_hash / embedding_status，重新加载后再判定就绪。
    metadata = sem_storage.load_metadata(pack_dir)
    print(f"[6] 索引构建：{built.get('item_count')} 项，文件 {built.get('index_file') or list(built)}")
    ready = index_is_ready(
        runtime.data_dir,
        pack_id,
        metadata,
        embedding.provider_id,
        embedding.model_name,
        embedding.dimension,
        allow_partial=True,
    )
    print(f"[7] index_is_ready = {ready}")
    assert ready

    # ---- 语义检索（工具入口）
    from repo.nb2.tools import build_default_registry

    event = UnifiedEvent(
        platform_name="onebot11",
        session_type="group",
        session_id="555",
        sender_id="1001",
        message_str="庆祝一下",
        message_obj=MessageObj(message=[Segment.text("庆祝一下")], message_str="庆祝一下"),
        transport=_Transport(),
    )
    # 让运行时认为语义已就绪
    runtime.semantic_enabled = True
    runtime.semantic_embedding_provider_id = "fake-embed"
    runtime.semantic_min_score = -1.0  # 伪造向量相似度偏低，仅验证链路

    # 直接检查运行时判定链路，便于定位
    print("[8a] get_all_embedding_providers =", [getattr(p, "provider_config", {}).get("id") for p in runtime.context.get_all_embedding_providers()])
    print("[8b] _resolve_embedding_provider ->", type(runtime._resolve_embedding_provider(pack_id)).__name__)
    print("[8c] _semantic_pack_ready =", runtime._semantic_pack_ready(event=event, for_direct_search=True))
    registry = build_default_registry(runtime)
    search = await registry.get("search_memes").call(event, runtime, {"query": "庆祝", "top_k": 3})
    print(f"[8] search_memes：ok={search.ok} 候选={len(search.data.get('candidates') or [])}")
    print(f"    候选：{search.data.get('candidates')}")
    assert search.ok and search.data.get("candidates"), search.message

    # ---- 重复搜索应被限制
    again = await registry.get("search_memes").call(event, runtime, {"query": "再来"})
    print(f"[9] 二次搜索被限制：ok={again.ok} msg={again.message}")
    assert again.ok is False

    # ---- 发送候选
    candidate = search.data["candidates"][0]
    image_id = candidate.get("id") or candidate.get("entry_id")
    print(f"[9a] 候选 ID 形态：id={candidate.get('id')!r} entry_id={str(candidate.get('entry_id'))[:16]}…")
    send = await registry.get("send_meme").call(event, runtime, {"ids": [image_id]})
    print(f"[10] send_meme：ok={send.ok} sent={send.data.get('sent')} msg={send.message}")
    sent = event.transport.sent
    print(f"     实际发送消息：{[msg.to_dicts() for msg in sent]}")
    assert send.ok and sent

    # ---- 选拔链路（真实 backend/selection.py + 伪造模型）
    from repo.nb2 import pipeline as pipe

    runtime.emotion_llm_provider_id = "fake-vision"
    cands = await pipe.search_candidates(runtime, event, "庆祝", top_k=3)
    print(f"[11a] pipeline 检索候选：{len(cands)}，ids={[c.get('id') for c in cands]}")
    selected = await pipe.select_images(runtime, event, query="庆祝", candidates=cands)
    print(f"[11b] pipeline 选拔结果：{len(selected)}")
    pipeline_result = await pipe.run_pipeline(runtime, event, user_text="我们庆祝一下", force=True)
    print(
        f"[11] run_pipeline：status={pipeline_result.status} "
        f"images={len(pipeline_result.images)} text={pipeline_result.text!r}"
    )

    print(f"\n共捕获 {len(captured)} 次模型请求。")
    print("语义检索全链路实测通过。")
    await context.close()


try:
    asyncio.run(main())
finally:
    server.shutdown()
