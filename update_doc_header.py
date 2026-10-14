import re

# 读取文档
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"原文档长度: {len(content)} 字符")

# ========== 步骤 6: 更新头部接口总数 ==========
# 找到并更新接口总数（从 51 改为 67）
old_header = '本文档详尽记录系统所有 REST API（共 51 个接口）'
new_header = '本文档详尽记录系统所有 REST API（共 67 个接口）'
content = content.replace(old_header, new_header)

print("已更新接口总数")

# 保存
with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print(f"更新后文档长度: {len(content)} 字符")
print("步骤6完成: 已更新接口总数")
