"""验证 Windows 控制台编码兜底确实生效（可脱离真实终端运行）。

用法：python tools/check_console.py
"""

from __future__ import annotations

import io
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _console import ensure_utf8_stdout, safe_print  # noqa: E402

ensure_utf8_stdout()

#: 检查结果先收集，最后一次性打印（模拟期间 stdout 被替换成内存流）。
lines: list[str] = []
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    lines.append(f"[{'OK  ' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def main() -> int:
    # 1) 模拟 Windows cp1252 控制台：先确认裸 print 会炸
    original = _sys.stdout
    buffer = io.BytesIO()
    _sys.stdout = io.TextIOWrapper(
        buffer, encoding="cp1252", errors="strict", line_buffering=True
    )
    try:
        raised = False
        try:
            print("中文输出")
        except UnicodeEncodeError:
            raised = True
        check("裸 print 在 cp1252 下抛 UnicodeEncodeError", raised)

        # 2) safe_print 不得抛异常（降级替换不可编码字符）
        crashed = False
        try:
            safe_print("安全输出：中文")
        except Exception:  # pragma: no cover - 兜底失败即测试失败
            crashed = True
        check("safe_print 在 cp1252 下不抛异常", not crashed)

        # 3) ensure_utf8_stdout 可把流切到 UTF-8
        ensure_utf8_stdout()
        encoding = (getattr(_sys.stdout, "encoding", "") or "").lower()
        check("ensure_utf8_stdout 切换到 utf-8", encoding.startswith("utf"), encoding)
        switched_ok = True
        try:
            print("切换后中文正常")
        except UnicodeEncodeError:  # pragma: no cover
            switched_ok = False
        check("切换后中文可正常输出", switched_ok)
    finally:
        _sys.stdout = original

    for line in lines:
        safe_print(line)
    safe_print()
    if failures:
        safe_print(f"失败 {len(failures)} 项：" + ", ".join(failures))
        return 1
    safe_print("控制台编码兜底自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
