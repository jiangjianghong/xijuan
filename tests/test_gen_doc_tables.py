"""gen_doc_tables 表格生成器测试。"""

import json
from pathlib import Path

from scripts import gen_doc_tables as g

FX = Path(__file__).parent / "fixtures" / "doc_tools"
SPEC = json.loads((FX / "mini_openapi.json").read_text(encoding="utf-8"))


def test_request_body_table():
    t = g.render_table(SPEC, "request-body", "POST", "/file/parse")
    assert "| file | string | 是 |" in t
    assert "type_id" in t and "default" in t


def test_query_params_enum_and_default():
    t = g.render_table(SPEC, "query-params", "POST", "/file/parse")
    assert "mode" in t
    assert "async" in t  # 默认值 / 枚举


def test_path_params():
    t = g.render_table(SPEC, "path-params", "GET", "/file/{file_id}/status")
    assert "file_id" in t


def test_response_drills_into_data_nullable_and_authority():
    t = g.render_table(SPEC, "response", "GET", "/file/{file_id}/status")
    assert "file_id" in t
    assert "error" in t
    # source_refs 这类裸 object 字段 → 链接到权威页
    assert "guides/source-refs.md" in t


def test_response_array_element():
    t = g.render_table(SPEC, "response", "GET", "/file/{file_id}/tables")
    assert "数组" in t
    assert "table_name" in t


def test_endpoint_index():
    t = g.render_table(SPEC, "endpoint-index")
    assert "/file/parse" in t and "文件处理" in t and "api/file.md" in t


def test_fill_blocks_idempotent_and_replaces():
    md = (FX / "sample.md").read_text(encoding="utf-8")
    once = g.fill_blocks(md, SPEC)
    twice = g.fill_blocks(once, SPEC)
    assert once == twice
    assert "OLD" not in once
    assert "| file |" in once
