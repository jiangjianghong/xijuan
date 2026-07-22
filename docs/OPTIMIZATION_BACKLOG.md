# 功能优化 Backlog（审查报告）

> 审查日期：2026-07-21
> 方法：11 个功能域并行深读全部源码（约 1.26 万行）→ 产出 81 条优化点 → 每条派独立审查员**回读源码对抗式验证** → 确认 **66 条**、剔除 **15 条**（误报 / 已处理 / 建议站不住）。
> 说明：本文件是**活文档 / backlog**，每条前的 `- [ ]` 可勾选跟踪。证据均为 `文件:行号`（相对仓库根），审查时为准，动手前请以当前代码复核。

## 总体判断

系统功能已相当完整、工程约定统一（config/Pydantic、并发信号量、单例、崩溃恢复都有先例），成熟度不错。核心风险集中在两类：

1. **一层系统性安全缺口**——全站零鉴权 + CORS 通配 + SSRF + 注入 + 存储型 XSS。
2. **几处"静默出错"**——不报错、界面显示成功、结果却是错的（LLM 标点指令腐蚀数值、calc 白名单算错数、MinerU 空结果当成功、向量召回偏低）。

多数高价值项是小改动。建议先清"静默出错 + 安全"，再做性能与一致性。

## ⭐ 快速见效清单（改动小、收益高，建议最先做）

- [ ] **R2** 删除 `extraction_service.py:948` 的"value 不得含英文标点"半句
- [ ] **R9** Milvus `nprobe` 16 → 64 并配置化（`milvus_client.py:144`）
- [ ] **R4** MinerU 空结果抛错 / 置 `parsing_failed`（`mineru_client.py:86`）
- [ ] **R3** 文件名 XSS 转义（`app.js:360`）
- [ ] **R1** 加一层统一 API Key（写/删接口优先）
- [ ] **H3** `files.progress` 加索引（`tables.py:83`）

---

## 🔴 第一优先级：安全 + 静默正确性

- [ ] **R1 · 全站零鉴权 + CORS 通配** — 影响：高 / 工作量：中
  - 证据：`app.py:59`（`allow_origins=["*"]`）、全 router 仅 `Depends(get_db)`、`docs/API_DOCUMENTATION.md:36`（"认证方式：无"）
  - 问题：任何能访问到服务的人可无条件 `DELETE /file`、`DELETE /doctype?force=true`（级联删文件+Milvus+PDF+配置）、读全部抽取结果与原始 PDF、`GET /log/stream` 实时拉取含 LLM 提示词与文档明文的日志；无租户隔离。
  - 建议：FastAPI `APIKeyHeader`/`HTTPBearer` 全局依赖；写/删/日志接口分级；CORS 收敛为前端域名白名单。

- [ ] **R2 · LLM 抽取指令"value 不得含英文标点"腐蚀数值** — 高 / 小
  - 证据：`extraction_service.py:948`（`…value和reason的值中不得含有英文标点符号`）、`:941`
  - 问题：每个 text/table 字段 prompt 末尾都追加此指令。金额 `1,234.56`、日期 `2024-01-01`、比率 `3.5%`、型号 `A-100` 会被模型改写或删标点，且**流入 analyzing 阶段**——calc 用 numexpr 解析被污染的数字直接算错/报错，judge 基于错误值判断。覆盖每个字段、跨阶段放大。
  - 建议：删掉该半句（引号污染已有 `normalize_cjk_quotes` 专门处理），改为"保持原文标点不变"。

- [ ] **R3 · 文件名未转义拼进 HTML，存储型 XSS** — 高 / 小
  - 证据：`app.js:360`、`app.js:220`（`title="${item.file_name}">${item.file_name}` 未转义）、后端 `file_router.py:295` 原样入库
  - 问题：经直连 `/file/parse` 可构造任意 filename（如 `"><img src=x onerror=...>.pdf`），管理端打开列表/队列即执行脚本。同文件 `refreshAllQueue`（`app.js:288`）已转义，属遗漏。
  - 建议：所有 `file_name`/`fileName` 输出点统一走 `Utils.escapeHtml`（文本）与属性转义（title）。

- [ ] **R4 · MinerU 空结果被静默当成解析成功** — 高 / 小
  - 证据：`mineru_client.py:86`（`results` 为空即 `return {"md_content":"",...}`）、`parse_service.py:55`、`pipeline_service.py:87`
  - 问题：扫描版 PDF（未开 OCR）、MinerU 200 返业务错误等，解析实际失败却被标记成功，文件一路跑到 `complete`，内容为空、0 表格/分块/向量、抽取全空；用户看到"处理完成"却无任何数据，也无 `*_failed` 可排查。
  - 建议：`results` 为空或首个 `md_content` 为空时抛明确异常（带响应体片段）；或 `parse_service` 拿到 content 后校验 `strip()` 非空否则置 `parsing_failed`。

- [ ] **R5 · calc 规则字符白名单硬过滤，静默算出错误数字** — 高 / 中
  - 证据：`analysis_service.py:254`（`math_chars = set("0123456789+-*/().eE ")`）、`:143`（校验只要求"至少一个依赖字段是数字"）、`:37`（缺失字段替换为中文提示）
  - 问题：缺失字段的中文占位提示被逐字符过滤后，字段名里的数字会残留（`revenue2023`→`2023`）、`12.5%`→`12.5`、`1,234`→`1234`、`2-3`→`-1`，最终 numexpr 用语法合法、语义错误的公式算出"看似正确"的数落库。财务/数值场景最危险的静默错误。
  - 建议：calc 要求全部依赖为有效数字，任一缺失/非数字直接判失败并给 reason；resolve 后若仍含非数学 token 直接报错而非删改。

- [ ] **R6 · 同步调用阻塞整个 asyncio 事件循环（吞吐杀手，两处）** — 高 / 中
  - 证据：Milvus 检索 `search_service.py:44`（async 里跑同步 gRPC + 每请求重建连接/`load()`）；VL 渲染 `vl_service/model.py:49`、`locate.py:63/147`（fitz 光栅化 + PIL 编码）
  - 问题：单次调用期间**所有并发请求（含前端高频轮询）全部冻结**，`global_max_concurrency=8` 在渲染阶段形同虚设。作者在 `_cleanup_file_artifacts` 已用线程池规避阻塞，热路径却未同样处理。
  - 建议：用 `asyncio.to_thread` / `run_in_threadpool` 把阻塞的 Milvus 调用与 PDF 渲染移出事件循环。

- [ ] **R7 · `callback_url` 未校验，SSRF + 数据外带** — 高（安全）/ 小
  - 证据：`callback.py:24`（`client.post(callback_url, ...)`）、`file_router.py:276`
  - 问题：可指向内网/云元数据端点（`169.254.169.254`）让服务器代发请求形成盲打 SSRF；也可把整篇解析文档 POST 到任意外部地址形成数据外带。与零鉴权叠加无门槛。
  - 建议：仅允许 http/https，解析 host 拒绝私有网段/环回/链路本地；可维护出站白名单；回调载荷签名。

- [ ] **R8 · `/search` 的 `file_id` 拼进 Milvus 表达式，注入 + 跨文件泄露** — 高（安全）/ 小
  - 证据：`milvus_client.py:149`（`f'file_id == "{file_id}"'`）、`schemas.py`（`SearchRequest` 无约束）、`search_router.py:16`（无鉴权）
  - 问题：传 `x" or file_id != "` 可篡改过滤条件越过文件隔离读其它文件分块；`top_k` 无上限/可为负触发超大扫描或报错。
  - 建议：`file_id` 施加白名单校验（`^[A-Za-z0-9_-]+$`）并转义/参数化；`top_k`/`score_threshold` 加边界。

- [ ] **R9 · Milvus `nprobe` 硬编码 16，召回率偏低** — 高 / 小
  - 证据：`milvus_client.py:144`（`nprobe: 16`）、`config.py:58`（nlist 默认 1024）
  - 问题：每次向量检索只扫 1024 桶中的 16 个（≈1.5%），相关 chunk 极易漏召回，直接导致字段抽取拿不到原文、判断/计算依据错误，**完全静默不可察**。
  - 建议：`nprobe` 提到 nlist 的 4%~12%（32~128）并做成 `MilvusConfig` 配置项；按数据规模复核 nlist 默认值。

- [ ] **R10 · `vl_model` 默认 `page_range=all`，大文档必失败/爆成本** — 高 / 中
  - 证据：`vl_service/model.py:18/38/46`、`extraction_service.py:1426`、`vl_client.py:133`
  - 问题：把全部页渲成图塞进单次 VL 请求，几十页文档轻则 4xx 抽取失败，重则 token 爆炸 + 被 `max_tokens=4096` 截断。默认 `all` 使该雷几乎必踩。
  - 建议：加页数/图片数上限（超限自动分批多次调用后合并，或强制 `page_range`）；每请求图片数/总像素纳入配置与校验。

- [ ] **R11 · `copy_from` 复制规则时依赖重映射无回退，静默丢依赖** — 高 / 中
  - 证据：`doctype_router.py:601`（只按本次新建字段映射）、`:466`（未映射 id 原样保留）、`:537`（skip 命中不建）、对比 import 的按名回退 `:860`
  - 问题：增量复制/`skip` 复制时，规则依赖的字段其实已作为副本存在于目标，但代码找不到 → `depend_fields` 被删、`expression` 里 `<field_result>` 指向目标不存在的源 id，复制出的规则跑分析时才静默失效。
  - 建议：复制前建"源 field_id/字段名 → 目标已有字段"回退映射，未新建的依赖优先回退，仍找不到才记 `missing_dependencies`，与 import 对齐。

---

## 🟠 第二优先级：健壮性 / 性能 / 数据一致性

| ID | 优化点 | 影响/工作量 | 证据 | 建议 |
|---|---|---|---|---|
| - [ ] H1 | 上传先整份读内存再校验大小 + 管线无并发闸/背压（1000MB 上限下并发大文件可 OOM；批量上传打爆 MinerU/LLM）〔file-api / meta / pipeline 三域共同指认〕 | 中/中 | `file_router.py:288/322`、`config.yaml:11` | 先按 Content-Length/流式分块预检超限即 413；`run_pipeline` 入口加进程级 `asyncio.Semaphore`（可配）或有界队列；后台从 `uploads/{id}.pdf` 读而非常驻 bytes |
| - [ ] H2 | 同一 `file_id` 的 retry 与在跑管线无并发保护 → 删/写同批结果竞态、progress 抖动、终态错误 | 中/小 | `file_router.py:474`、`pipeline_service.py:1199` | retry/上传入口用 `UPDATE ... WHERE progress IN(终态)` 做乐观 CAS，按 rowcount 判定，在途则 409 |
| - [ ] H3 | `files.progress` 无索引，而"处理中队列"每 3s 轮询 `WHERE progress IN(...)`，文件表只增不删 → 逐渐全表扫描 + filesort | 中/小 | `tables.py:83/89`、`file_router.py:146`、`init_service.py:127` | 加 `(progress, create_time)` 复合索引，ORM `__table_args__` 与 `index_migrations` 同步补 |
| - [ ] H4 | `AnalysisRuleResponse` 复用创建期校验器且无子类豁免 → 一条不合规规则入库使 `GET /analysis/rules` 整体 500（非跳过单条） | 中/小 | `schemas.py:451/472`、`analysis_router.py:47`、对比 `:317` | 给两个校验器加 `cls.__name__` 子类豁免（响应模型读库不跑创建校验）；import 落库前复核 |
| - [ ] H5 | `run_extraction` 逐字段串行 LLM（N 字段≈N×时延），且每字段重新从 MySQL 拉整篇 FileContent 大 blob | 中/中 | `extraction_service.py:1632/1332/1608` | 引入可配置并发（`Semaphore` + 独立 session）；`run_extraction` 层把已加载 content/page_mapping 下传，整文件只加载一次 |
| - [ ] H6 | Milvus 单例形同虚设：入库/检索/抽取处处 `new MilvusClient()+connect()+load()` 未复用 `get_milvus_client()` | 中/中 | `embedding_service.py:74`、`search_service.py:44`、`extraction_service.py:920`、`milvus_client.py:45` | 统一改用 `get_milvus_client()`；或对 `ensure_collection` 已存在分支做一次性 load 缓存 |
| - [ ] H7 | httpx 客户端每次现建现弃（无连接池/keep-alive），每次外部调用重新 TCP+TLS 握手 | 中/中 | `llm_client.py:68/150`、`vl_client.py:78`、`callback.py:23` | 每类外部服务维护进程级复用 `AsyncClient`，lifespan 关闭时统一 aclose |
| - [ ] H8 | 指数退避无 jitter → 429 时并发请求锁步在 1s/2s/4s 同时重发形成惊群 | 中/小 | `llm_client.py:84/169`、`vl_client.py:89` | 退避加 full jitter；429 优先解析 `Retry-After` |
| - [ ] H9 | `get_embeddings` 对 4xx 也重试（与 chat/vl 不一致，确定性错误拖满退避才抛） | 中/小 | `llm_client.py:168`、对比 `:78` | 4xx（非 429）直接 raise 不进退避 |
| - [ ] H10 | 更换 embedding 模型/维度后，旧 collection 维度不比对，运行期（入库/检索）才抛晦涩错误 | 中/中 | `milvus_client.py:45`、`config.py:44` | 启动 `ensure_collection` 比对既有 collection schema 维度与配置，不一致明确报错前移到启动期 |
| - [ ] H11 | 删除文件后 Milvus 向量无孤儿兜底（仅 PDF 有），后台清理失败/崩溃即"删了还能搜到" | 中/中 | `file_router.py:453`、`init_service.py:311` | 启动/周期任务 diff Milvus file_id 与 files 表删残留；或删除时落 outbox 由后台重试至成功 |
| - [ ] H12 | `recompute_page_mapping` 只刷 `file_content`，已落库的 chunk/table 页码与抽取 bbox 仍旧值 → "已修复"假象 | 中/中 | `file_router.py:621`、`chunk_service.py:364`、`table_service.py:226` | 重算后级联刷新 chunk/table page_num（含 Milvus 冗余）与该文件 source_refs 的 bbox；或响应明确告知需重跑下游 |
| - [ ] H13 | `page_mapping` 构建失败/无锚点时静默返回全空（全模块无日志）→ PDF 高亮整体失效无人知 | 中/小 | `page_mapping.py:117/138` | 记录候选锚数/LIS 保留数；为空或覆盖率过低时 warning（可写入 `files.error`） |
| - [ ] H14 | MinerU 调用无重试 + 错误响应体被丢弃（最耗时最易抖的阶段，一次闪断即失败） | 中/小 | `mineru_client.py:65`、`config.yaml:11` | 加有限次指数退避（区分 5xx/连接错误可重试、4xx 不重试）；非 2xx 读 `resp.text` 片段拼入异常/日志 |
| - [ ] H15 | judge 解析兜底用裸子串 `"true" in resp`，"not true"类文本误判；两者皆无时整段响应落 `result_value` | 中/小 | `analysis_service.py:222/194` | 兜底用正则锚定结论词/词边界；都无法确定时返回 unknown + `success=false`，原文放 reason 不入 result |
| - [ ] H16 | 类型级联删除在 async 请求内同步删 Milvus/PDF、且在 commit 之前（失败即 DB/向量不一致）；批量删每类型新建一次连接 | 中/中 | `doctype_router.py:385/452`、对比 `file_router.py:396` | 参照 file_router：先 commit MySQL，再 `BackgroundTasks`/线程池后台清理，Milvus 复用单例一次连接批量删 |
| - [ ] H17 | 崩溃恢复只把 `*ing` 批量置 `*_failed`，无批量重试/自动续跑（一次 deploy 让上百在途文件集体失败，只能逐个手工 retry）〔pipeline / meta 共同指认〕 | 中/中 | `init_service.py:206`、`file_router.py:460` | 新增 `POST /file/batch_retry`（按 file_ids 或 status/type_id 过滤），复用 `run_from_stage`；可选崩溃后受控并发自动续跑 |
| - [ ] H18 | PDF 保留清理只看 `create_time` 不看 progress → 可能误删在途/排队文件的 PDF，VL 抽取随即 404 | 中/中 | `retention_service.py:46/64` | 淘汰候选排除未到终态（非 complete/`*_failed`）文件，或对近一周期/`*ing` 文件加保护期 |
| - [ ] H19 | 原始 PDF 已落盘却不支持从 parsing 重试，`parsing_failed` 只能删档重传（丢 file_id、留垃圾记录） | 中/中 | `pipeline_service.py:1189`、`file_router.py:299/488` | `run_from_stage` 支持 parsing：`uploads/{id}.pdf` 存在时读回重跑并重建下游；同步覆盖 sync/stream 两变体与 retry `valid_stages` |
| - [ ] H20 | 超长表格（>8192）按 512 过度切碎、只首片带表名、子块位置整体右移 → 表格检索质量与页码定位下降 | 中/中 | `chunk_service.py:309/316`、`config.yaml:35` | 按 `max_embedding_len`(8192) 而非 `chunk_size` 切；每子块补表名前缀；子块定位用真实表格 `[start,end]` 区间换算 |
| - [ ] H21 | 表名仅从表格上文推断，回退取"最后一行"易误取正文/页眉（错表名级联导致表格抽取召回失败） | 中/中 | `table_service.py:86/111/155` | LLM 上下文附表格首行/前几行单元格 + 表下 1-2 行；回退优先取 caption/首个表头单元格并过滤纯数字/页眉 |

---

## 🟡 第三优先级：体验 / 可观测 / 打磨

| ID | 优化点 | 影响/工作量 | 证据 | 建议 |
|---|---|---|---|---|
| - [ ] L1 | 前端批量上传无并发控制 + 每个文件各刷一次列表、各弹一个 Toast | 中/中 | `app.js:154/174/177` | 上传加并发上限（3~5）排队；提交后 debounce 刷一次列表；合并汇总 Toast |
| - [ ] L2 | 详情页 Tab 切换无时序保护，慢响应覆盖后点的 Tab | 中/小 | `app.js:714/861`、对比 `pdfViewer.js:44` | 参照 PdfViewer 的 `_gen` 代际：写 innerHTML 前校验 token 未过期 |
| - [ ] L3 | 实时日志每行 `querySelectorAll` 全量 + 强制重排 → 突发日志 O(n²) 卡顿 | 中/中 | `log-viewer.js:227/229/232` | 用行数计数器/头节点引用增量裁剪；滚动用 rAF 合并 |
| - [ ] L4 | 日志新行无条件跳到底，把正在回看历史的用户强拽到底 | 中/小 | `log-viewer.js:229/301` | 仅当已接近底部才自动滚，否则保持位置并提示"N 条新日志" |
| - [ ] L5 | 轮询刷新详情固定 `switchTab('outline')` → 盯着提取/分析结果时完成瞬间跳回大纲；切文件闪旧数据 | 中/小 | `app.js:564/1238/548` | 记住 activeTab 刷新后恢复；openDrawer 先置骨架占位再加载 |
| - [ ] L6 | 9 个弹窗无 `role="dialog"`/`aria-modal`、不支持 Esc 关闭、无焦点管理 | 中/中 | `index.html:542`、`customSelect.js:242` | 弹窗基类加 Esc 关闭 + dialog 语义 + focus trap + 焦点还原 |
| - [ ] L7 | PDF 定位优先跳"模型自报页"，该页可能无高亮框甚至越界（10 页文档报第 999 页，徽章显示 999） | 中/中 | `app.js:1088/1051`、`pdfViewer.js:63/80` | 加载后用 totalPages 过滤越界模型页/徽章；无框时优先落有框页或提示"模型自报、无精确高亮" |
| - [ ] L8 | 文本/表格 LLM 与 embedding 的 `usage` token 被丢弃 → 无成本统计（LLM 是核心成本） | 中/中 | `llm_client.py:74/164`、`vl_service/model.py:60` | `chat_completion`/`get_embeddings` 取回 usage；抽取/分析累加落结果或用量表，按文件/类型汇总 |
| - [ ] L9 | 异步 `/analysis/run` 结果只推回调、不落库、无 `task_id` 查询 → 回调 2.5s 超时即永久丢失 | 中/中 | `analysis_router.py:398`、`callback.py:32` | 落任务/结果表 + `GET /analysis/run/{task_id}`；回调加有限次退避重试 |
| - [ ] L10 | 启动依赖自检模块 `startup_check` 从未被调用（死代码），文档宣称的探活表根本不打印 | 中/小 | `startup_check.py:1/55`、`app.py:42` | 补 `run_startup_checks()` 汇总各依赖只读探活（`_run_one`/`_format_table` 已就绪），run_init 后调用、warn 不阻断 |
| - [ ] L11 | `use_llm=0` 时把检索原文写进 `Text`(64KB) 列的 `extracted_value`，整页/多命中/表格 HTML 可能静默截断 | 中/小 | `tables.py:228/114`、`extraction_service.py:206/1037` | `extracted_value` 改 MEDIUMTEXT/LONGTEXT（补一条 MODIFY COLUMN 迁移）；或对 `use_llm=0` 的 value 设上限截断并在 reason 标注 |
| - [ ] L12 | 新建类型 `project_id` 无格式/存在性校验（与 `ProjectCreate` 严格校验不一致）→ 造"幽灵项目"类型在两级导航中消失〔doctype / data-model 共同指认〕 | 中/小 | `doctype_router.py:228`、`schemas.py:31/148` | 建档写 project_id 前校验 Project 存在（不存在 400 或回退 NULL）；补 pattern+max_length |
| - [ ] L13 | `copy_from` 无条件改写目标 `parent_type_id` → 多源复制覆盖血缘、误导项目级联 | 中/小 | `doctype_router.py:635/956` | 仅当 `parent_type_id` 为空时记录来源（首次复制定血缘），或加参数控制是否 reparent |
| - [ ] L14 | 独立分析 `items` 无并发/数量上限（几百 item 瞬时打爆 LLM） | 中/小 | `analysis_run_service.py:237`、`schemas.py:534` | 加 `Semaphore`（可配）+ `items` `max_length`，超限 422 |
| - [ ] L15 | 规则执行异常时 DB `reason` 落空（错误只进回调/流不进库，用户事后查不到失败原因） | 中/小 | `analysis_service.py:485/506/734/755` | except 分支把 `reason=str(e)` 一并写库，与回调/校验分支一致 |
| - [ ] L16 | `/file/list` 的 `page/page_size` 无边界（`page=0` 致 SQL 500；超大 `page_size` 拉全表） | 中/小 | `file_router.py:60/87`、对比 `log_router.py:162` | `page`≥1、`page_size` 加 `le` 上限，越界 422；`/processing` 硬编码 limit 参数化 |
| - [ ] L17 | 错误响应结构不统一：超限上传返 HTTP 200 内嵌 `code=400`，与其余端点 `HTTPException` 4xx 混用 | 中/中 | `file_router.py:290/356` | 统一错误出口（推荐超限 413/400 走 HTTPException），至少 `code!=200` 返对应 HTTP 状态 |
| - [ ] L18 | `vl_progressive` 逐批串行且无页数上限/无早停（200 页≈100 次串行 VL 调用 + 累积摘要线性膨胀） | 中/中 | `vl_service/progressive.py:51/80/99` | 加最大扫描页数上限 + 早停（累计信息达阈值即停）+ 累积摘要长度截断 |
| - [ ] L19 | `vl_progressive` 每批未剥 `<think>` 标签 + `raw[:20]` 精确匹配判定相关性（换带推理模型/换措辞即误判） | 中/小 | `vl_service/progressive.py:14/81/85`、对比 `locate.py:98` | 批内先 `strip_think_tags` 再判定；无信息判定改归一化/结构化布尔字段 |
| - [ ] L20 | 三种 VL 方法都不检查 `finish_reason`，被 `max_tokens` 截断的输出被静默解析成残缺 value | 中/小 | `vl_service/model.py:57`、`progressive.py:110`、`locate.py:153`、`config.py:105` | 解析前读 `finish_reason`，为 length 时 warning + 写 `source_refs._vl.truncated`，或自动提高 max_tokens 重试 |
| - [ ] L21 | `context`/`rule` 检索的 `max_results` 是跨关键词全局截断，高频关键词会挤掉其他关键词命中（占位符静默为空） | 中/小 | `extraction_service.py:630/817`、对比 `:857` | 改成与 `chunk_db` 一致的"每关键词各限 max_results" |
| - [ ] L22 | `search_config` 完全无子键校验（缺 `page_range`/`query_text` 等到抽取运行时才失败），与 `vl_config`/`web_search` 的 fail-fast 不一致 | 低/中 | `schemas.py:305`、对比 `:363/458` | 按 `search_type` 做最小必填校验，`model_validator` 里 fail-fast |
| - [ ] L23 | `stage_done` 回调把整篇 markdown/全部 chunks 塞进 2.5s 超时的 POST，大文档易超时静默丢事件 | 低/中 | `pipeline_service.py:466/550`、`callback.py:50` | 大 payload 只下发摘要/计数 + 拉取地址，让消费方按需回拉（embedding 阶段已如此） |
| - [ ] L24 | `build_page_mapping` 每块对全篇 `count+find` 全量扫描，大文档 O(块数×md长度)、且在事件循环内同步执行 | 低/中 | `page_mapping.py:60/126` | 一次性预处理"前缀→出现次数/位置"索引；或用两次 find 判唯一省去 count；必要时入线程池 |
| - [ ] L25 | `analysis.judge_timeout` 配置定义了却从未透传生效（judge 仍用全局 llm_timeout） | 低/小 | `config.py:97`、`analysis_service.py:177`、`llm_client.py:44` | `execute_judge` 透传 `judge_timeout`；或删除该无效配置 |
| - [ ] L26 | `chunking.max_chunk_size` 配置声明但分块逻辑从不引用（真正生效的表格上限是硬编码 8192） | 低/小 | `config.py:36`、`chunk_service.py:278/316` | 让分块真正使用它（作为超长块/表格切分上限替换硬编码），或移除 |
| - [ ] L27 | 四张明细表存在与复合主键最左前缀重复的 `file_id` 二级索引，纯写放大/占空间 | 低/小 | `tables.py:119/138/232/249` | 删除四个冗余 `ix_*_file_id`（复合主键 file_id 最左列已覆盖 `WHERE file_id=?`） |
| - [ ] L28 | 类型列表分页排序键 `created_at`（无小数秒）非唯一，同秒批量建类型时翻页漏行/重复 | 低/小 | `doctype_router.py:134` | 排序末尾追加唯一 tiebreaker（`type_id`） |

---

## 附一：被对抗式验证剔除的 15 条（不建议做，留档）

| 域#序 | 被否决的建议 | 否决理由（摘） |
|---|---|---|
| file-api#6 | 日志按等级过滤在 tail 截断之后 | 描述/建议未通过复核 |
| file-api#7 / meta#6 | 上传内容 sha256 去重"秒传"复用抽取结果 | LLM 结果复用建议站不住（两版均否决） |
| infra#6 | embedding 8192 静默截断改可配 + 日志 | 价值低 / 边缘 |
| infra#7 | per-item 回调改 fire-and-forget | 复核未通过 |
| extraction-vl#4 | `vl_locate` fitz 缺 try/finally 句柄泄漏 | 实际影响被夸大 |
| extraction-text#4 | `search_vector_db` 未复用单例 | 与 H6 重复/价值低 |
| extraction-text#5 | `parse_llm_json` 非贪婪正则解析嵌套对象失败 | salvage 兜底已覆盖 |
| extraction-text#6 | 表格 contains/fuzzy 未防 NULL 表名崩溃 | 正常管线不可达（save_tables 必写非空） |
| doctype#4 | 删除类型未清子类型 `parent_type_id` 悬挂指针 | 不致命 / 价值低 |
| analysis#3 | async 回调与 SSE 流 per-item 字段名不一致 | 复核未通过 |
| analysis#4 | 独立分析静默丢弃未覆盖规则 | 复核未通过 |
| analysis#5 | 网络搜索失败降级 judge 仍 `success:true` | **已处理**：`source_refs._web_search.error` 专门溯源 |
| meta#3 | 无健康/就绪探针、无指标 | **已处理**：docker-compose 已有 healthcheck；MySQL/Milvus 启动即连 |
| data-model#1 | `extraction_field` 缺 `(type_id,field_name)` 唯一约束 | 描述与建议有实质错误、内核价值低 |

## 附二：验证方法与置信度

- 全部 66 条均经"独立审查员回读源码"确认；其中 6 条"头条"（R2/R5/R3/R1/R9/R4）由本次审查者**再次亲自回读源码复核**，结论属实。
- 影响力/工作量为审查校准值；部分原评"高"经复核下修为"中"（如 H1 上传并发、H2 retry 竞态——真实但严重性有条件）。
- 少数验证 agent 运行期安全分类器不可用，其**结论正确性**不受影响（分类器只审查 agent 行为安全性，非内容对错）；涉及的头条项已人工复核。
