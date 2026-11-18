# 表情包管理器（NoneBot2 线路）

> 这是 `LoCCai/nonebot_plugin_meme_manager` 的 **NoneBot2 适配线路**（`main` 分支）。
> 原始 AstrBot 插件仓库为 `anka-afk/astrbot_plugin_meme_manager`，对应 `astrbot` 分支，
> 仅用于上游同步。本分支支持 **OneBot v11 / OneBot v12 / QQ 官方机器人**。

## 能力

- 🖼️ **表情包仓库**：多表情包、分类、描述、图片增删改查
- 🎯 **AI 配图**：语义检索候选 → 后端（chat / 决策服务）选拔 → 发送
- 📥 **自动收集**：群聊图片按视觉模型打标后进入待审核队列
- ☁️ **图床同步**：StarDots / Cloudflare R2 / Lsky / WebDAV
- 🕸️ **WebUI**：`/meme_manager/` 下的管理页、资源广场、语义化、设置中心
- 🧩 **协议无关**：全部业务逻辑跑在统一事件模型上，三协议共享同一套命令

## 安装

```bash
# 在 NoneBot2 机器人工程中
nb plugin install nonebot_plugin_meme_manager
# 或开发模式：把本仓库放入 plugins/ 目录
```

```toml
# pyproject.toml
[tool.nb]
plugins = ["nonebot_plugin_meme_manager"]

[tool.nonebot]
adapters = [
  { name = "OneBot V11", module_name = "nonebot.adapters.onebot.v11" },
  { name = "OneBot V12", module_name = "nonebot.adapters.onebot.v12" },
  { name = "QQ", module_name = "nonebot.adapters.qq" },
]
driver = "~fastapi"
```

可选依赖：

```bash
pip install "nonebot_plugin_meme_manager[semantic]"   # 语义检索（faiss）
pip install "nonebot_plugin_meme_manager[image-host]" # Cloudflare R2 图床
pip install "nonebot_plugin_meme_manager[qq]"         # QQ 官方机器人
```

## 指令

| 指令 | 说明 | 权限 |
| --- | --- | --- |
| `/表情管理 帮助` | 查看可用指令 | 所有人 |
| `/表情管理 查看图库` | 列出分类 | 所有人 |
| `/表情管理 图库统计` | 统计图片数量 | 所有人 |
| `/表情管理 搜索 [描述]` | 语义检索候选表情 | 所有人 |
| `/表情管理 发送 [ID]` | 发送候选表情 | 所有人 |
| `/表情管理 添加分类 [名称] [描述]` | 新建分类 | 管理员 |
| `/表情管理 添加表情 [分类]` | 后发图片入库 | 管理员 |
| `/表情管理 收集表情` | 收集引用图片 | 管理员 |
| `/表情管理 开始收集` / `结束收集` | 连续收集 | 管理员 |
| `/表情管理 清空指定类型` / `清空全部` | 清空图片 | 管理员 |
| `/表情管理 同步状态` / `同步到云端` / `从云端同步` | 图床同步 | 管理员 |

管理员判定：NoneBot2 `SUPERUSERS`，或平台角色为管理员/群主。

## WebUI

启动后访问 `http://<host>:<port>/meme_manager/`。前端沿用上游页面，
`nb2/web/static/bridge.js` 提供 `window.AstrBotPluginPage` 的 NoneBot2 实现，
所有 API 位于 `/meme_manager/api/*`。

## 配置

配置由插件携带的 `_conf_schema.json` 驱动，存放于数据目录：

```
data/meme_manager/config.json
```

- 可在 WebUI「设置中心」编辑；
- 也可直接改 JSON 后重启；
- 数据目录解析顺序：`MEME_MANAGER_DATA_DIR` 环境变量 → `nonebot-plugin-localstore`
  → 运行目录 `data/meme_manager`。

## 与上游同步

```bash
git switch astrbot && git fetch upstream && git merge --ff-only upstream/main
git switch main && git merge astrbot
git push origin main astrbot
```

详见 [MIGRATION_PLAN.md](MIGRATION_PLAN.md)。

## 测试

```bash
python -m pytest tests/nb2 -q      # 单元/集成测试
python tools/smoke_load.py         # 插件加载冒烟（驱动 + matcher + WebUI）
```

## 许可

[LICENSE](LICENSE)
