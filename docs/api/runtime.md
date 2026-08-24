# 并发运行时接口 /runtime

> 对应服务版本 0.3.0

只读监控接口，暴露当前进程内各并发池的实时水位与压力历史。配套前端页面为并发运行台（`ui/js/runtime-monitor.js`，入口是点击左上角叶子图标，与统计页同为普通内页）。

并发限额的配置说明见 [guides/configuration.md](../guides/configuration.md) 的 `concurrency` 节。

## 并发运行时快照

返回当前 worker 进程内各并发池的实时水位。

- 方法路径：`GET /runtime/concurrency`
- 认证：无（内网部署）

**查询参数**

<!-- AUTOGEN:query-params GET /runtime/concurrency -->
| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| window | string | 否 | 60s | 压力趋势时间窗口：60s / 5m / 30m，非法值回退 60s |
<!-- /AUTOGEN:query-params -->

> **单进程口径**：计数来自进程内的 limiter 注册表，多 worker 部署时**每个进程独立计数、不聚合**，响应中的 `scope` 恒为 `single-process`。

**并发池分组**

| 组 | 池 | 含义 |
|---|---|---|
| 模型通道 | `global_llm` / `global_embedding` / `global_vl` | 各模型 API 的全局并发，在 client 层生效 |
| 业务阶段 | `global_table_validation` / `global_extraction` / `global_analysis` | 跨文件的阶段级并发 |
| 独立接口 | `independent_analysis` | `POST /analysis/run` 的 item 并发 |
| 管线调度 | `global_pipeline` | 同时处理的文件数闸门，超限文件落 `queued` 排队 |

所有池记录的 `scope` 恒为 `global`，含 `limit` / `active` / `queued` / `completed` / `gate_wait_p95_ms` / `total_wait_p95_ms` / `tasks`（当前持有者上下文）。

> **单文件限流不在本接口返回**。`concurrency.task_table_validation` / `task_extraction` / `task_file_analysis` **仍然真实生效**，只是不再作为池记录下发，其事件也从 `events` 中滤除。原因是它们的每文件上限与对应全局池同量级时会常年显示 100% 饱和，而全局池远未跑满，与实情相反。被它们吸收的排队改由各池的 `total_wait_p95_ms` 体现。配置调优见 [configuration.md](../guides/configuration.md)。

**两个等待口径**

| 字段 | 含义 |
|---|---|
| `gate_wait_p95_ms` | **本闸等待**：只算在这一道闸的 `acquire()` 上阻塞的时间，是局部量 |
| `total_wait_p95_ms` | **端到端等待**：从工作项启动算起，含上游闸门（含未展示的单文件限流）的全部排队 |

串联闸门链上（单文件闸 → 业务阶段闸 → 模型通道闸），上游会把背压吸收掉，导致下游 `gate_wait` 接近 0 而实际等待很长——两者差得越大，说明堵点越靠上游。`summary.total_wait_p95_ms` 取各池端到端口径的最大值，代表最坏等待。

> ⚠️ **分位数不可加**：`total_wait` 是在每个工作项自己身上把各段实测耗时相加后再取分位，**不能**把上下游的 `gate_wait_p95_ms` 相加得到。

**`status` 取值**

| 值 | 判定 |
|---|---|
| `idle` | `active == 0` |
| `normal` | 有占用但未达压力线 |
| `pressure` | 占用率 ≥ 75% 或排队 ≥ 2 |
| `saturated` | 已占满且有排队 |
| `offline` | 未接入或快照缺失 |

**响应体**

<!-- AUTOGEN:response GET /runtime/concurrency status=200 -->
| 字段 | 类型 | 可空 | 说明 |
|---|---|:--:|---|
| updated_at | string | 是 | 快照生成时间（ISO8601，带时区） |
| scope | string | 是 | 统计范围，恒为 single-process |
| summary | object | 是 | 全局池汇总 {active, capacity, queued, hot_pools, total_wait_p95_ms} |
| pools | array[object] | 是 | 各并发池记录（模型通道 / 业务阶段 / 独立接口 / 管线闸门），单文件池不在其中 |
| events | array[object] | 是 | 最近 20 条并发事件（倒序） |
| history | object | 是 | 压力历史 {window, window_seconds, bucket_seconds, interval_ms, retention_seconds, windows, points}；points 定长 60 桶，空桶为 null |
<!-- /AUTOGEN:response -->

**压力历史 `history`**

进程内按 **1 秒**采样并保存 **30 分钟**（1800 点）的各池利用率，**纯内存、不落库，进程重启清零**。为让响应体积与窗口长度无关，无论选哪个窗口都降采样成**固定 60 个桶**，桶内取**峰值**（均值会把短时饱和抹平）。

| 字段 | 说明 |
|---|---|
| `window` | 生效窗口，`60s` / `5m` / `30m`；请求传了非法值则回退 `60s` |
| `window_seconds` | 窗口秒数（60 / 300 / 1800） |
| `bucket_seconds` | 每桶秒数（1 / 5 / 30） |
| `interval_ms` | 后端采样间隔，恒 1000 |
| `retention_seconds` | 历史保留秒数，恒 1800 |
| `windows` | 可选窗口列表，供前端渲染下拉 |
| `points` | 定长 60 项，元素为 `{at, overall, pools}`；**该桶无采样时为 `null`**（进程刚启动时集中在左侧），消费方需容错 |

`overall` 为全局池的 `active/capacity` 百分比，`pools[池ID]` 为单池利用率（`active / limit`，与容量矩阵柱高同源）。

```jsonc
{
  "code": 200,
  "message": "success",
  "data": {
    "updated_at": "2026-08-19T14:39:03.841996+08:00",
    "scope": "single-process",
    "summary": {
      "active": 3,            // 全局池当前占用合计
      "capacity": 58,         // 全局池 limit 合计
      "queued": 0,            // 全局池排队合计
      "hot_pools": 1,               // 处于 pressure/saturated 的池数
      "total_wait_p95_ms": 1840.0   // 各全局池端到端 p95 等待的最大值
    },
    "pools": [
      {
        "id": "global_extraction", "label": "字段抽取", "group": "业务阶段", "scope": "global",
        "limit": 8, "active": 3, "queued": 0, "completed": 126,
        "gate_wait_p95_ms": 0.0,        // 本闸没排队
        "total_wait_p95_ms": 1840.0,    // 但上游单文件闸排了 1.8s
        "status": "normal",
        "constraints": ["global_llm", "global_embedding", "global_vl"],
        "tasks": [ { "file_id": "a1b2...", "file_name": "季度报告.pdf", "stage": "extracting", "field_id": "amount" } ]
      },
      {
        "id": "global_pipeline", "label": "文件管线", "group": "管线调度", "scope": "global",
        "limit": 4, "active": 1, "queued": 2, "completed": 3,
        "gate_wait_p95_ms": 0.0, "total_wait_p95_ms": 0.0,
        "status": "saturated", "connected": true, "constraints": [],
        "tasks": [ { "file_id": "d3c7...", "file_name": "年报.pdf", "stage": "pipeline" } ],
        "note": "上传与重试的六个入口全程持有令牌，超限文件落 queued 排队。"
      }
    ],
    "events": [
      { "pool_id": "global_extraction", "type": "complete", "at": 1787123176.5,
        "context": { "file_id": "a1b2...", "file_name": "季度报告.pdf", "stage": "extracting", "field_id": "amount" } }
    ],
    "history": {
      "window": "60s", "window_seconds": 60, "bucket_seconds": 1,
      "interval_ms": 1000, "retention_seconds": 1800,
      "windows": ["60s", "5m", "30m"],
      "points": [
        null,                                                   // 该桶尚无采样（进程刚启动）
        { "at": 1787123175.5, "overall": 5,  "pools": { "global_llm": 6, "global_extraction": 0 } },
        { "at": 1787123176.5, "overall": 12, "pools": { "global_llm": 25, "global_extraction": 50 } }
      ]
    }
  }
}
```

`events` 为最近 20 条并发事件（倒序，最新在前），`type` ∈ `acquired`（拿到令牌，带 `wait_ms`）/ `waiting`（开始排队）/ `complete`（释放）。`context` 只保留白名单键（`file_id`/`file_name`/`stage`/`field_id`/`rule_id`/`task_id`/`model`/`index`），不会泄漏业务内容。

`window` 是唯一查询参数，非法值不报错、回退 `60s`——监控页不该因为一个展示参数拿到 4xx。

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（服务空闲时各池 `active=0`、`events=[]`） | ResponseWrapper |
