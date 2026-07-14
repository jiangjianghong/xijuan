"""真实 MinerU 产物回归:全局唯一锚 build_page_mapping(应对气候变化规划.pdf,14 页)。"""

import json
from pathlib import Path

from utils.page_mapping import build_page_mapping, lookup_page_num

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "page_mapping_sample"


def _load_sample():
    md = (_FIXTURE_DIR / "1.md").read_text(encoding="utf-8")
    middle = json.loads((_FIXTURE_DIR / "middle.json").read_text(encoding="utf-8"))
    return md, middle


def test_real_sample_monotonic_and_covers_pages():
    md, middle = _load_sample()
    mapping = build_page_mapping(md, middle)
    assert mapping, "真实产物应产出锚点"
    # 锚点按位置单调,页码非降(LIS 保证)
    starts = [m["start_pos"] for m in mapping]
    pages = [m["page_num"] for m in mapping]
    assert starts == sorted(starts)
    assert pages == sorted(pages)
    # 覆盖多页(14 页文档,至少覆盖到第 10 页以上)
    assert max(pages) >= 10


def test_real_sample_bbox_within_page_size():
    md, middle = _load_sample()
    mapping = build_page_mapping(md, middle)
    with_bbox = [m for m in mapping if "bbox" in m and "page_size" in m]
    assert with_bbox, "真实产物应有 bbox"
    for m in with_bbox:
        w, h = m["page_size"]
        x0, y0, x1, y1 = m["bbox"]
        assert 0 <= x0 <= x1 <= w
        assert 0 <= y0 <= y1 <= h


def test_real_sample_known_page_lookup():
    md, middle = _load_sample()
    mapping = build_page_mapping(md, middle)
    # 第 4 页的表格「专栏1 非二氧化碳温室气体管控工程」
    pos = md.find("专栏1 非二氧化碳温室气体管控工程")
    assert pos != -1
    assert lookup_page_num(mapping, pos, pos + 10) == "4"
