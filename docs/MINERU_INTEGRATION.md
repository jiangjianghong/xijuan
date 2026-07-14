# MinerU 解析集成说明（请求 → 返回 → 后处理）

> 本文档描述本系统当前对 MinerU 解析服务的完整调用实现，供其他 AI / 开发者对接或复刻时参考。
> 涉及代码：`service/mineru_client.py`（HTTP 调用）、`service/parse_service.py`（业务封装）、`utils/page_mapping.py`（后处理）、`utils/config.py`（配置）。

## 1. 总览

MinerU 是外部部署的 PDF 解析服务。本系统在管线的 **parsing 阶段**调用它，将 PDF 二进制转换为 Markdown 全文（`md_content`）和结构化布局信息（`middle_json`），随后基于二者构建「markdown 位置 → PDF 页码/bbox」映射（`page_mapping`），三者一并落库到 `file_content` 表。

```
上传 PDF（blue_print/file_router.py，校验 max_file_size）
        │ file_content_bytes
        ▼
parse_service.parse_file()        ← 更新 files.progress = "parsing"，记录开始时间
        │
        ▼
mineru_client.parse_pdf()         ← POST {base_url}/file_parse（multipart）
        │ 同步等待响应（单次 HTTP 请求，无轮询）
        ▼
{md_content, middle_json}
        │
        ▼
page_mapping.build_page_mapping() ← middle_json + md_content → 位置/页码/bbox 映射
        │                           （文本块前缀锚定 + 表格块 <table 字面量锚定）
        ▼
parse_service.save_file_content() ← 写入 file_content 表（md + middle_json + page_mapping）
```

## 2. 配置（`configs/config.yaml` → `MineruConfig`）

```yaml
mineru:
  base_url: "http://36.151.147.207:7078"   # MinerU 服务地址
  backend: "vllm-async-engine"             # 解析后端，随表单透传给 MinerU
  queue_width: 1                           # 预留字段，当前代码未引用
  parse_timeout: 1200                      # HTTP 超时（秒），整个解析请求的等待上限
  max_file_size: 104857600                 # 上传文件大小上限（字节），100MB，在上传路由校验
```

Pydantic 模型默认值（`utils/config.py:25`）：`base_url="http://localhost:8888"`、`backend="vllm-async-engine"`、`parse_timeout=300`、`max_file_size=104857600`。

另有**按文档类型覆盖**的 `max_parse_pages`（最大解析页数）：`parse_service.parse_file` 通过 `get_file_type_runtime_config(file_id)` 读取文件所属类型的运行时配置，若设置了 `max_parse_pages` 则只解析前 N 页。

## 3. HTTP 请求细节（`service/mineru_client.py:parse_pdf`）

**接口**：`POST {base_url}/file_parse`（`base_url` 去尾部 `/` 后拼接）

**请求方式**：`httpx.AsyncClient` 单次 POST，`timeout=parse_timeout`（默认配置 1200s）。**同步等待整个解析完成**——没有任务 ID、没有轮询，MinerU 在这一个 HTTP 响应里直接返回解析结果。

**multipart 文件部分**：

```python
files = {"files": (file_name, file_content, "application/pdf")}
```

**表单参数（data）**——全部为字符串：

| 参数 | 值 | 说明 |
|---|---|---|
| `return_middle_json` | `"true"` | 要求返回 middle_json（布局结构） |
| `return_model_output` | `"false"` | 不要模型原始输出 |
| `return_md` | `"true"` | 要求返回 markdown |
| `return_content_list` | `"true"` | 要求返回 content_list（阅读序内容列表，建页码映射用） |
| `return_images` | `"false"` | 不要图片 |
| `start_page_id` | `"0"` | 从第 0 页开始 |
| `end_page_id` | `str(max_parse_pages - 1)` 或 `"99999"` | 有页数限制时为「页数-1」（0-indexed 闭区间）；无限制时给一个大值表示全部页 |
| `parse_method` | `"auto"` | 解析方法 |
| `lang_list` | `"ch"` | 中文 |
| `output_dir` | `"./{file_id}"`（无 file_id 时 `"."`） | MinerU 侧输出目录隔离 |
| `backend` | 配置中的 `backend` | 如 `vllm-async-engine` / `auto` |

注意：`max_parse_pages <= 0` 会被归一化为 `None`（即解析全部页）。

## 4. 响应格式与解析

**MinerU 响应（JSON）**：

```json
{
  "results": {
    "<文件名（无后缀）>": {
      "md_content": "...markdown 全文...",
      "middle_json": "...（可能是 JSON 字符串，也可能直接是 dict）..."
    }
  }
}
```

**客户端解析逻辑**：

1. `resp.raise_for_status()` —— 非 2xx 直接抛 `httpx.HTTPStatusError`。
2. 取 `result["results"]` 中的**第一个 value**（不按文件名 key 查找，`next(iter(results.values()))`）。
3. `md_content` 缺失时取空字符串。
4. `middle_json` **兼容 dict 和 str 两种形态**：是 dict 则 `json.dumps(..., ensure_ascii=False)` 转字符串；统一以字符串形式向上返回/落库。
5. `results` 为空时返回 `{"md_content": "", "middle_json": ""}`（不报错）。

**返回值**：`{"md_content": str, "middle_json": str}`。

## 5. 业务封装与状态管理（`service/parse_service.py`）

`parse_file(file_path, file_content_bytes, file_id, session)`：

1. 先把 `files.progress` 置为 `"parsing"`，写 `start_parsing_time`，commit。
2. 读取 mineru 配置 + 文件类型的 `max_parse_pages`，调用 `parse_pdf`。
3. 成功：写 `end_parsing_time`，返回 `(content, middle_json_str)`。**注意此处不改 progress**——进入下一阶段（tableing）时由管线置位。
4. 失败：捕获任何异常，置 `progress="parsing_failed"`、写格式化后的错误信息到 `files.error`，commit 后 **re-raise**（由管线层处理回调/SSE error 事件）。

`save_file_content(file_id, content, session, middle_json, page_mapping)`：upsert 到 `file_content` 表（已有记录则覆盖三个字段，否则插入新行）。

## 6. middle_json 后处理：page_mapping（`utils/page_mapping.py`）

2026-07 起页码映射优先走 `build_page_mapping_auto`：MinerU 返回 `content_list`
（每项带 page_idx 与 1000×1000 归一化 bbox，md 即按其顺序渲染）时逐项顺序重放定位，
bbox 乘 page_size/1000 反归一化后落库；content_list 缺失或重放产出为空时降级为
原 middle_json 前缀匹配算法。content_list 用后即弃，不落库。

解析完成后，管线层（`pipeline_service.py`）调用：

```python
page_mapping = build_page_mapping_auto(content, middle_json_str, content_list_str)
```

**middle_json 关键结构**（MinerU 输出）：

```json
{
  "pdf_info": [
    {
      "page_idx": 0,                  // 0-indexed 页码
      "page_size": [w, h],
      "para_blocks": [
        {
          "bbox": [x0, y0, x1, y1],   // 块级框
          "lines": [{"spans": [{"content": "文本片段"}]}]
        },
        {
          "type": "table",            // 表格块：无 lines/spans 文本
          "bbox": [x0, y0, x1, y1]    // 整表框
        }
      ]
    }
  ]
}
```

**构建算法**（前向扫描锚定，两种锚点）：

1. 逐页遍历 `para_blocks`，维护单调前进的游标 `cursor`。
2. **表格块**（`type == "table"`，无 lines/spans 文本，提不出前缀）：在 `md_content` 中从 `cursor` 处前向 `find("<table")` 字面量定位，命中则记录锚点并挂**整表 bbox**；找不到 `<table` 字面量或块无 bbox 时不产锚点（容错跳过）。
3. **文本块**：提取纯文本（所有 span 的 `content` 用空格拼接），少于 3 个字符的块跳过；依次用块文本的前 50 / 30 / 20 字符前缀从 `cursor` 处 `find` 定位，都失败再试前 10 字符。
4. 命中则记录一条映射并把游标推进到 `pos + 1`（保证单调前进，避免回头错配）。
5. 映射项结构：`{"start_pos", "end_pos", "page_num"(1-indexed), "bbox", "page_size"}`，其中 `bbox`/`page_size` 在 middle_json 缺失时不带（存量老数据全部不带）；文本块 bbox 为该段落块的框，表格块 bbox 为整表框。坐标系为左上原点，与 `page_size` 同一单位，前端按 `canvas尺寸 / page_size` 线性缩放画框。
6. 最终按 `start_pos` 排序返回。

**配套查询函数**（供下游 tableing/chunking/extraction 用）：

- `lookup_page_num(mapping, start_pos, end_pos)` → 二分查找返回 `"1"` 或跨页 `"1-3"`；映射为空返回 `""`。
- `lookup_bboxes(mapping, start_pos, end_pos)` → 返回命中范围内的 `[{page_num, bbox, page_size}]` 块级框列表（无 bbox 的老数据条目跳过），用于前端 PDF 高亮（`source_refs.bboxes`）。

## 7. 错误与边界行为汇总

| 情形 | 行为 |
|---|---|
| 上传文件超过 `max_file_size` | 上传路由返回 `code=400`「文件大小超过限制 (100MB)」，不进入解析 |
| MinerU 返回非 2xx | `raise_for_status` 抛异常 → `parsing_failed` + error 落库 + re-raise |
| HTTP 超时（> `parse_timeout` 秒） | httpx 抛 `TimeoutException` → 同上 |
| 响应 `results` 为空 | 返回空 md / 空 middle_json，不报错（下游会得到空内容） |
| `middle_json` 为 dict | 自动 `json.dumps` 转字符串 |
| `middle_json` 为空 | `page_mapping` 直接为 `[]`，下游页码/bbox 功能降级（页码为空串、无高亮框） |
| `max_parse_pages <= 0` | 视为不限页数 |

## 8. 对接要点（给复刻方的提示）

- MinerU 接口是**一次性同步 HTTP**，长文档解析时间全部消耗在这一个请求上，务必设置足够大的客户端超时（本系统生产配置 1200s）。
- 响应中 `results` 的 key 是「文件名去后缀」，但实现上**不依赖 key**，直接取第一个 value——一次只传一个文件。
- `middle_json` 的 dict/str 二态必须兼容（不同 MinerU 版本/部署行为不一致）。
- `page_mapping` 不是 MinerU 直接给的，而是本系统自建的：优先用 `content_list`（阅读序 + page_idx + 归一化 bbox）顺序重放定位，降级时用 `md_content` × `middle_json.pdf_info[].para_blocks` 前缀匹配（文本块靠文本前缀、表格块靠 `<table` 字面量并挂整表 bbox）；它是后续表格页码、分块页码、抽取结果 bbox 高亮的唯一来源。
- 降级用的 middle_json 前缀匹配算法在长文档 + 重复公文套话下会因 cursor 单调误跳产生大面积页码错位（436 页文档实测仅建出 116 锚/69 页），content_list 路径根治此问题；该路径上线前解析的存量文件不自动修复（需重新解析）。
- 解析阶段失败的文件通过 `POST /file/{file_id}/retry/parsing` 重试（需重新提交原始文件内容，见 `pipeline_service.py:1169`）。
