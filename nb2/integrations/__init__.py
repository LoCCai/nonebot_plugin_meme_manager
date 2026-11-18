"""可选集成：与其它 NoneBot2 插件协作。

每个子模块都必须**在依赖缺失时静默跳过**，不能影响插件主流程加载。
"""

from __future__ import annotations

__all__ = ["moellm"]
