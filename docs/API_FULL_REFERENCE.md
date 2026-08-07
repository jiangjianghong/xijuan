# 析卷 AI 全量接口手册

> 对应服务版本 0.3.0  
> Base URL 示例：`http://localhost:5019`  
> 覆盖范围：当前源码注册的全部业务接口，共 51 个。本文档是自包含手册：接口 schema、业务语义、调用示例、参数说明、副作用、错误细节、配置指南、数据模型、枚举、架构说明和 backlog 均在本文内说明。

## 0. 系统架构与处理流程

本系统的核心目标是把上传的 PDF 转换为可检索、可溯源、可自动判断的结构化结果。一次完整处理会经过 MinerU 解析、表格识别、文本分块、向量入库、字段提取、逻辑分析六个阶段；接口层负责提交任务、查询状态、管理配置、调试规则和读取结果。

### 0.1 读者视角

| 读者 | 建议阅读顺序 | 重点 |
|---|---|---|
| 接入方 / 调用方 | 系统流程 -> 公共约定 -> `/file/parse` -> 回调 / SSE -> 结果查询接口 | 如何上传、如何等待完成、如何读取结果 |
| 配置维护者 | 文档类型 -> 字段提取 -> 逻辑分析 -> 调试接口 -> 复制 / 导入导出 | 如何为不同文件类型配置字段和规则 |
| 前端开发 | 接口总览 -> 文件状态 / 详情 -> 回调 / SSE -> source_refs / 页码 | 如何展示队列、详情、PDF 定位和错误 |
| 后端维护者 | 架构分层 -> 状态机 -> 数据模型 -> 配置项 -> backlog | 如何理解边界、副作用和已知风险 |

### 0.2 逻辑架构

| 层级 | 目录 / 模块 | 职责 | 关键输出 |
|---|---|---|---|
| HTTP 接入层 | `app.py`、`blue_print/` | 注册 FastAPI 应用、路由、静态 UI、SSE / PDF 响应 | `/file`、`/extraction`、`/analysis`、`/doctype`、`/search`、`/log` |
| 业务编排层 | `service/pipeline_service.py` | 编排六阶段处理、失败标记、从阶段重试、回调 / SSE 事件 | `files.progress`、阶段产物、最终结果 |
| 阶段服务层 | `service/parse_service.py`、`table_service.py`、`chunk_service.py`、`embedding_service.py`、`extraction_service.py`、`analysis_service.py` | 执行每个具体阶段 | Markdown、表格、分块、向量、字段结果、分析结果 |
| 配置与模型层 | `model/`、`utils/config.py` | SQLAlchemy ORM、Pydantic schema、配置加载 | MySQL 表、请求 / 响应模型、运行时配置 |
| 外部依赖层 | MinerU、OpenAI-compatible LLM / Embedding / VL、Milvus、MySQL | PDF 解析、语言模型、视觉模型、向量库、关系库 | 解析内容、模型输出、向量召回、持久化数据 |
| 前端展示层 | `ui/` | 文件列表、处理队列、详情抽屉、字段 / 规则配置、日志查看 | `/ui` 静态页面 |

### 0.3 核心数据流

```text
PDF 上传
  -> /file/parse 创建 files 记录并落盘 uploads/{file_id}.pdf
  -> parsing: MinerU 解析 PDF 为 Markdown / middle_json / page_mapping
  -> tableing: 从 Markdown 表格块提取表名、表格内容和页码
  -> chunking: 把正文和表格切成可检索分块
  -> embedding: 生成 embedding 并写入 Milvus
  -> extracting: 按文档类型加载字段配置，执行 table / text / vl 抽取
  -> analyzing: 按文档类型加载分析规则，执行 judge / calc / custom
  -> complete: 文件进入完成态，调用方读取 extraction / analysis / detail
```

### 0.4 六阶段处理管线

| 阶段 | progress | 入口服务 | 主要动作 | 主要产物 | 失败态 |
|---|---|---|---|---|---|
| PDF 解析 | `parsing` | `parse_service` | 调 MinerU，生成 Markdown、middle_json，构建页码映射 | `file_content` | `parsing_failed` |
| 表格识别 | `tableing` | `table_service` | 提取 HTML 表格块，用 LLM / 回退规则识别表名 | `file_table` | `tableing_failed` |
| 文本分块 | `chunking` | `chunk_service` | 递归切分 Markdown，表格作为独立块处理 | `file_chunk` | `chunking_failed` |
| 向量入库 | `embedding` | `embedding_service` | 调 embedding API，写 Milvus collection | Milvus 向量 | `embedding_failed` |
| 字段提取 | `extracting` | `extraction_service` | 按 `type_id` 加载字段，执行 table / text / vl 抽取 | `extraction_result` | `extracting_failed` |
| 逻辑分析 | `analyzing` | `analysis_service` | 按 `type_id` 加载规则，执行 judge / calc / custom | `analysis_result` | `analyzing_failed` |

### 0.5 配置隔离与执行关系

每个文件只绑定一个 `type_id`。字段配置 `extraction_field` 和分析规则 `analysis_rule` 都按 `type_id` 隔离，管线执行时只读取同类型且启用的配置。跨类型复用不能直接共享配置，需要通过复制、导入或派生类型生成独立副本；复制后修改源类型不会影响目标类型。

`field_id` 与 `rule_id` 在当前实现中是全局唯一，不只是类型内唯一。保存配置时，如果同一个 ID 已被其它 `type_id` 占用，会返回冲突错误。

### 0.6 运行模式与通知方式

| 场景 | 调用方式 | 返回方式 | 适用对象 |
|---|---|---|---|
| 后台处理 | `mode=async` | 立即返回任务 ID / 文件 ID，后续靠轮询或回调 | 前端上传、外部系统异步集成 |
| 阻塞处理 | `mode=sync` | 接口阻塞直到执行完成或失败 | 小文件、脚本调试、测试环境 |
| 流式处理 | `mode=stream` | SSE 分阶段返回事件 | 需要实时进度展示的页面或命令行 |
| 回调通知 | `callback_url` | 后端主动 POST 阶段和单项完成事件 | 外部业务系统接收处理结果 |

### 0.7 结果读取路径

| 目标 | 推荐接口 | 说明 |
|---|---|---|
| 查处理进度 | `GET /file/{file_id}/status` | 只读基础状态和错误 |
| 查完整详情 | `GET /file/{file_id}/detail` | 汇总文件、阶段时间、内容、结果等详情 |
| 查原始 Markdown | `GET /file/{file_id}/content` | 按页返回内容 |
| 查表格 | `GET /file/{file_id}/tables` | 读取 tableing 产物 |
| 查分块 | `GET /file/{file_id}/chunks` | 读取 chunking 产物 |
| 查字段结果 | `GET /file/{file_id}/extraction` | 字段抽取值、理由、页码、溯源 |
| 查分析结果 | `GET /file/{file_id}/analysis` | 规则结论、依赖字段、溯源 |
| 查 PDF | `GET /file/{file_id}/pdf` | 原始 PDF 二进制，可能因保留策略被清理 |

### 0.8 核心对象关系

| 对象 | 主键 / 标识 | 上游来源 | 下游使用方 | 说明 |
|---|---|---|---|---|
| 文档类型 | `type_id` | `/doctype` 创建或导入 | 文件、字段、规则 | 隔离不同文件格式的配置集合 |
| 文件记录 | `file_id` | `/file/parse` 上传创建 | 全部阶段服务、结果查询 | 每次上传生成新 ID，同名文件不复用 |
| 原始 PDF | `uploads/{file_id}.pdf` | 上传时落盘 | PDF 预览、VL 抽取 | 可能被保留策略清理；清理后 PDF 预览和 VL 会失败 |
| 解析内容 | `file_content.file_id` | parsing 阶段 | tableing、chunking、page 检索、页码映射 | 保存 Markdown、middle_json、page_mapping |
| 表格产物 | `file_id + table_index` | tableing 阶段 | table 字段抽取、表格查看 | 表名由 LLM 或回退规则得到 |
| 分块产物 | `file_id + chunk_id` | chunking 阶段 | MySQL 文本检索、embedding | 普通文本按规则切分，表格通常作为独立块 |
| 向量数据 | `file_id + chunk_id` | embedding 阶段 | `/search`、`vector_db` 字段抽取 | 存在 Milvus 中，不随回调下发 |
| 字段配置 | `field_id` | `/extraction/fields` | extracting 阶段、调试接口 | 当前实现全局唯一，按 `type_id` 隔离执行 |
| 字段结果 | `file_id + field_id` | extracting 阶段 | `/file/{id}/extraction`、analysis 规则 | 包含 value、reason、pages、source_pages、source_refs |
| 分析规则 | `rule_id` | `/analysis/rules` | analyzing 阶段、独立分析 | 当前实现全局唯一，按 `type_id` 隔离执行 |
| 分析结果 | `file_id + rule_id` | analyzing 阶段或 `/analysis/run` 持久化 | `/file/{id}/analysis` | 包含结论、理由、依赖字段值和溯源 |

核心关系可以简化为：`type_id` 决定配置集合，`file_id` 串联全部阶段产物，`field_id` 决定抽取结果，`rule_id` 决定分析结论。调用方读取结果时，通常以 `file_id` 为入口；配置维护时，通常以 `type_id` 为入口。

---

## 目录

- [0. 系统架构与处理流程](#0-系统架构与处理流程)
  - [0.1 读者视角](#01-读者视角)
  - [0.2 逻辑架构](#02-逻辑架构)
  - [0.3 核心数据流](#03-核心数据流)
  - [0.4 六阶段处理管线](#04-六阶段处理管线)
  - [0.5 配置隔离与执行关系](#05-配置隔离与执行关系)
  - [0.6 运行模式与通知方式](#06-运行模式与通知方式)
  - [0.7 结果读取路径](#07-结果读取路径)
  - [0.8 核心对象关系](#08-核心对象关系)
- [1. 公共约定](#1-公共约定)
  - [1.1 服务入口](#11-服务入口)
  - [1.2 通用响应信封](#12-通用响应信封)
  - [1.3 通用状态码](#13-通用状态码)
  - [1.4 处理管线与进度状态](#14-处理管线与进度状态)
  - [1.5 ID 与隔离规则](#15-id-与隔离规则)
  - [1.6 分页约定](#16-分页约定)
- [2. 接口总览](#2-接口总览)
- [3. 文件处理接口 `/file`](#3-文件处理接口-file)
  - [3.1 `GET /file/list` 分页查询文件列表](#31-get-filelist-分页查询文件列表)
  - [3.2 `GET /file/processing` 查询处理中队列](#32-get-fileprocessing-查询处理中队列)
  - [3.3 `POST /file/context_query` 文件片段上下文查询](#33-post-filecontext_query-文件片段上下文查询)
  - [3.4 `DELETE /file/batch` 批量删除文件](#34-delete-filebatch-批量删除文件)
  - [3.5 `POST /file/parse` 上传 PDF 并启动处理管线](#35-post-fileparse-上传-pdf-并启动处理管线)
  - [3.6 `GET /file/{file_id}/status` 查询文件处理状态](#36-get-filefile_idstatus-查询文件处理状态)
  - [3.7 `GET /file/{file_id}/pdf` 下载/预览原始 PDF](#37-get-filefile_idpdf-下载预览原始-pdf)
  - [3.8 `DELETE /file/{file_id}` 删除单个文件](#38-delete-filefile_id-删除单个文件)
  - [3.9 `POST /file/{file_id}/retry/{stage}` 从指定阶段重试](#39-post-filefile_idretrystage-从指定阶段重试)
  - [3.10 `POST /file/{file_id}/retry/extracting` 快捷重试字段提取](#310-post-filefile_idretryextracting-快捷重试字段提取)
  - [3.11 `POST /file/{file_id}/retry/analyzing` 快捷重试逻辑分析](#311-post-filefile_idretryanalyzing-快捷重试逻辑分析)
  - [3.12 `GET /file/{file_id}/tables` 查询文件表格列表](#312-get-filefile_idtables-查询文件表格列表)
  - [3.13 `GET /file/{file_id}/chunks` 查询文件分块列表](#313-get-filefile_idchunks-查询文件分块列表)
  - [3.14 `POST /file/{file_id}/recompute_page_mapping` 重算页码映射](#314-post-filefile_idrecompute_page_mapping-重算页码映射)
  - [3.15 `GET /file/{file_id}/outline` 查询章节大纲](#315-get-filefile_idoutline-查询章节大纲)
  - [3.16 `GET /file/{file_id}/content` 按页返回 Markdown 内容](#316-get-filefile_idcontent-按页返回-markdown-内容)
  - [3.17 `GET /file/{file_id}/extraction` 查询字段提取结果](#317-get-filefile_idextraction-查询字段提取结果)
  - [3.18 `GET /file/{file_id}/analysis` 查询逻辑分析结果](#318-get-filefile_idanalysis-查询逻辑分析结果)
  - [3.19 `GET /file/{file_id}/detail` 查询完整文件详情](#319-get-filefile_iddetail-查询完整文件详情)
- [4. 字段提取接口 `/extraction`](#4-字段提取接口-extraction)
  - [4.1 `GET /extraction/match-prompt-defaults` 获取提示词模板默认值](#41-get-extractionmatch-prompt-defaults-获取提示词模板默认值)
  - [4.2 `GET /extraction/fields` 查询字段配置](#42-get-extractionfields-查询字段配置)
  - [4.3 `POST /extraction/fields` 新增/更新字段配置](#43-post-extractionfields-新增更新字段配置)
  - [4.4 `DELETE /extraction/fields/{field_id}` 删除字段配置](#44-delete-extractionfieldsfield_id-删除字段配置)
  - [4.5 `GET /extraction/fields/{field_id}/check` 检查字段 ID 是否存在](#45-get-extractionfieldsfield_idcheck-检查字段-id-是否存在)
  - [4.6 `POST /extraction/test` 字段提取同步调试](#46-post-extractiontest-字段提取同步调试)
  - [4.7 `POST /extraction/test/stream` 字段提取流式调试](#47-post-extractionteststream-字段提取流式调试)
- [5. 逻辑分析接口 `/analysis`](#5-逻辑分析接口-analysis)
  - [5.1 `GET /analysis/rules` 查询分析规则](#51-get-analysisrules-查询分析规则)
  - [5.2 `POST /analysis/rules` 新增/更新分析规则](#52-post-analysisrules-新增更新分析规则)
  - [5.3 `DELETE /analysis/rules/{rule_id}` 删除分析规则](#53-delete-analysisrulesrule_id-删除分析规则)
  - [5.4 `GET /analysis/rules/{rule_id}/check` 检查规则 ID 是否存在](#54-get-analysisrulesrule_idcheck-检查规则-id-是否存在)
  - [5.5 `POST /analysis/test` 逻辑分析同步调试](#55-post-analysistest-逻辑分析同步调试)
  - [5.6 `POST /analysis/run` 独立逻辑分析执行](#56-post-analysisrun-独立逻辑分析执行)
  - [5.7 `POST /analysis/test/stream` 逻辑分析流式调试](#57-post-analysisteststream-逻辑分析流式调试)
- [6. 文档类型接口 `/doctype`](#6-文档类型接口-doctype)
  - [6.1 `GET /doctype/list` 查询文档类型列表](#61-get-doctypelist-查询文档类型列表)
  - [6.2 `POST /doctype` 新增/更新文档类型](#62-post-doctype-新增更新文档类型)
  - [6.3 `PUT /doctype/{type_id}` 更新文档类型，可改 ID](#63-put-doctypetype_id-更新文档类型可改-id)
  - [6.4 `DELETE /doctype/{type_id}` 删除文档类型](#64-delete-doctypetype_id-删除文档类型)
  - [6.5 `POST /doctype/batch_delete` 批量删除文档类型](#65-post-doctypebatch_delete-批量删除文档类型)
  - [6.6 `POST /doctype/{type_id}/copy_from` 从源类型复制配置](#66-post-doctypetype_idcopy_from-从源类型复制配置)
  - [6.7 `GET /doctype/{type_id}/export` 导出类型配置](#67-get-doctypetype_idexport-导出类型配置)
  - [6.8 `POST /doctype/import` 从 JSON 载荷导入配置](#68-post-doctypeimport-从-json-载荷导入配置)
  - [6.9 `GET /doctype/projects` 查询项目列表](#69-get-doctypeprojects-查询项目列表)
  - [6.10 `POST /doctype/projects` 新增/更新项目](#610-post-doctypeprojects-新增更新项目)
  - [6.11 `DELETE /doctype/projects/{project_id}` 删除项目](#611-delete-doctypeprojectsproject_id-删除项目)
  - [6.12 `POST /doctype/batch_assign_project` 批量归类类型到项目](#612-post-doctypebatch_assign_project-批量归类类型到项目)
  - [6.13 `POST /doctype/{type_id}/promote` 标记为模板](#613-post-doctypetype_idpromote-标记为模板)
  - [6.14 `POST /doctype/{type_id}/demote` 取消模板标记](#614-post-doctypetype_iddemote-取消模板标记)
- [7. 向量检索接口 `/search`](#7-向量检索接口-search)
- [8. 日志接口 `/log`](#8-日志接口-log)
- [9. 异步回调契约](#9-异步回调契约)
- [10. SSE 流式事件契约](#10-sse-流式事件契约)
- [11. 枚举与关键结构速查](#11-枚举与关键结构速查)
  - [11.1 枚举](#111-枚举)
  - [11.2 source_refs 结构要点](#112-source_refs-结构要点)
  - [11.3 pages 与 source_pages](#113-pages-与-source_pages)
  - [11.4 占位符规则](#114-占位符规则)
- [12. 附录](#12-附录)
  - [12.1 附录说明](#121-附录说明)
  - [12.2 逻辑分析配置手册](#122-逻辑分析配置手册)
  - [12.3 `config.yaml` 配置手册](#123-configyaml-配置手册)
  - [12.4 字段提取配置手册](#124-字段提取配置手册)
  - [12.5 `source_refs` 溯源结构与页码定位](#125-source_refs-溯源结构与页码定位)
  - [12.6 库表结构](#126-库表结构)
  - [12.7 枚举值与状态机](#127-枚举值与状态机)
  - [12.8 MinerU 解析集成](#128-mineru-解析集成)
  - [12.9 功能优化 Backlog](#129-功能优化-backlog)

---

## 1. 公共约定

### 1.1 服务入口

| 项 | 说明 |
|---|---|
| 默认服务地址 | `http://localhost:5019` |
| Swagger UI | `GET /docs` |
| OpenAPI JSON | 运行服务可暴露 FastAPI OpenAPI schema；本文档已把当前接口结构和业务说明写入正文 |
| 前端页面 | `GET /ui` |
| 鉴权 | 当前源码未实现应用层鉴权，默认面向内网部署；外网暴露需在网关层补鉴权、限流、审计 |
| JSON 编码 | UTF-8，接口消息、字段名、日志和错误文本大量使用中文 |

### 1.2 通用响应信封

除 `text/event-stream` 流和 `application/pdf` 二进制下载外，大多数接口返回 `ResponseWrapper`。

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `code` | integer | 是 | 业务状态码。成功通常为 `200`；少量业务校验失败会在 HTTP 200 下返回 `code=400` |
| `message` | string | 是 | 人类可读消息，默认 `success` |
| `data` | any | 否 | 业务负载，具体类型随接口而定 |

成功示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {"file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340"}
}
```

HTTPException 错误示例：

```json
{"detail": "文件不存在"}
```

Pydantic 422 校验错误示例：

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "expression"],
      "msg": "expression 必须包含至少一个 <field_result>字段标识</field_result> 占位符"
    }
  ]
}
```

### 1.3 通用状态码

| HTTP | 典型场景 | 响应形态 |
|---|---|---|
| `200` | 成功、异步任务已受理、批量操作完成、SSE 建连、PDF 下载 | `ResponseWrapper` / SSE / PDF |
| `400` | 参数值不合法、业务前置校验失败 | `{"detail":"..."}` 或 `ResponseWrapper` |
| `404` | 文件、字段、规则、类型、项目、日志文件、原始 PDF 不存在 | `{"detail":"..."}` 或 `ResponseWrapper` |
| `409` | ID 被其它类型占用、删除存在依赖但未强制 | `{"detail":"..."}` 或 `ResponseWrapper` |
| `422` | 请求体字段类型、正则、枚举、模型级校验失败 | FastAPI 校验错误数组 |
| `500` | 外部依赖或内部执行异常 | `{"detail":"..."}` 或 `ResponseWrapper` |

### 1.4 处理管线与进度状态

文件上传后进入 6 阶段管线：

```text
parsing -> tableing -> chunking -> embedding -> extracting -> analyzing -> complete
```

每个运行态都有对应失败态：`parsing_failed`、`tableing_failed`、`chunking_failed`、`embedding_failed`、`extracting_failed`、`analyzing_failed`。失败时 `files.error` 保存错误。服务启动恢复逻辑会把崩溃前残留的 `*ing` 改为对应 `*_failed`，并清理孤儿阶段数据。

### 1.5 ID 与隔离规则

| 对象 | 字段 | 规则 |
|---|---|---|
| 文件 | `file_id` | 由 `type_id`、文件名、纳秒时间戳、随机盐生成 SHA256 前 32 位；每次上传都生成新 ID，同名文件不去重 |
| 文档类型 | `type_id` | `^[a-zA-Z0-9_-]+$`，最长 64。文件、字段配置、规则配置按此隔离 |
| 字段配置 | `field_id` | `^[a-zA-Z0-9_]+$`，最长 100。当前实现全局唯一，不能跨 `type_id` 复用同 ID |
| 分析规则 | `rule_id` | `^[a-zA-Z0-9_]+$`，最长 100。当前实现全局唯一，不能跨 `type_id` 复用同 ID |

### 1.6 分页约定

`GET /file/list` 固定返回 `data={items,total,page,page_size,total_pages}`。`GET /doctype/list` 为兼容旧调用方，只有同时传入 `page` 和 `page_size` 才返回 `data={items,total}`；否则返回 `data=[...]`。

---

## 2. 接口总览

| 分组 | 数量 | 说明 |
|---|---:|---|
| `/file` | 19 | 文件上传、处理、重试、内容、结果、删除 |
| `/extraction` | 7 | 字段配置、默认提示词、同步/流式调试 |
| `/analysis` | 7 | 分析规则、同步/流式调试、独立分析执行 |
| `/doctype` | 14 | 文档类型、复制、导入导出、模板、项目归类 |
| `/search` | 1 | 向量检索 |
| `/log` | 3 | 日志读取和实时流 |

| 方法 | 路径 | 简述 |
|---|---|---|
| GET | `/file/list` | 分页查询文件列表 |
| GET | `/file/processing` | 查询处理中队列 |
| POST | `/file/context_query` | 文件片段上下文查询 |
| DELETE | `/file/batch` | 批量删除文件 |
| POST | `/file/parse` | 上传 PDF 并启动管线 |
| GET | `/file/{file_id}/status` | 查询文件处理状态 |
| GET | `/file/{file_id}/pdf` | 下载/预览原始 PDF |
| DELETE | `/file/{file_id}` | 删除单个文件 |
| POST | `/file/{file_id}/retry/{stage}` | 从指定阶段重试 |
| POST | `/file/{file_id}/retry/extracting` | 快捷重试字段提取 |
| POST | `/file/{file_id}/retry/analyzing` | 快捷重试逻辑分析 |
| GET | `/file/{file_id}/tables` | 查询表格列表 |
| GET | `/file/{file_id}/chunks` | 查询分块列表 |
| POST | `/file/{file_id}/recompute_page_mapping` | 重算页码映射 |
| GET | `/file/{file_id}/outline` | 查询章节大纲 |
| GET | `/file/{file_id}/content` | 按页返回 Markdown 内容 |
| GET | `/file/{file_id}/extraction` | 查询字段提取结果 |
| GET | `/file/{file_id}/analysis` | 查询逻辑分析结果 |
| GET | `/file/{file_id}/detail` | 查询完整文件详情 |
| GET | `/extraction/match-prompt-defaults` | 获取匹配提示词默认值 |
| GET | `/extraction/fields` | 查询字段配置 |
| POST | `/extraction/fields` | 新增/更新字段配置 |
| DELETE | `/extraction/fields/{field_id}` | 删除字段配置 |
| GET | `/extraction/fields/{field_id}/check` | 检查字段 ID 是否存在 |
| POST | `/extraction/test` | 字段提取同步调试 |
| POST | `/extraction/test/stream` | 字段提取流式调试 |
| GET | `/analysis/rules` | 查询分析规则 |
| POST | `/analysis/rules` | 新增/更新分析规则 |
| DELETE | `/analysis/rules/{rule_id}` | 删除分析规则 |
| GET | `/analysis/rules/{rule_id}/check` | 检查规则 ID 是否存在 |
| POST | `/analysis/test` | 逻辑分析同步调试 |
| POST | `/analysis/run` | 独立逻辑分析执行 |
| POST | `/analysis/test/stream` | 逻辑分析流式调试 |
| GET | `/doctype/list` | 查询文档类型列表 |
| POST | `/doctype` | 新增/更新文档类型 |
| PUT | `/doctype/{type_id}` | 更新文档类型，可改 ID |
| DELETE | `/doctype/{type_id}` | 删除文档类型 |
| POST | `/doctype/batch_delete` | 批量删除文档类型 |
| POST | `/doctype/{type_id}/copy_from` | 从源类型复制配置 |
| GET | `/doctype/{type_id}/export` | 导出类型配置 |
| POST | `/doctype/import` | 导入类型配置 |
| GET | `/doctype/projects` | 查询项目列表 |
| POST | `/doctype/projects` | 新增/更新项目 |
| DELETE | `/doctype/projects/{project_id}` | 删除项目 |
| POST | `/doctype/batch_assign_project` | 批量归类类型到项目 |
| POST | `/doctype/{type_id}/promote` | 标记为模板 |
| POST | `/doctype/{type_id}/demote` | 取消模板标记 |
| POST | `/search` | 向量检索 |
| GET | `/log/files` | 查询日志文件 |
| GET | `/log/recent` | 查询最近日志 |
| GET | `/log/stream` | 实时日志流 |

---

## 3. 文件处理接口 `/file`

`/file` 是管线入口和结果查询入口。上传接口会新建 `files` 记录，并按 `type_id` 使用对应字段配置和分析规则；查询接口从 `files`、`file_content`、`file_table`、`file_chunk`、`extraction_result`、`analysis_result` 等表读取阶段产物。

### 3.1 `GET /file/list` 分页查询文件列表

按 `create_time DESC` 查询文件列表，可按 `progress` 与 `type_id` 精确过滤。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/list` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<FileListResponse>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `page` | integer | 否 | `1` | 页码，从 1 开始 |
| `page_size` | integer | 否 | `20` | 每页条数 |
| `status` | string | 否 | `""` | 按 `files.progress` 精确过滤；空串不过滤 |
| `type_id` | string | 否 | `""` | 按文档类型精确过滤；空串返回全部类型 |

请求示例：

```bash
curl "http://localhost:5019/file/list?page=1&page_size=20&type_id=default&status=complete"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [
      {
        "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
        "file_name": "年度报告.pdf",
        "file_size": 2048576,
        "progress": "complete",
        "type_id": "default",
        "error": null,
        "create_time": "2026-08-07T09:30:00"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20,
    "total_pages": 1
  }
}
```

注意事项：

- `status` 是精确匹配，不支持多状态、模糊匹配或前缀过滤。
- `total_pages` 在无数据时仍返回 `1`，这是当前源码逻辑。
- 文件列表只返回基础字段；阶段起止时间请查询 `/file/{file_id}/detail`。

### 3.2 `GET /file/processing` 查询处理中队列

返回当前仍处于运行态的文件，供前端队列轮询使用。运行态集合包含 `parsing`、`table_name_validating`、`tableing`、`chunking`、`embedding`、`extracting`、`analyzing`。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/processing` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ProcessingItem[]>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `type_id` | string | 否 | `""` | 为空返回全部类型；非空只返回该类型处理中文件 |

请求示例：

```bash
curl "http://localhost:5019/file/processing?type_id=financial_report"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
      "file_name": "年度报告.pdf",
      "progress": "extracting",
      "type_id": "financial_report",
      "type_name": "财务报告",
      "project_id": "finance_project",
      "create_time": "2026-08-07T09:30:00"
    }
  ]
}
```

注意事项：

- 接口最多返回 500 条，按 `create_time DESC` 排序。
- 失败态和 `complete` 不会出现在结果里。
- `type_name`、`project_id` 来自 `doc_type` 左连接，类型记录缺失时可能为 `null`。

### 3.3 `POST /file/context_query` 文件片段上下文查询

按文件 Markdown 全文查询关键词或文本片段，返回命中上下文、页码、bbox，以及可选的全部分块列表。适合前端定位某段文字或调试抽取溯源。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/file/context_query` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<FileContextQueryResponse>` |
| 副作用 | 无 |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `file_id` | string | 是 | 无 | 目标文件 ID |
| `query` | string | 是 | 无 | 查询文本；会去除首尾空白，空串 422 |
| `query_type` | string | 否 | `keyword` | `keyword` 或 `text_fragment` |
| `context_before` | integer | 否 | `200` | 命中点前截取字符数，最小 0 |
| `context_after` | integer | 否 | `200` | 命中点后截取字符数，最小 0 |
| `case_sensitive` | boolean | 否 | `false` | 是否大小写敏感 |
| `include_all_chunks` | boolean | 否 | `true` | 是否返回全部分块并标记命中 |

请求示例：

```bash
curl -X POST http://localhost:5019/file/context_query \
  -H "Content-Type: application/json" \
  -d '{"file_id":"3f2a7d4b0c2e45a98e0d6a5c1b8f9340","query":"注册资本","context_before":50,"context_after":200}'
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
    "query": "注册资本",
    "query_type": "keyword",
    "matched": true,
    "match_count": 1,
    "matches": [
      {
        "match_index": 0,
        "keyword": "注册资本",
        "position": 1280,
        "match_start_pos": 1280,
        "match_end_pos": 1284,
        "context_start_pos": 1230,
        "context_end_pos": 1484,
        "context": "公司名称：示例公司。注册资本：1000万元。经营范围...",
        "page_num": "2",
        "bboxes": [{"page_num": 2, "bbox": [88.0, 120.0, 507.0, 150.0], "page_size": [595.0, 842.0]}]
      }
    ],
    "chunks": [
      {
        "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
        "chunk_id": "9f86d081884c7d659a2feaa0c55ad015",
        "chunk_index": 0,
        "total_chunks": 12,
        "chunk_content": "...",
        "start_pos": 1000,
        "end_pos": 1500,
        "page_num": "2",
        "hit": true,
        "hit_count": 1
      }
    ]
  }
}
```

错误与边界：

| 状态 | 条件 | 说明 |
|---|---|---|
| `200` | 未命中 | `matched=false`、`match_count=0`，不视为错误 |
| `404` | 文件内容不存在 | 文件未完成 parsing 或内容记录缺失 |
| `422` | `file_id` / `query` 为空 | Pydantic 校验失败 |

### 3.4 `DELETE /file/batch` 批量删除文件

批量删除文件及关联数据。MySQL 关联表同步删除并提交，Milvus 向量和原始 PDF 通过后台任务逐个清理。

| 项 | 说明 |
|---|---|
| 方法 | `DELETE` |
| 路径 | `/file/batch` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<BatchDeleteResponse>` |
| 副作用 | 删除 MySQL 文件数据，后台删除 Milvus 向量和 `uploads/{file_id}.pdf` |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `file_ids` | string[] | 是 | 无 | 要删除的文件 ID 列表；不存在或删除异常的 ID 进入 `failed_ids` |

请求示例：

```bash
curl -X DELETE http://localhost:5019/file/batch \
  -H "Content-Type: application/json" \
  -d '{"file_ids":["3f2a7d4b0c2e45a98e0d6a5c1b8f9340","missing_id"]}'
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "deleted_count": 1,
    "failed_ids": ["missing_id"]
  }
}
```

注意事项：

- 单个 ID 失败不会回滚其它已删除文件。
- 后台清理 Milvus / PDF 失败只记录 warning，不改变已返回的删除结果。
- 删除范围包含 `file_content`、`file_table`、`file_chunk`、`extraction_result`、`analysis_result`、`files`。

### 3.5 `POST /file/parse` 上传 PDF 并启动处理管线

上传 PDF，创建全新的 `file_id` 和文件记录，并以 `async`、`sync` 或 `stream` 模式运行六阶段管线。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/file/parse` |
| Content-Type | `multipart/form-data` |
| 响应 | `async/sync` 返回 `ResponseWrapper`；`stream` 返回 SSE |
| 副作用 | 新增 `files`，落盘 PDF，写入各阶段产物，可能写 Milvus，可能触发 PDF 保留策略清理 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `mode` | string | 否 | `async` | `async` 后台处理立即返回；`sync` 阻塞到完成；`stream` 返回 SSE。源码中非 `async` / `stream` 的值按同步处理 |
| `type_id` | string | 否 | `default` | 文档类型 ID，决定字段/规则配置作用域 |
| `callback_url` | string | 否 | 无 | `async` / `sync` 模式可用；阶段开始、阶段完成、字段/规则完成都会 POST 回调；`stream` 模式忽略 |

表单字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `file` | file | 是 | PDF 文件；大小受 `mineru.max_file_size` 限制 |

异步请求示例：

```bash
curl -X POST "http://localhost:5019/file/parse?type_id=default&mode=async" \
  -F "file=@report.pdf"
```

同步请求示例：

```bash
curl -X POST "http://localhost:5019/file/parse?type_id=default&mode=sync&callback_url=http://127.0.0.1:9000/callback" \
  -F "file=@report.pdf"
```

流式请求示例：

```bash
curl -N -X POST "http://localhost:5019/file/parse?type_id=default&mode=stream" \
  -F "file=@report.pdf"
```

异步响应示例：

```json
{
  "code": 200,
  "message": "文件已提交处理（异步）",
  "data": {"file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340"}
}
```

同步响应示例：

```json
{
  "code": 200,
  "message": "文件处理完成",
  "data": {"file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340"}
}
```

错误与边界：

| 状态 | 条件 | 说明 |
|---|---|---|
| HTTP `200` + `code=400` | 文件超过大小限制 | 响应 `message` 包含限制大小 |
| `500` | 同步模式管线异常 | 异常向上传播为接口失败 |
| 后台日志错误 | 异步模式管线异常 | 接口已返回；失败写入文件状态和日志 |

关键细节：

- 每次上传都会生成新 `file_id`，不会因为文件名相同而复用旧记录。
- 上传时会尝试把原始 PDF 写到 `uploads/{file_id}.pdf`，VL 字段和 PDF 预览依赖该文件；写盘失败只记 warning，不阻断管线。
- `callback_url` 每次回调超时 2.5 秒，失败不影响主流程。
- `mode=stream` 的响应是 SSE，不再返回 JSON 信封。

### 3.6 `GET /file/{file_id}/status` 查询文件处理状态

查询单个文件的基础信息、当前进度和错误文本。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/status` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<FileStatusResponse>` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `file_id` | string | 是 | 目标文件 ID |

请求示例：

```bash
curl "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/status"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
    "file_name": "年度报告.pdf",
    "file_size": 2048576,
    "progress": "extracting",
    "type_id": "default",
    "error": null,
    "create_time": "2026-08-07T09:30:00",
    "updated_at": "2026-08-07T09:31:20"
  }
}
```

错误：`404` 表示文件不存在。

### 3.7 `GET /file/{file_id}/pdf` 下载/预览原始 PDF

返回上传时落盘的原始 PDF 字节，供前端 PDF 预览和定位。响应不是 JSON。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/pdf` |
| 请求体 | 无 |
| 响应 | `application/pdf` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `file_id` | string | 是 | 文件 ID；源码会做 `[\\w-]+` 白名单校验防路径穿越 |

请求示例：

```bash
curl -L "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/pdf" -o preview.pdf
```

响应头要点：

| Header | 说明 |
|---|---|
| `Content-Type: application/pdf` | PDF 原始字节 |
| `Content-Disposition: inline; filename="{file_id}.pdf"` | 浏览器内联预览 |

错误：

| 状态 | 条件 |
|---|---|
| `404` | PDF 不存在、历史文件未落盘、被保留策略清理、`file_id` 不符合白名单 |

### 3.8 `DELETE /file/{file_id}` 删除单个文件

删除单个文件及全部关联数据。MySQL 同步提交，Milvus 向量和 PDF 后台清理。

| 项 | 说明 |
|---|---|
| 方法 | `DELETE` |
| 路径 | `/file/{file_id}` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<null>` |
| 副作用 | 删除 `files` 及阶段产物、结果；后台清理 Milvus 和 PDF |

请求示例：

```bash
curl -X DELETE "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340"
```

响应示例：

```json
{"code": 200, "message": "文件已删除", "data": null}
```

错误：`404` 表示文件不存在。

### 3.9 `POST /file/{file_id}/retry/{stage}` 从指定阶段重试

清理目标阶段及下游数据，从指定阶段重新执行管线。适合失败文件修复配置或外部依赖后重跑。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/file/{file_id}/retry/{stage}` |
| 请求体 | 无 |
| 响应 | `async/sync` 返回 `ResponseWrapper`；`stream` 返回 SSE |
| 副作用 | 重置目标阶段及下游阶段时间戳，清理下游数据，重新写入阶段产物和结果 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `file_id` | string | 是 | 目标文件 ID |
| `stage` | string | 是 | `tableing`、`chunking`、`embedding`、`extracting`、`analyzing`；兼容旧别名 `table_name_validating` -> `tableing` |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `mode` | string | 否 | `async` | `async` / `sync` / `stream` |
| `callback_url` | string | 否 | 无 | `async` / `sync` 模式下接收回调；`stream` 忽略 |

请求示例：

```bash
curl -X POST "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/retry/extracting?mode=async"
```

同步响应示例：

```json
{"code": 200, "message": "已从 extracting 阶段重试完成", "data": null}
```

异步响应示例：

```json
{"code": 200, "message": "已从 extracting 阶段开始重试", "data": null}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | `stage` 不在有效集合中 |
| `404` | 文件不存在 |

### 3.10 `POST /file/{file_id}/retry/extracting` 快捷重试字段提取

等价于 `POST /file/{file_id}/retry/extracting` 的专用路由，内部转发到通用 retry，起点固定为 `extracting`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `file_id` | path string | 是 | 无 | 目标文件 ID |
| `mode` | query string | 否 | `async` | `async` / `sync` / `stream` |
| `callback_url` | query string | 否 | 无 | 回调地址 |

请求示例：

```bash
curl -X POST "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/retry/extracting?mode=sync"
```

响应示例：

```json
{"code": 200, "message": "已从 extracting 阶段重试完成", "data": null}
```

### 3.11 `POST /file/{file_id}/retry/analyzing` 快捷重试逻辑分析

等价于通用 retry 的 `stage=analyzing`。只清理并重跑逻辑分析阶段，不重新抽取字段。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `file_id` | path string | 是 | 无 | 目标文件 ID |
| `mode` | query string | 否 | `async` | `async` / `sync` / `stream` |
| `callback_url` | query string | 否 | 无 | 回调地址 |

请求示例：

```bash
curl -X POST "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/retry/analyzing?mode=async"
```

响应示例：

```json
{"code": 200, "message": "已从 analyzing 阶段开始重试", "data": null}
```

### 3.12 `GET /file/{file_id}/tables` 查询文件表格列表

返回 tableing 阶段从 Markdown 中抽出的所有 HTML 表格，按 `table_index` 升序排列。表名由 LLM 从表格前文识别，失败时回退为表格前最后一行。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/tables` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<FileTableItem[]>` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `file_id` | string | 是 | 目标文件 ID |

请求示例：

```bash
curl "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/tables"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
      "table_index": 0,
      "total_table": 2,
      "table_name": "合并资产负债表",
      "table_content": "<table><tr><td>项目</td><td>期末余额</td></tr></table>",
      "page_num": "12"
    }
  ]
}
```

注意事项：

- 文件不存在但没有表格记录时，当前实现返回空数组，不返回 404。
- `table_content` 是 HTML `<table>` 片段，不是 Markdown 表格。
- `page_num` 是字符串，可能为空，也可能是范围如 `3-5`。

### 3.13 `GET /file/{file_id}/chunks` 查询文件分块列表

返回 chunking 阶段生成的文本分块。普通文本按递归分隔符切分；表格会作为独立 chunk 保留，超长表格按行/单元格边界拆分。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/chunks` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<FileChunkItem[]>` |
| 副作用 | 无 |

请求示例：

```bash
curl "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/chunks"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
      "chunk_id": "9f86d081884c7d659a2feaa0c55ad015",
      "chunk_index": 0,
      "total_chunks": 15,
      "chunk_content": "# 1 公司简介\n\n示例公司成立于...",
      "page_num": "1"
    }
  ]
}
```

注意事项：文件不存在或尚未分块时返回空数组；不做 404 检查。

### 3.14 `POST /file/{file_id}/recompute_page_mapping` 重算页码映射

用已落库的 Markdown 和 MinerU `middle_json` 重新构建 `page_mapping` 并写回 `file_content`。用于旧文件升级页码/bbox 映射算法后免重新上传刷新定位数据。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/file/{file_id}/recompute_page_mapping` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<object>` |
| 副作用 | 更新 `file_content.page_mapping` |

请求示例：

```bash
curl -X POST "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/recompute_page_mapping"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
    "anchor_count": 128,
    "page_min": 1,
    "page_max": 42
  }
}
```

错误与边界：

- `404`：文件内容不存在或全文为空。
- `middle_json` 缺失时不会报错，会写入空映射，`anchor_count=0`。

### 3.15 `GET /file/{file_id}/outline` 查询章节大纲

基于 Markdown 正则解析章节列表，口径与抽取阶段 `search_type=section` 一致。每个章节返回自身正文、子树正文、起止位置等信息。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/outline` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<object[]>` |
| 副作用 | 无 |

请求示例：

```bash
curl "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/outline"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "index": 0,
      "number": "1",
      "title": "公司简介",
      "level": 1,
      "numbered": true,
      "content": "# 1 公司简介\n\n示例公司...",
      "tree_content": "# 1 公司简介\n\n示例公司...",
      "start_pos": 0,
      "end_pos": 1200,
      "tree_end_pos": 1800
    }
  ]
}
```

注意事项：文件不存在、内容为空或无法解析章节时返回 `[]`，不返回 404。

### 3.16 `GET /file/{file_id}/content` 按页返回 Markdown 内容

基于 `file_content.page_mapping` 将整篇 Markdown 切成逐页内容，按页码升序返回。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/content` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<{page_num,content}[]>` |
| 副作用 | 无 |

请求示例：

```bash
curl "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/content"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {"page_num": 1, "content": "# 1 公司简介\n\n示例公司..."},
    {"page_num": 2, "content": "## 1.1 基本情况\n\n注册资本..."}
  ]
}
```

注意事项：无内容或无 `page_mapping` 时返回 `[]`。该接口返回的是按页切分后的 Markdown，不包含 `middle_json` 原始布局。

### 3.17 `GET /file/{file_id}/extraction` 查询字段提取结果

返回 `extraction_result` 中该文件的字段提取结果，并左连接字段配置补充 `field_name`。输出会把模型自报页码提升为顶层 `pages`，并派生 `source_pages`。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/extraction` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ExtractionResultItem[]>` |
| 副作用 | 无 |

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `file_id` | string | 文件 ID |
| `field_id` | string | 字段 ID |
| `field_name` | string/null | 字段名；配置被删时可能为 `null` |
| `extracted_value` | string | 抽取值；失败字段通常为空串 |
| `reason` | string/null | 模型理由或异常说明 |
| `pages` | integer[] | 模型自报参考页。VL、`use_llm=0`、模型未返回时为 `[]` |
| `source_pages` | integer[] | 可用页码，优先 `pages`，无则从 `source_refs` 命中页派生；恒为数组 |
| `source_refs` | object/null | 溯源结构，含文本/表格命中、bbox、VL 元数据等 |

请求示例：

```bash
curl "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/extraction"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
      "field_id": "company_name",
      "field_name": "公司名称",
      "extracted_value": "示例科技有限公司",
      "reason": "正文第 1 页公司基本情况处明确写明公司名称。",
      "pages": [1],
      "source_pages": [1],
      "source_refs": {
        "公司名称": [
          {"type": "context", "start_pos": 120, "end_pos": 260, "page_num": "1", "text": "公司名称：示例科技有限公司"}
        ],
        "_texts": {"公司名称": "公司名称：示例科技有限公司"}
      }
    }
  ]
}
```

注意事项：

- 即使文件不存在，当前实现也只是查不到结果并返回 `[]`。
- `source_pages` 是输出时现算，不落库。
- 老数据中若模型页码仍藏在 `source_refs._model_pages`，读取时会兼容并从输出的 `source_refs` 中剔除旧键。

### 3.18 `GET /file/{file_id}/analysis` 查询逻辑分析结果

返回 `analysis_result` 中该文件的分析结果，并左连接规则配置补充 `rule_name`。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/analysis` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<AnalysisResultItem[]>` |
| 副作用 | 无 |

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `file_id` | string | 文件 ID |
| `rule_id` | string | 规则 ID |
| `rule_name` | string/null | 规则名；配置被删时可能为 `null` |
| `result_value` | string | judge/calc/custom 结果 |
| `input_values` | object/null | 分析时依赖字段值快照 |
| `reason` | string/null | 判断/计算/生成理由 |
| `source_refs` | object/null | 依赖字段溯源，启用 web_search 时含 `_web_search` |

请求示例：

```bash
curl "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/analysis"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
      "rule_id": "profit_positive",
      "rule_name": "是否盈利",
      "result_value": "true",
      "input_values": {"net_profit": "5000000"},
      "reason": "净利润大于 0，因此判断为盈利。",
      "source_refs": {"net_profit": {"_texts": {"净利润": "净利润 5000000 元"}}}
    }
  ]
}
```

注意事项：文件不存在或尚未分析完成时返回 `[]`。

### 3.19 `GET /file/{file_id}/detail` 查询完整文件详情

在 `/status` 的基础上返回六阶段全部开始/结束时间戳，适合前端时间线和排障。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/{file_id}/detail` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<FileDetailResponse>` |
| 副作用 | 无 |

请求示例：

```bash
curl "http://localhost:5019/file/3f2a7d4b0c2e45a98e0d6a5c1b8f9340/detail"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
    "file_name": "年度报告.pdf",
    "file_size": 2048576,
    "progress": "complete",
    "type_id": "default",
    "error": null,
    "create_time": "2026-08-07T09:30:00",
    "updated_at": "2026-08-07T09:33:30",
    "start_parsing_time": "2026-08-07T09:30:01",
    "end_parsing_time": "2026-08-07T09:31:00",
    "start_tableing_time": "2026-08-07T09:31:00",
    "end_tableing_time": "2026-08-07T09:31:05",
    "start_chunking_time": "2026-08-07T09:31:05",
    "end_chunking_time": "2026-08-07T09:31:06",
    "start_embedding_time": "2026-08-07T09:31:06",
    "end_embedding_time": "2026-08-07T09:31:20",
    "start_extracting_time": "2026-08-07T09:31:20",
    "end_extracting_time": "2026-08-07T09:33:00",
    "start_analyzing_time": "2026-08-07T09:33:00",
    "end_analyzing_time": "2026-08-07T09:33:30"
  }
}
```

错误：`404` 表示文件不存在。

---

## 4. 字段提取接口 `/extraction`

字段配置决定 extracting 阶段从哪里找资料、是否调用 LLM/VL、如何把模型输出解析为 `{value, reason, pages}`。字段配置按 `type_id` 隔离，但 `field_id` 在当前实现里是全局唯一。

字段来源类型：

| `source_type` | 说明 | 关键配置 |
|---|---|---|
| `table` | 从 tableing 阶段抽出的 HTML 表格里匹配表名，再抽取字段 | `table_match_type`、`table_match_keywords`、`table_extract_prompt` |
| `text` | 从 Markdown 全文或分块中检索上下文，再抽取字段 | `search_type`、`search_config`、`text_extract_prompt` |
| `vl` | 直接读取 `uploads/{file_id}.pdf` 调视觉模型抽取 | `vl_method`、`vl_config`、`vl_extract_prompt` |

### 4.1 `GET /extraction/match-prompt-defaults` 获取提示词模板默认值

返回后端内置的章节匹配、表格匹配和 VL 辅助提示词模板。前端用它渲染默认值、做“恢复默认”和“是否改过”判断。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/extraction/match-prompt-defaults` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<object>` |
| 副作用 | 无 |

请求示例：

```bash
curl "http://localhost:5019/extraction/match-prompt-defaults"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "section": "请从以下章节列表中选择最相关的章节...",
    "table": "请从以下表格列表中选择最相关的表格...",
    "output_instruction": "请只输出 JSON...",
    "vl_batch": "你正在分批阅读 PDF...",
    "vl_locate": "请在缩略图网格中定位关键页..."
  }
}
```

注意事项：该接口不查库，返回的是运行代码中的系统默认模板。

### 4.2 `GET /extraction/fields` 查询字段配置

按 `priority` 升序返回字段配置。`type_id` 为空时返回所有类型的字段。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/extraction/fields` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ExtractionFieldResponse[]>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `type_id` | string | 否 | `""` | 按文档类型精确过滤；空串返回全量 |

请求示例：

```bash
curl "http://localhost:5019/extraction/fields?type_id=financial_report"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "field_id": "company_name",
      "type_id": "financial_report",
      "field_name": "公司名称",
      "source_type": "text",
      "enabled": 1,
      "priority": 0,
      "use_llm": 1,
      "table_name_pattern": null,
      "table_match_type": null,
      "table_match_keywords": null,
      "table_match_max_results": null,
      "table_system_prompt": null,
      "table_match_prompt": null,
      "table_extract_prompt": null,
      "search_type": "context",
      "search_config": {"keywords": ["公司名称"], "context_after": 200},
      "text_system_prompt": null,
      "text_extract_prompt": "从内容提取公司名称：\n<search_result>命中片段</search_result>\n输出 JSON。",
      "vl_method": null,
      "vl_config": null,
      "vl_system_prompt": null,
      "vl_extract_prompt": null,
      "is_advanced": 0,
      "depend_fields": null,
      "created_at": "2026-08-07T09:00:00",
      "updated_at": "2026-08-07T09:00:00"
    }
  ]
}
```

### 4.3 `POST /extraction/fields` 新增/更新字段配置

按 `field_id` upsert 字段配置。若已存在字段属于其它 `type_id`，返回 409。该接口只修改配置，不自动重跑已有文件；要让旧文件使用新配置，需要调用 `/file/{file_id}/retry/extracting` 或从更早阶段重试。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/extraction/fields` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<{field_id}>` |
| 副作用 | 新增或更新 `extraction_field` |

通用请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `field_id` | string | 是 | 无 | 字段 ID，`^[a-zA-Z0-9_]+$`，最长 100，全局唯一 |
| `type_id` | string | 否 | `default` | 文档类型 ID |
| `field_name` | string | 是 | 无 | 字段显示名，最长 200 |
| `source_type` | string | 是 | 无 | `table` / `text` / `vl` |
| `enabled` | integer | 否 | `1` | 是否启用，1/0 |
| `priority` | integer | 否 | `0` | 执行优先级，越小越先 |
| `use_llm` | integer | 否 | `1` | text/table 是否走 LLM 二次抽取；`0` 表示直接返回检索原文。VL 不受此开关影响 |
| `is_advanced` | integer | 否 | `0` | 是否进阶字段。进阶字段在普通字段完成后执行，可引用普通字段结果 |
| `depend_fields` | string[] | 否 | `null` | 请求传入会被服务端扫描结果覆盖；响应中返回实际依赖 |

table 字段参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `table_name_pattern` | string | 否 | `null` | 表名匹配模式，兼容旧配置 |
| `table_match_type` | string | 否 | `null` | `exact` / `fuzzy` / `contains` / `llm` |
| `table_match_keywords` | string[] | 否 | `null` | 匹配关键词列表，通常优先于 `table_name_pattern` |
| `table_match_max_results` | integer | 否 | `null` | 最多命中表数；空或 0 表示不限 |
| `table_system_prompt` | string | 否 | `null` | 表格抽取 system prompt |
| `table_match_prompt` | string | 否 | `null` | LLM 表格匹配模板；`table_match_type=llm` 且非空时必须包含 `{table_list}` |
| `table_extract_prompt` | string | 条件必填 | `null` | `source_type=table` 且 `use_llm=1` 时必填，并必须包含 `<search_result>标签</search_result>` |

text 字段参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `search_type` | string | 否 | `context` 逻辑兜底 | `context` / `section` / `rule` / `chunk_db` / `vector_db` / `page` |
| `search_config` | object | 否 | `{}` | 检索配置，键随 `search_type` 变化 |
| `text_system_prompt` | string | 否 | `null` | 文本抽取 system prompt |
| `text_extract_prompt` | string | 条件必填 | `null` | `source_type=text` 且 `use_llm=1` 时必填，并必须包含 `<search_result>标签</search_result>` |

常参见本文内 `search_config`：

| `search_type` | 常用键 | 说明 |
|---|---|---|
| `context` | `keywords`、`context_before`、`context_after`、`max_results`、`sort_order` | 关键词命中并截取前后文 |
| `section` | `section_pattern`、`section_match_type`/`match_type`、`threshold`、`max_results`、`section_match_prompt` | 匹配章节标题；LLM 匹配模板必须含 `{section_list}` |
| `rule` | `keywords`、`stop_words`、`direction`、`min_length`、`max_length`、`max_results` | 从关键词到停止词边界截取 |
| `chunk_db` | `keywords`、`keyword_filter`、`max_results`/`top_k` | MySQL 分块检索 |
| `vector_db` | `query_text`、`top_k`、`score_threshold` | Milvus 语义检索 |
| `page` | `page_range`、`max_length`、`page_source_field`、`max_pages` | 按页切 Markdown；进阶字段可由来源字段页码联动 |

VL 字段参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `vl_method` | string | 条件必填 | `null` | `vl_model` / `vl_progressive` / `vl_locate`；`source_type=vl` 必填 |
| `vl_config` | object | 否 | `{}` | VL 方法配置 |
| `vl_system_prompt` | string | 否 | `null` | VL system prompt |
| `vl_extract_prompt` | string | 条件必填 | `null` | `source_type=vl` 必填，并必须包含 `value` 与 `reason` 关键字 |

常参见本文内 `vl_config`：

| 方法 | 常用键 | 说明 |
|---|---|---|
| 全部 | `page_range`、`max_pages`、`max_pixels`、`page_source_field` | 页范围、候选页上限、像素上限、进阶字段页码联动 |
| `vl_model` | 无额外必填 | 指定页一次送入 VL |
| `vl_progressive` | `field_hints`、`batch_size`、`batch_prompt_template` | 分批扫描并累积伪历史；自定义模板需含 `{field_hints}`、`{page_label}`、`{total_pages}`、`{history}` |
| `vl_locate` | `field_hints`、`grid_pages`、`grid_cols`、`max_concurrent`、`key_pages_limit`、`fallback_pages`、`locate_prompt_template` | 缩略图定位关键页再高清抽取；定位模板需含 `{field_hints}`、`{page_labels}`、`{position_map}`、`{grid_rows}`、`{grid_cols}` |

text 字段请求示例：

```json
{
  "field_id": "company_name",
  "type_id": "financial_report",
  "field_name": "公司名称",
  "source_type": "text",
  "enabled": 1,
  "priority": 0,
  "use_llm": 1,
  "search_type": "context",
  "search_config": {"keywords": ["公司名称"], "context_before": 50, "context_after": 200, "max_results": 3},
  "text_extract_prompt": "从以下内容提取公司名称，输出 JSON：\n<search_result>命中片段</search_result>\n必须包含 value 和 reason。"
}
```

table 字段请求示例：

```json
{
  "field_id": "total_assets",
  "type_id": "financial_report",
  "field_name": "资产总额",
  "source_type": "table",
  "table_match_type": "contains",
  "table_match_keywords": ["资产负债表"],
  "table_match_max_results": 2,
  "table_extract_prompt": "从表格中提取资产总额，输出 JSON：\n<search_result>资产负债表</search_result>\n必须包含 value 和 reason。"
}
```

VL 字段请求示例：

```json
{
  "field_id": "signature_date",
  "type_id": "contract",
  "field_name": "签署日期",
  "source_type": "vl",
  "vl_method": "vl_locate",
  "vl_config": {"page_range": "all", "field_hints": "寻找签署页、盖章页或日期", "grid_pages": 6, "key_pages_limit": 3},
  "vl_extract_prompt": "请从关键页面提取签署日期，返回 JSON，必须包含 value 和 reason。"
}
```

curl 示例：

```bash
curl -X POST http://localhost:5019/extraction/fields \
  -H "Content-Type: application/json" \
  -d @field.json
```

响应示例：

```json
{"code": 200, "message": "字段配置已创建", "data": {"field_id": "company_name"}}
```

错误与校验：

| 状态 | 条件 | 说明 |
|---|---|---|
| `400` | 进阶字段引用不存在字段、引用其它进阶字段、被引用普通字段试图改为进阶字段 | 业务校验失败 |
| `409` | `field_id` 已被其它 `type_id` 占用 | 当前实现要求全局唯一 |
| `422` | ID 正则不合法、提示词缺占位符、VL 缺 `value/reason` 等 | Pydantic 校验失败 |

进阶字段说明：

- `is_advanced=1` 时，字段在普通字段全部抽完后执行。
- 配置文本中可用 `<field_result>字段ID</field_result>` 引用同类型普通字段结果。
- 服务端会扫描配置并写入 `depend_fields`，请求体传入值会被覆盖。
- 进阶字段只能引用普通字段，不能引用另一个进阶字段。
- `search_type=page` 或 VL 配置可用 `page_source_field` 根据来源字段的模型自报页码联动目标页。

### 4.4 `DELETE /extraction/fields/{field_id}` 删除字段配置

硬删除字段配置。历史 `extraction_result` 不级联清理；因此旧文件结果仍可从 `/file/{file_id}/extraction` 读到，但 `field_name` 可能变为 `null`。

| 项 | 说明 |
|---|---|
| 方法 | `DELETE` |
| 路径 | `/extraction/fields/{field_id}` |
| 查询参数 | `force` 可选 |
| 响应 | `ResponseWrapper<null>` |
| 副作用 | 删除 `extraction_field` 记录 |

路径和查询参数：

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|:--:|---|---|
| `field_id` | path | string | 是 | 无 | 字段 ID |
| `force` | query | boolean | 否 | `false` | 字段被进阶字段引用时，默认 409；`true` 强制删除 |

请求示例：

```bash
curl -X DELETE "http://localhost:5019/extraction/fields/company_name?force=false"
```

响应示例：

```json
{"code": 200, "message": "字段配置已删除", "data": null}
```

错误：

| 状态 | 条件 |
|---|---|
| `404` | 字段配置不存在 |
| `409` | 字段被同类型进阶字段引用且未传 `force=true` |

### 4.5 `GET /extraction/fields/{field_id}/check` 检查字段 ID 是否存在

保存前查重接口。查的是全局 `field_id`，不是某个 `type_id` 下的局部 ID。

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `field_id` | path string | 是 | 字段 ID |

请求示例：

```bash
curl "http://localhost:5019/extraction/fields/company_name/check"
```

响应示例：

```json
{"code": 200, "message": "success", "data": {"exists": true}}
```

### 4.6 `POST /extraction/test` 字段提取同步调试

用已保存字段配置或临时配置对单个文件执行一次字段提取调试，返回检索结果、提示词、模型输出、解析值、页码等信息。调试结果不落库。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/extraction/test` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<ExtractionTestResponse>` |
| 副作用 | 无；只调用检索/LLM/VL |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `file_id` | string | 是 | 无 | 目标文件 ID |
| `field_id` | string | 条件 | `null` | 已保存字段配置 ID；与 `config` 二选一 |
| `config` | object | 条件 | `null` | 临时字段配置，字段名同 `POST /extraction/fields`；与 `field_id` 二选一 |

使用已保存配置示例：

```json
{"file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340", "field_id": "company_name"}
```

使用临时配置示例：

```json
{
  "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
  "config": {
    "field_id": "__test__",
    "field_name": "注册资本",
    "source_type": "text",
    "search_type": "context",
    "search_config": {"keywords": ["注册资本"], "context_after": 100},
    "text_extract_prompt": "提取注册资本：\n<search_result>命中片段</search_result>\n输出 JSON，包含 value/reason。"
  }
}
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "search_results": [
      {"type": "context", "keyword": "注册资本", "text": "注册资本：1000万元", "page_num": "2"}
    ],
    "llm_input": "提取注册资本：\n<search_result>命中片段</search_result>...",
    "llm_output": "1000万元",
    "extracted_value": "1000万元",
    "reason": "命中片段中直接写明注册资本。",
    "pages": [2],
    "source_pages": [2],
    "resolved_refs": null
  }
}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | 既未传 `field_id` 也未传 `config`；进阶字段引用解析失败 |
| `404` | 字段配置不存在、文件内容不存在 |
| `500` | 检索、LLM、VL 调用异常 |

### 4.7 `POST /extraction/test/stream` 字段提取流式调试

入参与 `/extraction/test` 相同，但通过 SSE 分步返回检索、提示词、模型响应和最终结果。适合前端调试面板实时展示。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/extraction/test/stream` |
| Content-Type | `application/json` |
| 响应 | `text/event-stream` |
| 副作用 | 无；只调用检索/LLM/VL |

请求示例：

```bash
curl -N -X POST http://localhost:5019/extraction/test/stream \
  -H "Content-Type: application/json" \
  -d '{"file_id":"3f2a7d4b0c2e45a98e0d6a5c1b8f9340","field_id":"company_name"}'
```

典型事件：

```text
event: search_result
data: {"results":[...]}

event: prompt
data: {"prompt":"..."}

event: llm_response
data: {"response":"..."}

event: result
data: {"extracted_value":"示例科技有限公司","reason":"...","pages":[1]}

event: done
data: {"ok":true}
```

特殊事件：

- `resolved_refs`：仅进阶字段出现，位于最前，展示 `<field_result>` 引用解析结果和页码联动信息。
- `pdf_loaded`、`progressive_batch`、`locate_locate`、`locate_extract`：仅 VL 字段调试流出现。
- `error`：调试过程失败后终止。

注意事项：流式调试的 `result` 事件不保证携带完整 `source_refs` 或 `source_pages`；命中页码应看 `search_result` 或 VL 进度事件。

---

## 5. 逻辑分析接口 `/analysis`

逻辑分析规则在 analyzing 阶段消费字段提取结果，支持三类规则：`judge` 用 LLM 判断，`calc` 用 `numexpr` 计算，`custom` 用 LLM 自由生成，可选结构化输出。规则配置按 `type_id` 隔离，但 `rule_id` 当前实现全局唯一。

### 5.1 `GET /analysis/rules` 查询分析规则

按 `priority` 升序返回规则。`type_id` 为空返回所有类型规则。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/analysis/rules` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<AnalysisRuleResponse[]>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `type_id` | string | 否 | `""` | 文档类型精确过滤；空串返回全量 |

请求示例：

```bash
curl "http://localhost:5019/analysis/rules?type_id=financial_report"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "rule_id": "profit_positive",
      "type_id": "financial_report",
      "rule_name": "是否盈利",
      "rule_type": "judge",
      "expression": "净利润为 <field_result>net_profit</field_result>，请判断是否盈利。",
      "system_prompt": "你是财务分析助手。",
      "depend_fields": ["net_profit"],
      "web_search": null,
      "is_formatted": 0,
      "output_schema": null,
      "enabled": 1,
      "priority": 0,
      "created_at": "2026-08-07T09:00:00",
      "updated_at": "2026-08-07T09:00:00"
    }
  ]
}
```

### 5.2 `POST /analysis/rules` 新增/更新分析规则

按 `rule_id` upsert 规则。若同 ID 已存在且属于其它 `type_id`，返回 409。修改规则不会自动重跑历史文件；需要调用 `/file/{file_id}/retry/analyzing`。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/analysis/rules` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<{rule_id}>` |
| 副作用 | 新增或更新 `analysis_rule` |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `rule_id` | string | 是 | 无 | 规则 ID，`^[a-zA-Z0-9_]+$`，最长 100，全局唯一 |
| `type_id` | string | 否 | `default` | 文档类型 ID |
| `rule_name` | string | 是 | 无 | 规则显示名，最长 200 |
| `rule_type` | string | 是 | 无 | `judge` / `calc` / `custom` |
| `expression` | string | 是 | 无 | 表达式或提示词，必须包含至少一个 `<field_result>字段ID</field_result>` |
| `system_prompt` | string | 否 | `null` | `judge` / `custom` 的 system prompt；`calc` 忽略 |
| `depend_fields` | string[] | 否 | `null` | 依赖字段 ID 列表，执行时取这些字段值 |
| `web_search` | object | 否 | `null` | `judge` / `custom` 可用的联网搜索配置 |
| `is_formatted` | integer | 否 | `0` | `custom` 是否按 `output_schema` 结构化输出 |
| `output_schema` | object[] | 否 | `null` | `custom` 结构化输出字段树；`is_formatted=1` 时必填且需合法 |
| `enabled` | integer | 否 | `1` | 是否启用 |
| `priority` | integer | 否 | `0` | 执行优先级，越小越先 |

`web_search` 参数：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `enabled` | boolean | 是 | 是否启用联网搜索 |
| `query` | string | 启用时必填 | 搜索词，可包含 `<field_result>字段ID</field_result>` |
| `count` | integer | 否 | 返回结果数 |
| `freshness` | string | 否 | 时间范围参数，由底层 web search 服务解释 |

启用 `web_search` 时，`rule_type` 必须是 `judge` 或 `custom`，`query` 非空，且 `expression` 必须含 `<web_search_result/>` 占位符。

judge 示例：

```json
{
  "rule_id": "profit_positive",
  "type_id": "financial_report",
  "rule_name": "是否盈利",
  "rule_type": "judge",
  "expression": "净利润为 <field_result>net_profit</field_result>，请判断公司是否盈利，只返回 true 或 false 并说明理由。",
  "system_prompt": "你是严谨的财务分析助手。",
  "depend_fields": ["net_profit"],
  "enabled": 1,
  "priority": 0
}
```

calc 示例：

```json
{
  "rule_id": "profit_margin",
  "type_id": "financial_report",
  "rule_name": "净利率",
  "rule_type": "calc",
  "expression": "<field_result>net_profit</field_result> / <field_result>revenue</field_result> * 100",
  "depend_fields": ["net_profit", "revenue"],
  "enabled": 1,
  "priority": 1
}
```

custom 结构化输出示例：

```json
{
  "rule_id": "shareholder_summary",
  "type_id": "financial_report",
  "rule_name": "股东结构摘要",
  "rule_type": "custom",
  "expression": "根据以下信息汇总股东结构：<field_result>shareholders</field_result>",
  "depend_fields": ["shareholders"],
  "is_formatted": 1,
  "output_schema": [
    {"key": "总股东数", "type": "number", "example": "3"},
    {"key": "主要股东", "type": "array", "children": [
      {"key": "名称", "type": "string", "example": "张三"},
      {"key": "持股比例", "type": "string", "example": "51%"}
    ]}
  ]
}
```

响应示例：

```json
{"code": 200, "message": "规则配置已创建", "data": {"rule_id": "profit_positive"}}
```

错误与校验：

| 状态 | 条件 |
|---|---|
| `409` | `rule_id` 已被其它 `type_id` 占用 |
| `422` | `expression` 缺 `<field_result>`、`web_search` 配置非法、`output_schema` 结构非法 |

### 5.3 `DELETE /analysis/rules/{rule_id}` 删除分析规则

硬删除分析规则。历史 `analysis_result` 不级联清理；旧结果仍可读，但 `rule_name` 可能为 `null`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `rule_id` | path string | 是 | 规则 ID |

请求示例：

```bash
curl -X DELETE "http://localhost:5019/analysis/rules/profit_positive"
```

响应示例：

```json
{"code": 200, "message": "规则配置已删除", "data": null}
```

错误：`404` 表示规则配置不存在。

### 5.4 `GET /analysis/rules/{rule_id}/check` 检查规则 ID 是否存在

保存前查重接口。查的是全局 `rule_id`。

请求示例：

```bash
curl "http://localhost:5019/analysis/rules/profit_positive/check"
```

响应示例：

```json
{"code": 200, "message": "success", "data": {"exists": true}}
```

### 5.5 `POST /analysis/test` 逻辑分析同步调试

用已保存规则或临时规则配置，对某文件当前已落库的 `extraction_result` 执行一次分析调试。调试结果不落库。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/analysis/test` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<AnalysisTestResponse>` |
| 副作用 | 无；可能调用 LLM / web_search |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `file_id` | string | 是 | 无 | 目标文件 ID，依赖字段值来自该文件的 `extraction_result` |
| `rule_id` | string | 条件 | `null` | 已保存规则 ID；与 `config` 二选一 |
| `config` | object | 条件 | `null` | 临时规则配置；与 `rule_id` 二选一 |

请求示例：

```bash
curl -X POST http://localhost:5019/analysis/test \
  -H "Content-Type: application/json" \
  -d '{"file_id":"3f2a7d4b0c2e45a98e0d6a5c1b8f9340","rule_id":"profit_positive"}'
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "input_values": {"net_profit": "5000000"},
    "expression_resolved": "净利润为 5000000，请判断公司是否盈利，只返回 true 或 false 并说明理由。",
    "result_value": "true",
    "reason": "净利润大于 0，因此公司盈利。"
  }
}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | 既未传 `rule_id` 也未传 `config` |
| `404` | 规则不存在 |
| `500` | LLM、计算、web_search 或内部执行异常 |

### 5.6 `POST /analysis/run` 独立逻辑分析执行

不依赖文件处理管线，直接对外部传入字段值或某文件已落库提取结果执行启用规则。支持批量 `items`、`sync` / `async` / `stream` 三种模式。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/analysis/run` |
| Content-Type | `application/json` |
| 响应 | `sync/async` 返回 `ResponseWrapper`；`stream` 返回 SSE |
| 副作用 | `persist=true` 且 `source=file` 时写入 `analysis_result`；不修改 `files.progress` |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `mode` | string | 是 | 无 | `sync` / `async` / `stream` |
| `source` | string | 否 | `values` | `values` 使用请求字段值；`file` 从文件 `extraction_result` 读取 |
| `persist` | boolean | 否 | `false` | 是否写入 `analysis_result`；仅 `source=file` 可用 |
| `callback_url` | string | 条件 | `null` | `async` 模式必填 |
| `items` | object[] | 是 | 无 | 待分析输入组，至少 1 个 |

`items[]` 参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `type_id` | string | 条件 | `null` | `source=values` 必填；`source=file` 可省略，省略时取 `files.type_id` |
| `biz_id` | string | 是 | 无 | 调用方业务 ID，原样回传 |
| `field_values` | object | 条件 | `{}` | `source=values` 使用；`source=file` 禁传 |
| `rule_ids` | string[]/null | 否 | `null` | 不传/null 跑全部启用规则；空数组不跑；非空只跑指定规则 |
| `file_id` | string | 条件 | `null` | `source=file` 必填；`source=values` 禁传 |

`rule_ids` 语义：

| 取值 | 执行范围 | 缺依赖处理 |
|---|---|---|
| 不传 / `null` | 该类型全部启用规则 | 静默跳过依赖不满足的规则 |
| `[]` | 不执行任何规则 | 返回空 `results` |
| `['a','b']` | 只跑指定且启用的规则 | 缺依赖会产出 `success=false` 结果；不存在/未启用进入 `unknown_rule_ids` |

`source=values` 同步请求示例：

```json
{
  "mode": "sync",
  "source": "values",
  "items": [
    {
      "type_id": "financial_report",
      "biz_id": "doc-001",
      "field_values": {"net_profit": "5000000", "revenue": "150000000"},
      "rule_ids": ["profit_positive", "profit_margin"]
    }
  ]
}
```

`source=file` 持久化请求示例：

```json
{
  "mode": "sync",
  "source": "file",
  "persist": true,
  "items": [
    {"biz_id": "doc-001", "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340", "rule_ids": ["profit_margin"]}
  ]
}
```

同步响应示例：

```json
{
  "code": 200,
  "message": "逻辑分析完成",
  "data": {
    "total_items": 1,
    "items": [
      {
        "item_index": 0,
        "biz_id": "doc-001",
        "type_id": "financial_report",
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "results": [
          {
            "rule_id": "profit_positive",
            "rule_name": "是否盈利",
            "rule_type": "judge",
            "result": "true",
            "reason": "净利润大于 0。",
            "input_values": {"net_profit": "5000000"},
            "source_refs": null,
            "success": true,
            "index": 1,
            "total": 2
          }
        ],
        "unknown_rule_ids": [],
        "error": null
      }
    ]
  }
}
```

异步请求示例：

```bash
curl -X POST http://localhost:5019/analysis/run \
  -H "Content-Type: application/json" \
  -d '{"mode":"async","callback_url":"http://127.0.0.1:9000/callback","items":[{"type_id":"financial_report","biz_id":"doc-001","field_values":{"net_profit":"5000000"}}]}'
```

异步响应示例：

```json
{"code": 200, "message": "分析任务已提交（异步）", "data": {"task_id": "b8d67f3b0d3f4c3d9f6b1c2a4e5f6789"}}
```

错误与边界：

| 状态 | 条件 | 说明 |
|---|---|---|
| `422` | `async` 缺 `callback_url`；`source=file` 缺 `file_id`；`source=values` 传了 `file_id`；`persist=true` 但 `source!=file` | 请求体校验失败 |
| HTTP `200` + item `error` | `source=file` 时文件不存在、文件类型不一致、无提取结果 | 单个 item 失败不影响批次其它 item |
| HTTP `500` | 批量执行外层异常 | sync 模式抛出 |

### 5.7 `POST /analysis/test/stream` 逻辑分析流式调试

入参与 `/analysis/test` 相同，SSE 分步返回依赖字段值、表达式解析、网络搜索、提示词、模型响应和结果。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/analysis/test/stream` |
| Content-Type | `application/json` |
| 响应 | `text/event-stream` |
| 副作用 | 无 |

请求示例：

```bash
curl -N -X POST http://localhost:5019/analysis/test/stream \
  -H "Content-Type: application/json" \
  -d '{"file_id":"3f2a7d4b0c2e45a98e0d6a5c1b8f9340","rule_id":"profit_positive"}'
```

典型事件：

```text
event: input_values
data: {"input_values":{"net_profit":"5000000"}}

event: resolved_expression
data: {"expression":"净利润为 5000000..."}

event: prompt
data: {"prompt":"..."}

event: llm_response
data: {"response":"..."}

event: result
data: {"result_value":"true","reason":"净利润大于 0"}

event: done
data: {"ok":true}
```

注意事项：`calc` 类型不产生 `prompt` / `llm_response`；启用网络搜索时可能出现 `web_search` 事件。

---

## 6. 文档类型接口 `/doctype`

文档类型用于隔离不同格式文件的字段配置和分析规则。每个文件绑定一个 `type_id`；抽取和分析阶段只读取该类型下启用的字段/规则。配置不共享，复制和导入都会产生独立副本。

当前源码仍包含项目归类相关接口（`/doctype/projects`、`/doctype/batch_assign_project`），本文档按源码当前状态记录。

### 6.1 `GET /doctype/list` 查询文档类型列表

列出类型并附带文件数、字段数、规则数。支持搜索、模板/副本范围、项目过滤和分页。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/doctype/list` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<object[] | {items,total}>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `q` | string | 否 | `null` | 模糊搜索 `type_id` / `type_name` |
| `scope` | string | 否 | `all` | `all` 全部；`template` 模板含默认类型；`copy` 非模板且非默认 |
| `project_id` | string | 否 | `null` | 指定项目；`__ungrouped__` 表示未分组 |
| `page` | integer | 否 | `null` | 与 `page_size` 同传才分页 |
| `page_size` | integer | 否 | `null` | 1-500；与 `page` 同传才分页 |
| `sort` | string | 否 | `created_at` | `created_at` 降序或 `type_name` 升序；默认类型恒置顶 |

请求示例：

```bash
curl "http://localhost:5019/doctype/list?q=财务&scope=all&page=1&page_size=20&sort=created_at"
```

分页响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [
      {
        "type_id": "financial_report",
        "type_name": "财务报告",
        "description": "上市公司年报",
        "max_parse_pages": null,
        "enable_embedding": 1,
        "is_default": 0,
        "enabled": 1,
        "is_template": 1,
        "parent_type_id": null,
        "project_id": "finance_project",
        "project_name": "财务项目",
        "created_at": "2026-08-07T09:00:00",
        "updated_at": "2026-08-07T09:00:00",
        "file_count": 12,
        "field_count": 8,
        "rule_count": 3
      }
    ],
    "total": 1
  }
}
```

非分页响应示例：

```json
{"code": 200, "message": "success", "data": [{"type_id": "default", "type_name": "默认类型", "file_count": 0, "field_count": 0, "rule_count": 0}]}
```

### 6.2 `POST /doctype` 新增/更新文档类型

按 `type_id` upsert。新建类型时可设置 `project_id`；更新已存在类型时忽略 `project_id`，项目归属由批量归类接口维护。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/doctype` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<{type_id}>` |
| 副作用 | 新增或更新 `doc_type` |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `type_id` | string | 是 | 无 | 类型 ID，`^[a-zA-Z0-9_-]+$`，最长 64 |
| `type_name` | string | 是 | 无 | 类型显示名，最长 200 |
| `description` | string | 否 | `null` | 描述 |
| `max_parse_pages` | integer | 否 | `null` | 解析页数上限；空表示不限制 |
| `enable_embedding` | integer | 否 | `1` | 是否执行 embedding 阶段，1/0 |
| `enabled` | integer | 否 | `1` | 类型是否启用 |
| `project_id` | string | 否 | `null` | 仅新建时生效；`null` 表示未分组 |

请求示例：

```bash
curl -X POST http://localhost:5019/doctype \
  -H "Content-Type: application/json" \
  -d '{"type_id":"financial_report","type_name":"财务报告","description":"上市公司年报","enable_embedding":1}'
```

响应示例：

```json
{"code": 200, "message": "类型已创建", "data": {"type_id": "financial_report"}}
```

错误：`422` 表示 `type_id`、`type_name`、`max_parse_pages` 等字段校验失败。

### 6.3 `PUT /doctype/{type_id}` 更新文档类型，可改 ID

更新类型基础配置。若请求体 `type_id` 与路径 `type_id` 不同，则执行类型改名，并级联更新 `files`、`extraction_field`、`analysis_rule` 的 `type_id`，以及子类型的 `parent_type_id`。

| 项 | 说明 |
|---|---|
| 方法 | `PUT` |
| 路径 | `/doctype/{type_id}` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<object>` |
| 副作用 | 更新类型；改名时级联修改文件、字段、规则和子类型血缘 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 原类型 ID |

请求体同 `POST /doctype`，但 `project_id` 更新时忽略。

请求示例：

```json
{
  "type_id": "annual_report",
  "type_name": "年度报告",
  "description": "由 financial_report 改名",
  "max_parse_pages": 80,
  "enable_embedding": 1,
  "enabled": 1
}
```

响应示例：

```json
{
  "code": 200,
  "message": "类型已更新",
  "data": {
    "old_type_id": "financial_report",
    "type_id": "annual_report",
    "renamed": true,
    "updated_files": 12,
    "updated_fields": 8,
    "updated_rules": 3,
    "updated_children": 2
  }
}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | 原 `type_id` 为空，或尝试改名默认类型 |
| `404` | 类型不存在 |
| `409` | 新 `type_id` 已存在 |

### 6.4 `DELETE /doctype/{type_id}` 删除文档类型

删除单个类型。默认类型不可删；模板类型当前源码也禁止删除，需先 demote。有关联文件、字段或规则时，默认返回 409；`force=true` 时级联删除。

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|:--:|---|---|
| `type_id` | path | string | 是 | 无 | 目标类型 ID |
| `force` | query | boolean | 否 | `false` | 是否级联删除类型下文件、配置、结果、Milvus 向量和 PDF |

请求示例：

```bash
curl -X DELETE "http://localhost:5019/doctype/annual_report?force=true"
```

响应示例：

```json
{
  "code": 200,
  "message": "类型已删除",
  "data": {"type_id": "annual_report", "deleted_files": 12, "deleted_fields": 8, "deleted_rules": 3}
}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | 默认类型不可删除 |
| `404` | 类型不存在 |
| `409` | 模板禁止删除；或有关联数据但未 `force=true` |

### 6.5 `POST /doctype/batch_delete` 批量删除文档类型

对每个 `type_id` 复用单删逻辑，逐条记录成功/失败，最后统一提交。单条失败不会中断其它类型处理。

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `type_ids` | string[] | 是 | 无 | 待删除类型 ID 列表 |
| `force` | boolean | 否 | `false` | 是否级联删除 |

请求示例：

```bash
curl -X POST http://localhost:5019/doctype/batch_delete \
  -H "Content-Type: application/json" \
  -d '{"type_ids":["annual_report","missing_type"],"force":true}'
```

响应示例：

```json
{
  "code": 200,
  "message": "批量删除完成：成功 1/2",
  "data": {
    "results": [
      {"type_id": "annual_report", "ok": true, "deleted_files": 12, "deleted_fields": 8, "deleted_rules": 3},
      {"type_id": "missing_type", "ok": false, "reason": "类型不存在"}
    ],
    "deleted": 1
  }
}
```

### 6.6 `POST /doctype/{type_id}/copy_from` 从源类型复制配置

在同一实例内，把源类型的字段和规则复制到目标类型。复制后是独立配置，后续编辑互不影响。字段/规则 ID 会基于源 ID 生成副本 ID，如 `A -> A_0002 -> A_0003`。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/doctype/{type_id}/copy_from` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<CopyConfigsResponse>` |
| 副作用 | 向目标类型新增字段和规则，更新目标 `parent_type_id` |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 目标类型 ID |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `source_type_id` | string | 是 | 无 | 源类型 ID，不能等于目标类型 |
| `field_ids` | string[]/null | 否 | `null` | 不传或 `null` 复制全部字段；空数组不复制字段；非空复制指定字段 |
| `rule_ids` | string[]/null | 否 | `null` | 不传或 `null` 复制全部规则；空数组不复制规则；非空复制指定规则 |
| `on_conflict` | string | 否 | `rename` | `rename` 生成新副本 ID；`skip` 跳过已有同源副本 |

请求示例：

```json
{
  "source_type_id": "financial_template",
  "field_ids": ["company_name", "net_profit"],
  "rule_ids": null,
  "on_conflict": "rename"
}
```

响应示例：

```json
{
  "code": 200,
  "message": "配置复制完成",
  "data": {
    "copied_fields": 2,
    "skipped_fields": 0,
    "copied_rules": 3,
    "skipped_rules": 0,
    "missing_dependencies": ["净利率::revenue"]
  }
}
```

注意事项：

- 规则表达式中的 `<field_result>旧field_id</field_result>` 会按复制映射改成新字段 ID。
- 进阶字段配置内的字段引用和 `page_source_field` 也会重映射。
- 未随字段一起复制的依赖不会静默丢弃，会进入 `missing_dependencies`。

### 6.7 `GET /doctype/{type_id}/export` 导出类型配置

把某类型的字段和规则导出为 JSON 载荷。规则依赖用字段名列表 `depend_field_names` 表达，便于跨环境导入时按名称映射。

请求示例：

```bash
curl "http://localhost:5019/doctype/financial_report/export"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "type_id": "financial_report",
    "type_name": "财务报告",
    "description": "上市公司年报",
    "max_parse_pages": null,
    "enable_embedding": 1,
    "version": 1,
    "fields": [
      {
        "field_id": "company_name",
        "field_name": "公司名称",
        "source_type": "text",
        "enabled": 1,
        "priority": 0,
        "use_llm": 1,
        "search_type": "context",
        "search_config": {"keywords": ["公司名称"]},
        "text_extract_prompt": "..."
      }
    ],
    "rules": [
      {
        "rule_id": "profit_positive",
        "rule_name": "是否盈利",
        "rule_type": "judge",
        "expression": "净利润 <field_result>net_profit</field_result>",
        "depend_field_names": ["净利润"],
        "enabled": 1,
        "priority": 0
      }
    ]
  }
}
```

错误：`404` 表示类型不存在。

### 6.8 `POST /doctype/import` 从 JSON 载荷导入配置

把 export 载荷导入目标类型。目标类型不存在时可自动创建。字段默认保留源 `field_id`，若全局冲突则追加 `_copy`、`_copy_2` 等后缀；规则同理。

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `payload` | object | 是 | 无 | `GET /doctype/{type_id}/export` 返回的 `data` |
| `target_type_id` | string/null | 否 | `null` | 目标类型 ID；为空使用 `payload.type_id` |
| `create_type_if_missing` | boolean | 否 | `true` | 目标类型不存在时是否创建 |
| `on_conflict` | string | 否 | `rename` | 同名字段/规则处理：`rename` 或 `skip` |

请求示例：

```json
{
  "target_type_id": "financial_report_copy",
  "create_type_if_missing": true,
  "on_conflict": "rename",
  "payload": {
    "type_id": "financial_report",
    "type_name": "财务报告副本",
    "version": 1,
    "fields": [],
    "rules": []
  }
}
```

响应示例：

```json
{
  "code": 200,
  "message": "配置导入完成",
  "data": {
    "target_type_id": "financial_report_copy",
    "created_type": true,
    "copied_fields": 0,
    "skipped_fields": 0,
    "copied_rules": 0,
    "skipped_rules": 0,
    "missing_dependencies": []
  }
}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | `target_type_id` 与 `payload.type_id` 都为空；导入字段提示词不合法 |
| `404` | 目标类型不存在且 `create_type_if_missing=false` |
| `422` | 导入载荷结构不符合 Pydantic schema |

### 6.9 `GET /doctype/projects` 查询项目列表

列出所有项目，并附带每个项目下的类型数量。

请求示例：

```bash
curl "http://localhost:5019/doctype/projects"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "project_id": "finance_project",
      "project_name": "财务项目",
      "description": "财务报告模板与副本",
      "type_count": 5,
      "created_at": "2026-08-07T09:00:00",
      "updated_at": "2026-08-07T09:00:00"
    }
  ]
}
```

### 6.10 `POST /doctype/projects` 新增/更新项目

按 `project_id` upsert 项目。更新项目只改名称和描述，不影响成员类型。

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `project_id` | string | 是 | 无 | 项目 ID，`^[a-zA-Z0-9_-]+$`，最长 64 |
| `project_name` | string | 是 | 无 | 项目显示名，最长 200 |
| `description` | string | 否 | `null` | 描述 |

请求示例：

```bash
curl -X POST http://localhost:5019/doctype/projects \
  -H "Content-Type: application/json" \
  -d '{"project_id":"finance_project","project_name":"财务项目","description":"财报模板集合"}'
```

响应示例：

```json
{"code": 200, "message": "项目已创建", "data": {"project_id": "finance_project"}}
```

### 6.11 `DELETE /doctype/projects/{project_id}` 删除项目

删除项目本身，并把成员类型的 `project_id` 置空。不会删除任何文档类型、文件或配置。

请求示例：

```bash
curl -X DELETE "http://localhost:5019/doctype/projects/finance_project"
```

响应示例：

```json
{"code": 200, "message": "项目已删除", "data": {"project_id": "finance_project"}}
```

错误：`404` 表示项目不存在。

### 6.12 `POST /doctype/batch_assign_project` 批量归类类型到项目

把一批类型归入某项目，或传 `project_id=null` 移出项目。归类会沿 `parent_type_id` 级联血缘后代；默认类型会被跳过。

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `type_ids` | string[] | 是 | 无 | 待归类类型；每个类型的血缘后代会一起归类 |
| `project_id` | string/null | 否 | `null` | 目标项目 ID；`null` 表示移出 |

请求示例：

```json
{"type_ids": ["financial_report"], "project_id": "finance_project"}
```

响应示例：

```json
{
  "code": 200,
  "message": "批量归类完成",
  "data": {"requested": 1, "affected": 3, "project_id": "finance_project"}
}
```

错误：`404` 表示 `project_id` 非空但项目不存在。

### 6.13 `POST /doctype/{type_id}/promote` 标记为模板

把普通类型或副本类型标记为模板，设置 `is_template=1`。保留 `parent_type_id`。

请求示例：

```bash
curl -X POST "http://localhost:5019/doctype/financial_report/promote"
```

响应示例：

```json
{"code": 200, "message": "已标记为模板", "data": {"type_id": "financial_report"}}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | 默认类型无需标记 |
| `404` | 类型不存在 |

### 6.14 `POST /doctype/{type_id}/demote` 取消模板标记

把类型的 `is_template` 设置为 0，不影响血缘和配置。

请求示例：

```bash
curl -X POST "http://localhost:5019/doctype/financial_report/demote"
```

响应示例：

```json
{"code": 200, "message": "已取消模板标记", "data": {"type_id": "financial_report"}}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | 默认类型不可操作 |
| `404` | 类型不存在 |

---

## 7. 向量检索接口 `/search`

### 7.1 `POST /search` 向量相似度检索

把 `query` 发送到 embedding 服务生成向量，再到 Milvus 检索相似分块。可限定单个文件，也可跨全部已向量化文件检索。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/search` |
| Content-Type | `application/json` |
| 响应 | `ResponseWrapper<SearchResultItem[]>` |
| 副作用 | 调用 embedding 和 Milvus 检索，不写库 |

请求体参数：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `query` | string | 是 | 无 | 检索文本；空串通常返回空数组 |
| `file_id` | string/null | 否 | `null` | 限定某个文件；空则跨全部文件 |
| `top_k` | integer | 否 | `10` | 返回条数 |
| `score_threshold` | number/null | 否 | `null` | 相似度阈值，低于阈值过滤；空则不过滤 |

请求示例：

```bash
curl -X POST http://localhost:5019/search \
  -H "Content-Type: application/json" \
  -d '{"query":"公司注册资本是多少","file_id":"3f2a7d4b0c2e45a98e0d6a5c1b8f9340","top_k":5,"score_threshold":0.5}'
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "chunk_id": "9f86d081884c7d659a2feaa0c55ad015",
      "file_id": "3f2a7d4b0c2e45a98e0d6a5c1b8f9340",
      "chunk_index": 3,
      "chunk_content": "注册资本：1000万元。",
      "score": 0.87,
      "page_num": "2"
    }
  ]
}
```

注意事项：

- 只有完成 embedding 阶段且类型 `enable_embedding=1` 的文件才有向量数据。
- `score` 的取值和含义取决于 Milvus metric 配置；现有文档按 COSINE 相似度理解为越大越相似。
- 如果 embedding 服务或 Milvus 不可用，接口可能返回 500 或底层异常信息。

---

## 8. 日志接口 `/log`

日志接口读取 `logs/app_*.log` 文件，供前端日志页和运维排障。全部为只读接口。

日志等级合法值：`TRACE`、`DEBUG`、`INFO`、`SUCCESS`、`WARNING`、`ERROR`、`CRITICAL`。

### 8.1 `GET /log/files` 查询日志文件列表

列出 `logs/` 目录下的 `app_*.log` 文件，按修改时间倒序。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/log/files` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<object>` |
| 副作用 | 无 |

请求示例：

```bash
curl "http://localhost:5019/log/files"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "current": "app_2026-08-07.log",
    "items": [
      {"name": "app_2026-08-07.log", "size": 20480, "modified_at": 1786087200.0}
    ]
  }
}
```

注意事项：无日志文件时 `current=null`、`items=[]`。

### 8.2 `GET /log/recent` 查询最近日志

读取指定日志文件末尾若干行，并可按日志等级过滤。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/log/recent` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<{file,lines}>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `file` | string | 否 | 最新日志 | 指定日志文件名，只允许 `logs` 目录下 `app_*.log` |
| `lines` | integer | 否 | `200` | 读取末尾行数，0-2000 |
| `level` | string | 否 | 空 | 指定日志等级；空不过滤 |

请求示例：

```bash
curl "http://localhost:5019/log/recent?lines=100&level=ERROR"
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "file": "app_2026-08-07.log",
    "lines": [
      {
        "file": "app_2026-08-07.log",
        "level": "ERROR",
        "line": "2026-08-07 09:33:00 | ERROR | default | 3f2a... | Pipeline 失败",
        "timestamp": "2026-08-07 09:33:00",
        "type_id": "default",
        "file_id": "3f2a...",
        "message": "Pipeline 失败",
        "offset": null
      }
    ]
  }
}
```

错误：

| 状态 | 条件 |
|---|---|
| `400` | 文件名不合法、日志等级不合法 |
| `404` | 指定日志文件不存在 |

### 8.3 `GET /log/stream` 实时日志流

以 SSE 实时推送日志。连接建立后先回放末尾 `tail` 行，然后持续读取追加内容。未指定 `file` 时会跟随最新日志文件，日志轮转时自动切换。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/log/stream` |
| 请求体 | 无 |
| 响应 | `text/event-stream` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `file` | string | 否 | 最新日志 | 指定日志文件；不传则跟随最新 |
| `tail` | integer | 否 | `200` | 建连时回放末尾行数，0-2000 |
| `level` | string | 否 | 空 | 日志等级过滤 |

请求示例：

```bash
curl -N "http://localhost:5019/log/stream?tail=50&level=INFO"
```

事件示例：

```text
event: ready
data: {"file":"app_2026-08-07.log","message":"日志连接已建立"}

event: line
data: {"file":"app_2026-08-07.log","level":"INFO","line":"...","timestamp":"...","type_id":"default","file_id":"-","message":"...","offset":1234}

event: heartbeat
data: {"file":"app_2026-08-07.log","ts":1786087200.123}
```

事件类型：

| 事件 | 说明 |
|---|---|
| `ready` | 连接建立；无日志文件时也会返回 ready |
| `line` | 一行日志 |
| `rotated` | 未指定 `file` 时检测到最新日志文件变化并切换 |
| `heartbeat` | 心跳，默认约 15 秒一次 |

---

## 9. 异步回调契约

以下接口在 `async` 或 `sync` 模式传入 `callback_url` 时会回调：

| 接口 | 说明 |
|---|---|
| `POST /file/parse` | 管线处理回调 |
| `POST /file/{file_id}/retry/{stage}` | 重试管线回调 |
| `POST /file/{file_id}/retry/extracting` | 字段提取重试回调 |
| `POST /file/{file_id}/retry/analyzing` | 逻辑分析重试回调 |
| `POST /analysis/run` 且 `mode=async` | 独立逻辑分析任务回调 |

回调通用规则：

| 项 | 说明 |
|---|---|
| 方法 | 后端向 `callback_url` 发起 `POST` |
| 超时 | 2.5 秒 |
| 失败处理 | 只记录 warning，不影响主流程 |
| `stream` 模式 | 忽略 `callback_url`，改用 SSE |

管线阶段入口回调：

```json
{"file_id": "3f2a...", "status": "extracting"}
```

字段完成回调：

```json
{
  "file_id": "3f2a...",
  "status": "extracting",
  "event": "field_done",
  "data": {
    "field_id": "company_name",
    "field_name": "公司名称",
    "value": "示例科技有限公司",
    "reason": "正文第 1 页写明。",
    "pages": [1],
    "source_pages": [1],
    "source_refs": {},
    "success": true,
    "index": 1,
    "total": 8
  }
}
```

规则完成回调：

```json
{
  "file_id": "3f2a...",
  "status": "analyzing",
  "event": "rule_done",
  "data": {
    "rule_id": "profit_positive",
    "rule_name": "是否盈利",
    "rule_type": "judge",
    "result": "true",
    "reason": "净利润大于 0。",
    "input_values": {"net_profit": "5000000"},
    "source_refs": null,
    "success": true,
    "index": 1,
    "total": 3
  }
}
```

阶段完成回调：

| 阶段 | `event` | `data` 说明 |
|---|---|---|
| `parsing` | `stage_done` | `{content,middle_json,page_mapping}` |
| `tableing` | `stage_done` | `{total,tables:[...]}` |
| `chunking` | `stage_done` | `{total,chunks:[...]}` |
| `embedding` | `stage_done` | 通常不携带 `data`，只表示完成 |
| `extracting` | `stage_done` | `{total,succeeded,failed,results:[field_done.data...]}` |
| `analyzing` | `stage_done` | `{total,succeeded,failed,results:[rule_done.data...]}` |

阶段失败回调：

```json
{
  "file_id": "3f2a...",
  "status": "extracting_failed",
  "event": "stage_failed",
  "data": {"stage": "extracting", "error": "TimeoutError: ..."}
}
```

独立逻辑分析 `POST /analysis/run` 的 async 回调：

| 事件 | 说明 |
|---|---|
| 无 `event`，`status=analyzing` | 任务开始 |
| `rule_done` | 单条规则完成，数据包含 `item_index` / `biz_id` |
| `task_done` | 全部 item 完成，`data` 等同 sync 响应体 |
| `task_failed` | 任务级失败 |

---

## 10. SSE 流式事件契约

SSE 响应统一为：

```text
event: <事件名>
data: <JSON 字符串>

```

流式接口：

| 接口 | 触发方式 | 说明 |
|---|---|---|
| `POST /file/parse?mode=stream` | multipart 上传 | 新文件处理管线流 |
| `POST /file/{file_id}/retry/{stage}?mode=stream` | 无请求体 | 重试管线流 |
| `POST /file/{file_id}/retry/extracting?mode=stream` | 无请求体 | 字段提取重试流 |
| `POST /file/{file_id}/retry/analyzing?mode=stream` | 无请求体 | 逻辑分析重试流 |
| `POST /extraction/test/stream` | JSON | 字段提取调试流 |
| `POST /analysis/test/stream` | JSON | 逻辑分析调试流 |
| `POST /analysis/run` 且 `mode=stream` | JSON | 独立分析执行流 |
| `GET /log/stream` | query | 日志流 |

管线流典型序列：

```text
parsing -> tableing -> chunking -> embedding -> extracting -> field_extracted*N -> analyzing -> rule_evaluated*N -> complete
```

管线流字段完成事件与回调词汇不同：

| 语义 | 回调 | SSE |
|---|---|---|
| 字段完成事件名 | `field_done` | `field_extracted` |
| 规则完成事件名 | `rule_done` | `rule_evaluated` |
| 字段值 | `value` | `extracted_value` |
| 规则结果 | `result` | `result_value` |
| 序号 | `index` | `current` |

字段提取调试流常见事件：`resolved_refs`、`search_result`、`prompt`、`llm_response`、`result`、`done`、`error`。VL 字段可能额外出现 `pdf_loaded`、`progressive_batch`、`locate_locate`、`locate_extract`。

逻辑分析调试流常见事件：`input_values`、`resolved_expression`、`web_search`、`prompt`、`llm_response`、`result`、`done`、`error`。`calc` 类型通常没有 `prompt` / `llm_response`。

独立分析流常见事件：`analyzing`、`rule_done`、`task_done`、`task_failed`。

---

## 11. 枚举与关键结构速查

### 11.1 枚举

| 枚举 | 取值 | 使用位置 |
|---|---|---|
| `SourceType` | `table` / `text` / `vl` | `extraction_field.source_type` |
| `TableMatchType` | `exact` / `fuzzy` / `contains` / `llm` | 表格字段匹配方式 |
| `SearchType` | `context` / `section` / `rule` / `chunk_db` / `vector_db` / `page` | 文本字段检索方式 |
| `VLMethod` | `vl_model` / `vl_progressive` / `vl_locate` | VL 字段视觉抽取方式 |
| `RuleType` | `judge` / `calc` / `custom` | 分析规则类型 |
| `AnalysisRunMode` | `sync` / `async` / `stream` | `POST /analysis/run` |
| `AnalysisRunSource` | `values` / `file` | `POST /analysis/run` |

### 11.2 `source_refs` 结构要点

`source_refs` 是提取结果和分析结果的溯源结构。text/table 字段通常按检索 label 分组，并带 `_texts` 汇总实际注入提示词的文本；VL 字段通常只有 `_vl` 元数据。

text 示例：

```json
{
  "公司名称": [
    {
      "type": "context",
      "start_pos": 120,
      "end_pos": 260,
      "page_num": "1",
      "text": "公司名称：示例科技有限公司",
      "bboxes": [{"page_num": 1, "bbox": [88, 72, 507, 96], "page_size": [595, 842]}]
    }
  ],
  "_texts": {"公司名称": "公司名称：示例科技有限公司"}
}
```

table 示例：

```json
{
  "_tables": [
    {
      "type": "table",
      "table_index": 0,
      "table_name": "资产负债表",
      "start_pos": 5120,
      "end_pos": 6890,
      "page_num": "12",
      "text": "表格名称: 资产负债表\n<table>...</table>"
    }
  ],
  "_texts": {"资产负债表": "表格名称: 资产负债表\n<table>...</table>"}
}
```

VL 示例：

```json
{"_vl": {"method": "vl_locate", "total_pages": 48, "key_pages": [12, 13], "vl_total_tokens": 8421}}
```

### 11.3 `pages` 与 `source_pages`

| 字段 | 来源 | 是否落库 | 说明 |
|---|---|:--:|---|
| `pages` | 模型自报页码，落库为 `extraction_result.model_pages` | 是 | text/table 的 LLM 可返回；VL、`use_llm=0`、模型未返回时为 `[]` |
| `source_pages` | 输出时根据 `pages` 和 `source_refs` 派生 | 否 | 优先 `pages`，无则从程序命中页兜底；恒为 int 数组，范围会展开 |

### 11.4 占位符规则

| 占位符 | 使用位置 | 说明 |
|---|---|---|
| `<search_result>标签</search_result>` | 字段抽取 prompt | text/table 字段必须包含，系统用检索文本替换 |
| `<field_result>field_id</field_result>` | 分析规则、进阶字段配置 | 用字段提取值替换 |
| `<web_search_result/>` | 启用 `web_search` 的 judge/custom 规则 | 用联网搜索结果文本替换 |
| `{table_list}` | table LLM 匹配模板 | 自定义表格匹配模板必须包含 |
| `{section_list}` | section LLM 匹配模板 | 自定义章节匹配模板必须包含 |
---

## 12. 附录

本节收录的是接口正文之外、但仍需要一站式保留的补充材料：配置手册、字段提取规则、`source_refs`、库表结构、枚举状态机、MinerU 集成和优化 backlog。这里不再重复第 3-8 节已经完整展开的接口参考。

### 12.1 附录说明

| 附录项 | 本文位置 | 说明 |
|---|---|---|
| 逻辑分析配置手册 | 第 12.2 节 | 规则配法与排查 |
| `config.yaml` 配置手册 | 第 12.3 节 | 全局配置 |
| 字段提取配置手册 | 第 12.4 节 | table / text / vl 配法 |
| `source_refs` 溯源结构与页码定位 | 第 12.5 节 | 页码、bbox、source_pages |
| 库表结构 | 第 12.6 节 | MySQL / Milvus 表结构 |
| 枚举值与状态机 | 第 12.7 节 | `progress` 与枚举 |
| MinerU 解析集成 | 第 12.8 节 | 外部解析服务对接 |
| 功能优化 Backlog | 第 12.9 节 | 审查记录与待办 |

### 12.2 逻辑分析配置手册

> 对应服务版本 0.3.0

逻辑分析是六阶段管线的最后一环（`analyzing`），在字段提取（`extracting`）之后运行：它读取该文件已提取的字段值，按规则做**二次判断或计算**，把结论写入 `analysis_result`。本手册讲「怎么配规则」——按任务组织的配方、可直接套用的 JSON、以及排查清单。

- 接口签名、请求/响应字段、状态码：见第 5 节。
- `source_refs` 溯源结构（含 `_web_search`）：见第 12.5 节。
- 全局参数（`analysis` / `web_search` 节）：见第 12.3 节。
- 字段提取怎么配（judge/calc 依赖的字段从哪来）：见第 12.4 节。

---

#### 目录

1. [概览：三类规则与心智模型](#1-概览三类规则与心智模型)
2. [占位符与依赖（核心机制）](#2-占位符与依赖核心机制)
3. [judge 判断规则](#3-judge-判断规则)
4. [calc 计算规则](#4-calc-计算规则)
5. [custom 自定义规则](#5-custom-自定义规则)
6. [web_search 网络搜索（judge 与 custom）](#6-web_search-网络搜索judge-与-custom)
7. [两条执行路径：随管线 vs 独立分析](#7-两条执行路径随管线-vs-独立分析)
8. [调试规则](#8-调试规则)
9. [端到端配方：财务报表场景](#9-端到端配方财务报表场景)
10. [常见错误与排查](#10-常见错误与排查)
11. [跨类型复制的占位符重映射](#11-跨类型复制的占位符重映射)

---

#### 1. 概览：三类规则与心智模型

一条分析规则（`analysis_rule`）= 一个 `expression` 表达式 + 若干依赖字段（`depend_fields`）。表达式里用 `<field_result>字段ID</field_result>` 占位符引用提取结果，执行前会被替换成真实值，再交给对应引擎处理。

| 规则类型 | 引擎 | 产出 | 典型用途 |
|---|---|---|---|
| `judge` | LLM 判断 | `"true"` / `"false"` 字符串 + 理由 | 是否达标、是否盈利、是否存在风险等条件判断 |
| `calc` | `numexpr` 数学计算 | 数值字符串（默认保留 2 位小数）+ 计算式 | 利润率、负债率、净资产等比率/差值 |
| `custom` | LLM 自由生成 | `{value, reason}`；格式化时 `value` 为结构化 JSON 字符串 | 摘要、要素归纳、结构化抽取等开放式产出 |

数据流：

```
extraction_field  →  extraction_result   （提取值，本手册的输入）
                          ↓ 被 <field_result> 引用
analysis_rule     →  analysis_result     （判断/计算结论，本手册的产出）
```

规则按 `type_id` 隔离——每个文件绑定一个文档类型，分析时只加载**同类型且 `enabled=1`** 的规则，按 `priority` 升序执行。不同类型的规则互不共享（跨类型复制参见本文内 [第 11 节](#11-跨类型复制的占位符重映射)）。

---

#### 2. 占位符与依赖（核心机制）

##### 2.1 `<field_result>` 字段占位符

**格式**：`<field_result>字段ID</field_result>`，`字段ID` 即某条 `extraction_field` 的 `field_id`。

**渲染规则**：执行前逐个替换——

- 命中且提取值非空 → 替换为该字段的提取值；
- 未命中或提取值为空 → 替换为提示文本 `（未找到字段 '字段ID' 的提取结果）`。

> 替换用的是**该文件全部提取结果**的映射，不限于 `depend_fields`。也就是说占位符「能不能取到值」只看提取结果里有没有这个 `field_id`，与是否写进 `depend_fields` 无关。但仍强烈建议把表达式里引用到的每个字段都列进 `depend_fields`（原因参见本文内 2.2）。

**校验**：`expression` **必须包含至少一个** `<field_result>…</field_result>`（judge / calc / custom 都要求），否则保存时返回 **422**。

##### 2.2 `depend_fields` 的作用

`depend_fields` 是一个字段 ID 列表，声明本规则依赖哪些提取字段。它不参与占位符替换，但决定四件事：

1. **取值与留痕**：结果里的 `input_values` 只记录 `depend_fields` 列出的字段值，便于回溯规则「吃进了什么」。
2. **依赖校验**（参见本文内 2.3）：只对 `depend_fields` 里的字段做「是否为空 / 是否为数字」检查。
3. **溯源收集**：把这些字段的 `source_refs`（命中页码、bbox 等）挂进分析结果，供前端定位。
4. **独立分析的门控**：`/analysis/run` 独立执行时，未点名 `rule_ids` 的情况下，只有 `depend_fields` 被外部输入的键**完整覆盖**的规则才会跑（参见本文内 [7.2](#72-独立分析analysisrun)）。

**结论**：把 `expression`（以及 `web_search.query`）中引用到的每一个 `<field_result>` 字段都写进 `depend_fields`，否则校验、留痕、溯源、独立执行会漏掉它。

##### 2.3 依赖值校验：规则何时被「跳过」

保存规则不校验依赖值，但**执行时**会对 `depend_fields` 逐个检查，决定是否跳过：

| 场景 | judge | calc |
|---|---|---|
| `depend_fields` 为空 | 直接通过 | 直接通过 |
| 所有依赖字段都为空 | **跳过**，理由「所有依赖字段均为空: …」 | **跳过**，同左 |
| 至少一个非空 | 通过 | 还需**至少一个是有效数字**，否则跳过（理由列出空字段/非数字字段） |

被跳过的规则仍会写一条空结果（`result_value=""`，`reason` 为失败原因），并照常触发 `rule_done` 回调，只是 `success=false`。校验较宽松：**只要有一个依赖非空就会执行**，缺失的字段在表达式里以「未找到…」提示文本参与判断/计算——所以 calc 里混入空字段可能让公式算错（参见本文内 [第 10 节](#10-常见错误与排查)）。

---

#### 3. judge 判断规则

用 LLM 对表达式描述的条件做真/假判断，产出 `"true"` / `"false"` 及理由。

##### 基础结构

```json
{
  "rule_id": "revenue_qualified",
  "type_id": "financial_report",
  "rule_name": "营收达标判断",
  "rule_type": "judge",
  "expression": "公司营业总收入为 <field_result>total_revenue</field_result> 元。\n\n请判断：该公司营业总收入是否超过 1000 万元（10000000 元）？",
  "depend_fields": ["total_revenue"],
  "priority": 0
}
```

| 字段 | 必填 | 说明 |
|---|:--:|---|
| `rule_id` | 是 | 唯一标识，`^[a-zA-Z0-9_]+$`，最长 100，**全局唯一** |
| `type_id` | 否 | 归属文档类型，默认 `default` |
| `rule_name` | 是 | 显示名，最长 200 |
| `rule_type` | 是 | 固定 `"judge"` |
| `expression` | 是 | 判断提示词，**须含至少一个 `<field_result>`** |
| `system_prompt` | 否 | 作为 system message 调控 LLM 口径（judge / custom 用）；calc 忽略 |
| `depend_fields` | 否 | 依赖字段 ID 列表（参见本文内 2.2） |
| `web_search` | 否 | 联网检索（judge / custom 可用，参见本文内 [第 6 节](#6-web_search-网络搜索judge-与-custom)） |
| `enabled` | 否 | 1 启用 / 0 停用，默认 1 |
| `priority` | 否 | 升序执行，默认 0 |

##### 工作原理（重要）

你只需在 `expression` 里用**自然语言**把「已知条件 + 要判断什么」写清楚。系统会在发给 LLM 前**自动追加**一段固定的 JSON 输出指令，要求模型返回：

```json
{"result": "true 或 false", "reason": "判断理由/依据"}
```

因此：

- **不要**自己在 `expression` 里再写「请返回 JSON」之类的格式要求，交给系统即可。
- 返回值会被**归一化**：模型答 `true` / `是` → 存 `"true"`；答 `false` / `否` → 存 `"false"`。下游按小写字符串 `"true"`/`"false"` 消费。
- `reason` 取模型给的理由；模型偶发吐裸英文双引号破坏 JSON 时，系统会做兜底抢救，不至于整条失败。
- 用 `system_prompt` 固化裁判口径（如「你是严谨的财务审计助手，只依据给定数据判断，信息不足时判 false」）。

##### 配方

**单字段阈值判断**（上方基础结构即是）。

**多字段综合判断**：

```json
{
  "rule_id": "profit_positive",
  "type_id": "financial_report",
  "rule_name": "盈利状态判断",
  "rule_type": "judge",
  "expression": "公司财务数据如下：\n- 营业总收入：<field_result>total_revenue</field_result> 元\n- 净利润：<field_result>net_profit</field_result> 元\n\n请判断：该公司是否处于盈利状态（净利润大于 0）？",
  "depend_fields": ["total_revenue", "net_profit"],
  "priority": 1
}
```

**文本内容判断**（依赖文本类提取字段）：

```json
{
  "rule_id": "has_risk_warning",
  "type_id": "financial_report",
  "rule_name": "是否有风险警示",
  "rule_type": "judge",
  "expression": "公司名称：<field_result>company_name</field_result>\n风险因素描述：<field_result>risk_factors</field_result>\n\n请判断：该公司是否存在重大风险警示？",
  "depend_fields": ["company_name", "risk_factors"],
  "priority": 2
}
```

---

#### 4. calc 计算规则

对提取到的**数值**字段做数学运算，用 `numexpr` 安全求值。

##### 基础结构

```json
{
  "rule_id": "profit_margin",
  "type_id": "financial_report",
  "rule_name": "净利润率(%)",
  "rule_type": "calc",
  "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
  "depend_fields": ["net_profit", "total_revenue"],
  "priority": 0
}
```

##### 支持的运算与精度

| 运算符 | 说明 | 示例 |
|---|---|---|
| `+` `-` `*` `/` | 四则运算 | `A - B`、`A / B * 100` |
| `( )` | 括号分组 | `(A + B) * C` |

- 结果为整数则去掉小数位，否则按 `analysis.calc_precision`（默认 **2** 位）四舍五入。
- `reason` 自动生成，形如 `计算公式: 12.5/100*100 = 12.5`。

##### 重要：calc 只做算术，不做比较

执行前系统会**只保留** `0-9 + - * / ( ) . e E` 和空格这些字符，其余一律剥离。这意味着：

- 表达式里混入的文字（如单位、说明）会被自动清掉，占位符**必须解析成纯数字**（含科学计数法如 `1.2e8`）。若某字段解析成「未找到…」提示或含逗号/货币符号，会算错或报错——请在提取阶段就要求「仅返回数值」。
- **比较与布尔（`>` `<` `>=` `==` 等）不被支持**：这些符号会被剥离掉。要「是否大于阈值」这类真/假结论，请用 **judge** 规则，不要用 calc。

##### 配方

**资产负债率**：

```json
{
  "rule_id": "debt_ratio",
  "type_id": "financial_report",
  "rule_name": "资产负债率(%)",
  "rule_type": "calc",
  "expression": "<field_result>total_liabilities</field_result> / <field_result>total_assets</field_result> * 100",
  "depend_fields": ["total_liabilities", "total_assets"],
  "priority": 1
}
```

**净资产（差值）**：

```json
{
  "rule_id": "net_assets",
  "type_id": "financial_report",
  "rule_name": "净资产",
  "rule_type": "calc",
  "expression": "<field_result>total_assets</field_result> - <field_result>total_liabilities</field_result>",
  "depend_fields": ["total_assets", "total_liabilities"],
  "priority": 2
}
```

---

#### 5. custom 自定义规则

用 LLM 按 `expression` 提示词**自由生成**结果，返回 `{value, reason}`。适合判断 / 计算之外的开放式产出：摘要、要素归纳、把多个字段整合成一段结构化 JSON 等。

##### 基础结构（非格式化）

`is_formatted=0`（默认）时，模型直接返回纯文本 `value`：

```json
{
  "rule_id": "risk_summary",
  "type_id": "financial_report",
  "rule_name": "风险点摘要",
  "rule_type": "custom",
  "expression": "根据以下风险描述，用一句话概括核心风险：<field_result>risk_factors</field_result>",
  "depend_fields": ["risk_factors"],
  "priority": 0
}
```

| 字段 | 必填 | 说明 |
|---|:--:|---|
| `rule_type` | 是 | 固定 `"custom"` |
| `expression` | 是 | 生成提示词，**须含至少一个 `<field_result>`** |
| `system_prompt` | 否 | 作为 system message 调控生成口径；用法同 judge |
| `is_formatted` | 否 | `0`（默认）返回纯文本 `value`；`1` 按 `output_schema` 返回结构化 JSON |
| `output_schema` | `is_formatted=1` 时必填 | 输出字段树（见下） |
| `web_search` | 否 | 联网检索，**custom 同样支持**（参见本文内 [第 6 节](#6-web_search-网络搜索judge-与-custom)） |
| `depend_fields` | 否 | 依赖字段 ID 列表（参见本文内 2.2） |

##### 工作原理

与 judge 一样，系统会在 `expression` 后**自动追加**一段 JSON 输出指令，要求模型返回 `{"value": …, "reason": …}`——你**不用**自己在提示词里写格式要求。`value` 是主结果，`reason` 是模型给出的依据。

##### 格式化输出（`is_formatted=1` + `output_schema`）

想让模型产出**结构化 JSON**（而非一段纯文本）时，打开格式化开关并给出字段树。系统会把 `output_schema` 渲染成「结构说明 + 示例 JSON」拼进提示词，模型据此产出，`value` 即一段结构化 JSON 字符串。

```json
{
  "rule_id": "shareholder_summary",
  "type_id": "financial_report",
  "rule_name": "股东结构摘要",
  "rule_type": "custom",
  "expression": "根据以下信息汇总股东结构：<field_result>shareholders</field_result>",
  "depend_fields": ["shareholders"],
  "is_formatted": 1,
  "output_schema": [
    { "key": "总股东数", "type": "number", "example": "3" },
    { "key": "主要股东", "type": "array", "children": [
      { "key": "名称", "type": "string", "example": "张三" },
      { "key": "持股比例", "type": "string", "example": "51%" }
    ]}
  ]
}
```

**output_schema 节点结构**：

| 键 | 必填 | 说明 |
|---|:--:|---|
| `key` | 是 | 字段名，**同级不可重名** |
| `type` | 是 | `string` / `number` / `boolean` / `object` / `array` |
| `example` | 否 | 标量节点示例值，仅用于拼接示例 JSON（不做类型强转） |
| `desc` | 否 | 字段说明，注入结构说明帮助模型理解 |
| `children` | object/array 必填 | 子字段列表；`object`/`array` 须**非空**，标量节点**不得**有 children |

**校验（`is_formatted=1` 时，否则 422）**：`output_schema` 不能为空；每个节点 `key` 非空、同级不重名；`object`/`array` 必须有非空 `children`。枚举与结构权威见第 12.6 节的 `output_schema`。

---

#### 6. web_search 网络搜索（judge 与 custom）

judge / custom 规则可在执行前先联网检索（博查 Bocha AI），把检索到的公开信息一并喂给 LLM，适合「文档内查不到、需要外部事实佐证」的场景（如「该公司当前是否为 A 股上市公司」）。**judge / custom 支持，calc 无效。**

##### 规则级配置

在规则上加一个 `web_search` 对象：

```json
"web_search": {
  "enabled": true,
  "query": "<field_result>company_name</field_result> 是否A股上市公司 股票代码",
  "count": 5,
  "freshness": "oneYear"
}
```

| 键 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `enabled` | bool | 是 | `true` 才启用；`false`/缺省时整个 `web_search` 不生效 |
| `query` | string | 启用时必填 | 搜索词，**支持 `<field_result>字段ID</field_result>` 占位符**，会先用提取值拼接再检索 |
| `count` | int | 否 | 返回条数，缺省走全局 `web_search.count`（默认 5） |
| `freshness` | string | 否 | 时间范围 `noLimit`/`oneDay`/`oneWeek`/`oneMonth`/`oneYear`，缺省走全局配置 |

##### 两个占位符协作

启用网络搜索后，一条 judge 规则里会同时出现两种占位符：

- `<field_result>…</field_result>`：字段值占位符，`expression` 和 `web_search.query` 里都能用。
- `<web_search_result/>`：**搜索结果占位符**（自闭合、无标签），只出现在 `expression` 里，执行时被替换为格式化后的检索文本（按条编号，含标题/来源/日期/摘要）。

##### 校验规则（启用时，否则 422）

保存规则时，若 `web_search.enabled=true`：

1. `rule_type` 必须是 `judge` / `custom` —— 否则「仅 judge / custom 类型规则支持网络搜索」。
2. `query` 去空格后非空 —— 否则「启用网络搜索时 query 不能为空」。
3. `expression` **必须包含 `<web_search_result/>`** —— 否则「启用网络搜索时 expression 必须包含 `<web_search_result/>` 占位符」。

##### 失败不致命 + 溯源

- 检索失败（网络/鉴权/超时）**不会**让整条规则失败：`<web_search_result/>` 会被替换为 `（网络搜索失败: …）`，判断照常进行。
- 检索留痕写入 `source_refs._web_search`：`{query, results: [{name,url,siteName,datePublished,summary}], error?}`，通过 `GET /file/{id}/analysis` 与 `rule_done` 回调透出；调试流会多推一个 `web_search` 事件。结构见第 12.5 节。

##### 完整示例

```json
{
  "rule_id": "listing_status",
  "type_id": "financial_report",
  "rule_name": "是否A股上市公司",
  "rule_type": "judge",
  "expression": "公司名称：<field_result>company_name</field_result>\n\n以下是联网检索到的公开信息：\n<web_search_result/>\n\n请依据以上信息判断：该公司当前是否为 A 股上市公司？信息不足时判 false。",
  "system_prompt": "你是严谨的信息核查助手，只依据给定资料判断，不臆测。",
  "depend_fields": ["company_name"],
  "web_search": {
    "enabled": true,
    "query": "<field_result>company_name</field_result> A股 上市 股票代码",
    "count": 5,
    "freshness": "oneYear"
  },
  "priority": 5
}
```

##### 全局参数（`configs/config.yaml` 的 `web_search` 节）

规则里没写的部分走全局默认：

| 参数 | 默认 | 说明 |
|---|---|---|
| `base_url` / `api_key` | — | 博查 Web Search API 地址与密钥 |
| `count` | 5 | 默认返回条数 |
| `summary` | true | 返回长摘要 |
| `freshness` | `noLimit` | 默认时间范围 |
| `timeout` | 10 | 请求超时（秒） |
| `retry_count` | 2 | 重试次数（4xx 除 429 不重试） |
| `max_result_length` | 4000 | 注入 prompt 的搜索文本上限（末尾截断） |

---

#### 7. 两条执行路径：随管线 vs 独立分析

同一批规则有两种触发方式，取值来源与副作用**完全不同**：

| 维度 | 随管线执行（`analyzing` 阶段） | 独立分析 `POST /analysis/run` |
|---|---|---|
| 字段值来源 | 该文件的 `extraction_result`（库里已提取的） | `source=values`（默认）用请求里的 `field_values`；`source=file` 也读该文件的 `extraction_result` |
| 规则筛选 | `type_id` 匹配 + `enabled=1` | 同左，再按 item 级 `rule_ids` 收窄（不传=全部） |
| 规则是否执行 | 依赖校验（≥1 非空即跑，参见本文内 2.3） | 未点名时**先按 `depend_fields` 的键做覆盖门控**，再走同一套依赖校验；点名的规则跳过门控 |
| 写库 | upsert `analysis_result` | 默认**不写库**；`persist=true`（仅 `source=file`）才 upsert |
| 读提取结果 | 是 | `source=values` 否 / `source=file` 是 |
| `files.progress` | 走状态机（跑完置 `complete`） | **从不修改**，即便 `persist=true` |
| 批量 | 单文件 | `items` 多组并发 |

##### 7.1 随管线执行

`extracting` 完成后自动进入 `analyzing`：按 `type_id` + `enabled=1` 取规则、按 `priority` 升序，逐条解析占位符 → 判断/计算 → upsert 到 `analysis_result`（键为 `file_id + rule_id`）。**单条规则失败只写空结果并跳过，不影响其它规则**；全部跑完把 `files.progress` 置 `complete`。每条规则完成推 `rule_done`，阶段末推 `stage_done`（回调契约见第 9 节）。失败的单文件可用 `POST /file/{file_id}/retry/analyzing` 重跑本阶段。

##### 7.2 独立分析（`/analysis/run`）

按需触发的规则执行入口，默认不落库、**从不修改 `files.progress`**。适合「已有字段值、只想要分析结论」的外部集成，也适合「只想重跑某个文件的某几条规则」。

```jsonc
{
  "mode": "sync",                    // sync | async | stream
  "items": [
    { "type_id": "financial_report", "biz_id": "doc-001",
      "field_values": { "net_profit": "1200000", "total_revenue": "8000000" } },
    // 只跑点名的两条规则
    { "type_id": "financial_report", "biz_id": "doc-002",
      "field_values": { "net_profit": "1200000", "total_revenue": "8000000" },
      "rule_ids": ["profit_margin", "is_profitable"] }
  ]
}
```

**取值来源（`source`）**

| `source` | 字段值来源 | `field_values` | `file_id` | `persist` |
|---|---|:--:|:--:|:--:|
| `values`（默认） | 请求里的 `field_values` | 必填 | 禁传 | 不可用 |
| `file` | 该 `file_id` 已落库的 `extraction_result` | 禁传 | 必填 | 可用 |

```jsonc
{
  "mode": "sync",
  "source": "file",
  "persist": true,                   // 仅 source=file 可用
  "items": [
    { "biz_id": "doc-001", "file_id": "3f2a...",
      "rule_ids": ["profit_margin"] }   // 只重跑这一条
  ]
}
```

`source=file` 与 `POST /file/{file_id}/retry/analyzing` 的分工：

| | `/analysis/run` + `source=file` | `retry/analyzing` |
|---|---|---|
| 规则范围 | 可用 `rule_ids` 点名少数几条 | 该类型全部启用规则 |
| 落库 | 默认不落，`persist=true` 才落 | 必落 |
| `files.progress` | 不动 | 走状态机（`analyzing` → `complete`） |
| 适用 | 改了某条规则想单独验证 / 外部按需取结论 | 该阶段失败了要整体重跑 |

`source=file` 的其它语义：`type_id` 省略时取 `files.type_id`（传了且不一致 → 该 item 报错）；结果的 `source_refs` 会并入各依赖字段的提取溯源（键为 `field_id`，与 `_web_search` 同级，与管线版一致）。

**item 级 `error`**：文件不存在 / `type_id` 与文件不一致 / 该文件无提取结果，这三类查库才知道的问题不返回 422，而是把该 item 的 `error` 置为原因、`total` 记 0，**同批其它 item 照常执行**，整个请求仍是 200。能从请求体直接判断的问题（缺 `file_id`、`source=file` 还传了 `field_values`、`persist` 配 `source=values` 等）才是 422。

关键语义：

- **规则范围由 `rule_ids` 决定**：

  | `rule_ids` | 执行范围 | 依赖字段没盖全时 |
  |---|---|---|
  | 不传 / `null` | 该类型全部启用规则 | **静默跳过**——不在结果中、不计入 `total` |
  | `[]` | 不执行任何规则 | — |
  | `["a","b"]` | 只跑点名的 | 产出 `success=false` 结果（`reason` 列出缺失字段），计入 `total` / `failed` |

- **覆盖门控（不点名时最易踩坑）**：不传 `rule_ids` 时，某条规则只有当它的 `depend_fields` **每个键**都出现在该 item 的 `field_values` 里才会被执行，否则静默跳过。门控只看**键是否存在**，值可以为空（空值会在后续依赖校验里被判无效）。**显式点名的规则不受此门控**——缺键会得到一条失败结果而不是消失，便于调用方发现自己漏传了字段。
- **点名了不存在 / 未启用 / 不属于该 `type_id` 的规则不报错**，这些 ID 收进该 item 结果的 `unknown_rule_ids` 数组回传，需调用方自行检查（配错 ID 不会让请求失败）。
- `items` 之间**并发**，单个 item 内按 `priority, rule_id` 顺序执行。
- judge / custom 的 `web_search` 在这里**同样生效**。
- `async` 模式必须带 `callback_url`，用 `task_id` 推送 `rule_done` / `task_done` / `task_failed`；`stream` 走 SSE。字段签名与状态码见第 5 节。

---

#### 8. 调试规则

保存到管线前，用调试接口对**指定文件**试跑单条规则（依赖值取自该 `file_id` 已有的 `extraction_result`）：

- **同步** `POST /analysis/test`：传 `file_id` + （`rule_id` 用已存规则 / `config` 临时配置，二选一），返回 `input_values`、`expression_resolved`、`result_value`、`reason`。
- **流式** `POST /analysis/test/stream`（SSE）：分步观察每个环节。

judge / custom 的事件序列（便于定位是哪一步出问题）：

```
input_values → resolved_expression → [web_search] → prompt → llm_response → result → done
```

calc 更短：`input_values → resolved_expression → result → done`。事件清单见第 10 节。

排查建议：

- 看 `resolved_expression`：占位符是否都替换成了真实值？出现「未找到字段 '…' 的提取结果」说明该字段没提取到或没写进依赖。
- judge 看 `prompt` / `llm_response`：确认喂给模型的内容和模型原始回复。
- 启用了 web_search 就看 `web_search` 事件的 `query` 与 `results` 是否符合预期。

---

#### 9. 端到端配方：财务报表场景

目标：从年报提取关键财务指标，再计算比率、判断盈利与上市状态。全部规则挂在 `type_id = financial_report`。

**前置——字段提取**（见第 12.4 节），需先配好这些 `field_id`：`company_name`、`total_revenue`、`net_profit`、`total_assets`、`total_liabilities`。

**分析规则**（`POST /analysis/rules` 逐条 upsert）：

```json
[
  {
    "rule_id": "profit_margin",
    "type_id": "financial_report",
    "rule_name": "净利润率(%)",
    "rule_type": "calc",
    "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
    "depend_fields": ["net_profit", "total_revenue"],
    "priority": 0
  },
  {
    "rule_id": "debt_ratio",
    "type_id": "financial_report",
    "rule_name": "资产负债率(%)",
    "rule_type": "calc",
    "expression": "<field_result>total_liabilities</field_result> / <field_result>total_assets</field_result> * 100",
    "depend_fields": ["total_liabilities", "total_assets"],
    "priority": 1
  },
  {
    "rule_id": "is_profitable",
    "type_id": "financial_report",
    "rule_name": "是否盈利",
    "rule_type": "judge",
    "expression": "公司「<field_result>company_name</field_result>」净利润为 <field_result>net_profit</field_result> 元。\n\n请判断该公司是否处于盈利状态（净利润大于 0）？",
    "depend_fields": ["company_name", "net_profit"],
    "priority": 2
  },
  {
    "rule_id": "debt_risk",
    "type_id": "financial_report",
    "rule_name": "高负债风险判断",
    "rule_type": "judge",
    "expression": "公司「<field_result>company_name</field_result>」财务数据：\n- 资产总计：<field_result>total_assets</field_result> 元\n- 负债合计：<field_result>total_liabilities</field_result> 元\n\n请判断该公司资产负债率是否超过 70%（高负债风险）？",
    "depend_fields": ["company_name", "total_assets", "total_liabilities"],
    "priority": 3
  },
  {
    "rule_id": "listing_status",
    "type_id": "financial_report",
    "rule_name": "是否A股上市公司",
    "rule_type": "judge",
    "expression": "公司名称：<field_result>company_name</field_result>\n\n联网检索信息：\n<web_search_result/>\n\n请依据以上信息判断该公司当前是否为 A 股上市公司？信息不足判 false。",
    "depend_fields": ["company_name"],
    "web_search": {
      "enabled": true,
      "query": "<field_result>company_name</field_result> A股 上市 股票代码",
      "freshness": "oneYear"
    },
    "priority": 4
  }
]
```

配好后，凡是该类型下提取完成的文件都会自动跑这 5 条规则；也可用 `/analysis/run` 传外部 `field_values` 直接得结论。

---

#### 10. 常见错误与排查

| 现象 | 常见原因 | 处理 |
|---|---|---|
| 保存返回 **422**：缺 `<field_result>` | `expression` 一个字段占位符都没有 | 至少放一个 `<field_result>字段ID</field_result>` |
| 保存返回 **422**：web_search 相关 | 非 judge/custom 却启用了搜索 / `query` 为空 / `expression` 缺 `<web_search_result/>` | 参见本文内 [6. 校验规则](#校验规则启用时否则-422) 逐条对照 |
| 保存返回 **422**：output_schema | custom 开了 `is_formatted` 但 `output_schema` 为空 / 结构非法（空 children、缺 key、同级重名） | 补齐字段树；`object`/`array` 至少一个子字段 |
| 保存返回 **409** | `rule_id` 已被别的 `type_id` 占用（全局唯一） | 换一个 `rule_id` |
| 结果为空，理由「所有依赖字段均为空」 | 依赖字段在该文件没提取到值 | 先用 `/extraction/test` 确认字段能提出值；核对 `depend_fields` 的 `field_id` 拼写 |
| calc 结果错误或报「计算失败」 | 占位符解析成非数字（含逗号/货币符/「未找到…」提示）；或用了 `>` `<` 等被剥离的比较符 | 提取时要求「仅返回数值」；比较判断改用 judge |
| judge 结果不稳定/不准 | 表达式描述不清、缺少判据 | 把已知条件和判断标准写明确；用 `system_prompt` 固化口径；必要时补 `web_search` |
| web_search 无结果或走了失败提示 | `api_key`/网络问题、`query` 拼接后为空、`freshness` 过窄 | 看调试流 `web_search` 事件的 `query`/`error`；核对全局 `web_search` 配置 |
| 独立分析 `/analysis/run` 某规则「没跑」 | 未点名 `rule_ids` 时，该规则 `depend_fields` 的键未被 item 的 `field_values` **完整覆盖**，被静默跳过 | 在 `field_values` 里补齐该规则依赖的全部键；或用 `rule_ids` 点名该规则，缺键会变成带 `reason` 的失败结果而非消失（参见本文内 [7.2](#72-独立分析analysisrun)） |
| 独立分析点名的规则没出现在结果里 | 该 `rule_id` 在该 `type_id` 下不存在 / `enabled=0` | 查该 item 结果的 `unknown_rule_ids`，核对 `rule_id` 拼写与所属 `type_id` |
| 独立分析 file 模式返回 `error: 该文件无提取结果` | 该文件还没跑完 `extracting`（或提取结果已被清理） | 先查 `GET /file/{id}` 的 `progress` 是否已过 `extracting`；必要时 `POST /file/{id}/retry/extracting` |
| 独立分析 file 模式返回 `error: type_id 与文件不一致` | 请求里显式传的 `type_id` 与 `files.type_id` 不同 | 省略 `type_id` 让服务端取库里的值，或改成与文件一致 |
| 规则改了不生效 | `enabled=0`，或规则 `type_id` 与文件类型不一致 | 确认 `enabled=1` 且 `type_id` 与目标文件一致 |

通用调试顺序：`/analysis/test/stream` 看 `resolved_expression`（占位符替换对不对）→ judge 看 `prompt`/`llm_response`，calc 看 `result` 里的清洗后公式。

---

#### 11. 跨类型复制的占位符重映射

用 `POST /doctype/{type_id}/copy_from`（或从类型派生/导入）把配置复制到新类型时，字段会生成**基于源 ID 的新 `field_id`**。分析规则里的占位符会随 `depend_fields` **自动重映射**：`expression` 与 `web_search.query` 中的 `<field_result>旧字段ID</field_result>` 会被改写为新副本的字段 ID；依赖的字段若没被一起复制，则该依赖会回报给调用方（不静默丢弃）。复制完成后两份配置完全独立，改一份不影响另一份。存量副本若缺 `parent_type_id` 血缘，需手工标模板，此后经复制/派生的类型会自动记录来源。

---

##### 附录：字段速查

| 用途 | 字段 | 适用类型 |
|---|---|---|
| 引用提取值 | `expression` 内 `<field_result>字段ID</field_result>` | judge / calc / custom |
| 注入搜索结果 | `expression` 内 `<web_search_result/>` | judge / custom（启用 web_search 时必填） |
| 声明依赖/门控 | `depend_fields` | judge / calc / custom |
| 裁判口径 | `system_prompt` | judge / custom |
| 联网检索 | `web_search.{enabled,query,count,freshness}` | judge / custom |
| 格式化输出 | `is_formatted` + `output_schema` | 仅 custom |
| 计算精度 | 全局 `analysis.calc_precision`（默认 2） | 仅 calc |

**相关文档**：analysis 接口参考 · source-refs 指南 · configuration 指南 · extraction-config 指南 · callbacks 参考 · sse 参考


### 12.3 `config.yaml` 配置手册

> 对应服务版本 0.3.0

析卷 AI 的全部运行参数集中在 `configs/config.yaml`。本页逐节列出配置项、默认值与含义，作为配置的完整说明参考。

#### 加载与生效

- 默认路径 `configs/config.yaml`，可用环境变量 `APP_CONFIG_PATH` 指向其他文件。
- 由 `utils/config.py` 的 `get_config()` 以 `lru_cache` 单例加载；每节对应一个 Pydantic 模型（`ServerConfig`、`MineruConfig` …）。文件缺失时全部回退到模型内置默认。
- 配置在进程启动时读入并缓存，**改动后需重启服务**才生效。
- YAML 里省略某个键，即采用本页「默认值」列的值；省略整节则该节全部取默认。

#### 读法约定

- **默认值** 列是代码内置默认（`utils/config.py` 中各 Pydantic 模型的字段默认），即省略该键时实际生效的值。
- 仓库内 `configs/config.yaml` 是**部署示例**，会用环境实际值（真实主机 / 端口 / 密钥 / 模型名）覆盖默认。示例值与默认不同的，在「含义」列以「示例:」标注。
- `llm_extra_body` / `extra_body` 等对象会**原样透传**到底层 OpenAI 兼容请求的 `extra_body`，可用于关闭思考等模型私有开关。

---

#### server — HTTP 服务

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `host` | 字符串 | `"0.0.0.0"` | 监听地址，`0.0.0.0` = 所有网卡 |
| `port` | 整数 | `8080` | 监听端口。示例: `5019` |

#### mineru — 外部 PDF 解析服务

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `base_url` | 字符串 | `"http://localhost:8888"` | MinerU 服务地址 |
| `backend` | 字符串 | `"vllm-async-engine"` | MinerU 解析后端引擎 |
| `queue_width` | 整数 | `1` | 解析队列宽度（并发度） |
| `parse_timeout` | 整数 | `300` | 单文件解析轮询超时（秒）。示例: `1200` |
| `max_file_size` | 整数 | `104857600` | 上传 PDF 大小上限（字节），超限直接拒收。示例: `1048576000`（约 1000MB） |

#### chunking — 递归文本分块

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `chunk_size` | 整数 | `512` | 目标分块大小（字符） |
| `chunk_overlap` | 整数 | `50` | 相邻分块的重叠字符数 |
| `max_chunk_size` | 整数 | `2048` | 分块最大字符数；超长表格另按 `</tr>`/`</td>`/`\n` 边界再切 |
| `separators` | 字符串数组 | `["\n\n", "\n", "。", " "]` | 递归切分时按优先级依次尝试的分隔符 |

#### embedding — 向量化（OpenAI 兼容 Embedding API）

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `base_url` | 字符串 | `"http://localhost:8000/v1"` | Embedding API 地址 |
| `model_name` | 字符串 | `"bge-large-zh"` | 向量模型名。示例: `qwen3-embedding-8b` |
| `api_key` | 字符串 | `""` | API 密钥 |
| `embedding_dim` | 整数 | `1024` | 向量维度，**必须与模型输出及 Milvus 集合维度一致**。示例: `4096` |
| `batch_size` | 整数 | `32` | 每批向量化的文本条数。示例: `10` |
| `timeout` | 整数 | `60` | 请求超时（秒） |
| `retry_count` | 整数 | `3` | 失败重试次数 |

> 单条文本超过 **8192 字符**会在向量化前截断。

#### milvus — 向量数据库

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `host` | 字符串 | `"localhost"` | Milvus 地址 |
| `port` | 整数 | `19530` | 端口。示例: `7067` |
| `user` | 字符串 | `""` | 用户名 |
| `password` | 字符串 | `""` | 密码 |
| `collection_name` | 字符串 | `"file_chunks"` | 集合名，启动时不存在则自动创建 |
| `index_type` | 字符串 | `"IVF_FLAT"` | 向量索引类型 |
| `metric_type` | 字符串 | `"COSINE"` | 距离度量方式 |
| `nlist` | 整数 | `1024` | IVF 聚类桶数。示例: `4096` |
| `search_topk` | 整数 | `10` | 语义检索默认返回条数 |

#### mysql — 关系库（异步 aiomysql）

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `host` | 字符串 | `"localhost"` | MySQL 地址 |
| `port` | 整数 | `3306` | 端口。示例: `8117` |
| `database` | 字符串 | `"file_parser"` | 库名。示例: `wanzi_prase2_001` |
| `username` | 字符串 | `"root"` | 用户名 |
| `password` | 字符串 | `""` | 密码 |
| `pool_size` | 整数 | `50` | 连接池常驻连接数 |
| `max_overflow` | 整数 | `10` | 连接池允许溢出的额外连接数 |
| `pool_timeout` | 整数 | `10` | 从连接池获取连接的等待超时（秒） |

#### extraction — 字段提取 LLM（OpenAI 兼容 Chat API）

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `llm_base_url` | 字符串 | `"http://localhost:8000/v1"` | LLM API 地址 |
| `llm_model` | 字符串 | `"qwen-7b"` | 模型名。示例: `qwen3.5-122b` |
| `llm_api_key` | 字符串 | `""` | API 密钥 |
| `llm_timeout` | 整数 | `60` | 请求超时（秒） |
| `llm_retry_count` | 整数 | `3` | 失败重试次数；指数退避，4xx（除 429）不重试 |
| `max_context_length` | 整数 | `4096` | 注入 prompt 的检索文本字符上限，超长从末尾截断 |
| `llm_extra_body` | 对象 | `{}` | 透传到请求 `extra_body` 的额外参数。示例: `{chat_template_kwargs: {enable_thinking: false}}`（关闭思考） |

#### table_name_validation — 表名校验 LLM（tableing 阶段，独立且可回退）

本节全部字段可为空（`null`）。为空时回退到 `extraction` 的同名配置，便于表名校验复用主 LLM 又能单独覆写。

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `llm_base_url` | 字符串 / null | `null` | 为空回退 `extraction.llm_base_url` |
| `llm_model` | 字符串 / null | `null` | 为空回退 `extraction.llm_model` |
| `llm_api_key` | 字符串 / null | `null` | 为空回退 `extraction.llm_api_key` |
| `llm_timeout` | 整数 / null | `null` | 为空回退 `extraction.llm_timeout` |
| `llm_retry_count` | 整数 / null | `null` | 为空回退 `extraction.llm_retry_count` |
| `max_context_length` | 整数 / null | `null` | 表名上文取样的字符上限；为空回退 `extraction.max_context_length` |
| `max_context_lines` | 整数 / null | `null` | 表格前用于推断表名的上文行数；为空按 `3` |
| `max_concurrency` | 整数 / null | `null` | 表名校验的并发上限；为空按 `1` |
| `llm_extra_body` | 对象 / null | `null` | 透传到请求 `extra_body` 的额外参数 |

#### analysis — 逻辑分析

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `calc_precision` | 整数 | `2` | `calc` 规则 numexpr 计算结果保留的小数位 |
| `judge_timeout` | 整数 | `30` | `judge` 规则 LLM 判断的超时（秒） |

#### vl_model — 视觉模型抽取（OpenAI 兼容多模态）

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `base_url` | 字符串 | `"https://dashscope.aliyuncs.com/compatible-mode/v1"` | VL 模型 API 地址 |
| `api_key` | 字符串 | `""` | API 密钥 |
| `model` | 字符串 | `"qwen-vl-max"` | 多模态模型名。示例: `qwen3.5-122b` |
| `temperature` | 浮点 | `0.1` | 采样温度 |
| `max_tokens` | 整数 | `4096` | 单次生成的最大 token |
| `timeout` | 整数 | `180` | 请求超时（秒） |
| `extra_body` | 对象 | `{}` | 透传到请求 `extra_body` 的额外参数 |
| `global_max_concurrency` | 整数 | `8` | 全局 VL 调用并发信号量上限（跨所有字段/文件） |
| `default_max_pixels` | 整数 | `4000000` | 单图默认像素上限，可被字段 `vl_config.max_pixels` 覆盖 |
| `pdf_storage_dir` | 字符串 | `"uploads"` | 上传 PDF 的持久化目录，VL 抽取直接读取原始字节 |

#### web_search — 网络搜索（博查 Bocha AI，judge 规则使用）

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `base_url` | 字符串 | `"https://api.bochaai.com/v1/web-search"` | 搜索 API 地址 |
| `api_key` | 字符串 | `""` | API 密钥 |
| `count` | 整数 | `5` | 默认返回条数（可被规则 `web_search.count` 覆盖） |
| `summary` | 布尔 | `true` | 是否返回长摘要 |
| `freshness` | 字符串 | `"noLimit"` | 默认时间范围：`noLimit` / `oneDay` / `oneWeek` / `oneMonth` / `oneYear` |
| `timeout` | 整数 | `10` | 请求超时（秒） |
| `retry_count` | 整数 | `2` | 失败重试次数 |
| `max_result_length` | 整数 | `4000` | 注入 prompt 的搜索文本字符上限，超长从末尾截断 |

> 搜索失败不致命：占位符替换为失败提示后继续判断。溯源存 `source_refs._web_search`。

#### storage — PDF 保留治理

治理 `uploads` 下的原始 PDF，**只删物理文件、不动数据库**；被清文件的解析 / 抽取结果仍可查，仅 PDF 预览与 VL 抽取会返回 404。启动时、每 `cleanup_interval_minutes` 分钟、每次上传后各触发一次清理。

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `max_total_bytes` | 整数 | `0` | PDF 总大小上限（字节），`0` = 不限；超限按最旧 `create_time` 优先淘汰。示例: `10737418240`（10GB） |
| `max_retention_minutes` | 整数 | `0` | PDF 最长保留时长（分钟），`0` = 不限；超时即删除。示例: `4320`（3 天） |
| `cleanup_interval_minutes` | 整数 | `10` | 后台清理扫描周期（分钟） |


### 12.4 字段提取配置手册

> 对应服务版本 0.3.0

本手册讲**怎么配**字段提取（`extraction_field`）：三类来源（table / text / vl）如何选、
`search_config` / `vl_config` 各调什么、占位符怎么写、配错了怎么排查。以配方、可直接套用的
JSON 示例和排错清单为主。

- **字段全清单 / 类型 / 可空性**（`extraction_field` 的每一列）见第 12.6 节。
- **枚举合法取值**（`source_type` / `table_match_type` / `search_type` / `vl_method`）见第 11 节和第 12.7 节。
- **接口出入参 / 状态码**（`POST /extraction/fields`、调试接口）见第 4 节。
- **溯源结构**（`source_refs`、命中片段、bbox、模型自报页码）见第 12.5 节。
- **逻辑分析**（`<field_result>` 占位符、judge / calc 规则）见第 12.2 节。
- **进阶字段**（引用其他字段的提取结果 / 按前序字段页码联动取文）见本页 [§6](#6-进阶字段字段引用--页码联动)。

---

#### 1. 先选来源类型

一个字段只走一种来源，由 `source_type` 决定，并激活对应的一组配置字段：

| source_type | 子类型 | 适用场景 | 关键配置 |
|---|---|---|---|
| `table` | — | 结构化表格（财报、统计表） | `table_name_pattern`、`table_match_type` |
| `text` | `search_type=context` | 关键词上下文 | `keywords`、`context_before/after` |
| `text` | `search_type=section` | 整段章节 | `section_pattern`、`section_match_type` |
| `text` | `search_type=rule` | 停用词边界精确切片 | `keywords`、`stop_words`、`direction` |
| `text` | `search_type=chunk_db` | 分块库关键词过滤 | `keywords`、`max_results` |
| `text` | `search_type=vector_db` | 语义相似检索 | `query_text`、`top_k` |
| `text` | `search_type=page` | 按页码整片喂 LLM | `page_range`、`max_length` |
| `vl` | `vl_method=vl_model` | 短文档 / 相关页固定，一次视觉抽取 | `page_range`、`max_pages` |
| `vl` | `vl_method=vl_progressive` | 长文档、相关页分散，逐批扫描 | `field_hints`、`batch_size`、`page_range` |
| `vl` | `vl_method=vl_locate` | 长文档，先定位关键页再高清抽取 | `field_hints`、`grid_pages`、`page_range` |

**怎么选：**
- 数据在**规整表格**里 → `table`。
- 数据在**正文文字**里，且能被 MinerU 正确解析成 Markdown → `text`（按下面 6 种检索方式挑）。
- 数据藏在**扫描图 / 复杂版式 / 跨页**里，文本检索捞不全 → `vl`（直接读 PDF 原图，绕过 Markdown）。

`field_id`（`^[a-zA-Z0-9_]+$`，**全局唯一**）、`field_name`、`enabled`（默认 1）、`priority`
（默认 0，越小越先）等通用字段各类共用，含义见第 12.6 节的 `extraction_field`。

---

#### 2. 表格类（table）

从解析出的 `<table>` 块里抽数，适合财务报表、数据统计表等结构化内容。

##### 匹配方式 `table_match_type`

| 值 | 说明 | 示例 |
|---|---|---|
| `exact` | 表名完全相同才命中 | `"利润表"` 只匹配名为 `"利润表"` 的表 |
| `fuzzy` | 相似度 ≥ 80% 即命中 | `"利润表"` 可命中 `"合并利润表"`、`"利润表（续）"` |
| `contains` | 表名包含指定文字即命中 | `"利润"` 命中所有名字含「利润」的表 |
| `llm` | 由 LLM 语义判断是否相关 | `"收入相关"` 可命中 `"营业收入明细表"` |

从严到宽：`exact` < `fuzzy` < `contains` < `llm`。捞不到就往宽调，捞太多杂表就往严调，或改用 `table_match_keywords` / `table_match_max_results` 收敛命中集（见第 12.6 节的 `extraction_field`）。选 `llm` 时匹配提示词可按字段自定义（`table_match_prompt`，留空用系统默认），参见本文内 [§7.4](#74-llm-匹配提示词可配置)。

##### 配方

**① 从利润表取营业总收入**（表名可能带「合并」前缀 → 用 `fuzzy`）：

```json
{
  "field_id": "total_revenue",
  "field_name": "营业总收入",
  "source_type": "table",
  "priority": 1,
  "table_name_pattern": "利润表",
  "table_match_type": "fuzzy",
  "table_extract_prompt": "以下是检索到的利润表：\n<search_result>利润表</search_result>\n\n请找到「营业总收入」或「营业收入」对应的金额，仅返回数值（单位：元）。"
}
```

**② 多表兜底**（合并报表优先，无则退母公司报表 → 用 `contains` 一网打尽，在 prompt 里排序）：

```json
{
  "field_id": "net_profit",
  "field_name": "净利润",
  "source_type": "table",
  "priority": 3,
  "table_name_pattern": "利润",
  "table_match_type": "contains",
  "table_extract_prompt": "以下是利润相关表格：\n<search_result>利润</search_result>\n\n请优先从「合并利润表」提取「净利润」金额；若无合并报表，则从「利润表」提取。仅返回数值。"
}
```

**占位符标签 = `table_name_pattern` 的值**：上面 `<search_result>利润表</search_result>` / `<search_result>利润</search_result>` 的标签必须与 `table_name_pattern` 一致，命中表格的内容才会被注入到该占位符处。

---

#### 3. 文本类（text）

从正文抽取，`search_type` 决定「怎么把相关文字捞出来喂给 LLM」。六选一：

| search_type | 原理 | 占位符标签取值 |
|---|---|---|
| `context` | 全文找关键词，取其前后若干字符 | `keywords` 里每个关键词各一个占位符 |
| `section` | 匹配 Markdown 章节标题，取整段 | `section_pattern` 的值 |
| `rule` | 找到关键词后扩展到停用词边界，精确切片 | `keywords` 里的关键词 |
| `chunk_db` | 从预分块（MySQL）按关键词过滤 | `keywords` 里的关键词 |
| `vector_db` | 查询文本向量化，Milvus 语义召回 | `query_text` 的值 |
| `page` | 按页码直接切 Markdown 整片喂 LLM | 固定 `page_content` |

> 下面每种只列**常用可调项**；`search_config` 是自由 JSON，全部键与默认值见第 12.6 节的 `extraction_field`。

##### 3.1 上下文检索 `context`

关键词命中点前取 `context_before`（默认 200）字符、后取 `context_after`（默认 200）字符。`max_results`（默认 5）限制条数。多关键词时**每个关键词一个占位符**。

```json
{
  "field_id": "company_name",
  "field_name": "公司名称",
  "source_type": "text",
  "search_type": "context",
  "search_config": {
    "keywords": ["公司名称", "企业名称", "甲方"],
    "context_before": 50,
    "context_after": 100,
    "max_results": 3
  },
  "text_extract_prompt": "请从以下内容提取公司全称：\n<search_result>公司名称</search_result>\n<search_result>企业名称</search_result>\n<search_result>甲方</search_result>\n\n返回完整公司名称，不含简称。"
}
```

##### 3.2 章节检索 `section`

匹配 Markdown 标题（如 `# 1.1 经营范围`）并取整段。`section_match_type`（`exact`/`fuzzy`/`contains`/`llm`，默认 `contains`）、`threshold`（fuzzy 阈值 0.8）、`max_results`（默认 3）。选 `llm` 时匹配提示词可按字段自定义（`search_config.section_match_prompt`，留空用系统默认），参见本文内 [§7.4](#74-llm-匹配提示词可配置)。**占位符标签用 `section_pattern` 的值。**

```json
{
  "field_id": "business_scope",
  "field_name": "经营范围",
  "source_type": "text",
  "search_type": "section",
  "search_config": { "section_pattern": "经营范围", "section_match_type": "contains", "max_results": 1 },
  "text_extract_prompt": "以下是经营范围章节：\n<search_result>经营范围</search_result>\n\n请完整提取公司的经营范围描述。"
}
```

##### 3.3 规则检索 `rule`

找到关键词后按 `direction` 扩展（`forward`=向关键词**之后**扩展，`backward`=向关键词**之前**扩展，`both`=双向；默认 forward），止于最近的停用词，切出一段。默认停用词 `["#", "##", "###", "\n\n", "\n", "。", ".", "；", ";"]`；`min_length`（2）、`max_length`（200）兜底。适合「关键词 + 一小段值」的精确提取。

```json
{
  "field_id": "registration_date",
  "field_name": "注册日期",
  "source_type": "text",
  "search_type": "rule",
  "search_config": {
    "keywords": ["成立日期", "注册日期", "设立日期"],
    "direction": "forward",
    "stop_words": ["\n", "。", "；", "，"],
    "max_length": 50
  },
  "text_extract_prompt": "从以下内容提取注册日期：\n<search_result>成立日期</search_result>\n<search_result>注册日期</search_result>\n<search_result>设立日期</search_result>\n\n以 YYYY-MM-DD 格式返回。"
}
```

##### 3.4 分块库检索 `chunk_db`

从入库的文本分块里按 `keywords` 过滤，`max_results`（默认 10，亦可写 `top_k`）限制分块数。适合「相关内容散落多段、要整段喂 LLM 归纳」的场景。

```json
{
  "field_id": "risk_factors",
  "field_name": "风险因素",
  "source_type": "text",
  "search_type": "chunk_db",
  "search_config": { "keywords": ["风险", "不确定性"], "max_results": 5 },
  "text_extract_prompt": "以下是含风险内容的段落：\n<search_result>风险</search_result>\n<search_result>不确定性</search_result>\n\n请总结主要风险因素（不超过 3 条）。"
}
```

##### 3.5 向量检索 `vector_db`

`query_text` 向量化后从 Milvus 召回 `top_k`（默认 5）条最相似分块，`score_threshold`（L2 距离，越小越相似）可选过滤。适合关键词不固定、要「语义找」的抽象字段。**占位符标签用 `query_text` 的值。**

```json
{
  "field_id": "core_competitiveness",
  "field_name": "核心竞争力",
  "source_type": "text",
  "search_type": "vector_db",
  "search_config": { "query_text": "公司的核心竞争力和竞争优势是什么", "top_k": 3, "score_threshold": 0.5 },
  "text_extract_prompt": "以下是与核心竞争力相关的内容：\n<search_result>公司的核心竞争力和竞争优势是什么</search_result>\n\n请总结公司核心竞争力（不超过 5 条）。"
}
```

##### 3.6 按页检索 `page`

不做关键词/语义检索，直接按 `page_range` 把 Markdown 整片切出来喂 LLM。`page_range` 支持 `"all"` / `"1-3"` / `"1-3,5"` / `"2"`；`max_length`（默认 30000）超长时末尾截断。**占位符标签固定为 `page_content`。** 适合「相关信息集中在已知页码」的定点提取。

```json
{
  "field_id": "cover_title",
  "field_name": "封面标题",
  "source_type": "text",
  "search_type": "page",
  "search_config": { "page_range": "1-1", "max_length": 5000 },
  "text_extract_prompt": "以下是文档首页内容：\n<search_result>page_content</search_result>\n\n请提取封面标题。"
}
```

---

#### 4. VL 类（vl）

直接读 `uploads/{file_id}.pdf` 渲染成图给视觉模型，**不**依赖 MinerU 的 Markdown、**不**走文本 LLM 二次抽取，由 VL 直接输出 `{value, reason}` JSON。适合扫描图、复杂版式、跨页信息。

**前置条件：**
- `configs/config.yaml` 的 `vl_model:` 节配好 `base_url` / `api_key` / `model`（默认 dashscope qwen-vl-max），见第 12.3 节。
- 文件上传时 PDF 已持久化到 `uploads/{file_id}.pdf`（被 storage 保留策略清掉的文件抽取会 404）。

**三法对比：**

| vl_method | 思路 | VL 调用次数 | 并发 | 适用 |
|---|---|---|---|---|
| `vl_model` | 一把梭：指定页全塞 VL | 1 | — | 短文档、相关页固定 |
| `vl_progressive` | 逐批 + 伪历史累积，模型自判相关性 | 页数/`batch_size` + 1 聚合 | 串行 | 长文档、相关页分散 |
| `vl_locate` | 两轮：缩略图网格并行定位 → 关键页高清提取 | 页数/`grid_pages` + 1 提取 | 第一轮并行 | 长文档、要快速定位关键页 |

**三法共通：** `vl_extract_prompt` 是最终提取 prompt，**必须含 `value` 与 `reason` 关键字**（大小写不敏感，因为要 VL 直接吐 JSON）；`vl_system_prompt` 可空。后端 `service/vl_service/_defaults.py` 与前端 UI 都预填了默认 prompt，保持默认即可跑通。全局并发上限 `vl_model.global_max_concurrency`（默认 8）。

**三种方法共用的页码配置**（都写在 `vl_config` 里）：

| 键 | 默认 | 含义 |
|---|---|---|
| `page_range` | `"all"` | 纳入视野的页，语法同 3.6（`"1-3,5"`）。重复页会自动去重，不重复渲染 |
| `max_pages` | 不限 | 候选页上限，超出取**前 N 页** |
| `max_pixels` | 4000000 | 单图像素上限，超出按比例缩 |

`page_range` 对 `vl_progressive` 是「只在这些页里逐批扫」，对 `vl_locate` 是「只在这些页里做网格定位」。

##### 4.1 vl_model（全量）

`vl_config`：无专属键，只用上面的共用页码配置。

```json
{
  "field_id": "company_name_vl",
  "field_name": "企业名称",
  "source_type": "vl",
  "vl_method": "vl_model",
  "vl_config": { "page_range": "1-1", "max_pixels": 4000000 },
  "vl_extract_prompt": "请基于以上图片提取企业全称。\n只返回 JSON：{\"value\": \"企业全称\", \"reason\": \"在哪一页/位置看到\"}\n未找到返回：{\"value\": \"\", \"reason\": \"未找到\"}"
}
```

##### 4.2 vl_progressive（逐批扫描）

`vl_config`：`field_hints`（必填，人话描述要找什么，如 `"投资金额、签署日期、股东姓名"`）、`batch_size`（每批页数，默认 2）、`max_pixels`。可选 `batch_prompt_template` 覆盖批次 prompt，**自定义时必须含占位符** `{history}` `{field_hints}` `{page_label}` `{total_pages}`；另可选用 `{scan_scope}`（限页时展开为扫描范围说明，全文时为空串，老模板不含它也不报错）。注意 `{total_pages}` 恒为**文档总页数**，不是本次扫描页数。

```json
{
  "field_id": "contract_summary",
  "field_name": "合同关键信息",
  "source_type": "vl",
  "vl_method": "vl_progressive",
  "vl_config": { "field_hints": "签署日期、签约方、合同金额、有效期", "batch_size": 2 },
  "vl_extract_prompt": "基于以上累积摘要，综合整理合同关键信息。\n只返回 JSON：{\"value\": \"日期/签约方/金额/有效期，多项用分号分隔\", \"reason\": \"分别在哪些页看到\"}"
}
```

##### 4.3 vl_locate（缩略图定位 + 高清提取）

`vl_config`：`field_hints`（必填）、`grid_pages`（每张网格图页数，默认 6）、`grid_cols`（列数，默认 3）、`max_concurrent`（第一轮并行上限，默认 20，与全局并发取小）、`key_pages_limit`（关键页上限，默认 6）、`fallback_pages`（一页未命中时回退取前 N 页，默认 3）、`max_pixels`。可选 `locate_prompt_template` 覆盖定位 prompt，**自定义时必须含占位符** `{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}`。

> `key_pages_limit` 与共用的 `max_pages` 是**两个阶段**的约束，不要混淆：`max_pages` 管定位**之前**扫几页缩略图，`key_pages_limit` 管定位**之后**看几页高清。`fallback_pages` 取的是**候选页**的前 N 个（限了第 11-15 页就兜底看 11、12，不是文档第 1、2 页）。

```json
{
  "field_id": "total_assets_vl",
  "field_name": "资产总额",
  "source_type": "vl",
  "vl_method": "vl_locate",
  "vl_config": { "field_hints": "资产总额、负债总额、净利润", "grid_pages": 6, "grid_cols": 3, "key_pages_limit": 6 },
  "vl_extract_prompt": "请从以上高清财报页提取「资产总额」金额。\n只返回 JSON：{\"value\": \"金额（含单位）\", \"reason\": \"看到的页码与位置\"}\n未找到返回：{\"value\": \"\", \"reason\": \"未找到\"}"
}
```

> **模板转义坑：** 自定义 `batch_prompt_template` / `locate_prompt_template` 由后端 `str.format()` 渲染，模板里想输出**字面** `{` `}`（如 JSON 花括号）必须写成 `{{` `}}`，否则渲染报错。

---

#### 5. use_llm 开关（text / table）

`use_llm`（默认 1）**只对 text / table 生效**，vl 恒需模型、不读此开关。置 `0` 时检索照常跑、`source_refs` 照常构建，但**跳过占位符校验与 LLM 调用**，直接把各标签检索原文用 `\n---\n` 拼成 `value`，`reason` 固定为「未启用 LLM，直接返回检索原文」。

用途：只想拿到「命中的原文片段」而不需要模型加工时（后续自己处理，或交给 逻辑分析）。此时提取 prompt 的占位符必填要求被放宽（可留空）。前端字段表单有「使用 LLM 提取」勾选框（VL 时隐藏）。

---

#### 6. 进阶字段（字段引用 + 页码联动）

普通字段各自独立抽取；**进阶字段**（`is_advanced=1`）在**全部普通字段抽完之后**才执行，
它的检索配置可以引用前面已抽出来的值。典型场景：先抽出「甲方公司名」，再用这个名字去正文里
定位「该公司的注册资本」。

**两条硬规则：**
1. 进阶字段**只能引用普通字段**（不支持进阶引用进阶）。引用了不存在或本身是进阶的字段 → 保存 **400**。
2. `depend_fields` 由服务端扫描配置自动算出，**请求里传什么都会被覆盖**；`GET /extraction/fields` 回传它。

##### 6.1 字段引用 `<field_result>字段ID</field_result>`

在进阶字段的配置里写占位符，抽取前会被替换为被引用字段的**提取值**。支持的位置：

| 位置 | 例 |
|---|---|
| `search_config` 里的字符串值 | `query_text` / `section_pattern` |
| `search_config` 里的字符串列表 | `keywords` / `stop_words` |
| `table_match_keywords` | 表格匹配词 |
| 各类提示词 | `text_extract_prompt` / `table_extract_prompt` / `*_system_prompt` / `vl_extract_prompt` |
| `vl_config` | `field_hints` / `batch_prompt_template` / `locate_prompt_template` |
| `vl_config.page_source_field` | 页码来源字段 ID（不是占位符，直接填 field_id） |

```json
{
  "field_id": "registered_capital",
  "field_name": "注册资本",
  "source_type": "text",
  "is_advanced": 1,
  "search_type": "context",
  "search_config": {
    "keywords": ["<field_result>party_a</field_result>"],
    "context_after": 60,
    "max_results": 3
  },
  "text_extract_prompt": "从 <search_result><field_result>party_a</field_result></search_result> 中提取注册资本"
}
```

被引用字段没抽到值（空串）时，该引用被替换为空串；列表类配置（`keywords` 等）里因此变空的项**会被剔除**，
避免空关键词命中全文。注意 `<search_result>` 标签名同样会被替换 —— 上例最终标签是「甲方公司名的值」，
与检索关键词一致，占位符才对得上。

##### 6.2 页码联动（`search_type=page`）

让进阶字段只读「前一个字段所在的那几页」：

```json
{
  "field_id": "capital_by_page",
  "source_type": "text",
  "is_advanced": 1,
  "search_type": "page",
  "search_config": {
    "page_source_field": "party_a",
    "max_pages": 3,
    "max_length": 30000
  },
  "text_extract_prompt": "从 <search_result>page_content</search_result> 提取注册资本"
}
```

- `page_source_field`：来源**普通**字段的 `field_id`。取它的 **`source_pages`（可用页码）** 派生区间
  `[min, max]`，**覆盖**手填的 `page_range`。`source_pages` = 模型自报页（参见本文内 §7.3）优先，模型没自报时
  自动回落到程序从 `source_refs` 算出的命中页。
- `max_pages`：区间跨度上限，超了就从最小页起收敛（如来源页 `[3, 7]` + `max_pages=3` → 取 `3-5`）。
- 来源字段**既没有模型自报页、也没有任何检索命中页**（如抽取失败）→ 该进阶字段才失败。
  让来源字段的 prompt 要求返回 `pages` 仍是**推荐做法** —— 模型自报页通常比检索命中页更聚焦，
  派生出的区间更窄、更准。

**VL 的页码联动**：`source_type=vl` 的进阶字段把 `page_source_field` 写在 `vl_config` 里（不是 `search_config`），
三种 `vl_method` 都支持。与 text 的区别是**取离散页而非连续区间** —— 来源页为 `[3, 9, 15]` 时 VL 只渲染第 3、9、
15 页，不看中间页（VL 按页出图，跳页零代价）。`max_pages` 同样写在 `vl_config` 里，超出时取前 N 页。联动**覆盖**
手填的 `page_range`；来源字段无任何可用页码时该进阶字段才失败（与 text 一致）。

> 溯源里的 `_page_link.pages_from` 会标明这次联动用的是模型自报页（`"model"`）还是命中页兜底（`"refs"`）。

```json
{
  "field_id": "seal_by_page",
  "source_type": "vl",
  "is_advanced": 1,
  "vl_method": "vl_locate",
  "vl_config": {
    "page_source_field": "party_a",
    "max_pages": 3,
    "field_hints": "公章、骑缝章"
  },
  "vl_extract_prompt": "判断这几页是否盖章，输出 JSON {value, reason}"
}
```

##### 6.3 溯源与调试

进阶字段的解析过程会写进 `source_refs`（与检索 ref 同级）：

| 键 | 内容 |
|---|---|
| `_resolved_refs` | `{被引用 field_id: 实际填入的值}` |
| `_page_link` | 页码联动溯源。text `page` 检索：`{source_field, model_pages, mode:"range", derived_range: [start,end], capped}`；VL：`{source_field, model_pages, mode:"discrete", derived_pages: [...], capped}` |

调试接口 `POST /extraction/test/stream` 对进阶字段会**先推一个 `resolved_refs` 事件**再进入常规流程；
非流式 `POST /extraction/test` 则在响应里多回一个 `resolved_refs` 字段。两者读的都是该文件
**已落库**的普通字段结果，所以要先完整跑过一次提取。

##### 6.4 上游字段没抽到值会怎样

引用被替换为空串 → 关键词被剔除 / 占位符标签变空 → 检索必然落空。此时进阶字段**记为失败**
（与普通字段一致，不会出现「成功但值为空」），`reason` 会点名是哪个上游字段没取到值，
后端同时打一条 warning 日志。

为避免留下悬空引用，还有三道保护：

| 动作 | 行为 |
|---|---|
| 删除被进阶字段引用的普通字段 | **409**；确需删除加 `?force=true` |
| 把被引用的普通字段改成进阶字段 | **400**（会绕过「进阶只能引用普通」的不变量） |
| 禁用被引用的普通字段 | 放行，但返回消息里提示引用方会解析为空 |
| 复制 / 导入时没带上被引用的字段 | 占位符原样保留，并记入响应的 `missing_dependencies` |

复制类型（`copy_from`）/ 导出 / 导入时，占位符与 `page_source_field` 会按新旧 `field_id` 映射**自动重写**，
副本之间互不影响。

---

#### 7. 占位符规范

##### 7.1 `<search_result>标签</search_result>`（检索结果）

用于 `text_extract_prompt` / `table_extract_prompt`。**标签内容不是随便写的**，必须与检索配置对应，命中内容才会被注入到该占位符：

| 来源 | 标签取什么 |
|---|---|
| table | `table_name_pattern` 的值 |
| text · context / rule / chunk_db | `keywords` 里的每个关键词（各一个占位符） |
| text · section | `section_pattern` 的值 |
| text · vector_db | `query_text` 的值 |
| text · page | 固定 `page_content` |

无命中时占位符被替换为 `（未找到 '标签' 的相关内容）`，LLM 仍会执行、通常返回空值。

##### 7.2 校验规则（配错即 422）

| 配置 | 规则 |
|---|---|
| `text_extract_prompt` | 含 ≥1 个 `<search_result>标签</search_result>`（`use_llm=0` 放宽） |
| `table_extract_prompt` | 含 ≥1 个 `<search_result>标签</search_result>`（`use_llm=0` 放宽） |
| `vl_extract_prompt` | 含 `value` 与 `reason` 关键字（大小写不敏感）；`source_type=vl` 时必填，`use_llm` **不**放宽 |
| `vl_method` | `source_type=vl` 时必填 |
| `batch_prompt_template`（vl_progressive 自定义时） | 含 `{history}` `{field_hints}` `{page_label}` `{total_pages}` |
| `locate_prompt_template`（vl_locate 自定义时） | 含 `{field_hints}` `{page_labels}` `{position_map}` `{grid_rows}` `{grid_cols}` |
| `table_match_prompt`（`table_match_type=llm` 且非空时） | 含 `{table_list}` |
| `search_config.section_match_prompt`（`section_match_type=llm` 且非空时） | 含 `{section_list}` |

##### 7.3 让模型自报参考页码（可选）

text / table 抽取时，可在 prompt 里要求 LLM 除 `value` / `reason` 外再返回 `pages`（参考到的页码整数数组）。后端归一化后作为**顶层 `pages` 字段**下发（不再放进 `source_refs`），并落库到 `extraction_result.model_pages` 列。

即使不要求 `pages`，接口也恒有一个顶层 `source_pages` 字段 —— 模型自报页优先、程序命中页兜底，前端 PDF 定位（📍）跳的就是它。要求 `pages` 的价值在于**更聚焦**：检索常命中多页，模型自报的通常只有真正引用的那一两页，进阶字段页码联动据此派生的区间也更窄。细节见第 12.5 节第 7 小节。

##### 7.4 LLM 匹配提示词（可配置）

表格匹配（`table_match_type=llm`）与章节匹配（`section_match_type=llm`）的提示词可按字段自定义。**留空即用系统默认模板**，不配也能跑。

**提示词分两段**

| 段 | 内容 | 谁来写 |
|---|---|---|
| 用户可编辑段 | 说明「要找什么、怎么找」，即下面配置的模板 | 用户（可留空用默认） |
| 系统固定段 | 输出格式指令：`只返回序号，不要输出其他内容。例如：2 或 1,3` | 系统恒定追加到模板末尾，**用户改不到** |

输出格式段之所以不开放：匹配结果靠正则抓响应里的整数序号解析，模板若被改成「返回 JSON」，解析器会把 JSON 里的其它数字一并当成序号，命中集直接错乱。同理，模板里也不要要求模型解释理由或输出表名。

**存储位置（两者不对称）**

| 匹配类型 | 存哪 | 空值含义 |
|---|---|---|
| 表格 | `extraction_field.table_match_prompt`（独立列） | 用系统默认模板 |
| 章节 | `search_config.section_match_prompt`（JSON 内的键） | 用系统默认模板 |

**可用占位符**

| 占位符 | 表格匹配 | 章节匹配 |
|---|---|---|
| `{table_list}` / `{section_list}` | **必填**，候选表格清单（`序号. 表名`） | **必填**，候选章节清单（`序号. 章节号 标题`） |
| `{query}` | `table_match_keywords` 用顿号连接（无关键词时回退 `table_name_pattern`） | `section_pattern` 的值 |
| `{quantity_hint}` | 按 `table_match_max_results` 生成的数量约束句 | 按 `search_config.max_results`（默认 3）生成 |

候选列表占位符缺失即 **422**（模型看不到候选清单，匹配无从谈起）；`{query}` / `{quantity_hint}` 可选，**章节默认模板未使用 `{quantity_hint}`**，需要约束数量自己加进模板即可。渲染用字符串替换而非 `str.format()`，故模板里的字面 `{` `}` **无需转义**、未知占位符原样保留（这点与 VL 的 `batch_prompt_template` / `locate_prompt_template` 不同）。

```json
{
  "field_id": "total_revenue",
  "field_name": "营业总收入",
  "source_type": "table",
  "table_match_type": "llm",
  "table_match_keywords": ["利润表", "收入"],
  "table_match_max_results": 2,
  "table_match_prompt": "以下是文档中所有表格的名称和序号列表：\n\n{table_list}\n\n请找出与「{query}」最相关的**合并口径**报表，母公司报表一律排除。{quantity_hint}"
}
```

进阶字段的匹配模板里同样可以写 `<field_result>字段ID</field_result>` 引用普通字段的提取值（参见本文内 [§6](#6-进阶字段字段引用--页码联动)），复制 / 导出 / 导入时占位符会随新字段 ID 重映射。

**默认模板从哪拿**：`GET /extraction/match-prompt-defaults` 下发 `section` / `table` / `output_instruction` / `vl_batch` / `vl_locate` 五个默认值（见第 4 节）。前端据此渲染而不硬编码副本，避免副本落后于后端、被用户一保存就固化进库。

**前端在哪配**：字段表单的匹配方式选到「LLM 匹配」后出现 `▸ LLM 匹配高级设置` 折叠按钮，**默认收起**；展开后是提示词文本框 + 「↺ 恢复默认」+ 占位符说明 + 只读展示的系统固定输出段。已存过自定义模板的字段初始即展开。



---

#### 8. 端到端范例（财报场景 · 提取部分）

一次配好一组字段，供后续逻辑分析引用（分析规则的配法参见本文内 第 12.2 节）：

```json
[
  {
    "field_id": "company_name",
    "field_name": "公司名称",
    "source_type": "text",
    "priority": 0,
    "search_type": "context",
    "search_config": { "keywords": ["公司名称", "公司全称"], "context_before": 20, "context_after": 100 },
    "text_extract_prompt": "从以下内容提取公司全称：\n<search_result>公司名称</search_result>\n<search_result>公司全称</search_result>\n\n仅返回公司名称。"
  },
  {
    "field_id": "total_revenue",
    "field_name": "营业总收入",
    "source_type": "table",
    "priority": 1,
    "table_name_pattern": "利润表",
    "table_match_type": "fuzzy",
    "table_extract_prompt": "以下是利润表：\n<search_result>利润表</search_result>\n\n请提取「营业总收入」或「营业收入」金额，仅返回数值（单位：元）。"
  },
  {
    "field_id": "net_profit",
    "field_name": "净利润",
    "source_type": "table",
    "priority": 2,
    "table_name_pattern": "利润表",
    "table_match_type": "fuzzy",
    "table_extract_prompt": "以下是利润表：\n<search_result>利润表</search_result>\n\n请提取「净利润」或「归属于母公司股东的净利润」金额，仅返回数值（单位：元）。"
  },
  {
    "field_id": "total_assets",
    "field_name": "资产总计",
    "source_type": "table",
    "priority": 3,
    "table_name_pattern": "资产负债表",
    "table_match_type": "contains",
    "table_extract_prompt": "以下是资产负债表：\n<search_result>资产负债表</search_result>\n\n请提取「资产总计」或「资产合计」金额，仅返回数值（单位：元）。"
  }
]
```

保存用 `POST /extraction/fields`（逐条 upsert，按 `field_id` 全局唯一）。跨文档类型复用时用 `POST /doctype/{type_id}/copy_from` 复制，占位符会随字段自动重映射；相关接口细节已在本文 `/extraction` 与 `/doctype` 章节列出。

---

#### 9. 常见错误与排查

##### 9.1 保存报 422（占位符）

对照 §7.2。最常见：
- text/table 的提取 prompt 忘了写 `<search_result>标签</search_result>`，或标签与检索配置对不上（如 vector_db 标签没用 `query_text` 的值）。
- vl 的 `vl_extract_prompt` 里没有 `value` / `reason` 字样。
- 自定义 VL 模板缺占位符，或字面花括号没转义成 `{{ }}`（§4.3）。

##### 9.2 提取结果为空

多半是**没检索到内容**，而非 LLM 出错。逐步排查：
1. 用 `POST /extraction/test`（同步）或 `POST /extraction/test/stream`（SSE）传 `file_id` + `field_id`/`config` 调试，看返回的 `search_results` / `llm_input` 是不是空——占位符被替换成了「未找到」就说明检索没命中。
2. table：`table_match_type` 从严放宽（`exact` → `fuzzy` → `contains`），或确认表名是否被 tableing 阶段正确识别（看文件详情的表格页）。
3. text：确认 `keywords` / `section_pattern` / `query_text` 在文档里确实存在；关键词太生僻或写法不一致时多给几个近义词。
4. 文档本身是扫描图、Markdown 里根本没有该文字 → 改用 `vl`。

##### 9.3 VL 抽取报 404 / 无输出

- `uploads/{file_id}.pdf` 不存在：文件未成功上传，或被 `storage` 保留策略按容量/时长清理了。重新上传即可，保留策略见第 12.3 节。
- 相关页没渲进去：检查 `page_range`（vl_model）或加大 `grid_pages` / `key_pages_limit`（vl_locate）、`batch_size`（vl_progressive）。

##### 9.4 抽出来的值不是纯数字 / 后续计算出错

在提取 prompt 里明确要求「仅返回数值，不含单位和千分位」。数值字段被 逻辑分析 的 calc 规则引用时，非数字字符会导致计算失败——排查用 `POST /analysis/test` 看 `input_values`。

> 调试接口的完整出入参已在本文 `/extraction` 章节列出；`search_results` 的形态随 `source_type` / `search_type` 变化，VL 字段的 `llm_output` 即最终 `extracted_value`（直出 JSON）。


### 12.5 `source_refs` 溯源结构与页码定位

> 对应服务版本 0.3.0

`source_refs` 是每条**字段提取结果**与**逻辑分析结果**携带的溯源对象，回答「这个值 / 结论从 PDF 哪里得来」——命中的原始片段、注入 LLM 的全文、命中页码、PDF 高亮框、以及（judge 规则的）联网搜索来源。本节说明 `source_refs` 结构与页码定位；相关接口、回调和示例也已合并到本文档中。

前置阅读：异步回调契约（事件序列、payload 形态）、字段提取配置、逻辑分析配置。

> **核心结论**：`source_refs` 的形状**随字段 `source_type` 三分**（text / table / vl），页码散落在 **4 个位置**、格式**不统一**（string / int[]、单值 / 区间 / null），**没有一个全局字段能一把梭拿页码**。消费方必须**先按 `_vl` / `_tables` 键分流布局，再按布局取对应页码字段**，全程走容错。§9 是页码规则，§10 提供可直接抄的归一函数，§11 是容错清单。
>
> 实现位置：页码映射 `utils/page_mapping.py`（`build_page_mapping` / `lookup_page_num` / `lookup_bboxes`）；抽取组装 `service/extraction_service.py`（`_build_text_source_refs` / `_build_table_source_refs` / `_extract_page_field` / `extract_vl_field`）；分析嵌套 `service/analysis_service.py`（`run_analysis`）。

---

#### 1. source_refs 出现在哪

同一套结构在下列所有场景出现，取页码逻辑通用：

| 场景 | 位置 | 落库 |
|---|---|---|
| 字段提取结果 | `GET /file/{id}/extraction` → `results[i].source_refs` | `extraction_result.source_refs` |
| 逻辑分析结果 | `GET /file/{id}/analysis` → `results[i].source_refs` | `analysis_result.source_refs`（嵌套依赖字段，参见本文内 §8） |
| 回调 `field_done` | `data.source_refs` | extracting 单字段 |
| 回调 `rule_done` | `data.source_refs` | analyzing 单规则（嵌套） |
| 回调 `stage_done` | `data.results[i].source_refs` | extracting / analyzing 阶段汇总 |
| SSE 调试流 | 同上 | `/extraction/test/stream`、`/analysis/test/stream` |

> **抽取失败的字段** `source_refs` 直接是 `null`，没有任何溯源与页码。任何取值前先判空。

---

#### 2. 三种布局与分流判定

`source_refs` 的形状随字段 `source_type` 分三种布局，页码位置各不相同。**必须先分流，再取页码**，判定按**固定顺序**（顺序不能乱）：

```python
def classify_source_refs(refs):
    if refs is None:
        return "none"          # 失败字段 / 无溯源 → 无页码
    if not isinstance(refs, dict):
        return "unknown"       # 理论上不会出现，容错
    if "_vl" in refs:
        return "vl"            # VL 视觉抽取（§6）
    if "_tables" in refs:
        return "table"         # 表格抽取（§5）
    return "text"              # 文本抽取（§4，其余顶层 key 均为检索关键词 label）
```

| 布局 | 判定键 | 顶层 key | 页码在哪 |
|---|---|---|---|
| **text** | 无 `_vl` / `_tables` | 检索关键词 label（page 检索固定 `page_content`）+ `_texts` | `refs[label][i].page_num`（string） |
| **table** | 含 `_tables` | 固定 `_tables` + `_texts` | `refs._tables[i].page_num`（string） |
| **vl** | 含 `_vl` | 固定 `_vl` | `refs._vl.key_pages`（int[] / null） |

---

#### 3. 特殊键（`_` 前缀）一览

顶层所有 `_` 开头的 key 都是**元数据**，遍历 text 命中时应统一跳过（§10 的取页函数用 `label.startswith("_")` 自动跳过）。全清单：

| 键 | 出现布局 | 类型 | 含义 | 含页码 |
|---|---|---|---|:--:|
| `_texts` | text / table | `{label: string}` | 各 label 实际注入占位符的完整文本 | 否 |
| `_tables` | table | `[ref]` | 表格命中 ref 数组（key 固定，**不是**关键词） | 是（`ref.page_num`） |
| `_vl` | vl | `{method,total_pages,key_pages,target_pages,pages_capped,...}` | VL 视觉抽取元信息；`target_pages`=本次纳入视野的页（1-indexed），`pages_capped`=是否因 `max_pages` 截断 | 是（`key_pages` / `target_pages`） |
| `_model_pages` | **已废弃**（仅存量数据） | `int[]` | 模型自报参考页。已提升为顶层 `pages`（参见本文内 §7），API 输出时剔除；仅直接读库可能见到 | 是（模型自报） |
| `_web_search` | analysis judge（可选） | `{query,results,error?}` | 联网搜索溯源（参见本文内 §8） | 否（外部网页） |
| `_resolved_refs` | 进阶字段（`is_advanced=1`，可选） | `{field_id: string}` | 各 `<field_result>` 引用实际填入的值 | 否 |
| `_page_link` | 进阶字段 + `page` 检索联动（可选） | `{source_field,source_pages,pages_from,mode:"range",derived_range,capped}` | 由来源字段可用页码派生的**连续取文区间** | 是（`source_pages` / `derived_range`） |
| `_page_link` | 进阶字段 + VL 联动（可选） | `{source_field,source_pages,pages_from,mode:"discrete",derived_pages,capped}` | 由来源字段可用页码派生的**离散目标页**（VL 按页出图，不看中间页） | 是（`source_pages` / `derived_pages`） |
| `bboxes` | text / table 的**单条 ref 内**（可选，非顶层） | `[{page_num:int,bbox,page_size}]` | PDF 块级高亮框（参见本文内 §9.4） | 是（int，恒单页） |

> `_resolved_refs` / `_page_link` 是**进阶字段**的解析溯源，与该字段自身的检索 ref 并存（进阶字段解析完仍走普通抽取核心，布局判定不变）。`_page_link` 的 `mode` 键区分两种形态：`"range"`（text `page` 检索，连续区间）/ `"discrete"`（VL，只看这几页）；`pages_from` 标记页码来自模型自报（`"model"`）还是程序命中页兜底（`"refs"`）。配置方法见第 12.4 节第 6 小节。

> 存量老数据可能缺 `text` / `_texts` / `bboxes` / `_vl.target_pages` / `_vl.pages_capped` / `_page_link.mode` / `_page_link.pages_from` 等键（老 `page_mapping` 无 bbox，重新解析后才有；无 `mode` 时按 `"range"` 解读）——消费方一律用 `.get()` 容错，缺键不代表整条无效。反过来，存量数据还可能**多**一个 `_model_pages`（已提升为顶层 `pages`，API 输出会剔除）。

---

#### 4. text 类布局

```jsonc
{
  "投资估算":  [ {ref}, {ref} ],   // key = 检索关键词 label，value = 命中数组
  "总投资":    [ {ref} ],
  "_texts": { "投资估算": "...", "总投资": "..." }   // 注入 prompt 的全文，非页码，跳过
}
```

**取页码：**遍历顶层，**跳过所有 `_` 开头的 key**，其余每个 value 是 ref 数组，逐条读 `ref["page_num"]`：

```python
for label, refs_list in source_refs.items():
    if label.startswith("_"):        # 跳过 _texts / _model_pages 等元数据
        continue
    for ref in refs_list:
        page = ref.get("page_num", "")   # "3" / "3-5" / ""
```

##### 4.1 单条 text ref 的完整结构

```jsonc
{
  "type": "context",              // 检索方式：context/section/rule/chunk_db/vector_db/page
  "start_pos": 5120,              // markdown 全文起始位置
  "end_pos": 5680,                // markdown 全文结束位置
  "page_num": "3",                // ★页码（string），来源参见本文内 §4.2
  "chunk_id": "xxx",              // 仅 chunk_db/vector_db 有
  "chunk_index": 7,               // 仅 chunk_db/vector_db 有
  "text": "命中的原始片段...",     // 注入 prompt 的原文
  "bboxes": [                     // 可选，块级框，内部另有 int 页码，参见本文内 §9.4
    {"page_num": 3, "bbox": [88.0, 120.5, 507.3, 680.2], "page_size": [595.0, 842.0]}
  ]
}
```

##### 4.2 text 类 page_num 的两条来源（关键）

| 检索方式（`ref.type`） | `page_num` 来源 | 说明 |
|---|---|---|
| `chunk_db` / `vector_db` | 检索结果自带（取自 `file_chunk.page_num`） | chunking 阶段已算好，直接透传 |
| `context` / `section` / `rule` | `lookup_page_num(page_mapping, start_pos, end_pos)` 实时反查 | 由 `start_pos`/`end_pos` 反查 `page_mapping`，可能跨页 `"3-5"` |
| `page` | 回填字段配置的 `page_range` 原串 | 参见本文内 §4.4，不是算出来的 |

> 对消费方而言**无需关心是哪条来源**——统一读 `ref["page_num"]` 即可。上表只解释「为什么同是 text 类，页码格式可能是单值也可能是区间」。

##### 4.3 `lookup_page_num` 反查算法（背景，了解即可）

`context/section/rule` 的页码由此函数产出（`utils/page_mapping.py`）：

1. `page_mapping` 是 `[{start_pos, end_pos, page_num, bbox?, page_size?}]`，按 `start_pos` 升序。
2. 对 `start_pos` 数组 `bisect_right - 1` 定位命中片段起点所在块 → `page_start`。
3. 同法定位 `end_pos` → `page_end`。
4. `page_start == page_end` → 返回 `"3"`；否则返回 `"3-5"`。
5. `page_mapping` 为空 → 返回 `""`。

##### 4.4 特例：`page` 检索方式

当 `ref.type == "page"`（字段配置用「按页码切片」检索），布局仍是 text，但 label 固定为 `page_content`，`page_num` **直接回填字段配置的 `page_range` 原始字符串**：

```jsonc
{
  "page_content": [
    { "type": "page", "page_range": "5-8", "page_num": "5-8",
      "start_pos": 8000, "end_pos": 24000, "length": 16000,
      "truncated": false, "text": "...整页切片..." }
  ],
  "_texts": { "page_content": "..." }
}
```

- `page_num` 就是配置的 `page_range`，格式取决于用户怎么填（常参见本文内 `"5"` / `"5-8"`；理论上可能有逗号列表，按 §10 的 `parse_page_num_str` 兜底解析）。
- **page 方式的 ref 不含 `bboxes`**（整页切片无块级坐标），只能靠 `page_num` 跳页。

---

#### 5. table 类布局

```jsonc
{
  "_tables": [                    // ★ key 固定是 "_tables"，不是关键词
    {
      "type": "table",
      "table_index": 1,
      "table_name": "投资估算表",
      "start_pos": 5120,
      "end_pos": 6890,
      "page_num": "12",           // ★页码（string），直取 file_table.page_num
      "text": "表格名称: 投资估算表\n<table>...</table>",
      "bboxes": [                 // 可选，整表框，内部 int 页码参见本文内 §9.4
        {"page_num": 12, "bbox": [88.0, 120.5, 507.3, 680.2], "page_size": [595.0, 842.0]}
      ]
    }
  ],
  "_texts": { "投资估算表": "..." }
}
```

**取页码：**读 `source_refs["_tables"]` 数组，逐条 `ref["page_num"]`：

```python
for ref in source_refs.get("_tables", []):
    page = ref.get("page_num", "")   # "12" / ""，来自 file_table.page_num
```

- table 类页码**恒直取** `file_table.page_num`（tableing 阶段落库），不走反查。
- 单表一般单页，但表名与内容跨页时 `file_table.page_num` 也可能是 `""` 或含区间，仍用 `parse_page_num_str` 兜底。

---

#### 6. vl 类布局（无 page_num，读 key_pages）

```jsonc
{
  "_vl": {
    "method": "vl_locate",        // vl_model / vl_progressive / vl_locate
    "total_pages": 48,            // PDF 总页数
    "key_pages": [12, 13, 15],    // ★页码在这里：int 数组（1-indexed）
    "vl_total_tokens": 8421,
    "batches_with_info": null     // 仅 vl_progressive 出现
  }
}
```

**取页码：**读 `source_refs["_vl"]["key_pages"]`，**已是 int 数组、1-indexed、无需解析**：

```python
vl = source_refs["_vl"]
pages = vl.get("key_pages")       # [12,13,15] 或 None
if pages is None:
    # vl_progressive：全文扫描，未定位具体页 → 用 total_pages 兜底或标记「全篇」
    pages = list(range(1, vl["total_pages"] + 1))
```

| `method` | `key_pages` 取值 | 含义 |
|---|---|---|
| `vl_model` | 配置 `page_range` 解析后的页（1-indexed int[]） | 指定页整体喂 VL |
| `vl_locate` | 缩略图定位命中的关键页（去重排序，受 `key_pages_limit` 截断；定位不到回退前 `fallback_pages` 页） | 真正高清提取的页 |
| `vl_progressive` | **`null`** | 逐批扫全篇，不定位具体页；需页码请用 `total_pages` 兜底 |

> ⚠️ vl 类**没有** `page_num` 字段，也**没有** `bboxes`；`source_refs["_vl"]["page_num"]` 会 KeyError。vl 类同样**不产生** `_model_pages`（VL 不走 `{value,reason,pages}` 文本解析）。

---

#### 7. 顶层 `pages` / `source_pages`（页码不再藏在 source_refs 里）

页码是**与 `value` / `reason` 平级的顶层字段**，不在 `source_refs` 内部：

```jsonc
{
  "field_id": "project_name",
  "value": "某某产业园建设项目",
  "reason": "第3页「项目概况」段落明确写明项目全称",
  "pages": [3],                     // ★模型自报参考页（可能为 []）
  "source_pages": [3],              // ★可用页码（键恒存在，值可能为 []）
  "source_refs": { ... }            // 纯溯源，不含页码汇总
}
```

| 字段 | 含义 | 何时为空 |
|---|---|---|
| `pages` | **模型自报**：LLM 输出 `{value, reason, pages}` 里的 `pages`，即「我得出该值实际参考了哪几页」 | 模型未返回 / 解析失败 / `use_llm=0` / VL 类 |
| `source_pages` | **可用页码**：`pages` 非空时等于它，否则回落到程序从 `source_refs` 算出的命中页 | 两者皆无（失败字段 / 检索无命中 / `vl_progressive`） |

**关键约定：**

- **两者都是已展开的 `int[]`**（1-indexed、去重升序）。**不会出现 `"12-15"` 这类区间串** —— 后端 `parse_page_num_str` 已把 `ref.page_num` 的区间展开为逐页。
- **`source_pages` 保证「键一定存在」，不保证「非空」**。消费方可以少写遍历逻辑，但仍要判空。
- 区间展开有上限：单个区间串最多取前 **5** 页（`_MAX_SOURCE_PAGE_SPAN`），`"1-200"` → `[1,2,3,4,5]`。`bboxes[].page_num` 与 `page_nums` 是真实逐页数据，**不受此限**。
- 要区分「模型真的说了参考哪页」与「程序推出来的页」，读 `pages`；只想跳转 / 展示，读 `source_pages`。
- 前端提取卡片把两者分别渲染为「模型自报页码」「实际参考页码」两行，PDF 定位（📍）直接跳 `source_pages` 首项。

**`source_pages` 的计算规则**（后端 `collect_ref_pages`，每条 ref 按精度降序取第一个可用来源）：

1. `ref["bboxes"][i]["page_num"]` —— 已是 int、恒单页，最精确
2. `ref["page_nums"]` —— 跨页命中时逐页算好的 int 数组
3. `ref["page_num"]` —— int 或 str，str 走区间展开

vl 类另读 `_vl.key_pages`。`_` 前缀的元数据键一律跳过。

> **存量数据**：本次改动前落库的结果，模型自报页码存在 `source_refs["_model_pages"]` 里。后端读取时会自动兼容（`read_model_pages`）并提升到顶层 `pages`，**对外输出的 `source_refs` 已剔除该键**。直接读库的消费方仍可能看到它。

---

#### 8. analyzing 规则的 source_refs（嵌套依赖字段）

分析规则（`judge` / `calc`）本身**不产生新页码**，它的溯源是**把每个依赖字段的抽取 source_refs 原样嵌进来**（`analysis_service.py:run_analysis`）：

```jsonc
{
  "uuid-field-1": { /* 字段1 的完整抽取 source_refs（text/table/vl 三种布局之一）*/ },
  "uuid-field-2": { /* 字段2 的完整抽取 source_refs */ },
  "_web_search": {                                  // 可选：judge 联网搜索溯源，无 PDF 页码
    "query": "拼接后的搜索词",
    "results": [ {"name": "...", "url": "...", "siteName": "...",
                  "datePublished": "...", "summary": "..."} ],
    "error": "搜索失败提示"                          // 仅失败时出现
  }
}
```

**取页码：**遍历 `source_refs`，跳过 `_web_search`，剩下每个 value 就是一个字段的抽取 source_refs，**递归套用 §2–§7 的逻辑**（复用 §10 的 `pages_of_extraction`）。

- `_web_search` 溯源里**没有 PDF 页码**（是外部网页），只有 `url` / `siteName` 等。
- 依赖字段若抽取失败（source_refs 为 null），它不会进规则的 source_refs → 天然跳过。
- `input_values`（依赖字段取值）与 `source_refs` 是两个字段，前者是值、后者是溯源。

---

#### 9. 页码的类型与格式（完整说明规则）

##### 9.1 页码出现的全部位置（总览）

| # | 事件 / 接口 | 路径 | 类型 | 说明 |
|---|---|---|---|---|
| 1 | `stage_done`(tableing) / `GET /file/{id}/tables` | `tables[i].page_num` | **string** | 表格所在页，直取 |
| 2 | `stage_done`(chunking) / `GET /file/{id}/chunks` | `chunks[i].page_num` | **string** | 分块所在页，直取 |
| 3 | `field_done` / extracting 结果 | `source_refs` 内（§4/§5/§6） | **string / int[]** | 逐条 ref 的溯源页码，随布局分 3 种 |
| 3′ | `field_done` / extracting 结果 | **顶层 `source_pages`**（§7） | **int[]** | ★**首选**：已归一展开的可用页码，键恒存在 |
| 3″ | `field_done` / extracting 结果 | **顶层 `pages`**（§7） | **int[]** | 模型自报参考页（text/table，可能为 `[]`） |
| 4 | `rule_done` / analyzing 结果 | `source_refs[field_id]` 内（§8） | 同 #3 | 嵌套依赖字段的抽取 source_refs |

> **位置 3′ 是取页码的首选** —— 后端已做完区间展开、去重排序、模型页优先与命中页兜底，消费方直接读即可，不必自己遍历 `source_refs`（位置 3 只在需要逐条溯源时才用）。位置 4 的分析结果**没有**顶层页码字段，仍需按 §10 自行汇总。

> `parsing` 阶段 `stage_done.data.page_mapping` 是**原始映射表**（文本位置 → 页码），不是某条数据的页码，通常无需直读；它是位置 1/3/4 反查页码的底层数据。

##### 9.2 string 页码的取值与统一约定

位置 1 / 2 / 3(text,table) / 4 的 `page_num` 统一是**字符串**：

| 取值样例 | 含义 | 处理 |
|---|---|---|
| `"12"` | 单页 | `int("12")` |
| `"12-15"` | 跨页区间（含首尾） | 按 `-` split，展开成 `[12,13,14,15]` |
| `"5-8"` | page 检索回填的 `page_range` 原串 | 同上；也可能是 `"1,3,5"` 等自定义格式 |
| `""` | 取不到（无 `file_content` / `page_mapping` 为空 / 该 chunk 无页码） | 视为「未知页」，**不可强转 int** |

**统一约定：**

- 所有页码 **1-indexed**（第 1 页 = `1`，解析时 `page_idx + 1`），直接对应 PDF 第几页，无需 +1/-1。
- string 页码**禁止直接 `int()`**：先判空、再判 `-`，统一走 §10 的 `parse_page_num_str`。
- 需要「命中页集合」时归一成 `List[int]`。
- 位置 3(vl) 没有 `page_num`，页码在 `_vl.key_pages`，是 **int 数组（1-indexed）或 null**（§6）。

##### 9.3 直取页码：tableing / chunking

这两处最简单，`page_num` 就在数组元素上，直接读（均为 string，可能 `""` 或区间，仍用 `parse_page_num_str` 归一）：

```python
for t in data["tables"]:   # tableing：来自 file_table.page_num
    page = t["page_num"]        # "12" / "12-15" / ""
for c in data["chunks"]:   # chunking：来自 file_chunk.page_num
    page = c["page_num"]        # "1" / ""
```

##### 9.4 两个 page_num 别搞混：顶层 string vs bboxes 内 int

text / table 类 ref 里**有两个都叫 `page_num` 的字段，类型和用途不同**：

| 位置 | 类型 | 值样例 | 用途 |
|---|---|---|---|
| `ref.page_num` | **string** | `"3"` / `"3-5"` / `""` | 展示 / 跳页，**可能是区间** |
| `ref.bboxes[i].page_num` | **int** | `3` | PDF 高亮画框，**恒单页整数** |

- 只要「命中在第几页」→ 读 **`ref.page_num`**（string，注意区间）。
- 要「在某页画高亮框」→ 读 **`ref.bboxes[i].page_num`**（int）配合 `bbox` + `page_size`。
- `bboxes` 是**可选键**：存量老数据 / page 检索 / vl 类都没有它，读之前判存在。
- 同一条 ref 的 `bboxes` 可能横跨多页（跨页命中），每个元素各带自己的 int `page_num`。

---

#### 10. 参考实现（可直接抄）

> **抽取结果（`field_done` / `GET /file/{id}/extraction`）不需要这套函数** —— 直接读顶层 `source_pages`（§7），后端已算好。下面的实现是给 **tableing / chunking 的 `page_num`** 和 **analyzing 规则的嵌套 source_refs**（位置 1/2/4）用的，它们没有顶层页码字段。

一套把「任意 source_refs」归一成 `List[int]` 页码的函数：

```python
from typing import Any, List, Optional


def parse_page_num_str(s: Optional[str]) -> List[int]:
    """把 string 页码归一成 int 列表。
    "12" -> [12]；"12-15" -> [12,13,14,15]；"1,3,5" -> [1,3,5]；"" / None -> []。

    注：后端同名函数对单个区间串有 5 页上限（"1-200" -> [1,2,3,4,5]），
    这里的参考实现不设上限——按你的场景自行取舍。
    """
    if not s or not isinstance(s, str):
        return []
    pages: List[int] = []
    for part in s.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                if a <= b:
                    pages.extend(range(a, b + 1))
            except ValueError:
                continue
        else:
            try:
                pages.append(int(part))
            except ValueError:
                continue
    return sorted(set(pages))


def model_pages_of_extraction(source_refs: Any) -> List[int]:
    """【已过时】取存量数据里的 source_refs._model_pages。

    新数据请直接读顶层 pages 字段（§7）——API 输出的 source_refs 已剔除该键。
    本函数只在你直接读库、面对存量 JSON 时才需要。
    """
    if not isinstance(source_refs, dict):
        return []
    mp = source_refs.get("_model_pages")
    if not isinstance(mp, list):
        return []
    return sorted({int(p) for p in mp if isinstance(p, int) or str(p).isdigit()})


def pages_of_extraction(source_refs: Any) -> List[int]:
    """从「一个抽取字段」的 source_refs 里取所有命中页（1-indexed，去重排序）。
    覆盖 text / table / vl / page / 失败 全部形态。
    注：这是**程序算出的命中页**；模型自报页请另用 model_pages_of_extraction。
    """
    if not isinstance(source_refs, dict):
        return []                     # None（失败字段）或异常形态

    # —— vl 类 ——
    if "_vl" in source_refs:
        vl = source_refs["_vl"] or {}
        kp = vl.get("key_pages")
        if isinstance(kp, list):
            return sorted({int(p) for p in kp})
        # vl_progressive: key_pages=null，全篇扫描，用 total_pages 兜底
        total = vl.get("total_pages")
        return list(range(1, total + 1)) if isinstance(total, int) else []

    pages: List[int] = []

    # —— table 类 ——
    if "_tables" in source_refs:
        for ref in source_refs.get("_tables") or []:
            pages += parse_page_num_str(ref.get("page_num"))
        return sorted(set(pages))

    # —— text 类（含 page 检索）——
    for label, refs_list in source_refs.items():
        if label.startswith("_"):     # 跳过 _texts / _model_pages 等元数据键
            continue
        for ref in refs_list or []:
            pages += parse_page_num_str(ref.get("page_num"))
    return sorted(set(pages))


def pages_of_rule(rule_source_refs: Any) -> List[int]:
    """从「一条分析规则」的 source_refs 里取所有命中页（下钻依赖字段）。"""
    if not isinstance(rule_source_refs, dict):
        return []
    pages: List[int] = []
    for key, field_refs in rule_source_refs.items():
        if key == "_web_search":      # 联网搜索无 PDF 页码
            continue
        pages += pages_of_extraction(field_refs)
    return sorted(set(pages))
```

回调 / 结果分发时这样用：

```python
event = payload.get("event")
data  = payload.get("data") or {}

if event == "field_done":                       # extracting 单字段
    pages = data.get("source_pages") or []      # ★首选：后端已归一好
    model_pages = data.get("pages") or []       # 可选：模型自报页

elif event == "rule_done":                      # analyzing 单规则
    pages = pages_of_rule(data.get("source_refs"))

elif event == "stage_done" and payload["status"] == "tableing":
    for t in data["tables"]:
        pages = parse_page_num_str(t["page_num"])

elif event == "stage_done" and payload["status"] == "chunking":
    for c in data["chunks"]:
        pages = parse_page_num_str(c["page_num"])
```

---

#### 11. 容错清单（务必逐条落地）

0. **抽取结果优先读顶层 `source_pages`**：它恒存在、是已展开的 `int[]`，下面 3~5 条的解析坑后端都替你踩过了。只有 tableing / chunking / analyzing（无顶层页码字段）才需要自己解析。
1. **先判 `source_refs is None`**：失败字段 / 规则的 `source_refs` 是 `null`，没有任何页码。
2. **先分流再取字段**：`_vl` 类没有 `page_num`，text/table 类没有 `key_pages`；用错字段会 KeyError。
3. **string 页码禁止直接 `int()`**：可能是 `"3-5"` 区间或 `""` 空串，统一走 `parse_page_num_str`。
4. **区间要展开**：`"3-5"` 代表 3、4、5 三页，不是「第 3 到 5 号」的两个数。
5. **跳过 `_` 开头的 key**：text 类的 `_texts`、进阶字段的 `_resolved_refs` / `_page_link` / `_empty_refs`、分析类的 `_web_search` 都不是命中页数据。
6. **`bboxes` 是可选键**：存量老数据（老 `page_mapping` 无 bbox）、page 检索回退路径、vl 类都没有；`ref.get("bboxes")` 判空后再用。
7. **vl_progressive 的 `key_pages=null`**：不是错误，是「全篇扫描无具体定位页」，此时 `source_pages` 会是 `[]`，按 `total_pages` 兜底或标记「全篇」。
8. **页码是 1-indexed**：直接对应 PDF 第几页，无需 +1/-1。
9. **`ref.page_num` 有两种类型**：`page` 检索走 `page_mapping` 主路径时是 **int**（逐页 ref），`context/section/rule` 反查时是 **string**（可能是 `"3-5"`）；`bboxes[i].page_num` 恒为 int 单页。展示 / 跳页用前者，画框用后者。
10. **`source_pages` 键恒存在但可能为空数组**：失败字段、检索无命中、`vl_progressive` 三种情况下是 `[]`，仍要判空。
11. **`pages`（模型自报）与 `source_pages`（可用页码）别混用**：要区分「模型真的说了参考哪页」用 `pages`；只想跳转 / 展示用 `source_pages`。`pages` 在 VL 类 / `use_llm=0` / 模型未返回时恒为 `[]`。
12. **存量数据的 `source_refs` 可能多一个 `_model_pages`**：API 输出已剔除（值提升到顶层 `pages`），直接读库才会见到。


### 12.6 库表结构

> 对应服务版本 0.3.0

数据库表结构、JSON 子结构（`page_mapping` / `search_config` / `vl_config` / `source_refs` / `web_search`）、Milvus 集合与建表 SQL 的**完整说明**。本文接口章节中的响应字段与 `source_refs` 结构均已在本节展开。

> **数据库**: MySQL 8.0+
> **字符集**: utf8mb4
> **排序规则**: utf8mb4_unicode_ci

---

#### 目录

1. [表关系总览](#1-表关系总览)
2. [文件处理相关表](#2-文件处理相关表)
   - [2.1 doc_type - 文档类型表](#21-doc_type---文档类型表)
   - [2.2 files - 文件主表](#22-files---文件主表)
   - [2.3 file_content - 文件内容表](#23-file_content---文件内容表)
   - [2.4 file_table - 文件表格表](#24-file_table---文件表格表)
   - [2.5 file_chunk - 文件分块表](#25-file_chunk---文件分块表)
3. [配置相关表](#3-配置相关表)
   - [3.1 extraction_field - 字段提取配置表](#31-extraction_field---字段提取配置表)
   - [3.2 analysis_rule - 逻辑分析规则表](#32-analysis_rule---逻辑分析规则表)
4. [结果相关表](#4-结果相关表)
   - [4.1 extraction_result - 提取结果表](#41-extraction_result---提取结果表)
   - [4.2 analysis_result - 分析结果表](#42-analysis_result---分析结果表)
5. [向量数据库](#5-向量数据库-milvus)
6. [建表 SQL](#6-建表-sql)

---

#### 1. 表关系总览

##### 1.1 ER 图

```
┌─────────────────┐
│    doc_type     │ 1:N（type_id 作用域隔离）
│  (文档类型表)    │ ──────────┬──────────────────┬─────────────────┐
└─────────────────┘           ▼                  ▼                 ▼
                     ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
                     │     files       │ │extraction_field │ │  analysis_rule  │
                     │   (文件主表)     │ │  (字段提取配置)  │ │  (逻辑分析配置)  │
                     └────────┬────────┘ └────────┬────────┘ └────────┬────────┘
                              │ 1:1               │ 根据配置提取       │ 根据配置分析
                              ▼                   ▼                   ▼
                     ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
                     │  file_content   │ │extraction_result│ │ analysis_result │
                     │ (全文/页码映射)  │ │   (提取结果)     │◀┤   (分析结果)     │
                     └────────┬────────┘ └─────────────────┘ └─────────────────┘
                              │ 1:N                 ▲ 被 analysis 引用
                  ┌───────────┴───────────┐
                  ▼                       ▼
         ┌─────────────────┐     ┌─────────────────┐
         │   file_table    │     │   file_chunk    │
         │  (解析出的表格)  │     │  (文本分块)      │
         └─────────────────┘     └─────────────────┘
```

##### 1.2 表清单

| 序号 | 表名 | 说明 | 主键 |
|------|------|------|------|
| 1 | `doc_type` | 文档类型定义（配置作用域 + 模板/血缘标记） | `type_id` |
| 2 | `files` | 文件主表，记录文件基本信息和处理状态 | `file_id` |
| 3 | `file_content` | 文件解析后的全文内容 + middle_json + 页码映射 | `file_id` |
| 4 | `file_table` | 文件中提取的表格数据（含原文位置/页码） | `file_id` + `table_index` |
| 5 | `file_chunk` | 文件文本分块（含原文位置/页码） | `file_id` + `chunk_id` |
| 6 | `extraction_field` | 字段提取配置（按 type_id 隔离） | `field_id` |
| 7 | `analysis_rule` | 逻辑分析规则配置（按 type_id 隔离） | `rule_id` |
| 8 | `extraction_result` | 字段提取结果 | `file_id` + `field_id` |
| 9 | `analysis_result` | 逻辑分析结果 | `file_id` + `rule_id` |

> 表结构来自 `model/tables.py` ORM 定义；启动时 `service/init_service.py:run_init` 会自动建库建表并对旧库做增量 ALTER 迁移。本文已内联当前表结构说明。

---

#### 2. 文件处理相关表

##### 2.1 doc_type - 文档类型表

文档类型定义。每个文件、字段配置、规则配置都绑定一个 `type_id`，实现多类型配置隔离（配置不跨类型共享，共享靠 `POST /doctype/{id}/copy_from` 显式复制）。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `type_id` | VARCHAR(64) | PK, NOT NULL | - | 类型唯一标识（默认类型固定为 `default`） |
| `type_name` | VARCHAR(200) | NOT NULL | - | 类型显示名称 |
| `description` | TEXT | NULLABLE | NULL | 类型描述 |
| `max_parse_pages` | INT | NULLABLE | NULL | 解析页数上限（NULL=不限制） |
| `enable_embedding` | TINYINT | NOT NULL | 1 | 是否执行 embedding 阶段（0=跳过向量化） |
| `is_default` | TINYINT | - | 0 | 默认类型标记（默认类型不可删除） |
| `is_template` | TINYINT | - | 0 | 模板标记（`POST /doctype/{id}/promote|demote` 切换；顶部选择器只展示模板+默认+当前） |
| `parent_type_id` | VARCHAR(64) | NULLABLE | NULL | 复制来源类型（`copy_from`/`import` 自动记录的血缘） |
| `enabled` | TINYINT | - | 1 | 是否启用 |
| `created_at` | DATETIME | - | CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | DATETIME | - | CURRENT_TIMESTAMP | 更新时间（自动更新） |

###### 说明

- 与 `files` / `extraction_field` / `analysis_rule` 均为 **1:N**（通过各表 `type_id` 列关联，无外键约束）
- 删除非默认类型且其下有文件/配置时需 `force=true`（级联清理文件内容 + Milvus 向量 + 配置）

---

##### 2.2 files - 文件主表

记录上传文件的基本信息和处理进度状态。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件唯一标识（`SHA256(type_id|file_name|纳秒时间戳|随机盐)[:32]`，每次上传必产新 ID，同名重传不去重） |
| `type_id` | VARCHAR(64) | NOT NULL | 'default' | 所属文档类型（决定字段/规则配置作用域） |
| `file_name` | VARCHAR(512) | NOT NULL | - | 原始文件名 |
| `file_size` | BIGINT | - | 0 | 文件大小（字节） |
| `create_time` | DATETIME | - | CURRENT_TIMESTAMP | 文件上传时间 |
| `start_parsing_time` | DATETIME | NULLABLE | NULL | 开始解析时间 |
| `end_parsing_time` | DATETIME | NULLABLE | NULL | 解析完成时间 |
| `start_tableing_time` | DATETIME | NULLABLE | NULL | 开始表格名识别时间 |
| `end_tableing_time` | DATETIME | NULLABLE | NULL | 表格名识别完成时间 |
| `start_chunking_time` | DATETIME | NULLABLE | NULL | 开始分块时间 |
| `end_chunking_time` | DATETIME | NULLABLE | NULL | 分块完成时间 |
| `start_embedding_time` | DATETIME | NULLABLE | NULL | 开始向量化时间 |
| `end_embedding_time` | DATETIME | NULLABLE | NULL | 向量化完成时间 |
| `start_extracting_time` | DATETIME | NULLABLE | NULL | 开始字段提取时间 |
| `end_extracting_time` | DATETIME | NULLABLE | NULL | 字段提取完成时间 |
| `start_analyzing_time` | DATETIME | NULLABLE | NULL | 开始逻辑分析时间 |
| `end_analyzing_time` | DATETIME | NULLABLE | NULL | 逻辑分析完成时间 |
| `progress` | VARCHAR(32) | - | 'parsing' | 当前处理进度状态 |
| `error` | TEXT | NULLABLE | NULL | 错误信息（失败时记录） |
| `updated_at` | DATETIME | - | CURRENT_TIMESTAMP | 最后更新时间（自动更新） |

###### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_files_type_id` | `type_id` | 普通索引 |

###### progress 字段枚举值

| 值 | 说明 | 下一状态 |
|----|------|----------|
| `parsing` | 正在解析文件 | `tableing` / `parsing_failed` |
| `parsing_failed` | 文件解析失败 | - |
| `tableing` | 正在识别表格名称（LLM） | `chunking` / `tableing_failed` |
| `tableing_failed` | 表格名称识别失败 | - |
| `chunking` | 正在分块 | `embedding` / `chunking_failed` |
| `chunking_failed` | 分块失败 | - |
| `embedding` | 正在向量化 | `extracting` / `embedding_failed` |
| `embedding_failed` | 向量化失败 | - |
| `extracting` | 正在提取字段 | `analyzing` / `extracting_failed` |
| `extracting_failed` | 字段提取失败 | - |
| `analyzing` | 正在逻辑分析 | `complete` / `analyzing_failed` |
| `analyzing_failed` | 逻辑分析失败 | - |
| `complete` | 处理完成 | - |

###### 状态流转图

```
parsing ──▶ tableing ──▶ chunking ──▶ embedding ──▶ extracting ──▶ analyzing ──▶ complete
    │           │            │            │              │              │
    ▼           ▼            ▼            ▼              ▼              ▼
parsing_   tableing_    chunking_   embedding_    extracting_    analyzing_
 failed     failed       failed       failed        failed         failed
```

###### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "type_id": "default",
  "file_name": "2024年度财务报告.pdf",
  "file_size": 2048576,
  "create_time": "2025-01-15 10:30:00",
  "start_parsing_time": "2025-01-15 10:30:01",
  "end_parsing_time": "2025-01-15 10:31:15",
  "start_tableing_time": "2025-01-15 10:31:15",
  "end_tableing_time": "2025-01-15 10:31:18",
  "start_chunking_time": "2025-01-15 10:31:18",
  "end_chunking_time": "2025-01-15 10:31:20",
  "start_embedding_time": "2025-01-15 10:31:20",
  "end_embedding_time": "2025-01-15 10:32:00",
  "start_extracting_time": "2025-01-15 10:32:00",
  "end_extracting_time": "2025-01-15 10:33:00",
  "start_analyzing_time": "2025-01-15 10:33:00",
  "end_analyzing_time": "2025-01-15 10:33:30",
  "progress": "complete",
  "error": null,
  "updated_at": "2025-01-15 10:33:30"
}
```

---

##### 2.3 file_content - 文件内容表

存储文件解析后的完整 Markdown 格式文本内容、MinerU 原始布局 JSON 以及位置→页码/bbox 映射。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID（关联 files 表） |
| `file_content` | LONGTEXT | NOT NULL | - | 解析后的全文内容（Markdown 格式） |
| `middle_json` | LONGTEXT | NULLABLE | NULL | MinerU 原始布局 JSON（`pdf_info[].para_blocks` 结构，page_mapping 的构建来源） |
| `page_mapping` | JSON | NULLABLE | NULL | Markdown 位置 → PDF 页码/bbox 映射（parsing 阶段由 `utils/page_mapping.py:build_page_mapping` 构建） |

###### 说明

- 与 `files` 表是 **1:1** 关系
- 内容为 Markdown 格式，章节使用 `# 编号 标题` 格式
- 存储上限约 4GB（LONGTEXT）

###### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "file_content": "# 1 公司简介\n\n某某科技有限公司成立于2010年...\n\n# 2 财务报表\n\n## 2.1 资产负债表\n\n..."
}
```

###### page_mapping 结构

按 `start_pos` 排序的数组，每项把 markdown 中一个锚点位置映射到 PDF 页码与块级框：

```json
[
  {"start_pos": 0,    "end_pos": 50,   "page_num": 1, "bbox": [88.0, 72.5, 507.3, 96.1],   "page_size": [595.0, 842.0]},
  {"start_pos": 1208, "end_pos": 1214, "page_num": 2, "bbox": [90.2, 110.0, 505.8, 396.4], "page_size": [595.0, 842.0]}
]
```

- **构建方式**（parsing 阶段，`build_page_mapping(md_content, middle_json)`）有两种锚点：
  - **文本块**：取块文本前缀（依次尝试 50/30/20/10 字符）在 markdown 中前向扫描定位，`bbox` 为该段落块的框；
  - **表格块**（middle_json 中 `type == "table"`，无 lines/spans 文本）：改在 markdown 中前向找 `<table` 字面量定位，`bbox` 为整表框；找不到 `<table` 或块无 bbox 时不产锚点。
- **坐标系**：`page_num` 为 1-indexed 页码；`bbox = [x0, y0, x1, y1]` 为左上原点坐标，与 `page_size = [w, h]` 同一单位（MinerU 输出）；前端按 `canvas尺寸 / page_size` 线性缩放画框。
- **容错**：`bbox` / `page_size` 在 middle_json 缺失时不带该键（存量老数据全部不带 bbox）；`middle_json` 为空时 `page_mapping` 为 `[]`，下游页码/高亮功能降级。
- page_mapping 是表格页码、分块页码、抽取结果 `source_refs.bboxes` 高亮的**唯一来源**。查询入口：`lookup_page_num(mapping, start, end)` 返回页码字符串（`"3"` 或跨页 `"3-5"`）；`lookup_bboxes(mapping, start, end)` 返回命中范围内 `[{page_num, bbox, page_size}]`（无 bbox 条目跳过）。

---

##### 2.4 file_table - 文件表格表

存储从文件中提取的表格数据（tableing 阶段写入，表名由 LLM 从表格前文识别）。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID |
| `table_index` | INT | PK, NOT NULL | - | 表格序号（从 0 开始） |
| `total_table` | INT | - | 0 | 该文件的表格总数 |
| `table_name` | VARCHAR(500) | - | '' | 表格名称（LLM 从前文识别，截断至 30 字符；失败回退表格前最后一行） |
| `table_content` | LONGTEXT | NOT NULL | - | 表格内容（`<table>...</table>` HTML，MinerU 原样输出） |
| `start_pos` | INT | - | 0 | 表格 HTML 在 markdown 全文中的起始位置 |
| `end_pos` | INT | - | 0 | 表格 HTML 在 markdown 全文中的结束位置 |
| `page_num` | VARCHAR(20) | NULLABLE | '' | 所在 PDF 页码（`"3"` 或跨页 `"3-5"`，经 page_mapping 查得） |

###### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_file_table_file_id` | `file_id` | 普通索引 |

###### 说明

- 与 `files` 表是 **1:N** 关系（一个文件可有多个表格）
- 复合主键：`file_id` + `table_index`
- `table_content` 为 **HTML `<table>` 片段**（非 Markdown 表格）；`start_pos`/`end_pos` 是 table 类字段抽取时 `source_refs.bboxes` 定位的坐标来源

###### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "table_index": 0,
  "total_table": 3,
  "table_name": "合并资产负债表",
  "table_content": "<table><tr><td>项目</td><td>期末余额</td><td>期初余额</td></tr><tr><td>货币资金</td><td>1,234,567</td><td>987,654</td></tr></table>",
  "start_pos": 5120,
  "end_pos": 6890,
  "page_num": "12"
}
```

---

##### 2.5 file_chunk - 文件分块表

存储文件文本分块，用于检索和向量化。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID |
| `chunk_id` | VARCHAR(64) | PK, NOT NULL | - | 分块唯一 ID（`SHA256(file_id + chunk_index)[:32]`，确定性可重算） |
| `chunk_index` | INT | - | 0 | 分块序号（从 0 开始） |
| `total_chunks` | INT | - | 0 | 该文件的分块总数 |
| `chunk_content` | TEXT | NOT NULL | - | 分块文本内容 |
| `start_pos` | INT | - | 0 | 在 markdown 全文中的起始位置 |
| `end_pos` | INT | - | 0 | 在 markdown 全文中的结束位置 |
| `page_num` | VARCHAR(20) | NULLABLE | '' | 所在 PDF 页码（`"3"` 或跨页 `"3-5"`） |

###### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_file_chunk_file_id` | `file_id` | 普通索引 |

###### 说明

- 与 `files` 表是 **1:N** 关系
- 复合主键：`file_id` + `chunk_id`
- 分块参数由 `configs/config.yaml` 的 `chunking` 节决定（默认 `chunk_size: 512`、`chunk_overlap: 50`，递归分隔符 `["\n\n", "\n", "。", " "]`）
- **表格作为独立 chunk 保留**（不参与递归切分），`chunk_content` 含 `table_name\n<table>...` 前缀；超过 8192 字符的超长表格按 `</tr>` / `</td>` / `\n` 边界拆分
- `start_pos`/`end_pos` 是 `chunk_db`/`vector_db` 检索结果做 bbox 定位的坐标来源

###### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "chunk_id": "9f86d081884c7d659a2feaa0c55ad015",
  "chunk_index": 0,
  "total_chunks": 15,
  "chunk_content": "# 1 公司简介\n\n某某科技有限公司（以下简称"公司"）成立于2010年，是一家专注于人工智能技术研发的高新技术企业...",
  "start_pos": 0,
  "end_pos": 512,
  "page_num": "1"
}
```

---

#### 3. 配置相关表

##### 3.1 extraction_field - 字段提取配置表

定义需要从文档中提取的字段及其提取方式。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `field_id` | VARCHAR(100) | PK, NOT NULL | - | 字段唯一标识 |
| `type_id` | VARCHAR(64) | NOT NULL | 'default' | 所属文档类型（抽取时按 `file.type_id` 过滤） |
| `field_name` | VARCHAR(200) | NOT NULL | - | 字段显示名称 |
| `source_type` | ENUM | NOT NULL | - | 数据源类型 |
| `enabled` | TINYINT | - | 1 | 是否启用（1=启用, 0=禁用） |
| `priority` | INT | - | 0 | 执行优先级（越小越优先） |
| `use_llm` | TINYINT | NOT NULL | 1 | 是否走 LLM 二次抽取（0=直接返回检索原文；仅 text / table 生效，vl 恒需模型） |
| `is_advanced` | TINYINT | NOT NULL | 0 | 是否进阶字段（1=依赖前序普通字段，在普通字段全部抽完后执行；NULL/0=普通字段） |
| `depend_fields` | JSON | NULLABLE | NULL | 进阶字段引用的普通字段 ID 数组（保存时由服务端扫描配置算出，非调用方指定） |
| `created_at` | DATETIME | - | CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | DATETIME | - | CURRENT_TIMESTAMP | 更新时间（自动更新） |
| `table_name_pattern` | VARCHAR(500) | NULLABLE | NULL | 【表格类】表格名称匹配模式（兼容旧配置；也作为占位符 label） |
| `table_match_type` | ENUM | NULLABLE | NULL | 【表格类】表格匹配方式 |
| `table_match_keywords` | JSON | NULLABLE | NULL | 【表格类】匹配关键词数组（优先于 table_name_pattern） |
| `table_match_max_results` | INT | NULLABLE | NULL | 【表格类】最多匹配的表格数（0/NULL=不限） |
| `table_system_prompt` | TEXT | NULLABLE | NULL | 【表格类】LLM system 提示词（可选） |
| `table_extract_prompt` | TEXT | NULLABLE | NULL | 【表格类】LLM 提取提示词 |
| `search_type` | ENUM | NULLABLE | NULL | 【文本类】检索方式 |
| `search_config` | JSON | NULLABLE | NULL | 【文本类】检索配置参数 |
| `text_system_prompt` | TEXT | NULLABLE | NULL | 【文本类】LLM system 提示词（可选） |
| `text_extract_prompt` | TEXT | NULLABLE | NULL | 【文本类】LLM 提取提示词 |
| `vl_method` | ENUM | NULLABLE | NULL | 【VL 类】VL 抽取方法 |
| `vl_config` | JSON | NULLABLE | NULL | 【VL 类】VL 方法参数（按 vl_method 不同） |
| `vl_system_prompt` | TEXT | NULLABLE | NULL | 【VL 类】VL 系统提示词（可选） |
| `vl_extract_prompt` | TEXT | NULLABLE | NULL | 【VL 类】VL 最终提取提示词（vl 类必填，含 `value`/`reason` 关键字） |

###### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_extraction_field_type_id` | `type_id` | 普通索引 |

> 上述 4 列与 `source_type` ENUM 加入 `'vl'` 的扩展由 `service/init_service.py` 启动时自动 ALTER TABLE 迁移（ADD COLUMN + MODIFY COLUMN），不需要手工迁移脚本。

###### 枚举值说明

**source_type（数据源类型）**

| 值 | 说明 |
|----|------|
| `table` | 从 file_table 表中匹配表格后送 LLM 抽取 |
| `text` | 从 file_content / file_chunk / Milvus 检索后送 LLM 抽取 |
| `vl` | 直接读 `uploads/{file_id}.pdf`，由 VL 视觉模型直出 `{value, reason}` JSON，**不**走文本 LLM 二次抽取 |

**table_match_type（表格匹配方式）**

| 值 | 说明 |
|----|------|
| `exact` | 精确匹配 |
| `fuzzy` | 模糊匹配（相似度≥80%） |
| `contains` | 包含匹配 |
| `llm` | LLM 语义匹配 |

**search_type（文本检索方式）**

| 值 | 说明 |
|----|------|
| `context` | 上下文检索（关键词 + 前后文窗口） |
| `section` | 章节检索（按 `# 编号 标题` 匹配） |
| `rule` | 规则检索（关键词 + 停止词边界） |
| `chunk_db` | 数据库分块检索（file_chunk LIKE） |
| `vector_db` | 向量数据库检索（Milvus 语义） |
| `page` | 按页码直接切 markdown 喂 LLM（占位符固定 `page_content`） |

**vl_method（VL 抽取方法）**

| 值 | 说明 |
|----|------|
| `vl_model` | 全量模式：把 `page_range` 指定页一次性塞 VL，1 次调用产 JSON |
| `vl_progressive` | 逐批扫描：按 `batch_size` 分批，让 VL 自判相关性 + 摘要，最后一次文本聚合 |
| `vl_locate` | 两轮：第一轮缩略图网格并行让 VL 定位关键页，第二轮把关键页高清重渲染塞给 VL |

###### search_config JSON 结构

根据 `search_type` 不同，结构有所差异：

**context 模式**
```json
{
  "keywords": ["关键词1", "关键词2"],
  "context_before": 200,
  "context_after": 200,
  "max_results": 5,
  "sort_order": "asc"
}
```

**section 模式**
```json
{
  "section_pattern": "章节标题",
  "section_match_type": "contains",
  "threshold": 0.8,
  "max_results": 3
}
```

**rule 模式**
```json
{
  "keywords": ["关键词"],
  "stop_words": ["\n", "。"],
  "direction": "forward",
  "min_length": 2,
  "max_length": 200,
  "max_results": 5
}
```

**chunk_db 模式**
```json
{
  "keywords": ["关键词"],
  "max_results": 10,
  "sort_order": "asc"
}
```

**vector_db 模式**
```json
{
  "query_text": "查询文本",
  "top_k": 5,
  "score_threshold": 0.5
}
```

**page 模式**
```json
{
  "page_range": "3-5",
  "max_length": 30000
}
```
- `page_range`：`"3"` / `"3-5"`，按 page_mapping 切出对应页的 markdown
- `max_length`：注入 prompt 的最大字符数，超出从末尾截断（默认 30000）
- `page_source_field`（仅 `is_advanced=1`）：来源普通字段的 `field_id`。抽取时取该字段的
  **`source_pages`（可用页码：模型自报优先、程序命中页兜底）** 派生 `page_range = [min, max]`，
  **覆盖**手填的 `page_range`；来源字段**无任何可用页码**时该进阶字段才失败
- `max_pages`（仅 `is_advanced=1`）：派生区间的最大跨度，超出时从最小页起收敛为该页数

###### vl_config JSON 结构

根据 `vl_method` 不同，结构差异较大：

**vl_model 模式**
```json
{
  "page_range": "all",
  "max_pixels": 4000000
}
```
- `page_range`：`"all"` 或 `"1-3"` / `"1-3,5"`；后端 `parse_page_range` 解析，超界页码会被 clamp
- `max_pixels`：单图像素上限，超出按比例缩

**vl_progressive 模式**
```json
{
  "field_hints": "投资金额、签署日期",
  "batch_size": 2,
  "max_pixels": 4000000,
  "batch_prompt_template": null
}
```
- `field_hints`：人类语言提示要找的字段
- `batch_size`：每批塞 VL 的页数
- `batch_prompt_template`：可选自定义模板，留 `null` 时使用 `service/vl_service/_defaults.py:DEFAULT_BATCH_PROMPT`；必须含占位符 `{field_hints} {page_label} {total_pages} {history}`

**vl_locate 模式**
```json
{
  "field_hints": "资产总额、负债总额",
  "grid_pages": 6,
  "grid_cols": 3,
  "max_concurrent": 20,
  "thumb_scale": 0.75,
  "key_pages_limit": 6,
  "fallback_pages": 3,
  "max_pixels": 4000000,
  "locate_prompt_template": null
}
```
- `grid_pages` × `grid_cols`：第一轮每张网格图的布局（如 6 页 × 3 列 = 2 行）
- `max_concurrent`：第一轮多网格并行上限（与全局 `vl_model.global_max_concurrency` 取小）
- `thumb_scale`：缩略图缩放系数
- `key_pages_limit`：第一轮命中的关键页上限（去重排序后截断）
- `fallback_pages`：第一轮一页未命中时回退取前 N 页
- `locate_prompt_template`：可选自定义模板，留 `null` 时用 `_defaults.py:DEFAULT_LOCATE_PROMPT`；必须含占位符 `{field_hints} {page_labels} {position_map} {grid_rows} {grid_cols}`

> VL 模板里字面 `{ }` 必须写成 `{{ }}` 转义（后端用 `str.format()` 渲染）。

###### 示例数据

**表格类字段**
```json
{
  "field_id": "total_revenue",
  "type_id": "default",
  "field_name": "营业总收入",
  "source_type": "table",
  "enabled": 1,
  "priority": 1,
  "table_name_pattern": "利润表",
  "table_match_type": "fuzzy",
  "table_extract_prompt": "以下是利润表：\n<search_result>利润表</search_result>\n\n请提取营业总收入金额。",
  "search_type": null,
  "search_config": null,
  "text_extract_prompt": null
}
```

**文本类字段**
```json
{
  "field_id": "company_name",
  "type_id": "default",
  "field_name": "公司名称",
  "source_type": "text",
  "enabled": 1,
  "priority": 0,
  "table_name_pattern": null,
  "table_match_type": null,
  "table_extract_prompt": null,
  "search_type": "context",
  "search_config": {
    "keywords": ["公司名称", "企业名称"],
    "context_before": 50,
    "context_after": 100
  },
  "text_extract_prompt": "从以下内容提取公司全称：\n<search_result>公司名称</search_result>\n<search_result>企业名称</search_result>"
}
```

**VL 类字段（vl_locate）**
```json
{
  "field_id": "total_assets",
  "type_id": "default",
  "field_name": "资产总额",
  "source_type": "vl",
  "enabled": 1,
  "priority": 2,
  "table_name_pattern": null,
  "table_match_type": null,
  "table_extract_prompt": null,
  "search_type": null,
  "search_config": null,
  "text_extract_prompt": null,
  "vl_method": "vl_locate",
  "vl_config": {
    "field_hints": "资产总额、负债总额",
    "grid_pages": 6,
    "grid_cols": 3,
    "max_concurrent": 20,
    "thumb_scale": 0.75,
    "key_pages_limit": 6,
    "fallback_pages": 3,
    "max_pixels": 4000000
  },
  "vl_system_prompt": null,
  "vl_extract_prompt": "请基于以上图片提取「资产总额」。\n请只返回 JSON：{\"value\": \"数值（含单位）\", \"reason\": \"看到的页码与位置\"}\n未找到返回：{\"value\": \"\", \"reason\": \"未找到\"}"
}
```

---

##### 3.2 analysis_rule - 逻辑分析规则表

定义基于提取字段进行二次计算或判断的规则。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `rule_id` | VARCHAR(100) | PK, NOT NULL | - | 规则唯一标识 |
| `type_id` | VARCHAR(64) | NOT NULL | 'default' | 所属文档类型（分析时按 `file.type_id` 过滤） |
| `rule_name` | VARCHAR(200) | NOT NULL | - | 规则显示名称 |
| `rule_type` | ENUM | NOT NULL | - | 规则类型 |
| `expression` | TEXT | NOT NULL | - | 表达式/提示词 |
| `system_prompt` | TEXT | NULLABLE | NULL | LLM system 提示词（judge / custom 类可选） |
| `depend_fields` | JSON | NULLABLE | NULL | 依赖的字段 ID 列表 |
| `web_search` | JSON | NULLABLE | NULL | 网络搜索配置（judge / custom 类可选，见下） |
| `is_formatted` | TINYINT | NOT NULL | 0 | 格式化输出开关（custom 类，1=按 `output_schema` 返回结构化 JSON） |
| `output_schema` | JSON | NULLABLE | NULL | 格式化输出的字段树（custom 且 `is_formatted=1` 时必填，见下） |
| `enabled` | TINYINT | - | 1 | 是否启用 |
| `priority` | INT | - | 0 | 执行优先级 |
| `created_at` | DATETIME | - | CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | DATETIME | - | CURRENT_TIMESTAMP | 更新时间 |

###### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_analysis_rule_type_id` | `type_id` | 普通索引 |

###### 枚举值说明

**rule_type（规则类型）**

| 值 | 说明 | expression 用途 |
|----|------|-----------------|
| `judge` | 判断类 | 发送给 LLM 进行判断的完整提示词 |
| `calc` | 计算类 | 数学表达式（支持 +、-、*、/、()） |
| `custom` | 自定义类 | 发送给 LLM 自由生成的完整提示词（返回 `{value, reason}`；`is_formatted=1` 时按 `output_schema` 产出结构化 JSON） |

###### depend_fields JSON 结构

字符串数组，列出该规则依赖的所有 `field_id`：

```json
["total_revenue", "net_profit", "total_assets"]
```

###### web_search JSON 结构

judge / custom 类规则可选的联网检索增强（`service/analysis_service.py:apply_web_search`，博查 API）：

```json
{
  "enabled": true,
  "query": "<field_result>company_name</field_result> 最新行政处罚",
  "count": 5,
  "freshness": "oneYear"
}
```

- `query` 支持 `<field_result>field_id</field_result>` 占位符，先用提取结果解析再搜索
- 搜索结果替换 `expression` 中的 `<web_search_result/>` 占位符后送 LLM
- 溯源数据写入 `analysis_result.source_refs` 的 `_web_search` 键

###### output_schema JSON 结构

custom 类规则 `is_formatted=1` 时的输出字段树（`utils/output_schema.py` 校验并渲染成结构说明 + 示例 JSON 注入提示词）：

```json
[
  { "key": "总股东数", "type": "number", "example": "3" },
  { "key": "主要股东", "type": "array", "children": [
    { "key": "名称", "type": "string", "example": "张三" },
    { "key": "持股比例", "type": "string", "example": "51%" }
  ]}
]
```

- `key`（必填）：字段名，同级不可重名；`type` ∈ `string`/`number`/`boolean`/`object`/`array`
- `example` / `desc`（可选）：标量节点的示例值与说明，仅用于拼接示例 JSON
- `object` / `array` 必须含非空 `children`；标量节点不得有 `children`
- 校验失败（空 `children`、缺 `key`、同级重名等）保存时返回 **422**

###### 示例数据

**判断类规则**
```json
{
  "rule_id": "is_profitable",
  "type_id": "default",
  "rule_name": "是否盈利",
  "rule_type": "judge",
  "expression": "公司净利润为 <field_result>net_profit</field_result> 元。\n\n请判断该公司是否处于盈利状态？",
  "depend_fields": ["net_profit"],
  "enabled": 1,
  "priority": 0
}
```

**计算类规则**
```json
{
  "rule_id": "profit_margin",
  "type_id": "default",
  "rule_name": "净利润率",
  "rule_type": "calc",
  "expression": "<field_result>net_profit</field_result> / <field_result>total_revenue</field_result> * 100",
  "depend_fields": ["net_profit", "total_revenue"],
  "enabled": 1,
  "priority": 1
}
```

---

#### 4. 结果相关表

##### 4.1 extraction_result - 提取结果表

存储每个文件的字段提取结果。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID |
| `field_id` | VARCHAR(100) | PK, NOT NULL | - | 字段 ID（关联 extraction_field） |
| `extracted_value` | LONGTEXT | NOT NULL | '' | 提取的值 |
| `reason` | TEXT | NULLABLE | NULL | 提取理由/依据（LLM 返回） |
| `source_refs` | JSON | NULLABLE | NULL | 参考块（检索来源/页码/检索原文/bboxes/VL 元数据），提取失败时为 NULL |
| `model_pages` | JSON | NULLABLE | NULL | 模型自报参考页 `int[]`（LLM 输出的 `pages`）；VL 类 / `use_llm=0` / 模型未返回时为 NULL |

###### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_extraction_result_file_id` | `file_id` | 普通索引 |

###### 说明

- 复合主键：`file_id` + `field_id`
- 每个文件的每个字段只有一条记录
- 提取失败时 `extracted_value` 为空字符串，`source_refs` 与 `model_pages` 均为 NULL
- `model_pages` 是 2026-08 新增列。**存量行为 NULL**，它们的模型自报页码在
  `source_refs["_model_pages"]` 里；读取一律走 `extraction_service.read_model_pages()`
  兼容两处，不要直读本列。对外 API 把该值提升为顶层 `pages` 字段，并另算一个
  `source_pages`（模型自报优先、程序命中页兜底，恒为已展开的 `int[]`）——
  `source_pages` **不落库**，是 `model_pages` + `source_refs` 的纯派生值，
  每次输出时由 `derive_source_pages()` 现算，避免与 `source_refs` 脱节。
  见第 12.5 节第 7 小节。
- `extracted_value` 用 **LONGTEXT** 而非 TEXT：TEXT 上限 65535 **字节**，utf8mb4 下中文只能存约
  21845 字；而 `search_type=page` + `use_llm=0` 会把整段原文直接当字段值落库，`max_length`
  默认 30000 **字符**（≈90000 字节）即已超 TEXT 上限。字符与字节的单位错配曾导致线上
  `DataError 1406`（2026-07-28）。`analysis_result.result_value` 同理 —— 规则通过
  `<field_result>` 引用超长字段时结果也会超限。旧库由 `service/init_service.py` 的
  `longtext_migrations` 自动 `MODIFY COLUMN` 扩容，幂等。

###### source_refs JSON 结构

按占位符 label 分组的 dict，形态随字段的 `source_type` 不同（生成位置：`service/extraction_service.py` 的 `_build_table_source_refs` / `_build_text_source_refs`）。

**table 类**（固定 `_tables` 键）：

```json
{
  "_tables": [
    {
      "type": "table",
      "table_index": 1,
      "table_name": "投资估算表",
      "start_pos": 5120,
      "end_pos": 6890,
      "page_num": "12",
      "text": "表格名称: 投资估算表\n<table>...</table>",
      "bboxes": [{"page_num": 12, "bbox": [88.0, 120.5, 507.3, 680.2], "page_size": [595.0, 842.0]}]
    }
  ],
  "_texts": {"投资估算表": "表格名称: 投资估算表\n<table>...</table>"}
}
```

**text 类**（按检索关键词分组；section 检索无 keyword 时用 section_title 兜底，page 检索固定用 `page_content`）：

```json
{
  "公司名称": [
    {
      "type": "context",
      "start_pos": 120,
      "end_pos": 420,
      "page_num": "1",
      "text": "……公司名称：某某科技有限公司……",
      "bboxes": [{"page_num": 1, "bbox": [88.0, 72.5, 507.3, 96.1], "page_size": [595.0, 842.0]}]
    }
  ],
  "_texts": {"公司名称": "……拼接后实际注入占位符的完整文本……"}
}
```

**vl 类**（整个 source_refs 仅含 `_vl` 一个键，无检索文本）：

```json
{"_vl": {"method": "vl_locate", "total_pages": 48, "key_pages": [12, 13, 15], "vl_total_tokens": 8421}}
```

字段说明：

- 每条 ref 通用字段：`type`、`start_pos`、`end_pos`、`page_num`（字符串，可能为范围如 `"3-5"`）、`text`（该条命中注入 prompt 的原始片段，table 类含 `表格名称: xxx\n` 前缀）。
- `chunk_db` / `vector_db` 检索的 ref 另带 `chunk_id` / `chunk_index`；table 类另带 `table_index` / `table_name`；page 检索 ref 为 `{type:"page", page_range, start_pos, end_pos, length, truncated, page_num, text}`。
- 顶层 `_texts` 键 = `{label: 拼接后实际注入 <search_result> 占位符的完整文本}`（多条命中以 `\n---\n` 拼接）。
- `bboxes` = `[{page_num(int), bbox, page_size}]`，由 `lookup_bboxes` 从 `file_content.page_mapping` 查得，text 5 种检索（context/section/rule/chunk_db/vector_db）与 table 类携带（**非空才挂键**）；page 检索（整页切片）与 vl 类不挂。
- **容错**：提取失败时整个 `source_refs` 为 NULL；存量老数据无 `text` / `_texts` / `bboxes` 键（老文件 page_mapping 无 bbox，重新解析后才有），消费方读取时需容错。
- **进阶字段专属键**（`is_advanced=1` 才可能出现）：`_resolved_refs` = `{被引用 field_id: 实际填入的值}`；`_page_link` = `{source_field, model_pages, derived_range: [start, end], capped}`（仅 `page` 检索且配了 `page_source_field` 时）。

###### 示例数据

```json
{
  "file_id": "a1b2c3d4e5f6",
  "field_id": "total_revenue",
  "extracted_value": "150000000",
  "reason": "从合并利润表第3行「营业总收入」列提取，金额为1.5亿元"
}
```

---

##### 4.2 analysis_result - 分析结果表

存储每个文件的逻辑分析结果。

###### 表结构

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `file_id` | VARCHAR(64) | PK, NOT NULL | - | 文件 ID |
| `rule_id` | VARCHAR(100) | PK, NOT NULL | - | 规则 ID（关联 analysis_rule） |
| `result_value` | LONGTEXT | NOT NULL | '' | 分析结果值 |
| `input_values` | JSON | NULLABLE | NULL | 输入字段值快照 |
| `reason` | TEXT | NULLABLE | NULL | 分析理由/依据 |
| `source_refs` | JSON | NULLABLE | NULL | 依赖字段的参考块，`{field_id: 该字段的 extraction_result.source_refs}`；启用网络搜索的规则另含 `_web_search` 键（query/结果列表溯源）。无依赖参考时为 NULL |

###### 索引

| 索引名 | 字段 | 类型 |
|--------|------|------|
| `ix_analysis_result_file_id` | `file_id` | 普通索引 |

###### 说明

- 复合主键：`file_id` + `rule_id`
- `result_value` 对于 judge 类型为 `"true"` 或 `"false"`
- `result_value` 对于 calc 类型为计算结果（保留2位小数）
- `result_value` 对于 custom 类型为模型自由生成的 `value`（`is_formatted=1` 时为按 `output_schema` 组织的结构化 JSON 字符串）
- `input_values` 记录分析时使用的字段值，便于追溯

###### input_values JSON 结构

```json
{
  "net_profit": "5000000",
  "total_revenue": "150000000"
}
```

###### 示例数据

**判断类结果**
```json
{
  "file_id": "a1b2c3d4e5f6",
  "rule_id": "is_profitable",
  "result_value": "true",
  "input_values": {
    "net_profit": "5000000"
  },
  "reason": "净利润5000000元大于0，公司处于盈利状态"
}
```

**计算类结果**
```json
{
  "file_id": "a1b2c3d4e5f6",
  "rule_id": "profit_margin",
  "result_value": "3.33",
  "input_values": {
    "net_profit": "5000000",
    "total_revenue": "150000000"
  },
  "reason": "计算公式: 5000000 / 150000000 * 100 = 3.33"
}
```

---

#### 5. 向量数据库 (Milvus)

除 MySQL 外，系统还使用 Milvus 存储文本分块的向量表示（embedding 阶段写入；`doc_type.enable_embedding=0` 的类型跳过）。

##### 5.1 Collection 结构

**Collection 名称**: 由 `configs/config.yaml` 的 `milvus.collection_name` 配置，启动时自动创建（`utils/milvus_client.py:ensure_collection`）。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `chunk_id` | VARCHAR(64) | 主键（与 `file_chunk.chunk_id` 一致） |
| `file_id` | VARCHAR(64) | 文件 ID |
| `chunk_index` | INT64 | 分块序号 |
| `total_chunks` | INT64 | 分块总数 |
| `chunk_content` | VARCHAR(65535) | 分块文本（冗余存储，检索结果直接可用） |
| `start_pos` | INT64 | 在 markdown 全文中的起始位置 |
| `end_pos` | INT64 | 在 markdown 全文中的结束位置 |
| `page_num` | VARCHAR(20) | 所在 PDF 页码 |
| `embedding` | FLOAT_VECTOR | 向量（维度 = `embedding.embedding_dim` 配置，须与所用嵌入模型输出维度一致） |

##### 5.2 索引配置

由 `milvus` 配置节决定（默认值参见本文内 `utils/config.py`）：

- **索引类型**: `index_type`（默认 IVF_FLAT）
- **度量方式**: `metric_type`（默认 L2 欧氏距离）
- **nlist**: `nlist` 配置项
- **检索 topK**: `search_topk` 配置项

> `vector_db` 检索返回的 `chunk_content`/`start_pos`/`end_pos`/`page_num` 直接来自 Milvus 冗余字段，无需回查 MySQL；`start_pos`/`end_pos` 同样用于 `source_refs.bboxes` 定位。

---

#### 6. 建表 SQL

> 实际建库建表由启动时 `service/init_service.py:run_init` 按 `model/tables.py` ORM 自动完成（含旧库增量 ALTER 迁移），库名取 `configs/config.yaml` 的 `mysql.database`。以下 SQL 仅作参考。

```sql
-- 创建数据库（库名取 mysql.database 配置）
CREATE DATABASE IF NOT EXISTS wanz_parse
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE wanz_parse;

-- 0. doc_type 表
CREATE TABLE IF NOT EXISTS doc_type (
  type_id VARCHAR(64) PRIMARY KEY,
  type_name VARCHAR(200) NOT NULL,
  description TEXT NULL,
  max_parse_pages INT NULL,
  enable_embedding TINYINT NOT NULL DEFAULT 1,
  is_default TINYINT DEFAULT 0,
  is_template TINYINT DEFAULT 0,
  parent_type_id VARCHAR(64) NULL,
  enabled TINYINT DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 1. files 表
CREATE TABLE IF NOT EXISTS files (
  file_id VARCHAR(64) PRIMARY KEY,
  type_id VARCHAR(64) NOT NULL DEFAULT 'default',
  file_name VARCHAR(512) NOT NULL,
  file_size BIGINT DEFAULT 0,
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  start_parsing_time DATETIME NULL,
  end_parsing_time DATETIME NULL,
  start_tableing_time DATETIME NULL,
  end_tableing_time DATETIME NULL,
  start_chunking_time DATETIME NULL,
  end_chunking_time DATETIME NULL,
  start_embedding_time DATETIME NULL,
  end_embedding_time DATETIME NULL,
  start_extracting_time DATETIME NULL,
  end_extracting_time DATETIME NULL,
  start_analyzing_time DATETIME NULL,
  end_analyzing_time DATETIME NULL,
  progress VARCHAR(32) DEFAULT 'parsing',
  error TEXT NULL,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX ix_files_type_id (type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. file_content 表
CREATE TABLE IF NOT EXISTS file_content (
  file_id VARCHAR(64) PRIMARY KEY,
  file_content LONGTEXT NOT NULL,
  middle_json LONGTEXT NULL,
  page_mapping JSON NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. file_table 表
CREATE TABLE IF NOT EXISTS file_table (
  file_id VARCHAR(64) NOT NULL,
  table_index INT NOT NULL,
  total_table INT DEFAULT 0,
  table_name VARCHAR(500) DEFAULT '',
  table_content LONGTEXT NOT NULL,
  start_pos INT DEFAULT 0,
  end_pos INT DEFAULT 0,
  page_num VARCHAR(20) NULL DEFAULT '',
  PRIMARY KEY (file_id, table_index),
  INDEX ix_file_table_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. file_chunk 表
CREATE TABLE IF NOT EXISTS file_chunk (
  file_id VARCHAR(64) NOT NULL,
  chunk_id VARCHAR(64) NOT NULL,
  chunk_index INT DEFAULT 0,
  total_chunks INT DEFAULT 0,
  chunk_content TEXT NOT NULL,
  start_pos INT DEFAULT 0,
  end_pos INT DEFAULT 0,
  page_num VARCHAR(20) NULL DEFAULT '',
  PRIMARY KEY (file_id, chunk_id),
  INDEX ix_file_chunk_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. extraction_field 表
CREATE TABLE IF NOT EXISTS extraction_field (
  field_id VARCHAR(100) PRIMARY KEY,
  type_id VARCHAR(64) NOT NULL DEFAULT 'default',
  field_name VARCHAR(200) NOT NULL,
  source_type ENUM('table', 'text', 'vl') NOT NULL,
  enabled TINYINT DEFAULT 1,
  priority INT DEFAULT 0,
  use_llm TINYINT NOT NULL DEFAULT 1,
  is_advanced TINYINT NOT NULL DEFAULT 0,
  depend_fields JSON NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  table_name_pattern VARCHAR(500) NULL,
  table_match_type ENUM('exact', 'fuzzy', 'contains', 'llm') NULL,
  table_match_keywords JSON NULL,
  table_match_max_results INT NULL,
  table_system_prompt TEXT NULL,
  table_extract_prompt TEXT NULL,
  search_type ENUM('context', 'section', 'rule', 'chunk_db', 'vector_db', 'page') NULL,
  search_config JSON NULL,
  text_system_prompt TEXT NULL,
  text_extract_prompt TEXT NULL,
  vl_method VARCHAR(32) NULL,        -- 应用层 enum：vl_model/vl_progressive/vl_locate
  vl_config JSON NULL,
  vl_system_prompt TEXT NULL,
  vl_extract_prompt TEXT NULL,
  INDEX ix_extraction_field_type_id (type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 注：vl_method 在 ORM (model/tables.py) 层用 ENUM('vl_model','vl_progressive','vl_locate')；
-- DDL 用 VARCHAR(32) 是 init_service.py 自动迁移逻辑的兼容选择，旧库 ALTER ADD 时不需重排 ENUM。

-- 6. analysis_rule 表
CREATE TABLE IF NOT EXISTS analysis_rule (
  rule_id VARCHAR(100) PRIMARY KEY,
  type_id VARCHAR(64) NOT NULL DEFAULT 'default',
  rule_name VARCHAR(200) NOT NULL,
  rule_type ENUM('judge', 'calc', 'custom') NOT NULL,
  expression TEXT NOT NULL,
  system_prompt TEXT NULL,
  depend_fields JSON NULL,
  web_search JSON NULL,
  is_formatted TINYINT NOT NULL DEFAULT 0,
  output_schema JSON NULL,
  enabled TINYINT DEFAULT 1,
  priority INT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX ix_analysis_rule_type_id (type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 7. extraction_result 表
CREATE TABLE IF NOT EXISTS extraction_result (
  file_id VARCHAR(64) NOT NULL,
  field_id VARCHAR(100) NOT NULL,
  extracted_value LONGTEXT NOT NULL,
  reason TEXT NULL,
  source_refs JSON NULL,
  model_pages JSON NULL,
  PRIMARY KEY (file_id, field_id),
  INDEX ix_extraction_result_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 8. analysis_result 表
CREATE TABLE IF NOT EXISTS analysis_result (
  file_id VARCHAR(64) NOT NULL,
  rule_id VARCHAR(100) NOT NULL,
  result_value LONGTEXT NOT NULL,
  input_values JSON NULL,
  reason TEXT NULL,
  source_refs JSON NULL,
  PRIMARY KEY (file_id, rule_id),
  INDEX ix_analysis_result_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

---

#### 附录：字段类型速查

| MySQL 类型 | 存储范围 | 用途 |
|------------|----------|------|
| VARCHAR(64) | 最多 64 字符 | ID 类字段 |
| VARCHAR(100) | 最多 100 字符 | 配置 ID |
| VARCHAR(200) | 最多 200 字符 | 名称字段 |
| VARCHAR(500) | 最多 500 字符 | 模式/结果值 |
| VARCHAR(512) | 最多 512 字符 | 文件名 |
| TEXT | 最多 64KB | 中等文本 |
| LONGTEXT | 最多 4GB | 大文本（全文、表格内容） |
| JSON | 变长 | 结构化配置 |
| TINYINT | 0-255 | 布尔标志 |
| INT | ±21亿 | 序号、计数 |
| BIGINT | ±922亿亿 | 文件大小 |
| DATETIME | 日期时间 | 时间戳 |
| ENUM | 枚举值 | 固定选项 |

---

*文档版本: 2.0.0 | 最后更新: 2026-06 | 表结构说明同步自 model/tables.py*


### 12.7 枚举值与状态机

> 对应服务版本 0.3.0

本页是「析卷 AI」全部枚举值与文件处理状态机的**完整说明**。本文接口参考与配置指南章节均使用本节列出的枚举值，避免同一枚举散落多处漂移。

约定：下列枚举值均为**小写精确字符串**，直接出现在 API 请求/响应与数据库列中，匹配时大小写敏感。每个枚举给出「取值 + 含义 + 配置位置」。

#### 枚举速览

| 枚举 | 配置位置 | 取值 |
|---|---|---|
| [SourceType](#sourcetype) | `extraction_field.source_type` | `table` · `text` · `vl` |
| [TableMatchType](#tablematchtype) | `table` 来源的 `table_match_type` | `exact` · `fuzzy` · `contains` · `llm` |
| [SearchType](#searchtype) | `text` 来源的 `search_type` | `context` · `section` · `rule` · `chunk_db` · `vector_db` · `page` |
| [VLMethod](#vlmethod) | `vl` 来源的 `vl_method` | `vl_model` · `vl_progressive` · `vl_locate` |
| [RuleType](#ruletype) | `analysis_rule.rule_type` | `judge` · `calc` · `custom` |
| [progress](#progress) | `files.progress` | 参见本文内 [progress 状态机](#progress-状态机) |

---

<a id="sourcetype"></a>

#### SourceType — 字段来源类型

配置位置：`extraction_field.source_type`。决定该字段走哪条抽取链路。

| 值 | 说明 |
|----|------|
| `table` | 从已提取的表格中匹配并 LLM 抽取 |
| `text` | 从 Markdown 文本检索后 LLM 抽取 |
| `vl` | 直接对原 PDF 走 VL 视觉模型抽取 |

<a id="tablematchtype"></a>

#### TableMatchType — 表格匹配方式

配置位置：`table` 来源字段的 `table_match_type`。决定按表名定位目标表格的方式。

| 值 | 说明 |
|----|------|
| `exact` | 精确匹配 |
| `fuzzy` | 模糊匹配 |
| `contains` | 包含匹配 |
| `llm` | LLM 语义匹配（可携带 `table_match_keywords`） |

<a id="searchtype"></a>

#### SearchType — 文本检索方式

配置位置：`text` 来源字段的 `search_type`。决定从 Markdown 中召回喂给 LLM 的上下文的方式。

| 值 | 说明 |
|----|------|
| `context` | 关键词命中 + 前后上下文 |
| `section` | 章节标题匹配 |
| `rule` | 关键词起点 + 停止词边界 |
| `chunk_db` | MySQL 内分块检索 |
| `vector_db` | Milvus 语义检索 |
| `page` | 按 `page_range` 直接切 Markdown 喂 LLM；占位符固定为 `<search_result>page_content</search_result>`，可配 `max_length` 末尾截断 |

<a id="vlmethod"></a>

#### VLMethod — VL 抽取方法

配置位置：`vl` 来源字段的 `vl_method`。决定视觉模型端到端抽取的策略。

| 值 | 说明 |
|----|------|
| `vl_model` | 指定页全部塞 VL 一次出 JSON |
| `vl_progressive` | 分批扫描 + 伪历史累积 + 最后文本聚合 |
| `vl_locate` | 缩略图网格并行定位 + 关键页高清提取 |

<a id="ruletype"></a>

#### RuleType — 分析规则类型

配置位置：`analysis_rule.rule_type`。决定逻辑分析阶段该规则如何求值。

| 值 | 说明 |
|----|------|
| `judge` | LLM 判断，返回 `true`/`false`（也可能是 LLM 自由文本判断结果） |
| `calc` | `numexpr` 计算表达式，按 `analysis.calc_precision`（默认 2 位）保留小数 |
| `custom` | LLM 自由生成，返回 `{value, reason}`；`is_formatted=1` 时 `value` 为按 `output_schema` 组织的结构化 JSON 字符串 |

---

<a id="progress"></a>

#### progress 状态机

配置位置：`files.progress`。跟踪单个文件在六阶段管线中的处理进度。

**成功路径：** `parsing` → `tableing` → `chunking` → `embedding` → `extracting` → `analyzing` → `complete`

每个 `*ing` 状态都有对应的 `*_failed` 失败态；阶段失败时 `progress` 置为 `<stage>_failed` 并把错误写入 `files.error`。

```
parsing ──► tableing ──► chunking ──► embedding ──► extracting ──► analyzing ──► complete
   │           │            │             │             │             │
   ▼           ▼            ▼             ▼             ▼             ▼
 parsing_    tableing_    chunking_    embedding_    extracting_   analyzing_
  failed      failed       failed        failed        failed        failed
   │           │            │             │             │             │
   └───────────┴────────────┴─────┬───────┴─────────────┴─────────────┘
                                  │
                POST /file/{id}/retry/{stage}
        （清理该阶段及下游数据后，从对应 *ing 阶段重新进入管线）
```

**状态取值：**

| 值 | 说明 |
|----|------|
| `parsing` / `parsing_failed` | 解析（MinerU） |
| `tableing` / `tableing_failed` | 表格识别（LLM 命名） |
| `chunking` / `chunking_failed` | 分块 |
| `embedding` / `embedding_failed` | 向量化 + Milvus 写入 |
| `extracting` / `extracting_failed` | 字段提取 |
| `analyzing` / `analyzing_failed` | 逻辑分析 |
| `complete` | 处理完成 |

> 启动时 `init_service` 会把所有残留的 `*ing` 状态强制改为 `*_failed`（崩溃恢复），并清理对应的孤儿数据。兼容旧值 `table_name_validating` → `tableing`。

> 重试（`POST /file/{id}/retry/{stage}`）会重置目标阶段及所有下游阶段的开始/结束时间戳，清理下游数据后从该阶段重跑。阶段与接口细节已在本文 `/file` 章节列出。


### 12.8 MinerU 解析集成

> 对应服务版本 0.3.0

本文档面向维护者，描述本系统当前对 MinerU 解析服务的完整调用实现（请求 → 返回 → 后处理、page_mapping 构建、错误边界），供其他 AI / 开发者对接或复刻时参考。

涉及代码：`service/mineru_client.py`（HTTP 调用）、`service/parse_service.py`（业务封装）、`utils/page_mapping.py`（后处理）、`utils/config.py`（配置）。

#### 1. 总览

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

#### 2. 配置（`configs/config.yaml` → `MineruConfig`）

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

#### 3. HTTP 请求细节（`service/mineru_client.py:parse_pdf`）

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
| `return_images` | `"false"` | 不要图片 |
| `start_page_id` | `"0"` | 从第 0 页开始 |
| `end_page_id` | `str(max_parse_pages - 1)` 或 `"99999"` | 有页数限制时为「页数-1」（0-indexed 闭区间）；无限制时给一个大值表示全部页 |
| `parse_method` | `"auto"` | 解析方法 |
| `lang_list` | `"ch"` | 中文 |
| `output_dir` | `"./{file_id}"`（无 file_id 时 `"."`） | MinerU 侧输出目录隔离 |
| `backend` | 配置中的 `backend` | 如 `vllm-async-engine` / `auto` |

注意：`max_parse_pages <= 0` 会被归一化为 `None`（即解析全部页）。

#### 4. 响应格式与解析

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

#### 5. 业务封装与状态管理（`service/parse_service.py`）

`parse_file(file_path, file_content_bytes, file_id, session)`：

1. 先把 `files.progress` 置为 `"parsing"`，写 `start_parsing_time`，commit。
2. 读取 mineru 配置 + 文件类型的 `max_parse_pages`，调用 `parse_pdf`。
3. 成功：写 `end_parsing_time`，返回 `(content, middle_json_str)`。**注意此处不改 progress**——进入下一阶段（tableing）时由管线置位。
4. 失败：捕获任何异常，置 `progress="parsing_failed"`、写格式化后的错误信息到 `files.error`，commit 后 **re-raise**（由管线层处理回调/SSE error 事件）。

`save_file_content(file_id, content, session, middle_json, page_mapping)`：upsert 到 `file_content` 表（已有记录则覆盖三个字段，否则插入新行）。

#### 6. middle_json 后处理：page_mapping（`utils/page_mapping.py`）

页码映射走 `build_page_mapping`（**全局唯一锚 + 跨页表格补锚 + LIS 单调清洗**，数据源仅
middle_json）。对每个 para_block 取足够长前缀（文本块取 span content，表格块取
table_body span 的 html），在**整篇 md 做全局唯一匹配**（`count==1` 才认，避免歧义），
得到可信锚 `(pos, page_num, bbox, page_size)`；跨页表格的后续页在 middle_json 里是提不出
探针的空壳块，额外按表格组补一个末页锚（参见下方构建算法第 3 步）；锚点按位置排序后用 LIS
保留 page_num 非降的最长子序列，剔除极少数破坏单调的假唯一匹配。bbox 直接取 middle_json
的原生页坐标，无需反归一化。

解析完成后，管线层（`pipeline_service.py`）调用：

```python
page_mapping = build_page_mapping(content, middle_json_str)
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
          "type": "table",            // 表格块（跨页表的首页）：html 是合并后的整表
          "bbox": [x0, y0, x1, y1],   // 整表框
          "blocks": [
            {"type": "table_caption", "lines": [{"spans": [{"content": "表3 xxx"}]}]},
            {"type": "table_body", "lines": [{"spans": [{"type": "table", "html": "<table>...</table>"}]}]}
          ]
        },
        {
          "type": "table",            // 跨页表的后续页：空壳，提不出任何文本
          "bbox": [x0, y0, x1, y1],
          "blocks": [{"type": "table_body", "lines": [], "lines_deleted": true}]
        }
      ]
    }
  ]
}
```

> 跨页表格在 md 中只有**一个** `<table>`（MinerU 已合并），但在 middle_json 里占 N 个块：首块带完整 html，其余 N−1 块是上面那种 `lines_deleted` 空壳。生产抽样中 78% 的带表格文件存在跨页表，累计 328 个页面属于这种空壳块。

**构建算法**（全局唯一锚 + 跨页表格补锚 + LIS 单调清洗）：

1. 逐页遍历 `para_blocks`，对每块递归收集探针文本（文本块取 span `content`，表格块取 `table_body` span 的 `html`，用空格拼接）；探针不足 8 字符的块跳过。
2. 依次用探针的前 40 / 25 字符在**整篇 md 做全局唯一匹配**（`md.count(prefix) == 1` 才认），命中则产出锚点 `(pos, used_len, page_num, bbox, page_size)`。不唯一或找不到都不产锚——宁可缺锚，也不用歧义位置毒化映射。
3. **跨页表格补末页锚**（`_cross_page_table_anchors`）：MinerU 把跨页表格合并成一个 `<table>` 写进 md，middle_json 里只有**首页**块携带完整 html，后续页退化成 `{"lines": [], "lines_deleted": true}` 的 `table_body` 空壳、提不出探针——表格覆盖的第 2..N 页因而一个锚点都没有。按「第 i 个表格组 ↔ md 中第 i 张 `<table>`」配对（表格枚举正则与 `table_service` 一致），给跨页组在 `</table>` 之后补一个零宽锚点（`page_num` = 组末页，**不带 bbox/page_size**——它落在表格之外，只作页码分界，挂整表框会让前端在正文位置画出表格高亮）。表格组数与 md 中表格数对不上时整体放弃补锚，避免错位污染。
4. 锚点按 `pos` 排序，再用 LIS 保留 `page_num` 非降的最长子序列，剔除极少数破坏单调的假唯一匹配。
5. 每个保留的锚点产出一条映射项（位置区间 + 页码 + bbox/page_size）——**`page_mapping` 完整字段结构已在本文“数据模型”汇编里的 `file_content` 小节列出；此处保留语义要点**。语义要点：`page_num` 为 1-indexed；`bbox`/`page_size` 在 middle_json 缺失时不带（存量老数据全部不带）；文本块 bbox 为该段落块的框，表格块为整表框；坐标系为左上原点、与 `page_size` 同一单位，前端按 `canvas尺寸 / page_size` 线性缩放画框。

> **为什么必须补末页锚**：`lookup_page_num` 的语义是「取 `start_pos` 之前最近的锚点页码」，锚点之间的空白一律继承前一个锚。跨页表格制造的锚点空洞可横跨数十页、长达数万字符，表格结束后的正文会因此继承表格**之前**的页码。实测：一份 40 页文档里 22–23 页的表，其后第 23 页正文被标成第 21 页；一份 90 页文档里 49–64 页的表，其后正文被标成第 45 页。补锚只新增锚点、不改动已有锚点（已在生产数据上验证「丢失原有锚点 = 0」且单调性保持）。表格**内部**的页码仍为首页——空壳块只有 bbox、没有行级信息，无从得知第 k 行落在哪一页，故不做估算。

**配套查询函数**（供下游 tableing/chunking/extraction 用）：

- `lookup_page_num(mapping, start_pos, end_pos)` → 二分查找返回 `"1"` 或跨页 `"1-3"`；映射为空返回 `""`。
- `lookup_bboxes(mapping, start_pos, end_pos)` → 返回命中范围内的 `[{page_num, bbox, page_size}]` 块级框列表（无 bbox 的老数据条目跳过），用于前端 PDF 高亮（`source_refs.bboxes`）。

#### 7. 错误与边界行为汇总

| 情形 | 行为 |
|---|---|
| 上传文件超过 `max_file_size` | 上传路由返回 `code=400`「文件大小超过限制 (100MB)」，不进入解析 |
| MinerU 返回非 2xx | `raise_for_status` 抛异常 → `parsing_failed` + error 落库 + re-raise |
| HTTP 超时（> `parse_timeout` 秒） | httpx 抛 `TimeoutException` → 同上 |
| 响应 `results` 为空 | 返回空 md / 空 middle_json，不报错（下游会得到空内容） |
| `middle_json` 为 dict | 自动 `json.dumps` 转字符串 |
| `middle_json` 为空 | `page_mapping` 直接为 `[]`，下游页码/bbox 功能降级（页码为空串、无高亮框） |
| `max_parse_pages <= 0` | 视为不限页数 |

#### 8. 对接要点（给复刻方的提示）

- MinerU 接口是**一次性同步 HTTP**，长文档解析时间全部消耗在这一个请求上，务必设置足够大的客户端超时（本系统生产配置 1200s）。
- 响应中 `results` 的 key 是「文件名去后缀」，但实现上**不依赖 key**，直接取第一个 value——一次只传一个文件。
- `middle_json` 的 dict/str 二态必须兼容（不同 MinerU 版本/部署行为不一致）。
- `page_mapping` 不是 MinerU 直接给的，而是本系统自建的：对 `middle_json.pdf_info[].para_blocks` 逐块取足够长前缀（文本块靠 span content、表格块靠 table_body 的 html），在整篇 `md_content` 做**全局唯一匹配**得到可信锚，再用 LIS 剔除破坏单调者；它是后续表格页码、分块页码、抽取结果 bbox 高亮的唯一来源。
- **已知坑（已根治）**：旧算法用「短前缀 + 单调游标 `md.find(prefix, cursor)`」定位，在长文档 + 重复公文套话/巨型 OCR 表格下，非唯一短前缀会让游标误跳过头，其后真实位置在游标之前的块全部 miss，造成大面积页码塌缩（436 页扫描拼接件实测：真实第 208/218/232 页的材料被塌到第 41/55 页）。content_list 顺序重放同样依赖单调游标、且表格探针唯一性更差（覆盖 42 页 < 老算法 68 页），未能根治，已移除。现行「全局唯一锚 + LIS」不依赖游标推进，同一文件覆盖 320 页、村庄材料回到正确页。
- 上线前解析的存量文件不会自动修复，但可经 `POST /file/{file_id}/recompute_page_mapping` 用落库 md+middle_json 重算刷新（无需重新上传/解析）。
- 解析阶段失败当前不能通过 `retry` 重新执行；要恢复解析结果，需要重新上传原始 PDF。








