import re
from pathlib import Path

# 从代码中提取所有路由
prefix_map = {
    'file_router.py': '/file',
    'extraction_router.py': '/extraction',
    'analysis_router.py': '/analysis',
    'doctype_router.py': '/doctype',
    'search_router.py': '',
    'log_router.py': '/log',
    'settings_router.py': '/settings',
    'runtime_router.py': '/runtime',
    'invocation_router.py': '/invocations',
    'api_reference_router.py': '/api'
}

routes_with_file = []

for file in Path('blue_print').rglob('*.py'):
    if file.name == '__init__.py':
        continue
    content = file.read_text(encoding='utf-8')
    # 更宽松的匹配：允许空字符串和各种参数
    pattern = r'@router\.(get|post|put|delete|patch)\(\s*["\']([^"\']*)["\']'
    matches = re.findall(pattern, content)
    
    prefix = prefix_map.get(file.name, '')
    for method, path in matches:
        # 空字符串表示根路径
        if path == '':
            full_path = prefix if prefix else '/'
        else:
            full_path = prefix + path
        routes_with_file.append((method.upper(), full_path, file.name))

routes_with_file.sort(key=lambda x: (x[1], x[0]))

print(f"Total routes in code: {len(routes_with_file)}")
print("\n=== All Routes ===")
for method, path, filename in routes_with_file:
    print(f"{method:6} {path:50} ({filename})")
