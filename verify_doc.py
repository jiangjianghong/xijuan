# 验证文档更新完整性
import re

with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

print("=== Verification ===\n")

# 1. 检查接口总数
if '67' in content and 'REST API' in content:
    print("[OK] Interface count updated to 67")
else:
    print("[FAIL] Interface count not updated")

# 2. 检查新增的章节
new_sections = [
    '### 3.20 `GET /file/stats`',
    '### 5.8 `GET /analysis/tasks/{task_id}`',
    '### 6.9 `GET /doctype/{type_id}/params`',
    '### 6.10 `POST /doctype/{type_id}/params`',
    '### 6.11 `DELETE /doctype/{type_id}/params/{param_key}`',
    '### 6.12 `GET /doctype/{type_id}/config_version`',
    '## 8. 系统设置接口',
    '### 8.1 `POST /settings/login`',
    '### 8.2 `GET /settings/session`',
    '### 8.3 `GET /settings/config`',
    '### 8.4 `PATCH /settings/config`',
    '### 8.5 `POST /settings/logout`',
    '## 9. 运行时监控接口',
    '### 9.1 `GET /runtime/concurrency`',
    '## 10. 调用日志接口',
    '### 10.1 `GET /invocations`',
    '### 10.2 `GET /invocations/{log_id}`',
    '### 10.3 `GET /invocations/{log_id}/result.json`',
]

print("\n=== New Sections ===")
found = 0
for section in new_sections:
    if section in content:
        found += 1
        print(f"[OK] {section}")
    else:
        print(f"[MISS] {section}")

print(f"\nFound: {found}/{len(new_sections)}")

# 3. 检查章节重编号
renamed = [
    '## 11. 日志接口',
    '## 12. 异步回调契约',
    '## 13. SSE 流式事件契约',
    '## 14. 枚举与关键结构速查',
    '## 15. 附录',
]

print("\n=== Renamed Chapters ===")
found2 = 0
for section in renamed:
    if section in content:
        found2 += 1
        print(f"[OK] {section}")
    else:
        print(f"[MISS] {section}")

print(f"\nFound: {found2}/{len(renamed)}")

# 4. 统计
chapter_pattern = r'^## \d+\.'
chapters = re.findall(chapter_pattern, content, re.MULTILINE)
print(f"\n=== Statistics ===")
print(f"Total chapters: {len(chapters)}")

api_pattern = r'^### \d+\.\d+ `(GET|POST|PUT|DELETE|PATCH)'
apis = re.findall(api_pattern, content, re.MULTILINE)
print(f"Total API docs: {len(apis)}")

print(f"\nDocument size: {len(content)} chars")
print("\n=== Update Complete ===")
