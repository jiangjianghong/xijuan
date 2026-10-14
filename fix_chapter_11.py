import re

# 读取文档
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"原文档长度: {len(content)} 字符")

# ========== 更新第 11 章（日志接口）的子章节编号 ==========
# 从 8.1, 8.2, 8.3 改为 11.1, 11.2, 11.3
content = content.replace('### 8.1 `GET /log/files`', '### 11.1 `GET /log/files`')
content = content.replace('### 8.2 `GET /log/recent`', '### 11.2 `GET /log/recent`')
content = content.replace('### 8.3 `GET /log/stream`', '### 11.3 `GET /log/stream`')

print("已更新第 11 章的子章节编号")

# 保存
with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print(f"更新后文档长度: {len(content)} 字符")
print("步骤8完成: 已修复第 11 章的子章节编号")
