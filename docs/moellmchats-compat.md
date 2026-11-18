# MoEllmChats 兼容性研判（`LoCCai/nonebot-plugin-moellmchats`）

> 结论先行：**不需要额外的"LLM 兼容层"**，但需要一层**很薄的工具注册适配**
> （约 100 行，可选依赖）。它的 LLM 运行时自带 Provider/模型路由，不会消费我们的
> `nb2.llm.LLMContext`；它对外提供的扩展点是**强类型 `ToolSpec` 注册表**，正好
> 与 `nb2/tools.py` 的 `MemeTool` 一一对应。

## 1. 对方面貌（已实读源码与 `docs/plugin-integration.md`）

| 维度 | 事实 |
| --- | --- |
| 规模 | 约 120 个模块、4.5 MB 级；`tool_manager.py` 139 KB、`llm_tools.py` 97 KB |
| LLM 运行时 | 自带 `moe_llm.py` + `model_routing.py` + `model_capabilities.py`，直连各家 API（`moellmchats` 自己的 `data["messages"]` / `tools`） |
| 工具接入方式 | ① 兼容调用已有 Matcher（合成 OneBot 消息触发插件命令，单参数 `command`，保守标记为 `mutating`）；② **强类型 `ToolSpec`**（推荐，主进程执行，含 effect / permission / timeout / 参数 Schema / 二阶段确认） |
| 工具结果契约 | `ToolResult(text, images, metadata, files, structured, citations)`；`images` 是 **URL/引用字符串数组**，被注入到下一轮视觉模型消息 |
| 与 NoneBot 的关系 | 标准 NoneBot2 插件；`require("nonebot_plugin_moellmchats")` 后 `register_tool(ToolSpec(...))` |
| 工具发现 | `ToolSpec` 注册表 + 插件元数据/PicMenu 菜单；不存在"从别的插件读取 `tools` 属性"的自动发现 |

## 2. 我们这边的现状（`nb2`）

- `nb2/tools.py`：`MemeTool(name, description, parameters(JSON Schema), handler)` +
  `ToolRegistry`，已实现 `search_memes` / `send_meme` / `preview_memes`；
- `nb2/matchers/tools_cmds.py` 已导出 `get_tools()`（schema 列表）与
  `call_tool(name, event, arguments)` 作为对外调用点；
- `nb2/llm.py` 是我们自己的 OpenAI 兼容客户端，**只服务插件内部**（打标/检索/选拔），
  与宿主 LLM 无关。

## 3. 是否需要"额外的 LLM 兼容设计"

**不需要**，理由：

1. **不共享 LLM 运行时**。MoEllmChats 有完全独立的模型路由与 API Key 管理，插件无法也
   不应该把 `LLMContext` 注入进去；反之，也不需要它来驱动我们的打标/检索。
2. **它没有"LLM 工具钩子"**可挂。它不提供 `register_llm_tool` / 事件钩子让别的插件
   向它注册 prompt 或 llm_tool；唯一受支持的扩展面是 `ToolSpec` 与"兼容 Matcher 调用"。
3. **语义匹配点已经对齐**：我们的三个工具是"查表情 → 发表情"的两段式，正好适配
   `ToolSpec` 的 `parameters` + `handler` + `effect`（`search_memes` 只读，
   `send_meme` 为 `mutating` 从而走它自己的二阶段确认）。

## 4. 真正需要写的适配（建议实现，约 1 个模块）

新增 `nb2/integrations/moellm.py`（**可选依赖**，未安装 MoEllmChats 时静默跳过）：

```python
from nonebot import require
require("nonebot_plugin_moellmchats")
from nonebot_plugin_moellmchats import (
    ToolContext, ToolEffect, ToolResult, ToolSpec, register_tool,
)

async def search_memes(query: str, top_k: int = 3, *, _tool_context: ToolContext) -> ToolResult:
    event = _tool_context.event          # 原生 nonebot 事件
    unified = to_unified_event(_tool_context.bot, event)
    result = await REGISTRY.get("search_memes").call(unified, runtime, {"query": query, "top_k": top_k})
    if not result.ok:
        return ToolResult(text=result.message)
    return ToolResult(
        text=result.message,
        structured={"candidates": result.data.get("candidates", [])},
    )

register_tool(ToolSpec(
    name="meme_search", description="按情绪/语义检索表情包候选……",
    parameters={...},                       # 与 MemeTool.parameters 同构，直接复用
    handler=search_memes,
    effect=ToolEffect.READ_ONLY, permission="user", timeout_seconds=15,
))
```

要点（实读源码得到的确切契约）：

- handler 的注入参数名是 **`_tool_context: ToolContext`**，它带 `bot` / `event` /
  `request_id` / `confirmed`；
- `ToolResult.images` 是**字符串数组**（URL 或它认可的引用），不能塞本地 Path；
  要发本地图，应走 `send_meme` 的 `mutating` 工具并让它自己 `await event.send(...)`，
  或把图片先转成 `data:` / http URL 再放进 `images`（注意它最多保留
  `max_tool_images` 张，默认 4）；
- `ToolName` 只能 `[A-Za-z0-9_-]{1,64}`，中文名会被拒；参数 Schema 会做严格校验
  （`additionalProperties: false` 建议显式声明）；
- `mutating` 工具必须是 `async` handler，否则它直接拒绝执行（防超时后仍产生副作用）；
- 它的兼容 Matcher 路线（方式一）对本插件**不可靠**：我们的命令是
  `/表情管理 发送 meme:xxxx`，而它只允许模型生成 `command` 字符串、且会把整条消息合成
  后定向投递给目标 Matcher——语义检索候选是一次性事件状态（`event extras`），
  跨事件不保留，因此**不要**指望用方式一驱动多轮选图；必须用 `ToolSpec`。

## 5. 需要对方补齐的能力（若希望更深的集成）

| 缺口 | 影响 | 建议 |
| --- | --- | --- |
| 没有"插件之间共享 LLM 客户端"的约定 | 我们与它各自维护一套 Provider 配置 | 各自独立即可，不必强行统一；把两边的 `base_url/model` 配置项对齐即可 |
| `ToolResult.images` 不接受本地路径 | 发本地表情必须走 `send_meme` | 已按此设计，无需改动 |
| 没有跨会话的候选缓存契约 | 我们的 `search_memes` 缓存挂在 `UnifiedEvent.extras` 上，一次工具调用内有效 | 保持"同一轮内先搜后发"语义；这也是上游 AstrBot 的语义 |
| 未公开 `register_tool` 的卸载 API | 插件热重载时旧 spec 会残留 | `ToolRegistry` 有 `unregister(name)`，可用它做幂等重注册 |

## 6. 落地顺序建议

1. **P0（已完成）**：本仓库的 `nb2/tools.py` 与 `nb2/matchers/tools_cmds.py` 已把工具
   与协议解耦，`get_tools()` / `call_tool()` 是唯一对外面；
2. **P1（建议下一步）**：新增 `nb2/integrations/moellm.py`，用 `register_tool` 注册
   `meme_search`（只读）与 `meme_send`（mutating，走它的确认机制）；
3. **P2**：在 `tools/smoke_load.py` 增加"若安装了 MoEllmChats 则校验工具已注册"的
   分支，并补一个单测用假的 `ToolContext` 驱动 handler。
