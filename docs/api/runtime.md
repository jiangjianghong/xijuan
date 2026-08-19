# 并发运行时接口 /runtime

> 对应服务版本 0.3.0

只读监控接口，暴露当前进程内各并发池的实时水位。配套前端页面为设置页的「并发监控」面板（`ui/js/runtime-monitor.js`）。

并发限额的配置说明见 [guides/configuration.md](../guides/configuration.md) 的 `concurrency` 节。

## 并发运行时快照

返回当前 worker 进程内各并发池的实时水位。

- 方法路径：`GET /runtime/concurrency`
- 认证：无（内网部署）

> **单进程口径**：计数来自进程内的 limiter 注册表，多 worker 部署时**每个进程独立计数、不聚合**，响应中的 `scope` 恒为 `single-process`。

**并发池分组**

| 组 | 池 | 含义 |
|---|---|---|
| 模型通道 | `global_llm` / `global_embedding` / `global_vl` | 各模型 API 的全局并发，在 client 层生效 |
| 业务阶段 | `global_table_validation` / `global_extraction` / `global_analysis` | 跨文件的阶段级并发 |
| 文件内任务 | `task_table_validation` / `task_extraction` / `task_file_analysis` | 单个文件内部的并发，按文件实例聚合 |
| 独立接口 | `independent_analysis` | `POST /analysis/run` 的 item 并发 |
| 管线调度 | `global_pipeline` | 同时处理的文件数闸门，超限文件落 `queued` 排队 |

`scope=global` 的池记录含 `limit` / `active` / `queued` / `completed` / `wait_p95_ms` / `tasks`（当前持有者上下文）；`scope=task` 的池记录改为 `per_instance_limit` / `instance_count` / `busiest_active` / `aggregate_active` / `aggregate_queued` / `instances`——单文件池是「每文件一个实例」，故给出最繁忙实例与全实例累计两个口径。

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
| summary | object | 是 | 全局池汇总 {active, capacity, queued, hot_pools, wait_p95_ms} |
| pools | array[object] | 是 | 各并发池记录（模型通道 / 业务阶段 / 文件内任务 / 管线闸门） |
| events | array[object] | 是 | 最近 20 条并发事件（倒序） |
<!-- /AUTOGEN:response -->

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
      "hot_pools": 1,         // 处于 pressure/saturated 的池数
      "wait_p95_ms": 12.5     // 各全局池 p95 等待耗时的最大值
    },
    "pools": [
      {
        "id": "global_extraction", "label": "字段抽取", "group": "业务阶段", "scope": "global",
        "limit": 8, "active": 3, "queued": 0, "completed": 126, "wait_p95_ms": 0.0,
        "status": "normal",
        "constraints": ["global_llm", "global_embedding", "global_vl"],
        "tasks": [ { "file_id": "a1b2...", "stage": "extracting", "field_id": "amount" } ]
      },
      {
        "id": "task_extraction", "label": "文件内字段抽取", "group": "文件内任务", "scope": "task",
        "per_instance_limit": 4, "instance_count": 1, "busiest_active": 3,
        "aggregate_active": 3, "aggregate_queued": 0, "status": "normal",
        "constraints": ["global_extraction"],
        "instances": [ { "instance_id": "a1b2...", "active": 3, "queued": 0, "status": "normal" } ]
      },
      {
        "id": "global_pipeline", "label": "文件管线", "group": "管线调度", "scope": "global",
        "limit": 4, "active": 1, "queued": 2, "completed": 3, "wait_p95_ms": 0.0,
        "status": "saturated", "connected": true, "constraints": [],
        "tasks": [ { "file_id": "d3c7...", "stage": "pipeline" } ],
        "note": "上传与重试的六个入口全程持有令牌，超限文件落 queued 排队。"
      }
    ],
    "events": [
      { "pool_id": "global_extraction", "type": "complete", "at": 1787123176.5,
        "context": { "file_id": "a1b2...", "stage": "extracting", "field_id": "amount" } }
    ]
  }
}
```

`events` 为最近 20 条并发事件（倒序，最新在前），`type` ∈ `acquired`（拿到令牌，带 `wait_ms`）/ `waiting`（开始排队）/ `complete`（释放）。`context` 只保留白名单键（`file_id`/`file_name`/`stage`/`field_id`/`rule_id`/`task_id`/`model`/`index`），不会泄漏业务内容。

**状态码 / 错误**

| 状态码 | 触发条件 | 响应体 |
|---|---|---|
| 200 | 成功（服务空闲时各池 `active=0`、`events=[]`） | ResponseWrapper |
