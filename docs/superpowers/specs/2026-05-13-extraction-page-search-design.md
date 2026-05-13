# 文本抽取新增 page 检索方式 — 设计方案

**日期：** 2026-05-13
**作者：** brainstorming session
**状态：** 待评审

## 1. 背景

现有 text 类字段提供 5 种检索方式：`context` / `section` / `rule` / `chunk_db` / `vector_db`，全部围绕"关键词/语义过滤"展开。当用户已知目标信息在某固定页码范围（例如合同的"声明页 5-7"、报告的"附录页 20-25"），上述方法都需要绕道关键词匹配或向量召回，存在三个问题：

- 没有关键词命中时返回空，触发字段 failed
- 关键词命中位置散落多处，模型反而被噪声内容干扰
- 用户已知答案位置，仍被迫先想关键词，体验绕

**目标：** 让用户能直接声明"用第 N-M 页解析出来的 markdown 文本回答"，跳过任何检索过滤。

## 2. 总体方案

在 text source 下新增第 6 种 `search_type = "page"`，与现有 5 种并列。核心动作：

1. 读 `file_content.file_content`（md 全文）+ `file_content.page_mapping`
2. 解析 `search_config.page_range` → 起止页码
3. 通过 `page_mapping` 反查页码 → markdown 字符位置区间
4. 切片得到目标文本 → 若长度 > `max_length` 末尾截断 → 替换 prompt 占位符 `<search_result>page_content</search_result>` → 调 LLM → 解析 JSON
5. 写 `extraction_result`，`source_refs` 携带页码区间与是否截断的元信息

与 `vl_model` 系列区别：VL 走 PDF 原图给视觉模型，page 走 MinerU 解析后的 markdown 文本。
与 `chunk_db` 区别：chunk_db 按 chunk 边界 + 关键词过滤；page 按页码边界一段连续文本，无任何关键词过滤。

## 3. 数据模型与配置

**无 ORM 变更。** 沿用 `ExtractionField.search_type` (字符串) + `search_config` (JSON)。

### `search_config` schema（page 方法）

```json
{
  "page_range": "5-7",      // 必填；单一连续区间；格式 "N" 或 "N-M"；N>=1, N<=M, 1-indexed
  "max_length": 30000       // 选填；int>0；默认 30000；切片超长时从末尾截断
}
```

### 校验

- `page_range` 缺失 / 格式不合法 → 字段 failed，reason 含原值
- `N < 1` 或 `N > M` → 同上
- `max_length` 缺失用默认 30000；<=0 视为非法

### 文档类型隔离

`page` 方法依然按 `file.type_id` 隔离，不破坏现有多文档类型规则。

## 4. 切片算法

新增 `slice_by_page_range(md: str, page_mapping: list, start_page: int, end_page: int, max_length: int) -> dict`。

```
1. 找第一条 page_num >= start_page 的记录 → slice_start = 其 start_pos
   找不到 → 返回 {ok: False, reason: "页码 N-M 不在文档范围内"}
2. 找第一条 page_num > end_page 的记录 → slice_end = 其 start_pos
   找不到（end_page 是末页或越界）→ slice_end = len(md)
3. slice = md[slice_start:slice_end]
4. truncated = False
   if len(slice) > max_length:
       slice = slice[:max_length]
       truncated = True
5. 返回 {
     ok: True,
     text: slice,
     start_pos: slice_start,
     end_pos: slice_start + len(slice),
     length: len(slice),
     truncated: truncated,
   }
```

退化场景：
- `page_mapping` 为空（旧文件 / parse 失败但 file_content 存在）→ 直接失败，reason "该文件无 page_mapping，无法按页码取文本"
- 用户填 `100-200`、文档只 50 页 → step 1 找不到 → 失败

## 5. Prompt 占位符与 LLM 调用

完全复用现有 `replace_search_result_placeholders` 机制：

- `results_text_by_label = {"page_content": sliced_text}` —— 单 label，固定名
- prompt 模板里 `<search_result>page_content</search_result>` 被替换为切片文本
- 末尾追加 `JSON_OUTPUT_INSTRUCTION`（与其他文本方法一致）
- 走现有 `chat_completion` + `parse_llm_json_response`，与 `text_system_prompt` 处理逻辑一致

`text_extract_prompt` 不含占位符时，已有的 `validate_prompt_has_placeholder` 会 warning 并返回空，无需改。

## 6. source_refs 与 callback

为与现有 5 种方法兼容，`source_refs` 仍是 `Dict[str, List[Dict]]`：

```json
{
  "page_content": [
    {
      "type": "page",
      "page_range": "5-7",
      "start_pos": 12034,
      "end_pos": 28910,
      "length": 16876,
      "truncated": false,
      "page_num": "5-7"
    }
  ]
}
```

`page_num` 字段直接复用 `page_range` 字符串，让前端"源引用"展示与其他 5 种方法保持一致（前端已知道读 `page_num`）。

Callback `field_done.data.source_refs` 沿用上述结构，**不**改 callback 协议。

## 7. 错误处理矩阵

| 场景 | 表现 |
|---|---|
| `page_range` 缺失 / 不合规（`"a-b"`, `"5-3"`, `"0"`） | value="", reason="page_range 配置非法：<原值>"，success=False |
| 文件无 `file_content`（parsing 未完成） | 与现有 text 方法一致：空返回，上层标 failed |
| `page_mapping` 为空 | value="", reason="该文件无 page_mapping，无法按页码取文本" |
| 指定页码全部超出文档范围 | value="", reason="页码区间 N-M 不在文档范围内" |
| 切片为空（页码合法但 mapping 无对应 block） | 同上 |
| 切片超过 `max_length` | 末尾截断，`source_refs[...].truncated=true`，正常调 LLM；不算失败 |
| LLM 调用失败 | 与现有 5 种方法一致：catch Exception，返回空 + log error |

`max_length` 默认 `30000`，保守地避开常见 32k 上下文上限。

## 8. 测试计划

新增 `tests/test_extraction_page.py`，覆盖：

### 单测

- `slice_by_page_range`
  - 正常区间（中间几页）
  - 单页 `"5"`
  - 末页 / 越界尾部（`end_page` 超过文档末页 → 切到末尾）
  - 起始页越界（`start_page > 文档末页` → ok=False）
  - `page_mapping` 为空 → ok=False
  - `max_length` 触发截断 → `truncated=true`

### 集成测试（mock `chat_completion`）

- `search_type="page"` + 合法 page_range → LLM 被调用一次，prompt 中 `<search_result>page_content</search_result>` 已替换为切片文本
- `max_length=100` 触发截断 → `source_refs.page_content[0].truncated == True`
- 非法 `page_range` → `(value="", reason="page_range 配置非法...", success=False)`
- `page_mapping=[]` → 失败 reason 明确
- `page_range="999-1000"` 越界 → 失败 reason 明确

### 流式回调测试

通过现有 pipeline stream 测试夹具跑一次 page 方法字段，验证 `field_done.data.source_refs.page_content[0].page_num == "5-7"`。

## 9. 影响范围

### 需要改动

| 文件 | 改动 |
|---|---|
| `service/extraction_service.py` | 新增 `slice_by_page_range`、`search_page` 辅助函数；在两处 search_type dispatch（约 line 645、line 1327）加 `elif search_type == "page"` 分支；在两处 `results_text_by_label` 构建（约 line 700、line 1361）加 page 分支；构建 `source_refs` 时为 page 单独处理 |
| `ui/js/...` (字段配置页) | search_type 下拉新增 `page` 选项；展示 page_range / max_length 两个输入框；保存到 search_config |
| `tests/test_extraction_page.py` | 新增 |
| `CLAUDE.md` | 在 Extraction System 一节列出第 6 种 text 方法 `page` |

### 不改动

- `model/tables.py`（无新列）
- `model/schemas.py`（search_config 是 JSON，自由结构）
- `utils/page_mapping.py`（直接复用）
- callback 协议、pipeline 编排、Milvus、MinerU、VL 系列

## 10. 兼容性与回滚

- 旧字段（`search_type` 为 5 种之一）行为完全不变
- 老数据库无需迁移；老 callback 消费者不感知（仅看见 `source_refs[*][*].type == "page"` 的新值）
- 回滚：删除新增的 `search_page` / `slice_by_page_range`，移除两处 dispatch 分支，移除 UI 选项即可，无副作用
