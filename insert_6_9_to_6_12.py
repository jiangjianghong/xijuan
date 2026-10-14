import re

# 读取文档
with open('docs/API_FULL_REFERENCE.md', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"原文档长度: {len(content)} 字符")

# ========== 重新插入 6.9-6.12 ==========
sections_6_9_to_6_12 = """
### 6.9 `GET /doctype/{type_id}/params` 查询类型入参定义

查询文档类型的入参定义列表。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/doctype/{type_id}/params` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<TypeParamItem[]>` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 文档类型 ID |

**说明：**

- 入参定义用于在上传文件时传递动态参数
- 字段和规则配置中可使用 `<param>参数标识</param>` 占位符引用参数值
- 按 `priority` 升序、`param_key` 排序返回

请求示例：

```bash
curl "http://localhost:5019/doctype/contract/params"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "param_key": "contract_type",
      "param_name": "合同类型",
      "description": "用于区分采购合同、销售合同等",
      "default_value": "采购",
      "required": 1,
      "priority": 1
    }
  ]
}
```

---

### 6.10 `POST /doctype/{type_id}/params` 新增/更新类型入参

按 `param_key` 新增或更新一条入参定义。

| 项 | 说明 |
|---|---|
| 方法 | `POST` |
| 路径 | `/doctype/{type_id}/params` |
| 请求体 | `TypeParamUpsert` |
| 响应 | `ResponseWrapper<TypeParamItem>` |
| 副作用 | 插入或更新 `type_param` 表记录 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 文档类型 ID |

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `param_key` | string | 是 | 参数标识，字母数字下划线，1-64字符 |
| `param_name` | string | 是 | 参数显示名称 |
| `description` | string | 否 | 参数说明 |
| `default_value` | string | 否 | 默认值 |
| `required` | integer | 否 | 是否必填，0/1，默认0 |
| `priority` | integer | 否 | 排序优先级，默认0 |

**说明：**

- 如果 `param_key` 已存在，则更新；否则新增
- `param_key` 只能包含字母、数字、下划线
- 类型不存在返回 404

请求示例：

```bash
curl -X POST "http://localhost:5019/doctype/contract/params" \
  -H "Content-Type: application/json" \
  -d '{
    "param_key": "supplier_name",
    "param_name": "供应商名称",
    "required": 0,
    "priority": 3
  }'
```

---

### 6.11 `DELETE /doctype/{type_id}/params/{param_key}` 删除类型入参

删除一条入参定义。

| 项 | 说明 |
|---|---|
| 方法 | `DELETE` |
| 路径 | `/doctype/{type_id}/params/{param_key}` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper` |
| 副作用 | 删除 `type_param` 表记录 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 文档类型 ID |
| `param_key` | string | 是 | 参数标识 |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| `force` | boolean | 否 | `false` | 是否强制删除（即使被引用） |

**说明：**

- 如果参数仍被字段或规则引用，且 `force=false`，返回 409
- `force=true` 强制删除，引用处的占位符会渲染为空字符串
- 入参不存在返回 404

请求示例：

```bash
curl -X DELETE "http://localhost:5019/doctype/contract/params/supplier_name?force=true"
```

---

### 6.12 `GET /doctype/{type_id}/config_version` 查询配置版本

查询类型配置的当前版本标识（基于字段和规则的最后更新时间生成）。

| 项 | 说明 |
|---|---|
| 方法 | `GET` |
| 路径 | `/doctype/{type_id}/config_version` |
| 请求体 | 无 |
| 响应 | `ResponseWrapper<ConfigVersionResponse>` |
| 副作用 | 无 |

路径参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|:--:|---|
| `type_id` | string | 是 | 文档类型 ID |

**说明：**

- 版本标识由字段和规则的最后更新时间戳生成（Unix毫秒）
- 用于前端判断配置是否变更，决定是否重新加载
- 类型不存在返回 404

请求示例：

```bash
curl "http://localhost:5019/doctype/contract/config_version"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "type_id": "contract",
    "version": "1728892345678",
    "field_count": 15,
    "rule_count": 8,
    "last_updated": "2026-10-14T15:32:25"
  }
}
```

---

"""

# 找到 6.8 结束（在 "| `404` | 目标类型不存在" 之后的空行）并插入
# 直接在 "### 6.13" 之前插入
pattern = r'(\| `422` \| 导入载荷结构不符合 Pydantic schema \|\s*\n)'
replacement = r'\1' + sections_6_9_to_6_12 + '\n'
content = re.sub(pattern, replacement, content)

print("已插入 6.9-6.12 节")

# 保存
with open('docs/API_FULL_REFERENCE.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print(f"更新后文档长度: {len(content)} 字符")
print("步骤7完成: 已插入 6.9-6.12 节")
