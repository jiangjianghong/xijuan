"""生成丰富描述的 openapi.json。

用法:
    python scripts/gen_openapi.py

行为:
    1. 不启动服务，import FastAPI app -> 调用 app.openapi() 拿原生 schema
    2. 用 ENRICHMENTS 字典对 path/operation 做就地丰富 (summary/description/examples/tags)
    3. 注入顶层 info.description / servers / tags
    4. 写入 docs/openapi.json (UTF-8, 缩进 2)
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
PDF 文档智能处理系统的 HTTP API。

**管线**: parsing → tableing → chunking → embedding → extracting → analyzing → complete

**关键概念**

- **文档类型 (`type_id`)** —— 隔离不同格式文件的字段/规则配置。每个文件归属唯一 `type_id`，默认 `default`，不可删除。
- **`file_id`** —— 由 `(type_id, file_name, time.time_ns(), secrets.token_hex(8))` SHA256[:32] 生成；每次上传都是新 ID，**不做去重**。
- **重试** —— 失败后用 `POST /file/{file_id}/retry/{stage}`，不要重新上传（会产生新记录）。
- **配置隔离** —— `extraction_field` / `analysis_rule` 按 `type_id` 隔离；`field_id` / `rule_id` 全局唯一，跨 `type_id` 冲突返回 409。
- **回调** —— `POST /file/parse` / `retry` 携带 `callback_url` 时，每阶段开始 + `stage_done` + 每条 `field_done`/`rule_done` 都会 POST 通知（超时 2.5s，失败仅 warning）。
- **SSE 流** —— `mode=stream` 与 `/extraction/test/stream` / `/analysis/test/stream` 返回 `text/event-stream`。详细事件序列见 `docs/API_DOCUMENTATION.md` 第 9 节。
- **VL 抽取** —— `source_type=vl` 字段绕过 MinerU Markdown，直接读 `uploads/{file_id}.pdf`，由视觉模型一次产出 `{value, reason}` JSON，不再走文本 LLM 二次抽取。
""".strip()


TAGS = [
    {"name": "doctype", "description": "文档类型管理：创建/列出/删除/复制/导出/导入。"},
    {"name": "file", "description": "文件解析、查询、删除、重试、结果获取。"},
    {"name": "extraction", "description": "字段提取配置 CRUD 与调试。"},
    {"name": "analysis", "description": "逻辑分析规则 CRUD 与调试。"},
    {"name": "search", "description": "Milvus 向量相似度检索。"},
]


# ─── 每个 (path, method) 的丰富信息 ──────────────────────────
# summary 简短，description 详细带使用约束。examples 用于请求体。

ENRICHMENTS: Dict[str, Dict[str, Dict[str, Any]]] = {
    # ─── doctype ───────────────────────────────────────────
    "/doctype/list": {
        "get": {
            "summary": "列出所有文档类型",
            "description": (
                "返回所有文档类型，附带各类型下的 `file_count` / `field_count` / `rule_count`。\n\n"
                "排序：默认类型在前，其余按 `created_at` 升序。"
            ),
        }
    },
    "/doctype": {
        "post": {
            "summary": "新增/更新文档类型",
            "description": (
                "按 `type_id` upsert：已存在则更新 `type_name` / `description` / `enabled`，"
                "不存在则新建（`is_default` 强制为 0）。\n\n"
                "默认类型（`is_default=1`）禁止通过本接口创建或改 `is_default`。"
            ),
        }
    },
    "/doctype/{type_id}": {
        "delete": {
            "summary": "删除文档类型",
            "description": (
                "**默认类型不可删除**。非默认类型若有关联数据：\n"
                "- 不传 `force` → 返回 409 + 数量提示\n"
                "- `force=true` → 级联删除该类型下所有文件、Milvus 向量、`uploads/{file_id}.pdf`、字段、规则\n\n"
                "返回 `data.deleted_files/fields/rules` 仅在 `force=true` 时非零。"
            ),
        }
    },
    "/doctype/{type_id}/copy_from": {
        "post": {
            "summary": "同实例跨类型复制配置",
            "description": (
                "把源类型的字段+规则复制到目标 `type_id`（生成全新 ID 的独立副本）。\n\n"
                "- `field_ids` / `rule_ids` 留空表示全部复制\n"
                "- `on_conflict=skip`：目标已有同名时跳过\n"
                "- `on_conflict=rename`（默认）：自动加 ` (副本)` 后缀\n"
                "- 规则 `depend_fields` 按 **field_name** 在目标类型重映射到新的 `field_id`；"
                "找不到同名字段的依赖记入 `missing_dependencies` 返回，规则照常创建。"
            ),
        }
    },
    "/doctype/{type_id}/export": {
        "get": {
            "summary": "导出配置为 JSON 载荷",
            "description": (
                "把目标类型的字段+规则序列化为可跨实例迁移的 JSON。\n\n"
                "规则依赖以 **field_name** 列表（`depend_field_names`）序列化，避免依赖原 `field_id`。"
            ),
        }
    },
    "/doctype/import": {
        "post": {
            "summary": "从 JSON 载荷导入配置",
            "description": (
                "把 `GET /doctype/{type_id}/export` 产生的载荷导入到目标类型。\n\n"
                "- `target_type_id` 为空时使用 `payload.type_id`\n"
                "- 目标类型不存在且 `create_type_if_missing=true` → 自动创建\n"
                "- 字段始终生成**新 `field_id`**，避免与全局唯一约束冲突\n"
                "- 规则按 `depend_field_names` 在目标类型重映射；缺失的依赖进 `missing_dependencies`"
            ),
        }
    },

    # ─── file ──────────────────────────────────────────────
    "/file/parse": {
        "post": {
            "summary": "提交文件解析",
            "description": (
                "上传文件并启动 6 阶段处理管线。\n\n"
                "**`mode` 行为差异**\n"
                "- `async`（默认）：后台任务，立即返回 `file_id`，可配合 `callback_url` 监听阶段事件\n"
                "- `sync`：阻塞直到全部完成或失败\n"
                "- `stream`：返回 `text/event-stream`，事件序列见 `API_DOCUMENTATION.md` 第 9.1 节\n\n"
                "**`file_id` 生成**：`SHA256[:32]((type_id, file_name, time.time_ns(), token_hex(8)))` —— "
                "每次上传都是新 ID，**不做去重**。失败重试请用 `POST /file/{file_id}/retry/{stage}`。\n\n"
                "原始 PDF 字节会同步写到 `uploads/{file_id}.pdf`（供 VL 抽取使用），写盘失败不阻断主管线。"
            ),
        }
    },
    "/file/list": {
        "get": {
            "summary": "分页查询文件列表",
            "description": (
                "按 `create_time desc` 分页。`status` 与 `type_id` 均为精确匹配，空表示不过滤。"
            ),
        }
    },
    "/file/batch": {
        "delete": {
            "summary": "批量删除文件",
            "description": (
                "删除请求体中所有 `file_id` 关联的 MySQL 记录、Milvus 向量、`uploads/{file_id}.pdf`。\n\n"
                "不存在的 `file_id` 不报错，会出现在响应 `failed_ids` 中。Milvus / PDF 清理失败"
                "仅记 warning，不影响 MySQL 删除。"
            ),
        }
    },
    "/file/{file_id}/status": {
        "get": {
            "summary": "查询文件处理进度",
            "description": "返回当前 `progress` 与最近的 `error`。完整时间戳请走 `/file/{file_id}/detail`。",
        }
    },
    "/file/{file_id}/detail": {
        "get": {
            "summary": "文件完整详情",
            "description": "包含 `start_*_time` / `end_*_time` 全套时间戳，可计算每阶段耗时。",
        }
    },
    "/file/{file_id}": {
        "delete": {
            "summary": "删除文件",
            "description": (
                "级联删除：`files` / `file_content` / `file_table` / `file_chunk` / "
                "`extraction_result` / `analysis_result` / Milvus 向量 / `uploads/{file_id}.pdf`。\n\n"
                "Milvus / PDF 清理失败仅 warning。"
            ),
        }
    },
    "/file/{file_id}/retry/{stage}": {
        "post": {
            "summary": "从指定阶段重试",
            "description": (
                "清掉指定阶段及其下游已有数据，从该阶段重跑。\n\n"
                "**有效阶段**: `tableing` / `chunking` / `embedding` / `extracting` / `analyzing`\n\n"
                "兼容旧别名 `table_name_validating` → `tableing`。\n\n"
                "`mode=stream` 返回的事件序列与 `/file/parse?mode=stream` 一致（额外可能有 `resume` 事件）。"
            ),
        }
    },
    "/file/{file_id}/retry/extracting": {
        "post": {
            "summary": "快捷重试：字段提取",
            "description": "等价于 `POST /file/{file_id}/retry/extracting`。",
        }
    },
    "/file/{file_id}/retry/analyzing": {
        "post": {
            "summary": "快捷重试：逻辑分析",
            "description": "等价于 `POST /file/{file_id}/retry/analyzing`。",
        }
    },
    "/file/{file_id}/tables": {
        "get": {
            "summary": "文件表格列表",
            "description": (
                "返回 `file_table` 中按 `table_index` 升序的全部表格。\n\n"
                "`table_name` 由 LLM 在 `tableing` 阶段从表前文本识别得出（截断 30 字），"
                "`page_num` 是该表格在 PDF 中的页号（可能是范围如 `\"3-4\"`）。"
            ),
        }
    },
    "/file/{file_id}/chunks": {
        "get": {
            "summary": "文件分块列表",
            "description": "返回 `file_chunk` 按 `chunk_index` 升序的全部分块。表格作为独立 chunk 不拆分。",
        }
    },
    "/file/{file_id}/outline": {
        "get": {
            "summary": "文件章节大纲",
            "description": (
                "正则解析 Markdown 章节标题，与 `extraction.search_type=section` 使用同一套切片口径"
                "—— 前端看到什么 = 抽取时能匹配到什么。\n\n"
                "文件不存在或内容为空返回 `[]`（不返回 404），与 `/tables`、`/chunks` 一致。"
            ),
        }
    },
    "/file/{file_id}/extraction": {
        "get": {
            "summary": "字段提取结果",
            "description": (
                "返回 `extraction_result` 全表行。\n\n"
                "注意：`source_refs`（参考块/VL 元数据）虽然存在数据库里，但本接口当前**不返回**该字段；"
                "如需调试细节请走 `/extraction/test` 或 `/extraction/test/stream`。"
            ),
        }
    },
    "/file/{file_id}/analysis": {
        "get": {
            "summary": "逻辑分析结果",
            "description": "返回 `analysis_result` 全表行（含 `input_values` 但不含 `source_refs`）。",
        }
    },

    # ─── extraction ────────────────────────────────────────
    "/extraction/fields": {
        "get": {
            "summary": "列出字段配置",
            "description": "按 `priority` 升序。`type_id` 为空时返回全量；非空时按精确匹配过滤。",
        },
        "post": {
            "summary": "新增/更新字段配置（upsert）",
            "description": (
                "按 `field_id` 全局唯一 upsert。\n\n"
                "**冲突处理**：若 `field_id` 已存在但归属其他 `type_id`，返回 **409**，"
                "提示换 ID 或先删除原记录。\n\n"
                "**Prompt 校验**（Pydantic 层）\n"
                "- `source_type=text` 时 `text_extract_prompt` 必须含至少一个 `<search_result>标签</search_result>`\n"
                "- `source_type=table` 时 `table_extract_prompt` 同上\n"
                "- `source_type=vl` 时 `vl_method` 与 `vl_extract_prompt` 必填，"
                "且 prompt 必须含 `value` 和 `reason` 关键字（大小写不敏感）\n"
                "- `vl_progressive` 的自定义 `batch_prompt_template` 必含 "
                "`{field_hints}` `{page_label}` `{total_pages}` `{history}`\n"
                "- `vl_locate` 的自定义 `locate_prompt_template` 必含 "
                "`{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}`\n"
                "- 模板字面 `{ }` 需写成 `{{ }}` 转义（`str.format()` 渲染）"
            ),
        }
    },
    "/extraction/fields/{field_id}": {
        "delete": {
            "summary": "删除字段配置",
            "description": "**硬删除**（直接从表中 DELETE，不再走 `enabled=0` 的软删除路径）。",
        }
    },
    "/extraction/fields/{field_id}/check": {
        "get": {
            "summary": "检查字段 ID 是否存在",
            "description": "用于前端在保存前检测 ID 重复。",
        }
    },
    "/extraction/test": {
        "post": {
            "summary": "字段提取调试（同步）",
            "description": (
                "两种模式：传 `field_id` 加载已保存配置，或传 `config` 用临时配置；二选一。\n\n"
                "**响应差异**\n"
                "- 普通字段：`search_results` 是检索片段，`llm_input` 是渲染后的 prompt，"
                "`llm_output` 是 LLM 原始响应，`extracted_value`/`reason` 是解析后结果\n"
                "- VL 字段：`search_results` 是单元素数组 `[{type:\"vl_meta\", method, key_pages, "
                "vl_total_tokens, batches_with_info}]`；`llm_output` = `extracted_value`（VL 直出 JSON）"
            ),
        }
    },
    "/extraction/test/stream": {
        "post": {
            "summary": "字段提取流式调试（SSE）",
            "description": (
                "通过 SSE 分步推送检索结果 → prompt → LLM/VL 响应 → 最终结果。\n\n"
                "事件类型清单见 `docs/API_DOCUMENTATION.md` 第 9.2 节。"
                "VL 字段的进度事件（`pdf_loaded` / `progressive_batch` / `locate_locate` / `locate_extract`）"
                "**仅** SSE 推送，不走异步 `callback_url`。"
            ),
        }
    },

    # ─── analysis ──────────────────────────────────────────
    "/analysis/rules": {
        "get": {
            "summary": "列出分析规则",
            "description": "按 `priority` 升序。`type_id` 为空时返回全量。",
        },
        "post": {
            "summary": "新增/更新分析规则（upsert）",
            "description": (
                "按 `rule_id` 全局唯一 upsert。`rule_id` 已被其他 `type_id` 占用时返回 **409**。\n\n"
                "**校验**：`expression` 必须包含至少一个 `<field_result>字段ID</field_result>` 占位符。\n\n"
                "`system_prompt` 仅 `judge` 类型生效；`calc` 类型直接用 `numexpr` 计算，"
                "结果按 `analysis.calc_precision`（默认 2 位）保留小数。"
            ),
        }
    },
    "/analysis/rules/{rule_id}": {
        "delete": {
            "summary": "删除分析规则",
            "description": "**硬删除**。",
        }
    },
    "/analysis/rules/{rule_id}/check": {
        "get": {
            "summary": "检查规则 ID 是否存在",
            "description": "用于前端在保存前检测 ID 重复。",
        }
    },
    "/analysis/test": {
        "post": {
            "summary": "逻辑分析调试（同步）",
            "description": (
                "两种模式：传 `rule_id` 加载已保存规则，或传 `config` 用临时配置；二选一。\n\n"
                "返回 `input_values`（依赖字段提取值）、`expression_resolved`（占位符替换后的表达式）、"
                "`result_value` 与 `reason`。"
            ),
        }
    },
    "/analysis/test/stream": {
        "post": {
            "summary": "逻辑分析流式调试（SSE)",
            "description": (
                "SSE 分步推送：`input_values` → `resolved_expression` → "
                "（judge: `prompt` → `llm_response`）→ `result` → `done`。\n\n"
                "calc 类型无 `prompt` / `llm_response` 步骤。"
            ),
        }
    },

    # ─── search ────────────────────────────────────────────
    "/search": {
        "post": {
            "summary": "向量相似度检索",
            "description": (
                "将 `query` 通过 embedding 接口向量化后在 Milvus 中检索，返回分块及 L2 距离 `score`（越小越相似）。\n\n"
                "- `file_id` 限定检索范围\n"
                "- `score_threshold` 上限，超过该距离的结果被过滤\n"
                "- 返回字段包含 `chunk_id` / `file_id` / `chunk_index` / `total_chunks` / "
                "`chunk_content` / `start_pos` / `end_pos` / `page_num` / `score`"
            ),
        }
    },
}


# ─── 入口 ──────────────────────────────────────────────────

def build_app() -> FastAPI:
    """构造一个无 lifespan 的 FastAPI，仅注册路由用于导出 schema。"""
    app = FastAPI(
        title="文档解析与逻辑分析系统",
        version="0.2.0",
        description=API_INFO_DESCRIPTION,
    )
    register_routers(app)
    return app


def enrich(schema: Dict[str, Any]) -> Dict[str, Any]:
    """对生成的 openapi schema 做就地丰富。"""
    schema["tags"] = TAGS
    schema.setdefault("servers", []).append({
        "url": "http://localhost:5019",
        "description": "本地开发",
    })

    paths = schema.get("paths", {})
    enriched = 0
    for path, ops in ENRICHMENTS.items():
        if path not in paths:
            print(f"  [warn] 路径不在 schema 中: {path}")
            continue
        for method, fields in ops.items():
            op = paths[path].get(method)
            if not op:
                print(f"  [warn] 方法不在 schema 中: {method.upper()} {path}")
                continue
            op.setdefault("summary", fields.get("summary", ""))
            if "summary" in fields:
                op["summary"] = fields["summary"]
            if "description" in fields:
                # 已有 docstring 描述时拼接，避免丢失代码里的注释
                existing = op.get("description", "").strip()
                op["description"] = (
                    fields["description"]
                    if not existing
                    else f"{fields['description']}\n\n---\n\n_原 docstring_:\n\n{existing}"
                )
            enriched += 1

    print(f"  enriched {enriched} operations")
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
