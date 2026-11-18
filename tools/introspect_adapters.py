"""内省 nonebot 适配器 API，用于校准 nb2 适配层实现。"""

import inspect
import json
import sys

import nonebot  # noqa: F401
from nonebot.adapters.onebot import v11, v12
from nonebot.adapters import qq

out = {}


def sig(func):
    try:
        return str(inspect.signature(func))
    except Exception as exc:  # pragma: no cover
        return f"<{exc}>"


# --- OneBot v11 ---
out["v11"] = {
    "at": sig(v11.MessageSegment.at),
    "image": sig(v11.MessageSegment.image),
    "reply": sig(v11.MessageSegment.reply),
    "record": sig(v11.MessageSegment.record),
    "video": sig(v11.MessageSegment.video),
    "text": sig(v11.MessageSegment.text),
    "face": sig(v11.MessageSegment.face),
    "events": [n for n in dir(v11) if n.endswith("Event")],
    "event_fields": {
        n: sorted(getattr(v11, n).model_fields.keys())
        for n in ("GroupMessageEvent", "PrivateMessageEvent")
    },
    "bot_send": sig(v11.Bot.send),
}
try:
    seg = v11.MessageSegment.at(user_id="123")
    out["v11"]["at_user_id_ok"] = seg.data
except Exception as exc:
    out["v11"]["at_user_id_ok"] = f"ERR {exc}"

# --- OneBot v12 ---
out["v12"] = {
    "seg_attrs": [
        n for n in dir(v12.MessageSegment) if not n.startswith("_") and not n[0].isupper()
    ][:20],
    "message_mro": [c.__name__ for c in v12.Message.__mro__],
    "events": [n for n in dir(v12) if n.endswith("Event")],
    "message_event_fields": sorted(getattr(v12.MessageEvent, "model_fields", {}).keys()),
    "bot_send": sig(v12.Bot.send),
    "bot_upload_file": sig(v12.Bot.upload_file) if hasattr(v12.Bot, "upload_file") else None,
}
for factory in ("text", "image", "mention", "reply"):
    out["v12"][f"factory_{factory}"] = sig(getattr(v12.MessageSegment, factory, None)) if hasattr(
        v12.MessageSegment, factory
    ) else None
try:
    out["v12"]["direct_init"] = str(v12.MessageSegment(type="text", data={"text": "hi"}))
except Exception as exc:
    out["v12"]["direct_init"] = f"ERR {exc}"

# --- QQ official ---
out["qq"] = {
    "seg_attrs": [n for n in dir(qq.MessageSegment) if not n.startswith("_")],
    "events": [n for n in dir(qq) if n.endswith("Event")],
    "bot_send": sig(qq.Bot.send),
    "bot_apis": [
        n
        for n in dir(qq.Bot)
        if "file" in n.lower() or "message" in n.lower()
    ][:40],
}
for name in ("C2CMessageCreateEvent", "GroupAtMessageCreateEvent", "GuildMessageEvent"):
    cls = getattr(qq, name, None)
    if cls is not None:
        out["qq"][f"fields_{name}"] = sorted(getattr(cls, "model_fields", {}).keys())
out["qq"]["seg_sigs"] = {
    n: sig(getattr(qq.MessageSegment, n))
    for n in ("text", "image", "at", "markdown", "keyboard")
    if hasattr(qq.MessageSegment, n)
}

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
sys.stdout.flush()
