import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from repo.backend import auto_collect


@pytest.fixture
def manager(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text('{"schema_version":1,"decisions":{},"old_field":"kept"}', encoding="utf-8")
    monkeypatch.setattr(auto_collect, "AUTO_COLLECT_STATE_PATH", path)
    return auto_collect.AutoCollectManager(SimpleNamespace(), {"daily_recognition_limit": 2})


async def test_slow_state_save_allows_loop_and_close_waits_for_writer(manager, monkeypatch):
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    save = manager._save_json

    def delayed_save(path, snapshot):
        assert threading.get_ident() != main_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(4)
        save(path, snapshot)

    monkeypatch.setattr(manager, "_save_json", delayed_save)
    observation = asyncio.create_task(manager._sample_observation("pack:image", "message"))
    manager._snapshot_tasks.add(observation)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        closing = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        observation.cancel()  # repeated cancellation cannot release the lock
        next_write = asyncio.create_task(manager._daily_call_available())
        await asyncio.sleep(0)
        assert not closing.done() and not next_write.done()
        assert manager._state.get("observations") is None
    finally:
        release.set()
    await closing
    assert observation.cancelled()
    assert await next_write
    restored = auto_collect.AutoCollectManager(SimpleNamespace(), {})
    assert restored._state == manager._state
    assert restored._state["observations"]["pack:image"]["message_ids"] == ["message"]
    assert restored._state["recognition_count"] == 1 and restored._state["old_field"] == "kept"


async def test_failed_replace_preserves_committed_state_and_observation_can_retry(manager, monkeypatch):
    path = auto_collect.AUTO_COLLECT_STATE_PATH
    await manager._sample_observation("pack:image", "one")
    previous = path.read_bytes()

    def fail(*args):
        raise OSError("simulated disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(auto_collect.os, "replace", fail)
        with pytest.raises(OSError, match="simulated disk failure"):
            await manager._sample_observation("pack:image", "two")
    assert previous == path.read_bytes()
    assert not path.with_suffix(".json.tmp").exists()
    assert manager._state == json.loads(previous)
    await manager._sample_observation("pack:image", "two")
    assert manager._state["observations"]["pack:image"]["count"] == 2


async def test_concurrent_state_fields_and_quota_survive_reload(manager):
    quotas = await asyncio.gather(
        *(manager._daily_call_available() for _ in range(3)),
        manager._decision_call_started(), manager._remember_decision("a", {"kind": "one"}),
        manager._remember_understanding("b", {"tags": ["two"]}),
        *(manager._sample_observation("pack:image", "one") for _ in range(3)),
    )
    assert quotas[:3].count(True) == 2
    state = json.loads(auto_collect.AUTO_COLLECT_STATE_PATH.read_text(encoding="utf-8"))
    assert state == manager._state
    assert state["decision_count"] == 1 and state["recognition_count"] == 2
    assert state["decisions"]["a"]["kind"] == "one" and state["understandings"]["b"]["tags"] == ["two"]
    assert state["observations"]["pack:image"]["count"] == 1
