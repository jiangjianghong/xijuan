# 混合检索配置设计

## 目标

为字段提取增加可展开的组合检索配置。用户可以在同一字段中配置多条文本、表格和视觉通道，拖动调整顺序，并使用一个固定占位符引用组合结果。既有单路字段保持原行为。

## 配置模型

组合模式使用 `search_type: "hybrid"`，配置保存于 `search_config`：

```json
{
  "strategy": "union",
  "items": [
    {"id": "s1", "source_type": "text", "method": "chunk_db", "config": {}},
    {"id": "s2", "source_type": "table", "method": "table_match", "config": {}},
    {"id": "s3", "source_type": "vl", "method": "vl_locate", "config": {}}
  ]
}
```

`items` 数组顺序是唯一排序来源，决定回退优先级、合并顺序和去重优先级。每项使用稳定 `id`，复制或拖动不改变其他项的身份。

文本方法支持 `context`、`section`、`rule`、`chunk_db`、`vector_db`、`page`；表格项复用现有表格匹配和提取配置；VL 项复用现有 `vl_method`、`vl_config`、提示词配置。

## 执行语义

`union` 执行全部文本和表格项，按配置顺序收集结果，按原文位置或块 ID 去重，并统一格式化为证据文本。`fallback` 按顺序执行文本、表格和 VL；首个有效文本或表格结果即停止，VL 只在前序通道为空时直接返回最终 `{reason,value,pages}`，不再调用文本 LLM。

VL 出现在 `union` 时保存校验失败。`fallback` 中最多允许一个 VL；若 VL 后仍有配置，提示其不会执行并建议移动到末尾。空结果与异常分开记录，默认可恢复异常继续下一项。

## 提示词和兼容

混合模式统一使用 `<search_result>混合检索结果</search_result>`。文本和表格结果都注入该占位符；VL 直接提取不使用占位符。未开启混合模式的旧字段继续使用原 `search_type`、`search_config` 和原占位符。开启时旧配置作为 `items[0]`，不批量迁移存量数据。

## 前端交互

字段编辑器增加“组合检索”扩展按钮。展开后显示策略、可排序配置列表、添加/复制/删除操作及拖拽手柄；同时提供上移/下移按钮。保存前校验方法参数、VL 模式限制和混合占位符，提供复制固定占位符按钮。

## 可观测性与测试

调试结果保留每项的 ID、来源类型、方法、耗时、命中数、是否采用和异常。测试覆盖旧配置回归、union 去重与顺序、fallback 空结果与异常继续、表格两种策略、VL 仅 fallback 校验、统一占位符替换及拖拽顺序持久化。
