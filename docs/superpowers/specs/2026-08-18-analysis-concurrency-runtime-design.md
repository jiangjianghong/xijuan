# 逻辑分析并发分层与运行台说明设计

## 1. 背景

当前并发配置和运行台将 `task_analysis` 表示为“单请求分析”。它实际限制一次独立分析批量请求内部同时执行的 item 数，与同组的“单文件表名”“单文件抽取”不属于同一条文件管线，命名和分组容易让用户误解。

文件管线本身存在真正的单文件逻辑分析：一个 PDF 会执行多条 judge、calc 或 custom 规则。但当前规则按顺序执行，只进入 `global_analysis`，没有文件实例级的 limiter 和运行台观测项。

本次调整的目标是：

- 为文件管线增加真正生效的单文件逻辑分析并发；
- 将独立分析并发改成所有 `/analysis/run` 请求共享的全局限制，而不是每请求一份；
- 保留文件分析和独立分析共享的总并发上限；
- 重新组织运行台分组，区分文件内任务和独立接口；
- 在运行台右上角增加静态并发池说明弹窗；
- 保持运行台只读，不在运行台内修改设置或控制任务。

## 2. 并发模型

### 2.1 配置键

规范配置调整为：

```yaml
concurrency:
  task_file_analysis: 4
  independent_analysis: 4
  global_analysis: 8
```

三个配置的单位和作用域不同：

| 配置键 | 单位 | 作用域 | 默认值 |
|---|---|---|---:|
| `task_file_analysis` | 分析规则 | 单个 PDF 文件 | 4 |
| `independent_analysis` | 独立分析 item | 当前 worker 内所有 `/analysis/run` 请求共享 | 4 |
| `global_analysis` | 正在执行的分析规则 | 当前 worker 内文件分析与独立分析共享 | 8 |

约束关系为：

```text
文件管线规则
task_file_analysis（按 file_id 建立实例）
        -> global_analysis（当前 worker 总池）
        -> judge/custom 实际模型请求继续受 global_llm 约束

独立分析 item
independent_analysis（所有请求共享一个全局池）
        -> item 内每条规则进入 global_analysis
        -> judge/custom 实际模型请求继续受 global_llm 约束
```

### 2.2 明确移除单请求并发

独立分析不再为每次 `run_analysis_batch()` 注册局部 semaphore。多个 HTTP 请求提交的所有 item 共同竞争同一个 `independent_analysis` limiter。

例如三个请求分别提交 10 个 item，`independent_analysis=4` 时，三个请求合计最多只有 4 个 item 正在执行，不是每个请求各运行 4 个。

每个 item 内的规则继续按 `priority, rule_id` 顺序执行。`independent_analysis` 控制 item 数，`global_analysis` 控制正在执行的规则数。

### 2.3 文件内规则并发

同一文件的分析规则只依赖已经落库的字段提取结果，不依赖其他分析规则的输出，因此允许并行计算。文件级 limiter 使用 `file_id` 作为实例 ID，每个文件最多同时执行 `task_file_analysis` 条规则。

不能在并发任务中共享 SQLAlchemy `AsyncSession`。文件分析拆成两个阶段：

1. 使用原会话一次性读取规则、字段值和溯源数据，生成不可变快照；
2. 并发任务只执行校验、表达式解析、网络搜索、calc 或模型调用，不读写数据库；
3. 每条规则先取得文件级 `task_file_analysis` 槽位，再取得共享 `global_analysis` 槽位；
4. 计算结果按原规则顺序消费；
5. 使用原会话顺序 upsert、commit、回调或 yield；
6. 单条规则失败生成失败结果，不取消同文件其他规则；
7. 文件完成或外层异常时，在 `finally` 中注销文件 limiter，并收敛所有已创建任务。

普通和流式文件分析复用同一套纯计算函数，避免两条路径产生不同并发语义。并发计算可以先完成，但数据库结果、回调和流式事件仍按规则配置顺序对外出现。

### 2.4 全局总池口径

`global_analysis` 统一包围一次完整规则执行，而不只包围最终 LLM 调用。judge、calc、custom 和启用网络搜索的规则使用同一计数单位。实际文本模型 HTTP 请求仍会进一步取得 `global_llm` 槽位。

这样 `global_analysis=8` 才能稳定表示：当前 worker 内，文件管线和独立接口合计最多同时执行 8 条逻辑分析规则。

## 3. 配置切换与设置界面

### 3.1 配置切换

本次不兼容旧并发键，配置模型一次性切换为规范字段：

- 从 `ConcurrencyConfig` 删除 `task_analysis`；
- 从 `AnalysisConfig` 删除旧 `max_concurrency`；
- `task_file_analysis` 缺失时使用默认值 4；
- `independent_analysis` 缺失时使用默认值 4；
- 不再从 `task_analysis` 或 `analysis.max_concurrency` 推导任何新值；
- 项目自带 `configs/config.yaml` 直接删除旧键并写入两个新键；
- 设置接口只接受和返回规范字段，提交旧字段时返回“不允许修改的配置字段”。

### 3.2 设置字段

“设置 -> 模型并发”显示：

```text
全局逻辑分析总并发        concurrency.global_analysis
单文件逻辑分析并发        concurrency.task_file_analysis
独立逻辑分析并发          concurrency.independent_analysis
```

“独立逻辑分析并发”的帮助语义是“当前 worker 内所有独立分析请求合计同时执行的 item 数”，不得写成“单请求”或“单任务”。

保存配置后，`replace_limiters()` 更新两个全局稳定 limiter：`global_analysis` 和 `independent_analysis`。已存在的文件实例 limiter 在后续获取时按新 `task_file_analysis` 容量更新，正在执行的 lease 必须保持 acquire/release 配对。

## 4. 运行台数据与分组

### 4.1 池定义

运行台容量矩阵按以下固定顺序展示：

```text
模型通道
- global_llm              文本 LLM
- global_embedding        Embedding
- global_vl               VL 视觉

业务阶段
- global_table_validation 表名校验
- global_extraction       字段抽取
- global_analysis         逻辑分析总池

文件内任务
- task_table_validation   文件内表名校验
- task_extraction         文件内字段抽取
- task_file_analysis      文件内逻辑分析

独立接口
- independent_analysis    独立分析

管线
- global_pipeline         文件管线
```

`independent_analysis` 是 worker 级共享池，API 中 `scope=global`，但视觉分组为“独立接口”。`task_file_analysis` 使用 task 实例口径：柱图显示最繁忙文件的 active/per-instance limit，详情展示实例数、累计 active/queued 和文件实例列表。

前端使用固定池 ID 顺序排列，不依赖 API 数组恰好按视觉顺序返回。容量矩阵分组比例由原来的 `3:3:3:1` 调整为 `3:3:3:1:1`，移动端使用短标签且不得恢复横向滚动条。

### 4.2 约束路径

- `task_file_analysis -> global_analysis`；judge/custom 规则还会进入 `global_llm`；
- `independent_analysis -> global_analysis`；judge/custom 规则还会进入 `global_llm`；
- `global_analysis -> global_llm` 是条件路径，calc 不占用文本模型通道；
- `global_pipeline` 继续明确显示“未接入”。

## 5. 静态说明弹窗

### 5.1 入口与交互

运行台右上角在刷新按钮左侧增加 Lucide `circle-help` 图标按钮，`title` 和 `aria-label` 为“查看并发池说明”。点击后打开居中弹窗。

弹窗要求：

- 标题为“并发池说明”，副标题说明“默认配置示例 · 当前 worker · 只读监控”；
- 按“模型通道、业务阶段、文件内任务、独立接口、管线”分组；
- 每项包含名称、配置键、统计对象、约束关系和例子；
- 内容可以滚动，但页面背景不滚动；
- 支持关闭图标、遮罩点击、Escape；
- 打开后焦点进入关闭按钮，关闭后回到说明按钮；
- 复用当前圆润视觉语言，不使用嵌套卡片。

### 5.2 数字口径

说明文案完全静态，不从运行快照或设置接口动态拼接数字。所有数字使用“以默认配置为例”的措辞。修改运行设置后，容量矩阵继续显示真实值，但说明弹窗仍保留默认配置示例。

关键静态说明至少包括：

**文件内表名校验**

> 一个 PDF 里可能有很多张表，需要逐张调用 LLM 判断表名。以默认配置为例，每个文件最多同时校验 4 张表；所有文件合计又受“表名校验”全局上限 10 和“文本 LLM”上限 16 约束。文件 A 运行 4 个、文件 B 运行 4 个、文件 C 运行 2 个时，全局表名校验池已经达到 10，其他表格需要等待。

**文件内字段抽取**

> 一个 PDF 可以配置多个抽取字段。默认配置中的文件级上限是 4，但当前字段循环仍按顺序执行，所以单文件真实观测并发通常为 1；多个文件同时抽取时，共同受“字段抽取”全局上限 8 以及文本 LLM、Embedding、VL 模型通道约束。

**文件内逻辑分析**

> 一个 PDF 可以配置多条判断、计算或自定义规则。以默认配置为例，每个文件最多同时执行 4 条规则；不同文件继续共同竞争“逻辑分析总池”。当两个文件各运行 4 条规则时，默认总上限 8 已被占满，其他文件或独立分析规则需要等待。

**独立分析**

> 独立分析统计所有 `/analysis/run` 请求正在处理的 item，不限制单个请求。以默认配置为例，当前 worker 内所有独立分析请求合计最多同时处理 4 个 item。每个 item 内的规则仍按顺序执行，每条规则继续受“逻辑分析总池”默认上限 8 约束。

**逻辑分析总池**

> 文件管线规则与独立分析规则共享同一个总池。以默认配置为例，两类来源合计最多同时执行 8 条规则。judge 和 custom 规则还需要文本 LLM 槽位，calc 规则不占用文本模型通道。

其余模型通道和业务阶段沿用同样详细度，说明“统计什么、默认容量、与哪些池嵌套”，不得只显示一句抽象定义。

## 6. 错误处理与生命周期

- limiter 获取和释放统一使用异步上下文，异常时不得泄漏 active/queued；
- 文件规则任务之一失败只返回该规则失败，不中断批次；
- 外层任务被取消时取消并 await 尚未收敛的规则任务；
- `task_file_analysis` 必须在 `finally` 中注销，避免完成文件残留实例；
- 独立分析不再注册请求实例，因此运行快照中不会残留旧 `task_analysis`；
- 运行台请求失败继续保留最后成功快照，说明弹窗不依赖 API，离线时仍可打开；
- 页面离开时同时关闭详情抽屉和说明弹窗，恢复 body 滚动并清理焦点状态。

## 7. 测试与验收

### 7.1 配置与设置

- 新配置缺失时使用 4/4/8 默认值；
- 旧 `task_analysis` 和 `analysis.max_concurrency` 不影响新字段取值；
- 设置接口拒绝提交旧字段；
- 设置读取只返回规范键；
- 设置保存移除旧键并热更新稳定 limiter；
- 设置界面显示三个准确名称。

### 7.2 文件分析

- 单文件规则峰值不超过 `task_file_analysis`；
- 两个文件各自拥有独立文件级容量；
- 并发计算阶段不共享 `AsyncSession`；
- 数据库写入、回调和流式事件保持规则配置顺序；
- 单规则失败不影响其他规则；
- 正常、异常和取消路径都注销文件实例。

### 7.3 独立分析与总池

- 两个或更多并发 `/analysis/run` 批次共同受一个 `independent_analysis` 上限约束；
- 不再出现每请求各自拥有 4 个槽位的行为；
- item 内规则保持顺序；
- 文件规则和独立规则同时运行时，合计峰值不超过 `global_analysis`；
- judge/custom 仍受 `global_llm` 约束。

### 7.4 运行台前端

- API 返回 `task_file_analysis` 和 `independent_analysis`，不再返回 `task_analysis`；
- 矩阵按五个视觉分组固定排序；
- 详情抽屉正确区分 task 实例池和 global 独立接口池；
- 说明按钮、关闭按钮、遮罩和 Escape 行为正确；
- 弹窗文案为静态默认配置说明，不随快照数字变化；
- 1440x900、1280x720 和 500x900 下无横向滚动、文字重叠或遮挡；
- Node 测试、完整 pytest 和浏览器截图检查全部通过。

## 8. 不在本次范围

- 不在运行台修改任何并发设置；
- 不提供暂停、取消、抢占或调整任务优先级；
- 不增加单请求独立分析并发限制；
- 不让独立分析请求各自拥有一份 `independent_analysis`；
- 不把静态说明数字改为实时数字；
- 不并行执行单个独立分析 item 内的规则；
- 不将字段抽取改为并发；
- 不接入 `global_pipeline` 调度；
- 不增加跨 worker 聚合或持久化监控历史。
