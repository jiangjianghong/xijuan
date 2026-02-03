"""逻辑分析服务：对应 design.md 第 8 节（judge/calc）。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import numexpr
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import AnalysisResult, AnalysisRule, ExtractionResult, File
from utils.config import get_config
from utils.llm_client import chat_completion


def resolve_expression(
    expression: str,
    field_values: Dict[str, str],
    no_result_hint: str = "（未找到字段 '{}' 的提取结果）"
) -> str:
    """将表达式中的 <field_result>field_id</field_result> 占位符替换为实际值。

    Args:
        expression: 原始表达式。
        field_values: {field_id: extracted_value} 映射。
        no_result_hint: 无结果时的提示模板，{} 会被替换为字段标识。

    Returns:
        替换后的表达式。
    """
    pattern = r"<field_result>(.+?)</field_result>"

    def replacer(match: re.Match) -> str:
        field_id = match.group(1).strip()
        if field_id in field_values and field_values[field_id]:
            return field_values[field_id]
        return no_result_hint.format(field_id)

    return re.sub(pattern, replacer, expression)


def validate_expression_has_placeholder(expression: str) -> bool:
    """校验 expression 中是否包含至少一个有效的字段占位符。"""
    pattern = r"<field_result>.+?</field_result>"
    return bool(re.search(pattern, expression))


async def execute_judge(resolved_expression: str) -> Tuple[str, str]:
    """执行判断类规则：将表达式发送给 LLM，返回 true/false 及理由。

    Args:
        resolved_expression: 已替换占位符的完整 prompt。

    Returns:
        (result, reason) 元组，result 为 true/false 字符串。
    """
    prompt = f"""{resolved_expression}

请根据以上内容进行判断，以 JSON 格式返回结果：
{{"result": "true 或 false", "reason": "判断理由/依据"}}"""

    try:
        response = await chat_completion(prompt)
        response = response.strip()

        # 尝试提取 JSON 块
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            response = json_match.group(1)

        # 尝试解析 JSON
        try:
            data = json.loads(response)
            result_raw = str(data.get("result", "")).lower().strip()
            reason = str(data.get("reason", "")).strip()

            # 规范化返回值
            if "true" in result_raw or "是" in result_raw:
                return "true", reason
            elif "false" in result_raw or "否" in result_raw:
                return "false", reason
            else:
                return result_raw, reason
        except json.JSONDecodeError:
            pass

        # 尝试提取 JSON 对象
        json_obj_match = re.search(r"\{[^{}]*\"result\"[^{}]*\}", response, re.DOTALL)
        if json_obj_match:
            try:
                data = json.loads(json_obj_match.group())
                result_raw = str(data.get("result", "")).lower().strip()
                reason = str(data.get("reason", "")).strip()
                if "true" in result_raw or "是" in result_raw:
                    return "true", reason
                elif "false" in result_raw or "否" in result_raw:
                    return "false", reason
                else:
                    return result_raw, reason
            except json.JSONDecodeError:
                pass

        # JSON 解析失败，尝试从文本中提取结果
        response_lower = response.lower()
        if "true" in response_lower:
            return "true", ""
        elif "false" in response_lower:
            return "false", ""
        elif "是" in response_lower:
            return "true", ""
        elif "否" in response_lower:
            return "false", ""
        else:
            logger.warning("LLM 判断返回非标准值: {}", response)
            return response_lower, ""

    except Exception as e:
        logger.error("LLM 判断执行失败: {}", e)
        raise


async def execute_calc(resolved_expression: str, precision: int = 2) -> Tuple[str, str]:
    """执行计算类规则：使用 numexpr 安全计算公式。

    Args:
        resolved_expression: 已替换占位符的数学表达式。
        precision: 小数保留位数。

    Returns:
        (result, reason) 元组，result 为计算结果字符串。
    """
    # 清理表达式：只保留数学运算符和数字
    expr = resolved_expression.strip()

    # 移除可能的文字描述，只保留数学表达式
    # 尝试提取数学表达式部分
    math_chars = set("0123456789+-*/().eE ")
    cleaned_expr = ""
    for char in expr:
        if char in math_chars:
            cleaned_expr += char

    cleaned_expr = cleaned_expr.strip()

    if not cleaned_expr:
        raise ValueError(f"无法从表达式中提取有效的数学公式: {expr}")

    try:
        # 使用 numexpr 进行安全计算
        result = numexpr.evaluate(cleaned_expr)
        result_float = float(result)

        # 格式化结果
        if result_float == int(result_float):
            result_str = str(int(result_float))
        else:
            result_str = f"{result_float:.{precision}f}"

        # 自动生成计算理由
        reason = f"计算公式: {cleaned_expr} = {result_str}"
        return result_str, reason

    except Exception as e:
        logger.error("numexpr 计算失败: expr={}, error={}", cleaned_expr, e)
        raise ValueError(f"计算失败: {e}")


async def run_analysis(file_id: str, session: AsyncSession) -> None:
    """执行文件的完整逻辑分析流程。

    1. 获取所有 enabled=1 的 analysis_rule，按 priority 排序
    2. 对每条规则解析占位符、获取依赖字段值、执行 judge/calc
    3. 结果写入 analysis_result 表
    4. 单条规则失败跳过继续
    5. 完成后更新 files.progress = 'complete'

    Args:
        file_id: 文件 ID。
        session: 数据库会话。
    """
    logger.info("开始逻辑分析: {}", file_id)

    cfg = get_config().analysis

    # 获取所有启用的规则，按 priority 排序
    stmt = (
        select(AnalysisRule)
        .where(AnalysisRule.enabled == 1)
        .order_by(AnalysisRule.priority)
    )
    result = await session.execute(stmt)
    rules = result.scalars().all()

    # 获取该文件的所有提取结果
    stmt = select(ExtractionResult).where(ExtractionResult.file_id == file_id)
    result = await session.execute(stmt)
    extraction_results = result.scalars().all()

    # 构建 field_id -> extracted_value 映射
    field_values: Dict[str, str] = {
        er.field_id: er.extracted_value for er in extraction_results
    }

    for rule in rules:
        try:
            # 获取依赖字段值
            depend_fields = rule.depend_fields or []
            input_values: Dict[str, str] = {}
            for field_id in depend_fields:
                input_values[field_id] = field_values.get(field_id, "")

            # 解析表达式
            resolved_expression = resolve_expression(rule.expression, field_values)

            # 根据规则类型执行
            if rule.rule_type == "judge":
                result_value, reason = await execute_judge(resolved_expression)
            elif rule.rule_type == "calc":
                result_value, reason = await execute_calc(resolved_expression, cfg.calc_precision)
            else:
                logger.warning("未知规则类型: {}", rule.rule_type)
                result_value = ""
                reason = ""

            # 保存结果
            stmt = select(AnalysisResult).where(
                AnalysisResult.file_id == file_id,
                AnalysisResult.rule_id == rule.rule_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                existing.result_value = result_value
                existing.input_values = input_values
                existing.reason = reason
            else:
                analysis_result = AnalysisResult(
                    file_id=file_id,
                    rule_id=rule.rule_id,
                    result_value=result_value,
                    input_values=input_values,
                    reason=reason,
                )
                session.add(analysis_result)

            await session.commit()
            logger.info("规则分析成功: rule_id={}, result={}", rule.rule_id, result_value)

        except Exception as e:
            logger.error("规则分析失败: rule_id={}, error={}", rule.rule_id, e)
            # 保存空值
            stmt = select(AnalysisResult).where(
                AnalysisResult.file_id == file_id,
                AnalysisResult.rule_id == rule.rule_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            input_values = {}
            for field_id in (rule.depend_fields or []):
                input_values[field_id] = field_values.get(field_id, "")

            if existing:
                existing.result_value = ""
                existing.input_values = input_values
                existing.reason = ""
            else:
                analysis_result = AnalysisResult(
                    file_id=file_id,
                    rule_id=rule.rule_id,
                    result_value="",
                    input_values=input_values,
                    reason="",
                )
                session.add(analysis_result)

            await session.commit()

    # 更新文件状态为 complete
    stmt = (
        update(File)
        .where(File.file_id == file_id)
        .values(progress="complete")
    )
    await session.execute(stmt)
    await session.commit()

    logger.info("逻辑分析完成: {}", file_id)
