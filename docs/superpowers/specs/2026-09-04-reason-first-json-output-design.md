# Reason-First JSON Output Design

## Goal

统一项目内正式执行链路与所有调试链路的固定 JSON 输出提示词，使模型先生成可核验的 `reason` 分析依据，再生成最终的 `value` 或 `result`，并兼容旧的 `value → reason` 响应顺序。

## Scope

- 抽取 text/table 的正式链路与字段调试流。
- custom 分析规则的正式链路、规则调试流、独立分析执行链路。
- judge 分析规则的正式链路与规则调试流。
- VL 三种方法的正式链路、VL 字段调试流、前端默认提示词及文档示例。
- 非法 JSON 的 salvage 解析：支持 `reason → value/result` 与旧的 `value/result → reason` 两种顺序。

不改变数据库列、API/SSE 字段名、服务函数返回元组或用户已保存的自定义提示词内容；仅调整系统附加指令、默认示例和解析容错。

## Unified Output Contract

### Extraction, custom, and VL

固定指令要求模型按以下顺序生成：

```json
{"reason": "分步、可核验的分析和判定依据", "value": "最终要求输出的结果", "pages": [3, 5]}
```

约束文案必须明确：

> 请务必先输出 `reason`（分步、可核验的分析和判定依据），再输出 `value`（最终要求输出的结果）。必须先完成分析，再给出结论；不得在分析尚未完成时提前猜测最终结果。

`pages` 仍为可选的模型自报页码字段，保持与 `value` 平级；没有页码时返回 `[]`。

### Judge

固定指令要求模型按以下顺序生成：

```json
{"reason": "分步、可核验的判断依据", "result": "true"}
```

约束文案使用 `reason` 在前、`result` 在后的顺序，并要求先完成判断依据，再输出 true/false 结论。

## Architecture

1. 在后端提取服务中保留一份 extraction JSON 附加指令，正式 text/table 链路和字段调试流都引用它。
2. 在分析服务中集中提供 judge/custom 输出指令；正式执行和规则调试都调用同一 prompt builder。
3. VL 的最终提取提示由统一 helper 追加 reason-first 指令；三种 VL 方法和 VL 调试流都走同一 helper。用户自定义 `vl_extract_prompt` 仍保留原文，只在发送给模型前追加系统约束。
4. 解析层按键名读取，不依赖 JSON 键顺序；`salvage_value_reason` 增加双向顺序匹配，judge 调试的 salvage 同样兼容。
5. 前端 `ruleConfig.js` 的 VL 默认示例、OpenAPI 示例和相关文档示例与后端默认文案保持一致。

## Testing Strategy

- 解析测试：新顺序完整 JSON、旧顺序完整 JSON、代码围栏 JSON、非法 JSON 的新旧顺序 salvage。
- Prompt 测试：正式抽取/调试抽取、judge/custom 正式/调试的最终 prompt 均包含 reason-first 约束，示例 JSON 中 `reason` 位于 `value/result` 之前。
- VL 测试：三种方法和调试流使用包含 reason-first 约束的最终 prompt；现有返回值解析回归测试继续通过。
- 前端/文档契约测试：默认 VL prompt 与后端约束关键片段一致。

## Compatibility and Error Handling

- `json.loads` 成功时，按字段名读取，顺序不影响结果。
- `json.loads` 失败时，先尝试新顺序，再尝试旧顺序；均失败才退回现有原文兜底。
- 不对模型输出做二次自动改写，不根据 `reason` 推断或覆盖 `value/result`，避免引入隐式业务逻辑。
- 用户自定义 prompt 不被批量改写；系统附加指令始终追加在实际发送内容末尾，调试事件展示的 prompt 与发送给模型的内容一致。
