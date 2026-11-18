"""候选 ID 解析回归测试（semantic 检索 → 发送链路）。"""

from __future__ import annotations

import hashlib

import pytest

from repo.nb2 import pipeline as pipe
from repo.nb2.platform.event import MessageObj, UnifiedEvent
from repo.nb2.platform.message import Segment


def make_event() -> UnifiedEvent:
    return UnifiedEvent(
        platform_name="onebot11",
        session_type="group",
        session_id="555",
        sender_id="1001",
        message_str="庆祝",
        message_obj=MessageObj(message=[Segment.text("庆祝")], message_str="庆祝"),
    )


@pytest.fixture()
def runtime(tmp_path):
    from repo.nb2.runtime import MemeRuntime

    return MemeRuntime(data_dir=tmp_path)


def test_resolve_image_path_by_entry_id(runtime):
    """候选只带 entry_id 时，应通过语义元数据的 relative_path 定位文件。"""
    from repo.backend.semantic import storage as sem_storage

    event = make_event()
    context = runtime._resolve_runtime_pack_context(event=event)
    pack_dir = context["pack_dir"]
    memes_dir = context["memes_dir"]
    target = memes_dir / "happy" / "by-id.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = b"\x89PNG\r\n\x1a\nID"
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    metadata = sem_storage.load_metadata(pack_dir)
    metadata.setdefault("images", {})
    metadata["images"][digest] = {
        "category": "happy",
        "relative_path": "memes/happy/by-id.png",
        "content_sha256": digest,
        "caption": "庆祝",
        "tags": ["开心"],
        "caption_status": "done",
    }
    sem_storage.save_metadata(pack_dir, metadata)

    # save_metadata 会按内容+分类+路径重算 entry_id，读取回真实键再断言。
    stored = sem_storage.load_metadata(pack_dir).get("images", {})
    entry_id = next(iter(stored))
    path = pipe.resolve_image_path(runtime, event, {"entry_id": entry_id, "category": "happy"})
    from pathlib import Path

    assert path is not None and Path(path).resolve() == target.resolve()


def test_resolve_image_path_rejects_escape(runtime):
    """relative_path 指向包外时必须拒绝（防目录穿越）。"""
    from repo.backend.semantic import storage as sem_storage

    event = make_event()
    context = runtime._resolve_runtime_pack_context(event=event)
    pack_dir = context["pack_dir"]
    digest = "f" * 64
    metadata = sem_storage.load_metadata(pack_dir)
    metadata.setdefault("images", {})
    metadata["images"][digest] = {
        "category": "happy",
        "relative_path": "../../outside.png",
        "content_sha256": digest,
        "caption": "x",
        "tags": ["t"],
        "caption_status": "done",
    }
    sem_storage.save_metadata(pack_dir, metadata)
    assert pipe.resolve_image_path(runtime, event, {"entry_id": digest}) is None
