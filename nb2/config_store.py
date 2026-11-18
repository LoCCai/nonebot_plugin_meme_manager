"""NoneBot2 插件配置存储。

原插件通过 AstrBot 的 ``AstrBotConfig``（dict 子类 + ``schema``）读写配置，业务层
全部使用 ``self._read_config_value((...), default=...)`` 形式的路径读取。

本模块提供等价实现，保持 ``_conf_schema.json`` 作为唯一事实源：

* 首次启动按 schema 的 ``default`` 生成 ``config.json``；
* 读取时逐级回退到 schema 默认值，缺失/非法值不会让业务层崩溃；
* ``save()`` 原子写回（先写临时文件再替换），避免写坏配置。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .log import logger

#: ``_conf_schema.json`` 中表示「嵌套对象」的类型。
OBJECT_TYPE = "object"


def schema_default(spec: Any) -> Any:
    """从 schema 节点提取默认值。"""
    if not isinstance(spec, dict):
        return None
    if "default" in spec:
        return copy.deepcopy(spec["default"])
    if spec.get("type") == OBJECT_TYPE:
        items = spec.get("items") or {}
        if isinstance(items, dict):
            return {key: schema_default(value) for key, value in items.items()}
        return {}
    return None


def build_defaults(schema: dict[str, Any]) -> dict[str, Any]:
    """按 schema 递归构造默认配置。"""
    if not isinstance(schema, dict):
        return {}
    return {key: schema_default(spec) for key, spec in schema.items()}


def _options(spec: Any) -> list[Any] | None:
    if isinstance(spec, dict):
        options = spec.get("options")
        if isinstance(options, list):
            return options
    return None


class PluginConfig(dict):
    """带 schema 的配置对象（兼容 AstrBot ``AstrBotConfig`` 用法）。"""

    def __init__(
        self,
        path: str | Path,
        schema: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.path = Path(path)
        self.schema: dict[str, Any] = schema or {}
        self._defaults = build_defaults(self.schema)
        super().__init__(self._defaults)
        if data:
            self.update(data)
        self._normalize()

    # ------------------------------------------------------------ 载入/保存
    @classmethod
    def load(
        cls,
        path: str | Path,
        schema: dict[str, Any] | None = None,
        *,
        schema_path: str | Path | None = None,
    ) -> "PluginConfig":
        """从磁盘载入配置；文件不存在时按 schema 生成默认值并落盘。"""
        path = Path(path)
        if schema is None and schema_path is not None:
            schema = cls.load_schema(schema_path)
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as file_obj:
                    loaded = json.load(file_obj)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception as exc:
                logger.error("读取配置失败，将使用默认值：{} ({})", path, exc)
        config = cls(path, schema, data)
        if not path.is_file():
            config.save()
        return config

    @staticmethod
    def load_schema(schema_path: str | Path) -> dict[str, Any]:
        """读取 ``_conf_schema.json``。"""
        try:
            with Path(schema_path).open(encoding="utf-8") as file_obj:
                schema = json.load(file_obj)
            return schema if isinstance(schema, dict) else {}
        except Exception as exc:
            logger.error("读取配置 schema 失败：{} ({})", schema_path, exc)
            return {}

    def save(self) -> bool:
        """原子写回配置文件。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(dict(self), ensure_ascii=False, indent=2)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.path.parent),
                delete=False,
                suffix=".tmp",
            ) as handle:
                handle.write(payload)
                tmp_path = handle.name
            os.replace(tmp_path, self.path)
            return True
        except Exception as exc:
            logger.error("保存配置失败：{} ({})", self.path, exc)
            return False

    # 兼容 AstrBot 的写入接口名
    save_config = save

    # ------------------------------------------------------------ 读取
    def read(self, path: tuple[str, ...] | list[str] | str, default: Any = None) -> Any:
        """按路径读取配置，缺失时回退 schema 默认值。"""
        keys = (path,) if isinstance(path, str) else tuple(path)
        current: Any = dict(self)
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return self.schema_fallback(keys, default)
        if current is None:
            fallback = self.schema_fallback(keys, None)
            return fallback if fallback is not None else default
        return current

    def schema_fallback(self, keys: tuple[str, ...], default: Any) -> Any:
        """从 schema 逐级查找指定路径的默认值。"""
        node: Any = self.schema
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        value = schema_default(node)
        if isinstance(node, dict) and "default" not in node and node.get("type") == OBJECT_TYPE:
            value = schema_default(node)
        return default if value is None and not (
            isinstance(node, dict) and "default" in node
        ) else value

    def read_dict(self, path: tuple[str, ...] | list[str] | str, default: Any = None) -> Any:
        value = self.read(path, default)
        return value if value is not None else default

    def write(self, path: tuple[str, ...] | list[str] | str, value: Any) -> None:
        """按路径写入配置（不自动落盘）。"""
        keys = (path,) if isinstance(path, str) else tuple(path)
        if not keys:
            return
        current: dict[str, Any] = self
        for key in keys[:-1]:
            child = current.get(key)
            if not isinstance(child, dict):
                child = {}
                current[key] = child
            current = child
        current[keys[-1]] = value

    def update_path(self, path: tuple[str, ...] | list[str] | str, value: Any) -> bool:
        """写入并落盘。"""
        self.write(path, value)
        return self.save()

    # ------------------------------------------------------------ 规范化
    def _normalize(self) -> None:
        """剔除不在 schema 中的键，并修正枚举越界值。"""
        if not self.schema:
            return
        self._normalize_node(self, self.schema)

    def _normalize_node(self, current: dict[str, Any], spec: dict[str, Any]) -> None:
        for key in tuple(current):
            if key not in spec:
                del current[key]
                continue
            node = spec[key]
            if isinstance(node, dict) and node.get("type") == OBJECT_TYPE:
                child = current[key]
                items = node.get("items") or {}
                if isinstance(child, dict) and isinstance(items, dict):
                    self._normalize_node(child, items)
                else:
                    current[key] = schema_default(node) or {}
                continue
            options = _options(node)
            if options and current[key] not in options:
                current[key] = schema_default(node)

    # ------------------------------------------------------------ 便利属性
    def get_bool(self, path: Any, default: bool = False) -> bool:
        return bool(self.read(path, default))

    def get_int(self, path: Any, default: int = 0) -> int:
        try:
            return int(self.read(path, default))
        except (TypeError, ValueError):
            return default

    def get_float(self, path: Any, default: float = 0.0) -> float:
        try:
            return float(self.read(path, default))
        except (TypeError, ValueError):
            return default

    def get_str(self, path: Any, default: str = "") -> str:
        value = self.read(path, default)
        return default if value is None else str(value)


__all__ = ["PluginConfig", "build_defaults", "schema_default"]
