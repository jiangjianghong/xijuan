"""临时诊断脚本:调真实 MinerU 验证 return_content_list 的产物结构。

用法: PYTHONIOENCODING=utf-8 python scripts/test_content_list.py
产物落盘 _content_list_test/ 目录(md / middle.json / content_list.json)。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE_URL = "http://36.151.147.207:7078"
PDF_PATH = Path(r"C:\Users\19404\Desktop\Projects\wanz_prase2_001\应对气候变化规划.pdf")
OUT_DIR = Path(r"C:\Users\19404\Desktop\Projects\wanz_prase2_001\_content_list_test")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    pdf_bytes = PDF_PATH.read_bytes()
    print(f"PDF: {PDF_PATH.name}, {len(pdf_bytes)} bytes", flush=True)

    files = {"files": (PDF_PATH.name, pdf_bytes, "application/pdf")}
    data = {
        "return_middle_json": "true",
        "return_model_output": "false",
        "return_md": "true",
        "return_content_list": "true",
        "return_images": "false",
        "start_page_id": "0",
        "end_page_id": "99999",
        "parse_method": "auto",
        "lang_list": "ch",
        "output_dir": "./_content_list_probe",
        "backend": "vllm-async-engine",
    }

    t0 = time.time()
    print("POST /file_parse (return_content_list=true) ...", flush=True)
    resp = httpx.post(f"{BASE_URL}/file_parse", files=files, data=data, timeout=1200)
    print(f"HTTP {resp.status_code}, 耗时 {time.time() - t0:.0f}s", flush=True)
    resp.raise_for_status()

    result = resp.json()
    results = result.get("results", {})
    if not results:
        print("results 为空! 顶层键:", list(result.keys()))
        sys.exit(1)

    first = next(iter(results.values()))
    print("单文件结果键:", list(first.keys()), flush=True)

    md = first.get("md_content", "")
    middle = first.get("middle_json")
    content_list = first.get("content_list")

    (OUT_DIR / "1.md").write_text(md, encoding="utf-8")
    if middle is not None:
        if not isinstance(middle, str):
            middle = json.dumps(middle, ensure_ascii=False)
        (OUT_DIR / "middle.json").write_text(middle, encoding="utf-8")
    if content_list is not None:
        if not isinstance(content_list, str):
            content_list = json.dumps(content_list, ensure_ascii=False)
        (OUT_DIR / "content_list.json").write_text(content_list, encoding="utf-8")

    print(f"md 长度: {len(md)}")
    print(f"middle_json: {'有' if middle else '无'} ({len(middle) if middle else 0} 字符)")
    print(f"content_list: {'有' if content_list else '无'} ({len(content_list) if content_list else 0} 字符)")

    if content_list:
        cl = json.loads(content_list)
        print(f"content_list 项数: {len(cl)}")
        # 类型分布与字段结构
        from collections import Counter
        types = Counter(item.get("type") for item in cl)
        print("类型分布:", dict(types))
        seen_types = set()
        for item in cl:
            t = item.get("type")
            if t not in seen_types:
                seen_types.add(t)
                keys = {k: type(v).__name__ for k, v in item.items()}
                print(f"  [{t}] 字段: {keys}")
        # bbox 覆盖情况
        with_bbox = sum(1 for item in cl if item.get("bbox"))
        print(f"带 bbox 的项: {with_bbox}/{len(cl)}")
        # page_idx 覆盖
        with_page = sum(1 for item in cl if "page_idx" in item)
        print(f"带 page_idx 的项: {with_page}/{len(cl)}")


if __name__ == "__main__":
    main()
