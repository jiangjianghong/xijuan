# 并发运行台真实接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将并发运行台 demo 接入现有单 worker FastAPI + 原生 JavaScript SPA，提供真实的只读进程内并发快照、轮询展示和详情抽屉。

**Architecture:** 在 utils/concurrency.py 增加稳定身份的可观测 limiter registry；全局 limiter 提供 worker 级数据，局部 task limiter 提供实例级数据。新增只读 /runtime/concurrency 快照接口，前端在现有 App.switchPage() 页面体系中以 2 秒轮询渲染 demo 的 ECharts 视图。

**Tech Stack:** Python 3.12、FastAPI、asyncio、pytest/pytest-asyncio、原生 JavaScript、Node test runner、ECharts、Lucide。

---

## 文件地图

| 文件 | 责任 |
|---|---|
| utils/concurrency.py | 可观测全局 limiter、局部实例 registry、快照和热更新 |
| service/table_service.py | 表名校验局部 limiter 的实例登记与上下文 |
| service/extraction_service.py | 字段抽取局部 limiter 的实例登记与上下文 |
| service/analysis_run_service.py | 独立分析局部 limiter 的实例登记与上下文 |
| utils/llm_client.py | 文本 LLM / Embedding 请求上下文 |
| utils/vl_client.py | VL 请求上下文 |
| blue_print/runtime_router.py | 只读并发快照 API |
| blue_print/__init__.py | 注册 runtime router |
| ui/index.html | 叶子入口、运行台页面容器和脚本引用 |
| ui/js/api.js | GET /runtime/concurrency 封装 |
| ui/js/app.js | runtime-monitor 页面切换生命周期 |
| ui/js/runtime-monitor.js | 轮询、图表、事件流、详情抽屉、生命周期 |
| ui/css/style.css | 运行台命名空间样式 |
| tests/test_concurrency_runtime.py | limiter、实例 registry、快照聚合测试 |
| tests/test_runtime_router.py | API 响应和错误边界测试 |
| tests/js/runtime_monitor.test.js | Node 环境下的纯数据归一化和生命周期辅助测试 |

---

### Task 1: 固化可观测 limiter 的契约

**Files:**
- Modify: utils/concurrency.py
- Test: tests/test_concurrency_runtime.py

- [ ] **Step 1: 写失败测试，覆盖全局 limiter 的基本统计**

测试同一事件循环内同名 limiter 复用同一对象，并验证 acquire/release 后 active 配对：

~~~python
@pytest.mark.asyncio
async def test_global_limiter_tracks_active_and_completed():
    clear_limiters()
    limiter = get_limiter("global_llm", 2)
    assert runtime_snapshot()["pools"]["global_llm"]["active"] == 0

    async with limiter:
        assert runtime_snapshot()["pools"]["global_llm"]["active"] == 1

    snapshot = runtime_snapshot()["pools"]["global_llm"]
    assert snapshot["active"] == 0
    assert snapshot["completed"] == 1
~~~

- [ ] **Step 2: 运行测试确认当前实现失败**

~~~text
uv run pytest tests/test_concurrency_runtime.py::test_global_limiter_tracks_active_and_completed -q
~~~

预期：FAIL，因为当前 registry 只返回普通 asyncio.Semaphore，没有 runtime_snapshot() 或统计字段。

- [ ] **Step 3: 实现最小可观测 limiter**

在 utils/concurrency.py 中定义 ObservableLimiter，保留现有 async with limiter 调用方式。对象至少维护 name、limit、底层 semaphore、active、queued、completed 和固定长度等待样本。

实现 acquire、release、__aenter__、__aexit__ 和 snapshot。acquire 等待前增加 queued，成功后减少 queued 并增加 active；release 减少 active、增加 completed 并释放底层 semaphore。

- [ ] **Step 4: 实现 queued 和 P95**

等待样本使用 deque(maxlen=256)。P95 使用排序后的 nearest-rank，空样本返回 0。active、queued、completed 永远不能为负数；release 没有对应 acquire 时抛出明确 RuntimeError。

- [ ] **Step 5: 实现 registry 快照与清理**

提供 runtime_snapshot() 和 clear_limiters()。快照返回当前事件循环的全局 limiter 字典；clear_limiters 保留现有测试隔离语义。

- [ ] **Step 6: 运行 limiter 测试并提交**

~~~text
uv run pytest tests/test_concurrency_runtime.py tests/test_config_concurrency.py tests/test_model_client_concurrency.py -q
git add utils/concurrency.py tests/test_concurrency_runtime.py
git commit -m "feat: add observable concurrency limiters"
~~~

预期：新增测试和现有并发测试全部 PASS。

### Task 2: 保证配置热更新不破坏运行统计

**Files:**
- Modify: utils/concurrency.py
- Modify: service/settings_service.py（仅在需要适配新 registry 时）
- Test: tests/test_concurrency_runtime.py
- Test: tests/test_settings_service.py（若新增断言）

- [ ] **Step 1: 写失败测试**

~~~python
@pytest.mark.asyncio
async def test_replace_limiters_preserves_existing_holder():
    clear_limiters()
    limiter = get_limiter("global_llm", 2)
    await limiter.acquire()

    replace_limiters({"global_llm": 4})

    assert runtime_snapshot()["pools"]["global_llm"]["active"] == 1
    limiter.release()
    assert runtime_snapshot()["pools"]["global_llm"]["active"] == 0
~~~

- [ ] **Step 2: 运行测试确认当前实现失败**

~~~text
uv run pytest tests/test_concurrency_runtime.py::test_replace_limiters_preserves_existing_holder -q
~~~

预期：FAIL 或暴露 active 计数丢失，因为当前 replace_limiters 会删除旧 semaphore。

- [ ] **Step 3: 实现稳定 registry 对象**

replace_limiters 不删除仍可能被持有的统计对象。配置更新只替换容量策略，新请求使用新容量，旧 holder 仍能通过原对象 release。无运行事件循环时保留现有同步兼容行为。

- [ ] **Step 4: 运行回归测试并提交**

~~~text
uv run pytest tests/test_concurrency_runtime.py tests/test_config_concurrency.py tests/test_settings_service.py -q
git add utils/concurrency.py service/settings_service.py tests/test_concurrency_runtime.py tests/test_settings_service.py
git commit -m "fix: preserve concurrency metrics during hot reload"
~~~

### Task 3: 增加局部 task limiter 实例 registry

**Files:**
- Modify: utils/concurrency.py
- Modify: service/table_service.py
- Modify: service/extraction_service.py
- Modify: service/analysis_run_service.py
- Test: tests/test_concurrency_runtime.py

- [ ] **Step 1: 写实例维度失败测试**

验证两个实例各自 limit=4 时，busiest_active 不超过 4，但 aggregate_active 可以累计：

~~~python
@pytest.mark.asyncio
async def test_task_pool_reports_instances_without_fake_global_capacity():
    clear_limiters()
    a = register_task_limiter("task_table_validation", "file-a", 4)
    b = register_task_limiter("task_table_validation", "file-b", 4)

    await a.acquire()
    await b.acquire()

    pool = runtime_snapshot()["task_pools"]["task_table_validation"]
    assert pool["per_instance_limit"] == 4
    assert pool["busiest_active"] == 1
    assert pool["aggregate_active"] == 2
    assert pool["instance_count"] == 2
~~~

- [ ] **Step 2: 运行测试确认失败**

~~~text
uv run pytest tests/test_concurrency_runtime.py::test_task_pool_reports_instances_without_fake_global_capacity -q
~~~

预期：FAIL，因为当前没有 task instance registry。

- [ ] **Step 3: 实现实例 registry 接口**

提供 register_task_limiter(pool_name, instance_id, limit, metadata=None)。task_pools 快照至少返回 per_instance_limit、instance_count、busiest_active、aggregate_active、aggregate_queued 和有限 instances 列表。

- [ ] **Step 4: 替换表名校验局部 semaphore**

table_service.py 使用 file_id 作为 instance_id，替换直接创建的 asyncio.Semaphore；保留 global_table_validation 的 acquire 顺序和并发语义。

- [ ] **Step 5: 替换字段抽取局部 semaphore**

extraction_service.py 使用 file_id 作为 instance_id。当前字段循环串行，快照应真实反映 active 通常为 1、queued 通常为 0，不能注入 demo 模拟队列。

- [ ] **Step 6: 替换独立分析局部 semaphore**

analysis_run_service.py 使用稳定 task_id 或批次标识作为 instance_id；item 级 semaphore 的 active/queued 进入 task_analysis 实例快照。

- [ ] **Step 7: 运行服务并发回归测试并提交**

~~~text
uv run pytest tests/test_concurrency_runtime.py tests/test_stage_concurrency.py tests/test_analysis_run_service.py tests/test_model_client_concurrency.py -q
git add utils/concurrency.py service/table_service.py service/extraction_service.py service/analysis_run_service.py tests/test_concurrency_runtime.py
git commit -m "feat: track task limiter instances"
~~~

### Task 4: 接入业务上下文和事件 ring buffer

**Files:**
- Modify: utils/concurrency.py
- Modify: service/table_service.py
- Modify: service/extraction_service.py
- Modify: service/analysis_run_service.py
- Modify: utils/llm_client.py
- Modify: utils/vl_client.py
- Test: tests/test_concurrency_runtime.py

- [ ] **Step 1: 写事件上下文失败测试**

验证事件保留 pool_id、file_id、stage 等安全上下文，不写 prompt、API key、文件正文或结果值。

- [ ] **Step 2: 实现 context(metadata) lease 接口**

保留 async with limiter 兼容调用，新增 limiter.context(metadata) 返回 lease。lease 在 acquire 时登记 metadata，在 release 时移除并写入事件。事件 ring buffer 固定最多 100 条。

- [ ] **Step 3: 在模型客户端传入上下文**

llm_client.py 和 vl_client.py 的请求 acquire 处附带 model/stage；file_id、field_id、rule_id 从当前任务上下文传入，缺失时显示“当前请求”。

- [ ] **Step 4: 在业务阶段传入上下文**

表名校验、字段抽取、逻辑分析的 task/global limiter acquire 附带 file_id、stage 和 field_id/rule_id/task_id。不得把 prompt、密钥、正文或结果值写入事件。

- [ ] **Step 5: 运行并发测试并提交**

~~~text
uv run pytest tests/test_concurrency_runtime.py tests/test_model_client_concurrency.py tests/test_vl_client.py -q
git add utils/concurrency.py service/table_service.py service/extraction_service.py service/analysis_run_service.py utils/llm_client.py utils/vl_client.py tests/test_concurrency_runtime.py
git commit -m "feat: attach context to concurrency runtime events"
~~~

### Task 5: 新增只读运行快照 API

**Files:**
- Create: blue_print/runtime_router.py
- Modify: blue_print/__init__.py
- Create: tests/test_runtime_router.py
- Modify: model/schemas.py（仅在统一响应模型需要时）

- [ ] **Step 1: 写 API 失败测试**

调用 GET /runtime/concurrency，断言 HTTP 200、scope=single-process，并包含 6 个 global 池、3 个 task 池和 global_pipeline。

- [ ] **Step 2: 运行测试确认路由不存在**

~~~text
uv run pytest tests/test_runtime_router.py::test_runtime_concurrency_snapshot_shape -q
~~~

预期：FAIL，返回 404。

- [ ] **Step 3: 实现 router 和统一响应**

runtime_router.py 只读取 runtime_snapshot()，将全局池、task 实例池和 global_pipeline 未接入项规范化为设计文档中的 pools 数组，使用现有 ResponseWrapper 返回。

- [ ] **Step 4: 实现全局摘要**

摘要只统计六个 scope=global 共享池：

~~~python
summary = {
    "active": sum(pool["active"] for pool in global_pools),
    "capacity": sum(pool["limit"] for pool in global_pools),
    "queued": sum(pool["queued"] for pool in global_pools),
    "hot_pools": sum(pool["status"] in {"pressure", "saturated"} for pool in global_pools),
    "wait_p95_ms": max((pool["wait_p95_ms"] for pool in global_pools), default=0),
}
~~~

它表示共享资源槽位，不表示唯一业务任务数。

- [ ] **Step 5: 注册、异常测试和提交**

~~~text
uv run pytest tests/test_runtime_router.py tests/test_api_docs.py -q
git add blue_print/runtime_router.py blue_print/__init__.py model/schemas.py tests/test_runtime_router.py
git commit -m "feat: expose runtime concurrency snapshot"
~~~

### Task 6: 接入前端 API 与运行台页面

**Files:**
- Modify: ui/index.html
- Modify: ui/js/api.js
- Modify: ui/js/app.js
- Create: ui/js/runtime-monitor.js
- Modify: ui/css/style.css
- Test: tests/js/runtime_monitor.test.js

- [ ] **Step 1: 写 Node 失败测试**

新增 normalizeSnapshot 纯函数测试，覆盖 global/task 两种池结构、未接入池和失败快照。断言 global 池使用 active/limit，task 池使用 busiest_active/per_instance_limit。

- [ ] **Step 2: 运行 Node 测试确认失败**

~~~text
node --test tests/js/runtime_monitor.test.js
~~~

预期：FAIL，RuntimeMonitor 尚未定义。

- [ ] **Step 3: 增加 API 封装**

在 ui/js/api.js 增加：

~~~javascript
async getRuntimeConcurrency() {
    const result = await this.request('/runtime/concurrency');
    return result.data;
}
~~~

- [ ] **Step 4: 实现 runtime-monitor.js**

实现 RuntimeMonitor.state、normalizeSnapshot、activate、deactivate、refresh、render、openDetail、closeDetail。activate 立即刷新后每 2000ms 请求；deactivate 清理 timer、监听器和图表实例；失败时保留最后快照并显示错误；不提供 pause/resume。

- [ ] **Step 5: 拆 demo DOM 入现有 index.html**

新增 page-runtime-monitor，所有页面样式使用 runtime-monitor-page 命名空间。复用现有 ECharts/Lucide，不复制 mockup 全局 html/head/body。叶子图标使用 App.switchPage('runtime-monitor')，不新增顶部导航按钮。

- [ ] **Step 6: 接入 App.switchPage 生命周期**

在 app.js 页面切换中增加：

~~~javascript
if (typeof RuntimeMonitor !== 'undefined') {
    if (page === 'runtime-monitor') RuntimeMonitor.activate();
    else RuntimeMonitor.deactivate();
}
~~~

- [ ] **Step 7: 添加命名空间 CSS 和响应式规则**

保留桌面容量矩阵、窄屏横向滚动、详情抽屉、reduced-motion、44px 触控目标和 visible focus；不得覆盖现有 header、btn、glass-card、page-container 全局行为。

- [ ] **Step 8: 运行 Node 检查并提交**

~~~text
node --check ui/js/runtime-monitor.js
node --test tests/js/runtime_monitor.test.js
git add ui/index.html ui/js/api.js ui/js/app.js ui/js/runtime-monitor.js ui/css/style.css tests/js/runtime_monitor.test.js
git commit -m "feat: add runtime monitor page"
~~~

### Task 7: 集成验证和视口验收

**Files:**
- Modify: tests/test_concurrency_runtime.py（必要时补边界）
- Modify: tests/test_runtime_router.py（必要时补边界）
- Modify: tests/js/runtime_monitor.test.js（必要时补边界）

- [ ] **Step 1: 运行完整后端测试**

~~~text
uv run pytest -q
~~~

预期：全部现有和新增后端测试 PASS。

- [ ] **Step 2: 运行完整 JavaScript 测试**

~~~text
node --test tests/js/*.test.js
~~~

预期：全部现有和新增 Node 测试 PASS。

- [ ] **Step 3: 启动服务并检查 API**

~~~text
python app.py
curl http://127.0.0.1:5019/runtime/concurrency
~~~

预期：HTTP 200，响应包含 single-process、6 个 global 池、3 个 task 池和 global_pipeline 未接入项。

- [ ] **Step 4: 浏览器验收入口和轮询**

访问 http://127.0.0.1:5019/ui/，检查叶子图标入口、首次快照、2 秒更新时间、离开页面停止请求、刷新按钮只读、global/task 口径和详情抽屉。

- [ ] **Step 5: 验收错误态和视口**

检查 API 失败时保留最后数据并显示错误；使用 1440x900、1280x720、390x844 检查无遮挡、控件不重叠、窄屏可滚动、控制台无 JavaScript 错误。

- [ ] **Step 6: 提交最终验证结果**

~~~text
git status --short
git log -8 --oneline
~~~

预期：只有本次功能提交，工作区无未预期修改。
