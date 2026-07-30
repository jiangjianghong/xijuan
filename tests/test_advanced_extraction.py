"""进阶字段提取单元测试（纯函数，不需要数据库）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from model.schemas import ExtractionFieldCreate
from model.tables import ExtractionField
from service.extraction_service import (
    _has_real_source_refs,
    collect_depend_fields,
    collect_field_refs,
    derive_page_range_from_model_pages,
    resolve_advanced_field,
    resolve_field_refs,
)


def test_extraction_field_has_advanced_columns():
    cols = set(ExtractionField.__table__.columns.keys())
    assert "is_advanced" in cols
    assert "depend_fields" in cols


def test_field_create_accepts_advanced_fields():
    m = ExtractionFieldCreate(
        field_id="adv1", field_name="进阶", source_type="text",
        search_type="context",
        search_config={"keywords": ["<field_result>a</field_result>"]},
        text_extract_prompt="从 <search_result>x</search_result> 提取",
        is_advanced=1, depend_fields=["a"],
    )
    assert m.is_advanced == 1
    assert m.depend_fields == ["a"]


def test_field_create_defaults_basic():
    m = ExtractionFieldCreate(
        field_id="b1", field_name="普通", source_type="text",
        search_type="context", search_config={"keywords": ["x"]},
        text_extract_prompt="从 <search_result>x</search_result> 提取",
    )
    assert m.is_advanced == 0
    assert m.depend_fields is None


# ── Task 3: 占位符解析 ──────────────────────────────────────



def test_collect_field_refs_dedup_order():
    s = "关于<field_result>b</field_result>与<field_result>a</field_result>及<field_result>b</field_result>"
    assert collect_field_refs(s) == ["b", "a"]


def test_collect_field_refs_empty():
    assert collect_field_refs("没有占位符") == []
    assert collect_field_refs("") == []
    assert collect_field_refs(None) == []


def test_resolve_field_refs_replaces_values():
    s = "合同<field_result>a</field_result>"
    assert resolve_field_refs(s, {"a": "甲方协议"}) == "合同甲方协议"


def test_resolve_field_refs_missing_to_empty():
    assert resolve_field_refs("<field_result>x</field_result>尾", {}) == "尾"


# ── Task 4: 页码派生 ──────────────────────────────────────



def test_derive_range_min_max():
    assert derive_page_range_from_model_pages([7, 3, 5], None) == (3, 7, False)


def test_derive_range_cap():
    assert derive_page_range_from_model_pages([3, 7], 3) == (3, 5, True)


def test_derive_range_cap_not_needed():
    assert derive_page_range_from_model_pages([3, 4], 5) == (3, 4, False)


def test_derive_range_empty_raises():
    with pytest.raises(ValueError):
        derive_page_range_from_model_pages([], 5)


# ── VL 离散页码派生 ──────────────────────────────────────────


def test_pick_model_pages_dedup_sort():
    from service.extraction_service import pick_model_pages

    pages, capped = pick_model_pages([9, 3, 9, 15], None)
    assert pages == [3, 9, 15]
    assert capped is False


def test_pick_model_pages_cap():
    from service.extraction_service import pick_model_pages

    pages, capped = pick_model_pages([3, 9, 15, 20], 2)
    assert pages == [3, 9]
    assert capped is True


def test_pick_model_pages_cap_not_needed():
    from service.extraction_service import pick_model_pages

    pages, capped = pick_model_pages([3, 9], 5)
    assert pages == [3, 9]
    assert capped is False


def test_pick_model_pages_falsy_cap_means_unlimited():
    from service.extraction_service import pick_model_pages

    for mp in (None, 0, -1):
        pages, capped = pick_model_pages([1, 2, 3], mp)
        assert pages == [1, 2, 3], f"max_pages={mp}"
        assert capped is False


def test_pick_model_pages_empty_raises():
    from service.extraction_service import pick_model_pages

    with pytest.raises(ValueError):
        pick_model_pages([], 3)


# ── Task 5: 依赖扫描 ──────────────────────────────────────



def _field(**kw):
    base = dict(
        source_type="text", search_type="context", search_config=None,
        table_name_pattern=None,
        table_match_keywords=None, table_extract_prompt=None, table_system_prompt=None,
        text_extract_prompt=None, text_system_prompt=None,
        vl_extract_prompt=None, vl_system_prompt=None, vl_config=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_collect_depend_fields_from_keywords_and_prompt():
    f = _field(
        search_config={"keywords": ["普通词", "<field_result>a</field_result>"]},
        text_extract_prompt="用 <search_result>x</search_result> 和 <field_result>b</field_result>",
    )
    assert set(collect_depend_fields(f)) == {"a", "b"}


def test_collect_depend_fields_page_source():
    f = _field(search_type="page",
               search_config={"page_source_field": "src", "max_pages": 5})
    assert collect_depend_fields(f) == ["src"]


def test_collect_depend_fields_none_when_basic():
    f = _field(search_config={"keywords": ["纯文本"]},
               text_extract_prompt="<search_result>x</search_result>")
    assert collect_depend_fields(f) == []


# ── Task 6: 进阶字段解析 ──────────────────────────────────────



def _adv_text_field():
    return ExtractionField(
        field_id="adv", type_id="default", field_name="进阶", source_type="text",
        is_advanced=1, search_type="context",
        search_config={"keywords": ["<field_result>a</field_result>", "<field_result>missing</field_result>", "固定"]},
        text_extract_prompt="从 <search_result>x</search_result> 提取 <field_result>a</field_result>",
    )


def test_resolve_advanced_keywords_and_prompt():
    field = _adv_text_field()
    resolved, prov = resolve_advanced_field(field, {"a": "甲方"}, {})
    assert resolved.search_config["keywords"] == ["甲方", "固定"]
    assert resolved.text_extract_prompt == "从 <search_result>x</search_result> 提取 甲方"
    assert field.search_config["keywords"][0] == "<field_result>a</field_result>"
    assert prov["_resolved_refs"]["a"] == "甲方"


def test_resolve_advanced_page_link():
    field = ExtractionField(
        field_id="advp", type_id="default", field_name="页联动", source_type="text",
        is_advanced=1, search_type="page",
        search_config={"page_source_field": "src", "max_pages": 3, "max_length": 30000},
        text_extract_prompt="<search_result>page_content</search_result>",
    )
    resolved, prov = resolve_advanced_field(field, {}, {"src": [3, 7]})
    assert resolved.search_config["page_range"] == "3-5"
    assert prov["_page_link"] == {
        "source_field": "src", "model_pages": [3, 7],
        "mode": "range", "derived_range": [3, 5], "capped": True,
    }


def test_resolve_advanced_page_link_missing_pages_raises():
    field = ExtractionField(
        field_id="advp2", type_id="default", field_name="页联动", source_type="text",
        is_advanced=1, search_type="page",
        search_config={"page_source_field": "src", "max_pages": 3},
        text_extract_prompt="<search_result>page_content</search_result>",
    )
    with pytest.raises(ValueError):
        resolve_advanced_field(field, {}, {"src": []})


# ── VL 进阶字段页码联动 ────────────────────────────────────────


def _adv_vl_field(**overrides):
    cfg = {"page_source_field": "src", "field_hints": "金额 <field_result>a</field_result>"}
    cfg.update(overrides.pop("vl_config", {}))
    base = dict(
        field_id="advvl", type_id="default", field_name="VL进阶", source_type="vl",
        is_advanced=1, vl_method="vl_model", vl_config=cfg,
        vl_extract_prompt="提取，输出 {value, reason}",
    )
    base.update(overrides)
    return ExtractionField(**base)


def test_resolve_advanced_vl_page_link_discrete():
    """上游自报 [9,3,9,15] → 去重升序写成逗号串，refs 记 discrete。"""
    field = _adv_vl_field()
    resolved, prov = resolve_advanced_field(field, {"a": "甲"}, {"src": [9, 3, 9, 15]})

    assert resolved.vl_config["page_range"] == "3,9,15"
    assert prov["_page_link"] == {
        "source_field": "src", "model_pages": [9, 3, 9, 15],
        "mode": "discrete", "derived_pages": [3, 9, 15], "capped": False,
    }
    # 占位符照常解析
    assert resolved.vl_config["field_hints"] == "金额 甲"
    # 会话内原对象不被污染
    assert "page_range" not in field.vl_config


def test_resolve_advanced_vl_page_link_capped():
    field = _adv_vl_field(vl_config={"max_pages": 2})
    resolved, prov = resolve_advanced_field(field, {}, {"src": [3, 9, 15]})

    assert resolved.vl_config["page_range"] == "3,9"
    assert prov["_page_link"]["derived_pages"] == [3, 9]
    assert prov["_page_link"]["capped"] is True


def test_resolve_advanced_vl_page_link_overrides_manual_range():
    """联动覆盖手填 page_range（与 text 一致）。"""
    field = _adv_vl_field(vl_config={"page_range": "1-100"})
    resolved, _ = resolve_advanced_field(field, {}, {"src": [4]})
    assert resolved.vl_config["page_range"] == "4"


def test_resolve_advanced_vl_page_link_missing_pages_raises():
    field = _adv_vl_field()
    with pytest.raises(ValueError):
        resolve_advanced_field(field, {}, {"src": []})

    with pytest.raises(ValueError):
        resolve_advanced_field(field, {}, {})


def test_resolve_advanced_vl_without_page_source_untouched():
    """没配 page_source_field 的 VL 进阶字段不产生 _page_link。"""
    field = _adv_vl_field(vl_config={"page_range": "2-4"})
    field.vl_config.pop("page_source_field")
    resolved, prov = resolve_advanced_field(field, {}, {})
    assert resolved.vl_config["page_range"] == "2-4"
    assert "_page_link" not in prov


# ── 审查修复：成败判定不能被元数据键蒙混 ────────────────────────

def test_has_real_source_refs_ignores_metadata_only():
    """只有 _resolved_refs / _model_pages 等元数据时不算「有来源」。"""
    assert _has_real_source_refs({"_resolved_refs": {"a": "华为"}}) is False
    assert _has_real_source_refs({"_model_pages": [1, 2]}) is False
    assert _has_real_source_refs({"_page_link": {"source_field": "a"}}) is False
    assert _has_real_source_refs(None) is False
    assert _has_real_source_refs({}) is False


def test_has_real_source_refs_true_with_real_group():
    assert _has_real_source_refs({"甲方": [{"text": "命中"}]}) is True
    # 真实命中 + 元数据混在一起也算有来源
    assert _has_real_source_refs(
        {"甲方": [{"text": "命中"}], "_resolved_refs": {"a": "x"}}
    ) is True


def test_has_real_source_refs_false_when_group_empty():
    """键在但列表为空，不算命中。"""
    assert _has_real_source_refs({"甲方": []}) is False


def test_advanced_empty_result_is_not_success():
    """P0 回归：进阶字段解析了引用但什么都没抽到，必须判失败。"""
    from service.extraction_service import _is_extraction_success

    refs = {"_resolved_refs": {"a": "华为"}}
    assert _is_extraction_success("", refs) is False
    # 与同等情况下的普通字段保持一致
    assert _is_extraction_success("", None) is False


# ── 审查修复：引用解析的边界一致性 ──────────────────────────────

def test_resolve_advanced_keeps_non_ref_items_verbatim():
    """列表里不含引用的项原样保留（含刻意留的空白项），只剔除因引用变空的。"""
    field = ExtractionField(
        field_id="adv_list", type_id="default", field_name="列表", source_type="text",
        is_advanced=1, search_type="rule",
        search_config={
            "keywords": ["<field_result>a</field_result>", "<field_result>gone</field_result>", "固定"],
            "stop_words": ["  ", "。"],
        },
        text_extract_prompt="<search_result>x</search_result>",
    )
    resolved, prov = resolve_advanced_field(field, {"a": "甲方"}, {})
    assert resolved.search_config["keywords"] == ["甲方", "固定"]
    # stop_words 不含引用 → 一个字符都不动（普通字段也是这个行为）
    assert resolved.search_config["stop_words"] == ["  ", "。"]
    # 空引用被记录，供上层生成可排查的 reason
    assert prov["_empty_refs"] == ["gone"]


def test_resolve_advanced_resolves_table_name_pattern():
    """table_name_pattern 同时是占位符 label，必须与 prompt 一起解析。"""
    field = ExtractionField(
        field_id="adv_tbl", type_id="default", field_name="表格进阶", source_type="table",
        is_advanced=1,
        table_name_pattern="<field_result>a</field_result>",
        table_match_keywords=["<field_result>a</field_result>"],
        table_extract_prompt="从 <search_result><field_result>a</field_result></search_result> 提取",
    )
    resolved, _ = resolve_advanced_field(field, {"a": "资产负债表"}, {})
    assert resolved.table_name_pattern == "资产负债表"
    assert resolved.table_match_keywords == ["资产负债表"]
    assert "<search_result>资产负债表</search_result>" in resolved.table_extract_prompt


def test_collect_depend_fields_includes_table_name_pattern():
    f = _field(source_type="table", search_type=None,
               table_name_pattern="<field_result>t</field_result>")
    assert collect_depend_fields(f) == ["t"]


def test_collect_depend_fields_page_source_ignored_when_not_page():
    """非 page 检索下残留的 page_source_field 不算依赖（避免幻影依赖被 400 拒绝）。"""
    f = _field(search_type="context",
               search_config={"keywords": ["x"], "page_source_field": "stale"})
    assert collect_depend_fields(f) == []


def test_collect_depend_fields_vl_page_source():
    f = _field(source_type="vl", search_type=None,
               vl_config={"page_source_field": "src", "field_hints": "x"})
    assert collect_depend_fields(f) == ["src"]


def test_collect_depend_fields_vl_page_source_ignored_when_not_vl():
    """非 vl 来源类型下的残留 page_source_field 不算依赖（防幻影依赖）。"""
    f = _field(source_type="text", search_type="context",
               search_config={"keywords": ["x"]},
               vl_config={"page_source_field": "stale"})
    assert collect_depend_fields(f) == []


def test_collect_depend_fields_vl_combines_hints_and_page_source():
    f = _field(source_type="vl", search_type=None,
               vl_config={"page_source_field": "src",
                          "field_hints": "金额 <field_result>a</field_result>"})
    assert set(collect_depend_fields(f)) == {"a", "src"}
