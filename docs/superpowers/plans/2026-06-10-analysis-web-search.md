# 逻辑分析网络搜索（博查）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** judge 类分析规则支持可开关的网络搜索（博查 Bocha AI）：搜索词可拼接依赖字段提取结果，搜索结果通过 `<web_search_result/>` 占位符注入判断提示词，并随 `source_refs._web_search` 落库溯源。

**Architecture:** 新增 `utils/web_search.py` 博查客户端（httpx 异步 + 重试）；`analysis_rule` 表加 `web_search` JSON 列（`{"enabled", "query", "count", "freshness"}`）；`analysis_service.apply_web_search()` 统一处理搜索 + 占位符替换，被 `run_analysis` / `run_analysis_stream` / `test_rule_analysis_stream` / 调试路由四处复用；搜索失败不致命（占位符替换为失败提示继续判断）。UI 在规则弹窗加开关 + 搜索词配置，调试面板加 `web_search` 事件渲染，详情页分析卡片加搜索来源折叠块。

**Tech Stack:** FastAPI + SQLAlchemy async + httpx + Pydantic + 原生 JS（项目现有栈，无新依赖）。

**约定（全计划通用）：**
- 占位符常量：`<web_search_result/>`（自闭合，区别于成对的 `<field_result>` / `<search_result>`）
- 仅 judge 类型允许启用；calc 不支持
- 运行命令均在项目根 `C:\Users\19404\Desktop\Projects\wanz_prase2_001` 下执行
- 测试需要 MySQL 可连通（项目惯例，无 DB mock）；LLM/搜索一律 monkeypatch
- commit 消息：conventional 前缀英文 + 中文描述

---

### Task 1: WebSearchConfig 配置模型 + config.yaml

**Files:**
- Modify: `utils/config.py`（`VLModelConfig` 之后、顶层配置之前加子模型；`AppConfig` 加字段）
- Modify: `configs/config.yaml`（文件末尾追加 section）
- Test: `tests/test_web_search.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_web_search.py`：

```python
"""博查网络搜索客户端测试。"""

from __future__ import annotations

import httpx
import pytest

from utils.config import AppConfig


def test_web_search_config_defaults():
    """WebSearchConfig 默认值。"""
    cfg = AppConfig().web_search
    assert cfg.base_url == "https://api.bochaai.com/v1/web-search"
    assert cfg.count == 5
    assert cfg.summary is True
    assert cfg.freshness == "noLimit"
    assert cfg.timeout == 10
    assert cfg.retry_count == 2
    assert cfg.max_result_length == 4000
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_web_search.py -v`
Expected: FAIL，`AttributeError: 'AppConfig' object has no attribute 'web_search'`（或 pydantic 报未知属性）

- [ ] **Step 3: 实现配置模型**

`utils/config.py`，在 `VLModelConfig` 类定义之后（第 108 行 `pdf_storage_dir` 之后的空行处）追加：

```python
class WebSearchConfig(BaseModel):
    base_url: str = "https://api.bochaai.com/v1/web-search"
    api_key: str = ""
    count: int = 5
    summary: bool = True
    freshness: str = "noLimit"
    timeout: int = 10
    retry_count: int = 2
    max_result_length: int = 4000
```

`AppConfig` 类中 `vl_model: VLModelConfig = VLModelConfig()` 之后追加：

```python
    web_search: WebSearchConfig = WebSearchConfig()
```

`configs/config.yaml` 末尾追加：

```yaml

# 网络搜索配置（博查 Bocha AI，逻辑分析 judge 规则使用）
web_search:
  base_url: "https://api.bochaai.com/v1/web-search"
  api_key: "<你的博查 API Key>"
  count: 5              # 默认返回条数
  summary: true         # 返回长摘要
  freshness: "noLimit"  # 默认时间范围 noLimit/oneDay/oneWeek/oneMonth/oneYear
  timeout: 10
  retry_count: 2
  max_result_length: 4000  # 注入 prompt 的搜索文本最大字符数（末尾截断）
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_web_search.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/config.py configs/config.yaml tests/test_web_search.py
git commit -m "feat: 新增博查网络搜索配置(web_search)"
```

---

### Task 2: 博查搜索客户端 `utils/web_search.py`

**Files:**
- Create: `utils/web_search.py`
- Test: `tests/test_web_search.py`（追加）

- [ ] **Step 1: 写失败测试**

`tests/test_web_search.py` 末尾追加：

```python
# ── format_search_results ───────────────────────────────────

def test_format_search_results_empty():
    from utils.web_search import format_search_results

    assert format_search_results([], 4000) == "（未搜索到相关结果）"


def test_format_search_results_basic():
    from utils.web_search import format_search_results

    results = [
        {
            "name": "标题一",
            "url": "https://a.com/1",
            "siteName": "站点A",
            "datePublished": "2026-01-02T00:00:00Z",
            "summary": "摘要内容一",
        },
        {"name": "标题二", "url": "https://b.com/2", "summary": "摘要内容二"},
    ]
    text = format_search_results(results, 4000)
    assert "[1] 标题一" in text
    assert "来源: 站点A | 2026-01-02" in text
    assert "摘要: 摘要内容一" in text
    assert "[2] 标题二" in text


def test_format_search_results_truncate():
    from utils.web_search import format_search_results

    results = [{"name": "标题", "summary": "长" * 500}]
    text = format_search_results(results, 50)
    assert len(text) == 50


# ── bocha_web_search ────────────────────────────────────────

_BOCHA_RESPONSE = {
    "code": 200,
    "data": {
        "webPages": {
            "totalEstimatedMatches": 100,
            "value": [
                {
                    "name": "标题一",
                    "url": "https://a.com/1",
                    "siteName": "站点A",
                    "datePublished": "2026-01-02T00:00:00Z",
                    "summary": "摘要内容一",
                },
                {"name": "标题二", "url": "https://b.com/2", "snippet": "片段二"},
            ],
        }
    },
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """捕获请求 payload 并返回固定响应。"""

    last_json = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeAsyncClient.last_json = json
        return _FakeResponse(_BOCHA_RESPONSE)


async def test_bocha_web_search_parses_response(monkeypatch):
    from utils import web_search

    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeAsyncClient)
    formatted, results = await web_search.bocha_web_search("万科 处罚", count=3, freshness="oneYear")

    assert "[1] 标题一" in formatted
    assert len(results) == 2
    # snippet 兜底到 summary 键
    assert results[1]["summary"] == "片段二"
    # 调用参数透传
    assert _FakeAsyncClient.last_json["query"] == "万科 处罚"
    assert _FakeAsyncClient.last_json["count"] == 3
    assert _FakeAsyncClient.last_json["freshness"] == "oneYear"


async def test_bocha_web_search_empty_query():
    from utils.web_search import bocha_web_search

    with pytest.raises(ValueError):
        await bocha_web_search("  ")
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_web_search.py -v`
Expected: 新增用例 FAIL，`ModuleNotFoundError: No module named 'utils.web_search'`

- [ ] **Step 3: 实现客户端**

新建 `utils/web_search.py`：

```python
"""网络搜索客户端：封装博查 Bocha AI Web Search API。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from utils.config import get_config


def format_search_results(results: List[Dict[str, Any]], max_length: int) -> str:
    """将结构化搜索结果格式化为注入 prompt 的文本。

    Args:
        results: [{name, url, siteName, datePublished, summary}] 列表。
        max_length: 最大字符数，超出末尾截断。

    Returns:
        按条编号的格式化文本；空列表返回提示文本。
    """
    if not results:
        return "（未搜索到相关结果）"

    parts: List[str] = []
    for i, r in enumerate(results, 1):
        lines = [f"[{i}] {r.get('name', '')}"]
        date = (r.get("datePublished") or "")[:10]
        meta = " | ".join(x for x in [r.get("siteName") or "", date] if x)
        if meta:
            lines.append(f"来源: {meta}")
        content = r.get("summary") or ""
        if content:
            lines.append(f"摘要: {content}")
        parts.append("\n".join(lines))

    text = "\n\n".join(parts)
    if len(text) > max_length:
        text = text[:max_length]
    return text


async def bocha_web_search(
    query: str,
    *,
    count: Optional[int] = None,
    freshness: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """调用博查 Web Search API。

    Args:
        query: 搜索词。
        count: 返回条数，缺省走配置。
        freshness: 时间范围（noLimit/oneDay/oneWeek/oneMonth/oneYear），缺省走配置。

    Returns:
        (格式化文本, 结构化结果列表) 元组。结构化列表每条含
        name/url/siteName/datePublished/summary 五键（summary 兜底 snippet）。

    Raises:
        ValueError: 搜索词为空。
        httpx.HTTPError: 重试耗尽后仍失败。
    """
    if not query or not query.strip():
        raise ValueError("搜索词为空")

    cfg = get_config().web_search
    payload: Dict[str, Any] = {
        "query": query.strip(),
        "count": count or cfg.count,
        "summary": cfg.summary,
        "freshness": freshness or cfg.freshness,
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    last_exc: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=cfg.timeout) as client:
        for attempt in range(cfg.retry_count):
            try:
                resp = await client.post(cfg.base_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                pages = (data.get("data") or data).get("webPages", {}).get("value", []) or []
                results = [
                    {
                        "name": p.get("name", ""),
                        "url": p.get("url", ""),
                        "siteName": p.get("siteName", ""),
                        "datePublished": p.get("datePublished", ""),
                        "summary": p.get("summary") or p.get("snippet", ""),
                    }
                    for p in pages
                ]
                return format_search_results(results, cfg.max_result_length), results
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response else None
                # 4xx（除 429）是参数/鉴权错误，重试无意义
                if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                    raise
                last_exc = e
            except httpx.HTTPError as e:
                last_exc = e
            if attempt < cfg.retry_count - 1:
                logger.warning("博查搜索重试 {}/{}: {}", attempt + 1, cfg.retry_count, last_exc)
                await asyncio.sleep(2 ** attempt)

    assert last_exc is not None
    raise last_exc
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_web_search.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add utils/web_search.py tests/test_web_search.py
git commit -m "feat: 博查 Web Search 异步客户端(utils/web_search.py)"
```

---

### Task 3: `analysis_rule.web_search` 列 + 启动迁移

**Files:**
- Modify: `model/tables.py:176-197`（`AnalysisRule` 类）
- Modify: `service/init_service.py:57-71`（migrations 列表）

- [ ] **Step 1: ORM 加列**

`model/tables.py` `AnalysisRule` 类中，`depend_fields` 行之后加：

```python
    depend_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    web_search: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

（即在现有 `depend_fields` 行后新增 `web_search` 行。）

- [ ] **Step 2: 启动迁移加条目**

`service/init_service.py` `migrations` 列表末尾（`("doc_type", "enable_embedding", ...)` 之后）加：

```python
            ("analysis_rule", "web_search", "JSON NULL"),
```

- [ ] **Step 3: 验证迁移生效**

Run: `uv run pytest tests/test_startup_check.py -v`
Expected: PASS（启动初始化含建表+迁移，不报错即列已补上）

再确认列存在：

```bash
uv run python -c "
import asyncio
from sqlalchemy import text
from model.database import get_engine

async def main():
    engine = get_engine()
    async with engine.begin() as conn:
        r = await conn.execute(text(
            \"SELECT COUNT(*) FROM information_schema.COLUMNS \"
            \"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'analysis_rule' \"
            \"AND COLUMN_NAME = 'web_search'\"))
        print('web_search column exists:', r.scalar() == 1)
    await engine.dispose()

asyncio.run(main())
"
```

Expected: 输出 `web_search column exists: True`（若为 False，先跑一次 `tests/test_startup_check.py` 或任一会触发 `run_init` 的入口）

- [ ] **Step 4: Commit**

```bash
git add model/tables.py service/init_service.py
git commit -m "feat: analysis_rule 表新增 web_search JSON 列(含启动迁移)"
```

---

### Task 4: Schema 校验 + 结果项透出

**Files:**
- Modify: `model/schemas.py`（`AnalysisRuleCreate` 加字段+校验；`ExportRuleItem` 加字段；`AnalysisResultItem` 加 `source_refs`）
- Test: `tests/test_analysis_web_search.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_analysis_web_search.py`：

```python
"""逻辑分析网络搜索：schema 校验 + 服务层测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from model.schemas import AnalysisRuleCreate


def _judge_payload(**overrides):
    payload = {
        "rule_id": "ws_rule",
        "rule_name": "搜索测试规则",
        "rule_type": "judge",
        "expression": "结合搜索结果<web_search_result/>判断<field_result>a</field_result>是否合规",
        "depend_fields": ["a"],
    }
    payload.update(overrides)
    return payload


def test_web_search_valid():
    rule = AnalysisRuleCreate(**_judge_payload(web_search={"enabled": True, "query": "<field_result>a</field_result> 处罚"}))
    assert rule.web_search["enabled"] is True


def test_web_search_none_ok():
    rule = AnalysisRuleCreate(**_judge_payload())
    assert rule.web_search is None


def test_web_search_disabled_skips_checks():
    """关闭状态下不要求 query 与占位符。"""
    rule = AnalysisRuleCreate(**_judge_payload(
        expression="判断<field_result>a</field_result>",
        web_search={"enabled": False},
    ))
    assert rule.web_search == {"enabled": False}


def test_web_search_requires_query():
    with pytest.raises(ValidationError, match="query"):
        AnalysisRuleCreate(**_judge_payload(web_search={"enabled": True, "query": "  "}))


def test_web_search_requires_placeholder():
    with pytest.raises(ValidationError, match="web_search_result"):
        AnalysisRuleCreate(**_judge_payload(
            expression="判断<field_result>a</field_result>",
            web_search={"enabled": True, "query": "处罚"},
        ))


def test_web_search_judge_only():
    with pytest.raises(ValidationError, match="judge"):
        AnalysisRuleCreate(**_judge_payload(
            rule_type="calc",
            expression="<field_result>a</field_result>*2",
            web_search={"enabled": True, "query": "处罚"},
        ))
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_analysis_web_search.py -v`
Expected: FAIL，`web_search` 字段不存在（pydantic 默认忽略额外字段则 `test_web_search_valid` 报 `rule.web_search` AttributeError）

- [ ] **Step 3: 实现 schema**

`model/schemas.py`：

3a. 顶部 pydantic 导入行加 `model_validator`（现有 `from pydantic import BaseModel, Field, field_validator` 改为）：

```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

3b. `AnalysisRuleCreate` 中 `depend_fields` 行之后加字段：

```python
    depend_fields: Optional[List[str]] = None
    web_search: Optional[Dict[str, Any]] = None
```

3c. `AnalysisRuleCreate.validate_expression` 之后加模型级校验：

```python
    @model_validator(mode="after")
    def validate_web_search(self):
        ws = self.web_search
        if ws and ws.get("enabled"):
            if self.rule_type != RuleTypeEnum.judge:
                raise ValueError("仅 judge 类型规则支持网络搜索")
            if not (ws.get("query") or "").strip():
                raise ValueError("启用网络搜索时 query 不能为空")
            if "<web_search_result/>" not in self.expression:
                raise ValueError("启用网络搜索时 expression 必须包含 <web_search_result/> 占位符")
        return self
```

3d. `ExportRuleItem`（约第 89 行）`system_prompt` 之后加：

```python
    web_search: Optional[Dict[str, Any]] = None
```

3e. `AnalysisResultItem`（约第 352 行）`reason` 之后加：

```python
    source_refs: Optional[Dict[str, Any]] = None
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_analysis_web_search.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add model/schemas.py tests/test_analysis_web_search.py
git commit -m "feat: 规则 schema 支持 web_search 配置及校验"
```

---

### Task 5: 服务层 `apply_web_search` + 管线集成

**Files:**
- Modify: `service/analysis_service.py`（新增常量+函数；`run_analysis` / `run_analysis_stream` / `test_rule_analysis_stream` 集成）
- Test: `tests/test_analysis_web_search.py`（追加）

- [ ] **Step 1: 写失败测试**

`tests/test_analysis_web_search.py` 末尾追加：

```python
# ── apply_web_search ────────────────────────────────────────

async def test_apply_web_search_disabled():
    from service.analysis_service import apply_web_search

    expr = "背景:<web_search_result/> 判断"
    out, ref = await apply_web_search(expr, None, {})
    assert out == expr
    assert ref is None

    out, ref = await apply_web_search(expr, {"enabled": False, "query": "x"}, {})
    assert out == expr
    assert ref is None


async def test_apply_web_search_replaces(monkeypatch):
    from service import analysis_service

    async def fake_search(query, *, count=None, freshness=None):
        assert count == 3
        return "[1] 搜索结果文本", [{"name": "搜索结果文本", "url": "https://a.com"}]

    monkeypatch.setattr(analysis_service, "bocha_web_search", fake_search)

    ws = {"enabled": True, "query": "<field_result>company</field_result> 行政处罚", "count": 3}
    out, ref = await analysis_service.apply_web_search(
        "背景:<web_search_result/>\n判断万科是否被处罚", ws, {"company": "万科"}
    )
    assert "[1] 搜索结果文本" in out
    assert "<web_search_result/>" not in out
    assert ref["query"] == "万科 行政处罚"
    assert ref["results"][0]["url"] == "https://a.com"


async def test_apply_web_search_failure_not_fatal(monkeypatch):
    from service import analysis_service

    async def fake_search(query, *, count=None, freshness=None):
        raise RuntimeError("接口超时")

    monkeypatch.setattr(analysis_service, "bocha_web_search", fake_search)

    ws = {"enabled": True, "query": "万科 处罚"}
    out, ref = await analysis_service.apply_web_search("背景:<web_search_result/>", ws, {})
    assert "网络搜索失败" in out
    assert ref["error"] == "接口超时"
    assert ref["results"] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_analysis_web_search.py -v`
Expected: 新增用例 FAIL，`ImportError: cannot import name 'apply_web_search'`

- [ ] **Step 3: 实现 `apply_web_search`**

`service/analysis_service.py`：

3a. 导入区（`from utils.llm_client import chat_completion` 之后）加：

```python
from utils.web_search import bocha_web_search
```

3b. `resolve_expression` 函数定义之前加常量与函数：

```python
WEB_SEARCH_PLACEHOLDER = "<web_search_result/>"


async def apply_web_search(
    expression: str,
    web_search: Optional[dict],
    field_values: Dict[str, str],
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """若规则启用网络搜索，执行博查搜索并替换表达式中的占位符。

    Args:
        expression: 已完成 <field_result> 替换的表达式。
        web_search: 规则的 web_search 配置（{"enabled", "query", "count", "freshness"}）。
        field_values: {field_id: extracted_value}，用于解析搜索词中的占位符。

    Returns:
        (替换占位符后的表达式, _web_search 溯源数据或 None) 元组。
        搜索失败时占位符替换为失败提示并继续（溯源数据带 error 键），不抛异常。
    """
    if not web_search or not web_search.get("enabled"):
        return expression, None

    query = resolve_expression(web_search.get("query", ""), field_values).strip()
    try:
        formatted, results = await bocha_web_search(
            query,
            count=web_search.get("count"),
            freshness=web_search.get("freshness"),
        )
        ws_ref: Dict[str, Any] = {"query": query, "results": results}
    except Exception as e:
        logger.warning("网络搜索失败: query={}, error={}", query, e)
        formatted = f"（网络搜索失败: {e}）"
        ws_ref = {"query": query, "results": [], "error": str(e)}

    return expression.replace(WEB_SEARCH_PLACEHOLDER, formatted), ws_ref
```

- [ ] **Step 4: 集成 `run_analysis`**

`run_analysis` 中（原第 366-373 行）：

```python
            # 解析表达式
            resolved_expression = resolve_expression(rule.expression, field_values)

            # 根据规则类型执行
            if rule.rule_type == "judge":
                # 网络搜索：替换 <web_search_result/> 占位符并记录溯源
                resolved_expression, ws_ref = await apply_web_search(
                    resolved_expression, rule.web_search, field_values
                )
                if ws_ref:
                    source_refs["_web_search"] = ws_ref
                result_value, reason = await execute_judge(resolved_expression, system_prompt=rule.system_prompt or "")
            elif rule.rule_type == "calc":
                result_value, reason = await execute_calc(resolved_expression, cfg.calc_precision)
```

（即在 `if rule.rule_type == "judge":` 分支内、`execute_judge` 调用前插入 4 行。）

- [ ] **Step 5: 集成 `run_analysis_stream`**

`run_analysis_stream` 中（原第 610-617 行）做完全相同的修改：

```python
            # 解析表达式
            resolved_expression = resolve_expression(rule.expression, field_values)

            # 根据规则类型执行
            if rule.rule_type == "judge":
                # 网络搜索：替换 <web_search_result/> 占位符并记录溯源
                resolved_expression, ws_ref = await apply_web_search(
                    resolved_expression, rule.web_search, field_values
                )
                if ws_ref:
                    source_refs["_web_search"] = ws_ref
                result_value, reason = await execute_judge(resolved_expression, system_prompt=rule.system_prompt or "")
            elif rule.rule_type == "calc":
                result_value, reason = await execute_calc(resolved_expression, cfg.calc_precision)
```

- [ ] **Step 6: 集成 `test_rule_analysis_stream`（调试流）**

6a. 签名加参数（`system_prompt: str,` 之后）：

```python
async def test_rule_analysis_stream(
    file_id: str,
    rule_type: str,
    expression: str,
    depend_fields: List[str],
    system_prompt: str,
    session: AsyncSession,
    web_search: Optional[dict] = None,
) -> AsyncIterator[Dict[str, Any]]:
```

6b. docstring 事件序列说明更新为：

```python
    """单条规则调试流式接口，分步 yield 各阶段结果。

    Judge 类型事件序列：input_values → resolved_expression → [web_search] → prompt → llm_response → result → done
    Calc 类型事件序列：input_values → resolved_expression → result → done
```

6c. `# ── Judge 类型：LLM 调用 ──` 分支开头（`if rule_type == "judge":` 之后、`# Step 3: 组装 prompt` 之前）插入：

```python
    # ── Judge 类型：LLM 调用 ──
    if rule_type == "judge":
        # Step 2.5: 网络搜索（启用时）
        if web_search and web_search.get("enabled"):
            resolved, ws_ref = await apply_web_search(resolved, web_search, field_values)
            yield {"event": "web_search", "data": ws_ref or {}}
```

- [ ] **Step 7: 运行确认通过**

Run: `uv run pytest tests/test_analysis_web_search.py tests/test_analysis_service.py -v`
Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
git add service/analysis_service.py tests/test_analysis_web_search.py
git commit -m "feat: 逻辑分析 judge 规则接入网络搜索(apply_web_search)"
```

---

### Task 6: 路由透传（analysis_router + file_router）

**Files:**
- Modify: `blue_print/analysis_router.py`（list/upsert/test/test-stream）
- Modify: `blue_print/file_router.py:563-588`（analysis 结果带 source_refs）
- Test: `tests/test_analysis_web_search.py`（追加 API 测试）

- [ ] **Step 1: 写失败测试**

`tests/test_analysis_web_search.py` 顶部导入区补：

```python
from httpx import AsyncClient
```

文件末尾追加：

```python
# ── API 透传 ────────────────────────────────────────────────

_API_RULE = {
    "rule_id": "ws_api_test_rule",
    "rule_name": "搜索透传测试",
    "rule_type": "judge",
    "expression": "结合<web_search_result/>判断<field_result>a</field_result>",
    "depend_fields": ["a"],
    "web_search": {"enabled": True, "query": "<field_result>a</field_result> 资讯", "count": 3, "freshness": "oneYear"},
}


@pytest.mark.anyio
async def test_upsert_and_list_rule_with_web_search(client: AsyncClient):
    """upsert 透传 web_search 并能在列表读回。"""
    resp = await client.post("/analysis/rules", json=_API_RULE)
    assert resp.status_code == 200, resp.text

    try:
        resp = await client.get("/analysis/rules")
        rules = resp.json()["data"]
        rule = next(r for r in rules if r["rule_id"] == "ws_api_test_rule")
        assert rule["web_search"]["enabled"] is True
        assert rule["web_search"]["count"] == 3

        # 更新为关闭
        updated = dict(_API_RULE, web_search={"enabled": False})
        resp = await client.post("/analysis/rules", json=updated)
        assert resp.status_code == 200

        resp = await client.get("/analysis/rules")
        rule = next(r for r in resp.json()["data"] if r["rule_id"] == "ws_api_test_rule")
        assert rule["web_search"] == {"enabled": False}
    finally:
        await client.delete("/analysis/rules/ws_api_test_rule")


@pytest.mark.anyio
async def test_upsert_rule_web_search_validation(client: AsyncClient):
    """开启搜索但缺占位符 → 422。"""
    bad = dict(_API_RULE, rule_id="ws_bad_rule", expression="判断<field_result>a</field_result>")
    resp = await client.post("/analysis/rules", json=bad)
    assert resp.status_code == 422
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_analysis_web_search.py -v -k api`
Expected: `test_upsert_and_list_rule_with_web_search` FAIL（列表读回的 `web_search` 为 None——路由未透传）；`test_upsert_rule_web_search_validation` 可能已 PASS（schema 校验在 Task 4 已生效）

- [ ] **Step 3: 修改 `blue_print/analysis_router.py`**

3a. `list_rules` 的 `AnalysisRuleResponse(...)` 构造中 `depend_fields=r.depend_fields,` 之后加：

```python
                depend_fields=r.depend_fields,
                web_search=r.web_search,
```

3b. `upsert_rule` 更新分支 `existing.depend_fields = rule.depend_fields` 之后加：

```python
        existing.depend_fields = rule.depend_fields
        existing.web_search = rule.web_search
```

新增分支 `AnalysisRule(...)` 构造中 `depend_fields=rule.depend_fields,` 之后加：

```python
            depend_fields=rule.depend_fields,
            web_search=rule.web_search,
```

3c. `/test` 接口：导入行补 `apply_web_search`：

```python
from service.analysis_service import apply_web_search, execute_calc, execute_judge, resolve_expression, test_rule_analysis_stream
```

模式 1 分支（`depend_fields = rule.depend_fields or []` 之后）加：

```python
        depend_fields = rule.depend_fields or []
        web_search = rule.web_search
```

模式 2 分支（`depend_fields = config.get("depend_fields", [])` 之后）加：

```python
        depend_fields = config.get("depend_fields", [])
        web_search = config.get("web_search")
```

执行段 `if rule_type == "judge":` 分支改为：

```python
        if rule_type == "judge":
            expression_resolved, _ws_ref = await apply_web_search(
                expression_resolved, web_search, field_values
            )
            result_value, reason = await execute_judge(expression_resolved, system_prompt=system_prompt)
```

3d. `/test/stream` 接口：两个模式分支同样补 `web_search = rule.web_search` / `web_search = config.get("web_search")`，然后调用处改为：

```python
    async def event_generator():
        async for item in test_rule_analysis_stream(
            file_id, rule_type, expression, depend_fields, system_prompt, db,
            web_search=web_search,
        ):
            yield _sse_event(item["event"], item["data"])
```

- [ ] **Step 4: 修改 `blue_print/file_router.py`**

`get_analysis_results` 的 `AnalysisResultItem(...)` 构造中 `reason=r.reason,` 之后加：

```python
                reason=r.reason,
                source_refs=r.source_refs,
```

- [ ] **Step 5: 运行确认通过**

Run: `uv run pytest tests/test_analysis_web_search.py tests/test_analysis_router.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add blue_print/analysis_router.py blue_print/file_router.py tests/test_analysis_web_search.py
git commit -m "feat: 规则 API 与分析结果透出 web_search/source_refs"
```

---

### Task 7: 类型复制 / 导出 / 导入携带 web_search

**Files:**
- Modify: `blue_print/doctype_router.py`（copy_from 约 559-567 行、export 约 650-660 行、import 约 800-808 行三处 `AnalysisRule` / `ExportRuleItem` 构造）

- [ ] **Step 1: copy_from 透传**

`copy_from` 中 `new_rule = AnalysisRule(...)` 构造（约第 559 行起），`system_prompt=src.system_prompt,` 之后加：

```python
            system_prompt=src.system_prompt,
            web_search=src.web_search,
```

- [ ] **Step 2: export 透传**

export 中 `ExportRuleItem(...)` 构造（约第 650 行起），`system_prompt=r.system_prompt,` 之后加：

```python
                system_prompt=r.system_prompt,
                web_search=r.web_search,
```

- [ ] **Step 3: import 透传**

import 中 `new_rule = AnalysisRule(...)` 构造（约第 800 行起），`system_prompt=src.system_prompt,` 之后加：

```python
            system_prompt=src.system_prompt,
            web_search=src.web_search,
```

- [ ] **Step 4: 运行回归**

Run: `uv run pytest tests/test_doctype_management.py -v`
Expected: PASS（存量复制/导入导出回归不受影响）

- [ ] **Step 5: Commit**

```bash
git add blue_print/doctype_router.py
git commit -m "feat: 类型复制/导出/导入携带规则 web_search 配置"
```

---

### Task 8: UI — 规则表单 + 调试面板 + 详情页展示

**Files:**
- Modify: `ui/js/ruleConfig.js`（规则表单、校验、收集、调试面板）
- Modify: `ui/js/app.js`（analysis 卡片渲染 `_web_search`）

**注意：** 本任务为纯前端，无自动化测试；完成后用浏览器手测（Step 7）。

- [ ] **Step 1: `buildRuleForm` 加网络搜索区块**

`ui/js/ruleConfig.js` `buildRuleForm` 中，判断型配置区 `<div id="fm-judge-section">` 内、用户提示词 form-group 结束之后（`</div>` 闭合 judge expression 的 form-group 后、`</div>` 闭合 `fm-judge-section` 前）插入：

```html
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">网络搜索</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="fm-ws-enabled" ${ws.enabled ? 'checked' : ''} onchange="RuleConfig.onWebSearchToggle(this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="form-hint">开启后执行判断前先联网搜索（博查），结果替换提示词中的 &lt;web_search_result/&gt; 占位符</div>
                </div>
                <div id="fm-ws-config" style="display:${ws.enabled ? 'block' : 'none'}">
                    <div class="form-group">
                        <div class="form-label-row">
                            <label class="form-label">搜索词</label>
                            <div class="insert-tag-wrap">
                                <button type="button" class="insert-tag-btn" onclick="RuleConfig.showInsertTagDropdown('fm-ws-query','field_result',this)" title="插入占位符">{x}</button>
                            </div>
                        </div>
                        <textarea class="form-textarea" id="fm-ws-query" rows="2" placeholder="可用 <field_result>字段ID</field_result> 拼接依赖字段的提取值">${Utils.escapeHtml(ws.query || '')}</textarea>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">返回条数</label>
                            <input class="form-input" id="fm-ws-count" type="number" value="${ws.count ?? 5}" min="1" max="50">
                        </div>
                        <div class="form-group">
                            <label class="form-label">时间范围</label>
                            <select class="form-select" id="fm-ws-freshness">
                                <option value="noLimit" ${(ws.freshness || 'noLimit') === 'noLimit' ? 'selected' : ''}>不限</option>
                                <option value="oneDay" ${ws.freshness === 'oneDay' ? 'selected' : ''}>一天内</option>
                                <option value="oneWeek" ${ws.freshness === 'oneWeek' ? 'selected' : ''}>一周内</option>
                                <option value="oneMonth" ${ws.freshness === 'oneMonth' ? 'selected' : ''}>一月内</option>
                                <option value="oneYear" ${ws.freshness === 'oneYear' ? 'selected' : ''}>一年内</option>
                            </select>
                        </div>
                    </div>
                </div>
```

并在 `buildRuleForm` 开头变量区（`const dependFields = ...` 之后）加：

```javascript
        const ws = rule.web_search || {};
```

- [ ] **Step 2: 开关切换逻辑 `onWebSearchToggle`**

`onRuleTypeChange` 方法之后新增：

```javascript
    onWebSearchToggle(checked) {
        const area = document.getElementById('fm-ws-config');
        if (area) area.style.display = checked ? 'block' : 'none';

        // 开启时若用户提示词缺少占位符，自动追加
        if (checked) {
            const expr = document.getElementById('fm-expression');
            if (expr && !expr.value.includes('<web_search_result/>')) {
                expr.value = expr.value ? expr.value + '\n<web_search_result/>' : '<web_search_result/>';
            }
        }
    },
```

- [ ] **Step 3: 占位符下拉支持搜索词输入框**

`showInsertTagDropdown` 中标签收集分支（`else if (textareaId === 'fm-expression' || textareaId === 'fm-expression-calc')`）改为：

```javascript
        } else if (textareaId === 'fm-expression' || textareaId === 'fm-expression-calc' || textareaId === 'fm-ws-query') {
            const raw = (document.getElementById('fm-depend-fields') || {}).value || '';
            labels = raw.split(/[,，]/).map(s => s.trim()).filter(Boolean);
        }
```

- [ ] **Step 4: 收集与校验**

4a. `collectRuleFormData` 的 return 之前加收集逻辑，return 对象加 `web_search` 键：

```javascript
        // 网络搜索配置（仅 judge）
        let webSearch = null;
        const wsEnabledEl = document.getElementById('fm-ws-enabled');
        if (ruleType === 'judge' && wsEnabledEl && wsEnabledEl.checked) {
            webSearch = {
                enabled: true,
                query: (document.getElementById('fm-ws-query') || {}).value?.trim() || '',
                count: parseInt((document.getElementById('fm-ws-count') || {}).value) || 5,
                freshness: (document.getElementById('fm-ws-freshness') || {}).value || 'noLimit',
            };
        }

        return {
            rule_id: document.getElementById('fm-rule-id').value.trim(),
            rule_name: document.getElementById('fm-rule-name').value.trim(),
            rule_type: ruleType,
            expression: expression,
            system_prompt: ruleType === 'judge'
                ? (document.getElementById('fm-system-prompt').value.trim() || null)
                : null,
            depend_fields: dependFieldsStr ? dependFieldsStr.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [],
            web_search: webSearch,
            enabled: existingRule ? existingRule.enabled : 1,
            priority: parseInt(document.getElementById('fm-rule-priority').value) || 0,
        };
```

4b. `validateRuleForm` 末尾 `return true;` 之前加：

```javascript
        if (data.web_search && data.web_search.enabled) {
            if (!data.web_search.query) {
                Toast.error('开启网络搜索时搜索词不能为空');
                return false;
            }
            if (!data.expression.includes('<web_search_result/>')) {
                Toast.error('开启网络搜索时用户提示词须包含 <web_search_result/> 占位符');
                return false;
            }
        }
```

- [ ] **Step 5: 调试面板渲染 `web_search` 事件**

5a. `buildRuleDebugPanel` 中「表达式解析」section 之后、「LLM 提示词」section 之前插入：

```html
                <div class="debug-section" id="debug-sec-web-search" style="display:none;">
                    <div class="debug-section-header">网络搜索</div>
                    <div class="debug-section-body" id="debug-web-search-content"></div>
                </div>
```

5b. `handleRuleDebugEvent` 的 switch 中 `case 'resolved_expression':` 之后加：

```javascript
            case 'web_search':
                this._hideDebugLoading();
                this._showDebugLoading('正在调用 LLM...');
                this.renderRuleWebSearch(data);
                break;
```

5c. `renderRuleResolvedExpression` 之后新增渲染方法：

```javascript
    renderRuleWebSearch(data) {
        const section = document.getElementById('debug-sec-web-search');
        const container = document.getElementById('debug-web-search-content');
        if (!section || !container) return;

        let html = `<div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">搜索词: ${Utils.escapeHtml(data.query || '')}</div>`;
        if (data.error) {
            html += `<div style="color: #e74c3c; font-size: 12px;">搜索失败: ${Utils.escapeHtml(data.error)}</div>`;
        } else {
            const results = data.results || [];
            if (results.length === 0) {
                html += '<div style="color: var(--text-secondary); font-size: 13px;">无搜索结果</div>';
            }
            results.forEach((r, i) => {
                const date = (r.datePublished || '').slice(0, 10);
                const meta = [r.siteName, date].filter(Boolean).join(' · ');
                html += `
                    <div class="debug-result-group">
                        <div class="debug-result-group-header">[${i + 1}] ${Utils.escapeHtml(r.name || '')}${meta ? ` <span style="font-weight: normal; color: var(--text-secondary);">${Utils.escapeHtml(meta)}</span>` : ''}</div>
                        <div class="debug-result-item-content" style="font-size: 12px;">${Utils.escapeHtml(r.summary || '')}</div>
                    </div>
                `;
            });
        }
        container.innerHTML = html;
        section.style.display = '';
    },
```

5d. `resetRuleDebugResults` 的 id 数组加入 `'debug-sec-web-search'`：

```javascript
        ['debug-sec-input-values', 'debug-sec-resolved', 'debug-sec-web-search', 'debug-sec-prompt', 'debug-sec-llm', 'debug-sec-result'].forEach(id => {
```

- [ ] **Step 6: 详情页 analysis 卡片展示搜索来源**

`ui/js/app.js`：

6a. `case 'analysis':` 渲染中 reason 区块之后（`` ${item.reason ? `...` : ''} `` 之后、`</div>` 闭合 data-card 之前）加：

```javascript
                                    ${this.renderWebSearchRefs(item.source_refs)}
```

6b. `renderSourceRefs` 方法之后新增：

```javascript
    // 渲染分析结果 source_refs._web_search 的「网络搜索」折叠区块（无搜索数据返回空串）
    renderWebSearchRefs(sourceRefs) {
        if (!sourceRefs || typeof sourceRefs !== 'object') return '';
        const ws = sourceRefs._web_search;
        if (!ws || !ws.query) return '';
        const results = ws.results || [];
        let inner = `
            <div class="source-ref-seg">
                <div class="source-ref-meta">搜索词${ws.error ? ' · 搜索失败' : ''}</div>
                <div class="source-ref-text">${this.escapeHtml(ws.query)}</div>
            </div>
        `;
        results.forEach((r, i) => {
            const date = (r.datePublished || '').slice(0, 10);
            const meta = [`[${i + 1}] ${r.name || ''}`, r.siteName || '', date]
                .filter(Boolean).map(m => this.escapeHtml(m)).join(' · ');
            inner += `
                <div class="source-ref-seg">
                    <div class="source-ref-meta">${meta}</div>
                    <div class="source-ref-text">${this.escapeHtml(r.summary || '')}</div>
                </div>
            `;
        });
        return `
            <details class="source-refs">
                <summary>网络搜索（${results.length} 条）</summary>
                ${inner}
            </details>
        `;
    },
```

- [ ] **Step 7: 手测**

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 5019
```

浏览器打开 `http://localhost:5019/ui`，验证：
1. 配置中心 → 规则配置 → 新增规则（judge）：出现「网络搜索」开关；打开后显示搜索词/条数/时间范围；表达式自动追加 `<web_search_result/>`
2. 搜索词 `{x}` 下拉可插入依赖字段占位符
3. 关闭开关保存 → 规则不带搜索；打开但清空搜索词保存 → Toast 报错
4. 调试模式选已完成文件点「测试」→ 依赖字段值 → 表达式解析 → 网络搜索（真实调博查）→ LLM 提示词（含搜索文本）→ 结果
5. 切到 calc 类型：无网络搜索区块
6. 跑完整分析后文件详情 → 分析结果卡片出现「网络搜索（N 条）」折叠块

- [ ] **Step 8: Commit**

```bash
git add ui/js/ruleConfig.js ui/js/app.js
git commit -m "feat: 规则弹窗网络搜索配置/调试面板/详情页搜索来源展示"
```

---

### Task 9: 文档同步（CLAUDE.md + openapi.json）

**Files:**
- Modify: `CLAUDE.md`（Analysis System 段 + Configuration 段）
- Modify: `docs/openapi.json`（重新生成）

- [ ] **Step 1: 更新 CLAUDE.md**

`### Analysis System (service/analysis_service.py)` 段落，在两种 rule type 列表之后追加一段：

```markdown
- **judge 网络搜索**：规则可配置 `web_search` JSON（`{"enabled", "query", "count", "freshness"}`，仅 judge 类型）。启用时执行判断前先调博查 Bocha AI 搜索（`utils/web_search.py`），搜索词支持 `<field_result>field_id</field_result>` 占位符拼接提取结果，搜索文本替换 expression 中的 `<web_search_result/>` 占位符（schema 层强制要求存在）。搜索失败不致命（占位符替换为失败提示继续判断）。溯源数据存 `source_refs._web_search`（`{query, results: [{name,url,siteName,datePublished,summary}], error?}`），`GET /file/{id}/analysis` 与回调 `rule_done` 透出。调试流新增 `web_search` 事件。全局参数在 `configs/config.yaml` 的 `web_search` 节。
```

Configuration 段的 Key sections 列表中 `vl_model` 之后加 `web_search`。

- [ ] **Step 2: 重新生成 openapi.json**

```bash
uv run python -c "
import json
from app import app
spec = app.openapi()
with open('docs/openapi.json', 'w', encoding='utf-8') as f:
    json.dump(spec, f, ensure_ascii=False, indent=2)
print('ok')
"
```

Expected: 输出 `ok`；`git diff docs/openapi.json` 可见 `AnalysisRuleCreate`/`AnalysisRuleResponse` 新增 `web_search`、`AnalysisResultItem` 新增 `source_refs`。若 diff 中出现大面积无关格式变动（说明存量文件非该命令生成），改为手工在 `docs/openapi.json` 的上述三个 schema 中按现有格式补字段。

- [ ] **Step 3: 全量回归**

Run: `uv run pytest`
Expected: 全部 PASS（存量用例无回归）

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/openapi.json
git commit -m "docs: 同步逻辑分析网络搜索功能文档与 openapi"
```

---

## 自检记录

- **覆盖核对**：开关（web_search.enabled）✓、搜索词拼接依赖字段（query 占位符 + resolve_expression）✓、结果拼接提示词（`<web_search_result/>` 替换）✓、配置进 config.yaml ✓、三条执行路径 + 调试 ✓、溯源/回调/UI ✓、复制/导入导出 ✓
- **类型一致性**：`apply_web_search(expression, web_search, field_values) -> Tuple[str, Optional[Dict]]` 在 Task 5（定义）、Task 6（路由调用）一致；`bocha_web_search(query, *, count, freshness) -> Tuple[str, List[Dict]]` 在 Task 2（定义）、Task 5（mock 签名）一致；占位符字面量 `<web_search_result/>` 全计划统一
- **无占位符**：所有步骤含完整代码/命令
