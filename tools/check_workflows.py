"""校验 GitHub Actions 工作流（YAML 结构 + 常见跨平台坑）。

CI 无法在本地运行，但可以在提交前检查两类**一定会炸**的问题：

1. YAML 语法错误（如 ``run:`` 值里出现 ``: `` 未加引号）；
2. 跨平台 shell 问题——Windows runner 默认 PowerShell，bash 的
   ``\\`` 续行会被当成参数传给命令（pip 报 ``Directory '\\' is not installable``）。

用法：python tools/check_workflows.py
"""

from __future__ import annotations

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _console import ensure_utf8_stdout, safe_print  # noqa: E402

ensure_utf8_stdout()

import pathlib
import sys

WORKFLOW_DIR = pathlib.Path(".github/workflows")


def _load_yaml(path: pathlib.Path):
    try:
        import yaml
    except ImportError:  # pragma: no cover - 本地无 pyyaml 时跳过
        safe_print("未安装 PyYAML，跳过 YAML 解析检查")
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        safe_print(f"[FAIL] {path} YAML 解析失败：{exc}")
        return None


def check_file(path: pathlib.Path) -> list[str]:
    problems: list[str] = []
    data = _load_yaml(path)
    if data is None:
        return [f"{path}: YAML 无法解析"]

    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return [f"{path}: 缺少 jobs"]

    for job_name, job in jobs.items():
        for step in job.get("steps") or []:
            run = step.get("run") or ""
            label = f"{path}:{job_name}/{step.get('name') or step.get('uses') or 'step'}"
            if "\\\n" in run:
                problems.append(
                    f"{label}: run 里使用了 bash 续行（Windows runner 是 PowerShell，"
                    "会被当成参数，请改成单行或显式 shell: bash）"
                )
            if run.count("\n") > 0 and "shell:" not in str(job):
                pass  # 多行脚本本身没问题
    return problems


def main() -> int:
    if not WORKFLOW_DIR.is_dir():
        safe_print("未找到 .github/workflows")
        return 1
    problems: list[str] = []
    files = sorted(WORKFLOW_DIR.glob("*.y*ml"))
    for path in files:
        problems.extend(check_file(path))
        safe_print(f"检查 {path}")
    if problems:
        safe_print()
        safe_print(f"发现 {len(problems)} 处问题：")
        for problem in problems:
            safe_print("  " + problem)
        return 1
    safe_print(f"工作流检查通过（{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
