# config.yaml 配置手册

> 对应服务版本 0.3.0

析卷 AI 的全部运行参数集中在 `configs/config.yaml`。本页逐节列出配置项、默认值与含义，作为配置的唯一权威参考。

## 加载与生效

- 默认路径 `configs/config.yaml`，可用环境变量 `APP_CONFIG_PATH` 指向其他文件。
- 由 `utils/config.py` 的 `get_config()` 以 `lru_cache` 单例加载；每节对应一个 Pydantic 模型（`ServerConfig`、`MineruConfig` …）。文件缺失时全部回退到模型内置默认。
- 配置在进程启动时读入并缓存，**改动后需重启服务**才生效。
- YAML 里省略某个键，即采用本页「默认值」列的值；省略整节则该节全部取默认。

## 读法约定

- **默认值** 列是代码内置默认（`utils/config.py` 中各 Pydantic 模型的字段默认），即省略该键时实际生效的值。
- 仓库内 `configs/config.yaml` 是**部署示例**，会用环境实际值（真实主机 / 端口 / 密钥 / 模型名）覆盖默认。示例值与默认不同的，在「含义」列以「示例:」标注。
- `llm_extra_body` / `extra_body` 等对象会**原样透传**到底层 OpenAI 兼容请求的 `extra_body`，可用于关闭思考等模型私有开关。

---

## server — HTTP 服务

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `host` | 字符串 | `"0.0.0.0"` | 监听地址，`0.0.0.0` = 所有网卡 |
| `port` | 整数 | `8080` | 监听端口。示例: `5019` |

## mineru — 外部 PDF 解析服务

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `base_url` | 字符串 | `"http://localhost:8888"` | MinerU 服务地址 |
| `backend` | 字符串 | `"vllm-async-engine"` | MinerU 解析后端引擎 |
| `queue_width` | 整数 | `1` | 解析队列宽度（并发度） |
| `parse_timeout` | 整数 | `300` | 单文件解析轮询超时（秒）。示例: `1200` |
| `max_file_size` | 整数 | `104857600` | 上传 PDF 大小上限（字节），超限直接拒收。示例: `1048576000`（约 1000MB） |

## chunking — 递归文本分块

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `chunk_size` | 整数 | `512` | 目标分块大小（字符） |
| `chunk_overlap` | 整数 | `50` | 相邻分块的重叠字符数 |
| `max_chunk_size` | 整数 | `2048` | 分块最大字符数；超长表格另按 `</tr>`/`</td>`/`\n` 边界再切 |
| `separators` | 字符串数组 | `["\n\n", "\n", "。", " "]` | 递归切分时按优先级依次尝试的分隔符 |

## embedding — 向量化（OpenAI 兼容 Embedding API）

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

## milvus — 向量数据库

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

## mysql — 关系库（异步 aiomysql）

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

## extraction — 字段提取 LLM（OpenAI 兼容 Chat API）

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `llm_base_url` | 字符串 | `"http://localhost:8000/v1"` | LLM API 地址 |
| `llm_model` | 字符串 | `"qwen-7b"` | 模型名。示例: `qwen3.5-122b` |
| `llm_api_key` | 字符串 | `""` | API 密钥 |
| `llm_timeout` | 整数 | `60` | 请求超时（秒） |
| `llm_retry_count` | 整数 | `3` | 失败重试次数；指数退避，4xx（除 429）不重试 |
| `max_context_length` | 整数 | `4096` | 注入 prompt 的检索文本字符上限，超长从末尾截断 |
| `llm_extra_body` | 对象 | `{}` | 透传到请求 `extra_body` 的额外参数。示例: `{chat_template_kwargs: {enable_thinking: false}}`（关闭思考） |

## table_name_validation — 表名校验 LLM（tableing 阶段，独立且可回退）

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

## analysis — 逻辑分析

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `calc_precision` | 整数 | `2` | `calc` 规则 numexpr 计算结果保留的小数位 |
| `judge_timeout` | 整数 | `30` | `judge` 规则 LLM 判断的超时（秒） |

## vl_model — 视觉模型抽取（OpenAI 兼容多模态）

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

## web_search — 网络搜索（博查 Bocha AI，judge 规则使用）

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

## storage — PDF 保留治理

治理 `uploads` 下的原始 PDF，**只删物理文件、不动数据库**；被清文件的解析 / 抽取结果仍可查，仅 PDF 预览与 VL 抽取会返回 404。启动时、每 `cleanup_interval_minutes` 分钟、每次上传后各触发一次清理。

| 配置项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `max_total_bytes` | 整数 | `0` | PDF 总大小上限（字节），`0` = 不限；超限按最旧 `create_time` 优先淘汰。示例: `10737418240`（10GB） |
| `max_retention_minutes` | 整数 | `0` | PDF 最长保留时长（分钟），`0` = 不限；超时即删除。示例: `4320`（3 天） |
| `cleanup_interval_minutes` | 整数 | `10` | 后台清理扫描周期（分钟） |
