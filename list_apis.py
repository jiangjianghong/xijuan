import re

with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

# 提取所有接口章节标题
api_pattern = r'^### (\d+\.\d+) `(GET|POST|PUT|DELETE|PATCH) ([^`]+)`'
apis = re.findall(api_pattern, content, re.MULTILINE)

print(f"Total API sections found: {len(apis)}")
print("\n=== API List ===")
for num, method, path in apis:
    print(f"{num} {method} {path}")
