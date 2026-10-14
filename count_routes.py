import re
from pathlib import Path

# 统计所有路由装饰器
router_pattern = r'@router\.(get|post|put|delete|patch)\('
total = 0

for file in Path('blue_print').rglob('*.py'):
    content = file.read_text(encoding='utf-8')
    matches = re.findall(router_pattern, content)
    if matches:
        print(f"{file.name}: {len(matches)} routes")
        total += len(matches)

print(f"\nTotal routes: {total}")
