# 回调接口页码（page_num）获取说明

> 面向**后端消费方**：说明在异步回调（`callback_url`）的各类事件里，如何**准确、无遗漏**地取到每条数据对应的 PDF 页码。
>
> 前置阅读：[`ASYNC_CALLBACK.md`](./ASYNC_CALLBACK.md)（回调总协议、事件序列、payload 形态）。本文只聚焦「页码」这一件事。
>
> 实现位置：
> - 页码映射构建 / 反查：`utils/page_mapping.py`（`build_page_mapping` / `lookup_page_num` / `lookup_bboxes`）
> - 抽取 source_refs 组装：`service/extraction_service.py`（`_build_text_source_refs` / `_build_table_source_refs` / `_extract_page_field` / `extract_vl_field`）
> - 分析 source_refs 组装：`service/analysis_service.py`（`run_analysis`，字段 source_refs 嵌套透传）

---

## 0. 一句话结论

回调里页码有 **4 个来源位置**，格式**不统一**（有 string 有 int[]，有单值有区间有 null），**没有一个全局字段能直接拿页码**。后端必须**先判断数据来源类型，再按类型取对应字段**。直接写 `data["page_num"]` 一把梭会漏一半场景。

---

## 1. 页码出现的全部位置（总览）

| # | 事件 | 路径 | 字段 | 类型 | 说明 |
|---|---|---|---|---|---|
| 1 | `stage_done`（tableing） | `data.tables[i].page_num` | — | **string** | 表格所在页，直取 |
| 2 | `stage_done`（chunking） | `data.chunks[i].page_num` | — | **string** | 分块所在页，直取 |
| 3 | `field_done` / `stage_done`（extracting） | `data.source_refs` 内 | 见 §4 | **string / int[]** | **核心，随来源类型分 3 种布局** |
| 3′ | `field_done` / `stage_done`（extracting） | `data.source_refs._model_pages` | 见 §4.6 | **int[]** | 模型自报的参考页码（text/table 类，可选） |
| 4 | `rule_done` / `stage_done`（analyzing） | `data.source_refs[field_id]` 内 | 见 §5 | 同 §4 | 嵌套依赖字段的抽取 source_refs |

> `parsing` 阶段的 `stage_done.data.page_mapping` 是**原始映射表**（文本位置→页码），不是某条数据的页码，通常后端不用直接读；它是位置 1/3/4 反查页码的底层数据。

---

## 2. 核心规则：page_num 的类型与格式

**位置 1/2/3(text,table)/4** 的 `page_num` 统一是 **字符串**，可能取值：

| 取值样例 | 含义 | 后端处理 |
|---|---|---|
| `"12"` | 单页 | `int("12")` |
| `"12-15"` | 跨页区间（含首尾） | 按 `-` split，展开成 `[12,13,14,15]` |
| `"5-8"` | page 检索方式回填的 `page_range` 原串 | 同上按 `-` 解析；也可能是 `"1,3,5"` 等自定义格式（取决于配置，见 §4.2.4） |
| `""` | 取不到（无 `file_content` / `page_mapping` 为空 / 该 chunk 无页码） | 视为「未知页」，不可强转 int |

**位置 3(vl)** 没有 `page_num` 字段，页码在 `_vl.key_pages`，是 **int 数组（1-indexed）或 null**，见 §4.4。

**统一约定：**
- 所有页码 **1-indexed**（第 1 页 = `1`，解析时 `page_idx + 1`）。
- string 页码**禁止直接 `int()`**，先判空、再判 `-`。
- 需要「命中页集合」时，建议统一归一成 `List[int]`（参考 §7 的 `parse_page_num_str`）。

---

## 3. 直取页码：tableing / chunking 的 stage_done

这两处最简单，`page_num` 就在数组元素上，直接读：

```python
# tableing stage_done
for t in data["tables"]:
    page = t["page_num"]        # "12" / "12-15" / ""

# chunking stage_done
for c in data["chunks"]:
    page = c["page_num"]        # "1" / ""
```

- tableing：来自 `file_table.page_num`（tableing 阶段解析时落库）。
- chunking：来自 `file_chunk.page_num`（chunking 阶段落库）。
- 均为 string，可能 `""`，可能区间。用 §7 的 `parse_page_num_str` 归一。

---

## 4. 核心：extracting 的 source_refs 里取页码

`field_done.data.source_refs`（以及 `stage_done.data.results[i].source_refs`）的**形状随字段 `source_type` 变化**，页码位置也随之不同。**必须先分流，再取页码。**

### 4.1 第一步：判断 source_refs 属于哪种布局

按**固定顺序**判断（顺序不能乱）：

```python
def classify_source_refs(refs):
    if refs is None:
        return "none"          # 失败字段 / 无溯源 → 无页码
    if not isinstance(refs, dict):
        return "unknown"       # 理论上不会出现，容错
    if "_vl" in refs:
        return "vl"            # VL 视觉抽取
    if "_tables" in refs:
        return "table"         # 表格抽取
    return "text"              # 文本抽取（其余 key 均为检索关键词 label）
```

> 判定依据：table 类固定含 `_tables` 键；vl 类固定含 `_vl` 键；text 类没有这两个键，顶层 key 是检索关键词（外加一个 `_texts`）。失败字段的 `source_refs` 直接是 `null`。

### 4.2 text 类布局

```jsonc
{
  "投资估算":        [ {ref}, {ref} ],   // key = 检索关键词 label，value = 命中数组
  "总投资":          [ {ref} ],
  "_texts": { "投资估算": "...", "总投资": "..." }   // 注入 prompt 的全文，非页码，跳过
}
```

**取页码步骤：**遍历顶层，**跳过所有 `_` 开头的 key**（`_texts` 等），其余每个 value 是 ref 数组，逐条读 `ref["page_num"]`：

```python
for label, refs_list in source_refs.items():
    if label.startswith("_"):        # 跳过 _texts / _tables / _vl
        continue
    for ref in refs_list:
        page = ref.get("page_num", "")   # "3" / "3-5" / ""
```

#### 4.2.1 单条 text ref 的完整结构

```jsonc
{
  "type": "context",              // 检索方式：context/section/rule/chunk_db/vector_db/page
  "start_pos": 5120,              // markdown 全文起始位置
  "end_pos": 5680,                // markdown 全文结束位置
  "page_num": "3",                // ★页码（string），来源见下表
  "chunk_id": "xxx",              // 仅 chunk_db/vector_db 有
  "chunk_index": 7,               // 仅 chunk_db/vector_db 有
  "text": "命中的原始片段...",     // 注入 prompt 的原文
  "bboxes": [                     // 可选，块级框，内部另有 int 页码，见 §6
    {"page_num": 3, "bbox": [x0,y0,x1,y1], "page_size": [w,h]}
  ]
}
```

#### 4.2.2 text 类 page_num 的**两条来源**（关键）

| 检索方式（`ref.type`） | `page_num` 来源 | 说明 |
|---|---|---|
| `chunk_db` / `vector_db` | 检索结果自带（取自 `file_chunk.page_num`） | chunking 阶段已算好，直接透传 |
| `context` / `section` / `rule` | `lookup_page_num(page_mapping, start_pos, end_pos)` 实时反查 | 由 `start_pos`/`end_pos` 二分 `page_mapping` 算出，可能跨页 `"3-5"` |
| `page` | 回填 `page_range` 配置原串 | 见 §4.2.4，不是算出来的 |

> 对后端而言**无需关心是哪条来源**——统一读 `ref["page_num"]` 即可，上面只是解释为什么同是 text 类页码格式可能不同（单值 vs 区间）。

#### 4.2.3 `lookup_page_num` 反查算法（背景，了解即可）

`context/section/rule` 的页码由此函数产出（`utils/page_mapping.py:119`）：

1. `page_mapping` 是 `[{start_pos, end_pos, page_num, bbox?, page_size?}]`，按 `start_pos` 升序。
2. 对 `start_pos` 数组 `bisect_right - 1` 定位命中片段起点所在块 → `page_start`。
3. 同法定位 `end_pos` → `page_end`。
4. `page_start == page_end` → 返回 `"3"`；否则返回 `"3-5"`。
5. `page_mapping` 为空 → 返回 `""`。

#### 4.2.4 特例：`page` 检索方式

当 `ref.type == "page"`（字段配置用「按页码切片」检索），布局仍是 text，但 label 固定为 `page_content`，且 `page_num` **直接回填字段配置里的 `page_range` 原始字符串**（`extraction_service.py:665`）：

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

- `page_num` 就是配置的 `page_range`，格式取决于用户怎么填（常见 `"5"` / `"5-8"`；理论上可能有逗号列表，按 `parse_page_num_str` 兜底解析）。
- **page 方式的 ref 不含 `bboxes`**（整页切片无块级坐标），只能靠 `page_num` 跳页。

### 4.3 table 类布局

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
      "bboxes": [                 // 可选，整表框，内部 int 页码见 §6
        {"page_num": 12, "bbox": [x0,y0,x1,y1], "page_size": [w,h]}
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

### 4.4 vl 类布局（无 page_num，读 key_pages）

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

**取页码：**读 `source_refs["_vl"]["key_pages"]`，**已经是 int 数组，1-indexed，无需解析**：

```python
vl = source_refs["_vl"]
pages = vl.get("key_pages")       # [12,13,15] 或 None
if pages is None:
    # vl_progressive：全文扫描，未定位到具体页 → 用 total_pages 兜底或标记「全篇」
    pages = list(range(1, vl["total_pages"] + 1))
```

| `method` | `key_pages` 取值 | 含义 |
|---|---|---|
| `vl_model` | 配置 `page_range` 解析后的页（1-indexed int[]） | 指定页整体喂 VL |
| `vl_locate` | 缩略图定位命中的关键页（去重排序，受 `key_pages_limit` 截断；定位不到回退前 `fallback_pages` 页） | 真正高清提取的页 |
| `vl_progressive` | **`null`** | 逐批扫全篇，不定位具体页；需页码请用 `total_pages` 兜底 |

> ⚠️ vl 类**没有** `page_num` 字段，也**没有** `bboxes`。写 `data["source_refs"]["_vl"]["page_num"]` 会 KeyError。

### 4.5 extracting 页码来源速查表

| `source_type` | source_refs 判定键 | 页码字段 | 类型 | 取不到时 |
|---|---|---|---|---|
| text（context/section/rule） | 无 `_tables`/`_vl` | `refs[label][i].page_num` | string，可区间 | `""` |
| text（chunk_db/vector_db） | 无 `_tables`/`_vl` | `refs[label][i].page_num` | string | `""` |
| text（page） | 顶层含 `page_content` | `refs.page_content[i].page_num` | string(=page_range) | 配置非法则整字段失败 |
| table | 含 `_tables` | `refs._tables[i].page_num` | string | `""` |
| vl_model / vl_locate | 含 `_vl` | `refs._vl.key_pages` | int[] | 定位不到回退前 N 页 |
| vl_progressive | 含 `_vl` | `refs._vl.key_pages = null` | null | 用 `total_pages` 兜底 |
| 失败字段 | `source_refs = null` | 无 | — | 无页码 |

### 4.6 模型自报页码 `_model_pages`（新增，与算法页码互补）

除上面「算法算出的命中页」外，**text / table 类**的 `source_refs` 顶层可能带一个 `_model_pages` 键——这是**模型在输出 `{value, reason, pages}` 时自报的「我得出该值时实际参考了哪几页」**（`extraction_service.py` 的 `parse_llm_json_response` 解析 `pages` 字段后由 `_attach_model_pages` 落库）：

```jsonc
{
  "公司名称": [ {ref}, ... ],       // 算法命中（含 page_num / bboxes）
  "_texts": { ... },
  "_model_pages": [1, 3]            // ★模型自报参考页（int 数组，1-indexed，去重升序）
}
```

- **类型固定为 int 数组**（1-indexed，已去重升序），**不是** string、**不会**是区间——与 `ref.page_num`（string，可区间）不同。
- 与算法命中页**互补、可能不一致**：检索常命中多页，`_model_pages` 只列模型真正引用的页；模型未返回 / 解析失败 / 关闭 LLM（`use_llm=0`）/ VL 类时**无此键**。
- vl 类页码仍看 `_vl.key_pages`（VL 不走 `{value,reason,pages}` 文本解析，不产生 `_model_pages`）。
- 以 `_` 开头，§7 的取页函数 `label.startswith("_")` 会**自动跳过**它，因此老消费者不受影响；需要模型自报页时**单独读** `source_refs.get("_model_pages")`。
- 前端提取结果卡片将其单列为「模型自报页码」行、并作为 PDF 定位的**首选跳转页**（详见 `ui/js/app.js`）。

---

## 5. analyzing 规则的页码（嵌套依赖字段）

分析规则（`judge` / `calc`）本身**不产生新页码**，它的溯源是**把每个依赖字段的抽取 source_refs 原样嵌进来**（`analysis_service.py:345-349`）。结构：

```jsonc
{
  "uuid-field-1": { /* 字段1 的完整抽取 source_refs（text/table/vl 三种布局之一）*/ },
  "uuid-field-2": { /* 字段2 的完整抽取 source_refs */ },
  "_web_search": { /* 可选：judge 联网搜索溯源，无页码 */
    "query": "...", "results": [{"name","url","siteName","datePublished","summary"}]
  }
}
```

> 注意：`ASYNC_CALLBACK.md` §5.6.1 示例把它简化写成了 `[{"type":"field","field_id":...}]`，**以本文与实际代码为准**——它是 `{field_id: 该字段的抽取 source_refs}` 的 dict。

**取页码步骤：**遍历 `source_refs`，跳过 `_web_search`，剩下每个 value 就是一个字段的抽取 source_refs，**递归套用 §4 的逻辑**：

```python
def pages_of_rule(rule_source_refs):
    pages = []
    if not isinstance(rule_source_refs, dict):
        return pages
    for key, field_refs in rule_source_refs.items():
        if key == "_web_search":     # 联网搜索无页码，跳过
            continue
        pages += pages_of_extraction(field_refs)   # ← 复用 §7 的抽取取页函数
    return pages
```

- `_web_search` 溯源里没有 PDF 页码（是外部网页），只有 `url` / `siteName`。
- 依赖字段若抽取失败（其 source_refs 为 null），该字段不会进 `field_source_refs`，规则的 source_refs 里就没有它 → 天然跳过。

---

## 6. 两个 page_num 别搞混：顶层 string vs bboxes 内 int

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

## 7. 参考实现（可直接抄）

一套把「任意回调 source_refs」归一成 `List[int]` 页码的函数：

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
        if label.startswith("_"):     # 跳过 _texts 等元数据键
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

回调分发时这样用：

```python
event = payload.get("event")
data  = payload.get("data") or {}

if event == "field_done":                       # extracting 单字段
    pages = pages_of_extraction(data.get("source_refs"))
    print(data["field_name"], "命中页:", pages)

elif event == "rule_done":                      # analyzing 单规则
    pages = pages_of_rule(data.get("source_refs"))
    print(data["rule_name"], "依据页:", pages)

elif event == "stage_done" and payload["status"] == "tableing":
    for t in data["tables"]:
        print(t["table_name"], parse_page_num_str(t["page_num"]))

elif event == "stage_done" and payload["status"] == "chunking":
    for c in data["chunks"]:
        print(c["chunk_index"], parse_page_num_str(c["page_num"]))
```

---

## 8. 容错清单（务必逐条落地）

1. **先判 `source_refs is None`**：失败字段/规则的 `source_refs` 是 `null`，没有任何页码。
2. **先分流再取字段**：`_vl` 类没有 `page_num`，`page_num` 类没有 `key_pages`；用错字段会 KeyError。
3. **string 页码禁止直接 `int()`**：可能是 `"3-5"` 区间或 `""` 空串，统一走 `parse_page_num_str`。
4. **区间要展开**：`"3-5"` 代表 3、4、5 三页，不是「第 3 到 5 号」的两个数。
5. **跳过 `_` 开头的 key**：text 类的 `_texts`、分析类的 `_web_search` 都不是页码数据。
6. **`bboxes` 是可选键**：存量老数据（老 `page_mapping` 无 bbox）、page 检索、vl 类都没有；`ref.get("bboxes")` 判空后再用。
7. **vl_progressive 的 `key_pages=null`**：这不是错误，是「全篇扫描无具体定位页」，按 `total_pages` 兜底或标记「全篇」。
8. **页码是 1-indexed**：直接对应 PDF 第几页，无需 +1/-1。
9. **`ref.page_num`（string）与 `bboxes[i].page_num`（int）是两个字段**：展示/跳页用前者，画框用后者。
10. **老数据无 `text`/`_texts`/`bboxes`**：只有 `page_num`，页码仍可取，别因缺 `bboxes` 就当整条无效。
11. **`_model_pages` 是模型自报页、非程序命中页**：它是顶层 `_` 键（`startswith("_")` 自动跳过，不会污染 `pages_of_extraction`），仅 text/table 类可能有，老数据/vl 类无；需要时另用 `model_pages_of_extraction` 单取，别和命中页混算。
