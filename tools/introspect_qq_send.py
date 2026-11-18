"""检查 nonebot-adapter-qq 的发送与富媒体处理实现。"""

import inspect

from nonebot.adapters.qq import Bot, Message, MessageSegment

print("=== _prepare_message ===")
print(inspect.getsource(Bot._prepare_message))
print("=== _parse_send_message ===")
print(inspect.getsource(Bot._parse_send_message))
print("=== _extract_send_message ===")
print(inspect.getsource(Bot._extract_send_message))
print("=== file_image ===")
print(inspect.getsource(MessageSegment.file_image))
print("=== Message built ===")
msg = Message([MessageSegment.text("hi"), MessageSegment.image("https://x/y.png")])
print(repr(msg), list(msg))
try:
    local = MessageSegment.file_image(b"\x89PNG", "a.png")
    print("local attachment:", repr(local))
    print(Message([local]).extract_plain_text())
except Exception as exc:
    print("file_image failed:", exc)
