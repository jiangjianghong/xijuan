import re
from pathlib import Path

# 从代码中提取所有路由，同时记录文件名（用于推断前缀）
routes_with_file = []

prefix_map = {
    'file_router.py': '/file',
    'extraction_router.py': '/extraction',
    'analysis_router.py': '/analysis',
    'doctype_router.py': '/doctype',
    'search_router.py': '',  # search 没有前缀
    'log_router.py': '/log',
    'settings_router.py': '/settings',
    'runtime_router.py': '/runtime',
    'invocation_router.py': '/invocations',
    'api_reference_router.py': '/api'  # 这个可能是新的
}

for file in Path('blue_print').rglob('*.py'):
    if file.name == '__init__.py':
        continue
    content = file.read_text(encoding='utf-8')
    pattern = r'@router\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']'
    matches = re.findall(pattern, content)
    
    prefix = prefix_map.get(file.name, '')
    for method, path in matches:
        full_path = prefix + path
        routes_with_file.append((method.upper(), full_path, file.name))

routes_with_file.sort(key=lambda x: (x[1], x[0]))

# 从文档中提取所有路由
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    doc_content = f.read()

doc_pattern = r'### \d+\.\d+ `(GET|POST|PUT|DELETE|PATCH) ([^`]+)`'
routes_from_doc = set(re.findall(doc_pattern, doc_content))

print(f"Total routes in code: {len(routes_with_file)}")
print(f"Total routes in doc: {len(routes_from_doc)}")
print()

# 找出代码中有但文档中没有的
missing = []
for method, path, filename in routes_with_file:
    if (method, path) not in routes_from_doc:
        missing.append((method, path, filename))

if missing:
    print(f"Missing {len(missing)} route(s) in documentation:")
    for method, path, filename in missing:
        print(f"  {method:6} {path:50} (from {filename})")
else:
    print("All routes are documented!")
