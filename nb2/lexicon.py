"""表情包分类词库（category lexicon）。

用途：让「分类模式」的提示词除了分类名与一句话描述，还带上**心情词 / 语气词 /
语境词 / 用途词**，使模型在挑选表情时能对上更口语化的对话内容，而不是只会
复述分类名。

词库来源（按可信度排序）：
1. 表情包自带的 ``semantic_metadata.json``（作者已用视觉模型逐张打标）；
2. ``manifest.json`` 的分类描述（人工撰写）；
3. 内置的基础词表（``BASE_LEXICON``），覆盖官方包的 19 个标准分类。

词库文件查找顺序：
* ``<插件目录>/assets/category_lexicon/<pack_id>.json``（随仓库分发的策展词库）；
* ``<数据目录>/lexicon/<pack_id>.json``（用户在运行期生成/覆盖）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .log import logger

#: 词库文件目录（随仓库分发）。
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "category_lexicon"
#: 运行期词库目录（数据目录下）。
RUNTIME_DIR_NAME = "lexicon"

#: 提示词里每个分类最多展示的词数（避免上下文膨胀）。
MAX_WORDS_PER_BUCKET = 6

#: 官方包的 19 个标准分类：人工整理的基础词表。
#:
#: 结构：``分类 -> {mood, tone, context, intent}``。这些词是「对话里可能出现的
#: 说法」，而不是「图片的画面内容」——后者属于语义检索，不参与分类提示词。
BASE_LEXICON: dict[str, dict[str, list[str]]] = {
    "angry": {
        "mood": ["生气", "恼火", "不爽", "被气到", "火大", "强烈不满"],
        "tone": ["怼人", "抱怨", "指责", "算账", "威胁", "激烈反对"],
        "context": ["对方闯祸", "离谱言论", "被冒犯", "被坑了", "重复惹事"],
        "intent": ["表达不满", "反驳观点", "投诉", "警告对方"],
    },
    "happy": {
        "mood": ["开心", "高兴", "得意", "轻松", "满足", "心情不错"],
        "tone": ["愉快", "炫耀", "雀跃", "笑眯眯"],
        "context": ["问题解决", "事情办成", "好消息", "被夸奖"],
        "intent": ["积极反馈", "庆祝", "确认成功", "分享喜悦"],
    },
    "sad": {
        "mood": ["难过", "失落", "委屈", "低落", "心累", "伤感"],
        "tone": ["倾诉", "自怜", "叹气", "低声"],
        "context": ["遇到挫折", "被拒绝", "坏消息", "求安慰"],
        "intent": ["表达难过", "寻求安慰", "传达坏消息"],
    },
    "surprised": {
        "mood": ["惊讶", "震惊", "傻眼", "意外", "被吓到"],
        "tone": ["惊呼", "感叹", "不敢相信"],
        "context": ["超出预期", "突然转折", "意外发现", "离谱结果"],
        "intent": ["表达惊讶", "强调意外", "追问细节"],
    },
    "confused": {
        "mood": ["困惑", "懵", "疑惑", "不解", "摸不着头脑"],
        "tone": ["发问", "质疑", "迟疑", "冒冷汗"],
        "context": ["概念模糊", "逻辑矛盾", "对方操作莫名", "信息不足"],
        "intent": ["请求澄清", "表达不理解", "追问原因"],
    },
    "color": {
        "mood": ["暧昧", "心动", "羞耻", "欲言又止", "被撩到"],
        "tone": ["调情", "卖萌", "暗示", "撒娇"],
        "context": ["亲密互动", "私密话题", "打情骂俏", "深夜闲聊"],
        "intent": ["暧昧回应", "调情", "转移话题"],
    },
    "cpu": {
        "mood": ["卡住", "发懵", "转不过来", "脑子空白"],
        "tone": ["自嘲", "求助", "停顿", "拖时间"],
        "context": ["复杂问题", "技术讨论", "需要思考时间", "信息过载"],
        "intent": ["表示需要时间", "请求等待", "表达思考中"],
    },
    "fool": {
        "mood": ["自嘲", "厚脸皮", "不害臊", "尴尬认领", "故作镇定"],
        "tone": ["耍赖", "开玩笑", "卖傻", "幽默化解"],
        "context": ["自己失误", "被抓包", "小尴尬", "出洋相"],
        "intent": ["缓和气氛", "自我调侃", "拒绝认账"],
    },
    "givemoney": {
        "mood": ["期待", "厚脸皮", "卖萌要东西", "半开玩笑"],
        "tone": ["讨要", "催款", "撒娇"],
        "context": ["涉及报酬", "服务付费", "红包", "奖励"],
        "intent": ["催打款", "讨要报酬", "索取奖励"],
    },
    "like": {
        "mood": ["喜欢", "欣赏", "心动", "被种草", "认可"],
        "tone": ["赞美", "示好", "夸夸", "比心"],
        "context": ["美食", "优秀方案", "好看的内容", "被推荐"],
        "intent": ["表达喜爱", "点赞认可", "主动示好"],
    },
    "see": {
        "mood": ["好奇", "关注", "暗中观察", "围观", "偷瞄"],
        "tone": ["打量", "盯梢", "不动声色"],
        "context": ["监控进度", "观察变化", "潜水围观", "等结果"],
        "intent": ["表示在关注", "询问进度", "暗中留意"],
    },
    "shy": {
        "mood": ["害羞", "脸红", "不好意思", "被夸到", "旖旎"],
        "tone": ["扭捏", "小声", "害羞回应"],
        "context": ["收到赞美", "被调侃", "隐私话题", "外貌评价"],
        "intent": ["害羞回应", "表示不好意思", "转移话题"],
    },
    "work": {
        "mood": ["忙碌", "心累", "打工人", "疲惫"],
        "tone": ["汇报", "吐槽", "例行"],
        "context": ["任务分配", "进度汇报", "工作流程", "加班"],
        "intent": ["汇报进度", "确认任务", "抱怨工作"],
    },
    "reply": {
        "mood": ["等待", "期盼", "催促"],
        "tone": ["提醒", "打招呼", "追问"],
        "context": ["提问后无人回应", "需要确认", "等反馈"],
        "intent": ["等待回复", "催促回应", "确认对方在不在"],
    },
    "meow": {
        "mood": ["可爱", "卖萌", "被治愈", "撒娇"],
        "tone": ["卖萌", "夹子音", "哄人"],
        "context": ["宠物话题", "安抚情绪", "卖萌互动"],
        "intent": ["卖萌", "撒娇", "缓和气氛"],
    },
    "baka": {
        "mood": ["无语", "想吐槽", "无奈"],
        "tone": ["吐槽", "轻微责备", "调侃"],
        "context": ["低级错误", "可爱型抱怨", "小失误"],
        "intent": ["吐槽对方", "轻度责备", "打趣"],
    },
    "morning": {
        "mood": ["清醒", "有精神", "期待新一天"],
        "tone": ["问候", "打招呼"],
        "context": ["早上问候", "开始一天", "早安"],
        "intent": ["早安问候", "打招呼"],
    },
    "sleep": {
        "mood": ["困", "疲惫", "想睡", "熬夜后遗症"],
        "tone": ["道晚安", "打哈欠", "慵懒"],
        "context": ["作息话题", "熬夜", "准备睡觉"],
        "intent": ["道晚安", "表达困意", "劝对方休息"],
    },
    "sigh": {
        "mood": ["无奈", "无语", "心累", "感慨"],
        "tone": ["叹气", "自嘲", "无力"],
        "context": ["重复问题", "历史遗留难题", "说不通"],
        "intent": ["表达无奈", "感慨", "放弃解释"],
    },
}


def asset_path(pack_id: str) -> Path:
    """返回随仓库分发的词库文件路径。"""
    return ASSET_DIR / f"{pack_id}.json"


def runtime_path(data_dir: Path | str, pack_id: str) -> Path:
    """返回运行期词库文件路径。"""
    return Path(data_dir) / RUNTIME_DIR_NAME / f"{pack_id}.json"


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except FileNotFoundError:
        return None
    except Exception as exc:  # pragma: no cover - 词库损坏时不阻断启动
        logger.warning("读取分类词库失败 {}：{}", path, exc)
        return None


def load_pack_lexicon(pack_id: str, data_dir: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """读取某个表情包的词库。

    Args:
        pack_id: 表情包 ID。
        data_dir: 插件数据目录；提供时优先读取运行期覆盖文件。

    Returns:
        ``{分类: {"mood": [...], "tone": [...], "context": [...], "intent": [...]}}``；
        没有任何词库时返回空字典。
    """
    candidates: list[Path] = []
    if data_dir is not None:
        candidates.append(runtime_path(data_dir, pack_id))
    candidates.append(asset_path(pack_id))
    for path in candidates:
        payload = _load_json(path)
        if not payload:
            continue
        categories = payload.get("categories")
        if isinstance(categories, dict) and categories:
            return _normalize_lexicon(categories)
    return {}


def load_for_packs(pack_ids: Iterable[str], data_dir: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """合并多个表情包的词库（后出现者覆盖同名分类）。"""
    merged: dict[str, dict[str, Any]] = {}
    for pack_id in pack_ids:
        merged.update(load_pack_lexicon(str(pack_id), data_dir))
    return merged


def _normalize_lexicon(categories: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把词库规范成固定结构，过滤空值与非字符串。"""
    result: dict[str, dict[str, Any]] = {}
    for name, entry in categories.items():
        if not isinstance(entry, dict):
            continue
        bucket: dict[str, Any] = {}
        for key in ("mood", "tone", "context", "intent"):
            values = entry.get(key)
            if not isinstance(values, list):
                bucket[key] = []
                continue
            bucket[key] = [str(item).strip() for item in values if str(item).strip()]
        result[str(name)] = bucket
    return result


def base_lexicon_for(category: str) -> dict[str, list[str]]:
    """返回内置基础词表中该分类的词（深拷贝，避免被调用方改写）。"""
    entry = BASE_LEXICON.get(str(category))
    if not entry:
        return {"mood": [], "tone": [], "context": [], "intent": []}
    return {key: list(values) for key, values in entry.items()}


def merge_category(
    category: str,
    observed: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """合并内置基础词与观测词（去重，观测词优先）。"""
    base = base_lexicon_for(category)
    if not observed:
        return base
    merged: dict[str, list[str]] = {}
    for key in ("mood", "tone", "context", "intent"):
        seen: list[str] = []
        for source in (observed.get(key) or [], base.get(key) or []):
            for word in source:
                text = str(word).strip()
                if text and text not in seen:
                    seen.append(text)
        merged[key] = seen
    return merged


def render_hint_lines(
    category_mapping: dict[str, str],
    lexicon: dict[str, dict[str, Any]],
    *,
    max_items: int = MAX_WORDS_PER_BUCKET,
) -> str:
    """把词库渲染成提示词片段。

    Args:
        category_mapping: ``{分类: 描述}``（当前可用分类）。
        lexicon: :func:`load_for_packs` 的结果。
        max_items: 每个维度最多展示的词数。

    Returns:
        多行文本；没有任何可用词时返回空字符串。
    """
    lines: list[str] = []
    for category in category_mapping:
        merged = merge_category(category, lexicon.get(category))
        parts: list[str] = []
        labels = (("mood", "心情"), ("tone", "语气"), ("context", "语境"), ("intent", "用途"))
        for key, label in labels:
            words = merged.get(key) or []
            if not words:
                continue
            parts.append(f"{label}：{'、'.join(words[:max_items])}")
        if parts:
            lines.append(f"- {category}｜" + "｜".join(parts))
    return "\n".join(lines)


def lexicon_enabled(runtime: Any) -> bool:
    """读取配置开关（缺省开启；配置项不存在时也返回 True）。"""
    try:
        value = runtime._read_config_value(
            ("generation", "prompt", "lexicon_enabled"), True
        )
    except Exception:  # pragma: no cover - 防御性
        return True
    return bool(value)


__all__ = [
    "BASE_LEXICON",
    "ASSET_DIR",
    "asset_path",
    "runtime_path",
    "load_pack_lexicon",
    "load_for_packs",
    "base_lexicon_for",
    "merge_category",
    "render_hint_lines",
    "lexicon_enabled",
    "MAX_WORDS_PER_BUCKET",
]
