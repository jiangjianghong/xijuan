"""
Seed script: 为 "Attention Is All You Need" 论文配置字段提取和逻辑分析规则。
用法: python scripts/seed_attention_paper.py
要求: 服务运行在 http://localhost:5019

【重要】占位符规则：
- <search_result>标签</search_result> 中的"标签"必须与 keywords 中的关键词完全匹配
- 系统会用该关键词检索到的上下文替换整个占位符
"""

import httpx
import asyncio

BASE_URL = "http://localhost:5019"


# ══════════════════════════════════════════════════════════════
# 字段提取配置 (Extraction Fields)
# ══════════════════════════════════════════════════════════════
EXTRACTION_FIELDS = [
    # ── 基本信息类 ──
    {
        "field_id": "paper_title",
        "field_name": "论文标题",
        "source_type": "text",
        "enabled": 1,
        "priority": 0,
        "search_type": "context",
        "search_config": {
            "keywords": ["Attention Is All You Need"],
            "context_before": 50,
            "context_after": 100,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下文档内容中提取论文的完整英文标题。\n\n"
            "文档内容：\n<search_result>Attention Is All You Need</search_result>\n\n"
            "只需返回论文标题，不要包含其他信息。"
        ),
    },
    {
        "field_id": "model_name",
        "field_name": "提出的模型名称",
        "source_type": "text",
        "enabled": 1,
        "priority": 1,
        "search_type": "context",
        "search_config": {
            "keywords": ["Transformer"],
            "context_before": 100,
            "context_after": 200,
            "max_results": 5,
        },
        "text_extract_prompt": (
            "请从以下文档内容中提取论文提出的核心模型/架构名称。\n\n"
            "文档内容：\n<search_result>Transformer</search_result>\n\n"
            "只需返回模型名称（一个词），例如 'Transformer'。"
        ),
    },
    # ── 实验结果类 ──
    {
        "field_id": "bleu_en_de",
        "field_name": "英德翻译BLEU分数",
        "source_type": "text",
        "enabled": 1,
        "priority": 10,
        "search_type": "context",
        "search_config": {
            "keywords": ["28.4"],
            "context_before": 200,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取 Transformer 模型在 WMT 2014 英语到德语翻译任务上的最佳 BLEU 分数。\n\n"
            "文档内容：\n<search_result>28.4</search_result>\n\n"
            "只需返回数值，例如 '28.4'。"
        ),
    },
    {
        "field_id": "bleu_en_fr",
        "field_name": "英法翻译BLEU分数",
        "source_type": "text",
        "enabled": 1,
        "priority": 11,
        "search_type": "context",
        "search_config": {
            "keywords": ["41.8"],
            "context_before": 200,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取 Transformer 模型在 WMT 2014 英语到法语翻译任务上的最佳 BLEU 分数。\n\n"
            "文档内容：\n<search_result>41.8</search_result>\n\n"
            "只需返回数值，例如 '41.8'。"
        ),
    },
    {
        "field_id": "bleu_improvement",
        "field_name": "相比之前SOTA的BLEU提升",
        "source_type": "text",
        "enabled": 1,
        "priority": 12,
        "search_type": "context",
        "search_config": {
            "keywords": ["2 BLEU"],
            "context_before": 200,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取 Transformer 相比之前最优模型在英德翻译任务上 BLEU 分数提升了多少。\n\n"
            "文档内容：\n<search_result>2 BLEU</search_result>\n\n"
            "只需返回提升的数值，例如 '2' 或 'over 2'。"
        ),
    },
    # ── 模型架构参数类 ──
    {
        "field_id": "d_model",
        "field_name": "模型维度 d_model",
        "source_type": "text",
        "enabled": 1,
        "priority": 20,
        "search_type": "context",
        "search_config": {
            "keywords": ["dmodel=512"],
            "context_before": 100,
            "context_after": 150,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取 Transformer 基础模型的模型维度 d_model 的数值。\n\n"
            "文档内容：\n<search_result>dmodel=512</search_result>\n\n"
            "只需返回数值，例如 '512'。"
        ),
    },
    {
        "field_id": "num_heads",
        "field_name": "注意力头数 h",
        "source_type": "text",
        "enabled": 1,
        "priority": 21,
        "search_type": "context",
        "search_config": {
            "keywords": ["h=8"],
            "context_before": 100,
            "context_after": 150,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取 Transformer 模型使用的注意力头数量 h。\n\n"
            "文档内容：\n<search_result>h=8</search_result>\n\n"
            "只需返回数值，例如 '8'。"
        ),
    },
    {
        "field_id": "num_layers",
        "field_name": "编码器/解码器层数 N",
        "source_type": "text",
        "enabled": 1,
        "priority": 22,
        "search_type": "context",
        "search_config": {
            "keywords": ["N=6"],
            "context_before": 100,
            "context_after": 150,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取 Transformer 模型的编码器（或解码器）堆叠层数 N。\n\n"
            "文档内容：\n<search_result>N=6</search_result>\n\n"
            "只需返回数值，例如 '6'。"
        ),
    },
    {
        "field_id": "d_ff",
        "field_name": "前馈网络内层维度 d_ff",
        "source_type": "text",
        "enabled": 1,
        "priority": 23,
        "search_type": "context",
        "search_config": {
            "keywords": ["dff=2048"],
            "context_before": 100,
            "context_after": 150,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取 Transformer 前馈网络的内层维度 d_ff。\n\n"
            "文档内容：\n<search_result>dff=2048</search_result>\n\n"
            "只需返回数值，例如 '2048'。"
        ),
    },
    {
        "field_id": "d_k",
        "field_name": "注意力键值维度 d_k",
        "source_type": "text",
        "enabled": 1,
        "priority": 24,
        "search_type": "context",
        "search_config": {
            "keywords": ["dk=dv=dmodel/h=64"],
            "context_before": 100,
            "context_after": 150,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取每个注意力头的键(key)和值(value)维度 d_k。\n\n"
            "文档内容：\n<search_result>dk=dv=dmodel/h=64</search_result>\n\n"
            "只需返回数值，例如 '64'。"
        ),
    },
    # ── 训练配置类 ──
    {
        "field_id": "training_hardware",
        "field_name": "训练硬件",
        "source_type": "text",
        "enabled": 1,
        "priority": 30,
        "search_type": "context",
        "search_config": {
            "keywords": ["8 NVIDIA P100 GPUs"],
            "context_before": 50,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取训练模型所使用的硬件配置。\n\n"
            "文档内容：\n<search_result>8 NVIDIA P100 GPUs</search_result>\n\n"
            "请简洁回答，例如 '8 NVIDIA P100 GPUs'。"
        ),
    },
    {
        "field_id": "training_time_big",
        "field_name": "大模型训练时长(天)",
        "source_type": "text",
        "enabled": 1,
        "priority": 31,
        "search_type": "context",
        "search_config": {
            "keywords": ["3.5 days"],
            "context_before": 150,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取大型 Transformer 模型的训练时长（天数）。\n\n"
            "文档内容：\n<search_result>3.5 days</search_result>\n\n"
            "只需返回数值，例如 '3.5'。"
        ),
    },
    {
        "field_id": "training_time_base",
        "field_name": "基础模型训练时长(小时)",
        "source_type": "text",
        "enabled": 1,
        "priority": 32,
        "search_type": "context",
        "search_config": {
            "keywords": ["12 hours"],
            "context_before": 150,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取基础 Transformer 模型的训练时长（小时数）。\n\n"
            "文档内容：\n<search_result>12 hours</search_result>\n\n"
            "只需返回数值，例如 '12'。"
        ),
    },
    {
        "field_id": "training_steps_base",
        "field_name": "基础模型训练步数",
        "source_type": "text",
        "enabled": 1,
        "priority": 33,
        "search_type": "context",
        "search_config": {
            "keywords": ["100,000 steps"],
            "context_before": 100,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取基础模型的训练步数。\n\n"
            "文档内容：\n<search_result>100,000 steps</search_result>\n\n"
            "只需返回数值，例如 '100000'。"
        ),
    },
    {
        "field_id": "training_steps_big",
        "field_name": "大模型训练步数",
        "source_type": "text",
        "enabled": 1,
        "priority": 34,
        "search_type": "context",
        "search_config": {
            "keywords": ["300,000 steps"],
            "context_before": 100,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取大型模型的训练步数。\n\n"
            "文档内容：\n<search_result>300,000 steps</search_result>\n\n"
            "只需返回数值，例如 '300000'。"
        ),
    },
    {
        "field_id": "optimizer",
        "field_name": "优化器",
        "source_type": "text",
        "enabled": 1,
        "priority": 35,
        "search_type": "context",
        "search_config": {
            "keywords": ["Adam optimizer"],
            "context_before": 50,
            "context_after": 300,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取训练使用的优化器名称及其超参数。\n\n"
            "文档内容：\n<search_result>Adam optimizer</search_result>\n\n"
            "请简洁回答，例如 'Adam (β1=0.9, β2=0.98, ε=10^-9)'。"
        ),
    },
    {
        "field_id": "dropout_rate",
        "field_name": "Dropout率",
        "source_type": "text",
        "enabled": 1,
        "priority": 36,
        "search_type": "context",
        "search_config": {
            "keywords": ["Pdrop=0.1"],
            "context_before": 100,
            "context_after": 150,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取基础模型的 Dropout 率。\n\n"
            "文档内容：\n<search_result>Pdrop=0.1</search_result>\n\n"
            "只需返回数值，例如 '0.1'。"
        ),
    },
    {
        "field_id": "warmup_steps",
        "field_name": "Warmup步数",
        "source_type": "text",
        "enabled": 1,
        "priority": 37,
        "search_type": "context",
        "search_config": {
            "keywords": ["warmup_steps=4000"],
            "context_before": 100,
            "context_after": 150,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取学习率预热的步数。\n\n"
            "文档内容：\n<search_result>warmup_steps=4000</search_result>\n\n"
            "只需返回数值，例如 '4000'。"
        ),
    },
    {
        "field_id": "label_smoothing",
        "field_name": "标签平滑值",
        "source_type": "text",
        "enabled": 1,
        "priority": 38,
        "search_type": "context",
        "search_config": {
            "keywords": ["ϵls=0.1"],
            "context_before": 100,
            "context_after": 150,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取标签平滑的值 ε_ls。\n\n"
            "文档内容：\n<search_result>ϵls=0.1</search_result>\n\n"
            "只需返回数值，例如 '0.1'。"
        ),
    },
    # ── 模型参数量类 ──
    {
        "field_id": "base_model_params",
        "field_name": "基础模型参数量(百万)",
        "source_type": "text",
        "enabled": 1,
        "priority": 40,
        "search_type": "context",
        "search_config": {
            "keywords": ["65"],  # 表格中的参数量
            "context_before": 200,
            "context_after": 200,
            "max_results": 5,
        },
        "text_extract_prompt": (
            "请从以下内容中找到 Transformer 基础模型 (base) 的参数量（单位百万）。\n"
            "在表格中，base 模型对应的 params 列值约为 65×10^6。\n\n"
            "文档内容：\n<search_result>65</search_result>\n\n"
            "只需返回数值，例如 '65'。"
        ),
    },
    {
        "field_id": "big_model_params",
        "field_name": "大模型参数量(百万)",
        "source_type": "text",
        "enabled": 1,
        "priority": 41,
        "search_type": "context",
        "search_config": {
            "keywords": ["213"],  # 表格中的参数量
            "context_before": 200,
            "context_after": 200,
            "max_results": 5,
        },
        "text_extract_prompt": (
            "请从以下内容中找到 Transformer 大型模型 (big) 的参数量（单位百万）。\n"
            "在表格中，big 模型对应的 params 列值约为 213×10^6。\n\n"
            "文档内容：\n<search_result>213</search_result>\n\n"
            "只需返回数值，例如 '213'。"
        ),
    },
    # ── 训练数据类 ──
    {
        "field_id": "en_de_dataset_size",
        "field_name": "英德训练数据量(百万句对)",
        "source_type": "text",
        "enabled": 1,
        "priority": 50,
        "search_type": "context",
        "search_config": {
            "keywords": ["4.5 million sentence pairs"],
            "context_before": 50,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取英德翻译数据集的句子对数量（百万）。\n\n"
            "文档内容：\n<search_result>4.5 million sentence pairs</search_result>\n\n"
            "只需返回数值，例如 '4.5'。"
        ),
    },
    {
        "field_id": "en_fr_dataset_size",
        "field_name": "英法训练数据量(百万句子)",
        "source_type": "text",
        "enabled": 1,
        "priority": 51,
        "search_type": "context",
        "search_config": {
            "keywords": ["36M sentences"],
            "context_before": 50,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取英法翻译数据集的句子数量（百万）。\n\n"
            "文档内容：\n<search_result>36M sentences</search_result>\n\n"
            "只需返回数值，例如 '36'。"
        ),
    },
    # ── 核心创新点 ──
    {
        "field_id": "no_rnn_cnn",
        "field_name": "是否放弃RNN和CNN",
        "source_type": "text",
        "enabled": 1,
        "priority": 60,
        "search_type": "context",
        "search_config": {
            "keywords": ["dispensing with recurrence and convolutions entirely"],
            "context_before": 100,
            "context_after": 200,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请根据以下内容判断 Transformer 模型是否完全放弃了循环(RNN)和卷积(CNN)结构。\n\n"
            "文档内容：\n<search_result>dispensing with recurrence and convolutions entirely</search_result>\n\n"
            "只需回答 '是' 或 '否'。"
        ),
    },
    {
        "field_id": "attention_formula",
        "field_name": "注意力计算公式",
        "source_type": "text",
        "enabled": 1,
        "priority": 61,
        "search_type": "context",
        "search_config": {
            "keywords": ["Attention(Q, K, V)"],
            "context_before": 50,
            "context_after": 300,
            "max_results": 3,
        },
        "text_extract_prompt": (
            "请从以下内容中提取 Scaled Dot-Product Attention 的计算公式。\n\n"
            "文档内容：\n<search_result>Attention(Q, K, V)</search_result>\n\n"
            "请返回公式，例如 'Attention(Q,K,V) = softmax(QK^T/√d_k)V'。"
        ),
    },
]


# ══════════════════════════════════════════════════════════════
# 逻辑分析配置 (Analysis Rules)
# ══════════════════════════════════════════════════════════════
ANALYSIS_RULES = [
    {
        "rule_id": "param_ratio_big_vs_base",
        "rule_name": "大模型与基础模型参数量倍数",
        "rule_type": "calc",
        "expression": "<field_result>big_model_params</field_result> / <field_result>base_model_params</field_result>",
        "depend_fields": ["big_model_params", "base_model_params"],
        "enabled": 1,
        "priority": 1,
    },
    {
        "rule_id": "training_steps_ratio",
        "rule_name": "大模型与基础模型训练步数倍数",
        "rule_type": "calc",
        "expression": "<field_result>training_steps_big</field_result> / <field_result>training_steps_base</field_result>",
        "depend_fields": ["training_steps_big", "training_steps_base"],
        "enabled": 1,
        "priority": 2,
    },
    {
        "rule_id": "head_dim_check",
        "rule_name": "注意力头维度校验 (d_model/h)",
        "rule_type": "calc",
        "expression": "<field_result>d_model</field_result> / <field_result>num_heads</field_result>",
        "depend_fields": ["d_model", "num_heads"],
        "enabled": 1,
        "priority": 3,
    },
    {
        "rule_id": "ff_expansion_ratio",
        "rule_name": "前馈网络扩展比率 (d_ff/d_model)",
        "rule_type": "calc",
        "expression": "<field_result>d_ff</field_result> / <field_result>d_model</field_result>",
        "depend_fields": ["d_ff", "d_model"],
        "enabled": 1,
        "priority": 4,
    },
    {
        "rule_id": "dataset_size_ratio",
        "rule_name": "英法与英德数据集大小倍数",
        "rule_type": "calc",
        "expression": "<field_result>en_fr_dataset_size</field_result> / <field_result>en_de_dataset_size</field_result>",
        "depend_fields": ["en_fr_dataset_size", "en_de_dataset_size"],
        "enabled": 1,
        "priority": 5,
    },
    {
        "rule_id": "is_attention_only",
        "rule_name": "是否为纯注意力架构",
        "rule_type": "judge",
        "expression": (
            "根据以下信息判断该模型是否为纯注意力架构（完全不使用RNN或CNN）：\n"
            "模型名称：<field_result>model_name</field_result>\n"
            "是否放弃RNN和CNN：<field_result>no_rnn_cnn</field_result>\n"
            "请回答'是'或'否'，并简要说明。"
        ),
        "depend_fields": ["model_name", "no_rnn_cnn"],
        "enabled": 1,
        "priority": 6,
    },
    {
        "rule_id": "is_sota",
        "rule_name": "是否达到SOTA",
        "rule_type": "judge",
        "expression": (
            "该模型在英德翻译任务上 BLEU 分数为 <field_result>bleu_en_de</field_result>，"
            "在英法翻译任务上 BLEU 分数为 <field_result>bleu_en_fr</field_result>，"
            "相比之前最优模型提升了 <field_result>bleu_improvement</field_result> BLEU。\n"
            "请判断该模型是否在这些任务上达到了当时的 state-of-the-art（SOTA）。回答'是'或'否'。"
        ),
        "depend_fields": ["bleu_en_de", "bleu_en_fr", "bleu_improvement"],
        "enabled": 1,
        "priority": 7,
    },
    {
        "rule_id": "training_efficiency",
        "rule_name": "训练效率评估",
        "rule_type": "judge",
        "expression": (
            "大型模型训练时长：<field_result>training_time_big</field_result> 天，"
            "基础模型训练时长：<field_result>training_time_base</field_result> 小时，"
            "使用硬件：<field_result>training_hardware</field_result>。\n"
            "英德BLEU：<field_result>bleu_en_de</field_result>，英法BLEU：<field_result>bleu_en_fr</field_result>。\n"
            "请评估该模型的训练效率是否优秀（考虑到达成的性能）。回答'是'或'否'并说明理由。"
        ),
        "depend_fields": [
            "training_time_big", "training_time_base",
            "training_hardware", "bleu_en_de", "bleu_en_fr",
        ],
        "enabled": 1,
        "priority": 8,
    },
]


async def main():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        print("=" * 60)
        print("  Seeding Extraction Fields & Analysis Rules")
        print("  Target: Attention Is All You Need (Transformer)")
        print("=" * 60)

        # ── 先删除旧的配置 ──
        print("\n[0/2] Cleaning up old configurations...")

        # 获取已有字段
        resp = await client.get("/extraction/fields")
        old_fields = resp.json().get("data", [])
        for f in old_fields:
            fid = f.get("field_id")
            await client.delete(f"/extraction/fields/{fid}")
            print(f"  Deleted field: {fid}")

        # 获取已有规则
        resp = await client.get("/analysis/rules")
        old_rules = resp.json().get("data", [])
        for r in old_rules:
            rid = r.get("rule_id")
            await client.delete(f"/analysis/rules/{rid}")
            print(f"  Deleted rule: {rid}")

        # ── 写入字段提取配置 ──
        print(f"\n[1/2] Creating {len(EXTRACTION_FIELDS)} extraction fields...")
        for i, field in enumerate(EXTRACTION_FIELDS, 1):
            resp = await client.post("/extraction/fields", json=field)
            data = resp.json()
            status = "OK" if data.get("code") == 200 else "FAIL"
            print(f"  [{status}] {i:2d}. {field['field_id']:35s} - {field['field_name']}")
            if status == "FAIL":
                print(f"       {data.get('message', data.get('detail', ''))}")

        # ── 写入逻辑分析配置 ──
        print(f"\n[2/2] Creating {len(ANALYSIS_RULES)} analysis rules...")
        for i, rule in enumerate(ANALYSIS_RULES, 1):
            resp = await client.post("/analysis/rules", json=rule)
            data = resp.json()
            status = "OK" if data.get("code") == 200 else "FAIL"
            print(f"  [{status}] {i:2d}. {rule['rule_id']:30s} - {rule['rule_name']}")
            if status == "FAIL":
                print(f"       {data.get('message', data.get('detail', ''))}")

        # ── 验证 ──
        print("\n" + "-" * 60)
        print("Verification:")

        resp = await client.get("/extraction/fields")
        fields = resp.json().get("data", [])
        print(f"  Extraction fields in DB: {len(fields)}")

        resp = await client.get("/analysis/rules")
        rules = resp.json().get("data", [])
        print(f"  Analysis rules in DB:    {len(rules)}")

        print("\n" + "=" * 60)
        print("  Done! Now retry extraction:")
        print('  curl -X POST "http://localhost:5019/file/{file_id}/retry/extracting"')
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
