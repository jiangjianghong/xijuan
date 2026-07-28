# 进阶字段提取（字段间引用 + 页码联动）实现审查报告

审查对象：`docs/superpowers/plans/2026-07-23-advanced-extraction-fields.md` 的落地实现
审查范围：`e83b781..253e75b`（14 个提交）+ 工作区未提交改动
审查日期：2026-07-27

## 0. 验证过的事实（先摆结论依据）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 新增单测 | `uv run pytest tests/test_advanced_extraction.py -q` | 17 passed |
| 新增 API 测 | `uv run pytest tests/test_advanced_extraction_api.py -q` | 6 passed |
| 回归（抽取/页码/路由/类型管理/溯源） | `uv run pytest tests/test_extraction_service.py tests/test_extraction_page.py tests/test_extraction_router.py tests/test_doctype_management.py tests/test_source_refs_text.py -q` | 107 passed |
| 文档一致性 | `uv run python scripts/check_docs_sync.py` | docs sync OK |

整体判断：**主链路实现正确、与 plan 高度吻合，测试全绿**。但存在 1 个会污染数据的真 bug、3 个功能缺口（其中 1 个让 Task 9 在 UI 上等同死代码），以及一批边界一致性问题。下面按严重度排列。

---

## 1. 严重问题

### 1.1 【P0·真 bug】进阶字段「什么都没抽到」会被记成**成功**

**位置：** `service/extraction_service.py:1775-1801`（`_extract_field_result`）配合 `:174-187`（`_is_extraction_success` / `_ensure_valid_extraction_result`）

```python
# _extract_field_result 结尾
if provenance:
    source_refs = source_refs or {}     # ← None 被替换成 {}
    source_refs.update(provenance)      # ← 塞入 _resolved_refs / _page_link
return value, reason, source_refs
```

```python
def _is_extraction_success(value, source_refs) -> bool:
    return bool(source_refs) or bool(str(value or "").strip())
```

只要进阶字段解析出了**任何一个引用**（`_resolved_refs` 非空），`source_refs` 就一定非空，
`_is_extraction_success` 恒为 `True`，`_ensure_valid_extraction_result` 永远不抛。

**实测复现**（已在本机跑通）：

```
prov = {'_resolved_refs': {'a': '华为'}}
merged source_refs = {'_resolved_refs': {'a': '华为'}}
_is_extraction_success('', merged) = True     # 进阶字段
_is_extraction_success('', None)   = False    # 同样情况的普通字段
```

**触发路径（都是常见场景）：**
- 被引用字段抽空 → 关键词被 `_clean_str_list` 剔空 → 检索无命中 → `extract_text_field` 返回 `("", "", None)`；
- 表格匹配不到任何表 → `extract_table_field` 返回 `("", "", None)`；
- `page` 检索 `page_range` 非法 → `_extract_page_field` 返回 `("", reason, None)`。

**后果：** `extraction_result` 落一条 `extracted_value=""`、`source_refs={"_resolved_refs": {...}}` 的记录，
`success=True`、计入 `succeeded`、回调 `field_done.success=true`、前端显示"已提取"。
**普通字段在完全相同的情况下是失败**——两层字段的失败语义不一致，且不一致的方向是"静默变成功"。

**建议方向：** 在 `_extract_field_result` 里先做成功判定再合并 provenance；或让
`_is_extraction_success` 忽略 `_NON_REF_KEYS` 里的元数据键（`bool(source_refs)` 改成"存在至少一个真实 ref 分组"）。
后者更彻底，同时能修掉 `_model_pages`/`_vl` 单独存在时的同类问题。

---

## 2. 功能缺口（偏离 plan 意图 / plan 未覆盖但必要）

### 2.1 【P1】Task 9 的调试流进阶分支在 UI 上**永远不会触发**

- `ui/js/ruleConfig.js:2159-2162`（`runFieldTest`）只发 `{file_id, config: formData}`，**从不发 `field_id`**；
- `blue_print/extraction_router.py:362-383` 用 `config` 构造临时 `ExtractionField` 时，**没有传 `is_advanced`**（也没传 `depend_fields`）；
- 因此 `test_field_extraction_stream` 里 `if getattr(field, "is_advanced", 0):` 恒为假 → 不解析引用、不推 `resolved_refs`；
- `handleDebugEvent`（`ruleConfig.js:2206`）也**没有 `resolved_refs` 分支**，事件即使推来也不渲染。

**实际表现：** 在进阶字段表单里点「测试」，检索关键词仍是 `<field_result>xxx</field_result>` 原文，
page 联动字段会用手填的 `page_range`（或直接报 `page_range 配置非法`）。调试功能对进阶字段基本无用。

**同时这让文档失真：** `docs/api/extraction.md:269`、`docs/api/sse.md` 第 2 节、
`docs/guides/extraction-config.md` §6.3 都写了"调试流会先推 `resolved_refs`"，
但只在 `field_id` 模式（外部 API 直接调）成立，UI 走不到。

**建议方向：** 临时配置里透传 `is_advanced`（`config.get("is_advanced", 0)`），
并在 `handleDebugEvent` 加 `resolved_refs` 渲染；或文档明确标注"仅 `field_id` 模式生效"。

### 2.2 【P1】非流式 `POST /extraction/test` 完全没有进阶处理

`blue_print/extraction_router.py:212-340`：`test_extraction` 从 DB 加载字段后直接调
`extract_text_field` / `extract_table_field` / `extract_vl_field`，**没有 `resolve_advanced_field`**。
即使传 `field_id` 指向一个已保存的进阶字段，也会带着占位符去检索。

plan Task 9 只点名了 stream 版本，但两个接口是孪生能力（`/test` 与 `/test/stream` 共用配置构造逻辑），
只改一个是 plan 覆盖不全导致的实现缺口，不是实现者的偏离。

### 2.3 【P1】复制/导入时进阶字段的**悬空引用被静默带过**

`blue_print/doctype_router.py:482-500`（`_remap_field_placeholders`）注释写着：

> 未在映射表中的 field_id 原样保留（与 depend_fields 缺失依赖的处理一致，**通过 missing_dependencies 提示调用方**）

规则（`AnalysisRule`）确实这么做了（`doctype_router.py:667-673` 把缺失依赖塞进 `missing_deps`），
但**进阶字段的引用没有对应的缺失上报**：

- `copy_configs` 里 `on_conflict="skip"` 跳过了被引用的普通字段，或 `field_ids` 只选了进阶字段没选它的依赖；
- `import_configs` 里源 payload 缺了被引用字段；

这两种情况下 `_remap_advanced_field_config` 会把 `<field_result>原ID</field_result>` 和
`page_source_field` 原样写进新类型 —— 指向**另一个类型的 field_id**，运行时静默解析为空串。
`CopyConfigsResponse.missing_dependencies` 里看不到任何提示。

这违背了 CLAUDE.md 里既定的复制契约（"dependencies whose fields were not copied are returned to the caller, not silently dropped"）。

**建议方向：** `_remap_advanced_field_config` 返回未命中的 ref 列表，并入 `missing_deps`
（格式可沿用 `f"{field_name}::{src_fid}"`）。

---

## 3. 一致性与完整性问题

### 3.1 【P2】保存时的不变量事后可被破坏，无引用方校验

`upsert_field` 只校验**当前保存的这个字段**的引用合法（存在 + `is_advanced=0`），但：

| 破坏动作 | 位置 | 后果 |
|---|---|---|
| 删掉被引用的普通字段 | `extraction_router.py:187-199`（`delete_field` 无任何检查） | 引用悬空，运行时静默替换为空串 |
| 把被引用的普通字段改成进阶（`is_advanced=1`） | `upsert_field` 不检查"谁在引用我" | "进阶只能引用普通"的不变量被绕过；且两个进阶字段在同一阶段跑，引用方拿不到被引用方的值 |
| 禁用被引用的普通字段（`enabled=0`） | 保存校验不看 `enabled` | 运行时该字段不在 `all_fields` 里 → 引用替换为空串，无任何日志/提示 |

三种情况都是**静默降级**，用户看到的是"进阶字段抽出来是空的"，排查成本很高。
（叠加 §1.1 的 bug，还会显示成"成功"。）

**建议方向：** `delete_field` / 改 `is_advanced` / 改 `enabled` 时反查
`extraction_field.depend_fields` 里含该 id 的同类型进阶字段，给 409 或至少 warning；
运行时 `resolve_advanced_field` 对"引用的 id 不在 `field_values` 里"记一条 `logger.warning`。

### 3.2 【P2】`table_name_pattern` 是解析盲区（table 类进阶字段会静默失效）

`table_name_pattern` 在表格抽取里同时充当**占位符 label**（`extraction_service.py:1385`：
`label = field.table_name_pattern or "表格"`），但它：

- 不被 `collect_depend_fields` 扫描（`extraction_service.py:298-341` 的属性列表里没有它）；
- 不被 `resolve_advanced_field` 解析（`:397-408` 的 overrides 里没有它）；
- 不被 `_remap_advanced_field_config` 重映射（`doctype_router.py:520-535`）。

于是若用户在 `table_name_pattern` 里写 `<field_result>x</field_result>`：
prompt 里的同名占位符**会**被替换成实际值，label **不会** → 两边对不上 →
`replace_search_result_placeholders` 走 `no_result_hint` 分支，模型收到"（未找到 'xxx' 的相关内容）"。
且 `depend_fields` 里看不到这个依赖，`upsert_field` 也不会报错。

三处保持了"都不处理"的内部一致，所以不是崩溃级问题，但对用户是无提示的陷阱。

### 3.3 【P2】引用为空时的失败诊断很差

`resolve_field_refs` 把缺失/空引用替换为空串，`_clean_str_list` 再把变空的列表项剔除。连锁反应：

- `keywords` 全被剔空 → 检索直接返回 `[]` → `("", "", None)`；
- prompt 里 `<search_result><field_result>x</field_result></search_result>` 变成 `<search_result></search_result>`，
  而 `validate_prompt_has_placeholder` 的正则是 `<search_result>.+?</search_result>`（要求至少 1 个字符）→
  判定为"无占位符" → `logger.warning` + 返回 `("", "", None)`，**`reason` 为空串**。

最终用户拿到的是 `value=""` / `reason=""`（叠加 §1.1 还是 `success=true`），完全看不出是"上游字段没抽到"。
`docs/guides/extraction-config.md` §6.1 描述了这个替换规则，但没说后果是静默空结果。

### 3.4 【P3】若干边界行为未覆盖 / 未文档化

| # | 问题 | 位置 |
|---|---|---|
| a | `search_config` 里**嵌套 dict** 内的占位符不解析（只处理 str 和 list 元素），与 `collect_depend_fields` 一致，但文档的"支持位置"表格没说明这个边界 | `extraction_service.py:368-374` |
| b | `page_source_field` 只要出现在 `search_config` 里就被 `collect_depend_fields` 记为依赖，**不管 `search_type` 是不是 `page`** → 非 page 类型残留该键会产生幻影依赖，甚至因该 id 不存在被 400 拒绝保存 | `extraction_service.py:333-336` |
| c | `_clean_str_list` 对进阶字段 `search_config` 里**所有** list 值做 strip + 去空（不只是 keywords），如 `stop_words` 里刻意留的空白项会被吃掉。普通字段无此行为 → 两层字段行为不一致 | `extraction_service.py:277-288` |
| d | 被引用字段的值若是 list/dict，`_extract_value_reason_pages` 会把它 JSON 序列化成字符串，这个 JSON 串会被原样塞进 keywords 去检索 —— 几乎必然无命中 | `extraction_service.py:69-73` + `:1897` |
| e | provenance 落库了但**前端看不到**：`ui/js/app.js:1021/1063/1096` 都用 `!Array.isArray(refs)` 过滤，`_resolved_refs` / `_page_link` 是 object → 被跳过。渲染不会出错，但"引用填了什么值、联动到哪几页"在 UI 上无从查看，plan Task 15 的验收只能靠 API | `ui/js/app.js` |

（e 的过滤是安全的——我确认了 `app.js` 四处遍历 source_refs 的地方都有 `Array.isArray` 兜底，
新键不会导致前端报错，`_NON_REF_KEYS` 也已正确追加，排序逻辑不受影响。）

---

## 4. 工程状态问题

### 4.1 【P2】Task 16 的改动**全部未提交**，且包含源码

```
 M CLAUDE.md
 M docs/api/extraction.md
 M docs/api/sse.md
 M docs/guides/extraction-config.md
 M docs/guides/source-refs.md
 M docs/openapi.json
 M docs/reference/data-model.md
 M utils/openapi_enrich.py     ← 这是源码，不是文档
 M TODO
```

`utils/openapi_enrich.py` 是运行时会被 `app.py` 加载的模块（决定活的 `/docs` Swagger 内容）。
未提交状态下部署，线上 `/docs` 会缺少 `is_advanced` / `depend_fields` / `page_source_field` 的说明，
与仓库里的 `docs/openapi.json` 不一致——正是 `check_docs_sync` 想防的那种漂移。

文档内容本身质量很好（我逐段核对过 §6 全文与实现，描述准确），唯一失真的是 §2.1 提到的调试流部分。

### 4.2 【P3】测试覆盖缺口

已有测试质量不错（`test_two_phase_advanced_uses_basic_value` 用 `calls == [["甲方"], ["华为公司"]]`
同时断言了阶段顺序和值替换，比 plan 给的版本更严）。缺的是：

| 缺口 | 说明 |
|---|---|
| `run_extraction_stream` 两阶段 | Task 8 只做了实现，无任何测试（plan 也只写"手动冒烟"） |
| page 联动的端到端 | 只有 `resolve_advanced_field` 的纯函数测试，没有"普通字段自报页码 → 进阶字段真的只读那几页"的集成测试 |
| 调试流进阶分支 | 无测试——这正是 §2.1 那个"UI 走不到"的缺口没被发现的原因 |
| **引用为空 / 上游失败的行为** | 完全没测——这正是 §1.1 那个 P0 bug 的盲区 |
| `copy_from` 依赖未复制的情形 | 无测试——§2.3 的盲区 |

### 4.3 【P3】代码风格小偏离（不影响功能）

- plan Task 6 要求在文件顶部 import `copy` 和 `sqlalchemy.inspect`，实现改成了**函数内局部 import**
  （`extraction_service.py:293`、`:355`、`doctype_router.py:489`），且 `_clone_field_transient` 里
  重复 `from model.tables import ExtractionField as _EF`——该类第 15 行已经 import 过了。
  模块缓存让这没有性能问题，但与文件其余部分的 import 风格不一致。
- `tests/test_advanced_extraction.py` 中途 import（第 38/63/86/123 行），且 `ExtractionField`
  在第 4 行和第 123 行重复 import。这是照抄 plan 里的分 Task 片段留下的，与仓库其它测试文件风格不一致。

---

## 5. Plan 逐条符合度

| Task | 状态 | 备注 |
|---|---|---|
| 1 ORM 加列 + 启动迁移 | ✅ | `TINYINT NOT NULL DEFAULT 0` + `JSON NULL`，与 plan 一致 |
| 2 schemas 加字段 | ✅ | `ExtractionFieldCreate` / `ExportFieldItem` 均加 |
| 3 `collect_field_refs` / `resolve_field_refs` | ✅ | 与 plan 代码一致 |
| 4 `derive_page_range_from_model_pages` | ✅ | 与 plan 代码一致 |
| 5 `collect_depend_fields` | ✅ | 与 plan 代码一致（盲区见 §3.2 / §3.4b） |
| 6 `resolve_advanced_field` + 透明克隆 | ✅ | 游离克隆正确，**不会**污染会话内 ORM 对象（已确认无 `session.add`、无 autoflush 风险） |
| 7 `run_extraction` 两阶段 | ⚠️ | 结构正确，但引入 §1.1 的 P0 bug |
| 8 `run_extraction_stream` 两阶段 | ✅ | 与 Task 7 同构（同样带 §1.1 bug）；无测试 |
| 9 调试流支持进阶 | ⚠️ | 后端实现了，但 UI 路径走不到（§2.1）；`/test` 非流式版遗漏（§2.2） |
| 10 `upsert_field` 校验 + `list_fields` 回传 | ✅ | 校验/回传都对；引用方保护缺失见 §3.1 |
| 11 `copy_from` 重映射 | ⚠️ | 重映射正确（含 `page_source_field`），缺失依赖不上报（§2.3） |
| 12 导出/导入 | ⚠️ | 同上；往返测试通过 |
| 13 UI 列表分区 | ✅ | 浅绿区 + 空态 + `depend_fields` 提示行，比 plan 多做了引用展示 |
| 14 UI 进阶表单 | ✅ | **比 plan 做得好**：下拉带搜索框、自动排除自身、引用 chip 绿色区分、page 提示文案联动 |
| 15 UI 数据收集 | ✅ | `is_advanced` + `page_source_field` + `max_pages` 都带上；`api.js` 整体透传无需改动 |
| 16 文档 + openapi 三步 | ⚠️ | 内容完整准确、`check_docs_sync` 全绿，但**未提交**（§4.1），且调试流部分与实际不符（§2.1） |
| 「本期不做」清单 | ✅ | 进阶引用进阶、VL 作 page 来源、自动排序等均正确地未实现 |

---

## 6. 建议处理顺序

1. **§1.1** — 唯一会污染数据的 bug，且会掩盖其它所有失败，先修。
2. **§2.1** — 调试能力对进阶字段失效，直接影响可用性；顺带修 §2.2。
3. **§4.1** — 提交未提交的改动（尤其 `utils/openapi_enrich.py`），否则部署即漂移。
4. **§2.3 / §3.1** — 悬空引用的两个来源（复制时、事后编辑时），补上报与保护。
5. **§3.2 / §3.3 / §3.4** — 边界一致性，可以只补文档说明 + 一条 warning 日志，不一定要改行为。
6. **§4.2** — 补 §1.1、§2.1、§2.3 三个盲区的回归测试，防止再犯。
