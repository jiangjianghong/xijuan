# 并发运行台真实接入设计

## 1. 背景与目标

design-mockups/concurrency-pools.html 是一个使用模拟数据的并发池监控原型。本设计将它接入现有 FastAPI + 原生 JavaScript 单页应用，目标是：

- 从“析卷 AI”左侧叶子图标进入并发运行台；
- 在当前单 worker 进程内展示真实并发状态；
- 只读监控，不提供暂停、继续、调度控制或配置修改；
- 复用现有 ECharts、Lucide、字体和页面切换机制；
- 第一版使用 GET 快照 + 前端轮询，不引入 SSE、Redis、Prometheus 或新数据库表。

本设计不改变现有文件处理、字段抽取、逻辑分析和运行时设置的业务行为。

## 2. 当前系统约束

### 2.1 部署范围

生产环境当前只有一个 worker。utils/concurrency.py 的 limiter registry 按事件循环保存 semaphore，因此监控范围定义为：

~~~text
当前 FastAPI worker 进程
~~~

不承诺跨 worker、跨容器汇总。页面应明确显示 single-process 或“当前进程”。

### 2.2 全局池与局部池

六个 global_* limiter 是同一 worker 内共享的资源池：

~~~text
global_llm
global_embedding
global_vl
global_table_validation
global_extraction
global_analysis
~~~

三个 task_* 限制不是全局唯一 semaphore，而是每个文件或每次请求创建一份局部 semaphore：

~~~text
task_table_validation
task_extraction
task_analysis
~~~

例如两个文件各自拥有容量为 4 的 task_table_validation，真实总运行数可以是 8。因此不能用配置值 4 作为所有实例的总容量。

同一个业务请求还可能同时占用局部池、阶段全局池和模型全局池。把所有池的 active 相加会重复计算同一个请求，所以顶部汇总只统计全局共享池，并将其命名为“共享池槽位”，不称为“唯一运行任务数”。

### 2.3 当前已知语义

- global_pipeline 只有配置项，当前没有实际 acquire；显示“未接入”，不生成运行数据。
- 字段抽取按字段顺序执行，单个文件的 task_extraction 观测并发通常为 1，不能用 demo 的模拟 queued 值覆盖真实结果。
- 设置保存会调用 replace_limiters() 热替换 limiter；观测层必须保证热更新期间旧任务的 acquire/release 仍然配对。

## 3. 页面入口与前端集成

### 3.1 入口

在 ui/index.html 中将左侧叶子图标改为可聚焦按钮：

~~~text
点击叶子图标 -> App.switchPage('runtime-monitor')
~~~

产品标题继续保留现有统计页入口，不改变原有点击行为。叶子按钮必须具备明确的 aria-label、title 和可见焦点状态。

不增加新的顶部导航按钮。

### 3.2 页面模块

新增页面容器和脚本：

~~~text
ui/index.html                 # 页面容器和入口
ui/js/runtime-monitor.js      # 快照、轮询、图表、抽屉
ui/css/style.css              # 页面级样式，全部以 .runtime-monitor-page 命名空间隔离
~~~

demo 的 html、head、全局 body 样式和 Tailwind 配置不能整体复制。现有项目已经加载 ECharts、Lucide、Nunito/Lora，页面只复用这些资源。

### 3.3 页面区域

保留 demo 的视觉结构，但将文案改为真实监控语义：

1. 顶部状态栏：实时监控连接状态、最近更新时间、刷新快照。
2. 运行摘要：共享池槽位、共享池容量、排队资源、高压池、等待 P95。
3. 容量矩阵：模型通道、业务阶段、单任务限制、管线调度。
4. 压力与事件：总体压力、各全局池趋势、最近事件。
5. 右侧详情抽屉：池状态、约束路径、当前占用上下文、局部实例列表。

“暂停 / 继续”按钮删除。刷新按钮只请求快照，不控制后端任务。

## 4. 后端观测层

### 4.1 可观测 limiter

在 utils/concurrency.py 中增加稳定身份的可观测 limiter 或包装器，统一提供：

- limit：当前配置容量；
- active：当前持有槽位数；
- queued：等待 acquire 的任务数；
- completed：完成次数；
- 等待时长滚动样本及 P95；
- 当前持有者的有限上下文；
- 最近入队、完成、饱和、释放事件。

不要依赖 asyncio.Semaphore._value 或 _waiters 等私有字段作为 API 数据源。所有快照从 registry 统一读取。

### 4.2 热更新兼容

replace_limiters() 热更新时不能让旧任务持有旧对象、释放到新对象。实现必须满足：

- limiter 对外身份稳定，或旧对象仍然保留可正确 release 的状态；
- 新请求使用新容量；
- 已在等待或运行的请求不会丢失计数；
- 快照不会出现负数、active 大于有效容量或 queued 泄漏。

建议将配置容量和运行统计分离：配置更新只替换容量策略，运行统计对象继续存活。

### 4.3 业务上下文

为了让详情抽屉展示真实占用任务，acquire 时按可用程度附带：

~~~text
file_id
file_name
stage
field_id / rule_id
task_id
~~~

上下文只保存在进程内的有界结构，不写数据库。无法关联文件的底层模型请求显示“当前请求”，不能伪造文件名。

## 5. 只读 API

新增 blue_print/runtime_router.py，在 blue_print/__init__.py 注册：

~~~text
GET /runtime/concurrency
~~~

接口只读，不修改设置，不触发 acquire/release，不影响业务管线。

### 5.1 响应结构

~~~json
{
  "updated_at": "2026-08-17T18:20:30+08:00",
  "scope": "single-process",
  "summary": {
    "active": 47,
    "capacity": 66,
    "queued": 18,
    "hot_pools": 4,
    "wait_p95_ms": 1800
  },
  "pools": [
    {
      "id": "global_llm",
      "label": "文本 LLM",
      "group": "模型通道",
      "scope": "global",
      "limit": 16,
      "active": 12,
      "queued": 3,
      "completed": 142,
      "wait_p95_ms": 920,
      "status": "pressure",
      "constraints": [],
      "tasks": []
    },
    {
      "id": "task_table_validation",
      "label": "单文件表名",
      "group": "单任务限制",
      "scope": "task",
      "per_instance_limit": 4,
      "instance_count": 6,
      "busiest_active": 3,
      "aggregate_active": 11,
      "aggregate_queued": 4,
      "status": "pressure",
      "instances": []
    }
  ],
  "events": []
}
~~~

### 5.2 全局池数据口径

全局池返回单一 worker 级别的 limit/active/queued。顶部摘要只聚合 scope=global 的池，不能把 scope=task 的局部实例加进去。

### 5.3 单任务池数据口径

单任务池返回：

- per_instance_limit：每个文件或请求的配置上限；
- instance_count：当前存在的局部 limiter 实例数量；
- busiest_active：最繁忙实例的 active，用于容量矩阵柱图，范围为 0..per_instance_limit；
- aggregate_active/aggregate_queued：所有实例累计值，只在详情中展示；
- instances：有限数量的实例明细。

因此单任务柱图不会出现 8/4 这种误导性的全局汇总。

### 5.4 内存保留范围

- 每个池等待样本使用固定长度 deque，建议最多保留 256 个样本；
- 事件 ring buffer 建议最多保留 100 条，API 默认返回最近 20 条；
- 页面压力曲线在前端保留最近 60 个快照，刷新页面后重新开始；
- 不新增数据库表，不持久化监控历史。

## 6. 前端轮询与状态

### 6.1 生命周期

~~~text
进入 runtime-monitor -> 立即请求一次 -> 每 2 秒轮询
离开 runtime-monitor -> 清理定时器和 ECharts 实例
窗口失焦 -> 降低到 5 秒或暂停轮询
窗口重新聚焦 -> 立即补一次快照
~~~

请求失败时保留最后一次数据，同时显示“监控暂时不可用”和最后更新时间。监控失败不能影响文件处理和其他页面。

### 6.2 状态计算

全局池和单实例柱图都使用文字、数值和颜色共同表达：

~~~text
idle       active = 0
normal     利用率 < 75%
pressure   利用率 >= 75% 或 queued >= 2
saturated  active >= limit 且 queued > 0
offline    未接入
~~~

global_pipeline 固定为 offline/未接入。颜色不能作为唯一状态依据。

### 6.3 详情抽屉

全局池详情展示：

- 当前 active、limit、queued；
- 约束路径；
- 当前占用请求；
- 最近完成和排队事件。

单任务池详情展示：

- 每实例上限；
- 当前实例数；
- 最繁忙实例；
- 所有实例累计 active/queued；
- 按文件或 task_id 展开的实例列表。

抽屉支持关闭按钮、遮罩点击、Escape 和键盘触发。ECharts 柱图仍然是只读，不支持拖拽或修改容量。

## 7. 可访问性与响应式

- 叶子入口、刷新按钮、关闭按钮都有明确可访问名称；
- 摘要动态数值使用 aria-live="polite"；
- 容量矩阵窄屏允许横向滚动，柱宽不压缩到无法阅读；
- 所有触控目标至少 44px；
- 支持 prefers-reduced-motion，关闭非必要动画；
- 抽屉打开后焦点进入关闭按钮，关闭后回到触发柱；
- 文本、颜色和图形同时表达状态，满足键盘和低视力用户使用。

## 8. 测试与验收

### 8.1 后端

- acquire/release 后 active、queued、completed 配对正确；
- 等待样本的 P95 计算稳定；
- 热更新期间旧任务释放不破坏计数；
- 全局池返回 6 个共享池；
- 单任务池返回实例维度数据，不出现 aggregate 值超过错误的单实例容量语义；
- global_pipeline 明确为未接入；
- API 异常不会影响业务请求。

### 8.2 前端

- 点击叶子图标能切换到运行台；
- 进入页面立即加载，离开页面停止轮询；
- 刷新按钮只发起 GET 请求；
- 全局池和单任务池使用不同的展示口径；
- 详情抽屉能显示并关闭；
- 监控请求失败时有可见错误状态并保留最后快照；
- 1440x900、1280x720、390x844 下无遮挡、溢出或重叠；
- 浏览器控制台无 JavaScript 错误。

## 9. 明确不做的内容

- 不把 task_* 局部 semaphore 伪装成全局池；
- 不把所有池的 active 相加并称为唯一运行任务数；
- 不从监控页修改并发配置；
- 不提供暂停、继续、取消任务或重新调度；
- 不引入跨 worker 聚合；
- 不持久化监控历史；
- 不在第一版引入 SSE、Redis、Prometheus 或新的监控数据库。

## 10. 实施顺序

1. 为 utils.concurrency 增加可观测 limiter 和稳定快照 registry。
2. 为 global limiter 接入业务上下文和等待样本。
3. 为 task limiter 增加实例登记与实例级快照。
4. 新增 /runtime/concurrency 只读接口及测试。
5. 将 demo 拆入现有 SPA 页面、脚本和命名空间 CSS。
6. 接入叶子图标入口、轮询生命周期和详情抽屉。
7. 执行后端测试、前端交互测试和三种视口验收。
