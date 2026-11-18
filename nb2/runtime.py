"""插件运行时：MemeSender.__init__ 的 NoneBot2 移植。

原 AstrBot 插件把「配置读取 + 各类管理器实例化 + 提示词构建」全部塞在
``MemeSender.__init__`` 里，并混用 ``Star`` 基类与 mixin。这里把这些**与协议无关**
的运行时状态原样搬过来，作为 NoneBot2 matcher 共享的单例服务：

* 配置来自 ``PluginConfig``（``_conf_schema.json`` 驱动）；
* LLM 能力来自 ``nb2.llm.LLMContext``（替代 AstrBot ``Context``）；
* 语义任务、自动收集、手动收集、决策、图床等管理器与上游一致。

matcher 层只做三件事：协议事件 → ``UnifiedEvent``、调用本运行时的业务方法、
把结果写回事件。
"""

from __future__ import annotations

import asyncio
import copy
import re
from pathlib import Path
from typing import Any

from . import astrbot_types
from .astrbot_types import MessageSession
from .config_store import PluginConfig
from .llm import LLMContext, providers_from_config
from .log import logger
from .paths import PLUGIN_NAME, resolve_data_dir, resolve_schema_path

WEBUI_LOG_PREFIX = f"[{PLUGIN_NAME}][WebUI]"

#: 与上游一致的提示词标记。
MEME_PROMPT_MARKER_START = "<!-- meme_manager_prompt:start -->"
MEME_PROMPT_MARKER_END = "<!-- meme_manager_prompt:end -->"
SEMANTIC_PROMPT_MARKER_START = "<!-- meme_manager_semantic_prompt:start -->"
SEMANTIC_PROMPT_MARKER_END = "<!-- meme_manager_semantic_prompt:end -->"

DEFAULT_CATEGORY_DESCRIPTIONS: dict[str, str] = {}
DEFAULT_PACK_ID = "builtin-default"


def _load_default_categories() -> dict[str, str]:
    """从插件 ``config.py`` 复用上游的默认分类描述，避免两处维护。"""
    global DEFAULT_CATEGORY_DESCRIPTIONS
    if DEFAULT_CATEGORY_DESCRIPTIONS:
        return DEFAULT_CATEGORY_DESCRIPTIONS
    try:
        from ..config import DEFAULT_CATEGORY_DESCRIPTIONS as upstream_defaults

        DEFAULT_CATEGORY_DESCRIPTIONS = dict(upstream_defaults)
    except Exception:  # pragma: no cover - 防御性
        DEFAULT_CATEGORY_DESCRIPTIONS = {}
    return DEFAULT_CATEGORY_DESCRIPTIONS


def normalize_trigger_scope(value: Any) -> str:
    """规范化表情触发范围配置。"""
    scope = str(value or "").strip().lower()
    return scope if scope in {"only_chat_llm", "all_llm"} else "only_chat_llm"


class MemeRuntime:
    """表情包管理器运行时（单例）。

    Args:
        plugin_config: 插件配置；缺省时自动从磁盘载入。
        context: LLM 能力上下文；缺省时按配置构造。
    """

    def __init__(
        self,
        plugin_config: PluginConfig | None = None,
        context: LLMContext | None = None,
        *,
        data_dir: Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else resolve_data_dir(PLUGIN_NAME)
        # 业务层与 WebUI 通过 ``<pkg>.config`` 的模块级常量定位文件，先把它们
        # 绑定到本运行时的数据目录，避免沿用 AstrBot 的路径。
        from .paths_bind import bind_module_paths

        bind_module_paths(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_layout()
        self.config = plugin_config or PluginConfig.load(
            self.data_dir / "config.json",
            schema_path=resolve_schema_path(),
        )
        _load_default_categories()

        # 数据目录相关路径（与上游 config.py 导出的常量语义一致）。
        self.packs_dir = self.data_dir / "packs"
        self.registry_path = self.data_dir / "registry.json"
        self.selection_rules_path = self.data_dir / "selection_rules.json"
        self.backup_dir = self.data_dir / "backup"
        self.temp_dir = self.data_dir / "temp"

        # LLM 能力：业务层通过 self.context 访问 get_provider_by_id / llm_generate。
        self.context = context or LLMContext(
            providers_from_config(self._llm_config_section())
        )

        # ---- 语义检索 ----
        from ..backend.semantic.models import runtime_category_mapping  # noqa: F401
        from ..backend.semantic.task import SemanticTaskManager

        self.semantic_enabled = self._read_bool(("semantic", "enabled"))
        self.semantic_vision_provider_id = self._read_str(
            ("semantic", "vision_provider_id")
        )
        self.semantic_embedding_provider_id = self._read_str(
            ("semantic", "embedding_provider_id")
        )
        self.semantic_top_k = int(self._read_config_value(("semantic", "top_k"), 5) or 5)
        self.semantic_min_score = float(
            self._read_config_value(("semantic", "min_score"), 0.25)
        )
        self.semantic_task_manager = SemanticTaskManager(
            self.data_dir,
            context=self.context,
            config={
                "vision_provider_id": self.semantic_vision_provider_id,
                "embedding_provider_id": self.semantic_embedding_provider_id,
            },
        )

        # ---- 分类与图床 ----
        from ..backend.packs.categories import CategoryManager

        self.category_manager = CategoryManager()

        self.img_sync = None
        self.img_sync_config: dict[str, Any] | None = None
        self.img_sync_provider_type: str | None = None
        self._img_sync_pack_id = ""
        self._last_img_host_sync_task_status = None
        self._img_host_sync_monitor = None
        self._community_install_jobs: dict[str, Any] = {}
        self._community_install_tasks: set[Any] = set()
        self._init_image_host()

        # ---- 上传与决策 ----
        self.upload_states: dict[str, Any] = {}
        self.pending_images: dict[str, Any] = {}
        decision_config = self._read_path(("decision_service",), {})
        from ..backend.decision import DecisionClient

        self.decision_client = DecisionClient(
            decision_config if isinstance(decision_config, dict) else {}
        )

        from ..backend.auto_collect import AutoCollectManager
        from ..backend.manual_collect import ManualCollectManager

        auto_collect_config = self._read_path(("auto_collect",), {})
        self.auto_collect_manager = AutoCollectManager(
            self,
            {
                **(
                    auto_collect_config
                    if isinstance(auto_collect_config, dict)
                    else {}
                ),
                "auto_embedding": bool(
                    self._read_path(("semantic", "auto_collect_embedding"), True)
                ),
            },
        )
        self.manual_collect_manager = ManualCollectManager(self.auto_collect_manager)

        # ---- 提示词与生成参数 ----
        self.prompt_head = self._read_str(("generation", "prompt", "head"))
        self.prompt_tail = self._read_str(("generation", "prompt", "tail"))
        self.prompt_examples = [
            (
                bool(self._read_config_value(("generation", "prompt", f"{name}_enabled"), False)),
                self._read_str(("generation", "prompt", name)),
            )
            for name in ("category_example", "reply_example")
        ]
        self.max_memes_per_message = self._read_config_value(
            ("generation", "emotion", "max_memes_per_message"), -1
        )
        self.emotions_probability = self._read_config_value(
            ("generation", "emotion", "probability"), 100
        )
        self.send_meme_tool_enabled = self._read_bool(
            ("generation", "emotion", "send_tool_enabled"), True
        )
        self.meme_tool_only = self._read_bool(("generation", "emotion", "tool_only"))
        preview_images = self._read_config_value(
            ("generation", "emotion", "preview_images_per_page"), 9
        )
        self.preview_images_per_page = (
            max(0, min(9, preview_images)) if isinstance(preview_images, int) else 9
        )
        self.keep_meme_send_summary = self._read_bool(
            ("generation", "emotion", "keep_send_summary"), True
        )
        self.tool_probability_enabled = self._read_bool(
            ("semantic", "tool_probability_enabled")
        )
        self.tool_call_probability = self._read_config_value(
            ("semantic", "tool_call_probability"), 100
        )
        self.emotion_llm_enabled = (
            not self.meme_tool_only
            and self._read_bool(("generation", "emotion", "llm", "enabled"))
        )
        self.emotion_llm_provider_id = self._read_str(
            ("generation", "emotion", "llm", "provider_id")
        )
        self.selection_backend = self._read_str(
            ("generation", "emotion", "llm", "backend"), "chat"
        ) or "chat"
        self.semantic_query_source = self._read_str(
            ("semantic", "query", "source"), "generated"
        ) or "generated"
        self.semantic_query_provider_id = self._read_str(
            ("semantic", "query", "provider_id")
        ).strip()
        self.emotion_llm_context_turns = self._clamp_int(
            self._read_config_value(("generation", "emotion", "llm", "context_turns"), 0),
            0,
            20,
        )
        self.emotion_llm_inject_persona = self._read_bool(
            ("generation", "emotion", "llm", "inject_persona")
        )
        self.enable_mixed_message = self._read_bool(
            ("generation", "message", "enable_mixed")
        )
        self.mixed_message_probability = self._read_config_value(
            ("generation", "message", "mixed_probability"), 50
        )
        self.remove_invalid_alternative_markup = self._read_bool(
            ("generation", "markup", "remove_invalid_alternative")
        )
        self.filter_all_tags = self._read_bool(
            ("generation", "markup", "filter_all_tags")
        )
        self.convert_static_to_gif = self._read_bool(
            ("generation", "message", "convert_static_to_gif")
        )
        self.streaming_compatibility = self._read_bool(
            ("generation", "message", "streaming_compatibility"), True
        )
        self.send_image_as_base64 = self._read_bool(
            ("generation", "message", "send_image_as_base64")
        )
        self.content_cleanup_rule = self._read_str(
            ("generation", "message", "content_cleanup_rule"), "&&[a-zA-Z]*&&"
        )
        self.trigger_scope = normalize_trigger_scope(
            self._read_config_value(
                ("generation", "trigger", "scope"), "only_chat_llm"
            )
        )

        # ---- 人格与提示词 ----
        self.sys_prompt_add = ""
        self.persona_base_prompts: dict[str, str] = {}
        self.category_mapping: dict[str, str] = {}
        self.category_mapping_string = ""
        self._semantic_initial_rebuild_task: asyncio.Task | None = None

        # WebUI 运行时状态
        self._runtime_file_lock: asyncio.Lock | None = None
        self._delivery_history = None

        try:
            self._reload_personas()
        except Exception as exc:  # pragma: no cover - 数据目录首次创建时
            logger.warning("初始化表情包分类提示词失败：{}", exc)

    # ------------------------------------------------------------ 配置读取
    def _bootstrap_layout(self) -> None:
        """复用上游 ``config._bootstrap_pack_runtime`` 初始化数据目录结构。"""
        package = __package__.rsplit(".", 1)[0]
        try:
            import importlib

            module = importlib.import_module(f"{package}.config")
            bootstrap = getattr(module, "_bootstrap_pack_runtime", None)
            if callable(bootstrap):
                bootstrap(self.data_dir)
        except Exception as exc:  # pragma: no cover - 防御性
            logger.error("初始化插件数据目录结构失败：{}", exc)

    def _llm_config_section(self) -> dict[str, Any]:
        """收集配置中的 LLM 提供方定义。"""
        section = self._read_path(("llm",), None)
        if isinstance(section, dict):
            return section
        # 兼容直接写在根上的 providers 列表。
        providers = self._read_path(("providers",), None)
        if isinstance(providers, (list, dict)):
            return {"providers": providers}
        return {}

    def _read_path(self, path: tuple[str, ...], missing=None):
        value = self.config.read(path, missing)
        return missing if value is None else value

    def _read_config_value(self, primary_path: tuple[str, ...], default=None):
        value = self.config.read(primary_path, None)
        return default if value is None else value

    def _read_bool(self, path: tuple[str, ...], default: bool = False) -> bool:
        return bool(self._read_config_value(path, default))

    def _read_str(self, path: tuple[str, ...], default: str = "") -> str:
        value = self._read_config_value(path, default)
        return default if value is None else str(value)

    @staticmethod
    def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return minimum
        return max(minimum, min(maximum, number))

    def _get_provider_config(self, provider_key: str) -> dict:
        provider_config = self._read_path(("storage", "providers", provider_key), {})
        return dict(provider_config) if isinstance(provider_config, dict) else {}

    def _get_image_host_type(self) -> str:
        return (
            str(self._read_config_value(("storage", "provider"), "stardots") or "stardots")
            .strip()
            .lower()
        )

    def _get_github_accelerator_url(self) -> str:
        return str(
            self._read_config_value(("community", "github_accelerator"), "") or ""
        ).strip()

    # ------------------------------------------------------------ 图床
    def _init_image_host(self) -> None:
        """按配置初始化图床客户端（与上游逻辑一致）。"""
        image_host_type = self._get_image_host_type()
        if image_host_type == "stardots":
            stardots_config = self._get_provider_config("stardots")
            if stardots_config.get("key") and stardots_config.get("secret"):
                self.img_sync_config = {
                    "key": stardots_config["key"],
                    "secret": stardots_config["secret"],
                    "space": stardots_config.get("space", "memes"),
                    "provider": "stardots",
                }
                self.img_sync_provider_type = "stardots"
        elif image_host_type == "cloudflare_r2":
            r2_config = self._get_provider_config("cloudflare_r2")
            required = [
                "account_id",
                "access_key_id",
                "secret_access_key",
                "bucket_name",
            ]
            if all(r2_config.get(field) for field in required):
                if r2_config.get("public_url"):
                    r2_config["public_url"] = str(r2_config["public_url"]).rstrip("/")
                r2_config["provider"] = "cloudflare_r2"
                self.img_sync_config = dict(r2_config)
                self.img_sync_provider_type = "cloudflare_r2"
        elif image_host_type == "lsky":
            lsky_config = self._get_provider_config("lsky")
            if lsky_config.get("url") and lsky_config.get("token"):
                self.img_sync_config = dict(lsky_config)
                self.img_sync_provider_type = "lsky"
        elif image_host_type == "webdav":
            webdav_config = self._get_provider_config("webdav")
            required = ["url", "username", "password"]
            if all(webdav_config.get(field) for field in required):
                for key in ("url", "public_url"):
                    if webdav_config.get(key):
                        webdav_config[key] = str(webdav_config[key]).rstrip("/")
                webdav_config["provider"] = "webdav"
                self.img_sync_config = dict(webdav_config)
                self.img_sync_provider_type = "webdav"
            else:
                missing = [f for f in required if not webdav_config.get(f)]
                logger.warning(
                    "WebDAV 图床未初始化，缺少必要配置项: {}。当前已读取字段: {}",
                    ", ".join(missing),
                    ", ".join(sorted(webdav_config.keys())) or "无",
                )

        if self.img_sync_config and self.img_sync_provider_type:
            try:
                self._ensure_img_sync_for_pack()
            except Exception as exc:  # pragma: no cover - 图床配置异常
                logger.warning("初始化图床同步失败：{}", exc)

    def _resolve_sync_pack_target(self, preferred_pack_id: str | None = None):
        from ..backend.packs.resolver import get_pack_paths, resolve_pack_id

        pack_id = str(preferred_pack_id or "").strip()
        if pack_id:
            paths = get_pack_paths(pack_id)
            if paths["pack_dir"].is_dir():
                paths["memes_dir"].mkdir(parents=True, exist_ok=True)
                return pack_id, paths["memes_dir"]
        resolved_pack_id = resolve_pack_id()
        paths = get_pack_paths(resolved_pack_id)
        paths["memes_dir"].mkdir(parents=True, exist_ok=True)
        return resolved_pack_id, paths["memes_dir"]

    def _ensure_img_sync_for_pack(self, preferred_pack_id: str | None = None):
        """按当前表情包构建图床同步客户端（移植自上游）。"""
        if not (self.img_sync_config and self.img_sync_provider_type):
            return None
        target_pack_id, target_memes_dir = self._resolve_sync_pack_target(preferred_pack_id)
        running_process = (
            getattr(self.img_sync, "sync_process", None) if self.img_sync else None
        )
        if running_process and running_process.is_alive():
            if target_memes_dir.resolve() != Path(self.img_sync.local_dir).resolve():
                raise RuntimeError("已有同步任务正在运行，请等待完成后再切换同步表情包")
            return self.img_sync

        current_dir = None
        if self.img_sync and getattr(self.img_sync, "local_dir", None):
            try:
                current_dir = Path(self.img_sync.local_dir).resolve()
            except Exception:
                current_dir = None

        if current_dir != target_memes_dir.resolve():
            from ..image_host.img_sync import ImageSync

            previous_sync = self.img_sync
            try:
                self.img_sync = ImageSync(
                    config=self.img_sync_config,
                    local_dir=target_memes_dir,
                    provider_type=self.img_sync_provider_type,
                )
                self._img_sync_pack_id = target_pack_id
            except Exception:
                self.img_sync = previous_sync
                raise
        return self.img_sync

    # ------------------------------------------------------------ 提示词
    def _build_meme_prompt(self, category_mapping_string: str | None = None) -> str:
        mapping_string = category_mapping_string or self.category_mapping_string
        prompt = self.prompt_head + mapping_string + self.prompt_tail
        for enabled, template in getattr(self, "prompt_examples", []):
            if enabled and template.strip():
                prompt += "\n\n" + template.strip()
        return prompt + self._build_meme_quantity_prompt()

    def _build_meme_quantity_prompt(self) -> str:
        if not self._read_bool(("generation", "prompt", "quantity_guidance_enabled")):
            return ""
        template = self._read_str(("generation", "prompt", "quantity_guidance")).strip()
        return f"\n\n{template}\n" if template else ""

    def _wrap_meme_prompt(self, prompt: str) -> str:
        return f"\n\n{MEME_PROMPT_MARKER_START}\n{prompt}\n{MEME_PROMPT_MARKER_END}"

    def _strip_meme_prompt(self, prompt: str | None) -> str:
        prompt = prompt or ""
        for start, end in (
            (MEME_PROMPT_MARKER_START, MEME_PROMPT_MARKER_END),
            (SEMANTIC_PROMPT_MARKER_START, SEMANTIC_PROMPT_MARKER_END),
        ):
            prompt = re.sub(
                rf"(?:\n\n)?{re.escape(start)}[\s\S]*?{re.escape(end)}",
                "",
                prompt,
            )
        return prompt

    # ------------------------------------------------------------ 人格
    @staticmethod
    def _get_persona_key(persona: dict, index: int) -> str:
        return str(persona.get("name") or persona.get("id") or index)

    def _sync_persona_base_prompts(self, personas: list[dict]) -> None:
        active_keys = set()
        for index, persona in enumerate(personas):
            key = self._get_persona_key(persona, index)
            active_keys.add(key)
            self.persona_base_prompts[key] = self._strip_meme_prompt(
                persona.get("prompt", "")
            )
        for key in set(self.persona_base_prompts) - active_keys:
            del self.persona_base_prompts[key]

    def _apply_persona_prompts(self) -> None:
        """把插件提示词注入宿主人格（NoneBot2 无宿主人格时为空操作）。"""
        personas = getattr(getattr(self.context, "provider_manager", None), "personas", None)
        if not isinstance(personas, list):
            return
        self._sync_persona_base_prompts(personas)
        self.sys_prompt_add = ""
        for index, persona in enumerate(personas):
            key = self._get_persona_key(persona, index)
            persona["prompt"] = self.persona_base_prompts[key]

    def _reload_personas(self) -> None:
        from ..backend.semantic.models import runtime_category_mapping
        from ..utils import dict_to_string, load_json

        pack_context = self._resolve_runtime_pack_context()
        context_mapping = pack_context.get("category_mapping")
        self.category_mapping = runtime_category_mapping(
            context_mapping
            if isinstance(context_mapping, dict)
            else load_json(
                pack_context["metadata_path"],
                _load_default_categories(),
            )
        )
        self.category_mapping_string = dict_to_string(self.category_mapping)
        self._apply_persona_prompts()

    async def reload_emotions(self) -> None:
        """重新加载分类（供命令与 WebUI 调用）。"""
        try:
            await asyncio.to_thread(self.category_manager.sync_with_filesystem)
            self._reload_personas()
        except Exception as exc:
            logger.error("重新加载表情配置失败：{}", exc)

    # ------------------------------------------------------------ 选包
    def _resolve_persona_id(self, event: Any = None, req: Any = None) -> str | None:
        if event is not None and hasattr(event, "get_extra"):
            resolved = event.get_extra("meme_manager_resolved_persona_id")
            if resolved is not None:
                return str(resolved).strip() or None
        if req is not None:
            conversation = getattr(req, "conversation", None)
            persona_id = str(getattr(conversation, "persona_id", "") or "").strip()
            if persona_id:
                return persona_id
        if event is not None:
            persona_id = str(getattr(event, "persona_id", "") or "").strip()
            if persona_id:
                return persona_id
            resolved = str(event.get_extra("meme_manager_resolved_persona_id") or "").strip()
            if resolved:
                return resolved
        return None

    def _resolve_runtime_pack_context(self, event: Any = None, req: Any = None) -> dict:
        from ..backend.packs.resolver import resolve_pack_context

        session_id = ""
        if req is not None:
            session_id = str(getattr(req, "session_id", "") or "").strip()
        if not session_id and event is not None:
            session_id = str(getattr(event, "session_id", "") or "").strip()
        persona_id = self._resolve_persona_id(event=event, req=req)
        context = resolve_pack_context(
            session_id=session_id or None, persona_id=persona_id
        )
        if event is not None and hasattr(event, "set_extra"):
            event.set_extra(
                "meme_manager_runtime_memes_dir", str(context["memes_dir"])
            )
            event.set_extra(
                "meme_manager_runtime_pack_id", str(context.get("pack_id") or "")
            )
            event.set_extra(
                "meme_manager_runtime_pack_ids", context.get("pack_ids", [])
            )
        return context

    def _get_runtime_memes_dir_for_event(self, event: Any) -> str:
        if hasattr(event, "get_extra"):
            runtime_dir = str(
                event.get_extra("meme_manager_runtime_memes_dir") or ""
            ).strip()
            if runtime_dir:
                return runtime_dir
        return str(self._resolve_runtime_pack_context(event=event)["memes_dir"])

    # ------------------------------------------------------------ 分类
    def _get_manageable_categories(self) -> set[str]:
        return (
            set(self.category_manager.get_descriptions())
            | set(self.category_manager.get_local_categories())
        )

    def _ensure_default_category_descriptions(self, categories: list[str]) -> None:
        existing = self.category_manager.get_descriptions()
        updated = False
        defaults = _load_default_categories()
        for category in categories:
            if category in existing:
                continue
            default_description = defaults.get(category)
            if not default_description:
                continue
            if self.category_manager.update_description(category, default_description):
                existing[category] = default_description
                updated = True
        if updated:
            self._reload_personas()

    # ------------------------------------------------------------ 语义
    def _resolve_embedding_provider(self, pack_id: str = ""):
        return self.semantic_task_manager._resolve_embedding_provider(pack_id)

    def _semantic_pack_ready(
        self,
        event: Any = None,
        req: Any = None,
        *,
        require_tool: bool = False,
        for_direct_search: bool = False,
    ) -> bool:
        """判断当前绑定的表情包是否有可用向量索引（移植自上游）。"""
        from ..backend.semantic.index import EmbeddingAdapter, index_is_ready
        from ..backend.semantic.storage import load_metadata
        from ..backend.packs.resolver import get_pack_paths

        if not for_direct_search and not self.semantic_enabled:
            return False
        if require_tool and req is not None:
            tool_set = getattr(req, "func_tool", None)
            if tool_set is None or not callable(getattr(tool_set, "get_tool", None)):
                return False
            if not tool_set.get_tool("search_memes"):
                return False

        context = self._resolve_runtime_pack_context(event=event, req=req)
        pack_ids = context.get("pack_ids", [])
        if not pack_ids:
            return False

        for pack_id in pack_ids:
            try:
                pack_dir = get_pack_paths(pack_id)["pack_dir"]
                metadata = load_metadata(pack_dir)
                if not metadata.get("images"):
                    continue
                provider = self._resolve_embedding_provider(pack_id)
                embedding = EmbeddingAdapter(
                    provider, self.semantic_embedding_provider_id or ""
                )
                if embedding.ready and index_is_ready(
                    self.data_dir,
                    pack_id,
                    metadata,
                    embedding.provider_id,
                    embedding.model_name,
                    embedding.dimension,
                    allow_partial=True,
                ):
                    return True
            except Exception as exc:
                logger.warning("表情包 {} 的语义检查失败：{}", pack_id, exc)
        return False

    def _semantic_mode_active(self, event: Any = None, *, include_direct: bool = False) -> bool:
        if not self.semantic_enabled:
            return False
        context = self._resolve_runtime_pack_context(event=event)
        if include_direct:
            return bool(context.get("pack_ids"))
        return bool(context.get("pack_ids")) and self._semantic_pack_ready(event=event)

    # ------------------------------------------------------------ 生命周期
    async def startup(self) -> None:
        """插件启动：启动自动收集与首次语义重建。"""
        try:
            await self.auto_collect_manager.start(defer_missing_provider=True)
        except Exception as exc:
            logger.warning("启动自动收集失败：{}", exc)
        if self.semantic_enabled:
            self._semantic_initial_rebuild_task = asyncio.create_task(
                self._auto_rebuild_initial_pack()
            )

    async def shutdown(self) -> None:
        """插件卸载：停止后台任务并释放 LLM 会话。"""
        task = self._semantic_initial_rebuild_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.auto_collect_manager.stop()
        except Exception as exc:  # pragma: no cover - 防御性
            logger.debug("停止自动收集失败：{}", exc)
        close = getattr(self.context, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:  # pragma: no cover - 防御性
                pass

    async def _auto_rebuild_initial_pack(self) -> None:
        """语义开关开启后，后台静默补齐当前包的向量索引。"""
        from ..backend.semantic.index import EmbeddingAdapter, index_is_ready
        from ..backend.semantic.storage import (
            load_metadata,
            semantic_metadata_is_complete,
        )

        await asyncio.sleep(3)
        try:
            pack_context = self._resolve_runtime_pack_context()
            pack_id = str(pack_context.get("pack_id") or "").strip()
            pack_dir = pack_context.get("pack_dir")
            if not pack_id or not pack_dir:
                logger.info("{} 语义检索已开启，但当前没有可重建的表情包。", WEBUI_LOG_PREFIX)
                return
            metadata = load_metadata(pack_dir)
            images = metadata.get("images", {})
            caption_done = sum(
                1
                for item in images.values()
                if isinstance(item, dict) and item.get("caption_status") == "done"
            )
            if (
                not images
                or caption_done != len(images)
                or not semantic_metadata_is_complete(pack_dir, metadata)
            ):
                logger.info(
                    "{} 首次开启语义检索：{} 的图片描述尚未全部完成，暂不自动建立向量。",
                    WEBUI_LOG_PREFIX,
                    pack_id,
                )
                return
            embedding = self.semantic_task_manager._require_embedding_provider(
                pack_id, "首次建立语义索引"
            )
            if index_is_ready(
                self.data_dir,
                pack_id,
                metadata,
                embedding.provider_id,
                embedding.model_name,
                embedding.dimension,
            ):
                return
            result = await self.semantic_task_manager.rebuild_index(pack_id, force=True)
            logger.info(
                "{} 首次开启语义检索：{} 向量索引完成，共 {} 条。",
                WEBUI_LOG_PREFIX,
                pack_id,
                result.get("item_count", 0),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "{} 首次开启语义检索的后台向量重建未完成：{}", WEBUI_LOG_PREFIX, exc
            )

    # ------------------------------------------------------------ 对外接口
    def session_for(self, event: Any) -> MessageSession:
        """由统一事件构造会话对象（供 message_tool / 集成接口使用）。"""
        return MessageSession.from_event(event)

    def config_snapshot(self) -> dict[str, Any]:
        """返回配置快照（WebUI 设置页读取）。"""
        return copy.deepcopy(dict(self.config))


__all__ = [
    "MemeRuntime",
    "MessageSession",
    "normalize_trigger_scope",
    "DEFAULT_PACK_ID",
    "WEBUI_LOG_PREFIX",
]
