"""type_param 的读取、只读快照与入参校验。

与 service/type_params.py 分开：那边是纯占位符渲染（不碰库），这边负责
「参数清单从哪来」与「调用方传的实参合不合法」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.tables import TypeParam


class ParamValidationError(ValueError):
    """入参不合法。message 直接面向调用方，可原样放进 HTTP 400 / item error。"""


@dataclass(frozen=True)
class TypeParamDef:
    """一条参数定义的只读快照（脱离 AsyncSession 生命周期）。"""

    param_key: str
    default_value: Optional[str] = None
    required: int = 0


async def load_type_param_defs(
    type_id: str, session: AsyncSession
) -> Tuple[TypeParamDef, ...]:
    """读出该类型的参数定义，按 priority 排序。"""
    rows = (await session.execute(
        select(TypeParam)
        .where(TypeParam.type_id == type_id)
        .order_by(TypeParam.priority, TypeParam.param_key)
    )).scalars().all()
    return tuple(
        TypeParamDef(
            param_key=row.param_key,
            default_value=row.default_value,
            required=int(row.required or 0),
        )
        for row in rows
    )


def normalize_raw_params(raw: Any) -> Dict[str, str]:
    """把调用方传来的原始入参归一成 {str: str}。

    接受 None（视作空）、JSON 字符串（form 字段场景）、已解析的 dict。
    标量值统一 str 化；dict / list 值直接拒绝——占位符只能替换成文本。
    """
    if raw is None or raw == "":
        return {}

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ParamValidationError(f"params 不是合法 JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ParamValidationError("params 必须是 JSON 对象")

    normalized: Dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, (dict, list)):
            raise ParamValidationError(
                f"params.{key} 必须是标量（字符串 / 数字 / 布尔），不支持对象或数组"
            )
        normalized[str(key)] = "" if value is None else str(value)
    return normalized


def resolve_input_params(
    defs: Sequence[TypeParamDef],
    raw: Mapping[str, str],
) -> Dict[str, str]:
    """合并「默认值 <- 传入值」，校验未知 key 与必填缺失。

    未知 key 直接报错而非静默忽略：key 拼错时静默走默认空串，症状是几个字段
    悄悄抽出错的结果，而代价（MinerU + 几十次 LLM 调用）已经付掉了。

    Raises:
        ParamValidationError: 传了清单外的 key，或缺必填且无默认值。
    """
    known = {item.param_key: item for item in defs}

    unknown = [key for key in raw if key not in known]
    if unknown:
        raise ParamValidationError(
            f"未知入参: {', '.join(sorted(unknown))}；"
            f"该类型已定义的入参为: {', '.join(sorted(known)) or '（无）'}"
        )

    merged: Dict[str, str] = {}
    missing: List[str] = []
    for key, item in known.items():
        if key in raw:
            merged[key] = raw[key]
            continue
        if item.default_value:
            merged[key] = item.default_value
            continue
        if item.required:
            missing.append(key)
        merged[key] = item.default_value or ""

    if missing:
        raise ParamValidationError(
            f"缺少必填入参: {', '.join(sorted(missing))}"
        )
    return merged
