"""逻辑分析服务：对应 design.md 第 8 节（judge/calc）。"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import numexpr
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.database import rollback_if_broken
from model.tables import AnalysisResult, AnalysisRule, ExtractionResult, File
from service.extraction_service import (
    build_temporary_extraction_plan,
    iter_temporary_extraction_results,
)
from utils.callback import notify_callback
from utils.config import get_config
from utils.concurrency import (
    get_limiter,
    register_task_limiter,
    unregister_task_limiter,
    work_item,
)
from utils.llm_client import chat_completion
from utils.text_utils import normalize_cjk_quotes, salvage_reason, salvage_value_reason
from utils.output_schema import render_schema_prompt
from utils.web_search import bocha_web_search


@dataclass(frozen=True)
class FileRuleSnapshot:
    """脱离 AsyncSession 生命周期的文件分析规则快照。"""

    rule_id: str
    rule_name: str
    rule_type: str
    depend_fields: tuple[str, ...]
    expression: str
    web_search: dict[str, Any] | None
    system_prompt: str
    is_formatted: bool
    output_schema: Any | None

    @classmethod
    def from_orm(cls, rule: AnalysisRule) -> "FileRuleSnapshot":
        return cls(
            rule_id=rule.rule_id,
            rule_name=rule.rule_name,
            rule_type=rule.rule_type,
            depend_fields=tuple(rule.depend_fields or ()),
            expression=rule.expression or "",
            web_search=copy.deepcopy(rule.web_search),
            system_prompt=rule.system_prompt or "",
            is_formatted=bool(rule.is_formatted),
            output_schema=copy.deepcopy(rule.output_schema),
        )


@dataclass(frozen=True)
class FileRuleComputation:
    rule_id: str
    rule_name: str
    rule_type: str
    result: str
    reason: str
    input_values: dict[str, str]
    source_refs: dict[str, Any] | None
    success: bool


async def _compute_file_rule(
    rule: FileRuleSnapshot,
    field_values: dict[str, str],
    field_source_refs: dict[str, dict],
    calc_precision: int,
) -> FileRuleComputation:
    input_values = {
        field_id: field_values.get(field_id, "")
        for field_id in rule.depend_fields
    }
    source_refs = {
        field_id: field_source_refs[field_id]
        for field_id in rule.depend_fields
        if field_id in field_source_refs
    }

    def finish(result: str, reason: str, success: bool) -> FileRuleComputation:
        return FileRuleComputation(
            rule_id=rule.rule_id,
            rule_name=rule.rule_name,
            rule_type=rule.rule_type,
            result=result,
            reason=reason,
            input_values=input_values,
            source_refs=source_refs or None,
            success=success,
        )

    try:
        valid, validation_reason = validate_field_values(
            rule.rule_type,
            list(rule.depend_fields),
            field_values,
        )
        if not valid:
            return finish("", validation_reason, False)

        resolved_expression = resolve_expression(rule.expression, field_values)
        if rule.rule_type in {"judge", "custom"}:
            resolved_expression, web_ref = await apply_web_search(
                resolved_expression,
                rule.web_search,
                field_values,
            )
            if web_ref:
                source_refs["_web_search"] = web_ref

        if rule.rule_type == "judge":
            result, reason = await execute_judge(
                resolved_expression,
                system_prompt=rule.system_prompt,
            )
        elif rule.rule_type == "calc":
            result, reason = await execute_calc(resolved_expression, calc_precision)
        elif rule.rule_type == "custom":
            result, reason = await execute_custom(
                resolved_expression,
                is_formatted=rule.is_formatted,
                output_schema=rule.output_schema,
                system_prompt=rule.system_prompt,
            )
        else:
            return finish("", f"未知规则类型: {rule.rule_type}", False)
        return finish(result, reason, True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("规则分析失败: rule_id={}, error={}", rule.rule_id, exc)
        return finish("", str(exc), False)


async def _compute_file_rules(
    file_id: str,
    rules: list[FileRuleSnapshot],
    field_values: dict[str, str],
    field_source_refs: dict[str, dict],
    calc_precision: int,
) -> list[FileRuleComputation]:
    limits = get_config().concurrency
    file_limiter = register_task_limiter(
        "task_file_analysis",
        file_id,
        limits.task_file_analysis,
        {"file_id": file_id, "stage": "analyzing"},
    )
    total_limiter = get_limiter("global_analysis", limits.global_analysis)

    async def guarded(rule: FileRuleSnapshot) -> FileRuleComputation:
        context = {
            "file_id": file_id,
            "stage": "analyzing",
            "rule_id": rule.rule_id,
        }
        with work_item():
            async with file_limiter.context(context):
                async with total_limiter.context(context):
                    return await _compute_file_rule(
                        rule,
                        field_values,
                        field_source_refs,
                        calc_precision,
                    )

    tasks = [asyncio.create_task(guarded(rule)) for rule in rules]
    try:
        return [await task for task in tasks]
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        unregister_task_limiter("task_file_analysis", file_id)


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


WEB_SEARCH_PLACEHOLDER = "<web_search_result/>"


async def apply_web_search(
    expression: str,
    web_search: Optional[dict],
    field_values: Dict[str, str],
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """若规则启用网络搜索，执行博查搜索并替换表达式中的占位符。

    Args:
        expression: 已完成 <field_result> 替换的表达式。
        web_search: 规则的 web_search 配置（{"enabled", "query", "count", "freshness"}）。
        field_values: {field_id: extracted_value}，用于解析搜索词中的占位符。

    Returns:
        (替换占位符后的表达式, _web_search 溯源数据或 None) 元组。
        搜索失败时占位符替换为失败提示并继续（溯源数据带 error 键），不抛异常。
    """
    if not web_search or not web_search.get("enabled"):
        return expression, None

    query = resolve_expression(web_search.get("query", ""), field_values).strip()
    try:
        formatted, results = await bocha_web_search(
            query,
            count=web_search.get("count"),
            freshness=web_search.get("freshness"),
        )
        ws_ref: Dict[str, Any] = {"query": query, "results": results}
    except Exception as e:
        logger.warning("网络搜索失败: query={}, error={}", query, e)
        formatted = f"（网络搜索失败: {e}）"
        ws_ref = {"query": query, "results": [], "error": str(e)}

    return expression.replace(WEB_SEARCH_PLACEHOLDER, formatted), ws_ref


def validate_expression_has_placeholder(expression: str) -> bool:
    """校验 expression 中是否包含至少一个有效的字段占位符。"""
    pattern = r"<field_result>.+?</field_result>"
    return bool(re.search(pattern, expression))


def validate_field_values(
    rule_type: str,
    depend_fields: List[str],
    field_values: Dict[str, str],
) -> Tuple[bool, str]:
    """校验依赖字段值是否有效。

    校验规则：只要有至少一个依赖字段有有效值即通过校验。
    - 通用校验：至少一个字段值非空
    - calc 类型额外校验：至少一个字段值为有效数字

    Args:
        rule_type: 规则类型 (judge/calc)。
        depend_fields: 依赖的字段 ID 列表。
        field_values: {field_id: extracted_value} 映射。

    Returns:
        (is_valid, reason) 元组。
        - is_valid: 是否通过校验。
        - reason: 校验失败的原因说明。
    """
    if not depend_fields:
        return True, ""

    # 检查每个字段的状态
    empty_fields = []
    non_empty_fields = []
    valid_number_fields = []
    invalid_number_fields = []

    for field_id in depend_fields:
        value = field_values.get(field_id, "")

        if not value or not value.strip():
            empty_fields.append(field_id)
        else:
            non_empty_fields.append(field_id)

            # 对于 calc 类型，检查是否为有效数字
            if rule_type == "calc":
                cleaned_value = value.strip().replace(",", "").replace(" ", "")
                if re.match(r"^-?[\d.]+(?:[eE][+-]?\d+)?$", cleaned_value):
                    valid_number_fields.append(field_id)
                else:
                    invalid_number_fields.append(field_id)

    # 所有字段都为空 → 不通过
    if not non_empty_fields:
        return False, f"所有依赖字段均为空: {', '.join(empty_fields)}"

    # calc 类型：至少需要一个有效数字
    if rule_type == "calc" and not valid_number_fields:
        reasons = []
        if empty_fields:
            reasons.append(f"字段为空: {', '.join(empty_fields)}")
        if invalid_number_fields:
            reasons.append(f"字段值不是有效数字: {', '.join(invalid_number_fields)}")
        return False, "; ".join(reasons)

    return True, ""


async def execute_judge(resolved_expression: str, *, system_prompt: str = "") -> Tuple[str, str]:
    """执行判断类规则：将表达式发送给 LLM，返回 true/false 及理由。

    Args:
        resolved_expression: 已替换占位符的完整 prompt。
        system_prompt: 可选的系统提示词，作为 system message 发送。

    Returns:
        (result, reason) 元组，result 为 true/false 字符串。
    """
    prompt = f"""{resolved_expression}

请根据以上内容进行判断，以 JSON 格式返回结果：
{{"result": "true 或 false", "reason": "判断理由/依据"}}
重点关注：只输出 JSON 结果不要带有```等标识；result 与 reason 的值中不得含有英文双引号，需要引用文字请一律使用中文引号“”，否则会破坏 JSON 结构。"""

    try:
        sys_prompt = (system_prompt or "").strip()
        timeout = get_config().analysis.judge_timeout
        if sys_prompt:
            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt},
            ]
            response = await chat_completion("", messages=messages, timeout=timeout)
        else:
            response = await chat_completion(prompt, timeout=timeout)
        response = response.strip()

        # 尝试提取 JSON 块
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            response = json_match.group(1)

        # 尝试解析 JSON
        try:
            data = json.loads(response)
            result_raw = str(data.get("result", "")).lower().strip()
            reason = normalize_cjk_quotes(str(data.get("reason", "")).strip())

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
                reason = normalize_cjk_quotes(str(data.get("reason", "")).strip())
                if "true" in result_raw or "是" in result_raw:
                    return "true", reason
                elif "false" in result_raw or "否" in result_raw:
                    return "false", reason
                else:
                    return result_raw, reason
            except json.JSONDecodeError:
                pass

        # JSON 解析失败，尝试从文本中提取结果；reason 用 salvage 抢救（模型吐裸英文双引号时常见）
        salvaged_reason = salvage_reason(response)
        response_lower = response.lower()
        if "true" in response_lower:
            return "true", salvaged_reason
        elif "false" in response_lower:
            return "false", salvaged_reason
        elif "是" in response_lower:
            return "true", salvaged_reason
        elif "否" in response_lower:
            return "false", salvaged_reason
        else:
            logger.warning("LLM 判断返回非标准值: {}", response)
            return response_lower, salvaged_reason

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


# ── custom 自定义规则 ────────────────────────────────────────

CUSTOM_JSON_INSTRUCTION_PLAIN = """

请根据以上内容生成结果，以 JSON 格式返回，包含 value（结果内容）和 reason（生成依据）两个字段：
{"value": "生成的结果内容", "reason": "说明依据"}
重点关注：只输出 JSON 结果不要带有```等标识；value 与 reason 的值中不得含有英文双引号，需引用文字请一律使用中文引号“”，否则会破坏 JSON 结构。"""


def _extract_custom_value_reason(data: Dict[str, Any]) -> Tuple[str, str]:
    """从 dict 取出 (value, reason)。value 为对象/数组时转 JSON 字符串。"""
    raw_value = data.get("value", "")
    if isinstance(raw_value, (list, dict)):
        value = json.dumps(raw_value, ensure_ascii=False)
    else:
        value = normalize_cjk_quotes(str(raw_value).strip())
    reason = normalize_cjk_quotes(str(data.get("reason", "")).strip())
    return value, reason


def parse_custom_json_response(response: str) -> Tuple[str, str]:
    """解析 custom LLM 返回的 {value, reason}。

    value 为对象/数组时 json.dumps 成字符串（即格式化输出的 JSON 字符串）；
    标量转字符串并归一化中文标点。解析失败时用 salvage 兜底。
    """
    response = (response or "").strip()

    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if json_match:
        response = json_match.group(1)

    try:
        data = json.loads(response)
        if isinstance(data, dict):
            return _extract_custom_value_reason(data)
    except json.JSONDecodeError:
        pass

    json_obj_match = re.search(r"\{[^{}]*\"value\"[^{}]*\}", response, re.DOTALL)
    if json_obj_match:
        try:
            data = json.loads(json_obj_match.group())
            return _extract_custom_value_reason(data)
        except json.JSONDecodeError:
            pass

    salvaged_value, salvaged_reason = salvage_value_reason(response)
    if salvaged_value or salvaged_reason:
        return salvaged_value, salvaged_reason
    return response.strip(), ""


def _build_custom_prompt(
    resolved_expression: str,
    is_formatted: bool,
    output_schema: Optional[list],
) -> str:
    """组装 custom 用户提示词。"""
    if is_formatted and output_schema:
        schema_block = render_schema_prompt(output_schema)
        return (
            f"{resolved_expression}\n\n{schema_block}\n\n"
            '以 JSON 格式返回：{"value": <上面结构的 JSON>, "reason": "生成依据"}\n'
            "重点关注：只输出 JSON 结果不要带有```等标识。"
        )
    return f"{resolved_expression}{CUSTOM_JSON_INSTRUCTION_PLAIN}"


async def execute_custom(
    resolved_expression: str,
    *,
    is_formatted: bool = False,
    output_schema: Optional[list] = None,
    system_prompt: str = "",
) -> Tuple[str, str]:
    """执行自定义规则：LLM 自由生成，返回 (value, reason)。

    is_formatted=True 时把 output_schema 渲染成结构说明+示例 JSON 注入提示词，
    要求模型输出符合结构的 JSON（value 落库为 JSON 字符串）。
    """
    prompt = _build_custom_prompt(resolved_expression, is_formatted, output_schema)
    sys_prompt = (system_prompt or "").strip()
    timeout = get_config().analysis.judge_timeout
    if sys_prompt:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ]
        response = await chat_completion("", messages=messages, timeout=timeout)
    else:
        response = await chat_completion(prompt, timeout=timeout)
    return parse_custom_json_response(response)


async def _load_file_analysis_context(
    file_id: str,
    session: AsyncSession,
) -> tuple[list[FileRuleSnapshot], dict[str, str], dict[str, dict]]:
    file_row = (
        await session.execute(select(File).where(File.file_id == file_id))
    ).scalar_one_or_none()
    type_id = (file_row.type_id if file_row else None) or "default"

    result = await session.execute(
        select(AnalysisRule)
        .where(AnalysisRule.enabled == 1, AnalysisRule.type_id == type_id)
        .order_by(AnalysisRule.priority, AnalysisRule.rule_id)
    )
    rules = [FileRuleSnapshot.from_orm(rule) for rule in result.scalars().all()]

    extraction_result = await session.execute(
        select(ExtractionResult).where(ExtractionResult.file_id == file_id)
    )
    extraction_rows = extraction_result.scalars().all()
    field_values = {
        row.field_id: row.extracted_value
        for row in extraction_rows
    }
    field_source_refs = {
        row.field_id: copy.deepcopy(row.source_refs)
        for row in extraction_rows
        if row.source_refs
    }
    return rules, field_values, field_source_refs


async def _persist_file_computation(
    file_id: str,
    item: FileRuleComputation,
    session: AsyncSession,
) -> None:
    existing = (
        await session.execute(
            select(AnalysisResult).where(
                AnalysisResult.file_id == file_id,
                AnalysisResult.rule_id == item.rule_id,
            )
        )
    ).scalar_one_or_none()
    values = {
        "result_value": item.result,
        "input_values": item.input_values,
        "reason": item.reason,
        "source_refs": item.source_refs,
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
    else:
        session.add(
            AnalysisResult(
                file_id=file_id,
                rule_id=item.rule_id,
                **values,
            )
        )
    await session.commit()


async def _persist_file_computation_safely(
    file_id: str,
    item: FileRuleComputation,
    session: AsyncSession,
) -> FileRuleComputation:
    try:
        await _persist_file_computation(file_id, item, session)
        return item
    except Exception as exc:
        logger.error("分析结果落库失败: rule_id={}, error={}", item.rule_id, exc)
        await rollback_if_broken(session)
        failed = replace(
            item,
            result="",
            reason=str(exc),
            source_refs=None,
            success=False,
        )
        try:
            await _persist_file_computation(file_id, failed, session)
        except Exception as retry_exc:
            logger.error(
                "失败结果落库失败: rule_id={}, error={}",
                item.rule_id,
                retry_exc,
            )
            await rollback_if_broken(session)
        return failed


def _callback_item(
    item: FileRuleComputation,
    index: int,
    total: int,
) -> dict[str, Any]:
    return {
        "rule_id": item.rule_id,
        "rule_name": item.rule_name,
        "rule_type": item.rule_type,
        "result": item.result,
        "reason": item.reason,
        "input_values": item.input_values,
        "source_refs": item.source_refs,
        "success": item.success,
        "index": index,
        "total": total,
    }


def _stream_item(
    item: FileRuleComputation,
    index: int,
    total: int,
) -> dict[str, Any]:
    return {
        "rule_id": item.rule_id,
        "rule_name": item.rule_name,
        "rule_type": item.rule_type,
        "result_value": item.result,
        "input_values": item.input_values,
        "reason": item.reason,
        "source_refs": item.source_refs,
        "success": item.success,
        "current": index,
        "total": total,
    }


async def run_analysis(
    file_id: str,
    session: AsyncSession,
    callback_url: Optional[str] = None,
) -> None:
    """并发计算文件规则，并按配置顺序落库和发送回调。"""
    logger.info("开始逻辑分析: {}", file_id)
    rules, field_values, field_source_refs = await _load_file_analysis_context(
        file_id,
        session,
    )
    computed = await _compute_file_rules(
        file_id,
        rules,
        field_values,
        field_source_refs,
        get_config().analysis.calc_precision,
    )
    total = len(computed)
    aggregated: list[dict[str, Any]] = []
    for index, computation in enumerate(computed, start=1):
        settled = await _persist_file_computation_safely(
            file_id,
            computation,
            session,
        )
        outward = _callback_item(settled, index, total)
        aggregated.append(outward)
        await notify_callback(
            callback_url,
            file_id,
            "analyzing",
            event="rule_done",
            data=outward,
        )

    await session.execute(
        update(File).where(File.file_id == file_id).values(progress="complete")
    )
    await session.commit()
    succeeded = sum(bool(item["success"]) for item in aggregated)
    await notify_callback(
        callback_url,
        file_id,
        "analyzing",
        event="stage_done",
        data={
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "results": aggregated,
        },
    )
    logger.info("逻辑分析完成: {}", file_id)


async def run_analysis_stream(file_id: str, session: AsyncSession):
    """并发计算文件规则，并按配置顺序落库和流式输出。"""
    logger.info("开始流式逻辑分析: {}", file_id)
    rules, field_values, field_source_refs = await _load_file_analysis_context(
        file_id,
        session,
    )
    computed = await _compute_file_rules(
        file_id,
        rules,
        field_values,
        field_source_refs,
        get_config().analysis.calc_precision,
    )
    total = len(computed)
    for index, computation in enumerate(computed, start=1):
        settled = await _persist_file_computation_safely(
            file_id,
            computation,
            session,
        )
        yield _stream_item(settled, index, total)

    await session.execute(
        update(File).where(File.file_id == file_id).values(progress="complete")
    )
    await session.commit()
    logger.info("流式逻辑分析完成: {}", file_id)


async def test_rule_analysis_stream(
    file_id: str,
    rule_type: str,
    expression: str,
    depend_fields: List[str],
    system_prompt: str,
    session: AsyncSession,
    web_search: Optional[dict] = None,
    is_formatted: int = 0,
    output_schema: Optional[list] = None,
    re_extract: bool = False,
) -> AsyncIterator[Dict[str, Any]]:
    """单条规则调试流式接口，分步 yield 各阶段结果。

    Judge 类型事件序列：input_values → resolved_expression → [web_search] → prompt → llm_response → result → done
    Calc 类型事件序列：input_values → resolved_expression → result → done
    Custom 类型事件序列：input_values → resolved_expression → [web_search] → prompt → llm_response → result → done

    Args:
        file_id: 文件 ID。
        rule_type: 规则类型 (judge/calc/custom)。
        expression: 规则表达式（含占位符）。
        depend_fields: 依赖的字段 ID 列表。
        system_prompt: 系统提示词（judge/custom 使用）。
        session: 数据库会话。
        web_search: 可选的网络搜索配置（judge/custom 使用）。
        is_formatted: custom 是否格式化输出（1 时按 output_schema 注入结构）。
        output_schema: custom 格式化输出结构定义。

    Yields:
        Dict: {"event": str, "data": dict}
    """
    logger.info("开始规则调试流: file_id={}, rule_type={}", file_id, rule_type)

    cfg = get_config().analysis

    # ── Step 1: 获取依赖字段值 ──
    try:
        if re_extract:
            plan = await build_temporary_extraction_plan(
                file_id, depend_fields, session
            )
            yield {
                "event": "extraction_started",
                "data": {"total": len(plan.ordered_fields)},
            }
            extraction_items = []
            async for item in iter_temporary_extraction_results(plan):
                extraction_items.append(item)
                yield {"event": "extraction_field", "data": item}

            succeeded = sum(1 for item in extraction_items if item["success"])
            yield {
                "event": "extraction_done",
                "data": {
                    "total": len(extraction_items),
                    "succeeded": succeeded,
                    "failed": len(extraction_items) - succeeded,
                },
            }
            items_by_id = {item["field_id"]: item for item in extraction_items}
            invalid_fields = []
            for field_id in depend_fields:
                item = items_by_id.get(field_id)
                if (
                    not item
                    or not item["success"]
                    or not str(item["value"] or "").strip()
                ):
                    invalid_fields.append(field_id)
            if invalid_fields:
                yield {
                    "event": "error",
                    "data": {
                        "message": "本次抽取的依赖字段失败或为空: "
                        + ", ".join(invalid_fields)
                    },
                }
                return
            field_values = {
                field_id: str(item["value"] or "")
                for field_id, item in items_by_id.items()
            }
        else:
            stmt = select(ExtractionResult).where(ExtractionResult.file_id == file_id)
            result = await session.execute(stmt)
            extraction_results = result.scalars().all()
            field_values = {
                er.field_id: er.extracted_value for er in extraction_results
            }

        input_values: Dict[str, str] = {}
        for fid in depend_fields:
            input_values[fid] = field_values.get(fid, "")

        yield {
            "event": "input_values",
            "data": {"input_values": input_values, "depend_fields": depend_fields},
        }

        # 校验依赖字段值
        is_valid, validate_reason = validate_field_values(
            rule_type, depend_fields, field_values
        )
        if not is_valid:
            yield {
                "event": "error",
                "data": {"message": f"字段校验失败: {validate_reason}"},
            }
            return
    except Exception as e:
        logger.error("规则调试 - 获取字段值失败: {}", e)
        yield {"event": "error", "data": {"message": f"获取字段值失败: {e}"}}
        return

    # ── Step 2: 解析表达式 ──
    try:
        resolved = resolve_expression(expression, field_values)
        yield {
            "event": "resolved_expression",
            "data": {
                "original_expression": expression,
                "resolved_expression": resolved,
            },
        }
    except Exception as e:
        logger.error("规则调试 - 表达式解析失败: {}", e)
        yield {"event": "error", "data": {"message": f"表达式解析失败: {e}"}}
        return

    # ── Judge 类型：LLM 调用 ──
    if rule_type == "judge":
        # Step 2.5: 网络搜索（启用时）
        if web_search and web_search.get("enabled"):
            resolved, ws_ref = await apply_web_search(resolved, web_search, field_values)
            yield {"event": "web_search", "data": ws_ref or {}}

        # Step 3: 组装 prompt
        try:
            user_prompt = f"""{resolved}

请根据以上内容进行判断，以 JSON 格式返回结果：
{{"result": "true 或 false", "reason": "判断理由/依据"}}
重点关注：只输出 JSON 结果不要带有```等标识；result 与 reason 的值中不得含有英文双引号，需要引用文字请一律使用中文引号“”，否则会破坏 JSON 结构。"""

            sys_prompt = (system_prompt or "").strip()
            yield {
                "event": "prompt",
                "data": {
                    "system_prompt": sys_prompt,
                    "user_prompt": user_prompt,
                },
            }
        except Exception as e:
            logger.error("规则调试 - 组装 prompt 失败: {}", e)
            yield {"event": "error", "data": {"message": f"组装 prompt 失败: {e}"}}
            return

        # Step 4: 调用 LLM
        try:
            if sys_prompt:
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                raw_response = await chat_completion(
                    "", messages=messages, timeout=cfg.judge_timeout
                )
            else:
                raw_response = await chat_completion(
                    user_prompt, timeout=cfg.judge_timeout
                )
            raw_response = raw_response.strip()

            yield {
                "event": "llm_response",
                "data": {"raw_response": raw_response},
            }
        except Exception as e:
            logger.error("规则调试 - LLM 调用失败: {}", e)
            yield {"event": "error", "data": {"message": f"LLM 调用失败: {e}"}}
            return

        # Step 5: 解析 LLM 结果
        try:
            # 复用 execute_judge 的 JSON 解析逻辑
            response = raw_response
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
            if json_match:
                response = json_match.group(1)

            result_value = ""
            reason = ""
            parsed = False

            try:
                data = json.loads(response)
                result_raw = str(data.get("result", "")).lower().strip()
                reason = normalize_cjk_quotes(str(data.get("reason", "")).strip())
                if "true" in result_raw or "是" in result_raw:
                    result_value = "true"
                elif "false" in result_raw or "否" in result_raw:
                    result_value = "false"
                else:
                    result_value = result_raw
                parsed = True
            except json.JSONDecodeError:
                pass

            if not parsed:
                json_obj_match = re.search(
                    r"\{[^{}]*\"result\"[^{}]*\}", response, re.DOTALL
                )
                if json_obj_match:
                    try:
                        data = json.loads(json_obj_match.group())
                        result_raw = str(data.get("result", "")).lower().strip()
                        reason = normalize_cjk_quotes(str(data.get("reason", "")).strip())
                        if "true" in result_raw or "是" in result_raw:
                            result_value = "true"
                        elif "false" in result_raw or "否" in result_raw:
                            result_value = "false"
                        else:
                            result_value = result_raw
                        parsed = True
                    except json.JSONDecodeError:
                        pass

            if not parsed:
                reason = salvage_reason(raw_response)
                response_lower = raw_response.lower()
                if "true" in response_lower:
                    result_value = "true"
                elif "false" in response_lower:
                    result_value = "false"
                elif "是" in response_lower:
                    result_value = "true"
                elif "否" in response_lower:
                    result_value = "false"
                else:
                    result_value = response_lower

            yield {
                "event": "result",
                "data": {"result_value": result_value, "reason": reason},
            }
        except Exception as e:
            logger.error("规则调试 - 结果解析失败: {}", e)
            yield {"event": "error", "data": {"message": f"结果解析失败: {e}"}}
            return

    # ── Calc 类型：直接计算 ──
    elif rule_type == "calc":
        # Step 3: 计算
        try:
            result_value, reason = await execute_calc(resolved, cfg.calc_precision)
            yield {
                "event": "result",
                "data": {"result_value": result_value, "reason": reason},
            }
        except Exception as e:
            logger.error("规则调试 - 计算失败: {}", e)
            yield {"event": "error", "data": {"message": f"计算失败: {e}"}}
            return

    # ── Custom 类型：LLM 自由生成（含格式化） ──
    elif rule_type == "custom":
        # Step 2.5: 网络搜索（启用时）
        if web_search and web_search.get("enabled"):
            resolved, ws_ref = await apply_web_search(resolved, web_search, field_values)
            yield {"event": "web_search", "data": ws_ref or {}}

        # Step 3: 组装 prompt（复用 execute_custom 的组装逻辑）
        try:
            user_prompt = _build_custom_prompt(resolved, bool(is_formatted), output_schema)
            sys_prompt = (system_prompt or "").strip()
            yield {
                "event": "prompt",
                "data": {"system_prompt": sys_prompt, "user_prompt": user_prompt},
            }
        except Exception as e:
            logger.error("规则调试 - 组装 custom prompt 失败: {}", e)
            yield {"event": "error", "data": {"message": f"组装 prompt 失败: {e}"}}
            return

        # Step 4: 调用 LLM
        try:
            if sys_prompt:
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                raw_response = await chat_completion(
                    "", messages=messages, timeout=cfg.judge_timeout
                )
            else:
                raw_response = await chat_completion(
                    user_prompt, timeout=cfg.judge_timeout
                )
            raw_response = raw_response.strip()
            yield {"event": "llm_response", "data": {"raw_response": raw_response}}
        except Exception as e:
            logger.error("规则调试 - custom LLM 调用失败: {}", e)
            yield {"event": "error", "data": {"message": f"LLM 调用失败: {e}"}}
            return

        # Step 5: 解析结果
        try:
            value, reason = parse_custom_json_response(raw_response)
            yield {"event": "result", "data": {"result_value": value, "reason": reason}}
        except Exception as e:
            logger.error("规则调试 - custom 结果解析失败: {}", e)
            yield {"event": "error", "data": {"message": f"结果解析失败: {e}"}}
            return

    else:
        yield {
            "event": "error",
            "data": {"message": f"未知规则类型: {rule_type}"},
        }
        return

    # ── Done ──
    yield {"event": "done", "data": {}}
