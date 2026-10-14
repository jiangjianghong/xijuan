import re

with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

# 检查接口总数
count_match = re.search(r'共 (\d+) 个', content)
if count_match:
    count = int(count_match.group(1))
    print(f"[OK] Interface count: {count}")
else:
    print("[FAIL] Interface count not found")

# 统计实际接口文档数量
api_pattern = r'^### (\d+\.\d+) `(GET|POST|PUT|DELETE|PATCH) ([^`]+)`'
apis = re.findall(api_pattern, content, re.MULTILINE)
print(f"[OK] Actual API docs: {len(apis)}")

# 检查新增章节
new_sections = [
    (r'### 3\.20 `GET /file/stats`', '3.20 GET /file/stats'),
    (r'### 5\.8 `GET /analysis/tasks/\{task_id\}`', '5.8 GET /analysis/tasks/{task_id}'),
    (r'### 6\.9 `GET /doctype/\{type_id\}/params`', '6.9 GET /doctype/{type_id}/params'),
    (r'### 6\.10 `POST /doctype/\{type_id\}/params`', '6.10 POST /doctype/{type_id}/params'),
    (r'### 6\.11 `DELETE /doctype/\{type_id\}/params/\{param_key\}`', '6.11 DELETE /doctype/{type_id}/params/{param_key}'),
    (r'### 6\.12 `GET /doctype/\{type_id\}/config_version`', '6.12 GET /doctype/{type_id}/config_version'),
    (r'## 8\. 系统设置接口', '8. 系统设置接口'),
    (r'### 8\.1 `POST /settings/login`', '8.1 POST /settings/login'),
    (r'### 8\.2 `GET /settings/session`', '8.2 GET /settings/session'),
    (r'### 8\.3 `GET /settings/config`', '8.3 GET /settings/config'),
    (r'### 8\.4 `PATCH /settings/config`', '8.4 PATCH /settings/config'),
    (r'### 8\.5 `POST /settings/logout`', '8.5 POST /settings/logout'),
    (r'## 9\. 运行时监控接口', '9. 运行时监控接口'),
    (r'### 9\.1 `GET /runtime/concurrency`', '9.1 GET /runtime/concurrency'),
    (r'## 10\. 调用日志接口', '10. 调用日志接口'),
    (r'### 10\.1 `GET /invocations`', '10.1 GET /invocations'),
    (r'### 10\.2 `GET /invocations/\{log_id\}`', '10.2 GET /invocations/{log_id}'),
    (r'### 10\.3 `GET /invocations/\{log_id\}/result\.json`', '10.3 GET /invocations/{log_id}/result.json'),
]

print("\n=== New Sections ===")
found = 0
for pattern, name in new_sections:
    if re.search(pattern, content):
        print(f"[OK] {name}")
        found += 1
    else:
        print(f"[MISS] {name}")
print(f"Found: {found}/{len(new_sections)}")

# 检查重命名的章节
renamed_chapters = [
    (r'## 11\. 日志接口', '11. 日志接口'),
    (r'## 12\. 异步回调契约', '12. 异步回调契约'),
    (r'## 13\. SSE 流式事件契约', '13. SSE 流式事件契约'),
    (r'## 14\. 枚举及关键结构速查', '14. 枚举及关键结构速查'),
    (r'## 15\. 变更日志', '15. 变更日志'),
]

print("\n=== Renamed Chapters ===")
found = 0
for pattern, name in renamed_chapters:
    if re.search(pattern, content):
        print(f"[OK] {name}")
        found += 1
    else:
        print(f"[MISS] {name}")
print(f"Found: {found}/{len(renamed_chapters)}")

# 统计
chapters = len(re.findall(r'^## \d+\.', content, re.MULTILINE))
print(f"\n=== Summary ===")
print(f"Declared API count: {count if count_match else 'N/A'}")
print(f"Actual API docs: {len(apis)}")
print(f"Total chapters: {chapters}")
print(f"Document size: {len(content)} chars")

if count_match and len(apis) == count:
    print("\n✓ 文档更新完成！所有接口已正确记录。")
else:
    print(f"\n⚠ 接口数量不匹配：声明 {count if count_match else 'N/A'}，实际 {len(apis)}")
