"""测试 MinerU 返回的页码信息，探索 middle_json 结构。"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = "http://36.151.147.207:7078"
TIMEOUT = 300
DEFAULT_PDF = r"C:\Users\19404\Desktop\JOB_DATA\files\NIPS-2017-attention-is-all-you-need-Paper.pdf"


async def test_middle_json(pdf_path: str):
    """请求 MinerU 返回 middle_json，探索页码信息。"""
    path = Path(pdf_path)
    if not path.exists():
        print(f"文件不存在: {pdf_path}")
        return

    file_content = path.read_bytes()
    print(f"文件: {path.name} ({len(file_content)} bytes)")
    print()

    url = f"{BASE_URL}/file_parse"
    files = {"files": (path.name, file_content, "application/pdf")}

    # 开启 middle_json，只解析前 3 页以加快速度
    data = {
        "return_middle_json": "true",
        "return_model_output": "false",
        "return_md": "true",
        "return_images": "false",
        "start_page_id": "0",
        "end_page_id": "2",
        "parse_method": "auto",
        "lang_list": "ch",
        "output_dir": "./test_pages",
        "backend": "vllm-async-engine",
    }

    print("请求参数:")
    for k, v in data.items():
        print(f"  {k}: {v}")
    print()
    print("正在请求 MinerU (可能需要几十秒)...")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(url, files=files, data=data)
        print(f"状态码: {resp.status_code}")

        if resp.status_code != 200:
            print(f"错误: {resp.text[:500]}")
            return

        result = resp.json()

    # 保存完整响应到文件，方便查看
    with open("test_mineru_response.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("完整响应已保存到: test_mineru_response.json")
    print()

    # 分析响应结构
    print("=" * 60)
    print("响应顶层 keys:", list(result.keys()))
    print()

    results = result.get("results", {})
    if not results:
        print("results 为空")
        return

    for file_key, file_result in results.items():
        print(f"文件 key: {file_key}")
        print(f"  子 keys: {list(file_result.keys())}")
        print()

        # md_content
        md = file_result.get("md_content", "")
        if md:
            print(f"  md_content 长度: {len(md)}")
            print(f"  md_content 前 300 字符:")
            print(f"  {md[:300]}")
            print()

        # middle_json - 这是关键
        middle_json = file_result.get("middle_json")
        if middle_json is None:
            print("  middle_json: 无 (None)")
            print()
            # 检查其他可能包含页码的字段
            for k, v in file_result.items():
                if k == "md_content":
                    continue
                if isinstance(v, str) and len(v) > 500:
                    print(f"  {k}: (长字符串, len={len(v)})")
                elif isinstance(v, (list, dict)):
                    print(f"  {k}: type={type(v).__name__}, len={len(v)}")
                    if isinstance(v, list) and v:
                        print(f"    第一个元素 type: {type(v[0]).__name__}")
                        if isinstance(v[0], dict):
                            print(f"    第一个元素 keys: {list(v[0].keys())}")
                            # 打印第一个元素
                            first_str = json.dumps(v[0], ensure_ascii=False)
                            print(f"    第一个元素: {first_str[:500]}")
                    elif isinstance(v, dict) and v:
                        print(f"    keys: {list(v.keys())[:20]}")
                else:
                    print(f"  {k}: {v}")
            continue

        # 分析 middle_json 结构
        if isinstance(middle_json, str):
            try:
                middle_json = json.loads(middle_json)
            except json.JSONDecodeError:
                print(f"  middle_json: 字符串但非 JSON, 前 300 字符: {middle_json[:300]}")
                continue

        print(f"  middle_json type: {type(middle_json).__name__}")

        if isinstance(middle_json, list):
            print(f"  middle_json 长度: {len(middle_json)}")
            for i, item in enumerate(middle_json[:5]):  # 只看前 5 个
                print(f"\n  --- middle_json[{i}] ---")
                if isinstance(item, dict):
                    print(f"    keys: {list(item.keys())}")
                    for k, v in item.items():
                        if isinstance(v, str) and len(v) > 200:
                            print(f"    {k}: (string, len={len(v)}) {v[:100]}...")
                        elif isinstance(v, (list, dict)):
                            v_str = json.dumps(v, ensure_ascii=False)
                            if len(v_str) > 300:
                                print(f"    {k}: {v_str[:300]}...")
                            else:
                                print(f"    {k}: {v_str}")
                        else:
                            print(f"    {k}: {v}")
                else:
                    item_str = str(item)
                    print(f"    {item_str[:300]}")

        elif isinstance(middle_json, dict):
            print(f"  middle_json keys: {list(middle_json.keys())}")
            for k, v in middle_json.items():
                if isinstance(v, list):
                    print(f"  {k}: list, len={len(v)}")
                    if v and isinstance(v[0], dict):
                        print(f"    第一个元素 keys: {list(v[0].keys())}")
                        first_str = json.dumps(v[0], ensure_ascii=False)
                        print(f"    第一个元素: {first_str[:500]}")
                elif isinstance(v, dict):
                    print(f"  {k}: dict, keys={list(v.keys())[:10]}")
                elif isinstance(v, str) and len(v) > 200:
                    print(f"  {k}: string, len={len(v)}")
                else:
                    print(f"  {k}: {v}")

    print()
    print("=" * 60)
    print("请查看 test_mineru_response.json 获取完整响应")


if __name__ == "__main__":
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    asyncio.run(test_middle_json(pdf_path))
