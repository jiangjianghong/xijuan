"""自定义规则「格式化输出」字段树(output_schema)的校验与提示词渲染。

字段树是节点列表，每个节点：
    {"key": str, "type": "string"|"number"|"boolean"|"object"|"array",
     "example": Any（标量类型时可选）, "desc": str（可选）,
     "children": [子节点...]（object/array 时必填非空）}

本模块只依赖标准库，供 model 层（schema 校验）与 service 层（执行渲染）共用。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

SCALAR_TYPES = {"string", "number", "boolean"}
CONTAINER_TYPES = {"object", "array"}
VALID_TYPES = SCALAR_TYPES | CONTAINER_TYPES

_TYPE_CN = {
    "string": "字符串",
    "number": "数字",
    "boolean": "布尔",
    "object": "对象",
    "array": "数组",
}

_SCALAR_DEFAULT = {"string": "", "number": 0, "boolean": False}


class OutputSchemaError(ValueError):
    """output_schema 结构非法。"""


def validate_output_schema(schema: Any, *, _path: str = "") -> None:
    """校验字段树结构合法；非法则抛 OutputSchemaError。

    - 必须是非空 list
    - 每个节点是 dict，含非空 str key、合法 type
    - object/array 必须有非空 children（递归校验）
    - 标量类型不得有非空 children
    - 同级 key 不得重复
    """
    if not isinstance(schema, list) or not schema:
        raise OutputSchemaError(f"{_path or '字段树'}必须是非空列表")
    seen: set = set()
    for i, node in enumerate(schema):
        p = f"{_path}[{i}]"
        if not isinstance(node, dict):
            raise OutputSchemaError(f"{p} 必须是对象")
        key = node.get("key")
        if not isinstance(key, str) or not key.strip():
            raise OutputSchemaError(f"{p} 的 key 不能为空")
        norm_key = key.strip()
        if norm_key in seen:
            raise OutputSchemaError(f"{p} 的 key『{key}』在同级重复")
        seen.add(norm_key)
        typ = node.get("type")
        if typ not in VALID_TYPES:
            raise OutputSchemaError(
                f"{p} 的 type『{typ}』非法（应为 {sorted(VALID_TYPES)} 之一）"
            )
        if typ in CONTAINER_TYPES:
            validate_output_schema(node.get("children"), _path=f"{p}.children")
        elif node.get("children"):
            raise OutputSchemaError(f"{p} 是标量类型，不应有 children")


def build_example_json(schema: List[Dict[str, Any]]) -> Any:
    """把字段树构建成示例 JSON 对象（供预览与提示词）。

    入参须为已校验的字段树（未经 validate_output_schema 的畸形树可能触发 KeyError）。
    """
    return _build_object(schema)


def _build_object(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {node["key"]: _build_node(node) for node in nodes}


def _build_node(node: Dict[str, Any]) -> Any:
    typ = node["type"]
    if typ == "object":
        return _build_object(node.get("children") or [])
    if typ == "array":
        children = node.get("children") or []
        # children 为单个标量节点 → 标量数组；否则视为单个 object 元素
        if len(children) == 1 and children[0].get("type") in SCALAR_TYPES:
            return [_scalar_example(children[0])]
        return [_build_object(children)]
    return _scalar_example(node)


def _scalar_example(node: Dict[str, Any]) -> Any:
    ex = node.get("example")
    if ex is not None and ex != "":
        return ex
    return _SCALAR_DEFAULT.get(node["type"], "")


def render_schema_prompt(schema: List[Dict[str, Any]]) -> str:
    """渲染成「字段说明清单 + 示例 JSON」，附加到 custom 提示词。

    入参须为已校验的字段树（未经 validate_output_schema 的畸形树可能触发 KeyError）。
    """
    lines = _render_lines(schema, 0)
    example = json.dumps(build_example_json(schema), ensure_ascii=False)
    return (
        "请按以下结构输出 JSON（把示例值替换为真实值，缺失的填空字符串）：\n"
        + "\n".join(lines)
        + "\n示例：" + example
    )


def _render_lines(nodes: List[Dict[str, Any]], indent: int) -> List[str]:
    lines: List[str] = []
    pad = "    " * indent
    for node in nodes:
        typ = node["type"]
        desc = (node.get("desc") or "").strip()
        label = f"{pad}- {node['key']} ({_TYPE_CN.get(typ, typ)})"
        if desc:
            label += f"：{desc}"
        lines.append(label)
        if typ in CONTAINER_TYPES:
            lines.extend(_render_lines(node.get("children") or [], indent + 1))
    return lines
