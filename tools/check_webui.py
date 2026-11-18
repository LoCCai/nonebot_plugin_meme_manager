"""WebUI 端到端自检（CI 与本地都可跑）。

用 FastAPI TestClient 驱动真实路由，逐页、逐类关键端点检查状态码与响应结构，
覆盖前端实际调用的接口（含前端 JS 里出现的全部 API 路径）。

退出码非 0 表示有检查失败。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / "tools"))
import _pkg_alias  # noqa: E402  （把本仓库插件包统一别名为 repo）

_pkg_alias.install()
os.environ.setdefault("MEME_MANAGER_DATA_DIR", tempfile.mkdtemp(prefix="meme_webui_"))

from repo.nb2 import bootstrap  # noqa: E402

bootstrap.ensure()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from repo.nb2.runtime import MemeRuntime  # noqa: E402
from repo.nb2.web import ROUTER_PREFIX, get_router  # noqa: E402
from repo.nb2.web.bridge import register_routes  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def main() -> int:
    runtime = MemeRuntime()

    app = FastAPI()
    router = get_router()
    count = register_routes(router, runtime)
    app.include_router(router)
    client = TestClient(app)

    print(f"[1] 已注册 {count} 条 API 路由，前缀 {ROUTER_PREFIX}")
    check("API 路由数量", count > 60, f"{count} 条")

    # ---- 页面可达性（四个页面 + 入口 + 桥）
    pages = [
        f"{ROUTER_PREFIX}/app/index.html",
        f"{ROUTER_PREFIX}/app/a_manage/index.html",
        f"{ROUTER_PREFIX}/app/catalog/index.html",
        f"{ROUTER_PREFIX}/app/semantic/index.html",
        f"{ROUTER_PREFIX}/app/settings/index.html",
        f"{ROUTER_PREFIX}/bridge.js",
    ]
    for page in pages:
        response = client.get(page)
        check(f"页面 {page}", response.status_code == 200, str(response.status_code))

    # 静态资源（前端依赖）
    for asset in (
        "app/shared/ui.css",
        "app/shared/ui.js",
        "app/shared/fa.min.css",
        "app/a_manage/script.js",
        "app/settings/config.js",
    ):
        response = client.get(f"{ROUTER_PREFIX}/{asset}")
        check(f"静态资源 {asset}", response.status_code == 200, str(response.status_code))

    # ---- 鉴权桥
    response = client.get(f"{ROUTER_PREFIX}/api/bridge/auth_token")
    check("bridge/auth_token 未授权=401", response.status_code == 401)
    response = client.get(
        f"{ROUTER_PREFIX}/api/bridge/auth_token",
        headers={"Authorization": "Bearer demo-token"},
    )
    check(
        "bridge/auth_token 授权=200 且回显 token",
        response.status_code == 200 and response.json().get("token") == "demo-token",
    )

    # ---- 只读端点（前端首屏会调用）
    readonly = [
        "preview/manifest",
        "emoji",
        "emotions",
        "packs",
        "settings/config",
        "settings/rules",
        "settings/targets",
        "sync/status",
        "img_host/sync/status",
        "semantic/status",
        "auto-collect/inbox",
        "community/index/cache",
    ]
    for endpoint in readonly:
        response = client.get(f"{ROUTER_PREFIX}/api/{endpoint}")
        ok = response.status_code == 200
        # 部分端点需要 pack_id 时返回 400/409 也属预期，不能 5xx。
        if not ok and response.status_code in {400, 404, 409, 422}:
            ok = True
            detail = f"{response.status_code}（可预期的参数/状态错误）"
        else:
            detail = str(response.status_code)
        check(f"GET {endpoint}", ok, detail)
        if response.status_code == 200:
            try:
                json.loads(response.text)
            except Exception:
                check(f"GET {endpoint} 返回 JSON", False, response.text[:80])

    # ---- 写端点：创建一个本地表情包，验证写链路 + 资源广场数据
    response = client.post(
        f"{ROUTER_PREFIX}/api/packs/create",
        json={"name": "自检包", "description": "WebUI 自检", "pack_id": "webui-check"},
    )
    check("POST packs/create=200", response.status_code == 200, f"{response.status_code} {response.text[:200]}")
    created = response.status_code == 200
    if created:
        payload = response.json()
        check("创建返回 pack_id", payload.get("pack_id") == "webui-check", str(payload)[:160])

        response = client.get(f"{ROUTER_PREFIX}/api/packs")
        packs = response.json().get("packs", []) if response.status_code == 200 else []
        ids = {str(item.get("id")) for item in packs}
        check("已安装列表含新包", "webui-check" in ids, str(sorted(ids)))

        response = client.get(f"{ROUTER_PREFIX}/api/packs/webui-check")
        check("GET packs/<id>=200", response.status_code == 200, str(response.status_code))

        # 配置读写：设置页保存需要带上当前 revision（乐观并发控制）
        snapshot = client.get(f"{ROUTER_PREFIX}/api/settings/config")
        revision = (
            snapshot.json().get("revision") if snapshot.status_code == 200 else None
        )
        check("设置快照可取 revision", bool(revision), str(revision))

        response = client.post(
            f"{ROUTER_PREFIX}/api/settings/config",
            json={
                "revision": revision,
                "changes": {"webui.preview_concurrency": 3},
            },
        )
        check(
            "POST settings/config=200",
            response.status_code == 200,
            f"{response.status_code} {response.text[:180]}",
        )
        if response.status_code == 200:
            body = response.json()
            check("保存后 applied=True", body.get("applied") is True, str(body)[:160])
            saved = client.get(f"{ROUTER_PREFIX}/api/settings/config")
            check("设置回读=200", saved.status_code == 200)
            fields = saved.json().get("fields") or []
            current = next(
                (
                    item
                    for item in fields
                    if item.get("path") == "webui.preview_concurrency"
                ),
                {},
            )
            check(
                "设置值已生效",
                current.get("value") == 3,
                f"value={current.get('value')}",
            )

        # 表情图未上传时，图片端点应 400/404 而不是 500
        response = client.get(
            f"{ROUTER_PREFIX}/api/meme_image",
            params={"category": "happy", "filename": "nope.png"},
        )
        check(
            "meme_image 缺失文件非 5xx",
            response.status_code < 500,
            str(response.status_code),
        )

        # 清理：卸载自检包
        response = client.post(
            f"{ROUTER_PREFIX}/api/packs/uninstall", json={"pack_id": "webui-check"}
        )
        check("POST packs/uninstall 非 5xx", response.status_code < 500, str(response.status_code))

    print()
    if failures:
        print(f"失败 {len(failures)} 项：" + ", ".join(failures))
        return 1
    print("WebUI 自检全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
