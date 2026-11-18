"""命令处理器测试（直接调用，不依赖 nonebug 的假 bot）。"""

from __future__ import annotations

import pytest

from repo.nb2.platform.event import MessageObj, UnifiedEvent
from repo.nb2.platform.message import Segment, UnifiedMessage
from repo.nb2.matchers import commands as cmd


class _Transport:
    def __init__(self):
        self.sent: list[UnifiedMessage] = []

    class _Caps:
        name = "test"

    capabilities = _Caps()

    async def send(self, message):
        self.sent.append(message)


def make_event(text: str = "", *, is_admin: bool = False, session_id: str = "555") -> UnifiedEvent:
    return UnifiedEvent(
        platform_name="onebot11",
        session_type="group",
        session_id=session_id,
        sender_id="1001",
        message_str=text,
        message_obj=MessageObj(message=[Segment.text(text)], message_str=text),
        transport=_Transport(),
        is_admin=is_admin,
    )


def texts(event: UnifiedEvent) -> list[str]:
    return [m.plain_text for m in event.transport.sent]


@pytest.fixture()
def runtime(tmp_path):
    """每个测试独立的运行时（构造时会把业务模块的路径常量重定向到该目录）。"""
    from repo.nb2.runtime import MemeRuntime

    return MemeRuntime(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_list_emotions_lists_categories(runtime):
    event = make_event()
    results = [r async for r in cmd.list_emotions(event, runtime, "")]
    assert results and "当前图库" in results[0].plain_text
    assert "happy" in results[0].plain_text


@pytest.mark.asyncio
async def test_show_library_stats_reports_pack(runtime):
    event = make_event()
    results = [r async for r in cmd.show_library_stats(event, runtime, "")]
    assert results and "图片" in results[0].plain_text
    assert "builtin-default" in results[0].plain_text


@pytest.mark.asyncio
async def test_add_category_without_args_shows_usage(runtime):
    event = make_event()
    results = [r async for r in cmd.add_category_command(event, runtime, "")]
    assert "添加分类" in results[0].plain_text


@pytest.mark.asyncio
async def test_add_category_existing_reports_exists(runtime):
    event = make_event()
    results = [r async for r in cmd.add_category_command(event, runtime, "happy 已存在")]
    assert "已存在" in results[0].plain_text


@pytest.mark.asyncio
async def test_add_category_creates_new(runtime):
    event = make_event()
    results = [r async for r in cmd.add_category_command(event, runtime, "nb2cat 新分类描述")]
    assert "已创建分类" in results[0].plain_text
    assert "nb2cat" in runtime.category_manager.get_descriptions()


@pytest.mark.asyncio
async def test_add_category_waits_for_description(runtime, monkeypatch):
    """未提供描述时进入等待；模拟用户下一条消息。"""
    from repo.nb2.platform import session as session_module

    event = make_event()

    async def fake_waiter(e, filter_obj=None):
        # 模拟用户发送描述：直接投递给等待中的协程。
        waiter_event = make_event("这是一个等待补充的描述")
        # 找到刚注册的等待者并完成它。
        for key in session_module.pending_keys():
            await session_module.dispatch(key, waiter_event)
            break

    monkeypatch.setattr(cmd, "_wait_for_category_description", fake_waiter)
    results = [r async for r in cmd.add_category_command(event, runtime, "waitedcat")]
    joined = "\n".join(r.plain_text for r in results)
    assert "请发送分类" in joined or "已创建分类" in joined


@pytest.mark.asyncio
async def test_help_command_lists_migrated_commands():
    event = make_event()
    results = [r async for r in cmd.help_command(event, None, "")]
    text = results[0].plain_text
    assert "查看图库" in text
    assert "添加表情" in text
    assert "从云端覆盖" in text


# ------------------------------------------------------------ 收集类命令


@pytest.mark.asyncio
async def test_collect_memes_without_images_hints(runtime):
    event = make_event()
    results = [r async for r in cmd.collect_memes(event, runtime, "")]
    assert "收集表情" in results[0].plain_text


@pytest.mark.asyncio
async def test_start_collecting_without_provider_reports(runtime):
    event = make_event()
    results = [r async for r in cmd.start_collecting_memes(event, runtime, "")]
    assert "无法开始收集" in results[0].plain_text


@pytest.mark.asyncio
async def test_stop_collecting_without_session(runtime):
    event = make_event()
    results = [r async for r in cmd.stop_collecting_memes(event, runtime, "")]
    assert "当前没有连续收集任务" in results[0].plain_text


# ------------------------------------------------------------ 添加表情


@pytest.mark.asyncio
async def test_upload_meme_without_args_shows_usage(runtime):
    event = make_event()
    results = [r async for r in cmd.upload_meme(event, runtime, "")]
    assert "添加表情" in results[0].plain_text


@pytest.mark.asyncio
async def test_upload_meme_unknown_category_rejected(runtime):
    event = make_event()
    results = [r async for r in cmd.upload_meme(event, runtime, "不存在的分类")]
    assert "无效" in results[0].plain_text
    assert f"1001" not in runtime.upload_states


@pytest.mark.asyncio
async def test_upload_meme_sets_pending_state(runtime):
    """有效分类会写入 upload_states，供管线消费。"""
    event = make_event()
    results = [r async for r in cmd.upload_meme(event, runtime, "happy")]
    assert "请在30秒内发送" in results[0].plain_text
    key = f"{event.session_id}_{event.get_sender_id()}"
    state = runtime.upload_states[key]
    assert state["category"] == "happy"
    assert state["pack_id"] == "builtin-default"
    assert state["expire_time"] > __import__("time").time()
    assert state["memes_dir"]


# ------------------------------------------------------------ 清空与删除


@pytest.mark.asyncio
async def test_clear_category_requires_args(runtime):
    event = make_event()
    results = [r async for r in cmd.clear_category_command(event, runtime, "")]
    assert "清空指定类型" in results[0].plain_text


@pytest.mark.asyncio
async def test_clear_category_unknown_category(runtime):
    event = make_event()
    results = [r async for r in cmd.clear_category_command(event, runtime, "nope")]
    assert "未找到类型" in results[0].plain_text


@pytest.mark.asyncio
async def test_clear_category_cancelled_keeps_files(runtime, monkeypatch):
    memes_dir = runtime.packs_dir / "builtin-default" / "memes" / "happy"
    memes_dir.mkdir(parents=True, exist_ok=True)
    (memes_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    async def cancel(event, timeout=30):
        return False

    monkeypatch.setattr(cmd, "_wait_for_command_confirmation", cancel)
    event = make_event()
    results = [r async for r in cmd.clear_category_command(event, runtime, "happy")]
    assert "即将清空类型" in results[0].plain_text
    assert (memes_dir / "a.png").is_file()


@pytest.mark.asyncio
async def test_clear_category_confirmed_deletes_images(runtime, monkeypatch):
    memes_dir = runtime.packs_dir / "builtin-default" / "memes" / "happy"
    memes_dir.mkdir(parents=True, exist_ok=True)
    (memes_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (memes_dir / "b.jpg").write_bytes(b"\xff\xd8\xff")

    async def confirm(event, timeout=30):
        return True

    monkeypatch.setattr(cmd, "_wait_for_command_confirmation", confirm)
    event = make_event()
    results = [r async for r in cmd.clear_category_command(event, runtime, "happy")]
    joined = "\n".join(r.plain_text for r in results)
    assert "已清空类型" in joined
    assert "2 个表情包" in joined
    assert not list(memes_dir.glob("*.*"))


@pytest.mark.asyncio
async def test_clear_all_reports_empty(runtime):
    event = make_event()
    results = [r async for r in cmd.clear_all_emojis_command(event, runtime, "")]
    assert "没有可清空" in results[0].plain_text


@pytest.mark.asyncio
async def test_clear_all_confirmed(runtime, monkeypatch):
    memes_dir = runtime.packs_dir / "builtin-default" / "memes"
    for category in ("happy", "sad"):
        target = memes_dir / category
        target.mkdir(parents=True, exist_ok=True)
        (target / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    async def confirm(event, timeout=30):
        return True

    monkeypatch.setattr(cmd, "_wait_for_command_confirmation", confirm)
    event = make_event()
    results = [r async for r in cmd.clear_all_emojis_command(event, runtime, "")]
    joined = "\n".join(r.plain_text for r in results)
    assert "即将清空全部表情包" in joined
    assert "已清空全部表情包" in joined
    assert "2 个文件" in joined


@pytest.mark.asyncio
async def test_delete_category_requires_args(runtime):
    event = make_event()
    results = [r async for r in cmd.delete_category_command(event, runtime, "")]
    assert "删除类型本身" in results[0].plain_text


@pytest.mark.asyncio
async def test_delete_category_unknown_category(runtime):
    event = make_event()
    results = [r async for r in cmd.delete_category_command(event, runtime, "nope")]
    assert "未找到类型" in results[0].plain_text


@pytest.mark.asyncio
async def test_delete_category_cancelled(runtime, monkeypatch):
    memes_dir = runtime.packs_dir / "builtin-default" / "memes" / "happy"
    memes_dir.mkdir(parents=True, exist_ok=True)

    async def cancel(event, timeout=30):
        return False

    monkeypatch.setattr(cmd, "_wait_for_command_confirmation", cancel)
    event = make_event()
    results = [r async for r in cmd.delete_category_command(event, runtime, "happy")]
    joined = "\n".join(r.plain_text for r in results)
    assert "即将删除类型" in joined
    assert memes_dir.is_dir()


@pytest.mark.asyncio
async def test_delete_category_confirmed(runtime, monkeypatch):
    memes_dir = runtime.packs_dir / "builtin-default" / "memes" / "happy"
    memes_dir.mkdir(parents=True, exist_ok=True)
    (memes_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    runtime.category_manager.update_description("happy", "开心")

    async def confirm(event, timeout=30):
        return True

    monkeypatch.setattr(cmd, "_wait_for_command_confirmation", confirm)
    event = make_event()
    results = [r async for r in cmd.delete_category_command(event, runtime, "happy")]
    joined = "\n".join(r.plain_text for r in results)
    assert "已删除类型" in joined
    assert not memes_dir.is_dir()


# ------------------------------------------------------------ 恢复默认包


@pytest.mark.asyncio
async def test_restore_default_rejects_category_arg(runtime, monkeypatch):
    def fake_install(index_url, overwrite=False, set_as_default=True, **kwargs):
        return {"selected_pack_name": "官方包", "pack_id": "official"}

    import repo.backend.packs.storage as storage

    monkeypatch.setattr(storage, "install_first_official_pack_from_index", fake_install)
    event = make_event()
    results = [r async for r in cmd.restore_default_memes_command(event, runtime, "happy")]
    joined = "\n".join(r.plain_text for r in results)
    assert "不再支持按类别恢复" in joined
    assert "已从官方仓库安装默认表情包" in joined


@pytest.mark.asyncio
async def test_restore_default_handles_exists(runtime, monkeypatch):
    def fake_install(index_url, overwrite=False, set_as_default=True, **kwargs):
        raise FileExistsError("exists")

    import repo.backend.packs.storage as storage

    monkeypatch.setattr(storage, "install_first_official_pack_from_index", fake_install)
    event = make_event()
    results = [r async for r in cmd.restore_default_memes_command(event, runtime, "")]
    assert "已存在" in results[0].plain_text


# ------------------------------------------------------------ 图床同步


@pytest.mark.asyncio
async def test_sync_status_without_image_host(runtime):
    event = make_event()
    results = [r async for r in cmd.check_sync_status(event, runtime, "")]
    assert "图床服务尚未配置" in results[0].plain_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "label"),
    [
        (cmd.sync_to_remote, "同步到云端"),
        (cmd.sync_from_remote, "从云端同步"),
        (cmd.overwrite_to_remote, "覆盖到云端"),
        (cmd.overwrite_from_remote, "从云端覆盖"),
    ],
)
async def test_sync_commands_without_image_host(runtime, handler, label):
    event = make_event()
    results = [r async for r in handler(event, runtime, "")]
    assert "图床服务尚未配置" in results[0].plain_text


class _FakeSyncManager:
    upload_tracker = None


class _FakeProvider:
    bucket_name = "bkt"

    def get_image_list(self):
        return [{"category": "happy", "filename": "a.png"}]


class _FakeSyncClient:
    def __init__(self, success=True):
        self.local_dir = ""
        self.provider = _FakeProvider()
        self.sync_manager = _FakeSyncManager()
        self.success = success
        self.calls: list[str] = []

    def check_status(self):
        return {
            "to_upload": [{"category": "happy", "filename": "a.png"}],
            "to_download": [{"category": "sad", "filename": "b.png"}],
            "conflict_count": 1,
            "conflicts": [{"relative_path": "happy/a.png"}],
            "is_synced": False,
        }

    async def start_sync(self, mode):
        self.calls.append(mode)
        return self.success


@pytest.mark.asyncio
async def test_sync_status_reports_diff(runtime, monkeypatch):
    client = _FakeSyncClient()
    client.local_dir = str(runtime.packs_dir / "builtin-default" / "memes")
    monkeypatch.setattr(runtime, "_ensure_img_sync_for_pack", lambda *a, **kw: client)
    event = make_event()
    results = [r async for r in cmd.check_sync_status(event, runtime, "")]
    text = results[0].plain_text
    assert "图床同步状态报告" in text
    assert "需要上传: 1" in text
    assert "需要下载: 1" in text
    assert "同步到云端" in text


@pytest.mark.asyncio
async def test_sync_to_remote_success(runtime, monkeypatch):
    client = _FakeSyncClient()
    monkeypatch.setattr(runtime, "_ensure_img_sync_for_pack", lambda *a, **kw: client)
    event = make_event()
    results = [r async for r in cmd.sync_to_remote(event, runtime, "")]
    joined = "\n".join(r.plain_text for r in results)
    assert "正在开启云端同步任务" in joined
    assert "云端同步已完成" in joined
    assert client.calls == ["upload"]


@pytest.mark.asyncio
async def test_sync_from_remote_releases_semantic_lock(runtime, monkeypatch):
    client = _FakeSyncClient()
    client.local_dir = str(runtime.packs_dir / "builtin-default" / "memes")
    monkeypatch.setattr(runtime, "_ensure_img_sync_for_pack", lambda *a, **kw: client)

    begun: list[tuple[str, str]] = []
    ended: list[str] = []

    def begin(pack_id, operation):
        begun.append((pack_id, operation))

    def end(pack_id):
        ended.append(pack_id)

    monkeypatch.setattr(runtime.semantic_task_manager, "begin_external_pack_operation", begin)
    monkeypatch.setattr(runtime.semantic_task_manager, "end_external_pack_operation", end)

    event = make_event()
    results = [r async for r in cmd.sync_from_remote(event, runtime, "")]
    joined = "\n".join(r.plain_text for r in results)
    assert "从云端同步已完成" in joined
    assert begun and ended
    assert begun[0][0] == ended[0]


@pytest.mark.asyncio
async def test_sync_from_remote_lock_conflict(runtime, monkeypatch):
    client = _FakeSyncClient()
    monkeypatch.setattr(runtime, "_ensure_img_sync_for_pack", lambda *a, **kw: client)

    def begin(pack_id, operation):
        raise RuntimeError("语义任务运行中")

    monkeypatch.setattr(runtime.semantic_task_manager, "begin_external_pack_operation", begin)
    event = make_event()
    results = [r async for r in cmd.sync_from_remote(event, runtime, "")]
    assert "语义任务运行中" in results[0].plain_text
    assert client.calls == []


@pytest.mark.asyncio
async def test_overwrite_from_remote_failure_message(runtime, monkeypatch):
    client = _FakeSyncClient(success=False)
    client.local_dir = str(runtime.packs_dir / "builtin-default" / "memes")
    monkeypatch.setattr(runtime, "_ensure_img_sync_for_pack", lambda *a, **kw: client)
    event = make_event()
    results = [r async for r in cmd.overwrite_from_remote(event, runtime, "")]
    joined = "\n".join(r.plain_text for r in results)
    assert "任务失败" in joined
    assert client.calls == ["overwrite_from_remote"]


def test_register_all_covers_migrated_commands(monkeypatch):
    registered: dict[str, bool] = {}

    def fake_register(command, handler, *, admin=False, priority=10):
        registered[command] = admin
        return object()

    monkeypatch.setattr(cmd, "register_command", fake_register)
    mapping = cmd.register_all()
    expected_admin = {
        "添加分类",
        "添加表情",
        "收集表情",
        "开始收集",
        "结束收集",
        "恢复默认表情包",
        "清空指定类型",
        "清空全部",
        "删除类型本身",
        "同步到云端",
        "从云端同步",
        "覆盖到云端",
        "从云端覆盖",
    }
    expected_public = {"查看图库", "图库统计", "同步状态", "帮助"}
    assert set(mapping) == expected_admin | expected_public
    for name in expected_admin:
        assert registered[name] is True, name
    for name in expected_public:
        assert registered[name] is False, name


def test_format_category_counts():
    assert cmd._format_category_counts({}) == "无可删除的表情包文件。"
    text = cmd._format_category_counts({"a": 2, "b": 0, "c": 1}, limit=1)
    assert "- a: 2 个" in text
    assert "其余 1 个类型已省略" in text


async def _collect(generator):
    return [item async for item in generator]


def test_sender_scoped_filter_key():
    event = make_event()
    scope = cmd.SenderScopedSessionFilter(event)
    assert scope.filter(event) == f"{event.unified_msg_origin}:1001"
