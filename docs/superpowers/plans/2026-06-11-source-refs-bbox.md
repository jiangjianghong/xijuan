# source_refs 携带 bbox 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 字段抽取结果的 `source_refs` 每条 ref 携带块级 bbox（含页码与页面尺寸），供前端在 PDF 上高亮定位。

**Architecture:** MinerU `middle_json` 中每个 `para_block` 自带 `bbox`，每页自带 `page_size`。在 `build_page_mapping`（解析阶段构建、存于 `file_content.page_mapping` JSON 列）的每条 entry 上附带 `bbox` + `page_size`；新增 `lookup_bboxes(mapping, start_pos, end_pos)` 二分查找返回范围内所有锚点块的 bbox 列表；`_build_text_source_refs` / `_build_table_source_refs` 给每条 ref 挂 `bboxes` 字段（非空才带）。page 类 ref 不挂（整页切片无意义），vl 类不动。存量老文件 page_mapping 无 bbox → `lookup_bboxes` 返回空 → ref 不带 `bboxes`，消费方容错（与 `text`/`_texts` 上线策略一致）。API 与回调对 `source_refs` 原样透传，无需改动。

**Tech Stack:** Python / FastAPI / SQLAlchemy async / pytest-asyncio（`asyncio_mode = "auto"`）

**设计决策（已与用户确认）：**
- 用途：前端 PDF 高亮定位（块级段落框精度足够，不做行级）
- 存量数据：不迁移，老文件 ref 不带 `bboxes`，消费方容错
- `page_size` 冗余存在每条 mapping entry 上，保持 list 结构向后兼容

**ref 返回示例（text 类）：**
```json
{"type": "context", "start_pos": 1200, "end_pos": 1450,
 "page_num": "3", "text": "...检索原文...",
 "bboxes": [{"page_num": 3, "bbox": [88, 320, 510, 388], "page_size": [612, 792]}]}
```

---

### Task 1: page_mapping 携带 bbox + 新增 lookup_bboxes

**Files:**
- Modify: `utils/page_mapping.py`
- Test: `tests/test_page_mapping.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_page_mapping.py`：

```python
"""page_mapping 工具测试：bbox / page_size 携带与 lookup_bboxes 查找。"""

from utils.page_mapping import build_page_mapping, lookup_bboxes


def _make_middle(blocks_per_page):
    """构造最小 middle_json。blocks_per_page: [(page_size, [(bbox, text), ...]), ...]"""
    pdf_info = []
    for page_idx, (page_size, blocks) in enumerate(blocks_per_page):
        para_blocks = []
        for bbox, text in blocks:
            block = {"lines": [{"spans": [{"content": text}]}]}
            if bbox is not None:
                block["bbox"] = bbox
            para_blocks.append(block)
        page = {"page_idx": page_idx, "para_blocks": para_blocks}
        if page_size is not None:
            page["page_size"] = page_size
        pdf_info.append(page)
    return {"pdf_info": pdf_info}


def test_build_page_mapping_carries_bbox_and_page_size():
    md = "第一段内容用于定位测试的文本片段\n\n第二段内容也用于定位测试的文本"
    middle = _make_middle([
        ([612, 792], [
            ([50, 100, 500, 150], "第一段内容用于定位测试的文本片段"),
            ([50, 200, 500, 260], "第二段内容也用于定位测试的文本"),
        ]),
    ])
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 2
    assert mapping[0]["bbox"] == [50, 100, 500, 150]
    assert mapping[0]["page_size"] == [612, 792]
    assert mapping[1]["bbox"] == [50, 200, 500, 260]
    # 原有字段不受影响
    assert mapping[0]["page_num"] == 1
    assert mapping[0]["start_pos"] == 0


def test_build_page_mapping_block_without_bbox():
    """block 无 bbox / 页面无 page_size 时 entry 不带对应键（容错）。"""
    md = "第一段内容用于定位测试的文本片段"
    middle = _make_middle([(None, [(None, "第一段内容用于定位测试的文本片段")])])
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 1
    assert "bbox" not in mapping[0]
    assert "page_size" not in mapping[0]


def test_lookup_bboxes_range_spans_multiple_blocks():
    mapping = [
        {"start_pos": 0, "end_pos": 20, "page_num": 1,
         "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
        {"start_pos": 100, "end_pos": 120, "page_num": 2,
         "bbox": [10, 80, 300, 120], "page_size": [612, 792]},
        {"start_pos": 300, "end_pos": 320, "page_num": 3,
         "bbox": [10, 140, 300, 180], "page_size": [612, 792]},
    ]
    # 范围 [5, 110] 落在第 1、2 块（5 在块 1 锚点之后 → 包含块 1）
    result = lookup_bboxes(mapping, 5, 110)
    assert result == [
        {"page_num": 1, "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
        {"page_num": 2, "bbox": [10, 80, 300, 120], "page_size": [612, 792]},
    ]


def test_lookup_bboxes_legacy_mapping_without_bbox():
    """存量老数据 entry 无 bbox → 跳过，返回空列表。"""
    mapping = [{"start_pos": 0, "end_pos": 20, "page_num": 1}]
    assert lookup_bboxes(mapping, 0, 100) == []


def test_lookup_bboxes_empty_mapping():
    assert lookup_bboxes([], 0, 100) == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_page_mapping.py -v
```

预期：FAIL，`ImportError: cannot import name 'lookup_bboxes'`

- [ ] **Step 3: 实现**

修改 `utils/page_mapping.py`。

3a. `build_page_mapping` 的页/块循环（现第 55-92 行）替换为（提取 `_make_entry` 局部辅助消除两处重复构造）：

```python
    for page in pdf_info:
        page_num = page.get("page_idx", 0) + 1  # 转为 1-indexed
        page_size = page.get("page_size")
        blocks = page.get("para_blocks", [])

        for block in blocks:
            block_text = _extract_block_text(block)
            if not block_text or len(block_text.strip()) < 3:
                continue
            bbox = block.get("bbox")

            def _make_entry(pos: int, length: int) -> Dict[str, Any]:
                entry: Dict[str, Any] = {
                    "start_pos": pos,
                    "end_pos": pos + length,
                    "page_num": page_num,
                }
                if bbox:
                    entry["bbox"] = bbox
                if page_size:
                    entry["page_size"] = page_size
                return entry

            # 用不同长度的前缀尝试定位
            found = False
            for prefix_len in (50, 30, 20):
                prefix = block_text[:prefix_len].strip()
                if not prefix:
                    continue
                pos = md_content.find(prefix, cursor)
                if pos != -1:
                    mapping.append(_make_entry(pos, len(prefix)))
                    cursor = pos + 1
                    found = True
                    break

            if not found:
                # 尝试更短的片段
                short = block_text[:10].strip()
                if short:
                    pos = md_content.find(short, cursor)
                    if pos != -1:
                        mapping.append(_make_entry(pos, len(short)))
                        cursor = pos + 1
```

3b. 文件末尾新增 `lookup_bboxes`（与 `lookup_page_num` 同套二分定位）：

```python
def lookup_bboxes(
    mapping: List[Dict[str, Any]],
    start_pos: int,
    end_pos: int,
) -> List[Dict[str, Any]]:
    """根据文本位置查找命中范围内的块级 bbox 列表。

    Args:
        mapping: build_page_mapping 返回的映射列表。
        start_pos: 查询的起始位置。
        end_pos: 查询的结束位置。

    Returns:
        [{"page_num": int, "bbox": [x0, y0, x1, y1], "page_size": [w, h]}] 列表。
        锚点块无 bbox（存量老数据）时跳过；映射为空返回空列表。
    """
    if not mapping:
        return []

    positions = [m["start_pos"] for m in mapping]

    # 包含 start_pos 所在块（其锚点可能在 start_pos 之前）
    idx = bisect_right(positions, start_pos) - 1
    if idx < 0:
        idx = 0

    results: List[Dict[str, Any]] = []
    for m in mapping[idx:]:
        if m["start_pos"] > end_pos:
            break
        bbox = m.get("bbox")
        if not bbox:
            continue
        item: Dict[str, Any] = {"page_num": m["page_num"], "bbox": bbox}
        if m.get("page_size"):
            item["page_size"] = m["page_size"]
        results.append(item)
    return results
```

3c. `build_page_mapping` 的 docstring Returns 行更新为：

```python
    Returns:
        按 start_pos 排序的映射列表，每项: {"start_pos": int, "end_pos": int, "page_num": int,
        "bbox": [x0, y0, x1, y1], "page_size": [w, h]}（bbox/page_size 在 middle_json 缺失时不带）。
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_page_mapping.py -v
```

预期：5 个测试全部 PASS

- [ ] **Step 5: 回归：确认老消费者不受影响**

```bash
uv run pytest tests/test_extraction_page.py -v
```

预期：PASS（`slice_by_page_range` / `lookup_page_num` 只读 start_pos/page_num，新增键不影响）

- [ ] **Step 6: 提交**

```bash
git add utils/page_mapping.py tests/test_page_mapping.py
git commit -m "feat: page_mapping 携带块级 bbox 与 page_size,新增 lookup_bboxes"
```

---

### Task 2: text 类 ref 挂 bboxes

**Files:**
- Modify: `service/extraction_service.py`（`_build_text_source_refs`，约 811-864 行；顶部 import 约 22 行）
- Test: `tests/test_extraction_service.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_extraction_service.py` 末尾追加：

```python
def test_build_text_source_refs_attaches_bboxes():
    from service.extraction_service import _build_text_source_refs

    mapping = [
        {"start_pos": 0, "end_pos": 20, "page_num": 1,
         "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
        {"start_pos": 100, "end_pos": 120, "page_num": 2,
         "bbox": [10, 80, 300, 120], "page_size": [612, 792]},
    ]
    results = [{"keyword": "金额", "context": "命中文本", "start_pos": 5, "end_pos": 110}]
    refs, _texts = _build_text_source_refs("context", results, mapping)
    ref = refs["金额"][0]
    assert ref["bboxes"] == [
        {"page_num": 1, "bbox": [10, 20, 300, 60], "page_size": [612, 792]},
        {"page_num": 2, "bbox": [10, 80, 300, 120], "page_size": [612, 792]},
    ]


def test_build_text_source_refs_legacy_mapping_no_bboxes_key():
    """老 mapping 无 bbox → ref 不带 bboxes 键。"""
    from service.extraction_service import _build_text_source_refs

    mapping = [{"start_pos": 0, "end_pos": 20, "page_num": 1}]
    results = [{"keyword": "金额", "context": "命中文本", "start_pos": 5, "end_pos": 15}]
    refs, _texts = _build_text_source_refs("context", results, mapping)
    assert "bboxes" not in refs["金额"][0]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_extraction_service.py -v
```

预期：新增 2 个测试 FAIL（`bboxes` 键不存在），原有测试 PASS

- [ ] **Step 3: 实现**

3a. `service/extraction_service.py` 顶部 import 改为（现第 22 行）：

```python
from utils.page_mapping import lookup_bboxes, lookup_page_num
```

3b. `_build_text_source_refs` 中，页码查找之后、`source_refs.setdefault(...)` 之前（现第 844-850 行之间）插入 bbox 查找。改完后该段为：

```python
        # 页码：chunk_db/vector_db 结果自带 page_num，其他类型通过 page_mapping 查找
        if "page_num" in r:
            ref["page_num"] = r["page_num"]
        elif r.get("start_pos") is not None and r.get("end_pos") is not None:
            ref["page_num"] = lookup_page_num(page_mapping, r["start_pos"], r["end_pos"])

        # 块级 bbox：所有带全文坐标的结果（含 chunk_db/vector_db）统一查 page_mapping；
        # 存量老 mapping 无 bbox 时返回空，不挂键（消费方容错）
        if r.get("start_pos") is not None and r.get("end_pos") is not None:
            bboxes = lookup_bboxes(page_mapping, r["start_pos"], r["end_pos"])
            if bboxes:
                ref["bboxes"] = bboxes

        source_refs.setdefault(keyword, []).append(ref)
```

3c. `_build_text_source_refs` docstring 首段补一句：

```python
    """构建带检索原文的 source_refs 与按 label 拼接的检索文本。

    每条 ref 携带 text（该条命中注入 prompt 的原始片段）；
    带全文坐标的 ref 另携带 bboxes（块级 PDF 框，老数据无 bbox 时不带该键）；
    source_refs["_texts"] = {label: 拼接后实际注入占位符的完整文本}。
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_extraction_service.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add service/extraction_service.py tests/test_extraction_service.py
git commit -m "feat: text 类 source_refs 每条 ref 携带块级 bboxes"
```

---

### Task 3: table 类 ref 挂 bboxes

**Files:**
- Modify: `service/extraction_service.py`（`_build_table_source_refs` 约 650-682 行；`extract_table_field` 约 770-772 行）
- Test: `tests/test_extraction_service.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_extraction_service.py` 末尾追加：

```python
def test_build_table_source_refs_attaches_bboxes():
    from model.tables import FileTable
    from service.extraction_service import _build_table_source_refs

    table = FileTable(
        file_id="f1", table_index=0, total_table=1,
        table_name="资产负债表", table_content="<table><tr><td>1</td></tr></table>",
        start_pos=10, end_pos=50, page_num="2",
    )
    mapping = [
        {"start_pos": 0, "end_pos": 20, "page_num": 2,
         "bbox": [30, 40, 580, 700], "page_size": [612, 792]},
    ]
    refs, _texts = _build_table_source_refs([table], "资产负债表", mapping)
    ref = refs["_tables"][0]
    assert ref["bboxes"] == [
        {"page_num": 2, "bbox": [30, 40, 580, 700], "page_size": [612, 792]},
    ]
    # 原有字段不受影响
    assert ref["table_name"] == "资产负债表"
    assert ref["text"].startswith("表格名称: 资产负债表\n")


def test_build_table_source_refs_legacy_mapping_no_bboxes_key():
    from model.tables import FileTable
    from service.extraction_service import _build_table_source_refs

    table = FileTable(
        file_id="f1", table_index=0, total_table=1,
        table_name="表A", table_content="<table></table>",
        start_pos=10, end_pos=50, page_num="2",
    )
    refs, _texts = _build_table_source_refs([table], "表A", [])
    assert "bboxes" not in refs["_tables"][0]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_extraction_service.py -v
```

预期：新增 2 个测试 FAIL，`TypeError: _build_table_source_refs() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: 实现**

3a. `_build_table_source_refs` 加第三参数并挂 bboxes，整函数改为：

```python
def _build_table_source_refs(
    matched_tables: List[FileTable],
    label: str,
    page_mapping: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """构建带表格原文的 source_refs 与拼接后的检索文本。

    每条 ref 的 text 含模型实际看到的 "表格名称: xxx\\n" 前缀；
    带全文坐标的 ref 另携带 bboxes（块级 PDF 框，老数据无 bbox 时不带该键）；
    source_refs["_texts"] = {label: 拼接后实际注入占位符的完整文本}。

    Returns:
        (source_refs, results_text_by_label) 元组。
    """
    table_refs = []
    parts = []
    for table in matched_tables:
        table_name = table.table_name or f"表格{table.table_index}"
        text = f"表格名称: {table_name}\n{table.table_content}"
        ref: Dict[str, Any] = {
            "type": "table",
            "table_index": table.table_index,
            "table_name": table.table_name,
            "start_pos": table.start_pos,
            "end_pos": table.end_pos,
            "page_num": table.page_num or "",
            "text": text,
        }
        if table.start_pos is not None and table.end_pos is not None:
            bboxes = lookup_bboxes(page_mapping, table.start_pos, table.end_pos)
            if bboxes:
                ref["bboxes"] = bboxes
        table_refs.append(ref)
        parts.append(text)

    results_text_by_label: Dict[str, str] = {label: "\n---\n".join(parts)} if parts else {}
    source_refs: Dict[str, Any] = {
        "_tables": table_refs,
        "_texts": results_text_by_label,
    }
    return source_refs, results_text_by_label
```

3b. `extract_table_field` 中调用处（现第 770-772 行）改为先查 page_mapping 再传入：

```python
    # 使用用户指定的表名作为统一 label
    label = field.table_name_pattern or "表格"

    # 查 page_mapping 用于 bbox 定位（无 file_content 时为空列表，ref 不挂 bboxes）
    content_row = (
        await session.execute(
            select(FileContent).where(FileContent.file_id == file_id)
        )
    ).scalar_one_or_none()
    page_mapping = (content_row.page_mapping or []) if content_row else []

    source_refs, results_text_by_label = _build_table_source_refs(
        matched_tables, label, page_mapping
    )
```

（`FileContent` 与 `select` 已在本文件 import，无需新增。）

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_extraction_service.py -v
```

预期：全部 PASS

- [ ] **Step 5: 全量回归**

```bash
uv run pytest
```

预期：全部 PASS（需测试库连通）

- [ ] **Step 6: 提交**

```bash
git add service/extraction_service.py tests/test_extraction_service.py
git commit -m "feat: table 类 source_refs 每条 ref 携带块级 bboxes"
```

---

### Task 4: 同步文档

**Files:**
- Modify: `CLAUDE.md`（Extraction System 一节 `source_refs` 说明段）
- Modify: `scripts/gen_openapi.py`（字段提取结果接口 description，约 399-407 行）

- [ ] **Step 1: 更新 CLAUDE.md**

Extraction System 一节中这段：

> `source_refs` 落库时携带检索原文：每条 ref 含 `text`（该条命中注入 prompt 的原始片段，table 类含 `表格名称: xxx\n` 前缀），顶层 `_texts` 键为 `{label: 拼接后实际注入占位符的完整文本}`。vl 类（`_vl`）无检索文本不受影响。`GET /file/{id}/extraction` 与回调 `field_done`/`stage_done` 均透出完整 `source_refs`。存量老数据无 `text`/`_texts`，消费方需容错。

改为：

> `source_refs` 落库时携带检索原文：每条 ref 含 `text`（该条命中注入 prompt 的原始片段，table 类含 `表格名称: xxx\n` 前缀），顶层 `_texts` 键为 `{label: 拼接后实际注入占位符的完整文本}`。text/table 类 ref 另携带 `bboxes: [{page_num, bbox: [x0,y0,x1,y1], page_size: [w,h]}]`（MinerU 块级框，来自 page_mapping，供前端 PDF 高亮定位；page 类整页切片不挂）。vl 类（`_vl`）无检索文本/bbox 不受影响。`GET /file/{id}/extraction` 与回调 `field_done`/`stage_done` 均透出完整 `source_refs`。存量老数据无 `text`/`_texts`/`bboxes`（老文件 page_mapping 无 bbox，重新解析后才有），消费方需容错。

- [ ] **Step 2: 更新 gen_openapi.py**

`scripts/gen_openapi.py` 第 403-405 行 description 改为：

```python
                "`source_refs` 为参考块字典：每条 ref 含 `text`（该条命中注入 prompt 的原始片段），"
                "text/table 类 ref 另含 `bboxes`（`[{page_num, bbox, page_size}]` 块级 PDF 框，供前端高亮定位）；"
                "顶层 `_texts` 键为 `{label: 拼接后实际注入占位符的完整文本}`；"
                "vl 类为 `{_vl: {...}}` 元数据（无检索文本）。"
                "存量老数据无 `text`/`_texts`/`bboxes`，消费方需容错。\n\n"
```

- [ ] **Step 3: 重新生成 openapi 产物（若仓库有生成产物的惯例）**

```bash
uv run python scripts/gen_openapi.py
```

预期：无报错。若脚本输出生成的文件（如 `docs/openapi.json`），一并提交。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md scripts/gen_openapi.py
git commit -m "docs: source_refs 携带 bboxes 说明同步"
```

（若 Step 3 生成了产物文件，记得一并 `git add`。）

---

## 验收清单

- [ ] 新文件走完整管线后，`GET /file/{id}/extraction` 的 text/table 类 ref 带 `bboxes`，page/vl 类不带
- [ ] 回调 `field_done` / extracting `stage_done` 中 `source_refs` 同样带 `bboxes`（透传，无代码改动，仅验证）
- [ ] 存量老文件抽取不报错，ref 无 `bboxes` 键
- [ ] `uv run pytest` 全绿
