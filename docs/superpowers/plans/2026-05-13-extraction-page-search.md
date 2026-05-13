# 文本抽取新增 page 检索方式 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 text source 下新增第 6 种检索方式 `page`，让用户能直接声明 `page_range: "5-7"` 让 LLM 用对应页码的 markdown 文本回答字段。

**Architecture:** 复用 `page_mapping` 把页码转字符位置区间；新增独立函数 `_extract_page_field` 走专属路径（避开按 keyword 分组的通用逻辑），其余 5 种 search_type 完全不动。

**Tech Stack:** Python 3.11 + FastAPI + SQLAlchemy async + pytest-asyncio；前端原生 JS（`ui/js/ruleConfig.js`）。

---

## 关联文档

- 设计稿：`docs/superpowers/specs/2026-05-13-extraction-page-search-design.md`

## 文件结构

| 文件 | 责任 |
|---|---|
| `service/extraction_service.py` | 加 `_parse_page_range`, `slice_by_page_range`, `_extract_page_field` 三个函数；在 `extract_text_field` 入口与 stream 调试函数入口各加一条 `if search_type == "page"` 早退分支 |
| `tests/test_extraction_page.py` | 新增；覆盖 `_parse_page_range` / `slice_by_page_range` / `_extract_page_field` |
| `ui/js/ruleConfig.js` | 下拉加 `page` 选项；`buildSearchConfigFields` 加 `case 'page'`；`collectSearchConfig` 加 `case 'page'` |
| `CLAUDE.md` | Extraction System 段落增列第 6 种 `page` 方法 |

---

## Task 1: 页码区间字符串解析 `_parse_page_range`

**Files:**
- Modify: `service/extraction_service.py`（在 `# ── 检索方法 ─` 段落上方加私有函数）
- Test: `tests/test_extraction_page.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_extraction_page.py`：

```python
"""page 检索方式测试。"""

from __future__ import annotations

import pytest

from service.extraction_service import _parse_page_range


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5", (5, 5)),
        ("5-7", (5, 7)),
        ("  3 - 9 ", (3, 9)),
        ("1-1", (1, 1)),
        ("100", (100, 100)),
    ],
)
def test_parse_page_range_valid(raw, expected):
    assert _parse_page_range(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "0",
        "0-3",
        "5-3",
        "a",
        "5-a",
        "5-7-9",
        "-",
        "5-",
        "-5",
        None,
        5,
    ],
)
def test_parse_page_range_invalid(raw):
    assert _parse_page_range(raw) is None
```

- [ ] **Step 2: 运行测试验证失败**

```
uv run pytest tests/test_extraction_page.py -v
```

预期：`ImportError: cannot import name '_parse_page_range'`。

- [ ] **Step 3: 实现 `_parse_page_range`**

在 `service/extraction_service.py` 中 `# ── 章节解析 ─` 上方插入：

```python
# ── 页码区间解析（page 检索方式） ─────────────────────────────


def _parse_page_range(raw: Any) -> Optional[Tuple[int, int]]:
    """解析 '5' 或 '5-7' 形式的页码区间。

    Returns:
        (start_page, end_page)；不合法返回 None。
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if "-" in s:
        parts = s.split("-")
        if len(parts) != 2:
            return None
        a_str, b_str = parts[0].strip(), parts[1].strip()
        if not a_str or not b_str:
            return None
        try:
            a = int(a_str)
            b = int(b_str)
        except ValueError:
            return None
    else:
        try:
            a = int(s)
        except ValueError:
            return None
        b = a
    if a < 1 or b < a:
        return None
    return a, b
```

- [ ] **Step 4: 运行测试验证通过**

```
uv run pytest tests/test_extraction_page.py -v
```

预期：所有 17 个 case 通过。

- [ ] **Step 5: 提交**

```
git add service/extraction_service.py tests/test_extraction_page.py
git commit -m "feat(extraction): _parse_page_range 解析 page 区间字符串"
```

---

## Task 2: 切片函数 `slice_by_page_range`

**Files:**
- Modify: `service/extraction_service.py`（接在 `_parse_page_range` 之后）
- Test: `tests/test_extraction_page.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_extraction_page.py` 末尾追加：

```python
from service.extraction_service import slice_by_page_range


def _make_mapping():
    """构造一个 5 页文档的 page_mapping。

    md 内容假设为：
      "PAGE1_AAA" * 1 + "PAGE2_BBB" * 1 + ...
    每页一个 block，每个 block 长度 9，block 之间无间隔。
    """
    return [
        {"start_pos": 0, "end_pos": 9, "page_num": 1},
        {"start_pos": 9, "end_pos": 18, "page_num": 2},
        {"start_pos": 18, "end_pos": 27, "page_num": 3},
        {"start_pos": 27, "end_pos": 36, "page_num": 4},
        {"start_pos": 36, "end_pos": 45, "page_num": 5},
    ]


_MD_5P = "PAGE1_AAA" + "PAGE2_BBB" + "PAGE3_CCC" + "PAGE4_DDD" + "PAGE5_EEE"


def test_slice_by_page_range_middle():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 2, 4, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE2_BBB" + "PAGE3_CCC" + "PAGE4_DDD"
    assert r["start_pos"] == 9
    assert r["end_pos"] == 36
    assert r["length"] == 27
    assert r["truncated"] is False


def test_slice_by_page_range_single():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 3, 3, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE3_CCC"


def test_slice_by_page_range_to_end():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 4, 5, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE4_DDD" + "PAGE5_EEE"
    assert r["end_pos"] == len(_MD_5P)


def test_slice_by_page_range_overflow_end():
    """end_page 越界 → 切到末尾。"""
    r = slice_by_page_range(_MD_5P, _make_mapping(), 5, 99, 30000)
    assert r["ok"] is True
    assert r["text"] == "PAGE5_EEE"


def test_slice_by_page_range_start_overflow():
    """start_page 越过末页 → ok=False。"""
    r = slice_by_page_range(_MD_5P, _make_mapping(), 10, 20, 30000)
    assert r["ok"] is False
    assert "10-20" in r["reason"]


def test_slice_by_page_range_empty_mapping():
    r = slice_by_page_range(_MD_5P, [], 1, 3, 30000)
    assert r["ok"] is False
    assert "page_mapping" in r["reason"]


def test_slice_by_page_range_truncate():
    r = slice_by_page_range(_MD_5P, _make_mapping(), 1, 5, 10)
    assert r["ok"] is True
    assert r["text"] == "PAGE1_AAAP"
    assert r["length"] == 10
    assert r["truncated"] is True
    assert r["start_pos"] == 0
    assert r["end_pos"] == 10


def test_slice_by_page_range_empty_md():
    r = slice_by_page_range("", _make_mapping(), 1, 1, 30000)
    assert r["ok"] is False
```

- [ ] **Step 2: 运行测试验证失败**

```
uv run pytest tests/test_extraction_page.py -v
```

预期：`slice_by_page_range` 找不到。

- [ ] **Step 3: 实现 `slice_by_page_range`**

在 `_parse_page_range` 之后追加：

```python
def slice_by_page_range(
    md: str,
    page_mapping: List[Dict[str, Any]],
    start_page: int,
    end_page: int,
    max_length: int,
) -> Dict[str, Any]:
    """按页码区间切 markdown。

    Returns:
        {"ok": True, "text": str, "start_pos": int, "end_pos": int,
         "length": int, "truncated": bool}
        失败时返回 {"ok": False, "reason": str}。
    """
    if not md:
        return {"ok": False, "reason": "文档内容为空"}
    if not page_mapping:
        return {
            "ok": False,
            "reason": "该文件无 page_mapping，无法按页码取文本",
        }

    slice_start: Optional[int] = None
    for entry in page_mapping:
        if entry["page_num"] >= start_page:
            slice_start = entry["start_pos"]
            break
    if slice_start is None:
        return {
            "ok": False,
            "reason": f"页码区间 {start_page}-{end_page} 不在文档范围内",
        }

    slice_end = len(md)
    for entry in page_mapping:
        if entry["page_num"] > end_page:
            slice_end = entry["start_pos"]
            break

    if slice_end <= slice_start:
        return {
            "ok": False,
            "reason": f"页码区间 {start_page}-{end_page} 切片为空",
        }

    text = md[slice_start:slice_end]
    truncated = False
    if len(text) > max_length:
        text = text[:max_length]
        truncated = True

    return {
        "ok": True,
        "text": text,
        "start_pos": slice_start,
        "end_pos": slice_start + len(text),
        "length": len(text),
        "truncated": truncated,
    }
```

- [ ] **Step 4: 运行测试验证通过**

```
uv run pytest tests/test_extraction_page.py -v
```

预期：本任务新增 8 个 case 全部通过；Task 1 的 17 个 case 仍通过。

- [ ] **Step 5: 提交**

```
git add service/extraction_service.py tests/test_extraction_page.py
git commit -m "feat(extraction): slice_by_page_range 按页码切 markdown"
```

---

## Task 3: 字段抽取主路径 `_extract_page_field`

**Files:**
- Modify: `service/extraction_service.py`（在 `extract_table_field` 函数前新增 `_extract_page_field`，并在 `extract_text_field` 入口加早退）
- Test: `tests/test_extraction_page.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_extraction_page.py` 末尾追加：

```python
from unittest.mock import AsyncMock, MagicMock

import service.extraction_service as ext_svc
from service.extraction_service import _extract_page_field


def _make_field(prompt='提取问题: <search_result>page_content</search_result>', system=None):
    """构造一个简单的 ExtractionField stub。"""
    field = MagicMock()
    field.field_id = "fld_test"
    field.text_extract_prompt = prompt
    field.text_system_prompt = system
    return field


@pytest.mark.asyncio
async def test_extract_page_field_happy(monkeypatch):
    captured = {}

    async def fake_chat(prompt, messages=None):
        captured["prompt"] = prompt
        captured["messages"] = messages
        return '{"value": "答案", "reason": "在第3页"}'

    monkeypatch.setattr(ext_svc, "chat_completion", fake_chat)

    field = _make_field()
    config = {"page_range": "3", "max_length": 30000}
    value, reason, refs = await _extract_page_field(_MD_5P, _make_mapping(), config, field)

    assert value == "答案"
    assert reason == "在第3页"
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
            }
        ]
    }
    # prompt 中的 <search_result>page_content</search_result> 已被替换为 PAGE3_CCC
    sent = captured["prompt"] or (captured["messages"] and captured["messages"][-1]["content"])
    assert "PAGE3_CCC" in sent
    assert "<search_result>" not in sent


@pytest.mark.asyncio
async def test_extract_page_field_truncated(monkeypatch):
    async def fake_chat(prompt, messages=None):
        return '{"value": "v", "reason": "r"}'

    monkeypatch.setattr(ext_svc, "chat_completion", fake_chat)

    field = _make_field()
    config = {"page_range": "1-5", "max_length": 10}
    value, reason, refs = await _extract_page_field(_MD_5P, _make_mapping(), config, field)

    assert value == "v"
    assert refs["page_content"][0]["truncated"] is True
    assert refs["page_content"][0]["length"] == 10


@pytest.mark.asyncio
async def test_extract_page_field_invalid_range():
    field = _make_field()
    value, reason, refs = await _extract_page_field(
        _MD_5P, _make_mapping(), {"page_range": "5-3"}, field
    )
    assert value == ""
    assert "page_range" in reason
    assert "'5-3'" in reason
    assert refs is None


@pytest.mark.asyncio
async def test_extract_page_field_missing_range():
    field = _make_field()
    value, reason, refs = await _extract_page_field(_MD_5P, _make_mapping(), {}, field)
    assert value == ""
    assert "page_range" in reason
    assert refs is None


@pytest.mark.asyncio
async def test_extract_page_field_empty_mapping():
    field = _make_field()
    value, reason, refs = await _extract_page_field(
        _MD_5P, [], {"page_range": "1-2"}, field
    )
    assert value == ""
    assert "page_mapping" in reason
    assert refs is None


@pytest.mark.asyncio
async def test_extract_page_field_out_of_range():
    field = _make_field()
    value, reason, refs = await _extract_page_field(
        _MD_5P, _make_mapping(), {"page_range": "100-200"}, field
    )
    assert value == ""
    assert "100-200" in reason
    assert refs is None


@pytest.mark.asyncio
async def test_extract_page_field_missing_placeholder(monkeypatch):
    """prompt 不带占位符时返回空 value，与现有 5 种方法一致。"""
    fake_chat_called = False

    async def fake_chat(prompt, messages=None):
        nonlocal fake_chat_called
        fake_chat_called = True
        return '{"value": "X", "reason": ""}'

    monkeypatch.setattr(ext_svc, "chat_completion", fake_chat)

    field = _make_field(prompt="提取问题（无占位符）")
    value, reason, refs = await _extract_page_field(
        _MD_5P, _make_mapping(), {"page_range": "1"}, field
    )
    assert value == ""
    assert fake_chat_called is False
```

- [ ] **Step 2: 运行测试验证失败**

```
uv run pytest tests/test_extraction_page.py -v
```

预期：`_extract_page_field` 找不到。

- [ ] **Step 3: 实现 `_extract_page_field`**

在 `service/extraction_service.py` 中 `async def extract_table_field` **之前**追加：

```python
async def _extract_page_field(
    content: str,
    page_mapping: List[Dict[str, Any]],
    search_config: Dict[str, Any],
    field: ExtractionField,
) -> Tuple[str, str, Optional[Dict]]:
    """page 检索方式：直接按页码切 markdown 喂 LLM。

    与其他 5 种 text 方法不同，page 不做关键词过滤，只用一个固定 label
    `page_content` 作为 prompt 占位符。
    """
    page_range_raw = (search_config or {}).get("page_range", "")
    parsed = _parse_page_range(page_range_raw)
    if not parsed:
        return "", f"page_range 配置非法：{page_range_raw!r}", None
    start_page, end_page = parsed

    max_length = (search_config or {}).get("max_length", 30000)
    if not isinstance(max_length, int) or max_length <= 0:
        return "", f"max_length 配置非法：{max_length!r}", None

    sliced = slice_by_page_range(content, page_mapping, start_page, end_page, max_length)
    if not sliced["ok"]:
        return "", sliced["reason"], None

    results_text_by_label = {"page_content": sliced["text"]}
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
            }
        ]
    }

    prompt_template = field.text_extract_prompt or ""
    if not validate_prompt_has_placeholder(prompt_template):
        logger.warning("字段 {} 的 text_extract_prompt 缺少占位符", field.field_id)
        return "", "", None

    llm_input = replace_search_result_placeholders(prompt_template, results_text_by_label)
    llm_input += JSON_OUTPUT_INSTRUCTION

    try:
        system_prompt = (field.text_system_prompt or "").strip()
        if system_prompt:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": llm_input},
            ]
            response = await chat_completion("", messages=messages)
        else:
            response = await chat_completion(llm_input)
        value, reason = parse_llm_json_response(response)
        return value, reason, source_refs
    except Exception as e:
        logger.error("LLM 文本提取失败 (page): {}", e)
        return "", "", None
```

- [ ] **Step 4: 运行测试验证通过**

```
uv run pytest tests/test_extraction_page.py -v
```

预期：本任务新增 7 个 case 全部通过；前两个任务的测试仍通过。

- [ ] **Step 5: 提交**

```
git add service/extraction_service.py tests/test_extraction_page.py
git commit -m "feat(extraction): _extract_page_field page 字段抽取主流程"
```

---

## Task 4: 接入 `extract_text_field` 主调度

**Files:**
- Modify: `service/extraction_service.py`（约 line 645，`search_type` dispatch 上方加早退）

- [ ] **Step 1: 找到 dispatch 入口**

`service/extraction_service.py` 当前长这样（约 line 643-660）：

```python
content = file_content.file_content
page_mapping = file_content.page_mapping or []
search_type = field.search_type or "context"
search_config = field.search_config or {}

# 调用对应的检索方法
search_results = []
if search_type == "context":
    search_results = await search_context(content, search_config)
elif search_type == "section":
    search_results = await search_section(content, search_config)
elif search_type == "rule":
    search_results = await search_rule(content, search_config)
elif search_type == "chunk_db":
    search_results = await search_chunk_db(file_id, search_config, session)
elif search_type == "vector_db":
    search_results = await search_vector_db(file_id, search_config)

if not search_results:
    return "", "", None
```

- [ ] **Step 2: 在 dispatch 上方加早退**

修改为：

```python
content = file_content.file_content
page_mapping = file_content.page_mapping or []
search_type = field.search_type or "context"
search_config = field.search_config or {}

# page 方法走独立路径（不经按 keyword 分组的通用流程）
if search_type == "page":
    return await _extract_page_field(content, page_mapping, search_config, field)

# 调用对应的检索方法
search_results = []
if search_type == "context":
    search_results = await search_context(content, search_config)
elif search_type == "section":
    ...  # 保持原样
```

- [ ] **Step 3: 运行 extraction 全套测试**

```
uv run pytest tests/test_extraction_service.py tests/test_extraction_page.py tests/test_extraction_router.py -v
```

预期：现有 5 种方法不受影响，page 测试继续通过。

- [ ] **Step 4: 提交**

```
git add service/extraction_service.py
git commit -m "feat(extraction): extract_text_field 接入 page 检索方式"
```

---

## Task 5: 接入 stream 调试函数

**Files:**
- Modify: `service/extraction_service.py`（约 line 1327-1352，stream 调试 dispatch）

- [ ] **Step 1: 定位 stream dispatch**

在 `service/extraction_service.py` 大约 line 1327 起，是 stream 调试函数（用于「检索预览」事件流）。当前约长这样：

```python
else:
    # 文本检索
    search_type = field.search_type or "context"
    search_config = field.search_config or {}

    # 获取文件内容
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await session.execute(stmt)
    file_content = result.scalar_one_or_none()

    if not file_content:
        yield {"event": "error", "data": {"message": "文件内容不存在"}}
        return

    content = file_content.file_content

    if search_type == "context":
        search_results = await search_context(content, search_config)
    elif search_type == "section":
        ...
```

- [ ] **Step 2: 改为同样加早退（但只 yield 预览事件，不调 LLM）**

stream 调试只走到 `search_results`/`results_by_label` 构建即可（LLM 调用在后面 Step 2），所以加 page 分支：

```python
else:
    # 文本检索
    search_type = field.search_type or "context"
    search_config = field.search_config or {}

    # 获取文件内容
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await session.execute(stmt)
    file_content = result.scalar_one_or_none()

    if not file_content:
        yield {"event": "error", "data": {"message": "文件内容不存在"}}
        return

    content = file_content.file_content
    page_mapping = file_content.page_mapping or []   # ← 新增

    search_results = []
    if search_type == "page":
        # page 方法不走通用流程，但 stream 预览也展示切片文本
        page_range_raw = (search_config or {}).get("page_range", "")
        parsed = _parse_page_range(page_range_raw)
        if parsed:
            start_p, end_p = parsed
            max_len = (search_config or {}).get("max_length", 30000)
            if not isinstance(max_len, int) or max_len <= 0:
                max_len = 30000
            sliced = slice_by_page_range(content, page_mapping, start_p, end_p, max_len)
            if sliced["ok"]:
                results_by_label["page_content"] = sliced["text"]
        # 不论成功与否,page 都不构建 search_results 列表
    elif search_type == "context":
        search_results = await search_context(content, search_config)
    elif search_type == "section":
        ...
```

并把后续 `for keyword, items in results_by_keyword.items(): if search_type == "context": ...` 区块**保持不变** —— page 方法 `search_results` 为空，不会进入按 keyword 分组的循环。

- [ ] **Step 3: 手工验证 stream 预览**

启动 server：`uv run uvicorn app:app --host 0.0.0.0 --port 5019 --reload`

打开 `http://localhost:5019/ui` → 流式调试页面 `stream-demo.js` 加载的页面，对一个已有文件触发一个 search_type=page 的字段抽取，确认 search_results 事件 `data.results_by_label.page_content` 含切片文本。（如果 UI 还没改，可以临时 `curl -N` 调 stream 调试 endpoint 验证）

- [ ] **Step 4: 提交**

```
git add service/extraction_service.py
git commit -m "feat(extraction): stream 调试函数接入 page 检索方式"
```

---

## Task 6: 前端 UI 接入

**Files:**
- Modify: `ui/js/ruleConfig.js`（3 处：dropdown 选项、`buildSearchConfigFields`、`collectSearchConfig`）

- [ ] **Step 1: 加 dropdown 选项**

在 `ui/js/ruleConfig.js` 约 line 461-466，把：

```javascript
<option value="context" ${searchType === 'context' ? 'selected' : ''}>上下文检索</option>
<option value="section" ${searchType === 'section' ? 'selected' : ''}>章节检索</option>
<option value="rule" ${searchType === 'rule' ? 'selected' : ''}>规则检索</option>
<option value="chunk_db" ${searchType === 'chunk_db' ? 'selected' : ''}>分块数据库</option>
<option value="vector_db" ${searchType === 'vector_db' ? 'selected' : ''}>向量数据库</option>
```

改为：

```javascript
<option value="context" ${searchType === 'context' ? 'selected' : ''}>上下文检索</option>
<option value="section" ${searchType === 'section' ? 'selected' : ''}>章节检索</option>
<option value="rule" ${searchType === 'rule' ? 'selected' : ''}>规则检索</option>
<option value="chunk_db" ${searchType === 'chunk_db' ? 'selected' : ''}>分块数据库</option>
<option value="vector_db" ${searchType === 'vector_db' ? 'selected' : ''}>向量数据库</option>
<option value="page" ${searchType === 'page' ? 'selected' : ''}>按页码取文</option>
```

- [ ] **Step 2: 加 `buildSearchConfigFields` 的 case 'page'**

在 `vector_db` case 块（约 line 643-659）**之后**、`switch` 块结束 `}` **之前**插入：

```javascript
case 'page':
    html = `
        <div class="form-row">
            <div class="form-group">
                <label class="form-label">页码范围</label>
                <input class="form-input" id="fm-sc-page-range" value="${Utils.escapeHtml(config.page_range || '')}" placeholder="如 5-7 或单页 5">
                <div class="form-hint">单一连续区间，1 起。直接把这些页的解析文本喂给 LLM</div>
            </div>
            <div class="form-group">
                <label class="form-label">最大字符数</label>
                <input class="form-input" id="fm-sc-page-max-length" type="number" value="${config.max_length ?? 30000}" min="1">
                <div class="form-hint">超过则末尾截断，避免超过 LLM 上下文上限</div>
            </div>
        </div>
    `;
    break;
```

- [ ] **Step 3: 加 `collectSearchConfig` 的 case 'page'**

在 `collectSearchConfig`（约 line 1016-1071）的 `case 'vector_db':` 之后、`}` 之前插入：

```javascript
case 'page':
    config.page_range = getVal('fm-sc-page-range');
    config.max_length = getInt('fm-sc-page-max-length', 30000);
    break;
```

- [ ] **Step 4: 浏览器手工验证**

1. `python app.py` 起服务
2. 打开 `http://localhost:5019/ui`
3. 进入「字段配置」→ 新建字段 → 来源类型选「文本」→ 检索方式选「按页码取文」
4. 检查表单出现「页码范围」和「最大字符数」两个输入框
5. 填入 `page_range=1-3`，prompt 写 `请抽取作者：<search_result>page_content</search_result>`，保存
6. 选一个已 parsing 完毕的文件，触发该字段抽取
7. 在「源引用」面板中确认 `page_num` 显示 `1-3`、`type=page`、`length` 与 `truncated` 字段存在

**注：** 这是 UI 改动，只能用浏览器验证，不能用 type-checking 或单测保证功能正确。如果你跑不动浏览器（headless 环境等），**明确报告"无法浏览器验证"**，不要谎称"已通过"。

- [ ] **Step 5: 提交**

```
git add ui/js/ruleConfig.js
git commit -m "feat(ui): 字段配置加 page 检索方式选项"
```

---

## Task 7: 更新 CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`（Extraction System 段落）

- [ ] **Step 1: 找到段落**

`CLAUDE.md` 中 `### Extraction System (service/extraction_service.py)` 段落，当前长这样：

```markdown
- **text** - 5 search methods: `context` (keyword+surrounding text), `section` (chapter matching), `rule` (keyword+stopword boundary), `chunk_db` (MySQL chunk search), `vector_db` (Milvus semantic search). Results injected into prompt via same placeholder system.
```

- [ ] **Step 2: 改为 6 种方法**

把上行替换为：

```markdown
- **text** - 6 search methods: `context` (keyword+surrounding text), `section` (chapter matching), `rule` (keyword+stopword boundary), `chunk_db` (MySQL chunk search), `vector_db` (Milvus semantic search), `page` (按 `page_range` 直接切 markdown 喂 LLM；占位符固定为 `<search_result>page_content</search_result>`，可配 `max_length` 末尾截断). Results injected into prompt via same placeholder system.
```

- [ ] **Step 3: 提交**

```
git add CLAUDE.md
git commit -m "docs(claude-md): 标注 page 为第 6 种 text 检索方式"
```

---

## Task 8: 端到端冒烟（人工）

**Files:** 无代码改动。

- [ ] **Step 1: 启动服务**

```
python app.py
```

- [ ] **Step 2: 跑一次完整管线**

通过 UI 上传一个有结构化信息的 PDF（最好 5 页以上），等待 parsing → tableing → chunking → embedding 完成。

- [ ] **Step 3: 配置 page 字段并运行抽取**

在「字段配置」页（默认文档类型）：
- 新建一个文本类字段，例如「文档摘要」
- 检索方式选「按页码取文」
- page_range 填 `1-2`，max_length 留默认 30000
- 用户提示词写 `请提取这份文档的摘要：<search_result>page_content</search_result>`
- 保存后回到文件详情页，触发该字段抽取
- 确认抽取结果非空、reason 合理、源引用面板 `page_num=1-2`

- [ ] **Step 4: 故意配错触发失败路径**

- 把 page_range 改成 `999-1000` → 重新抽取该字段 → 确认结果 success=False、reason 含 "999-1000 不在文档范围"
- 把 page_range 改成 `5-3` → 确认 reason 含 "page_range 配置非法：'5-3'"
- 把 max_length 改成 `10` → 确认抽取仍成功，源引用 `truncated=true`

- [ ] **Step 5: 不需要提交**

无代码改动。如果发现 bug，回到对应 Task 修复。

---

## 自审

- [x] 设计稿每节都有任务覆盖：第 2 节(配置 schema) → Task 6；第 3 节(校验) → Task 1, 3；第 4 节(切片) → Task 2；第 5 节(prompt 占位符) → Task 3；第 6 节(source_refs) → Task 3；第 7 节(错误矩阵) → Task 1, 2, 3；第 8 节(测试) → Task 1, 2, 3, 8；第 9 节(影响范围) → Task 4, 5, 6, 7
- [x] 无 TBD / TODO / "适当处理"
- [x] 类型一致：Task 1 返回 `Optional[Tuple[int,int]]`、Task 2 返回 `Dict[str,Any]`，Task 3 消费上述两者，调用签名匹配
- [x] 函数命名一致：`_parse_page_range` / `slice_by_page_range` / `_extract_page_field` 在所有任务中拼写完全一致
- [x] HTML element id 一致：`fm-sc-page-range` 和 `fm-sc-page-max-length` 在 Task 6 Step 2 与 Step 3 一致
