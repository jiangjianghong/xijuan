# 表格块 bbox 锚点（定位框住表体）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `build_page_mapping` 把 MinerU 表格块（自带整表 bbox）也收进 page_mapping，让 table 类字段的定位框真正框住表体，而不是表格上方的文本块。

**Architecture:** MinerU `middle_json` 的表格块 `type == "table"`、有顶层 `bbox`，但无 `lines/spans` 文本（`_extract_block_text` 返回空被跳过）——这就是表格进不了 page_mapping 的原因。修复：块循环开头加表格专门分支，不提文本前缀，改在 markdown 中从 cursor 前向找下一个 `<table` 字面量作为锚点（MinerU 输出的表格在 markdown 中就是 `<table>` HTML，`file_table.start_pos` 也指向它），挂整表 bbox。下游 `lookup_bboxes` / 前端零改动。存量数据照旧（重新解析后生效），找不到 `<table` 或块无 bbox 时不产生锚点（行为退回现状，容错）。

**Tech Stack:** Python / pytest（`asyncio_mode=auto`；本模块为纯函数，测试无需 DB）。

**关键代码事实：**
- `utils/page_mapping.py:build_page_mapping` 当前块循环（56-99 行）：`block_text = _extract_block_text(block)` 为空即 `continue`（62-64 行），`bbox`/`_make_entry` 闭包定义在其后（65-77 行）。表格分支要用 `_make_entry`，因此需把 `bbox` 与 `_make_entry` 定义**上移到块循环开头**（`_extract_block_text` 调用之前）。
- MinerU 表格块形状：`{"type": "table", "bbox": [x0,y0,x1,y1], "blocks": [...嵌套 table_body/table_caption...]}`——顶层无 `lines` 键。若真实数据中表格块缺 bbox 或不在 `para_blocks`，新分支不产锚点，无副作用。
- 跨页大表：MinerU 每页各有一个表格块（各自 bbox），markdown 中可能合并为一个 `<table>`——后续表格块找不到第二个 `<table` 时不产锚点，可接受（首页框正确）。
- 既有测试：`tests/test_page_mapping.py`（5 个）、`tests/test_extraction_page.py`（33 个）不得回归。

---

### Task 1: build_page_mapping 表格块锚点

**Files:**
- Modify: `utils/page_mapping.py:56-99`（块循环）+ docstring 算法说明（34-35 行）
- Test: `tests/test_page_mapping.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_page_mapping.py` 末尾追加：

```python
def test_build_page_mapping_table_block_anchor():
    """表格块（type=table，无 lines 文本）应锚定 markdown 中 <table 位置并携带整表 bbox。"""
    md = (
        "前文段落内容用于定位测试的文本\n\n"
        "<table><tr><td>1</td></tr></table>\n\n"
        "后文段落内容也用于定位测试啊"
    )
    middle = {
        "pdf_info": [{
            "page_idx": 0,
            "page_size": [612, 792],
            "para_blocks": [
                {"bbox": [40, 60, 560, 100],
                 "lines": [{"spans": [{"content": "前文段落内容用于定位测试的文本"}]}]},
                {"type": "table", "bbox": [40, 120, 560, 400]},
                {"bbox": [40, 420, 560, 460],
                 "lines": [{"spans": [{"content": "后文段落内容也用于定位测试啊"}]}]},
            ],
        }],
    }
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 3
    table_entry = mapping[1]
    assert table_entry["start_pos"] == md.find("<table")
    assert table_entry["bbox"] == [40, 120, 560, 400]
    assert table_entry["page_size"] == [612, 792]
    assert table_entry["page_num"] == 1


def test_build_page_mapping_table_block_without_bbox_no_anchor():
    """表格块无 bbox 时不产生锚点（无文本前缀可退化，直接跳过）。"""
    md = "<table><tr><td>1</td></tr></table>"
    middle = {"pdf_info": [{"page_idx": 0, "para_blocks": [{"type": "table"}]}]}
    assert build_page_mapping(md, middle) == []


def test_build_page_mapping_table_block_no_table_in_md():
    """markdown 中找不到 <table（如表格未转出 HTML）时不产生锚点、不影响后续块。"""
    md = "只有文本段落内容用于定位测试"
    middle = {
        "pdf_info": [{
            "page_idx": 0,
            "page_size": [612, 792],
            "para_blocks": [
                {"type": "table", "bbox": [1, 2, 3, 4]},
                {"bbox": [40, 60, 560, 100],
                 "lines": [{"spans": [{"content": "只有文本段落内容用于定位测试"}]}]},
            ],
        }],
    }
    mapping = build_page_mapping(md, middle)
    assert len(mapping) == 1  # 仅文本块锚点
    assert "bbox" in mapping[0] and mapping[0]["bbox"] == [40, 60, 560, 100]
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_page_mapping.py -v -k table_block`
Expected: `test_build_page_mapping_table_block_anchor` FAIL（len(mapping) == 2，表格块被现有逻辑跳过）；`test_build_page_mapping_table_block_no_table_in_md` FAIL 或 PASS 均可能（核对失败信息即可）；`without_bbox` PASS（现状本来就跳过）

- [ ] **Step 3: 实现**

`utils/page_mapping.py` 块循环（现 61-99 行）改为——`bbox`/`_make_entry` 上移到循环体开头，其后插入表格分支，文本路径保持不变：

```python
        for block in blocks:
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

            # 表格块：无 lines/spans 文本（提不出前缀），改在 markdown 中前向找
            # <table 字面量作锚点，挂整表 bbox；找不到或无 bbox 时不产锚点（容错）
            if block.get("type") == "table":
                if bbox:
                    pos = md_content.find("<table", cursor)
                    if pos != -1:
                        mapping.append(_make_entry(pos, len("<table")))
                        cursor = pos + 1
                continue

            block_text = _extract_block_text(block)
            if not block_text or len(block_text.strip()) < 3:
                continue

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

同时 docstring 算法说明（现 34-35 行）改为：

```python
    算法：遍历 middle_json 中每页的每个 para_block。文本块提取文本前缀在
    md_content 中前向扫描定位；表格块（type=table）改以 <table 字面量定位，
    挂整表 bbox。记录 (start_pos, page_num)。
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_page_mapping.py -v`
Expected: 8 个全部 PASS（5 旧 + 3 新）

- [ ] **Step 5: 回归**

Run: `uv run pytest tests/test_page_mapping.py tests/test_extraction_page.py tests/test_extraction_service.py tests/test_source_refs_text.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add utils/page_mapping.py tests/test_page_mapping.py
git commit -m "feat: page_mapping 收录表格块锚点,table 类定位框住表体"
```

---

## 手工验收（人工过一遍）

- [ ] 重新上传一个含表格的 PDF 走完管线，table 类字段点「定位」→ 框应覆盖**表体**（而非仅表名行）
- [ ] text 类字段定位不回归（框位置与之前一致或更准）
- [ ] 存量旧文件不受影响（不重新解析则行为照旧）
