# NoneBot2 迁移方案（Meme Manager）

> 本文档是 `main`（NoneBot2 线路）的架构契约与实施路线图。
> `astrbot` 分支保持与上游 `LoCCai/nonebot_plugin_meme_manager`（实为 AstrBot 插件）一致，仅用于同步。

## 1. 目标

把现有 AstrBot 插件深度适配为 **NoneBot2 插件**：

1. 同时运行在 **OneBot v11**、**OneBot v12**、**QQ 官方机器人**（`nonebot-adapter-qq`）三种协议上；
2. 保留既有业务能力（表情包仓库、资源广场、语义检索、自动收集、图床同步、WebUI）；
3. 代码结构与 NoneBot2 生态契合：`__plugin_meta__`、`PluginConfig`、`Matcher`、`APIRouter`；
4. 上游同步成本可控：业务层尽量零改动，AstrBot 专有入口保持原样不被 NoneBot2 入口加载。

## 2. 现状盘点

| 项目 | 事实 |
| --- | --- |
| 上游 | `LoCCai/nonebot_plugin_meme_manager`，仅 `main` 分支，v5.4.1（`1561b4f`） |
| 实际身份 | AstrBot 插件：`metadata.yaml` 声明 `astrbot_version >= 4.28.2`，使用 `_conf_schema.json`、`pages/` 插件页 |
| 规模 | Python 约 1.5 MB；`mixins/web_api.py` 186 KB、`mixins/event_handlers.py` 126 KB、`mixins/commands.py` 43 KB、`main.py` 60 KB |
| AstrBot 耦合 | 仅 16 个文件 import astrbot |
| 事件 API 使用频次 | `set_extra`×119、`get_extra`×108、`plain_result`×78、`unified_msg_origin`×15、`send`×12、`get_sender_id`×5、`session_id`、`message_str`、`get_platform_name`、`get_result`、`send_streaming`、`persona_id` |
| AstrBot 钩子 | `on_astrbot_loaded`、`event_message_type(ALL)`、`on_waiting_llm_request`、`on_llm_request`、`on_llm_response`、`on_agent_begin/done`、`on_decorating_result`、`after_message_sent`、`llm_tool(search_memes/send_meme/preview)`、`command_group("表情管理")` |
| WebUI 后端 | **Quart** + `werkzeug`，186 KB 路由处理器 |
| WebUI 前端 | `pages/` 静态页，依赖宿主注入的 `window.AstrBotPluginPage` 桥（仅 8 个方法：`ready/getContext/apiGet/apiPost/upload/download/subscribeSSE/unsubscribeSSE`） |
| 纯业务层 | `backend/packs`、`backend/semantic`、`backend/decision`、`backend/auto_collect`、`image_host` —— 平台无关 Python，仅依赖 `logger` |

**核心判断：不需要重写业务层。** 适配面集中在四层：① 事件模型 ② 消息收发 ③ Web 框架 ④ AI 工具桥。

## 3. 总体架构

```
nonebot_plugin_meme_manager/            # 仓库根即插件包
├── __init__.py            # 【改】NoneBot2 入口：__plugin_meta__、require、加载 matchers/web
├── nb2/                   # 【新增】NoneBot2 适配层（本分支专属代码）
│   ├── platform/
│   │   ├── message.py     #   Segment / UnifiedMessage
│   │   ├── event.py       #   UnifiedEvent（兼容 AstrMessageEvent 常用表面）
│   │   ├── capabilities.py#   能力矩阵与降级策略
│   │   ├── registry.py    #   nonebot adapter → 适配器单例
│   │   ├── session.py     #   session_waiter 兼容实现
│   │   └── adapters/
│   │       ├── onebot11.py
│   │       ├── onebot12.py
│   │       └── qq_official.py
│   ├── config_store.py    # 【新增】PluginConfig：_conf_schema.json + data/config.json
│   ├── runtime.py         # 【新增】MemeRuntime（MemeSender.__init__ 的移植）
│   ├── log.py             # 【新增】logger shim（供业务层使用）
│   ├── matchers/
│   │   ├── commands.py    #   表情管理命令组
│   │   ├── pipeline.py    #   消息进站：自动收集 / 概率发送 / 结果装饰
│   │   └── tools.py       #   AI 工具桥（search_memes / send_meme / preview）
│   └── web/
│       ├── app.py         #   FastAPI APIRouter（web_api.py 移植）
│       └── static/bridge.js #  window.AstrBotPluginPage 的 NoneBot2 实现
├── backend/               # 【保留】业务核心（仅改 logger import）
├── image_host/            # 【保留】
├── pages/                 # 【保留】前端零改动（由 bridge.js 提供宿主 API）
├── config.py              # 【改】数据目录改为 NoneBot2 插件数据目录
├── utils.py               # 【改】logger import
├── main.py                # 【保留】AstrBot 入口（本分支不加载，仅随上游同步）
├── mixins/                # 【保留】AstrBot 混入（本分支不加载；移植成果在 nb2/）
├── metadata.yaml          # 【保留】上游元数据
├── _conf_schema.json      # 【保留】配置 schema（WebUI 设置页的唯一事实源）
└── pyproject.toml         # 【新增】NoneBot2 插件元数据与依赖
```

**设计原则：并行入口、共享业务层。** `astrbot` 分支的 `main.py`/`mixins/` 原样保留但不被导入，`nb2/` 是唯一被 NoneBot2 加载的入口代码。上游合并时冲突面仅限 `__init__.py`、`config.py`、`utils.py` 与少量 logger import。

## 4. 协议抽象层

### 4.1 统一消息模型

```python
Segment(type, data)         # type: text|image|at|reply|face|voice|video|file|markdown|keyboard|ark|unknown
UnifiedMessage(segments)    # 提供 plain_text / images() / first_image()
```

图片段统一用 `data["source"] ∈ {url, file, path, base64, bytes}` + 对应值表示，由各适配器在出口处转换。

### 4.2 统一事件

`UnifiedEvent` 只暴露业务层实际用到的表面（与 `AstrMessageEvent` 同名同义），从而让 `commands.py` / `event_handlers.py` 的移植是「换 import + 换返回约定」，而不是重写逻辑：

| 成员 | 语义 |
| --- | --- |
| `message_str` / `get_message_str()` | 纯文本（@ 已剥离） |
| `get_sender_id()` | 平台用户 ID 字符串 |
| `session_id` | 会话 ID（群/私聊/频道） |
| `unified_msg_origin` | `平台:会话类型:会话ID` |
| `get_platform_name()` / `platform_name` | `onebot11` / `onebot12` / `qq_official` |
| `message_obj` | 统一后的消息对象 |
| `get_message_type()` | `group` / `private` / `guild` / `channel` |
| `plain_result(text)` | 构造纯文本出站消息 |
| `await send(msg)` | 经适配器立即发送 |
| `set_extra/get_extra` | 事件级 KV（管线各阶段传递状态） |
| `is_admin` | 由 `SUPERUSERS` + 平台角色推导 |

### 4.3 能力矩阵与降级

| 能力 | OneBot v11 | OneBot v12 | QQ 官方 |
| --- | --- | --- | --- |
| 文本 | ✅ | ✅ | ✅ |
| 图片（URL/本地文件） | ✅ | ✅（先上传得 `file_id`） | ✅（需先上传富媒体） |
| @ 提及 | ✅ | ✅ `mention` | ✅（群聊） |
| 引用回复 | ✅ `reply` | ✅ `reply` | ❌ 降级：不引用 |
| Markdown | ⚠️ 取决于实现 | ⚠️ | ✅（模板受限） |
| 按钮/键盘 | ❌ | ✅（协议支持） | ✅ `keyboard` |
| 主动推送 | ✅ | ✅ | ❌ 受被动回复窗口限制 |
| 消息 ID | ✅ | ✅ | ✅ `msg_id` 必须回填 |

**降级策略**：适配器在 `send()` 前用 `capabilities` 过滤不支持的段；`reply` 不支持时剥离并保留文本；QQ 官方的被动窗口用 `msg_id` 回填，超窗则记录告警并放弃推送。

### 4.4 协议映射要点

- **OneBot v11**：`GroupMessageEvent`/`PrivateMessageEvent`；`MessageSegment.image(file="file:///...")` 发送本地图；`bot.send(event, msg)`。
- **OneBot v12**：消息是 `list[Segment(type, data)]`，事件按 `detail_type`（`private`/`group`/`channel`）分流，群 ID/频道 ID 字段不同；`alt_message` 可作纯文本兜底。
- **QQ 官方**：`C2CMessageCreateEvent`（`openid`）、`GroupAtMessageCreateEvent`（`group_openid`）、`GuildMessageEvent`（`guild_id`+`channel_id`）；发送需 `msg_id`；富媒体需先 `post_c2c_files` / `post_group_files` 上传。适配器需实现「图片先上传后发送」的两段式逻辑。

## 5. 关键决策

| 议题 | 决策 | 理由 |
| --- | --- | --- |
| 配置存储 | 保留 `_conf_schema.json` 作为唯一事实源，新建 `PluginConfig` 类（dict + `get(path)`/`save()`），数据落 `data/meme_manager/config.json` | 现有代码全部通过 `self._read_config_value((...))` 读配置；设置页也从 schema 渲染。避免手写 200+ 字段的 Pydantic 模型 |
| 数据目录 | `nonebot.get_driver().config` 无关，走插件数据目录（`nonebot_plugin_localstore` 或自管 `data/meme_manager`） | 与 AstrBot 的 `get_astrbot_plugin_data_path()` 解耦 |
| 多轮交互 | 自研 `session_waiter` 兼容层：`on_message(priority=1)` 分发器 + 按 `umo+sender` 键控的 Future 注册表 | NoneBot2 无内置等待器；保持原 `@session_waiter` 装饰器调用形状，移植零改动 |
| AI 工具 | 抽象出 `MemeTool` 注册表（name/description/JSON Schema/handler）。默认暴露为插件内 LLM 客户端可调用的函数；若检测到常见 LLM 插件则注册为工具 | NoneBot2 无宿主 LLM 管线，不能沿用 `llm_tool` 装饰器 |
| 出站修饰 | AstrBot 的 `on_decorating_result`/`after_message_sent` 在 NoneBot2 无对应钩子 → 改为「插件自有发送路径」：所有表情发送统一走 `MemeSender` 服务，管线状态用 `event.set_extra` 传递 | 保真度换可维护性；不再劫持宿主输出 |
| WebUI | Quart→FastAPI（`APIRouter` 挂 driver），前端零改动，新增 `bridge.js` 实现 `window.AstrBotPluginPage` | 8 个桥方法即可复用 100% 前端资产 |
| 管理权限 | `SUPERUSERS` ∪ 平台管理员角色（v11 `sender.role=admin/owner`、QQ 官方群主/管理员） | 跨协议统一 |

## 6. 阶段计划

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| **P0 分支** ✅ | `origin`→`upstream`；`main`→`astrbot`（track `upstream/main`）；`astrbot` 基点新建 `main` | `git branch -vv` 显示两条线路 |
| **P1 骨架** | `nb2/` 目录、`log.py`、`config_store.py`、`pyproject.toml`、根 `__init__.py` 入口、数据目录 | `nonebot.load_plugin()` 成功、`/meme_manager` 可访问 |
| **P2 协议层** | `UnifiedEvent`/`UnifiedMessage`/`Segment`/能力矩阵/三适配器/`session_waiter` | 纯转换单测全绿；不依赖 nonebot 也能跑 |
| **P3 命令** | `表情管理` 命令组全部子命令 | 三协议下命令可用、管理员校验生效 |
| **P4 管线** | 自动收集、概率发送、语义检索、结果发送 | 群消息可自动收集并回报 |
| **P5 WebUI** | Quart→FastAPI 全量路由 + `bridge.js` | 四个页面可打开、增删改查可用 |
| **P6 AI 工具** | `search_memes`/`send_meme`/`preview` 工具注册表 | 工具可被调用并返回图片 |
| **P7 测试** | 适配层单测 + `nonebug` 集成测试 + 三协议冒烟 | CI 全绿 |

## 7. 上游同步流程

```bash
git switch astrbot
git fetch upstream
git merge --ff-only upstream/main   # 只快进，不产生本地提交
git switch main
git merge astrbot                   # 冲突集中于入口文件
```

**纪律**：
- `astrbot` 分支**禁止本地提交**，始终保持与上游一致，便于 `--ff-only`；
- `main` 分支不为业务层代码做「风格性改写」，只在必要处改 `logger` import，降低冲突面；
- `nb2/` 为新增目录，天然无冲突。

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 上游重构 `backend/` 接口 | 合并冲突 | 业务层零改写；冲突按上游版本重放小补丁 |
| QQ 官方被动回复窗口 | 无法主动发送表情 | 仅在交互窗口内发送；超窗降级为文本提示 |
| OneBot v12 生态实现差异大 | 图片发送失败 | 能力探测 + 上传 `file_id` 两段式，失败回退 base64 |
| 186 KB WebAPI 移植量大 | 回归风险 | 逐端点移植 + 端点级契约测试；前端不变即可验证 |
| 无宿主 LLM，AI 功能语义变化 | 用户体验差异 | 保留插件内 LLM 决策路径；工具化后可选接入 |

## 9. 测试策略

1. **纯转换单测**（无 nonebot 依赖）：`Segment`/`UnifiedMessage` ↔ 各协议消息的双向转换、能力降级、`umo` 生成、`session_id` 归一化；
2. **配置单测**：schema 默认值、缺失键、非法值回退、保存回读；
3. **适配器单测**：用 mock bot 验证 `send()` 的调用参数（含 QQ 两段式上传）；
4. **集成测试**：`nonebug` 驱动 OneBot v11 假事件，验证命令、自动收集与 Web API；
5. **冒烟**：`nonebot.load_plugin()` + 启动 driver，确认路由挂载与 matcher 注册。