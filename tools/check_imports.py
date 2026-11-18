"""导入图连通性检查（CI 用）。

校验仓库内每个 ``from .`` / ``from ..`` 相对导入都能解析到真实模块，
避免「相对层级写错」在运行时才暴露。

用法：python tools/check_imports.py
"""

from __future__ import annotations

import pathlib
import re
import sys

#: 仓库根（插件包根）。
ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = ROOT.name

IMPORT_RE = re.compile(r"^\s*from\s+(\.+)([\w.]*)\s+import", re.M)


def _module_file(base: pathlib.Path, dotted: str) -> pathlib.Path | None:
    """把点分模块名解析为文件或包目录。"""
    parts = [part for part in dotted.split(".") if part]
    target = base
    for index, part in enumerate(parts):
        candidate = target / part
        is_last = index == len(parts) - 1
        if candidate.with_suffix(".py").is_file():
            return candidate.with_suffix(".py")
        if candidate.is_dir():
            if is_last:
                return candidate
            target = candidate
            continue
        return None
    return None


def _package_root_for(path: pathlib.Path) -> pathlib.Path | None:
    """找到该文件所属的包根（含 ``__init__.py`` 的最外层目录）。"""
    if path.name == "__init__.py":
        directory = path.parent
    else:
        directory = path.parent
    while directory != ROOT and (directory / "__init__.py").is_file():
        directory = directory.parent
    return directory if directory != path.parent or (path.parent / "__init__.py").is_file() else None


def check() -> list[str]:
    """返回所有解析失败的相对导入。"""
    problems: list[str] = []
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in IMPORT_RE.finditer(text):
            dots = match.group(1)
            dotted = match.group(2)
            if not dotted:
                continue
            # 相对层数：1 个点 = 当前包；n 个点 = 向上 n-1 层。
            base = path.parent
            levels = len(dots) - 1
            while levels > 0:
                if base == ROOT.parent:
                    base = None
                    break
                base = base.parent
                levels -= 1
            if base is None:
                line = text[: match.start()].count("\n") + 1
                problems.append(f"{path.relative_to(ROOT)}:{line}: 相对层级越界 from {dots}{dotted}")
                continue
            if _module_file(base, dotted) is None:
                line = text[: match.start()].count("\n") + 1
                problems.append(
                    f"{path.relative_to(ROOT)}:{line}: 无法解析 from {dots}{dotted}"
                )
    return problems


def main() -> int:
    problems = check()
    if problems:
        print(f"发现 {len(problems)} 处相对导入问题：")
        for problem in problems:
            print("  " + problem)
        return 1
    print(f"相对导入检查通过（{PACKAGE}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
