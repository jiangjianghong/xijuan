import re

# 读取文档
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"原文档长度: {len(content)} 字符")

# ========== 步骤 2: 更新目录 ==========

# 1. 在 5.7 之后添加 5.8
old_toc = '  - [5.7 `POST /analysis/test/stream` 逻辑分析流式调试](#57-post-analysisteststream-逻辑分析流式调试)'
new_toc = '''  - [5.7 `POST /analysis/test/stream` 逻辑分析流式调试](#57-post-analysisteststream-逻辑分析流式调试)
  - [5.8 `GET /analysis/tasks/{task_id}` 查询独立分析任务](#58-get-analysistaskstask_id-查询独立分析任务)'''
content = content.replace(old_toc, new_toc)

# 2. 在 6.8 之后添加 6.9-6.12，并更新后续编号
old_toc = '  - [6.8 `POST /doctype/import` 从 JSON 载荷导入配置](#68-post-doctypeimport-从-json-载荷导入配置)'
new_toc = '''  - [6.8 `POST /doctype/import` 从 JSON 载荷导入配置](#68-post-doctypeimport-从-json-载荷导入配置)
  - [6.9 `GET /doctype/{type_id}/params` 查询类型入参定义](#69-get-doctypetype_idparams-查询类型入参定义)
  - [6.10 `POST /doctype/{type_id}/params` 新增/更新类型入参](#610-post-doctypetype_idparams-新增更新类型入参)
  - [6.11 `DELETE /doctype/{type_id}/params/{param_key}` 删除类型入参](#611-delete-doctypetype_idparamsparam_key-删除类型入参)
  - [6.12 `GET /doctype/{type_id}/config_version` 查询配置版本](#612-get-doctypetype_idconfig_version-查询配置版本)'''
content = content.replace(old_toc, new_toc)

# 更新原 6.9-6.14 的编号为 6.13-6.18
content = content.replace(
    '  - [6.9 `GET /doctype/projects` 查询项目列表](#69-get-doctypeprojects-查询项目列表)',
    '  - [6.13 `GET /doctype/projects` 查询项目列表](#613-get-doctypeprojects-查询项目列表)'
)
content = content.replace(
    '  - [6.10 `POST /doctype/projects` 新增/更新项目](#610-post-doctypeprojects-新增更新项目)',
    '  - [6.14 `POST /doctype/projects` 新增/更新项目](#614-post-doctypeprojects-新增更新项目)'
)
content = content.replace(
    '  - [6.11 `DELETE /doctype/projects/{project_id}` 删除项目](#611-delete-doctypeprojectsproject_id-删除项目)',
    '  - [6.15 `DELETE /doctype/projects/{project_id}` 删除项目](#615-delete-doctypeprojectsproject_id-删除项目)'
)
content = content.replace(
    '  - [6.12 `POST /doctype/batch_assign_project` 批量归类类型到项目](#612-post-doctypebatch_assign_project-批量归类类型到项目)',
    '  - [6.16 `POST /doctype/batch_assign_project` 批量归类类型到项目](#616-post-doctypebatch_assign_project-批量归类类型到项目)'
)
content = content.replace(
    '  - [6.13 `POST /doctype/{type_id}/promote` 标记为模板](#613-post-doctypetype_idpromote-标记为模板)',
    '  - [6.17 `POST /doctype/{type_id}/promote` 标记为模板](#617-post-doctypetype_idpromote-标记为模板)'
)
content = content.replace(
    '  - [6.14 `POST /doctype/{type_id}/demote` 取消模板标记](#614-post-doctypetype_iddemote-取消模板标记)',
    '  - [6.18 `POST /doctype/{type_id}/demote` 取消模板标记](#618-post-doctypetype_iddemote-取消模板标记)'
)

# 3. 在第 7 章之前添加新的章节目录
old_toc = '- [7. 向量检索接口 `/search`](#7-向量检索接口-search)'
new_toc = '''- [7. 向量检索接口 `/search`](#7-向量检索接口-search)
- [8. 系统设置接口 `/settings`](#8-系统设置接口-settings)
  - [8.1 `POST /settings/login` 管理员登录](#81-post-settingslogin-管理员登录)
  - [8.2 `GET /settings/session` 查询会话状态](#82-get-settingssession-查询会话状态)
  - [8.3 `GET /settings/config` 读取运行时配置](#83-get-settingsconfig-读取运行时配置)
  - [8.4 `PATCH /settings/config` 更新运行时配置](#84-patch-settingsconfig-更新运行时配置)
  - [8.5 `POST /settings/logout` 退出登录](#85-post-settingslogout-退出登录)
- [9. 运行时监控接口 `/runtime`](#9-运行时监控接口-runtime)
  - [9.1 `GET /runtime/concurrency` 查询并发池状态](#91-get-runtimeconcurrency-查询并发池状态)
- [10. 调用日志接口 `/invocations`](#10-调用日志接口-invocations)
  - [10.1 `GET /invocations` 分页查询调用日志](#101-get-invocations-分页查询调用日志)
  - [10.2 `GET /invocations/{log_id}` 查询调用详情](#102-get-invocationslog_id-查询调用详情)
  - [10.3 `GET /invocations/{log_id}/result.json` 下载调用结果](#103-get-invocationslog_idresultjson-下载调用结果)'''
content = content.replace(old_toc, new_toc)

# 4. 更新原 8-12 章的编号为 11-15 章
content = content.replace('- [8. 日志接口 `/log`](#8-日志接口-log)', '- [11. 日志接口 `/log`](#11-日志接口-log)')
content = content.replace('- [9. 异步回调契约](#9-异步回调契约)', '- [12. 异步回调契约](#12-异步回调契约)')
content = content.replace('- [10. SSE 流式事件契约](#10-sse-流式事件契约)', '- [13. SSE 流式事件契约](#13-sse-流式事件契约)')
content = content.replace('- [11. 枚举与关键结构速查](#11-枚举与关键结构速查)', '- [14. 枚举与关键结构速查](#14-枚举与关键结构速查)')
content = content.replace('  - [11.1 枚举](#111-枚举)', '  - [14.1 枚举](#141-枚举)')
content = content.replace('  - [11.2 source_refs 结构要点](#112-source_refs-结构要点)', '  - [14.2 source_refs 结构要点](#142-source_refs-结构要点)')
content = content.replace('  - [11.3 pages 与 source_pages](#113-pages-与-source_pages)', '  - [14.3 pages 与 source_pages](#143-pages-与-source_pages)')
content = content.replace('  - [11.4 占位符规则](#114-占位符规则)', '  - [14.4 占位符规则](#144-占位符规则)')
content = content.replace('- [12. 附录](#12-附录)', '- [15. 附录](#15-附录)')

print("已更新目录")

# 保存
with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print(f"更新后文档长度: {len(content)} 字符")
print("步骤2完成: 已更新目录")
