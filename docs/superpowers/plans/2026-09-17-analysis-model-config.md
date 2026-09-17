# 独立分析模型配置

用户要求：增加分析模型配置，不沿用抽取配置。

设计：AnalysisConfig 独立保存 base_url、model、api_key、retry_count、enable_thinking、extra_body；保留 judge_timeout 和 calc_precision。地址、模型默认为空，实际分析模型调用前检查并提示配置缺失。calc 规则不需要模型配置。

共用 HTTP 客户端通过 config_group 选择完整配置，默认仍为 extraction；分析正式、独立分析以及调试调用统一指定 analysis，包括系统提示词分支。禁止合并抽取的密钥、思考参数或 extra_body。设置页面提供独立表单，analysis.api_key 使用现有只写密钥协议及热更新机制。

实施与验证：

- [x] 新增请求隔离及未配置报错测试，确认旧实现失败。
- [x] 扩展配置模型、客户端配置选择与所有分析调用。
- [x] 增加设置字段、密钥保护和示例 YAML。
- [x] 回归分析执行、调试与设置测试。

使用方式：重启加载代码后，在“设置 → 逻辑分析”填写地址、模型及所需密钥并保存。旧部署不会自动复制 extraction 配置；未填写时模型分析明确失败。运行时设置保存后新请求立即使用新配置。
