# source_refs 溯源结构与页码定位

> 对应服务版本 0.3.0

`source_refs` 是每条**字段提取结果**与**逻辑分析结果**携带的溯源对象，回答「这个值 / 结论从 PDF 哪里得来」——命中的原始片段、注入 LLM 的全文、命中页码、PDF 高亮框、以及（judge 规则的）联网搜索来源。本页是 `source_refs` 结构与页码定位的**唯一权威**，`api/file.md`、`api/callbacks.md` 等只给「详见本页」的链接。

前置阅读：[异步回调契约](../api/callbacks.md)（事件序列、payload 形态）、[字段提取配置](extraction-config.md)、[逻辑分析配置](analysis-config.md)。

> **核心结论**：`source_refs` 的形状**随字段 `source_type` 三分**（text / table / vl），页码散落在 **4 个位置**、格式**不统一**（string / int[]、单值 / 区间 / null），**没有一个全局字段能一把梭拿页码**。消费方必须**先按 `_vl` / `_tables` 键分流布局，再按布局取对应页码字段**，全程走容错。§9 是页码规则，§10 提供可直接抄的归一函数，§11 是容错清单。
>
> 实现位置：页码映射 `utils/page_mapping.py`（`build_page_mapping` / `lookup_page_num` / `lookup_bboxes`）；抽取组装 `service/extraction_service.py`（`_build_text_source_refs` / `_build_table_source_refs` / `_extract_page_field` / `extract_vl_field`）；分析嵌套 `service/analysis_service.py`（`run_analysis`）。

---

## 1. source_refs 出现在哪

同一套结构在下列所有场景出现，取页码逻辑通用：

| 场景 | 位置 | 落库 |
|---|---|---|
| 字段提取结果 | `GET /file/{id}/extraction` → `results[i].source_refs` | `extraction_result.source_refs` |
| 逻辑分析结果 | `GET /file/{id}/analysis` → `results[i].source_refs` | `analysis_result.source_refs`（嵌套依赖字段，见 §8） |
| 回调 `field_done` | `data.source_refs` | extracting 单字段 |
| 回调 `rule_done` | `data.source_refs` | analyzing 单规则（嵌套） |
| 回调 `stage_done` | `data.results[i].source_refs` | extracting / analyzing 阶段汇总 |
| SSE 调试流 | 同上 | `/extraction/test/stream`、`/analysis/test/stream` |

> **抽取失败的字段** `source_refs` 直接是 `null`，没有任何溯源与页码。任何取值前先判空。

---

## 2. 三种布局与分流判定

`source_refs` 的形状随字段 `source_type` 分三种布局，页码位置各不相同。**必须先分流，再取页码**，判定按**固定顺序**（顺序不能乱）：

```python
def classify_source_refs(refs):
    if refs is None:
        return "none"          # 失败字段 / 无溯源 → 无页码
    if not isinstance(refs, dict):
        return "unknown"       # 理论上不会出现，容错
    if "_vl" in refs:
        return "vl"            # VL 视觉抽取（§6）
    if "_tables" in refs:
        return "table"         # 表格抽取（§5）
    return "text"              # 文本抽取（§4，其余顶层 key 均为检索关键词 label）
```

| 布局 | 判定键 | 顶层 key | 页码在哪 |
|---|---|---|---|
| **text** | 无 `_vl` / `_tables` | 检索关键词 label（page 检索固定 `page_content`）+ `_texts` | `refs[label][i].page_num`（string） |
| **table** | 含 `_tables` | 固定 `_tables` + `_texts` | `refs._tables[i].page_num`（string） |
| **vl** | 含 `_vl` | 固定 `_vl` | `refs._vl.key_pages`（int[] / null） |

---

## 3. 特殊键（`_` 前缀）一览

顶层所有 `_` 开头的 key 都是**元数据**，遍历 text 命中时应统一跳过（§10 的取页函数用 `label.startswith("_")` 自动跳过）。全清单：

| 键 | 出现布局 | 类型 | 含义 | 含页码 |
|---|---|---|---|:--:|
| `_texts` | text / table | `{label: string}` | 各 label 实际注入占位符的完整文本 | 否 |
| `_tables` | table | `[ref]` | 表格命中 ref 数组（key 固定，**不是**关键词） | 是（`ref.page_num`） |
| `_vl` | vl | `{method,total_pages,key_pages,...}` | VL 视觉抽取元信息 | 是（`key_pages`） |
| `_model_pages` | text / table（可选） | `int[]` | 模型自报参考页（去重升序，见 §7） | 是（模型自报） |
| `_web_search` | analysis judge（可选） | `{query,results,error?}` | 联网搜索溯源（见 §8） | 否（外部网页） |
| `bboxes` | text / table 的**单条 ref 内**（可选，非顶层） | `[{page_num:int,bbox,page_size}]` | PDF 块级高亮框（见 §9.4） | 是（int，恒单页） |

> 存量老数据可能缺 `text` / `_texts` / `bboxes` / `_model_pages` 等键（老 `page_mapping` 无 bbox，重新解析后才有）——消费方一律用 `.get()` 容错，缺键不代表整条无效。

---

## 4. text 类布局

```jsonc
{
  "投资估算":  [ {ref}, {ref} ],   // key = 检索关键词 label，value = 命中数组
  "总投资":    [ {ref} ],
  "_texts": { "投资估算": "...", "总投资": "..." }   // 注入 prompt 的全文，非页码，跳过
}
```

**取页码：**遍历顶层，**跳过所有 `_` 开头的 key**，其余每个 value 是 ref 数组，逐条读 `ref["page_num"]`：

```python
for label, refs_list in source_refs.items():
    if label.startswith("_"):        # 跳过 _texts / _model_pages 等元数据
        continue
    for ref in refs_list:
        page = ref.get("page_num", "")   # "3" / "3-5" / ""
```

### 4.1 单条 text ref 的完整结构

```jsonc
{
  "type": "context",              // 检索方式：context/section/rule/chunk_db/vector_db/page
  "start_pos": 5120,              // markdown 全文起始位置
  "end_pos": 5680,                // markdown 全文结束位置
  "page_num": "3",                // ★页码（string），来源见 §4.2
  "chunk_id": "xxx",              // 仅 chunk_db/vector_db 有
  "chunk_index": 7,               // 仅 chunk_db/vector_db 有
  "text": "命中的原始片段...",     // 注入 prompt 的原文
  "bboxes": [                     // 可选，块级框，内部另有 int 页码，见 §9.4
    {"page_num": 3, "bbox": [88.0, 120.5, 507.3, 680.2], "page_size": [595.0, 842.0]}
  ]
}
```

### 4.2 text 类 page_num 的两条来源（关键）

| 检索方式（`ref.type`） | `page_num` 来源 | 说明 |
|---|---|---|
| `chunk_db` / `vector_db` | 检索结果自带（取自 `file_chunk.page_num`） | chunking 阶段已算好，直接透传 |
| `context` / `section` / `rule` | `lookup_page_num(page_mapping, start_pos, end_pos)` 实时反查 | 由 `start_pos`/`end_pos` 反查 `page_mapping`，可能跨页 `"3-5"` |
| `page` | 回填字段配置的 `page_range` 原串 | 见 §4.4，不是算出来的 |

> 对消费方而言**无需关心是哪条来源**——统一读 `ref["page_num"]` 即可。上表只解释「为什么同是 text 类，页码格式可能是单值也可能是区间」。

### 4.3 `lookup_page_num` 反查算法（背景，了解即可）

`context/section/rule` 的页码由此函数产出（`utils/page_mapping.py`）：

1. `page_mapping` 是 `[{start_pos, end_pos, page_num, bbox?, page_size?}]`，按 `start_pos` 升序。
2. 对 `start_pos` 数组 `bisect_right - 1` 定位命中片段起点所在块 → `page_start`。
3. 同法定位 `end_pos` → `page_end`。
4. `page_start == page_end` → 返回 `"3"`；否则返回 `"3-5"`。
5. `page_mapping` 为空 → 返回 `""`。

### 4.4 特例：`page` 检索方式

当 `ref.type == "page"`（字段配置用「按页码切片」检索），布局仍是 text，但 label 固定为 `page_content`，`page_num` **直接回填字段配置的 `page_range` 原始字符串**：

```jsonc
{
  "page_content": [
    { "type": "page", "page_range": "5-8", "page_num": "5-8",
      "start_pos": 8000, "end_pos": 24000, "length": 16000,
      "truncated": false, "text": "...整页切片..." }
  ],
  "_texts": { "page_content": "..." }
}
```

- `page_num` 就是配置的 `page_range`，格式取决于用户怎么填（常见 `"5"` / `"5-8"`；理论上可能有逗号列表，按 §10 的 `parse_page_num_str` 兜底解析）。
- **page 方式的 ref 不含 `bboxes`**（整页切片无块级坐标），只能靠 `page_num` 跳页。

---

## 5. table 类布局

```jsonc
{
  "_tables": [                    // ★ key 固定是 "_tables"，不是关键词
    {
      "type": "table",
      "table_index": 1,
      "table_name": "投资估算表",
      "start_pos": 5120,
      "end_pos": 6890,
      "page_num": "12",           // ★页码（string），直取 file_table.page_num
      "text": "表格名称: 投资估算表\n<table>...</table>",
      "bboxes": [                 // 可选，整表框，内部 int 页码见 §9.4
        {"page_num": 12, "bbox": [88.0, 120.5, 507.3, 680.2], "page_size": [595.0, 842.0]}
      ]
    }
  ],
  "_texts": { "投资估算表": "..." }
}
```

**取页码：**读 `source_refs["_tables"]` 数组，逐条 `ref["page_num"]`：

```python
for ref in source_refs.get("_tables", []):
    page = ref.get("page_num", "")   # "12" / ""，来自 file_table.page_num
```

- table 类页码**恒直取** `file_table.page_num`（tableing 阶段落库），不走反查。
- 单表一般单页，但表名与内容跨页时 `file_table.page_num` 也可能是 `""` 或含区间，仍用 `parse_page_num_str` 兜底。

---

## 6. vl 类布局（无 page_num，读 key_pages）

```jsonc
{
  "_vl": {
    "method": "vl_locate",        // vl_model / vl_progressive / vl_locate
    "total_pages": 48,            // PDF 总页数
    "key_pages": [12, 13, 15],    // ★页码在这里：int 数组（1-indexed）
    "vl_total_tokens": 8421,
    "batches_with_info": null     // 仅 vl_progressive 出现
  }
}
```

**取页码：**读 `source_refs["_vl"]["key_pages"]`，**已是 int 数组、1-indexed、无需解析**：

```python
vl = source_refs["_vl"]
pages = vl.get("key_pages")       # [12,13,15] 或 None
if pages is None:
    # vl_progressive：全文扫描，未定位具体页 → 用 total_pages 兜底或标记「全篇」
    pages = list(range(1, vl["total_pages"] + 1))
```

| `method` | `key_pages` 取值 | 含义 |
|---|---|---|
| `vl_model` | 配置 `page_range` 解析后的页（1-indexed int[]） | 指定页整体喂 VL |
| `vl_locate` | 缩略图定位命中的关键页（去重排序，受 `key_pages_limit` 截断；定位不到回退前 `fallback_pages` 页） | 真正高清提取的页 |
| `vl_progressive` | **`null`** | 逐批扫全篇，不定位具体页；需页码请用 `total_pages` 兜底 |

> ⚠️ vl 类**没有** `page_num` 字段，也**没有** `bboxes`；`source_refs["_vl"]["page_num"]` 会 KeyError。vl 类同样**不产生** `_model_pages`（VL 不走 `{value,reason,pages}` 文本解析）。

---

## 7. `_model_pages` 模型自报页码（与算法命中页互补）

除「算法算出的命中页」外，**text / table 类**的 `source_refs` 顶层可能带一个 `_model_pages`——这是**模型输出 `{value, reason, pages}` 时自报的「我得出该值实际参考了哪几页」**（`parse_llm_json_response` 解析 `pages` 后由 `_attach_model_pages` 落库）：

```jsonc
{
  "公司名称": [ {ref}, ... ],       // 算法命中（含 page_num / bboxes）
  "_texts": { ... },
  "_model_pages": [1, 3]            // ★模型自报参考页（int 数组，1-indexed，去重升序）
}
```

- **类型固定为 int 数组**（1-indexed，已去重升序），**不是** string、**不会**是区间——与 `ref.page_num`（string，可区间）不同。
- 与算法命中页**互补、可能不一致**：检索常命中多页，`_model_pages` 只列模型真正引用的页；模型未返回 / 解析失败 / 关闭 LLM（`use_llm=0`）/ VL 类时**无此键**。
- 以 `_` 开头，§10 的取页函数会**自动跳过**它，老消费者不受影响；需要模型自报页时**单独读** `source_refs.get("_model_pages")`（或用 `model_pages_of_extraction`）。
- 前端提取结果卡片将其单列为「模型自报页码」行，并作为 PDF 定位（📍）的**首选跳转页**，无则回退算法命中页。

---

## 8. analyzing 规则的 source_refs（嵌套依赖字段）

分析规则（`judge` / `calc`）本身**不产生新页码**，它的溯源是**把每个依赖字段的抽取 source_refs 原样嵌进来**（`analysis_service.py:run_analysis`）：

```jsonc
{
  "uuid-field-1": { /* 字段1 的完整抽取 source_refs（text/table/vl 三种布局之一）*/ },
  "uuid-field-2": { /* 字段2 的完整抽取 source_refs */ },
  "_web_search": {                                  // 可选：judge 联网搜索溯源，无 PDF 页码
    "query": "拼接后的搜索词",
    "results": [ {"name": "...", "url": "...", "siteName": "...",
                  "datePublished": "...", "summary": "..."} ],
    "error": "搜索失败提示"                          // 仅失败时出现
  }
}
```

**取页码：**遍历 `source_refs`，跳过 `_web_search`，剩下每个 value 就是一个字段的抽取 source_refs，**递归套用 §2–§7 的逻辑**（复用 §10 的 `pages_of_extraction`）。

- `_web_search` 溯源里**没有 PDF 页码**（是外部网页），只有 `url` / `siteName` 等。
- 依赖字段若抽取失败（source_refs 为 null），它不会进规则的 source_refs → 天然跳过。
- `input_values`（依赖字段取值）与 `source_refs` 是两个字段，前者是值、后者是溯源。

---

## 9. 页码的类型与格式（唯一权威规则）

### 9.1 页码出现的全部位置（总览）

| # | 事件 / 接口 | 路径 | 类型 | 说明 |
|---|---|---|---|---|
| 1 | `stage_done`(tableing) / `GET /file/{id}/tables` | `tables[i].page_num` | **string** | 表格所在页，直取 |
| 2 | `stage_done`(chunking) / `GET /file/{id}/chunks` | `chunks[i].page_num` | **string** | 分块所在页，直取 |
| 3 | `field_done` / extracting 结果 | `source_refs` 内（§4/§5/§6） | **string / int[]** | **核心，随布局分 3 种** |
| 3′ | `field_done` / extracting 结果 | `source_refs._model_pages`（§7） | **int[]** | 模型自报参考页（text/table，可选） |
| 4 | `rule_done` / analyzing 结果 | `source_refs[field_id]` 内（§8） | 同 #3 | 嵌套依赖字段的抽取 source_refs |

> `parsing` 阶段 `stage_done.data.page_mapping` 是**原始映射表**（文本位置 → 页码），不是某条数据的页码，通常无需直读；它是位置 1/3/4 反查页码的底层数据。

### 9.2 string 页码的取值与统一约定

位置 1 / 2 / 3(text,table) / 4 的 `page_num` 统一是**字符串**：

| 取值样例 | 含义 | 处理 |
|---|---|---|
| `"12"` | 单页 | `int("12")` |
| `"12-15"` | 跨页区间（含首尾） | 按 `-` split，展开成 `[12,13,14,15]` |
| `"5-8"` | page 检索回填的 `page_range` 原串 | 同上；也可能是 `"1,3,5"` 等自定义格式 |
| `""` | 取不到（无 `file_content` / `page_mapping` 为空 / 该 chunk 无页码） | 视为「未知页」，**不可强转 int** |

**统一约定：**

- 所有页码 **1-indexed**（第 1 页 = `1`，解析时 `page_idx + 1`），直接对应 PDF 第几页，无需 +1/-1。
- string 页码**禁止直接 `int()`**：先判空、再判 `-`，统一走 §10 的 `parse_page_num_str`。
- 需要「命中页集合」时归一成 `List[int]`。
- 位置 3(vl) 没有 `page_num`，页码在 `_vl.key_pages`，是 **int 数组（1-indexed）或 null**（§6）。

### 9.3 直取页码：tableing / chunking

这两处最简单，`page_num` 就在数组元素上，直接读（均为 string，可能 `""` 或区间，仍用 `parse_page_num_str` 归一）：

```python
for t in data["tables"]:   # tableing：来自 file_table.page_num
    page = t["page_num"]        # "12" / "12-15" / ""
for c in data["chunks"]:   # chunking：来自 file_chunk.page_num
    page = c["page_num"]        # "1" / ""
```

### 9.4 两个 page_num 别搞混：顶层 string vs bboxes 内 int

text / table 类 ref 里**有两个都叫 `page_num` 的字段，类型和用途不同**：

| 位置 | 类型 | 值样例 | 用途 |
|---|---|---|---|
| `ref.page_num` | **string** | `"3"` / `"3-5"` / `""` | 展示 / 跳页，**可能是区间** |
| `ref.bboxes[i].page_num` | **int** | `3` | PDF 高亮画框，**恒单页整数** |

- 只要「命中在第几页」→ 读 **`ref.page_num`**（string，注意区间）。
- 要「在某页画高亮框」→ 读 **`ref.bboxes[i].page_num`**（int）配合 `bbox` + `page_size`。
- `bboxes` 是**可选键**：存量老数据 / page 检索 / vl 类都没有它，读之前判存在。
- 同一条 ref 的 `bboxes` 可能横跨多页（跨页命中），每个元素各带自己的 int `page_num`。

---

## 10. 参考实现（可直接抄）

一套把「任意回调 / 结果 source_refs」归一成 `List[int]` 页码的函数：

```python
from typing import Any, List, Optional


def parse_page_num_str(s: Optional[str]) -> List[int]:
    """把 string 页码归一成 int 列表。
    "12" -> [12]；"12-15" -> [12,13,14,15]；"1,3,5" -> [1,3,5]；"" / None -> []。
    """
    if not s or not isinstance(s, str):
        return []
    pages: List[int] = []
    for part in s.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                if a <= b:
                    pages.extend(range(a, b + 1))
            except ValueError:
                continue
        else:
            try:
                pages.append(int(part))
            except ValueError:
                continue
    return sorted(set(pages))


def model_pages_of_extraction(source_refs: Any) -> List[int]:
    """取「模型自报参考页码」（source_refs._model_pages，1-indexed，去重排序）。
    仅 text / table 类的 LLM 抽取可能有；无此键 / 老数据 / vl 类 → []。
    这是模型自称引用的页，与 pages_of_extraction（程序算的命中页）互补，不要混用。
    """
    if not isinstance(source_refs, dict):
        return []
    mp = source_refs.get("_model_pages")
    if not isinstance(mp, list):
        return []
    return sorted({int(p) for p in mp if isinstance(p, int) or str(p).isdigit()})


def pages_of_extraction(source_refs: Any) -> List[int]:
    """从「一个抽取字段」的 source_refs 里取所有命中页（1-indexed，去重排序）。
    覆盖 text / table / vl / page / 失败 全部形态。
    注：这是**程序算出的命中页**；模型自报页请另用 model_pages_of_extraction。
    """
    if not isinstance(source_refs, dict):
        return []                     # None（失败字段）或异常形态

    # —— vl 类 ——
    if "_vl" in source_refs:
        vl = source_refs["_vl"] or {}
        kp = vl.get("key_pages")
        if isinstance(kp, list):
            return sorted({int(p) for p in kp})
        # vl_progressive: key_pages=null，全篇扫描，用 total_pages 兜底
        total = vl.get("total_pages")
        return list(range(1, total + 1)) if isinstance(total, int) else []

    pages: List[int] = []

    # —— table 类 ——
    if "_tables" in source_refs:
        for ref in source_refs.get("_tables") or []:
            pages += parse_page_num_str(ref.get("page_num"))
        return sorted(set(pages))

    # —— text 类（含 page 检索）——
    for label, refs_list in source_refs.items():
        if label.startswith("_"):     # 跳过 _texts / _model_pages 等元数据键
            continue
        for ref in refs_list or []:
            pages += parse_page_num_str(ref.get("page_num"))
    return sorted(set(pages))


def pages_of_rule(rule_source_refs: Any) -> List[int]:
    """从「一条分析规则」的 source_refs 里取所有命中页（下钻依赖字段）。"""
    if not isinstance(rule_source_refs, dict):
        return []
    pages: List[int] = []
    for key, field_refs in rule_source_refs.items():
        if key == "_web_search":      # 联网搜索无 PDF 页码
            continue
        pages += pages_of_extraction(field_refs)
    return sorted(set(pages))
```

回调 / 结果分发时这样用：

```python
event = payload.get("event")
data  = payload.get("data") or {}

if event == "field_done":                       # extracting 单字段
    pages = pages_of_extraction(data.get("source_refs"))
    model_pages = model_pages_of_extraction(data.get("source_refs"))  # 可选：模型自报页

elif event == "rule_done":                      # analyzing 单规则
    pages = pages_of_rule(data.get("source_refs"))

elif event == "stage_done" and payload["status"] == "tableing":
    for t in data["tables"]:
        pages = parse_page_num_str(t["page_num"])

elif event == "stage_done" and payload["status"] == "chunking":
    for c in data["chunks"]:
        pages = parse_page_num_str(c["page_num"])
```

---

## 11. 容错清单（务必逐条落地）

1. **先判 `source_refs is None`**：失败字段 / 规则的 `source_refs` 是 `null`，没有任何页码。
2. **先分流再取字段**：`_vl` 类没有 `page_num`，text/table 类没有 `key_pages`；用错字段会 KeyError。
3. **string 页码禁止直接 `int()`**：可能是 `"3-5"` 区间或 `""` 空串，统一走 `parse_page_num_str`。
4. **区间要展开**：`"3-5"` 代表 3、4、5 三页，不是「第 3 到 5 号」的两个数。
5. **跳过 `_` 开头的 key**：text 类的 `_texts` / `_model_pages`、分析类的 `_web_search` 都不是命中页数据。
6. **`bboxes` 是可选键**：存量老数据（老 `page_mapping` 无 bbox）、page 检索、vl 类都没有；`ref.get("bboxes")` 判空后再用。
7. **vl_progressive 的 `key_pages=null`**：不是错误，是「全篇扫描无具体定位页」，按 `total_pages` 兜底或标记「全篇」。
8. **页码是 1-indexed**：直接对应 PDF 第几页，无需 +1/-1。
9. **`ref.page_num`（string）与 `bboxes[i].page_num`（int）是两个字段**：展示 / 跳页用前者，画框用后者。
10. **老数据无 `text` / `_texts` / `bboxes` / `_model_pages`**：只有 `page_num`，页码仍可取，别因缺 `bboxes` 就当整条无效。
11. **`_model_pages` 是模型自报页、非程序命中页**：顶层 `_` 键（`startswith("_")` 自动跳过，不污染 `pages_of_extraction`），仅 text/table 类可能有，老数据 / vl 类无；需要时另用 `model_pages_of_extraction` 单取，别和命中页混算。
