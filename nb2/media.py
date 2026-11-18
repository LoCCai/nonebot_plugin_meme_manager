"""统一消息段与业务层 AstrBot 图片组件的桥接。

业务层（``backend/auto_collect.py``、``mixins/event_handlers.py``）按 AstrBot
组件接口处理图片：``image.url`` / ``image.file`` / ``image.convert_to_file_path()``
/ ``image.convert_to_base64()``。这里给 :class:`nb2.platform.message.Segment` 注入
等价能力，使业务代码零改动即可在 NoneBot2 下工作。
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import aiohttp

from .log import logger

#: 下载超时（秒）。
DOWNLOAD_TIMEOUT = 30
#: 单张图片允许的最大字节数。
MAX_IMAGE_BYTES = 20 * 1024 * 1024

_INSTALLED = False
_CACHE_DIR: Path | None = None


def _cache_dir() -> Path:
    """媒体缓存目录（按内容哈希去重）。"""
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = Path(tempfile.mkdtemp(prefix="meme_manager_media_"))
    return _CACHE_DIR


def _guess_suffix(data: bytes, fallback: str = ".png") -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return fallback


def _write_cache(data: bytes, suffix: str | None = None) -> Path:
    digest = hashlib.sha256(data).hexdigest()[:24]
    directory = _cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}{suffix or _guess_suffix(data)}"
    if not target.is_file():
        target.write_bytes(data)
    return target


async def _fetch_bytes(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status >= 400:
                raise RuntimeError(f"下载图片失败（HTTP {response.status}）：{url}")
            data = await response.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片超过 {MAX_IMAGE_BYTES} 字节上限：{url}")
    return data


def _sync_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
        return response.read(MAX_IMAGE_BYTES + 1)


# ------------------------------------------------------------------ 段能力


async def convert_to_base64(segment: Any) -> str:
    """返回图片的 base64 文本（不含 ``base64://`` 前缀）。"""
    source, value = segment.media_source()
    if source == "base64":
        return str(value or "")
    if source == "bytes":
        return base64.b64encode(bytes(value or b"")).decode("ascii")
    if source in {"path", "file"}:
        path = Path(str(value))
        if path.is_file():
            return base64.b64encode(path.read_bytes()).decode("ascii")
        logger.warning("图片文件不存在：{}", path)
        return ""
    if source == "url":
        try:
            return base64.b64encode(await _fetch_bytes(str(value))).decode("ascii")
        except Exception as exc:
            logger.warning("下载图片失败：{}", exc)
            return ""
    # 兼容从平台段数据直接读出的 url/file 字段。
    url = str(segment.data.get("url") or "")
    if url.startswith(("http://", "https://")):
        try:
            return base64.b64encode(await _fetch_bytes(url)).decode("ascii")
        except Exception as exc:
            logger.warning("下载图片失败：{}", exc)
            return ""
    file_ref = str(segment.data.get("file") or "")
    if file_ref:
        path = Path(file_ref)
        if path.is_file():
            return base64.b64encode(path.read_bytes()).decode("ascii")
        if file_ref.startswith("base64://"):
            return file_ref[len("base64://") :]
        if file_ref.startswith(("http://", "https://")):
            try:
                return base64.b64encode(await _fetch_bytes(file_ref)).decode("ascii")
            except Exception as exc:
                logger.warning("下载图片失败：{}", exc)
                return ""
    return ""


async def convert_to_file_path(segment: Any) -> Path:
    """返回图片的本地文件路径（必要时落盘缓存）。"""
    source, value = segment.media_source()
    if source == "path" and value:
        return Path(str(value))
    if source == "file" and value:
        text = str(value)
        if text.startswith(("base64://", "http://", "https://")):
            return await _materialize(text)
        path = Path(text)
        if path.is_file():
            return path
        return await _materialize(text)
    if source in {"base64", "bytes"}:
        data = (
            base64.b64decode(str(value))
            if source == "base64" and value
            else bytes(value or b"")
        )
        if not data:
            raise ValueError("图片内容为空")
        return _write_cache(data)
    if source == "url" and value:
        return await _materialize(str(value))

    url = str(segment.data.get("url") or "")
    if url:
        return await _materialize(url)
    file_ref = str(segment.data.get("file") or "")
    if file_ref:
        return await _materialize(file_ref)
    raise ValueError("无法解析图片地址")


async def _materialize(reference: str) -> Path:
    """把 ``base64://`` / URL / 本地路径统一落成缓存文件。"""
    if reference.startswith("base64://"):
        return _write_cache(base64.b64decode(reference[len("base64://") :]))
    if reference.startswith(("http://", "https://")):
        return _write_cache(await _fetch_bytes(reference))
    if reference.startswith("file://"):
        raw = reference[len("file://") :]
        # Windows 上 ``file:///C:/...`` 会剥成 ``/C:/...``，需要去掉前导斜杠。
        if re.match(r"^/[A-Za-z]:", raw):
            raw = raw[1:]
        path = Path(raw)
        if path.is_file():
            return path
        raise FileNotFoundError(reference)
    path = Path(reference)
    if path.is_file():
        return path
    raise FileNotFoundError(reference)


# ------------------------------------------------------------------ 安装


def install() -> bool:
    """给 :class:`Segment` 注入媒体能力方法（幂等）。"""
    global _INSTALLED
    if _INSTALLED:
        return True
    from .platform.message import Segment

    if not hasattr(Segment, "convert_to_base64"):
        Segment.convert_to_base64 = convert_to_base64  # type: ignore[attr-defined]
    if not hasattr(Segment, "convert_to_file_path"):
        Segment.convert_to_file_path = convert_to_file_path  # type: ignore[attr-defined]
    _INSTALLED = True
    return True


__all__ = [
    "install",
    "convert_to_base64",
    "convert_to_file_path",
    "MAX_IMAGE_BYTES",
    "DOWNLOAD_TIMEOUT",
]
