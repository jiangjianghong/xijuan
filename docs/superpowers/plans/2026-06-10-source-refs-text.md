# source_refs 携带检索原文 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 抽取结果的 `source_refs` 落库时携带模型实际看到的检索原文（每条命中一段 `text` + 每个占位符 label 一份拼接后的 `_texts`），并在 API / 回调 / 前端 UI 全部出口透出。

**Architecture:** 方案 A（增量扩展）：保持 `source_refs` 现有 `{label: [refs...]}` 形状不变，给每条 ref 加 `text` 字段，顶层新增 `_texts` 键存 `{label: 拼接后注入 prompt 的文本}`。写入时一次成型——回调 payload 本来就含 `source_refs`，零改动自动携带；API 只需在 `ExtractionResultItem` 补上 `source_refs` 字段透传；UI 详情页抽取 tab 加可折叠「检索原文」区块。VL 类（`_vl`）无检索文本，保持不变。存量老数据无 `text`/`_texts`，所有消费方容错（缺失即不展示），无需迁移。

**Tech Stack:** FastAPI + SQLAlchemy async（JSON 列）+ pytest-asyncio + 原生 JS 前端。

**背景知识（实施者必读）：**

- 抽取有三类来源（`service/extraction_service.py`）：
  - **text 类** `extract_text_field`（约 :785）：6 种检索。其中 5 种（context/section/rule/chunk_db/vector_db）走通用流程（:815-877）：检索 → 按 keyword 建 `source_refs` → 按 keyword 拼 `results_text_by_label` → 注入 prompt。第 6 种 page 走独立函数 `_extract_page_field`（:583）。
  - **table 类** `extract_table_field`（约 :648）：匹配表格 → refs 放 `source_refs["_tables"]`（:733-745）→ 所有表格内容拼成一段放 `results_text_by_label[label]`（label = `field.table_name_pattern or "表格"`，:747-756）。
  - **vl 类**：不经文本检索，`source_refs = {"_vl": {...}}`，**本计划不动它**。
- 各检索类型结果里的「原文片段」字段名不同：context→`context`，section→`content`，rule→`extracted_text`，chunk_db/vector_db→`chunk_content`。
- 已知现状（保持不变，不要"顺手修"）：section/vector_db 的检索结果没有 `keyword` 键，所以现有代码里它们进不了 `results_text_by_label`（占位符会被替换成"未找到"提示）。`_texts` 必须忠实等于 `results_text_by_label`——它的语义是"模型实际看到的"，不是"理应看到的"。
- `lookup_page_num(mapping, start, end)` 来自 `utils/page_mapping.py:100`，mapping 为空时返回 `""`。mapping 条目形如 `{"page_num": "1", "start_pos": 0}`。
- 运行测试命令一律用 `uv run pytest ...`（Windows + uv 环境）。

**最终 source_refs 形状示例（text 类，context 检索）：**

```jsonc
{
  "_texts": { "合同金额": "片段1\n---\n片段2" },
  "合同金额": [
    {"type": "context", "start_pos": 100, "end_pos": 400, "text": "片段1", "page_num": "3"},
    {"type": "context", "start_pos": 900, "end_pos": 1200, "text": "片段2", "page_num": "5"}
  ]
}
```

---

### Task 1: text 类通用路径——提取纯函数 `_build_text_source_refs` 并携带原文

把 `extract_text_field` 里构建 `source_refs` + `results_text_by_label` 的内联代码（`service/extraction_service.py:831-877`）抽成模块级纯函数，加 `text`/`_texts`，便于无 DB 单测。

**Files:**
- Modify: `service/extraction_service.py`（:831-877 一带）
- Test: `tests/test_source_refs_text.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_source_refs_text.py`：

```python
"""source_refs 携带检索原文（text/_texts）测试。"""

from __future__ import annotations

from service.extraction_service import _build_text_source_refs

_MAPPING = [
    {"page_num": "1", "start_pos": 0},
    {"page_num": "2", "start_pos": 100},
]


def test_context_refs_carry_text_and_joined():
    search_results = [
        {"keyword": "金额", "position": 10, "context": "合同金额为100万元",
         "start_pos": 5, "end_pos": 25},
        {"keyword": "金额", "position": 120, "context": "总金额含税",
         "start_pos": 110, "end_pos": 130},
    ]
    refs, texts = _build_text_source_refs("context", search_results, _MAPPING)

    assert [r["text"] for r in refs["金额"]] == ["合同金额为100万元", "总金额含税"]
    assert refs["金额"][0]["page_num"] == "1"
    assert refs["金额"][1]["page_num"] == "2"
    assert texts == {"金额": "合同金额为100万元\n---\n总金额含税"}
    assert refs["_texts"] == texts


def test_chunk_db_refs_carry_text():
    search_results = [
        {"keyword": "乙方", "chunk_id": "c1", "chunk_index": 0,
         "chunk_content": "乙方为某某公司", "start_pos": 0, "end_pos": 20,
         "page_num": "2"},
    ]
    refs, texts = _build_text_source_refs("chunk_db", search_results, [])

    ref = refs["乙方"][0]
    assert ref["text"] == "乙方为某某公司"
    assert ref["chunk_id"] == "c1"
    assert ref["page_num"] == "2"
    assert texts == {"乙方": "乙方为某某公司"}


def test_rule_refs_carry_text():
    search_results = [
        {"keyword": "工期", "position": 3, "extracted_text": "工期为90天",
         "start_pos": 3, "end_pos": 12},
    ]
    refs, texts = _build_text_source_refs("rule", search_results, [])
    assert refs["工期"][0]["text"] == "工期为90天"
    assert texts == {"工期": "工期为90天"}


def test_section_without_keyword_keeps_legacy_behavior():
    """section 结果无 keyword：refs 按 section_title 分组、_texts 为空（与现状一致）。"""
    search_results = [
        {"section_number": "3", "section_title": "付款方式", "section_index": 2,
         "content": "按月支付", "start_pos": 0, "end_pos": 10},
    ]
    refs, texts = _build_text_source_refs("section", search_results, [])
    assert refs["付款方式"][0]["text"] == "按月支付"
    assert texts == {}
    assert refs["_texts"] == {}
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_source_refs_text.py -v`
Expected: FAIL — `ImportError: cannot import name '_build_text_source_refs'`

- [ ] **Step 3: 实现**

在 `service/extraction_service.py` 中、`extract_text_field` 定义之前，新增：

```python
# 各检索类型结果中"原文片段"的字段名
_SEGMENT_TEXT_KEY = {
    "context": "context",
    "section": "content",
    "rule": "extracted_text",
    "chunk_db": "chunk_content",
    "vector_db": "chunk_content",
}


def _build_text_source_refs(
    search_type: str,
    search_results: List[Dict[str, Any]],
    page_mapping: List[Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict]], Dict[str, str]]:
    """构建带检索原文的 source_refs 与按 label 拼接的检索文本。

    每条 ref 携带 text（该条命中注入 prompt 的原始片段）；
    source_refs["_texts"] = {label: 拼接后实际注入占位符的完整文本}。

    Returns:
        (source_refs, results_text_by_label) 元组。
    """
    text_key = _SEGMENT_TEXT_KEY.get(search_type, "context")

    # 按关键词分组收集 source_refs
    source_refs: Dict[str, List[Dict]] = {}
    for r in search_results:
        keyword = r.get("keyword", "")
        # section 类型没有 keyword，用 section_title 作为 key
        if not keyword and "section_title" in r:
            keyword = r.get("section_title", "")

        ref: Dict[str, Any] = {
            "type": search_type,
            "start_pos": r.get("start_pos"),
            "end_pos": r.get("end_pos"),
            "text": r.get(text_key, ""),
        }
        if "chunk_id" in r:
            ref["chunk_id"] = r["chunk_id"]
            ref["chunk_index"] = r["chunk_index"]

        # 页码：chunk_db/vector_db 结果自带 page_num，其他类型通过 page_mapping 查找
        if "page_num" in r:
            ref["page_num"] = r["page_num"]
        elif r.get("start_pos") is not None and r.get("end_pos") is not None:
            ref["page_num"] = lookup_page_num(page_mapping, r["start_pos"], r["end_pos"])

        source_refs.setdefault(keyword, []).append(ref)

    # 按关键词分组拼接检索文本（与注入 prompt 的内容完全一致）
    results_by_keyword: Dict[str, List[Dict]] = {}
    for r in search_results:
        kw = r.get("keyword", "")
        if kw:
            results_by_keyword.setdefault(kw, []).append(r)

    results_text_by_label: Dict[str, str] = {
        kw: "\n---\n".join(r.get(text_key, "") for r in items)
        for kw, items in results_by_keyword.items()
    }
    source_refs["_texts"] = results_text_by_label
    return source_refs, results_text_by_label
```

然后把 `extract_text_field` 中原有的两段内联逻辑（`# 按关键词分组收集 source_refs` 到 `# 将分组结果转换为文本` 整块，即原 :831-877，从 `source_refs: Dict[str, List[Dict]] = {}` 到 `results_text_by_label[keyword] = "\n---\n".join([r["chunk_content"] for r in items])` 的循环结束）整体替换为一行：

```python
    source_refs, results_text_by_label = _build_text_source_refs(
        search_type, search_results, page_mapping
    )
```

注意保留替换点之后的 `# 构建 LLM 输入` 及其后所有代码不动。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_source_refs_text.py -v`
Expected: 4 PASS

- [ ] **Step 5: 回归既有测试**

Run: `uv run pytest tests/test_extraction_service.py tests/test_extraction_router.py -v`
Expected: 全部 PASS（若有因 DB/外部服务跳过的按原样跳过）

- [ ] **Step 6: Commit**

```bash
git add service/extraction_service.py tests/test_source_refs_text.py
git commit -m "feat: text 类检索 source_refs 携带原文片段与拼接文本(_texts)"
```

---

### Task 2: page 路径携带原文

`_extract_page_field`（`service/extraction_service.py:583-645`）的 source_refs 加 `text` 与 `_texts`。注意既有测试 `test_extract_page_field_happy` 对 refs 做**全等断言**，需同步更新。

**Files:**
- Modify: `service/extraction_service.py:608-621`
- Modify: `tests/test_extraction_page.py`（更新 `test_extract_page_field_happy` 的全等断言；`test_extract_page_field_truncated` 增加 text 断言）

- [ ] **Step 1: 先改测试（失败先行）**

`tests/test_extraction_page.py` 中 `test_extract_page_field_happy` 的 `assert refs == {...}` 整体替换为：

```python
    assert refs == {
        "page_content": [
            {
                "type": "page",
                "page_range": "3",
                "start_pos": 18,
                "end_pos": 27,
                "length": 9,
                "truncated": False,
                "page_num": "3",
                "text": "PAGE3_CCC",
            }
        ],
        "_texts": {"page_content": "PAGE3_CCC"},
    }
```

`test_extract_page_field_truncated` 末尾追加两行断言：

```python
    assert refs["page_content"][0]["text"] == refs["_texts"]["page_content"]
    assert len(refs["_texts"]["page_content"]) == 10
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_extraction_page.py -v`
Expected: `test_extract_page_field_happy` 与 `test_extract_page_field_truncated` FAIL（缺 text/_texts 键），其余 PASS

- [ ] **Step 3: 实现**

`_extract_page_field` 中 `source_refs` 构建块（原 :609-621）替换为：

```python
    source_refs: Dict[str, List[Dict[str, Any]]] = {
        "page_content": [
            {
                "type": "page",
                "page_range": page_range_raw,
                "start_pos": sliced["start_pos"],
                "end_pos": sliced["end_pos"],
                "length": sliced["length"],
                "truncated": sliced["truncated"],
                "page_num": page_range_raw,
                "text": sliced["text"],
            }
        ],
        "_texts": {"page_content": sliced["text"]},
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_extraction_page.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add service/extraction_service.py tests/test_extraction_page.py
git commit -m "feat: page 检索 source_refs 携带切片原文与 _texts"
```

---

### Task 3: table 类携带原文——提取纯函数 `_build_table_source_refs`

把 `extract_table_field` 里构建 refs + 拼接文本的内联代码（`service/extraction_service.py:733-756`）抽成纯函数并加 `text`/`_texts`。每条 ref 的 `text` 含模型实际看到的 `表格名称: xxx\n` 前缀。

**Files:**
- Modify: `service/extraction_service.py`
- Test: `tests/test_source_refs_text.py`（追加）

- [ ] **Step 1: 写失败测试**

`tests/test_source_refs_text.py` 顶部 import 区追加 `from unittest.mock import MagicMock` 与 `_build_table_source_refs`，文件末尾追加：

```python
def _make_table(index, name, content, start=0, end=10, page="1"):
    t = MagicMock()
    t.table_index = index
    t.table_name = name
    t.table_content = content
    t.start_pos = start
    t.end_pos = end
    t.page_num = page
    return t


def test_table_refs_carry_text_and_joined():
    tables = [
        _make_table(0, "报价表", "<table>A</table>", 0, 20, "2"),
        _make_table(1, "明细表", "<table>B</table>", 30, 60, "3"),
    ]
    refs, texts = _build_table_source_refs(tables, "报价")

    assert refs["_tables"][0]["text"] == "表格名称: 报价表\n<table>A</table>"
    assert refs["_tables"][1]["text"] == "表格名称: 明细表\n<table>B</table>"
    assert refs["_tables"][0]["table_name"] == "报价表"
    assert refs["_tables"][0]["page_num"] == "2"
    assert texts == {
        "报价": "表格名称: 报价表\n<table>A</table>\n---\n表格名称: 明细表\n<table>B</table>"
    }
    assert refs["_texts"] == texts


def test_table_refs_unnamed_table_fallback():
    tables = [_make_table(2, "", "<table>C</table>", page="")]
    refs, texts = _build_table_source_refs(tables, "表格")

    assert refs["_tables"][0]["text"] == "表格名称: 表格2\n<table>C</table>"
    assert refs["_tables"][0]["page_num"] == ""
    assert texts == {"表格": "表格名称: 表格2\n<table>C</table>"}
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_source_refs_text.py -v`
Expected: 新增 2 条 FAIL（ImportError 或 AttributeError），原 4 条 PASS

- [ ] **Step 3: 实现**

`service/extraction_service.py` 中、`extract_table_field` 定义之前新增（`FileTable` 已在该模块 import，类型注解直接用）：

```python
def _build_table_source_refs(
    matched_tables: List[FileTable], label: str
) -> Tuple[Dict[str, List[Dict]], Dict[str, str]]:
    """构建带表格原文的 source_refs 与拼接后的检索文本。

    每条 ref 的 text 含模型实际看到的 "表格名称: xxx\\n" 前缀；
    source_refs["_texts"] = {label: 拼接后实际注入占位符的完整文本}。

    Returns:
        (source_refs, results_text_by_label) 元组。
    """
    table_refs = []
    parts = []
    for table in matched_tables:
        table_name = table.table_name or f"表格{table.table_index}"
        text = f"表格名称: {table_name}\n{table.table_content}"
        table_refs.append({
            "type": "table",
            "table_index": table.table_index,
            "table_name": table.table_name,
            "start_pos": table.start_pos,
            "end_pos": table.end_pos,
            "page_num": table.page_num or "",
            "text": text,
        })
        parts.append(text)

    results_text_by_label: Dict[str, str] = {label: "\n---\n".join(parts)} if parts else {}
    source_refs: Dict[str, List[Dict]] = {
        "_tables": table_refs,
        "_texts": results_text_by_label,
    }
    return source_refs, results_text_by_label
```

然后把 `extract_table_field` 中原 :733-756 整块（从 `# 构建 source_refs（表格类使用 _tables 键）` 到 `results_text_by_label[label] = "\n---\n".join(parts)` 行，含 `label = ...` 行）替换为：

```python
    # 使用用户指定的表名作为统一 label
    label = field.table_name_pattern or "表格"
    source_refs, results_text_by_label = _build_table_source_refs(matched_tables, label)
```

注意保留其后 `# 构建 LLM 输入` 及之后代码不动。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_source_refs_text.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add service/extraction_service.py tests/test_source_refs_text.py
git commit -m "feat: table 类 source_refs 携带表格原文与 _texts"
```

---

### Task 4: API 出口——`GET /file/{id}/extraction` 返回 source_refs

`ExtractionResultItem` 补 `source_refs` 字段，路由透传。回调出口（field_done/stage_done）payload 已含 `source_refs`，落库后自动携带新字段，无需改动。

**Files:**
- Modify: `model/schemas.py:343-348`
- Modify: `blue_print/file_router.py:548-559`
- Test: `tests/test_file_router.py`（追加）

- [ ] **Step 1: 写失败测试**

`tests/test_file_router.py` 末尾追加（顶部无需新 import，函数内局部导入避免无 DB 时收集失败）：

```python
@pytest.mark.anyio
async def test_get_extraction_results_with_source_refs(client: AsyncClient):
    """提取结果应透出 source_refs（含检索原文）。"""
    from model.database import get_session_factory
    from model.tables import ExtractionResult

    file_id = "test_src_refs_file"
    refs = {
        "_texts": {"金额": "合同金额为100万元"},
        "金额": [{"type": "context", "start_pos": 1, "end_pos": 9,
                  "page_num": "1", "text": "合同金额为100万元"}],
    }
    factory = get_session_factory()
    async with factory() as session:
        session.add(ExtractionResult(
            file_id=file_id, field_id="f_amount",
            extracted_value="100万元", reason="r", source_refs=refs,
        ))
        await session.commit()

    try:
        resp = await client.get(f"/file/{file_id}/extraction")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["source_refs"] == refs
    finally:
        async with factory() as session:
            obj = await session.get(ExtractionResult, (file_id, "f_amount"))
            if obj:
                await session.delete(obj)
                await session.commit()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_file_router.py::test_get_extraction_results_with_source_refs -v`
Expected: FAIL — 响应里无 `source_refs` 键（KeyError 或断言不等）

- [ ] **Step 3: 实现**

`model/schemas.py` 的 `ExtractionResultItem` 增加一个字段：

```python
class ExtractionResultItem(BaseModel):
    file_id: str
    field_id: str
    field_name: Optional[str] = None
    extracted_value: str
    reason: Optional[str] = None
    source_refs: Optional[Dict[str, Any]] = None
```

（确认文件顶部 `typing` 导入已含 `Dict`、`Any`，`schemas.py` 现有代码已使用 `Dict[str, Any]`，应已具备。）

`blue_print/file_router.py` 的 `get_extraction_results` 构造处增加透传：

```python
            ExtractionResultItem(
                file_id=r.file_id,
                field_id=r.field_id,
                field_name=field_name,
                extracted_value=r.extracted_value,
                reason=r.reason,
                source_refs=r.source_refs,
            ).model_dump()
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_file_router.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add model/schemas.py blue_print/file_router.py tests/test_file_router.py
git commit -m "feat: 提取结果 API 透出 source_refs(含检索原文)"
```

---

### Task 5: 前端 UI——抽取结果 tab 展示「检索原文」

详情弹窗的「提取结果」tab，每个字段卡片下方加可折叠 `<details>` 区块：逐段展示来源（label/类型/页码）+ 原文。老数据无 `text` 时整块不渲染。

**Files:**
- Modify: `ui/js/app.js`（extraction case，约 :600-625；并新增 `renderSourceRefs` 方法）
- Modify: `ui/css/style.css`（`.data-card-field-value` 样式块之后，约 :923 处追加）

- [ ] **Step 1: app.js 新增渲染方法**

在 `app.js` 中 `escapeHtml` 方法所在对象内（与 extraction case 同一对象，紧挨其他工具方法）新增：

```javascript
    // 渲染 source_refs 的「检索原文」折叠区块（老数据无 text/_texts 时返回空串）
    renderSourceRefs(sourceRefs) {
        if (!sourceRefs || typeof sourceRefs !== 'object') return '';
        const segs = [];
        for (const [label, refs] of Object.entries(sourceRefs)) {
            if (label === '_texts' || label === '_vl' || !Array.isArray(refs)) continue;
            refs.forEach(ref => {
                if (!ref || !ref.text) return;
                segs.push({ label, type: ref.type || '', page: ref.page_num || '', text: ref.text });
            });
        }
        if (segs.length === 0) return '';
        let inner = '';
        segs.forEach(seg => {
            const labelText = seg.label && seg.label !== '_tables' ? seg.label : '';
            const meta = [labelText, seg.type, seg.page ? `第 ${seg.page} 页` : '']
                .filter(Boolean).map(m => this.escapeHtml(m)).join(' · ');
            inner += `
                <div class="source-ref-seg">
                    <div class="source-ref-meta">${meta}</div>
                    <div class="source-ref-text">${this.escapeHtml(seg.text)}</div>
                </div>
            `;
        });
        return `
            <details class="source-refs">
                <summary>检索原文（${segs.length} 段）</summary>
                ${inner}
            </details>
        `;
    },
```

注意：app.js 若是 class 语法则方法间不加逗号、用 `renderSourceRefs(sourceRefs) { ... }` 形式；若是对象字面量则保留逗号——以 `escapeHtml` 的现有写法为准。

- [ ] **Step 2: extraction case 调用**

`app.js` 的 `case 'extraction':` 中，卡片模板里 `${item.reason ? ... : ''}` 之后、`</div>`（data-card 收尾）之前插入一行：

```javascript
                                    ${this.renderSourceRefs(item.source_refs)}
```

- [ ] **Step 3: 追加 CSS**

`ui/css/style.css` 中 `.data-card-field-value` 规则块之后追加：

```css
.source-refs {
    margin-top: 8px;
    font-size: 12px;
}

.source-refs summary {
    cursor: pointer;
    color: var(--bio-med);
    font-weight: 500;
    user-select: none;
}

.source-ref-seg {
    margin-top: 8px;
    padding: 8px;
    background: var(--bg-primary, #fff);
    border-radius: 4px;
}

.source-ref-meta {
    font-size: 11px;
    color: var(--bio-light);
    margin-bottom: 4px;
}

.source-ref-text {
    color: var(--text-secondary);
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 160px;
    overflow-y: auto;
}
```

- [ ] **Step 4: 手工验证**

Run: `uv run uvicorn app:app --host 0.0.0.0 --port 5019`
打开 `http://localhost:5019/ui`，选一个已完成抽取的文件 → 详情 → 提取结果 tab：
- 新跑的文件：字段卡片出现「检索原文（N 段）」，展开可见来源/页码/原文
- 老文件（无 text）：不出现该区块，无 JS 报错（F12 console 干净）

（若库里暂无带新 source_refs 的数据，先重跑一个文件的 extracting 阶段：`POST /file/{file_id}/retry/extracting`。）

- [ ] **Step 5: Commit**

```bash
git add ui/js/app.js ui/css/style.css
git commit -m "feat: 详情页提取结果展示检索原文折叠区块"
```

---

### Task 6: 文档同步

**Files:**
- Modify: `CLAUDE.md`（Extraction System 小节）
- Modify: `docs/ASYNC_CALLBACK.md`（field_done / source_refs 说明处）

- [ ] **Step 1: CLAUDE.md**

`### Extraction System` 小节末尾追加一段：

```markdown
- `source_refs` 落库时携带检索原文：每条 ref 含 `text`（该条命中注入 prompt 的原始片段，table 类含 `表格名称: xxx\n` 前缀），顶层 `_texts` 键为 `{label: 拼接后实际注入占位符的完整文本}`。vl 类（`_vl`）无检索文本不受影响。`GET /file/{id}/extraction` 与回调 `field_done`/`stage_done` 均透出完整 `source_refs`。存量老数据无 `text`/`_texts`，消费方需容错。
```

- [ ] **Step 2: ASYNC_CALLBACK.md**

找到 field_done 示例 payload 的 `source_refs` 字段说明处，补充同样内容（text 字段 + `_texts` 键 + 老数据容错），示例 JSON 中给 ref 加上 `"text": "..."` 并加 `"_texts": {...}`。

- [ ] **Step 3: 全量回归**

Run: `uv run pytest`
Expected: 全部 PASS（环境性 skip 除外）

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/ASYNC_CALLBACK.md
git commit -m "docs: source_refs 检索原文(text/_texts)说明"
```
