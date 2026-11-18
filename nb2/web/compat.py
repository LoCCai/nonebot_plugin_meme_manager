"""Quart / Flask API 兼容层（让上游 WebAPI 处理器几乎零改动地跑在 FastAPI 上）。

上游 ``mixins/web_api.py`` 用 Quart 的模块级全局对象写处理器：

* ``request.args`` / ``request.headers`` / ``request.get_json()`` / ``await request.files``
* ``jsonify(...)`` / ``make_response(...)`` / ``send_file(...)``
* 返回 ``(响应, 状态码)`` 元组

这里用 ``contextvars`` 提供等价的请求上下文，并把上述辅助函数映射到 Starlette。
处理器只要把 ``from quart import ...`` 换成 ``from .compat import ...`` 即可运行。
"""

from __future__ import annotations

import contextvars
import mimetypes
from typing import Any
from urllib.parse import unquote

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse

_current_request: contextvars.ContextVar[Request | None] = contextvars.ContextVar(
    "meme_manager_request", default=None
)


def set_current_request(request: Request | None) -> contextvars.Token:
    """设置当前请求（后端调用处理器前使用）。"""
    return _current_request.set(request)


def reset_current_request(token: contextvars.Token) -> None:
    _current_request.reset(token)


class _IfNoneMatch:
    """``request.if_none_match.contains(x)`` 的等价实现。"""

    def __init__(self, header: str) -> None:
        self._values = [item.strip() for item in (header or "").split(",") if item.strip()]

    def contains(self, value: str) -> bool:
        target = value.strip()
        return target in self._values or f'"{target}"' in self._values


class _Args(dict):
    """查询参数（``request.args``）。"""

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)


class _Headers(dict):
    """请求头（``request.headers``），大小写不敏感。"""

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        for name, value in self.items():
            if name.lower() == key.lower():
                return value
        return default

    def __contains__(self, key: object) -> bool:
        return any(name.lower() == str(key).lower() for name in self.keys())


class _RequestProxy:
    """模块级 ``request`` 的代理（对应 Quart 行为）。"""

    @property
    def _request(self) -> Request:
        request = _current_request.get()
        if request is None:
            raise RuntimeError("当前没有请求上下文")
        return request

    @property
    def method(self) -> str:
        return self._request.method

    @property
    def args(self) -> _Args:
        return _Args(dict(self._request.query_params))

    @property
    def headers(self) -> _Headers:
        return _Headers({key: value for key, value in self._request.headers.items()})

    @property
    def content_length(self) -> int | None:
        value = self._request.headers.get("content-length")
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    @property
    def if_none_match(self) -> _IfNoneMatch:
        return _IfNoneMatch(self._request.headers.get("if-none-match", ""))

    async def get_json(self, force: bool = False, silent: bool = False) -> Any:
        try:
            return await self._request.json()
        except Exception:
            if silent:
                return None
            raise

    async def get_data(self) -> bytes:
        return await self._request.body()

    @property
    def files(self) -> "UploadedFileStore":
        return UploadedFileStore(self._request)

    @property
    def form(self) -> "_AwaitableDict":
        async def _load() -> _Args:
            form = await self._request.form()
            return _Args({key: form[key] for key in form} if hasattr(form, "keys") else {})

        return _AwaitableDict(_load)

    # Quart 允许给 request 赋值属性（上游用于放宽上传上限），这里无操作。
    def __setattr__(self, name: str, value: Any) -> None:  # pragma: no cover - 兼容
        object.__setattr__(self, name, value)

    @property
    def max_content_length(self) -> None:  # pragma: no cover - 兼容
        return None

    @max_content_length.setter
    def max_content_length(self, value: Any) -> None:  # pragma: no cover - 兼容
        return None


request = _RequestProxy()


class _AwaitableDict:
    """可 ``await`` 的字典（Quart 的 ``request.form`` 是协程属性）。"""

    def __init__(self, loader) -> None:
        self._loader = loader
        self._cache: dict[str, Any] | None = None

    async def _load(self) -> dict[str, Any]:
        if self._cache is None:
            self._cache = await self._loader()
        return self._cache

    def __await__(self):
        return self._load().__await__()

    async def get(self, key: str, default: Any = None) -> Any:
        return (await self._load()).get(key, default)


class UploadedFile:
    """Starlette ``UploadFile`` 的 Quart 风格包装。"""

    def __init__(self, upload: Any) -> None:
        self._upload = upload
        self.filename = getattr(upload, "filename", "") or ""
        self.content_type = getattr(upload, "content_type", "") or ""

    async def read(self) -> bytes:
        return await self._upload.read()

    async def save(self, destination: str) -> None:
        data = await self._upload.read()
        with open(destination, "wb") as handle:
            handle.write(data)

    @property
    def stream(self) -> Any:  # 兼容 ``uploaded_file.stream.read``
        return self._upload.file


class UploadedFileStore:
    """``await request.files`` 的等价实现。"""

    def __init__(self, request_obj: Request) -> None:
        self._request = request_obj

    async def _load(self) -> dict[str, UploadedFile]:
        form = await self._request.form()
        files: dict[str, UploadedFile] = {}
        for key in form:
            value = form[key]
            if hasattr(value, "filename"):
                files[key] = UploadedFile(value)
        return files

    def __await__(self):
        return self._load().__await__()


def jsonify(*args: Any, **kwargs: Any) -> JSONResponse:
    """``jsonify`` 的等价实现：单参数即数据，多参数合并为对象。"""
    if len(args) == 1 and not kwargs:
        payload = args[0]
    elif args and not kwargs:
        payload = list(args)
    else:
        payload = dict(kwargs)
    return JSONResponse(content=_json_safe(payload))


def _json_safe(value: Any) -> Any:
    from enum import Enum

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Enum):
        return value.value
    return str(value)


async def make_response(response: Any) -> Response:
    """``make_response`` 的等价实现。"""
    if isinstance(response, Response):
        return response
    if isinstance(response, (dict, list)):
        return jsonify(response)
    if isinstance(response, bytes):
        return Response(content=response)
    if isinstance(response, str):
        media_type = mimetypes.guess_type(response)[0] or "text/plain"
        if response and ("/" in response or "\\" in response):
            from pathlib import Path

            if Path(response).is_file():
                return FileResponse(response, media_type=media_type)
        return Response(content=response, media_type="text/plain; charset=utf-8")
    return Response(content=str(response))


async def send_file(
    path_or_bytes: Any,
    mimetype: str | None = None,
    as_attachment: bool = False,
    attachment_filename: str | None = None,
    download_name: str | None = None,
    **kwargs: Any,
) -> Response:
    """``send_file`` 的等价实现。"""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        headers = {}
        name = download_name or attachment_filename
        if as_attachment and name:
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return Response(
            content=bytes(path_or_bytes),
            media_type=mimetype or "application/octet-stream",
            headers=headers,
        )
    if hasattr(path_or_bytes, "read"):
        data = path_or_bytes.getvalue() if hasattr(path_or_bytes, "getvalue") else path_or_bytes.read()
        headers = {}
        name = download_name or attachment_filename
        if as_attachment and name:
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return Response(
            content=data,
            media_type=mimetype or "application/octet-stream",
            headers=headers,
        )
    from pathlib import Path

    path = Path(str(path_or_bytes))
    filename = download_name or attachment_filename
    return FileResponse(
        path,
        media_type=mimetype or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        filename=filename if as_attachment else None,
    )


class RequestEntityTooLarge(Exception):
    """``werkzeug.exceptions.RequestEntityTooLarge`` 的等价实现。"""


def normalize_result(result: Any) -> Response:
    """把处理器的返回值规范化为 Starlette ``Response``。

    支持：``Response`` / ``(Response, status)`` / ``(body, status)`` /
    ``dict`` / ``list`` / ``str`` / ``None``。
    """
    status = 200
    if isinstance(result, tuple):
        if len(result) == 2:
            result, status = result
        elif len(result) == 3:
            result, status, _headers = result
    if result is None:
        response: Response = JSONResponse(content=None, status_code=status)
    elif isinstance(result, Response):
        response = result
    elif isinstance(result, (dict, list)):
        response = JSONResponse(content=_json_safe(result), status_code=status)
    elif isinstance(result, bytes):
        response = Response(content=result, status_code=status)
    elif isinstance(result, str):
        response = Response(content=result, status_code=status, media_type="text/html; charset=utf-8")
    else:  # pragma: no cover - 防御性
        response = JSONResponse(content=_json_safe(result), status_code=status)
    if status != 200:
        response.status_code = status
    return response


__all__ = [
    "request",
    "jsonify",
    "make_response",
    "send_file",
    "normalize_result",
    "set_current_request",
    "reset_current_request",
    "RequestEntityTooLarge",
    "UploadedFile",
    "StreamingResponse",
    "unquote",
]
