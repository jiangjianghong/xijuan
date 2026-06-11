"""生成丰富描述的 openapi.json。

用法:
    python scripts/gen_openapi.py

行为:
    1. 不启动服务，import FastAPI app -> 调用 app.openapi() 拿原生 schema
    2. 用四层丰富信息对生成的 schema 做就地增强：
       - ENRICHMENTS：每个 (path, method) 的 summary / description
       - GLOBAL_PARAM_DOCS + PARAM_OVERRIDES：每个 query/path 参数的 description / enum / example
       - SCHEMA_DOCS：每个 component schema 的整体说明、逐属性 description、请求示例
       - 顶层 info.description / servers / tags
    3. 写入 docs/openapi.json (UTF-8, 缩进 2)

设计说明:
    - 所有响应都包在 `ResponseWrapper{code,message,data}` 里，且 `response_model=ResponseWrapper`
      使得 FastAPI 不会为响应的 `data` 生成具体 schema。因此**响应负载形态写在 operation 的
      description 文本里**（组件 schema 里只有请求体模型）。
    - 描述刻意写得详尽：含参数语义、默认值、约束、副作用、错误码、`data` 字段清单。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

# 让脚本可独立运行（python scripts/gen_openapi.py），把项目根加进 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI

from blue_print import register_routers


# ─── 顶层元信息 ──────────────────────────────────────────────

API_INFO_DESCRIPTION = """
析卷 AI 的 HTTP API —— 基于 **FastAPI + MinerU + LLM**，把 PDF 经过
六阶段管线沉淀为结构化的「字段提取结果」与「逻辑分析结论」。

## 处理管线

```
parsing → tableing → chunking → embedding → extracting → analyzing → complete
```

| 阶段 | 动作 | 产物表 |
|---|---|---|
| `parsing` | MinerU 解析 PDF 为 Markdown + middle_json + 页码映射 | `file_content` |
| `tableing` | LLM 从表前文识别每张表的名称（截断 30 字） | `file_table` |
| `chunking` | 递归文本切分（表格作为独立块不拆分） | `file_chunk` |
| `embedding` | 向量化写入 Milvus（向量数据不随回调下发） | Milvus |
| `extracting` | 按字段配置用 LLM/VL 抽取 `{value, reason}` | `extraction_result` |
| `analyzing` | judge（LLM 真假判断）/ calc（numexpr 计算） | `analysis_result` |

每个阶段失败会把 `files.progress` 置为 `<stage>_failed` 并写入 `error`；进程崩溃后
启动时 `init_service` 会把残留的 `*ing` 态归一为 `*_failed` 并清理孤儿数据。

## 关键概念

- **文档类型 (`type_id`)** —— 隔离不同格式文件的字段/规则配置。每个文件归属唯一
  `type_id`（默认 `default`，不可删除）。抽取字段 `extraction_field`、逻辑规则
  `analysis_rule` 均按 `type_id` 隔离，**不跨类型共享**；共享靠显式复制 / 导入。
- **类型的血缘维度**：`is_template`（模板标记，`promote` / `demote` 切换）+ `parent_type_id`
  （复制来源，`copy_from` / `import` 自动记录）。
- **`file_id`** —— 由 `(type_id, file_name, time.time_ns(), secrets.token_hex(8))`
  取 `SHA256[:32]` 生成；**每次上传都是新 ID，不做去重**。
- **`field_id` / `rule_id`** —— **全局唯一**（不只是类型内唯一）。upsert 时若 ID 已被
  其它 `type_id` 占用，返回 **409**。
- **重试** —— 失败后用 `POST /file/{file_id}/retry/{stage}`（会清理该阶段及下游数据后
  重跑），**不要**重新上传（上传必产生新 `file_id` 与新记录）。
- **回调 (`callback_url`)** —— `POST /file/parse` / `retry`（`async` / `sync` 模式）携带时，
  管线在「每阶段开始」「每条 `field_done` / `rule_done`」「每阶段 `stage_done`」都会向该
  地址 POST 通知（超时 **2.5s**，失败仅 warning，绝不阻断主流程）。完整 payload 形态见
  `docs/API_DOCUMENTATION.md` 与 `docs/ASYNC_CALLBACK.md`。
- **SSE 流** —— `mode=stream` 与 `/extraction/test/stream` / `/analysis/test/stream`
  返回 `text/event-stream`，逐阶段/逐步推送事件。
- **VL 抽取** —— `source_type=vl` 字段绕过 MinerU Markdown，直接读 `uploads/{file_id}.pdf`，
  由视觉模型一次产出 `{value, reason}` JSON，**不再走文本 LLM 二次抽取**。

## 占位符体系

- 抽取 prompt：`<search_result>标签</search_result>` —— 渲染时替换为对应检索片段。
- 分析表达式：`<field_result>字段ID</field_result>` —— 渲染时替换为该字段的提取值。

## 响应约定

除 SSE 与 `multipart` 上传外，所有接口统一返回
`ResponseWrapper{code:int=200, message:str, data:any}`。**业务负载在 `data` 里**；因为
`data` 是动态类型，其具体形态写在每个接口的描述中，而非组件 schema。

`/doctype/list` 的分页是唯一的形态切换：**传齐 `page` + `page_size` 返回
`{items, total}`，否则原样返回数组**（向后兼容旧调用方）。
""".strip()


TAGS = [
    {
        "name": "doctype",
        "description": (
            "文档类型管理：CRUD、跨类型复制、导出/导入、模板血缘（promote/demote）、批量删除。"
        ),
    },
    {"name": "file", "description": "文件解析（async/sync/stream）、进度查询、删除、按阶段重试、各阶段结果获取。"},
    {"name": "extraction", "description": "字段提取配置（table / text / vl 三类源）CRUD 与同步/流式调试。"},
    {"name": "analysis", "description": "逻辑分析规则（judge / calc 两类）CRUD 与同步/流式调试。"},
    {"name": "search", "description": "Milvus 向量相似度检索（L2 距离）。"},
]


# ─── 每个 (path, method) 的丰富信息 ──────────────────────────
# summary 简短；description 详细，覆盖参数语义、默认值、副作用、错误码、data 形态。

ENRICHMENTS: Dict[str, Dict[str, Dict[str, Any]]] = {
    # ─── doctype ───────────────────────────────────────────
    "/doctype/list": {
        "get": {
            "summary": "列出文档类型（可搜索/过滤/分页）",
            "description": (
                "列出文档类型，每项附 `file_count` / `field_count` / `rule_count` 计数。\n\n"
                "**返回形态（重要）**\n"
                "- 传齐 `page` + `page_size` → `data = {items: [...], total: <int>}`（分页）\n"
                "- 否则 → `data = [...]`（数组，向后兼容；不传任何参数即旧的全量行为）\n\n"
                "**过滤**\n"
                "- `q`：对 `type_id` / `type_name` 做 `LIKE %q%` 模糊匹配\n"
                "- `scope`：`all`（全部）/ `template`（`is_template=1` 或默认类型）/ "
                "`copy`（既非模板也非默认的副本）\n\n"
                "**排序**：默认类型恒置顶（`is_default DESC` 优先），其后按 `sort` —— "
                "`created_at`（降序，默认）或 `type_name`（升序）。\n\n"
                "**性能**：计数对当前结果集的 `type_id` 用 3 条 `GROUP BY` 聚合，避免 N+1。\n\n"
                "**每项字段**：`type_id` / `type_name` / `description` / `is_default` / `enabled` / "
                "`is_template` / `parent_type_id` / `created_at` / "
                "`updated_at` / `file_count` / `field_count` / `rule_count`。\n\n"
                "> 顶部类型选择器通常只取模板 + 默认 + 当前选中；副本的搜索/管理在「管理」弹窗里用本接口完成。"
            ),
        }
    },
    "/doctype": {
        "post": {
            "summary": "新增/更新文档类型（upsert）",
            "description": (
                "按 `type_id` upsert。\n\n"
                "- **新建**（`type_id` 不存在）：`is_default` 固定写为 0\n"
                "- **更新**（`type_id` 已存在）：只更新 `type_name` / `description` / `enabled`；"
                "**`is_default` 保留原值**（默认类型可改名但不会变成非默认，反之亦然）\n\n"
                "**不经由本接口设置**的字段：`is_template`（用 `promote`/`demote`）、"
                "`parent_type_id`（由 `copy_from`/`import` 自动记录）。\n\n"
                "`type_id` 必须匹配 `^[a-zA-Z0-9_-]+$`（最长 64）。返回 `data={type_id}`。"
            ),
        }
    },
    "/doctype/{type_id}": {
        "delete": {
            "summary": "删除文档类型（单个）",
            "description": (
                "**默认类型不可删除**（返回 400）。非默认类型若有关联数据：\n"
                "- 不传 `force` → 返回 **409** + 数量提示（文件 N、字段 N、规则 N）\n"
                "- `force=true` → 级联删除该类型下所有文件、`file_content/table/chunk`、"
                "`extraction_result/analysis_result`、Milvus 向量、`uploads/{file_id}.pdf`、字段、规则\n\n"
                "**错误码**：400（默认类型）/ 404（类型不存在）/ 409（有数据未 force）。\n\n"
                "返回 `data={type_id, deleted_files, deleted_fields, deleted_rules}`，"
                "三个计数仅在 `force=true` 时非零。"
            ),
        }
    },
    "/doctype/batch_delete": {
        "post": {
            "summary": "批量删除文档类型",
            "description": (
                "对 `type_ids` 逐条复用单删逻辑，**单条失败不中断**整批，最后一次性提交。\n\n"
                "每条会被**跳过**（`ok=false` + `reason`）的情况：类型不存在 / 默认类型 / "
                "有关联数据但未传 `force`。其余执行删除；`force=true` 时级联文件 + Milvus + PDF + 字段 + 规则。\n\n"
                "返回 `data={results:[{type_id, ok, reason?, deleted_files, deleted_fields, deleted_rules}], deleted:<成功条数>}`。"
            ),
        }
    },
    "/doctype/{type_id}/copy_from": {
        "post": {
            "summary": "同实例跨类型复制配置",
            "description": (
                "把源类型（`source_type_id`）的字段+规则复制到当前 `type_id`，生成全新 ID 的**独立副本**"
                "（复制后两份互不影响）。\n\n"
                "- `field_ids` / `rule_ids` 不传或为 `null` 表示该类目全部复制；空数组 `[]` 表示不复制\n"
                "- 字段名 / 规则名保持不变；新的 `field_id` / `rule_id` 基于源 ID 自动编号，"
                "例如 `amount` → `amount_0002`，再次复制为 `amount_0003`\n"
                "- `on_conflict=skip`：目标类型已有同源 `field_id` / `rule_id` 副本时跳过\n"
                "- `on_conflict=rename`（默认）：自动取下一个可用 `_000N` 副本 ID\n"
                "- 规则的 `depend_fields` 按源 `field_id` 精确重映射到本次复制生成的新 `field_id`；"
                "依赖字段未随本次复制一起复制时记入 `missing_dependencies`（格式 `规则名::源field_id`），"
                "规则仍照常创建（仅丢失该依赖）\n\n"
                "**血缘副作用**（目标非默认类型时）：把目标 `parent_type_id` 记为 `source_type_id`。\n\n"
                "**错误码**：400（源=目标）/ 404（源或目标不存在）。\n\n"
                "返回 `data=CopyConfigsResponse{copied_fields, skipped_fields, copied_rules, "
                "skipped_rules, missing_dependencies}`。"
            ),
        }
    },
    "/doctype/{type_id}/export": {
        "get": {
            "summary": "导出配置为 JSON 载荷",
            "description": (
                "把目标类型的字段+规则序列化为可跨实例迁移的 JSON（字段按 `priority` 升序）。\n\n"
                "规则依赖以 **field_name** 列表（`depend_field_names`）序列化，避免依赖原 `field_id`，"
                "便于在 `POST /doctype/import` 时按名重映射。\n\n"
                "前端「只读查看配置」也复用本接口。返回 `data=ExportPayload`（见组件 schema）。"
            ),
        }
    },
    "/doctype/import": {
        "post": {
            "summary": "从 JSON 载荷导入配置",
            "description": (
                "把 `GET /doctype/{type_id}/export` 产生的载荷导入到目标类型。\n\n"
                "- `target_type_id` 为空时使用 `payload.type_id`（都为空 → 400）\n"
                "- 目标类型不存在且 `create_type_if_missing=true` → 自动创建；为 false → 404\n"
                "- 字段始终生成**新 `field_id`**，避免与全局唯一约束冲突\n"
                "- `on_conflict` 是导入时的同名策略：`rename`（默认，加 ` (副本)` 后缀）或 `skip`\n"
                "- 规则按 `depend_field_names` 在目标类型按字段名重映射；缺失依赖进 `missing_dependencies`\n\n"
                "返回 `data=ImportConfigsResponse{target_type_id, created_type, copied_fields, "
                "skipped_fields, copied_rules, skipped_rules, missing_dependencies}`。"
            ),
        }
    },
    "/doctype/{type_id}/promote": {
        "post": {
            "summary": "标记为模板",
            "description": (
                "把副本/普通类型标记为模板（`is_template=1`），保留 `parent_type_id` 作血缘。\n\n"
                "模板会进入顶部选择器，并可用 `GET /doctype/list?scope=template` 过滤出来。\n\n"
                "**错误码**：400（默认类型无需标记）/ 404（类型不存在）。返回 `data={type_id}`。"
            ),
        }
    },
    "/doctype/{type_id}/demote": {
        "post": {
            "summary": "取消模板标记",
            "description": (
                "取消模板标记（`is_template=0`），不影响 `parent_type_id` 血缘。\n\n"
                "**错误码**：400（默认类型不可操作）/ 404（类型不存在）。返回 `data={type_id}`。"
            ),
        }
    },

    # ─── file ──────────────────────────────────────────────
    "/file/parse": {
        "post": {
            "summary": "提交文件解析",
            "description": (
                "上传文件并启动 6 阶段处理管线（parsing → tableing → chunking → embedding → "
                "extracting → analyzing → complete）。`Content-Type` 必须为 `multipart/form-data`。\n\n"
                "**`mode` 行为差异**\n"
                "- `async`（默认）：后台任务，立即返回 `data={file_id}`，`message=\"文件已提交处理（异步）\"`\n"
                "- `sync`：阻塞直到全部完成或失败，再返回 `data={file_id}`\n"
                "- `stream`：返回 `text/event-stream`，逐阶段推送事件（事件序列见 `API_DOCUMENTATION.md` 第 9.1 节）\n"
                "- 其它任意值按 `sync` 处理（代码 fallback 分支）\n\n"
                "**`callback_url`** 在 `async` 与 `sync` 模式下都会被使用（每阶段开始、每条 "
                "`field_done`/`rule_done`、每阶段 `stage_done` 都 POST 通知；超时 2.5s，失败仅 warning，"
                "不阻断主流程）。`stream` 模式忽略 `callback_url`，事件改走 SSE。\n\n"
                "**`file_id` 生成**：`SHA256[:32]((type_id, file_name, time.time_ns(), token_hex(8)))` —— "
                "每次上传都是新 ID，**不做去重**。失败重试请用 `POST /file/{file_id}/retry/{stage}`，"
                "**不要**重新上传（会产生新记录）。\n\n"
                "原始 PDF 字节会同步写到 `uploads/{file_id}.pdf`（供 VL 抽取使用），写盘失败不阻断主管线。\n\n"
                "**文件大小限制**：受 `mineru.max_file_size` 控制（默认 100MB / 104857600 字节），超限返回 "
                "`ResponseWrapper{code:400, message:\"文件大小超过限制 (100MB)\"}`（不抛 HTTP 错误）。"
            ),
        }
    },
    "/file/list": {
        "get": {
            "summary": "分页查询文件列表",
            "description": (
                "按 `create_time DESC` 分页。`status` 与 `type_id` 均为**精确匹配**，空字符串表示不过滤。\n\n"
                "**`status` 合法值**（即 `progress` 字段）：`parsing` / `tableing` / `chunking` / "
                "`embedding` / `extracting` / `analyzing` / `complete`，以及对应的 `*_failed` 失败态。\n\n"
                "返回 `data=FileListResponse{items:[FileListItem], total, page, page_size, total_pages}`；"
                "`FileListItem = {file_id, file_name, file_size, progress, type_id, error, create_time}`。"
            ),
        }
    },
    "/file/batch": {
        "delete": {
            "summary": "批量删除文件",
            "description": (
                "删除请求体中所有 `file_id` 关联的 MySQL 记录、Milvus 向量、`uploads/{file_id}.pdf`。\n\n"
                "不存在的 `file_id` 不报错，会出现在响应 `failed_ids` 中。Milvus / PDF 清理走后台任务，"
                "失败仅记 warning，不影响 MySQL 删除。\n\n"
                "返回 `data=BatchDeleteResponse{deleted_count, failed_ids}`。"
            ),
        }
    },
    "/file/{file_id}/status": {
        "get": {
            "summary": "查询文件处理进度",
            "description": (
                "返回当前 `progress` 与最近的 `error`，含 `create_time` / `updated_at` 两个时间戳。\n\n"
                "`progress` 取值：`parsing` / `tableing` / `chunking` / `embedding` / `extracting` / "
                "`analyzing` / `complete`，以及对应的 `*_failed` 失败态。\n\n"
                "如需每阶段的开始/结束时间戳（计算阶段耗时），请走 `GET /file/{file_id}/detail`。\n\n"
                "**状态码**：200 / 404（文件不存在）。"
            ),
        }
    },
    "/file/{file_id}/detail": {
        "get": {
            "summary": "文件完整详情",
            "description": (
                "在 `/status` 字段基础上额外返回全套阶段时间戳：`start_parsing_time` / `end_parsing_time` / "
                "`start_tableing_time` / `end_tableing_time` / `start_chunking_time` / `end_chunking_time` / "
                "`start_embedding_time` / `end_embedding_time` / `end_extracting_time` / `end_analyzing_time`"
                "（parsing / tableing / chunking / embedding 各有 start+end，extracting / analyzing "
                "只记 end），可据此计算每阶段耗时。\n\n"
                "**状态码**：200 / 404（文件不存在）。"
            ),
        }
    },
    "/file/{file_id}": {
        "delete": {
            "summary": "删除文件",
            "description": (
                "级联删除：`files` / `file_content` / `file_table` / `file_chunk` / "
                "`extraction_result` / `analysis_result`（MySQL 立即提交），Milvus 向量与 "
                "`uploads/{file_id}.pdf` 走后台清理（失败仅 warning），接口立即返回。\n\n"
                "**状态码**：200 / 404（文件不存在）。"
            ),
        }
    },
    "/file/{file_id}/retry/{stage}": {
        "post": {
            "summary": "从指定阶段重试",
            "description": (
                "清掉指定阶段及其下游已有数据（依赖 `init_service` 同款清理逻辑），从该阶段重跑。\n\n"
                "**有效 `stage`**：`tableing` / `chunking` / `embedding` / `extracting` / `analyzing`。\n"
                "兼容旧别名 `table_name_validating` → `tableing`。其它值返回 **400**。\n\n"
                "**`mode`**：`async`（默认，后台任务）/ `sync`（阻塞返回）/ `stream`（SSE 流）。\n\n"
                "**`callback_url`** 仅在 `async` / `sync` 模式生效；`stream` 模式忽略，事件改走 SSE。\n\n"
                "`stream` 返回的事件序列与 `/file/parse?mode=stream` 完全一致。\n\n"
                "**状态码**：200 / 400（无效阶段名）/ 404（文件不存在）。"
            ),
        }
    },
    "/file/{file_id}/retry/extracting": {
        "post": {
            "summary": "快捷重试：字段提取",
            "description": (
                "语义等价于 `POST /file/{file_id}/retry/{stage}` 中 `stage=extracting`，内部即转发调用。\n\n"
                "支持参数：`mode`（async/sync/stream）、`callback_url`（async/sync 生效）。"
            ),
        }
    },
    "/file/{file_id}/retry/analyzing": {
        "post": {
            "summary": "快捷重试：逻辑分析",
            "description": (
                "语义等价于 `POST /file/{file_id}/retry/{stage}` 中 `stage=analyzing`，内部即转发调用。\n\n"
                "支持参数：`mode`（async/sync/stream）、`callback_url`（async/sync 生效）。"
            ),
        }
    },
    "/file/{file_id}/tables": {
        "get": {
            "summary": "文件表格列表",
            "description": (
                "返回 `file_table` 中按 `table_index` 升序的全部表格。\n\n"
                "`table_name` 由 LLM 在 `tableing` 阶段从表前文本识别得出（截断 30 字），"
                "`page_num` 是该表格在 PDF 中的页号（可能是范围如 `\"3-4\"`）。\n\n"
                "返回 `data=[FileTableItem{file_id, table_index, total_table, table_name, "
                "table_content, page_num}]`（文件不存在/无表格时为 `[]`，非 404）。"
            ),
        }
    },
    "/file/{file_id}/chunks": {
        "get": {
            "summary": "文件分块列表",
            "description": (
                "返回 `file_chunk` 按 `chunk_index` 升序的全部分块（表格作为独立 chunk 不拆分）。\n\n"
                "返回 `data=[FileChunkItem{file_id, chunk_id, chunk_index, total_chunks, "
                "chunk_content, page_num}]`（文件不存在/无分块时为 `[]`）。"
            ),
        }
    },
    "/file/{file_id}/outline": {
        "get": {
            "summary": "文件章节大纲",
            "description": (
                "正则解析 Markdown 章节标题（`parse_sections`），与 `extraction.search_type=section` 使用同一套"
                "切片口径 —— 前端看到什么 = 抽取时能匹配到什么。\n\n"
                "返回 `data=[{index, number, title, content, start_pos, end_pos}]`，`content` 是该章节切片正文。"
                "文件不存在或内容为空返回 `[]`（不返回 404），与 `/tables`、`/chunks` 一致。"
            ),
        }
    },
    "/file/{file_id}/extraction": {
        "get": {
            "summary": "字段提取结果",
            "description": (
                "返回 `extraction_result` 全表行（LEFT JOIN `extraction_field` 获取字段名称）：\n"
                "`data=[{file_id, field_id, field_name, extracted_value, reason, source_refs}]`。\n\n"
                "`field_name` 来自字段配置表，若配置已被删除则为 `null`。\n\n"
                "`source_refs` 为参考块字典：每条 ref 含 `text`（该条命中注入 prompt 的原始片段），"
                "text/table 类 ref 另含 `bboxes`（`[{page_num, bbox, page_size}]` 块级 PDF 框，供前端高亮定位）；"
                "顶层 `_texts` 键为 `{label: 拼接后实际注入占位符的完整文本}`；"
                "vl 类为 `{_vl: {...}}` 元数据（无检索文本）。"
                "存量老数据无 `text`/`_texts`/`bboxes`，消费方需容错。\n\n"
                "如需调试细节请走 `/extraction/test` 或 `/extraction/test/stream`。"
            ),
        }
    },
    "/file/{file_id}/analysis": {
        "get": {
            "summary": "逻辑分析结果",
            "description": (
                "返回 `analysis_result` 全表行（LEFT JOIN `analysis_rule` 获取规则名称）：\n"
                "`data=[{file_id, rule_id, rule_name, result_value, input_values, reason}]`"
                "（含 `input_values` 字典，但不含 `source_refs`）。\n\n"
                "`rule_name` 来自规则配置表，若配置已被删除则为 `null`。"
            ),
        }
    },

    # ─── extraction ────────────────────────────────────────
    "/extraction/fields": {
        "get": {
            "summary": "列出字段配置",
            "description": (
                "按 `priority` 升序返回字段配置。`type_id` 为空时返回全量；非空时按精确匹配过滤。\n\n"
                "返回 `data=[ExtractionFieldResponse]`（即 `ExtractionFieldCreate` 各字段 + `created_at` / `updated_at`）。"
            ),
        },
        "post": {
            "summary": "新增/更新字段配置（upsert）",
            "description": (
                "按 `field_id` **全局唯一** upsert。\n\n"
                "**冲突处理**：若 `field_id` 已存在但归属其它 `type_id`，返回 **409**，提示换 ID 或先删除原记录。\n\n"
                "**Prompt / 配置校验（Pydantic 层，违反返回 422）**\n"
                "- `source_type=text` 时 `text_extract_prompt` 必须含至少一个 `<search_result>标签</search_result>`\n"
                "- `source_type=table` 时 `table_extract_prompt` 同上\n"
                "- `source_type=vl` 时 `vl_method` 与 `vl_extract_prompt` 必填，且 prompt 必须含 "
                "`value` 和 `reason` 关键字（大小写不敏感，因为最终要求 VL 输出 `{value, reason}` JSON）\n"
                "- `vl_progressive` 的自定义 `batch_prompt_template` 必含 "
                "`{field_hints}` `{page_label}` `{total_pages}` `{history}`\n"
                "- `vl_locate` 的自定义 `locate_prompt_template` 必含 "
                "`{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}`\n"
                "- 模板里的字面 `{` `}` 需写成 `{{` `}}` 转义（`str.format()` 渲染）\n\n"
                "返回 `data={field_id}`，`message` 区分「已创建 / 已更新」。"
            ),
        }
    },
    "/extraction/fields/{field_id}": {
        "delete": {
            "summary": "删除字段配置",
            "description": (
                "**硬删除**（直接 `DELETE` 整行，不走 `enabled=0` 软删除），仅删除字段配置本身——"
                "该字段历史已写入的 `extraction_result` 行**不会级联清理**。\n\n"
                "字段不存在 → **404**。成功返回 `message=\"字段配置已删除\"`，`data=null`。"
            ),
        }
    },
    "/extraction/fields/{field_id}/check": {
        "get": {
            "summary": "检查字段 ID 是否存在",
            "description": (
                "只读探测 `field_id` 是否已被占用（前端保存前查重用）。\n\n"
                "**全局**查存在性（不按 `type_id` 过滤），因此 `exists=true` 也可能是该 ID 被**其它类型**占用"
                "——与 upsert 的跨类型 409 冲突一致。无论是否存在都返回 **200** + `data={exists: bool}`（不会 404）。"
            ),
        }
    },
    "/extraction/test": {
        "post": {
            "summary": "字段提取调试（同步）",
            "description": (
                "两种模式（二选一）：传 `field_id` 加载已保存配置，或传 `config`（完整字段配置 dict）用临时配置。\n\n"
                "**`search_results` 形态因 `source_type` / `search_type` 不同而异**\n"
                "- `source_type=table`：`[{table_name, table_content(截断 500 字)}, ...]`\n"
                "- `source_type=text` + `search_type=context`：关键词命中前后上下文片段列表\n"
                "- `search_type=section`：章节切片列表\n"
                "- `search_type=rule`：关键词起点 + 停止词边界片段列表\n"
                "- `search_type=chunk_db`：MySQL 分块命中列表\n"
                "- `search_type=vector_db`：Milvus 语义命中列表（含 `score`）\n"
                "- `search_type=page`：按 `page_range` 切出的整页 Markdown\n"
                "- `source_type=vl`：**单元素**数组 `[{type:\"vl_meta\", method, key_pages, "
                "vl_total_tokens, batches_with_info}]`\n\n"
                "**`llm_input` / `llm_output`**\n"
                "- 普通字段：`llm_input` 是渲染后的 prompt，`llm_output` 是 LLM 原始响应，"
                "`extracted_value` / `reason` 是 JSON 解析后的结果\n"
                "- VL 字段：`llm_input` 是 `vl_extract_prompt`，`llm_output` = `extracted_value`"
                "（VL 直出 JSON，不再二次抽取）\n\n"
                "返回 `data=ExtractionTestResponse{search_results, llm_input, llm_output, extracted_value, reason}`。\n\n"
                "**状态码**：200 / 400（既未传 `field_id` 也未传 `config`）/ 404（字段或文件内容不存在）/ 500（提取异常）。"
            ),
        }
    },
    "/extraction/test/stream": {
        "post": {
            "summary": "字段提取流式调试（SSE）",
            "description": (
                "通过 SSE 分步推送检索结果 → prompt → LLM/VL 响应 → 最终结果。入参与 `/extraction/test` 相同"
                "（`field_id` 或 `config` 二选一）。\n\n"
                "事件类型清单见 `docs/API_DOCUMENTATION.md` 第 9.2 节。VL 字段的进度事件"
                "（`pdf_loaded` / `progressive_batch` / `locate_locate` / `locate_extract`）**仅** SSE 推送，"
                "不走异步 `callback_url`。\n\n"
                "**状态码**：200（SSE 流）/ 400 / 404。"
            ),
        }
    },

    # ─── analysis ──────────────────────────────────────────
    "/analysis/rules": {
        "get": {
            "summary": "列出分析规则",
            "description": (
                "按 `priority` 升序返回规则。`type_id` 为空时返回全量；非空时按精确匹配过滤。\n\n"
                "返回 `data=[AnalysisRuleResponse]`（即 `AnalysisRuleCreate` 各字段 + `created_at` / `updated_at`）。"
            ),
        },
        "post": {
            "summary": "新增/更新分析规则（upsert）",
            "description": (
                "按 `rule_id` **全局唯一** upsert。`rule_id` 已被其它 `type_id` 占用时返回 **409**。\n\n"
                "**校验（Pydantic 层，违反返回 422）**：`expression` 必须包含至少一个 "
                "`<field_result>字段ID</field_result>` 占位符。\n\n"
                "`system_prompt` 仅 `judge` 类型生效；`calc` 类型直接用 `numexpr` 计算，结果按 "
                "`analysis.calc_precision`（默认 2 位）保留小数。返回 `data={rule_id}`。"
            ),
        }
    },
    "/analysis/rules/{rule_id}": {
        "delete": {
            "summary": "删除分析规则",
            "description": (
                "**硬删除**（直接 `DELETE` 整行，不走 `enabled=0` 软删除），仅删除规则配置本身——"
                "该规则历史已写入的 `analysis_result` 行**不会级联清理**。\n\n"
                "规则不存在 → **404**。成功返回 `message=\"规则配置已删除\"`，`data=null`。"
            ),
        }
    },
    "/analysis/rules/{rule_id}/check": {
        "get": {
            "summary": "检查规则 ID 是否存在",
            "description": (
                "只读探测 `rule_id` 是否已被占用（前端保存前查重用）。\n\n"
                "**全局**查存在性（不按 `type_id` 过滤），因此 `exists=true` 也可能是该 ID 被**其它类型**占用"
                "——与 upsert 的跨类型 409 冲突一致。无论是否存在都返回 **200** + `data={exists: bool}`（不会 404）。"
            ),
        }
    },
    "/analysis/test": {
        "post": {
            "summary": "逻辑分析调试（同步）",
            "description": (
                "两种模式（二选一）：传 `rule_id` 加载已保存规则，或传 `config` 用临时配置。\n\n"
                "依赖字段值来自该 `file_id` 已有的 `extraction_result`；`depend_fields` 中缺失提取结果的字段取空串。\n\n"
                "**返回字段**（`data=AnalysisTestResponse`）：\n"
                "- `input_values`：依赖字段（`depend_fields`）的提取值字典；缺失字段值为空串\n"
                "- `expression_resolved`：`<field_result>...</field_result>` 占位符替换后的表达式\n"
                "- `result_value`：最终结果\n"
                "  - `judge`：调用 LLM 判断（受 `system_prompt` 控制），通常返回 `true` / `false`\n"
                "  - `calc`：`numexpr` 计算，按 `analysis.calc_precision`（默认 2 位）保留小数\n"
                "- `reason`：LLM 给出的判断理由（calc 类型为计算说明）\n\n"
                "**状态码**：200 / 400（既未传 `rule_id` 也未传 `config`）/ 404（规则不存在）/ 500（分析异常）。"
            ),
        }
    },
    "/analysis/test/stream": {
        "post": {
            "summary": "逻辑分析流式调试（SSE）",
            "description": (
                "SSE 分步推送：`input_values` → `resolved_expression` → "
                "（judge：`prompt` → `llm_response`）→ `result` → `done`。calc 类型无 "
                "`prompt` / `llm_response` 步骤。入参与 `/analysis/test` 相同。\n\n"
                "**状态码**：200（SSE 流）/ 400 / 404。"
            ),
        }
    },

    # ─── search ────────────────────────────────────────────
    "/search": {
        "post": {
            "summary": "向量相似度检索",
            "description": (
                "将 `query` 通过 embedding 接口向量化后在 Milvus 中检索，返回分块及 L2 距离 `score`"
                "（越小越相似）。\n\n"
                "**请求字段**\n"
                "- `query`：必填，检索文本（空串直接返回空列表）\n"
                "- `file_id`：可选，限定检索范围；省略则跨全部已向量化的文件\n"
                "- `top_k`：可选，默认 `10`\n"
                "- `score_threshold`：可选，L2 距离上限，超过该距离的结果被过滤；省略不过滤\n\n"
                "返回 `data=[SearchResultItem{chunk_id, file_id, chunk_index, chunk_content, "
                "score, page_num}]`。"
            ),
        }
    },
}


# ─── 参数级文档 ──────────────────────────────────────────────
# GLOBAL_PARAM_DOCS 按参数名兜底；PARAM_OVERRIDES 按 (path, method) 精确覆盖。
# 值可为 str（仅 description）或 dict（description / enum / example）。

GLOBAL_PARAM_DOCS: Dict[str, str] = {
    "file_id": "目标文件 ID（`POST /file/parse` 返回的 32 位 SHA256 摘要）。",
    "field_id": "字段配置 ID（全局唯一，匹配 `^[a-zA-Z0-9_]+$`，最长 100）。",
    "rule_id": "分析规则 ID（全局唯一，匹配 `^[a-zA-Z0-9_]+$`，最长 100）。",
    "type_id": "文档类型 ID（匹配 `^[a-zA-Z0-9_-]+$`，最长 64）。",
    "page": "页码，从 1 开始。",
    "page_size": "每页条数。",
    "callback_url": "可选回调地址；管线每阶段开始 / `field_done` / `rule_done` / `stage_done` 都会向此 URL POST（超时 2.5s，失败仅 warning）。仅 `async` / `sync` 模式生效，`stream` 模式忽略。",
}

PARAM_OVERRIDES: Dict[tuple, Dict[str, Any]] = {
    ("/doctype/list", "get"): {
        "q": "模糊搜索关键词，对 `type_id` / `type_name` 做 `LIKE %q%`；省略不过滤。",
        "scope": {
            "description": "范围过滤：`all` 全部 / `template` 模板（含默认类型）/ `copy` 副本（既非模板也非默认）。",
            "enum": ["all", "template", "copy"],
        },
        "page": "页码（从 1 开始）。**与 `page_size` 同时传入才启用分页**，返回 `{items, total}`；否则返回数组。",
        "page_size": "每页条数（1–500）。**与 `page` 同时传入才启用分页**。",
        "sort": {
            "description": "排序字段：`created_at`（降序，默认）或 `type_name`（升序）。默认类型恒置顶，不受影响。",
            "enum": ["created_at", "type_name"],
        },
    },
    ("/file/list", "get"): {
        "status": "按 `progress` 精确过滤：`parsing` / `tableing` / `chunking` / `embedding` / `extracting` / `analyzing` / `complete` 及对应 `*_failed`；空串不过滤。",
        "type_id": "按文档类型精确过滤；空串返回全部类型。",
        "page": "页码，从 1 开始（默认 1）。",
        "page_size": "每页条数（默认 20）。",
    },
    ("/extraction/fields", "get"): {
        "type_id": "按文档类型精确过滤字段配置；空串返回全部。",
    },
    ("/analysis/rules", "get"): {
        "type_id": "按文档类型精确过滤规则；空串返回全部。",
    },
    ("/file/parse", "post"): {
        "mode": {
            "description": "处理模式：`async`（默认，后台任务立即返回）/ `sync`（阻塞至完成）/ `stream`（SSE 流）。其它值按 `sync` 处理。",
            "enum": ["async", "sync", "stream"],
        },
        "type_id": "归属文档类型，默认 `default`；决定使用哪套字段/规则配置。",
    },
    ("/file/{file_id}/retry/{stage}", "post"): {
        "stage": {
            "description": "重试起点阶段：`tableing` / `chunking` / `embedding` / `extracting` / `analyzing`（兼容旧别名 `table_name_validating` → `tableing`）。该阶段及下游数据会被清理后重跑。",
            "enum": ["tableing", "chunking", "embedding", "extracting", "analyzing"],
        },
        "mode": {
            "description": "处理模式：`async`（默认）/ `sync` / `stream`。",
            "enum": ["async", "sync", "stream"],
        },
    },
    ("/file/{file_id}/retry/extracting", "post"): {
        "mode": {"description": "处理模式：`async`（默认）/ `sync` / `stream`。", "enum": ["async", "sync", "stream"]},
    },
    ("/file/{file_id}/retry/analyzing", "post"): {
        "mode": {"description": "处理模式：`async`（默认）/ `sync` / `stream`。", "enum": ["async", "sync", "stream"]},
    },
    ("/doctype/{type_id}", "delete"): {
        "force": "为 `true` 时级联删除该类型下所有文件、配置、Milvus 向量与 PDF；为 `false`（默认）且有关联数据则返回 409。",
    },
}


# ─── Schema（组件模型）级文档 ────────────────────────────────
# 每个 schema：description（整体说明）、properties（逐字段说明）、examples（请求示例数组）。
# 注：响应模型（如 FileListResponse / DocTypeResponse）因统一包在 ResponseWrapper.data(Any) 中，
# 不会出现在组件 schema 里——其形态写在对应 operation 的 description。

_SEARCH_CONFIG_DOC = (
    "检索配置（自由 JSON，键随 `search_type` 不同）：\n"
    "- `context`：`keywords`[必] / `context_before`(200) / `context_after`(200) / `max_results`(5) / `sort_order`(asc)\n"
    "- `section`：`section_pattern` / `section_match_type`|`match_type`(contains) / `threshold`(0.8) / `max_results`(3) / `sort_order`(asc)\n"
    "- `rule`：`keywords`[必] / `stop_words`(默认中文标点集) / `direction`(forward) / `min_length`(2) / `max_length`(200) / `max_results`(5) / `sort_order`(asc)\n"
    "- `chunk_db`：`keywords`[必] / `keyword_filter` / `max_results`|`top_k`(10) / `sort_order`(asc)\n"
    "- `vector_db`：`query_text` / `top_k`(5) / `score_threshold`\n"
    "- `page`：`page_range`（如 `\"1-3\"` / `\"all\"` / `\"2\"`）/ `max_length`(30000，末尾截断)"
)

_VL_CONFIG_DOC = (
    "VL 配置（自由 JSON，键随 `vl_method` 不同）：\n"
    "- `vl_model`：`page_range`(默认 `\"all\"`，指定页一次性塞 VL)\n"
    "- `vl_progressive`：`field_hints`(\"\") / `batch_size`(2) / 可选 `batch_prompt_template`"
    "（占位符 `{history}` `{field_hints}` `{page_label}` `{total_pages}`）\n"
    "- `vl_locate`：`field_hints`(\"\") / `grid_pages`(6) / `grid_cols`(3) / `max_concurrent`(20) / "
    "可选 `locate_prompt_template`（占位符 `{field_hints}` `{page_labels}` `{position_map}` "
    "`{grid_rows}` `{grid_cols}`）"
)

SCHEMA_DOCS: Dict[str, Dict[str, Any]] = {
    # ── 通用包装 ──
    "ResponseWrapper": {
        "description": "统一响应包装。业务负载在 `data` 中，其具体形态见各接口描述。",
        "properties": {
            "code": "业务状态码，成功 200；业务校验失败（如文件超限）也可能在 200 响应里返回非 200 的 `code`。",
            "message": "人类可读的结果说明。",
            "data": "业务负载，类型随接口而定（对象 / 数组 / null）。",
        },
    },
    # ── 枚举 ──
    "SourceTypeEnum": {
        "description": "字段提取来源：`table`（按表名匹配后 LLM 抽取）/ `text`（文本检索后 LLM 抽取）/ `vl`（视觉模型端到端抽取，绕过 Markdown）。",
    },
    "TableMatchTypeEnum": {
        "description": "表名匹配方式：`exact` 精确 / `fuzzy` 模糊（相似度）/ `contains` 包含 / `llm` 由 LLM 判定。",
    },
    "SearchTypeEnum": {
        "description": "文本检索方式：`context` 关键词上下文 / `section` 章节匹配 / `rule` 关键词+停止词边界 / `chunk_db` MySQL 分块检索 / `vector_db` Milvus 语义检索 / `page` 按页码区间切片。",
    },
    "VLMethodEnum": {
        "description": "VL 抽取方法：`vl_model` 指定页一次出 JSON / `vl_progressive` 分批扫描+伪历史累积 / `vl_locate` 缩略图网格定位+关键页高清提取。",
    },
    "RuleTypeEnum": {
        "description": "规则类型：`judge`（LLM 真假判断，用 `system_prompt` 调控）/ `calc`（numexpr 数学计算）。",
    },
    # ── 文档类型 ──
    "DocTypeCreate": {
        "description": "创建/更新文档类型的请求体（按 `type_id` upsert）。",
        "properties": {
            "type_id": "类型 ID，匹配 `^[a-zA-Z0-9_-]+$`（最长 64）；作为 upsert 主键。",
            "type_name": "类型显示名（最长 200）。",
            "description": "类型描述（可空）。",
            "enabled": "是否启用，1 启用 / 0 停用。",
        },
        "examples": [{"type_id": "financial_report", "type_name": "财务报告", "description": "上市公司年度财报", "enabled": 1}],
    },
    "DocTypeBatchDeleteRequest": {
        "description": "批量删除类型的请求体；逐条记录结果，不因单条失败中断。",
        "properties": {
            "type_ids": "要删除的类型 ID 列表（默认类型 / 不存在 / 有数据未 force 的会被跳过）。",
            "force": "是否级联删除类型下的文件与配置；默认 `false`。",
        },
        "examples": [{"type_ids": ["tmp_a", "tmp_b"], "force": False}],
    },
    "CopyConfigsRequest": {
        "description": "从源类型复制字段/规则到目标类型（独立副本）。",
        "properties": {
            "source_type_id": "源类型 ID（必填，须 ≠ 目标 `type_id`）。",
            "field_ids": "要复制的字段 ID 列表；不传或 `null` 表示全部字段，空数组 `[]` 表示不复制字段。",
            "rule_ids": "要复制的规则 ID 列表；不传或 `null` 表示全部规则，空数组 `[]` 表示不复制规则。",
            "on_conflict": "目标类型已有同源 ID 副本时的策略：`rename`（默认，生成下一个 `_000N` 副本 ID）或 `skip`（跳过）。字段名/规则名保持不变。",
        },
        "examples": [{"source_type_id": "financial_report", "field_ids": None, "rule_ids": None, "on_conflict": "rename"}],
    },
    "ExportFieldItem": {
        "description": "导出格式的字段项（不含 `type_id` / 时间戳；`field_id` 保留但导入时会重新生成）。",
        "properties": {
            "field_id": "源端字段 ID（导入时不复用，会生成新 ID）。",
            "field_name": "字段显示名（导入时的同名判定依据）。",
            "source_type": "来源类型：`table` / `text` / `vl`。",
            "enabled": "是否启用（1/0）。",
            "priority": "执行优先级，数字越小越先（升序）。",
            "table_name_pattern": "[table] 表名匹配模式。",
            "table_match_type": "[table] 匹配方式：`exact` / `fuzzy` / `contains` / `llm`。",
            "table_match_keywords": "[table] 匹配关键词列表。",
            "table_match_max_results": "[table] 最多命中表数。",
            "table_system_prompt": "[table] LLM system prompt。",
            "table_extract_prompt": "[table] 抽取 prompt，须含 `<search_result>标签</search_result>` 占位符。",
            "search_type": "[text] 检索方式：`context` / `section` / `rule` / `chunk_db` / `vector_db` / `page`。",
            "search_config": _SEARCH_CONFIG_DOC,
            "text_system_prompt": "[text] LLM system prompt。",
            "text_extract_prompt": "[text] 抽取 prompt，须含 `<search_result>标签</search_result>` 占位符。",
            "vl_method": "[vl] 方法：`vl_model` / `vl_progressive` / `vl_locate`。",
            "vl_config": _VL_CONFIG_DOC,
            "vl_system_prompt": "[vl] LLM system prompt。",
            "vl_extract_prompt": "[vl] 抽取 prompt，须含 `value` 与 `reason` 关键字。",
        },
    },
    "ExportRuleItem": {
        "description": "导出格式的规则项；依赖用 `depend_field_names`（字段名列表）表达，便于跨环境恢复。",
        "properties": {
            "rule_id": "源端规则 ID（导入时不复用，会生成新 ID）。",
            "rule_name": "规则显示名（导入时的同名判定依据）。",
            "rule_type": "规则类型：`judge` / `calc`。",
            "expression": "表达式，含 `<field_result>字段ID</field_result>` 占位符。",
            "system_prompt": "[judge] LLM system prompt。",
            "depend_field_names": "依赖字段的**名称**列表（导入时按名重映射到目标类型的 `field_id`）。",
            "enabled": "是否启用（1/0）。",
            "priority": "执行优先级（升序）。",
        },
    },
    "ExportPayload": {
        "description": "导出/导入的整体载荷。`GET /doctype/{type_id}/export` 产出，`POST /doctype/import` 消费。",
        "properties": {
            "type_id": "源类型 ID（导入时若未指定 `target_type_id` 则用它）。",
            "type_name": "源类型名（自动创建目标类型时作为名称）。",
            "description": "源类型描述。",
            "version": "载荷版本号，当前为 1。",
            "fields": "字段项列表（`ExportFieldItem`）。",
            "rules": "规则项列表（`ExportRuleItem`）。",
        },
    },
    "ImportConfigsRequest": {
        "description": "从 JSON 载荷导入字段+规则到目标类型。",
        "properties": {
            "payload": "导出载荷（`ExportPayload`）。",
            "target_type_id": "目标类型 ID；为空时用 `payload.type_id`。",
            "create_type_if_missing": "目标类型不存在时是否自动创建（默认 `true`）；为 `false` 且不存在则 404。",
            "on_conflict": "同名策略：`rename`（默认）或 `skip`。",
        },
    },
    # ── 文件 ──
    "BatchDeleteRequest": {
        "description": "批量删除文件的请求体。",
        "properties": {"file_ids": "要删除的文件 ID 列表（不存在的会进入响应 `failed_ids`）。"},
        "examples": [{"file_ids": ["a1b2c3...", "d4e5f6..."]}],
    },
    # ── 字段提取配置 ──
    "ExtractionFieldCreate": {
        "description": (
            "字段提取配置（按 `field_id` 全局唯一 upsert）。`source_type` 决定哪一组字段生效："
            "`table` 用 `table_*`，`text` 用 `search_type` / `search_config` / `text_*`，`vl` 用 `vl_*`。"
        ),
        "properties": {
            "field_id": "字段 ID，匹配 `^[a-zA-Z0-9_]+$`（最长 100），**全局唯一**。",
            "type_id": "归属文档类型，默认 `default`。",
            "field_name": "字段显示名（最长 200）。",
            "source_type": "来源类型：`table` / `text` / `vl`（决定下面哪组字段生效）。",
            "enabled": "是否启用（1/0）。",
            "priority": "执行优先级，数字越小越先（升序）。",
            "table_name_pattern": "[table] 表名匹配模式（配合 `table_match_type`）。",
            "table_match_type": "[table] 匹配方式：`exact` / `fuzzy` / `contains` / `llm`。",
            "table_match_keywords": "[table] 匹配关键词列表。",
            "table_match_max_results": "[table] 最多命中表数。",
            "table_system_prompt": "[table] LLM system prompt（可空）。",
            "table_extract_prompt": "[table] 抽取 prompt；`source_type=table` 时须含至少一个 `<search_result>标签</search_result>`。",
            "search_type": "[text] 检索方式：`context` / `section` / `rule` / `chunk_db` / `vector_db` / `page`。",
            "search_config": "[text] " + _SEARCH_CONFIG_DOC,
            "text_system_prompt": "[text] LLM system prompt（可空）。",
            "text_extract_prompt": "[text] 抽取 prompt；`source_type=text` 时须含至少一个 `<search_result>标签</search_result>`。",
            "vl_method": "[vl] 方法：`vl_model` / `vl_progressive` / `vl_locate`；`source_type=vl` 时必填。",
            "vl_config": "[vl] " + _VL_CONFIG_DOC,
            "vl_system_prompt": "[vl] LLM system prompt（可空）。",
            "vl_extract_prompt": "[vl] 抽取 prompt；`source_type=vl` 时必填，且须含 `value` 与 `reason` 关键字（大小写不敏感）。",
        },
        "examples": [
            {
                "field_id": "company_name",
                "type_id": "financial_report",
                "field_name": "公司名称",
                "source_type": "text",
                "enabled": 1,
                "priority": 0,
                "search_type": "context",
                "search_config": {"keywords": ["公司名称", "企业名称"], "context_before": 50, "context_after": 200, "max_results": 3},
                "text_extract_prompt": "从以下内容提取公司名称：\n<search_result>命中片段</search_result>\n以 JSON 输出 {value, reason}。",
            },
            {
                "field_id": "total_assets",
                "type_id": "financial_report",
                "field_name": "资产总计",
                "source_type": "table",
                "enabled": 1,
                "priority": 1,
                "table_name_pattern": "资产负债表",
                "table_match_type": "contains",
                "table_extract_prompt": "从下表提取资产总计：\n<search_result>表格</search_result>\n输出 {value, reason}。",
            },
            {
                "field_id": "seal_present",
                "type_id": "contract",
                "field_name": "是否盖章",
                "source_type": "vl",
                "enabled": 1,
                "priority": 0,
                "vl_method": "vl_locate",
                "vl_config": {"field_hints": "公章/合同章", "grid_pages": 6, "grid_cols": 3, "max_concurrent": 20},
                "vl_extract_prompt": "判断文档是否盖章，输出 JSON {value, reason}。",
            },
        ],
    },
    "ExtractionTestRequest": {
        "description": "字段提取调试请求：`field_id`（用已存配置）或 `config`（临时完整配置）二选一，均需 `file_id`。",
        "properties": {
            "file_id": "目标文件 ID（须已完成 parsing，文本类需 `file_content`，VL 类需 `uploads/{file_id}.pdf`）。",
            "field_id": "已保存字段配置 ID；与 `config` 二选一。",
            "config": "临时字段配置 dict（结构同 `ExtractionFieldCreate`）；与 `field_id` 二选一。",
        },
        "examples": [{"file_id": "a1b2c3...", "field_id": "company_name"}],
    },
    # ── 逻辑分析配置 ──
    "AnalysisRuleCreate": {
        "description": "逻辑分析规则配置（按 `rule_id` 全局唯一 upsert）。",
        "properties": {
            "rule_id": "规则 ID，匹配 `^[a-zA-Z0-9_]+$`（最长 100），**全局唯一**。",
            "type_id": "归属文档类型，默认 `default`。",
            "rule_name": "规则显示名（最长 200）。",
            "rule_type": "规则类型：`judge`（LLM 判断）/ `calc`（numexpr 计算）。",
            "expression": "表达式，须含至少一个 `<field_result>字段ID</field_result>` 占位符（渲染时替换为字段提取值）。",
            "system_prompt": "[judge] 调控 LLM 判断的 system prompt；`calc` 类型忽略。",
            "depend_fields": "依赖的字段 ID 列表（用于取值并填充占位符）。",
            "enabled": "是否启用（1/0）。",
            "priority": "执行优先级（升序）。",
        },
        "examples": [
            {
                "rule_id": "assets_positive",
                "type_id": "financial_report",
                "rule_name": "资产为正",
                "rule_type": "calc",
                "expression": "<field_result>total_assets</field_result> > 0",
                "depend_fields": ["total_assets"],
                "enabled": 1,
                "priority": 0,
            }
        ],
    },
    "AnalysisTestRequest": {
        "description": "逻辑分析调试请求：`rule_id`（用已存规则）或 `config`（临时配置）二选一，均需 `file_id`。",
        "properties": {
            "file_id": "目标文件 ID（其 `extraction_result` 提供依赖字段值）。",
            "rule_id": "已保存规则 ID；与 `config` 二选一。",
            "config": "临时规则配置 dict（`rule_type` / `expression` / `system_prompt` / `depend_fields`）；与 `rule_id` 二选一。",
        },
        "examples": [{"file_id": "a1b2c3...", "rule_id": "assets_positive"}],
    },
    # ── 向量检索 ──
    "SearchRequest": {
        "description": "向量相似度检索请求。",
        "properties": {
            "query": "检索文本（必填，空串返回空列表）。",
            "file_id": "限定检索的文件；省略则跨全部已向量化文件。",
            "top_k": "返回条数，默认 10。",
            "score_threshold": "L2 距离上限，超过则过滤；省略不过滤。",
        },
        "examples": [{"query": "公司注册资本是多少", "file_id": None, "top_k": 5, "score_threshold": None}],
    },
}


# ─── 入口 ──────────────────────────────────────────────────

def build_app() -> FastAPI:
    """构造一个无 lifespan 的 FastAPI，仅注册路由用于导出 schema。"""
    app = FastAPI(
        title="析卷 AI",
        version="0.3.0",
        description=API_INFO_DESCRIPTION,
    )
    register_routers(app)
    return app


def _apply_params(op: Dict[str, Any], path: str, method: str) -> None:
    """给单个 operation 的参数注入 description / enum / example。"""
    overrides = PARAM_OVERRIDES.get((path, method), {})
    for param in op.get("parameters", []):
        name = param.get("name")
        meta = overrides.get(name)
        if meta is None and name in GLOBAL_PARAM_DOCS:
            meta = GLOBAL_PARAM_DOCS[name]
        if meta is None:
            continue
        if isinstance(meta, str):
            meta = {"description": meta}
        if "description" in meta:
            param["description"] = meta["description"]
        if "example" in meta:
            param["example"] = meta["example"]
        if "enum" in meta:
            param.setdefault("schema", {})["enum"] = meta["enum"]


def _apply_schema_docs(schema: Dict[str, Any]) -> None:
    """给 components.schemas 注入整体说明 / 逐属性 description / examples。"""
    components = schema.get("components", {}).get("schemas", {})
    for name, doc in SCHEMA_DOCS.items():
        target = components.get(name)
        if target is None:
            print(f"  [warn] schema 不在 spec 中: {name}")
            continue
        if "description" in doc:
            target["description"] = doc["description"]
        if "examples" in doc:
            target["examples"] = doc["examples"]
        props = target.get("properties", {})
        for prop_name, prop_desc in doc.get("properties", {}).items():
            if prop_name not in props:
                print(f"  [warn] 属性不在 schema {name} 中: {prop_name}")
                continue
            props[prop_name]["description"] = prop_desc


def enrich(schema: Dict[str, Any]) -> Dict[str, Any]:
    """对生成的 openapi schema 做就地丰富。"""
    schema["tags"] = TAGS
    schema.setdefault("servers", []).append({
        "url": "http://localhost:5019",
        "description": "本地开发",
    })

    paths = schema.get("paths", {})
    enriched_ops = 0
    covered = set()
    for path, ops in ENRICHMENTS.items():
        if path not in paths:
            print(f"  [warn] 路径不在 schema 中: {path}")
            continue
        for method, fields in ops.items():
            op = paths[path].get(method)
            if not op:
                print(f"  [warn] 方法不在 schema 中: {method.upper()} {path}")
                continue
            if "summary" in fields:
                op["summary"] = fields["summary"]
            else:
                op.setdefault("summary", "")
            if "description" in fields:
                op["description"] = fields["description"]
            enriched_ops += 1
            covered.add((path, method))

    # 参数级丰富（对所有 operation 应用，不限于 ENRICHMENTS 覆盖的）
    for path, methods in paths.items():
        for method, op in methods.items():
            if not isinstance(op, dict) or "parameters" not in op and "requestBody" not in op and "responses" not in op:
                continue
            _apply_params(op, path, method)

    # schema 级丰富
    _apply_schema_docs(schema)

    # 覆盖率自检：列出未被 ENRICHMENTS 覆盖的 operation
    missing = []
    for path, methods in paths.items():
        for method in methods:
            if method in ("get", "post", "put", "delete", "patch") and (path, method) not in covered:
                missing.append(f"{method.upper()} {path}")
    if missing:
        print(f"  [warn] 未被 ENRICHMENTS 覆盖的 operation ({len(missing)}):")
        for m in missing:
            print(f"          {m}")

    print(f"  enriched {enriched_ops} operations；schema_docs {len(SCHEMA_DOCS)} 个")
    return schema


def main() -> None:
    out_path = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("构造 FastAPI app...")
    app = build_app()
    schema = app.openapi()

    print("丰富 schema...")
    schema = enrich(schema)

    print(f"写入 {out_path}")
    out_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done. size = {out_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
