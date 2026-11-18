"""控制台编码兜底：让脚本在任意平台的终端都能打印中文。

Windows 的默认控制台编码是 cp1252/GBK，直接 ``print`` 中文会抛
``UnicodeEncodeError: 'charmap' codec can't encode characters``。
CI（GitHub Actions Windows runner）与部分本地终端都会遇到。

这里把 ``stdout`` / ``stderr`` 统一切到 UTF-8，并允许无法编码的字符降级替换，
避免「日志打不出来导致脚本退出码为 1」这类假失败。
"""

from __future__ import annotations

import sys


def ensure_utf8_stdout() -> None:
    """把标准输出/错误流切换为 UTF-8（幂等，失败时静默降级）。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - 极少数被替换过的流
            continue


def safe_print(*args: object, sep: str = " ", end: str = "\n") -> None:
    """不会因编码失败而抛异常的 print。

    Windows 控制台（cp1252/GBK）打印中文可能抛 ``UnicodeEncodeError``，
    在多处脚本里，这会掩盖真正的检查结果甚至让脚本异常退出。
    """
    text = sep.join(str(arg) for arg in args)
    try:
        print(text, end=end)
    except UnicodeEncodeError:  # pragma: no cover - 老终端兜底
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, end=end)


__all__ = ["ensure_utf8_stdout", "safe_print"]
