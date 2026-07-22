"""独立逻辑分析服务：使用外部字段值执行规则，不读写文件分析结果。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import AnalysisRule
from service.analysis_service import (
    apply_web_search,
    execute_calc,
    execute_custom,
    execute_judge,
    resolve_expression,
    validate_field_values,
)
from utils.config import get_config


@dataclass(frozen=True)
class AnalysisRuleSnapshot:
    """脱离 AsyncSession 生命周期的只读规则快照。"""

    rule_id: str
    type_id: str
    rule_name: str
    rule_type: str
    expression: str
    system_prompt: str
    depend_fields: list[str]
    web_search: Optional[dict]
    priority: int
    is_formatted: int = 0
    output_schema: Optional[list] = None

    @classmethod
    def from_orm(cls, rule: AnalysisRule) -> "AnalysisRuleSnapshot":
        return cls(
            rule_id=rule.rule_id,
            type_id=rule.type_id or "default",
            rule_name=rule.rule_name,
            rule_type=rule.rule_type,
            expression=rule.expression,
            system_prompt=rule.system_prompt or "",
            depend_fields=list(rule.depend_fields or []),
            web_search=rule.web_search,
            priority=int(rule.priority or 0),
            is_formatted=int(getattr(rule, "is_formatted", 0) or 0),
            output_schema=getattr(rule, "output_schema", None),
        )


def select_covered_rules(
    rules: Sequence[AnalysisRuleSnapshot],
    field_values: Mapping[str, str],
) -> list[AnalysisRuleSnapshot]:
    """返回依赖字段键被输入完整覆盖的规则，保持原顺序。"""

    provided = set(field_values)
    return [
        rule
        for rule in rules
        if set(rule.depend_fields).issubset(provided)
    ]


def _rule_result(
    rule: AnalysisRuleSnapshot,
    value: str,
    reason: str,
    input_values: Dict[str, str],
    source_refs: Optional[Dict[str, Any]],
    success: bool,
) -> Dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "rule_name": rule.rule_name,
        "rule_type": rule.rule_type,
        "result": value,
        "reason": reason,
        "input_values": input_values,
        "source_refs": source_refs,
        "success": success,
    }


async def execute_rule(
    rule: AnalysisRuleSnapshot,
    field_values: Mapping[str, str],
) -> Dict[str, Any]:
    """执行一条规则；规则级异常转换为失败结果，不中断同组后续规则。"""

    values = dict(field_values)
    input_values = {
        field_id: values.get(field_id, "")
        for field_id in rule.depend_fields
    }
    source_refs: Dict[str, Any] = {}

    try:
        valid, reason = validate_field_values(
            rule.rule_type,
            rule.depend_fields,
            values,
        )
        if not valid:
            return _rule_result(
                rule,
                "",
                reason,
                input_values,
                None,
                False,
            )

        resolved = resolve_expression(rule.expression, values)
        if rule.rule_type == "judge":
            resolved, web_ref = await apply_web_search(
                resolved,
                rule.web_search,
                values,
            )
            if web_ref:
                source_refs["_web_search"] = web_ref
            value, reason = await execute_judge(
                resolved,
                system_prompt=rule.system_prompt,
            )
        elif rule.rule_type == "calc":
            value, reason = await execute_calc(
                resolved,
                get_config().analysis.calc_precision,
            )
        elif rule.rule_type == "custom":
            resolved, web_ref = await apply_web_search(
                resolved,
                rule.web_search,
                values,
            )
            if web_ref:
                source_refs["_web_search"] = web_ref
            value, reason = await execute_custom(
                resolved,
                is_formatted=bool(rule.is_formatted),
                output_schema=rule.output_schema,
                system_prompt=rule.system_prompt,
            )
        else:
            raise ValueError(f"未知规则类型: {rule.rule_type}")

        return _rule_result(
            rule,
            value,
            reason,
            input_values,
            source_refs or None,
            True,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        return _rule_result(
            rule,
            "",
            error,
            input_values,
            None,
            False,
        )


async def _load_rules_by_type(
    type_ids: set[str],
    session: AsyncSession,
) -> dict[str, list[AnalysisRuleSnapshot]]:
    """一次查询全部类型规则，并在启动并发前转换为快照。"""

    statement = (
        select(AnalysisRule)
        .where(
            AnalysisRule.enabled == 1,
            AnalysisRule.type_id.in_(sorted(type_ids)),
        )
        .order_by(
            AnalysisRule.type_id,
            AnalysisRule.priority,
            AnalysisRule.rule_id,
        )
    )
    rows = (await session.execute(statement)).scalars().all()
    grouped: dict[str, list[AnalysisRuleSnapshot]] = defaultdict(list)
    for row in rows:
        snapshot = AnalysisRuleSnapshot.from_orm(row)
        grouped[snapshot.type_id].append(snapshot)
    for rules in grouped.values():
        rules.sort(key=lambda rule: (rule.priority, rule.rule_id))
    return dict(grouped)


RuleDoneHandler = Callable[[Dict[str, Any]], Awaitable[None]]


async def run_analysis_batch(
    items: Sequence[Mapping[str, Any]],
    session: AsyncSession,
    *,
    on_rule_done: Optional[RuleDoneHandler] = None,
) -> Dict[str, Any]:
    """批量执行独立分析：item 间并发，item 内规则顺序执行。"""

    rules_by_type = await _load_rules_by_type(
        {str(item["type_id"]) for item in items},
        session,
    )

    async def run_item(
        item_index: int,
        item: Mapping[str, Any],
    ) -> Dict[str, Any]:
        type_id = str(item["type_id"])
        biz_id = str(item["biz_id"])
        field_values = dict(item["field_values"])
        rules = select_covered_rules(
            rules_by_type.get(type_id, []),
            field_values,
        )
        total = len(rules)
        results: list[Dict[str, Any]] = []

        for index, rule in enumerate(rules, start=1):
            result = await execute_rule(rule, field_values)
            result = {**result, "index": index, "total": total}
            results.append(result)
            if on_rule_done is not None:
                await on_rule_done({
                    **result,
                    "item_index": item_index,
                    "biz_id": biz_id,
                })

        succeeded = sum(1 for result in results if result["success"])
        return {
            "item_index": item_index,
            "biz_id": biz_id,
            "type_id": type_id,
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "results": results,
        }

    ordered_items = await asyncio.gather(*(
        run_item(index, item)
        for index, item in enumerate(items)
    ))
    return {
        "total_items": len(items),
        "items": ordered_items,
    }
