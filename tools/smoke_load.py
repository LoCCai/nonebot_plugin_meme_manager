"""插件加载冒烟测试：真实 nonebot 驱动 + FastAPI + WebUI。

运行：
    python tools/smoke_load.py

退出码非 0 表示插件无法在 NoneBot2 中加载或 WebUI 不可用。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

os.environ.setdefault("MEME_MANAGER_DATA_DIR", tempfile.mkdtemp(prefix="meme_smoke_"))
os.environ.setdefault("ENVIRONMENT", "prod")

import nonebot  # noqa: E402

nonebot.init(driver="~fastapi", superusers={"1001"})

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "OK  " if condition else "FAIL"
    print(f"[{status}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


# 1. 导入插件（触发 __plugin_meta__ 与兼容层安装）
try:
    plugin = nonebot.load_plugin("repo")
    check("load_plugin(repo)", plugin is not None, str(plugin))
except Exception as exc:  # pragma: no cover - 冒烟脚本
    check("load_plugin(repo)", False, f"{type(exc).__name__}: {exc}")
    raise SystemExit(1)

import repo  # noqa: E402

check("__plugin_meta__", hasattr(repo, "__plugin_meta__"))
check("supported_adapters", "~onebot.v11" in (repo.__plugin_meta__.supported_adapters or set()))

# 2. 注册 matcher
result = repo.register_matchers()
commands = result["commands"]
check("命令 matcher", len(commands) >= 4, f"{len(commands)} 个：{', '.join(commands)}")
check("消息管线 matcher", len(result["pipeline"]) == 1)

# 3. 运行时与数据目录
runtime = repo.get_runtime()
check("运行时数据目录", runtime.data_dir.is_dir(), str(runtime.data_dir))
check("默认表情包清单", (runtime.packs_dir / "builtin-default" / "manifest.json").is_file())
check("分类描述", bool(runtime.category_mapping), f"{len(runtime.category_mapping)} 个分类")

# 4. WebUI 挂载
try:
    runtime_ok = repo.register_matchers is not None
    from repo.nb2.web import mount_webui

    mounted = mount_webui(runtime)
    check("WebUI 挂载", mounted)
except Exception as exc:  # pragma: no cover - 冒烟脚本
    check("WebUI 挂载", False, f"{type(exc).__name__}: {exc}")
    raise SystemExit(1)

driver = nonebot.get_driver()
server_app = getattr(driver, "server_app", None)
check("FastAPI server_app", server_app is not None)

# 5. 通过 ASGI 客户端访问页面与 API
if server_app is not None:
    from fastapi.testclient import TestClient

    client = TestClient(server_app)
    page = client.get("/meme_manager/app/index.html")
    check("页面可访问", page.status_code == 200, str(page.status_code))

    bridge = client.get("/meme_manager/bridge.js")
    check("bridge.js 可访问", bridge.status_code == 200)

    emoji = client.get("/meme_manager/api/emoji")
    check("API /emoji 可用", emoji.status_code == 200, emoji.text[:120])

# 6. 协议适配器齐全
from repo.nb2.platform.adapters import get_adapter  # noqa: E402

for name in ("onebot11", "onebot12", "qq_official"):
    check(f"适配器 {name}", get_adapter(name) is not None)

print()
if failures:
    print(f"失败 {len(failures)} 项：" + ", ".join(failures))
    raise SystemExit(1)
print("全部检查通过。")
