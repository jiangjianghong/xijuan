"""自定义规则字段树 output_schema 的校验与渲染测试。"""

from __future__ import annotations

import json

import pytest

from utils.output_schema import (
    OutputSchemaError,
    build_example_json,
    render_schema_prompt,
    validate_output_schema,
)

_SCHEMA = [
    {"key": "公司名称", "type": "string", "example": "华为", "desc": "营业执照全称"},
    {"key": "股东", "type": "array", "desc": "所有股东", "children": [
        {"key": "姓名", "type": "string", "example": "张三", "desc": "股东姓名"},
        {"key": "持股比例", "type": "string", "example": "52%", "desc": "百分比"},
    ]},
]


def test_validate_ok():
    validate_output_schema(_SCHEMA)  # 不抛异常即通过


def test_validate_rejects_empty():
    with pytest.raises(OutputSchemaError):
        validate_output_schema([])


def test_validate_rejects_non_list():
    with pytest.raises(OutputSchemaError):
        validate_output_schema({"key": "x", "type": "string"})


def test_validate_rejects_bad_type():
    with pytest.raises(OutputSchemaError, match="type"):
        validate_output_schema([{"key": "a", "type": "int"}])


def test_validate_rejects_empty_key():
    with pytest.raises(OutputSchemaError, match="key"):
        validate_output_schema([{"key": "  ", "type": "string"}])


def test_validate_rejects_duplicate_key():
    with pytest.raises(OutputSchemaError, match="重复"):
        validate_output_schema([
            {"key": "a", "type": "string"},
            {"key": "a", "type": "number"},
        ])


def test_validate_rejects_container_without_children():
    with pytest.raises(OutputSchemaError, match="children"):
        validate_output_schema([{"key": "s", "type": "object", "children": []}])


def test_validate_rejects_scalar_with_children():
    with pytest.raises(OutputSchemaError, match="标量"):
        validate_output_schema([
            {"key": "s", "type": "string", "children": [{"key": "x", "type": "string"}]},
        ])


def test_build_example_json_nested():
    obj = build_example_json(_SCHEMA)
    assert obj == {"公司名称": "华为", "股东": [{"姓名": "张三", "持股比例": "52%"}]}


def test_build_example_json_scalar_defaults():
    obj = build_example_json([
        {"key": "n", "type": "number"},
        {"key": "b", "type": "boolean"},
        {"key": "s", "type": "string"},
    ])
    assert obj == {"n": 0, "b": False, "s": ""}


def test_build_example_scalar_array():
    obj = build_example_json([
        {"key": "标签", "type": "array", "children": [
            {"key": "item", "type": "string", "example": "A"},
        ]},
    ])
    assert obj == {"标签": ["A"]}


def test_render_schema_prompt_has_lines_and_example():
    out = render_schema_prompt(_SCHEMA)
    assert "公司名称 (字符串)：营业执照全称" in out
    assert "姓名 (字符串)：股东姓名" in out
    # 示例 JSON 完整拼在末尾
    assert json.dumps(build_example_json(_SCHEMA), ensure_ascii=False) in out
