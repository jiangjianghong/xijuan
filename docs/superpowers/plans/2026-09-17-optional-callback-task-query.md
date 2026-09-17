# 可选回调与独立分析任务查询

目标：所有异步入口支持省略 callback_url；独立分析的异步结果可按 task_id 查询。

## 设计

- 文件解析与指定阶段重试沿用 file_id、现有状态/结果查询接口；回调地址保持可选。
- POST /analysis/run 的 async 分支先提交 analysis_task 记录，再调度后台任务并返回 task_id。
- GET /analysis/tasks/{task_id} 返回 ResponseWrapper，data 为 task_id、status、result、error、created_at、updated_at。不存在返回 HTTP 404。
- analysis_task 独立于 analysis_result；即使 persist=false 也保存异步任务的完整结果。persist 仍只控制 source=file 的文件级结果写入。
- queued → analyzing → complete / analysis_failed；终态先落库后发回调。回调缺省时不触发通知。单规则失败保留在结果中，不等于任务执行异常。
- 使用现有 MySQL/SQLAlchemy；启动自动建表。单 worker 启动恢复 queued/analyzing 为 analysis_failed，已完成结果保持。sync/stream 不创建可查询记录，旧 task_id 不可追溯。任务记录暂不自动过期。

## 实施与验证

- [x] 在 tests/test_analysis_task_store.py 验证持久化、失败、启动恢复；SQLite 真实表验证 SQL 行为，异步适配仅用于测试。
- [x] 在 tests/test_analysis_run_router.py 验证省略/传入回调、查询成功/不存在、queued/analyzing/失败、source=file persist 透传。
- [x] 新建 AnalysisTask ORM 及 service/analysis_task_store.py，路由接入创建/更新/读取；初始化接入恢复。
- [x] 新建指定阶段重试的 HTTP 回归测试，覆盖所有支持阶段及有/无回调。
- [x] 更新 Markdown 与 OpenAPI，运行相关 pytest、文档一致性检查及 git diff --check。

验证：60 项相关测试通过；docs sync OK。独立审查无阻断缺陷；补充了嵌套结果完整性和启动恢复调用测试。没有运行真实 MySQL/aiomysql 联调。
