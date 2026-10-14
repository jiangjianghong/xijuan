# 读取文档
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"原文档总行数: {len(lines)}")

# 1. 在第 143 行后（3.19 之后）插入 3.20 的目录项
toc_line_3_20 = "  - [3.20 `GET /file/stats` 文件处理统计](#320-get-filestats-文件处理统计)\n"

# 找到 3.19 的目录行
for i, line in enumerate(lines):
    if "3.19 `GET /file/{file_id}/detail` 查询完整文件详情" in line:
        print(f"找到 3.19 目录项在第 {i+1} 行")
        lines.insert(i+1, toc_line_3_20)
        print(f"已在第 {i+2} 行插入 3.20 目录项")
        break

# 2. 在第 4 章之前插入 3.20 章节内容
section_3_20 = """
### 3.20 `GET /file/stats` 文件处理统计

处理统计（概览 KPI / 状态分布 / 类型排行 / 处理趋势 / 阶段耗时）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/file/stats` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<FileStatsResponse>` |
| 副作用 | 无 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `range` | string | 否 | `d30` | 时间窗口：`d7`/`d30`/`d90`/`all` |

**说明：**

- 不受顶部项目、文档类型选择器影响（统计全部类型）
- `range` 时间窗口按 `files.create_time` 过滤，**统一作用于全部 6 组指标**
- 共 4 条聚合查询（状态分布、类型×项目、趋势、阶段耗时），无 N+1

请求示例：

```bash
curl "http://localhost:5019/file/stats?range=d30"
```

---
"""

# 找到 "## 4. 字段提取接口" 之前插入
for i, line in enumerate(lines):
    if line.strip() == "## 4. 字段提取接口 `/extraction`":
        print(f"找到第 4 章在第 {i+1} 行")
        lines.insert(i, section_3_20)
        print(f"已在第 {i} 行插入 3.20 章节内容")
        break

# 保存
with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.writelines(lines)

print(f"更新后文档总行数: {len(lines)}")
print("✓ 已添加 3.20 节")
