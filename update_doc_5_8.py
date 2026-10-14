import re

# 读取文档
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"原文档长度: {len(content)} 字符")

# ========== 步骤 3: 添加 5.8 节内容 ==========
section_5_8 = """
### 5.8 `GET /analysis/tasks/{task_id}` 查询独立分析任务

查询 `/analysis/run` 异步模式创建的分析任务状态和结果。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/analysis/tasks/{task_id}` |
| 请求体 | 无 |
| 响应 | `AnalysisTaskQueryResponse` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `task_id` | string | 是 | 任务 ID，由 `/analysis/run` 返回 |

**说明：**

- 任务状态：`queued` / `analyzing` / `complete` / `analysis_failed`
- 所有 async 任务均保存状态及完整结果，与 `callback_url` 和 `persist` 无关
- 已完成结果在服务重启后保留，中断任务会标记为失败
- 任务记录暂不自动过期
- `sync/stream` 模式和功能上线前的任务编号不可查询，返回 404

请求示例：

```bash
curl "http://localhost:5019/analysis/tasks/task_abc123"
```

响应示例（进行中）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "task_abc123",
    "status": "analyzing",
    "result": null,
    "error": null,
    "created_at": "2026-10-14T10:30:00",
    "updated_at": "2026-10-14T10:30:05"
  }
}
```

响应示例（完成）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "task_abc123",
    "status": "complete",
    "result": {
      "total": 3,
      "succeeded": 3,
      "failed": 0,
      "results": [
        {
          "rule_id": "RULE_001",
          "rule_name": "金额合规性",
          "result": true,
          "reason": "金额在允许范围内",
          "success": true
        }
      ]
    },
    "error": null,
    "created_at": "2026-10-14T10:30:00",
    "updated_at": "2026-10-14T10:30:08"
  }
}
```

---
"""

# 在 "## 6. 文档类型接口" 之前插入
pattern = r'(---\s*\n)(## 6\. 文档类型接口 `/doctype`)'
content = re.sub(pattern, r'\1' + section_5_8 + r'\n\2', content)

print("已添加 5.8 节")

# 保存
with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print(f"更新后文档长度: {len(content)} 字符")
print("步骤3完成: 已添加 5.8 节")
