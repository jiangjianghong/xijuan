# 混合检索实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为字段提取增加可展开、可拖拽排序的文本/表格混合检索，并支持在 fallback 链末尾使用 VL 直接提取，同时保证旧配置无需迁移即可继续运行。

**Architecture:** 新增 `search_type="hybrid"`；`search_config` 保存 `strategy` 和有序 `items`。每项包含稳定 ID、来源类型、方法和原有配置。提取服务逐项执行现有检索器，union 去重并格式化证据，fallback 按顺序短路；VL 只在 fallback 中直接返回四元组结果。

**Tech Stack:** FastAPI、Pydantic v2、SQLAlchemy JSON、async Python、现有 table/extraction/VL services、原生 JavaScript/HTML/CSS、pytest-asyncio。

## Global Constraints

- 旧单路 `search_type`、`search_config`、`table_*`、`vl_*` 和原提示词行为保持不变。
- 混合文本/表格证据只替换 `<search_result>混合检索结果</search_result>`。
- union 仅允许 text 与 table；fallback 允许 text、table 和最多一个 vl。
- items 顺序决定 fallback 优先级、union 合并顺序和去重优先级。
- 不新增数据库列、不批量迁移存量字段；导入导出保留旧键。

### Task 1: 配置模型与校验

**Files:** `model/schemas.py`; `tests/test_hybrid_search_schema.py`

- [ ] 写失败测试：合法 union/fallback、union 含 VL 失败、重复 VL 失败、非法 strategy/空 items/缺 ID 失败、旧配置通过。
- [ ] 运行 `uv run pytest tests/test_hybrid_search_schema.py -v` 确认失败。
- [ ] 增加 hybrid 枚举和 validator；按 source_type 校验 text/table/vl 专属配置，不改变旧分支。
- [ ] 重跑测试并提交 `feat: validate hybrid extraction config`。

### Task 2: union 执行与证据合并

**Files:** `service/extraction_service.py`; 必要时 `service/table_service.py`; `tests/test_hybrid_extraction.py`

**Interfaces:** `extract_hybrid_field(file_id, field, snapshot) -> tuple[str,str,Optional[dict],list[int]]`；内部提供 item 分派和合并函数。

- [ ] 写测试：text+table 按 item 顺序合并；相同 chunk_id/位置只保留第一项；表格证据含表名、页码、内容；只替换一次固定占位符。
- [ ] 运行 `uv run pytest tests/test_hybrid_extraction.py -k union -v`。
- [ ] 分派 text 到现有六种检索路径，table 复用现有匹配/提取逻辑；保留 source_refs 与页码。
- [ ] 按 chunk_id、位置、内容哈希依次去重，应用总长度限制，调用一次文本 LLM。
- [ ] 测试通过后提交 `feat: merge hybrid text and table evidence`。

### Task 3: fallback 与 VL 兜底

**Files:** `service/extraction_service.py`; `tests/test_hybrid_extraction.py`

- [ ] 写测试：空 text 后执行 table；可恢复异常继续；table 命中时不调用 VL；前序为空时 VL 返回 value/reason/source_refs/pages；union 含 VL 运行时拒绝。
- [ ] 运行 `uv run pytest tests/test_hybrid_extraction.py -k fallback -v`。
- [ ] 在字段主流程识别 hybrid；首个有效 text/table 证据调用一次文本 LLM后停止。
- [ ] VL 调用现有 `extract_vl_field`，直接返回结果，不走占位符或文本 LLM；记录每项耗时、命中和错误。
- [ ] 运行 focused tests 与 `uv run pytest tests/test_extraction_service.py -v`，提交 `feat: add hybrid fallback including vl`。

### Task 4: API、导入导出与调试兼容

**Files:** 实际字段 CRUD 路由（通常为 `blue_print/extraction_router.py`）；`service/extraction_service.py` 调试分支；`tests/test_extraction_field_router.py`。

- [ ] 测试创建/更新/读取后 items 顺序和 ID 不变，导出导入保持配置，旧字段响应不变。
- [ ] 运行 focused API 测试确认失败。
- [ ] 更新序列化、导入导出和调试重提取，使 hybrid 使用同一快照。
- [ ] 运行 `uv run pytest tests/test_extraction_field_router.py -v`，提交 `feat: persist hybrid extraction fields`。

### Task 5: 前端扩展面板与排序

**Files:** `ui/js/schemaBuilder.js` 及字段编辑器实际引用的 HTML/CSS/JS 文件；现有前端测试位置。

- [ ] 检查并记录字段编辑器入口和现有状态保存函数。
- [ ] 实现组合检索开关；开启时旧配置成为 items[0]，关闭时恢复旧序列化。
- [ ] 实现 strategy、text/table/VL 专属编辑器、添加/复制/删除、固定占位符复制按钮。
- [ ] 使用稳定 ID 渲染拖拽手柄；拖拽、上移、下移都更新数组；union 禁用 VL，fallback 限制一个 VL并提示置底。
- [ ] 运行 JS 语法检查和浏览器手工清单，提交 `feat: add hybrid search editor`。

### Task 6: 全量验证

**Files:** 上述测试文件；必要时配置/API 文档。

- [ ] 运行 `uv run pytest tests/test_hybrid_search_schema.py tests/test_hybrid_extraction.py tests/test_extraction_field_router.py -v`。
- [ ] 运行完整 `uv run pytest` 并记录环境限制。
- [ ] 检查旧 text/table/VL、统一占位符、排序持久化、union/fallback 约束和调试信息。
- [ ] 必要时更新示例并提交 `docs: document hybrid extraction search`。
