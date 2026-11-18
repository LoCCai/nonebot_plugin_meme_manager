"""临时实验：新移植命令在各分支下的行为（不入库）。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
os.environ["MEME_MANAGER_DATA_DIR"] = tempfile.mkdtemp(prefix="exp_")

from repo.nb2 import bootstrap

bootstrap.ensure()

from repo.nb2.platform.event import MessageObj, UnifiedEvent
from repo.nb2.platform.message import Segment
from repo.nb2.runtime import MemeRuntime
from repo.nb2.matchers import commands as cmd


def make_event(text=""):
    return UnifiedEvent(
        platform_name="onebot11",
        session_type="group",
        session_id="555",
        sender_id="1001",
        message_str=text,
        message_obj=MessageObj(message=[Segment.text(text)], message_str=text),
        is_admin=True,
    )


async def collect(gen):
    out = []
    async for item in gen:
        out.append(item.plain_text if hasattr(item, "plain_text") else str(item))
    return out


async def main():
    runtime = MemeRuntime()
    print("data:", runtime.data_dir)

    for name, fn, args in [
        ("collect_memes", cmd.collect_memes, ""),
        ("start", cmd.start_collecting_memes, ""),
        ("stop", cmd.stop_collecting_memes, ""),
        ("upload-noargs", cmd.upload_meme, ""),
        ("upload-badcat", cmd.upload_meme, "not-exist-cat"),
        ("clear-noargs", cmd.clear_category_command, ""),
        ("clear-unknown", cmd.clear_category_command, "nope"),
        ("clear-empty", cmd.clear_category_command, "happy"),
        ("clearall", cmd.clear_all_emojis_command, ""),
        ("delete-noargs", cmd.delete_category_command, ""),
        ("delete-unknown", cmd.delete_category_command, "nope"),
        ("sync-status", cmd.check_sync_status, ""),
        ("sync-up", cmd.sync_to_remote, ""),
        ("sync-down", cmd.sync_from_remote, ""),
        ("ow-up", cmd.overwrite_to_remote, ""),
        ("ow-down", cmd.overwrite_from_remote, ""),
        ("help", cmd.help_command, ""),
    ]:
        try:
            out = await collect(fn(make_event(), runtime, args))
            print(f"[{name}] ->", (out[0][:70].replace("\n", " | ") if out else "<empty>"))
        except Exception as exc:
            print(f"[{name}] EXC {type(exc).__name__}: {exc}")

    # 带图片的分类清空（确认=True）
    memes_dir = runtime.packs_dir / "builtin-default" / "memes" / "happy"
    memes_dir.mkdir(parents=True, exist_ok=True)
    (memes_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    print("emoji count:", len(__import__("repo.backend.packs.images", fromlist=["x"]).get_emoji_by_category("happy")))

    async def fake_confirm(event, timeout=30):
        return True

    cmd._wait_for_command_confirmation = fake_confirm
    out = await collect(cmd.clear_category_command(make_event(), runtime, "happy"))
    print("[clear-happy]", out[0][:100].replace("\n", " | "))
    left = list(memes_dir.iterdir()) if memes_dir.is_dir() else []
    print("left files:", [p.name for p in left])


asyncio.run(main())
