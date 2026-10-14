import re

with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

# 更新接口总数从 67 改为 66
content = re.sub(
    r'覆盖范围：当前源码注册的全部业务接口，共 67 个。',
    '覆盖范围：当前源码注册的全部业务接口，共 66 个。',
    content
)

with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print("已更新接口总数为 66")
