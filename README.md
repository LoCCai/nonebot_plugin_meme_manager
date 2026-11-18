# 表情包管理器（NoneBot2 线路）

> 这是 `LoCCai/nonebot_plugin_meme_manager` 的 **七七 / QWeb 专用线路**（`qiqi` 分支），
> 由原 `feat/moellm-library-20261118` 更名，包含 LLM 分类词库接入与 QWeb 管理适配。
> **通用 NoneBot2 插件继续使用默认 `main` 分支**，不依赖七七或 QWeb。
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

管理员默认仅限 NoneBot2 `SUPERUSERS`；`integration.superusers_only=false`
时才同时允许平台管理员/群主管理共享图库。

## WebUI

资源广场首次进入自动获取目录；已有缓存直接显示，点击「刷新资源」再查询各包最新版本。
安装多个包后，在「设置中心 → 使用规则」的默认或指定会话规则中多选资源包并保存，
即可合并使用；第一个包是主包，同名分类描述优先。安装时的「设为默认」是切换默认包，
不是把新包追加到已有多包规则。

分类词库选图无需向量模型。只有精确语义检索、图片描述建库才需要视觉/Embedding
提供方与本地 FAISS。当前七七分类接入复用聊天 LLM；语义提供方不会自动继承 QWeb
账户模型，NoneBot2 的声明式提供方也尚未实现 Embedding 请求执行。不要把普通聊天
模型填写为向量模型；接 New API 的 Embedding 路由需要独立的调用适配与模型维度配置。

七七部署通过 QWeb 管理员控制台的「表情资源管理」使用完整 WebUI，入口为
`/qweb/admin/memes`。宿主对页面、图片与接口统一执行管理员登录、写入校验及审计，
复用当前插件运行时和数据目录；无需开启下述独立 WebUI。LLM 随文表情使用
`nonebot-plugin-moellmchats` 的 `qiqi` 分支，模型与账户设置仍由宿主管理。
本仓库不携带站点凭据。生产依赖请固定经过验收的提交 SHA。

WebUI 默认关闭；仅在入口已配置访问认证的情况下开启 `webui.enabled`，重启后
访问 `http://<host>:<port>/meme_manager/`。接口本身不提供用户认证，不要直接暴露公网。
前端沿用上游页面，
`nb2/web/static/bridge.js` 提供 `window.AstrBotPluginPage` 的 NoneBot2 实现，
所有 API 位于 `/meme_manager/api/*`。

## 配置

配置由插件携带的 `_conf_schema.json` 驱动，存放于数据目录：

```
data/meme_manager/config.json
```

- 可在 WebUI「设置中心」编辑；
- 也可直接改 JSON 后重启；
- 数据目录解析顺序：`MEME_MANAGER_DATA_DIR` 环境变量或 NoneBot `.env` 配置 → `nonebot-plugin-localstore`
  → 运行目录 `data/meme_manager`。

## MoEllmChats 随文表情

支持新版 MoEllmChats 的 `emotion_source: "meme_manager"`：读取会话已启用资源包的
分类和词库，由原聊天模型选择分类，本地挑选图片交给聊天插件发送。无需独立分类模型、
视觉打标或向量索引；`official-001` 自带词库，其他包可使用已有词库或内置基础词表。
`generation.prompt.lexicon_enabled` 控制词库提示。无图片的分类不进入提示词。

只使用此模式时，保持 `semantic.enabled=false`、`auto_collect.enabled=false`，
无需配置任何模型提供商。独立语义工具需额外开启 `integration.semantic_tools_enabled`
及语义检索依赖；工具名使用 `meme_library_search` / `meme_library_send`，
不会替换聊天插件原有的表情生成工具。

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
