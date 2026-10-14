import re

# 读取文档
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"原文档长度: {len(content)} 字符")

# ========== 步骤 1: 更新接口总览表格 ==========
# 找到接口总览的分组统计表格并更新
old_table = r'\| `/file` \| 19 \| 文件上传、处理、重试、内容、结果、删除 \|'
new_table = '| `/file` | 20 | 文件上传、处理、重试、内容、结果、统计、删除 |'
content = content.replace(old_table, new_table)

old_table = r'| `/extraction` | 7 | 字段配置、默认提示词、同步/流式调试 |'
content = content.replace(old_table, old_table)  # 保持不变

old_table = r'| `/analysis` | 7 | 分析规则、同步/流式调试、独立分析执行 |'
new_table = '| `/analysis` | 8 | 分析规则、同步/流式调试、独立分析执行、任务查询 |'
content = content.replace(old_table, new_table)

old_table = r'| `/doctype` | 14 | 文档类型、复制、导入导出、模板、项目归类 |'
new_table = '| `/doctype` | 18 | 文档类型、参数管理、复制、导入导出、模板、项目归类 |'
content = content.replace(old_table, new_table)

# 在 /log 之前添加新的三个分组
old_log_line = '| `/log` | 3 | 日志读取和实时流 |'
new_lines = '''| `/settings` | 5 | 运行时配置管理、会话认证 |
| `/runtime` | 1 | 并发池状态监控 |
| `/invocations` | 3 | 调用日志查询和结果下载 |
| `/log` | 3 | 应用日志读取和实时流 |'''
content = content.replace(old_log_line, new_lines)

print("已更新接口总览分组表格")

# ========== 步骤 2: 在详细路由表格中添加新接口 ==========
# 找到 GET /file/{file_id}/detail 之后，GET /extraction/match-prompt-defaults 之前插入
old_line = '| GET | `/file/{file_id}/detail` | 查询完整文件详情 |'
new_line = '''| GET | `/file/{file_id}/detail` | 查询完整文件详情 |
| GET | `/file/stats` | 文件处理统计 |'''
content = content.replace(old_line, new_line)

# 在 POST /analysis/test/stream 之后添加 GET /analysis/tasks/{task_id}
old_line = '| POST | `/analysis/test/stream` | 逻辑分析流式调试 |'
new_line = '''| POST | `/analysis/test/stream` | 逻辑分析流式调试 |
| GET | `/analysis/tasks/{task_id}` | 查询独立分析任务 |'''
content = content.replace(old_line, new_line)

# 在 POST /doctype/import 之后添加参数管理接口
old_line = '| POST | `/doctype/import` | 导入类型配置 |'
new_line = '''| POST | `/doctype/import` | 导入类型配置 |
| GET | `/doctype/{type_id}/params` | 查询类型入参定义 |
| POST | `/doctype/{type_id}/params` | 新增/更新类型入参 |
| DELETE | `/doctype/{type_id}/params/{param_key}` | 删除类型入参 |
| GET | `/doctype/{type_id}/config_version` | 查询配置版本 |'''
content = content.replace(old_line, new_line)

# 在 POST /search 之后、GET /log/files 之前添加新章节的接口
old_line = '| POST | `/search` | 向量检索 |'
new_line = '''| POST | `/search` | 向量检索 |
| POST | `/settings/login` | 管理员登录 |
| GET | `/settings/session` | 查询会话状态 |
| GET | `/settings/config` | 读取运行时配置 |
| PATCH | `/settings/config` | 更新运行时配置 |
| POST | `/settings/logout` | 退出登录 |
| GET | `/runtime/concurrency` | 查询并发池状态 |
| GET | `/invocations` | 分页查询调用日志 |
| GET | `/invocations/{log_id}` | 查询调用详情 |
| GET | `/invocations/{log_id}/result.json` | 下载调用结果 |'''
content = content.replace(old_line, new_line)

print("已更新接口总览详细表格")

# 保存
with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print(f"更新后文档长度: {len(content)} 字符")
print("步骤1完成: 已更新接口总览")
