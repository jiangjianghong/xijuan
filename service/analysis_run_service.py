"""独立逻辑分析服务：使用外部字段值执行规则，不读写文件分析结果。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import AnalysisResult, AnalysisRule, ExtractionResult, File
from service.analysis_service import (
    apply_web_search,
    execute_calc,
    execute_custom,
    execute_judge,
    resolve_expression,
    validate_field_values,
)
from service.type_param_store import (
    ParamValidationError,
    load_type_param_defs_by_types,
    normalize_raw_params,
    resolve_input_params,
)
from service.type_params import render_rule_params
from utils.config import get_config
from utils.concurrency import (
    get_limiter,
    work_item,
)


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
    enabled: int = 1

    @classmethod
    def from_orm(cls, rule: AnalysisRule) -> "AnalysisRuleSnapshot":
        enabled_raw = getattr(rule, "enabled", 1)
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
            enabled=1 if enabled_raw is None else int(enabled_raw),
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


@dataclass(frozen=True)
class RulePlan:
    """一个 item 的规则执行计划。"""

    rules: list[AnalysisRuleSnapshot]
    unknown_rule_ids: list[str]
    require_coverage: bool


def plan_rules(
    rules: Sequence[AnalysisRuleSnapshot],
    field_values: Mapping[str, str],
    rule_ids: Optional[Sequence[str]] = None,
) -> RulePlan:
    """规划一个 item 实际要执行的规则。

    `rule_ids` 为 None（不传）时沿用隐式筛选：只跑**启用**（enabled=1）且依赖字段被
    输入覆盖的规则，其余静默跳过。显式点名（含空数组）时只跑点名的规则，**无视 enabled
    开关**，且**不做**覆盖过滤 —— 缺依赖字段交由 `execute_rule` 产出失败结果，避免规则
    从 results 里凭空消失。独立分析的显式执行不应依赖开关状态。
    点名了但该类型下不存在的 rule_id 收进 `unknown_rule_ids` 回传，不报错。
    """

    if rule_ids is None:
        enabled_rules = [rule for rule in rules if rule.enabled]
        return RulePlan(
            rules=select_covered_rules(enabled_rules, field_values),
            unknown_rule_ids=[],
            require_coverage=False,
        )

    named = list(dict.fromkeys(rule_ids))
    wanted = set(named)
    available = {rule.rule_id for rule in rules}
    matched = [rule for rule in rules if rule.rule_id in wanted]
    matched.sort(key=lambda rule: (rule.priority, rule.rule_id))
    return RulePlan(
        rules=matched,
        unknown_rule_ids=[rid for rid in named if rid not in available],
        require_coverage=True,
    )


def merge_field_source_refs(
    rule_source_refs: Optional[Dict[str, Any]],
    depend_fields: Sequence[str],
    field_source_refs: Mapping[str, dict],
) -> Optional[Dict[str, Any]]:
    """把依赖字段的提取溯源并进规则结果的 source_refs。

    与管线版 run_analysis 对齐：键为 field_id，与 `_web_search` 等元数据键同级。
    """

    merged: Dict[str, Any] = {
        field_id: field_source_refs[field_id]
        for field_id in depend_fields
        if field_id in field_source_refs
    }
    if rule_source_refs:
        merged.update(rule_source_refs)
    return merged or None


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
    *,
    require_coverage: bool = False,
    params: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """执行一条规则；规则级异常转换为失败结果，不中断同组后续规则。"""

    values = dict(field_values)
    input_values = {
        field_id: values.get(field_id, "")
        for field_id in rule.depend_fields
    }
    source_refs: Dict[str, Any] = {}

    try:
        # 参数渲染先于 <field_result> 渲染，理由同 extraction 侧
        rendered = render_rule_params(rule, params or {})
        if rendered.params_used:
            source_refs["_params"] = rendered.params_used

        if require_coverage:
            missing = [
                field_id
                for field_id in rule.depend_fields
                if field_id not in values
            ]
            if missing:
                return _rule_result(
                    rule,
                    "",
                    f"缺少依赖字段: {', '.join(missing)}",
                    input_values,
                    None,
                    False,
                )

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

        resolved = resolve_expression(rendered.expression, values)
        if rule.rule_type == "judge":
            resolved, web_ref = await apply_web_search(
                resolved,
                rendered.web_search,
                values,
            )
            if web_ref:
                source_refs["_web_search"] = web_ref
            value, reason = await execute_judge(
                resolved,
                system_prompt=rendered.system_prompt,
            )
        elif rule.rule_type == "calc":
            value, reason = await execute_calc(
                resolved,
                get_config().analysis.calc_precision,
            )
        elif rule.rule_type == "custom":
            resolved, web_ref = await apply_web_search(
                resolved,
                rendered.web_search,
                values,
            )
            if web_ref:
                source_refs["_web_search"] = web_ref
            value, reason = await execute_custom(
                resolved,
                is_formatted=bool(rule.is_formatted),
                output_schema=rule.output_schema,
                system_prompt=rendered.system_prompt,
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
            # 失败时保留已收集的溯源（_params / _web_search）：规则挂了的时候，
            # 「当时喂进去的参数是什么」正是最该看的东西。与管线侧
            # _compute_file_rule 的 finish() 行为对齐。
            source_refs or None,
            False,
        )


async def _load_rules_by_type(
    type_ids: set[str],
    session: AsyncSession,
) -> dict[str, list[AnalysisRuleSnapshot]]:
    """一次查询全部类型规则，并在启动并发前转换为快照。

    **不**按 enabled 过滤：显式点名的规则应无视开关执行；enabled 过滤仅在
    `plan_rules` 的隐式路径（不传 rule_ids）里对「跑全部启用规则」生效。
    """

    statement = (
        select(AnalysisRule)
        .where(
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


@dataclass(frozen=True)
class FileFieldSnapshot:
    """脱离 AsyncSession 生命周期的文件字段值快照。"""

    file_id: str
    type_id: str
    field_values: dict[str, str]
    field_source_refs: dict[str, dict]
    # 该文件提交时的入参快照；source=file 的 item 默认继承它
    input_params: dict[str, str]


async def load_file_snapshots(
    file_ids: set[str],
    session: AsyncSession,
) -> dict[str, FileFieldSnapshot]:
    """并发启动前批量读取文件类型与提取结果，转为只读快照。

    AsyncSession 非并发安全，故所有读库集中在此处一次做完（2 条查询），
    并发段只碰快照。库里不存在的 file_id 不会出现在返回值里。
    """

    if not file_ids:
        return {}

    ordered = sorted(file_ids)
    file_rows = (await session.execute(
        select(File).where(File.file_id.in_(ordered))
    )).scalars().all()

    extraction_rows = (await session.execute(
        select(ExtractionResult).where(ExtractionResult.file_id.in_(ordered))
    )).scalars().all()

    values: dict[str, dict[str, str]] = defaultdict(dict)
    refs: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in extraction_rows:
        values[row.file_id][row.field_id] = row.extracted_value or ""
        if row.source_refs:
            refs[row.file_id][row.field_id] = row.source_refs

    return {
        row.file_id: FileFieldSnapshot(
            file_id=row.file_id,
            type_id=row.type_id or "default",
            field_values=dict(values.get(row.file_id, {})),
            field_source_refs=dict(refs.get(row.file_id, {})),
            input_params=dict(getattr(row, "input_params", None) or {}),
        )
        for row in file_rows
    }


RuleDoneHandler = Callable[[Dict[str, Any]], Awaitable[None]]


async def persist_analysis_results(
    items: Sequence[Mapping[str, Any]],
    item_results: Sequence[Mapping[str, Any]],
    session: AsyncSession,
) -> None:
    """把 file 模式的分析结果 upsert 进 analysis_result。

    并发结束后统一执行。**不改 files.progress** —— 管线状态机只由
    pipeline / retry 维护，此接口仅写结果行。报错的 item 整条跳过。
    """

    for item, result in zip(items, item_results):
        if result.get("error") or not result.get("results"):
            continue
        file_id = str(item["file_id"])
        for row in result["results"]:
            existing = (await session.execute(
                select(AnalysisResult).where(
                    AnalysisResult.file_id == file_id,
                    AnalysisResult.rule_id == row["rule_id"],
                )
            )).scalar_one_or_none()

            if existing:
                existing.result_value = row["result"]
                existing.input_values = row["input_values"]
                existing.reason = row["reason"]
                existing.source_refs = row["source_refs"]
            else:
                session.add(AnalysisResult(
                    file_id=file_id,
                    rule_id=row["rule_id"],
                    result_value=row["result"],
                    input_values=row["input_values"],
                    reason=row["reason"],
                    source_refs=row["source_refs"],
                ))
    await session.commit()


async def run_analysis_batch(
    items: Sequence[Mapping[str, Any]],
    session: AsyncSession,
    *,
    on_rule_done: Optional[RuleDoneHandler] = None,
    source: str = "values",
    persist: bool = False,
) -> Dict[str, Any]:
    """批量执行独立分析：item 与规则双层并发，闸门在规则层。

    `source="file"` 时字段值取自各 item `file_id` 已落库的 extraction_result。
    所有读库在并发前完成、写库在并发后完成（AsyncSession 非并发安全）。
    """

    from_file = source == "file"

    snapshots: dict[str, FileFieldSnapshot] = {}
    if from_file:
        snapshots = await load_file_snapshots(
            {str(item["file_id"]) for item in items if item.get("file_id")},
            session,
        )
        type_ids = {snapshot.type_id for snapshot in snapshots.values()}
    else:
        type_ids = {str(item["type_id"]) for item in items}

    rules_by_type = await _load_rules_by_type(type_ids, session)

    # 参数定义与规则一样，全部在并发启动前读完（AsyncSession 非并发安全）。
    # 单次 IN 查询而非按 type 循环，理由同 _load_rules_by_type：避免 N+1。
    param_defs_by_type = await load_type_param_defs_by_types(type_ids, session)

    concurrency_cfg = get_config().concurrency
    stage_limiter = get_limiter("global_analysis", concurrency_cfg.global_analysis)
    # 闸门在规则层：所有独立分析请求合计同时执行的规则数，与 task_file_analysis
    # （单文件规则并发）对称，两者共用 global_analysis 总池。
    item_limiter = get_limiter(
        "independent_analysis",
        concurrency_cfg.independent_analysis,
    )

    def _empty_item(
        item_index: int,
        biz_id: str,
        type_id: str,
        error: str,
    ) -> Dict[str, Any]:
        return {
            "item_index": item_index,
            "biz_id": biz_id,
            "type_id": type_id,
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "results": [],
            "unknown_rule_ids": [],
            "error": error,
        }

    async def run_item(
        item_index: int,
        item: Mapping[str, Any],
    ) -> Dict[str, Any]:
        biz_id = str(item["biz_id"])
        requested_type = item.get("type_id")
        field_source_refs: Mapping[str, dict] = {}

        if from_file:
            file_id = str(item["file_id"])
            snapshot = snapshots.get(file_id)
            if snapshot is None:
                return _empty_item(
                    item_index, biz_id, str(requested_type or ""),
                    f"文件不存在: {file_id}",
                )
            if requested_type and str(requested_type) != snapshot.type_id:
                return _empty_item(
                    item_index, biz_id, snapshot.type_id,
                    f"type_id 与文件不一致：请求 {requested_type}，"
                    f"文件实际 {snapshot.type_id}",
                )
            if not snapshot.field_values:
                return _empty_item(
                    item_index, biz_id, snapshot.type_id,
                    f"该文件无提取结果: {file_id}",
                )
            type_id = snapshot.type_id
            field_values = dict(snapshot.field_values)
            field_source_refs = snapshot.field_source_refs
        else:
            type_id = str(requested_type)
            field_values = dict(item["field_values"])

        # 未知 key / 缺必填要查该类型的参数清单才知道，属于 item 级错误：一个坏
        # item 不该拖垮整批（能从请求体直接判断的值类型问题已在 422 层拦掉）
        try:
            raw_params = normalize_raw_params(item.get("params"))
            if from_file:
                # source=file：文件快照打底，item 传的逐键覆盖
                raw_params = {**snapshot.input_params, **raw_params}
            params = resolve_input_params(
                param_defs_by_type.get(type_id, ()), raw_params
            )
        except ParamValidationError as exc:
            return _empty_item(item_index, biz_id, type_id, str(exc))

        plan = plan_rules(
            rules_by_type.get(type_id, []),
            field_values,
            item.get("rule_ids"),
        )
        rules = plan.rules
        total = len(rules)

        async def _one_rule(index: int, rule: AnalysisRuleSnapshot) -> Dict[str, Any]:
            context = {
                "stage": "analyzing",
                "task_id": biz_id,
                "rule_id": rule.rule_id,
                "index": index,
            }
            with work_item():
                async with item_limiter.context(context):
                    async with stage_limiter.context(context):
                        result = await execute_rule(
                            rule,
                            field_values,
                            require_coverage=plan.require_coverage,
                            params=params,
                        )
            if from_file:
                result = {
                    **result,
                    "source_refs": merge_field_source_refs(
                        result.get("source_refs"),
                        rule.depend_fields,
                        field_source_refs,
                    ),
                }
            return {**result, "index": index, "total": total}

        # 规则之间无依赖（execute_rule 只读 field_values，异常已在内部收敛为
        # 失败结果），故可并发；as_completed 让 rule_done 完成即推，results
        # 再按 index 排回配置序，对外聚合口径不变。
        results: list[Dict[str, Any]] = []
        tasks = [
            asyncio.create_task(_one_rule(index, rule))
            for index, rule in enumerate(rules, start=1)
        ]
        try:
            for completed in asyncio.as_completed(tasks):
                result = await completed
                results.append(result)
                if on_rule_done is not None:
                    await on_rule_done({
                        **result,
                        "item_index": item_index,
                        "biz_id": biz_id,
                    })
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        results.sort(key=lambda row: row["index"])
        succeeded = sum(1 for result in results if result["success"])
        return {
            "item_index": item_index,
            "biz_id": biz_id,
            "type_id": type_id,
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "results": results,
            "unknown_rule_ids": plan.unknown_rule_ids,
            "error": None,
        }

    ordered_items = await asyncio.gather(*(
        run_item(index, item)
        for index, item in enumerate(items)
    ))

    # persist 只在 file 模式有意义（values 模式无 file_id，无法定位结果行）
    if from_file and persist:
        await persist_analysis_results(items, ordered_items, session)

    return {
        "total_items": len(items),
        "items": ordered_items,
    }
