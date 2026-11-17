# 析卷智能体

`xijuan_agent` 是接入现有 FastAPI 服务的文档研究工作台。每条消息可以引用多个已处理完成的文件，Agent 会使用同一个 OpenAI 兼容模型流式回答、发起工具调用，并可将原始 PDF 页图交给模型视觉分析。页面里的对话和上下文状态只保留在当前浏览器内存中，刷新后清空。

## 配置

在系统设置的「智能体 Agent」分组配置模型地址、模型名称和 API Key。模型服务需兼容 `chat/completions` 流式输出、function calling，以及 OpenAI `image_url` data URL 图像输入。工具调用和 PDF 图片阅读共用该模型。

`agent.max_rounds` 默认为 25，可在设置页调整为 1 到 100。每次 `pdf_reader` 调用最多读取 8 页。原始 PDF 仍按现有 `vl_model.pdf_storage_dir` 配置存放，并受 PDF 保留策略管理。

上下文采用滑动窗口：默认估算上限为 24000 tokens，达到 18000 tokens 时用同一个 Agent 模型摘要较早的对话和工具结果，最近 4 组工具调用保留原文。`context_max_tokens`、`context_compress_threshold` 和 `context_recent_tool_rounds` 都可在设置页调整。

## 工作台与 API

- `GET /agent`：独立工作台页面，复用现有管理员会话、API Key 或 IP 白名单鉴权。
- `GET /agent/files?q=...`：只返回 `complete` 文件，供工作台搜索、多选并生成引用标签。
- `POST /agent/stream`：SSE 智能体运行接口，请求体为 `{ "file_ids": ["..."], "question": "...", "history": [...] }`；旧客户端传单个 `file_id` 仍兼容。
- 事件包含 `status`、`context_ready`、`context_window`、`context_compact`、`token`、`tool_call_delta`、`tool_call`、`tool_result`、`history_state`、`done` 和 `error`。客户端断开流即可中止当前运行；页面默认收起工具调用详情。

## 工具

- 文本检索：`search_context`、`search_rule`、`search_chunk_db`、`search_bm25`、`search_vector_db`、`search_page`。
- 章节：`list_sections` 列出标题和层级，`read_section` 按章节索引读取完整章节。
- 表格：`list_tables` 列出表名和索引，`read_table` 按索引读取内容，不运行表名匹配模式。
- 外部与视觉：`web_search` 调用已配置的博查搜索；`pdf_reader` 按页码范围渲染页图，并将图像放回同一 Agent 模型上下文。

文档在当前对话中持续引用，无需每条消息重新添加；新对话会清空文件范围。每次文档工具调用都会显式携带文件范围：批量检索、章节/表格列表传 `file_ids`（默认传入当前对话已添加的全部文件，结果标注 `file_id/file_name`）；章节、表格和 `pdf_reader` 精读传单个 `file_id`。服务端会拒绝不属于当前对话引用范围的 ID。章节和表格先列索引，再用 `file_id + index` 精确读取。各工具的参数、边界和结果语义定义在 `tools.py` 的 OpenAI function schemas 中。

Agent 模型请求支持 `agent.extra_params`，设置页“附加参数”会把 JSON 对象合并到 Chat Completions 顶层请求中。流式响应里的 `reasoning_content`、`reasoning`、`analysis` 或 `thinking` 增量会通过 SSE 独立发送，工作台在默认收起的“思考过程”区域显示。
