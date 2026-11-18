# 分类词库（AI 选图用的心情 / 语气 / 语境 / 用途词）

> 解决的问题：官方资源包只带「分类名 + 一句话描述」（如 `angry` = 「当对话包含抱怨、
> 批评或激烈反对时使用」），模型拿到用户一句「你是不是有病啊」时，很难稳定映射到
> 分类。加入口语化词库后，提示词里直接给出每个分类常见的**心情 / 语气 / 语境 / 用途**
> 词，选图命中率明显提升。

## 数据从哪来

| 来源 | 是否已分类 | 是否含语义 | 说明 |
| --- | --- | --- | --- |
| 资源包 `manifest.json` | ✅ 19–22 个分类，均有中文描述 | ❌ | 只够「分类模式」 |
| 资源包 `memes/<分类>/` 目录 | ✅ 图片已按分类归档 | ❌ | 无需 AI 识图 |
| `semantic_metadata.json`（仅 4/17 个包自带） | ✅ | ✅ 逐张 caption + tags | 本词库的主要数据源 |
| 内置 `BASE_LEXICON` | — | — | 人工整理，覆盖官方 19 类，兜底 |

实测：官方包 `official-001` 的 398 张图与 `ddzs987-semantic-001` 的语义记录
**content_sha256 100% 对应**，所以可以直接借后者的打标数据给前者生成词库。

## 生成方式

```bash
# 1) 下载包（只需 manifest）与打标数据
python - <<'PY'
# 见 tools/probe_taxonomy_status.py 中的下载逻辑
PY

# 2) 生成词库 → assets/category_lexicon/<pack_id>.json
python tools/build_lexicon.py \
  --pack official-001 \
  --zip packs_cache/official-001.zip \
  --metadata packs_cache/ddzs987_semantic_metadata.json

# 3) 人工核对
python tools/show_lexicon.py
```

生成规则（`tools/build_lexicon.py`）：

1. 取每张图的 `tags`，清洗掉画面描述词（`动漫少女`/`二次元`/`脸红` 等）、
   过于宽泛的词（`聊天`/`反应`）、露骨词；
2. 每个词按线索正则**只归入一个**维度，优先级：语境 > 用途 > 语气 > 心情
   （避免同一词在四行里重复出现）；
3. 按分类内出现频次取前 6 个，再与 `BASE_LEXICON` 合并去重。

## 词库格式

```json
{
  "schema_version": 1,
  "pack_id": "official-001",
  "categories": {
    "angry": {
      "description": "当对话包含抱怨、批评或激烈反对时使用",
      "mood":    ["不满", "恼火", "生气", "愤怒"],
      "tone":    ["抱怨", "吐槽", "反驳"],
      "context": ["抱怨工作", "对方闯祸", "被冒犯"],
      "intent":  ["表达不满", "反驳观点", "投诉"],
      "source":  "semantic_metadata+base"
    }
  }
}
```

## 生效方式

运行时由 `nb2/lexicon.py` 读取并渲染进分类提示词（`MemeRuntime._build_lexicon_prompt`）：

```
[分类词库]
以下是各分类常见的心情/语气/语境/用途词，用于判断当前对话更适合哪个分类；仅作参考，不要逐字复述。
- angry｜心情：不满、恼火、生气…｜语气：抱怨、吐槽…｜语境：抱怨工作、对方闯祸…｜用途：表达不满、反驳观点…
- happy｜心情：开心、高兴、得意…｜…
```

* 开关：`generation.prompt.lexicon_enabled`（缺省 **开启**）；
* 实测增量：官方包 19 个分类约 **+1580 字符**（1232 → 2812），
  相当于每分类多 ~83 字符，对上下文预算影响可接受；
* 找不到包专属词库时自动回退到内置 `BASE_LEXICON`，不会报错。

## 覆盖范围与后续

- 已生成：`official-001`（19 类，来自 398 张图的真实打标数据）；
- 其余包可同法生成：只要该包图片与某个语义增强包内容一致（sha256 相同），
  或该包自带 `semantic_metadata.json`；
- 若某包既无语义数据、图片也不重复，则只能：① 只用内置基础词表；② 或自行跑一次
  视觉模型打标（`/meme_manager/app/semantic/` 页面可批量执行）。
