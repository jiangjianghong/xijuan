# 析卷智能体

`xijuan_agent` 是接入现有 FastAPI 服务的文档研究工作台。每条消息可以引用多个已处理完成的文件，Agent 会使用同一个 OpenAI 兼容模型流式回答、发起工具调用，并可将原始 PDF 页图交给模型视觉分析。页面里的对话和上下文状态只保留在当前浏览器内存中，刷新后清空。

## 配置

在系统设置的「智能体 Agent」分组配置模型地址、模型名称和 API Key。模型服务需兼容 `chat/completions` 流式输出、function calling，以及 OpenAI `image_url` data URL 图像输入。工具调用和 PDF 图片阅读共用该模型。

`agent.max_rounds` 默认为 25，可在设置页调整为 1 到 100。每次 `pdf_reader` 调用最多读取 8 页。原始 PDF 仍按现有 `vl_model.pdf_storage_dir` 配置存放，并受 PDF 保留策略管理。

上下文采用滑动窗口：`agent.context_max_tokens` 默认 24000，是应用对模型请求的估算预算，包含消息、工具定义和最大输出预留；不会自动探测模型的真实容量，请按所用模型配置。文本按序列化字符数的一半估算，PDF 页图每页暂估 1536 tokens，并非精确 tokenizer 计数。

预计请求达到 `context_compress_threshold`（默认 18000）且存在可整理的旧证据时，由同一个 Agent 模型在后台生成摘要；预算内的研究继续执行，只有下一次请求将超过上限时才等待摘要。最近 `context_recent_tool_rounds`（默认 4）组完整工具调用通常保留原文，预算不足时可继续整理最近组；同一个问题内部也能整理较早工具组。摘要只在请求边界替换对应快照，不丢弃期间新增消息，不重复摘要没有新增证据的记忆。无法满足估算预算时停止发起请求并提示调整设置。回答结束时合并已完成摘要，尚未完成的任务取消，后续提问再按需启动；中止、断连和异常同样清理后台任务。

`agent.tool_result_max_chars` 默认 20000 字符，可在设置页「智能体 → 单次工具结果上限」调整为 1000–200000。限制作用于每次工具调用的最终文本（JSON 结果包含转义及元数据），超长结果保留截断标记及可用来源；正常、异常、PDF 文本说明均统一检查。章节、表格和页文本使用该配置，页文本也可显式请求更小窗口。PDF 图片不按字符截断，其输入消耗仍计入上下文估算；尚未被模型读取的页图不会被替换成文字占位摘要，页图本身超出预算时明确报错。`search_context` 的邻近窗口、`search_rule` 的扩展长度和搜索返回条数仍是各工具独立的检索参数。

## 工作台与 API

- `GET /agent`：独立工作台页面，复用现有管理员会话、API Key 或 IP 白名单鉴权。
- `GET /agent/files?q=...`：只返回 `complete` 文件，供工作台搜索、多选并生成引用标签。
- `POST /agent/stream`：SSE 智能体运行接口，请求体为 `{ "file_ids": ["..."], "question": "...", "history": [...] }`；旧客户端传单个 `file_id` 仍兼容。
- 事件包含 `status`、`context_ready`、`context_window`、`context_compact`、`token`、`tool_call_delta`、`tool_call`、`tool_result`、`history_state`、`done` 和 `error`。`context_window` 提供 `estimated_tokens`（消息）、`estimated_request_tokens`（含工具定义及输出预留）和 `max_tokens`（总预算）；`context_compact` 通过 `task_id` 与 `state=started/waiting/completed/skipped/cancelled` 更新同一条整理状态。客户端断开流即可中止当前运行；页面默认收起工具调用详情。

## 工具

- 文本检索：`search_context`、`search_rule`、`search_chunk_db`、`search_bm25`、`search_vector_db`、`search_page`。
- 章节：`list_sections` 列出标题和层级，`read_section` 按章节索引读取完整章节。
- 表格：`list_tables` 列出表名和索引，`read_table` 按索引读取内容，不运行表名匹配模式。
- 外部与视觉：`web_search` 调用已配置的博查搜索；`pdf_reader` 按页码范围渲染页图，并将图像放回同一 Agent 模型上下文。

文档在当前对话中持续引用，无需每条消息重新添加；新对话会清空文件范围。每次文档工具调用都会显式携带文件范围：批量检索、章节/表格列表传 `file_ids`（默认传入当前对话已添加的全部文件，结果标注 `file_id/file_name`）；章节、表格和 `pdf_reader` 精读传单个 `file_id`。服务端会拒绝不属于当前对话引用范围的 ID。章节和表格先列索引，再用 `file_id + index` 精确读取。各工具的参数、边界和结果语义定义在 `tools.py` 的 OpenAI function schemas 中。

Agent 模型请求支持 `agent.extra_params`，设置页“附加参数”会把 JSON 对象合并到 Chat Completions 顶层请求中。流式响应里的 `reasoning_content`、`reasoning`、`analysis` 或 `thinking` 增量会通过 SSE 独立发送，工作台在默认收起的“思考过程”区域显示。
