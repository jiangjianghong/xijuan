import re
from pathlib import Path

# 从代码中提取所有路由
routes_from_code = []

for file in Path('blue_print').rglob('*.py'):
    content = file.read_text(encoding='utf-8')
    # 匹配路由装饰器和路径
    pattern = r'@router\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']'
    matches = re.findall(pattern, content)
    for method, path in matches:
        routes_from_code.append(f"{method.upper()} {path}")

routes_from_code.sort()

# 从文档中提取所有路由
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    doc_content = f.read()

doc_pattern = r'### \d+\.\d+ `(GET|POST|PUT|DELETE|PATCH) ([^`]+)`'
routes_from_doc = re.findall(doc_pattern, doc_content)
routes_from_doc = [f"{method} {path}" for method, path in routes_from_doc]
routes_from_doc.sort()

print(f"Routes in code: {len(routes_from_code)}")
print(f"Routes in doc: {len(routes_from_doc)}")
print()

# 找出代码中有但文档中没有的
missing_in_doc = set(routes_from_code) - set(routes_from_doc)
if missing_in_doc:
    print("Missing in documentation:")
    for route in sorted(missing_in_doc):
        print(f"  - {route}")
else:
    print("All routes are documented!")

print()

# 找出文档中有但代码中没有的
extra_in_doc = set(routes_from_doc) - set(routes_from_code)
if extra_in_doc:
    print("Extra in documentation:")
    for route in sorted(extra_in_doc):
        print(f"  - {route}")
