"""测试 MinerU API 连通性，排查 502 问题。"""

import asyncio
import sys
import os
from pathlib import Path

import httpx

# 修复 Windows 终端编码
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = "http://36.151.147.207:7078"
TIMEOUT = 120  # 测试用较短超时

# 你要测试的 PDF 文件路径，可通过命令行参数传入
DEFAULT_PDF = "翻译-Attention is all you need.pdf"


async def test_connection():
    """测试 1: 基本连通性"""
    print("=" * 60)
    print("[测试 1] 基本连通性 - GET /")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(BASE_URL)
            print(f"  状态码: {resp.status_code}")
            print(f"  响应: {resp.text[:200]}")
    except Exception as e:
        print(f"  失败: {e}")


async def test_parse(pdf_path: str, field_name: str, extra_data: dict, label: str):
    """通用测试函数"""
    print("=" * 60)
    print(f"[{label}]")
    print(f"  字段名: {field_name}")
    print(f"  参数: {extra_data}")

    path = Path(pdf_path)
    if not path.exists():
        print(f"  跳过: 文件不存在 {pdf_path}")
        return

    file_content = path.read_bytes()
    print(f"  文件大小: {len(file_content)} bytes")

    url = f"{BASE_URL}/file_parse"
    files = {field_name: (path.name, file_content, "application/pdf")}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(url, files=files, data=extra_data)
            print(f"  状态码: {resp.status_code}")
            if resp.status_code == 200:
                result = resp.json()
                # 打印结构，不打印全部内容
                print(f"  响应 keys: {list(result.keys())}")
                results = result.get("results", {})
                if results:
                    first_key = next(iter(results))
                    md = results[first_key].get("md_content", "")
                    print(f"  md_content 长度: {len(md)}")
                    print(f"  md_content 前 200 字符: {md[:200]}")
                else:
                    print(f"  响应内容: {str(result)[:300]}")
            else:
                print(f"  响应: {resp.text[:500]}")
    except httpx.TimeoutException:
        print(f"  超时 (>{TIMEOUT}s)")
    except Exception as e:
        print(f"  失败: {type(e).__name__}: {e}")


async def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF

    print(f"测试 MinerU API: {BASE_URL}")
    print(f"测试文件: {pdf_path}")
    print()

    # 测试 0: 网络可达性
    print("=" * 60)
    print("[测试 0] 网络可达性 - ping")
    ret = os.system("ping -n 1 -w 3000 36.151.147.207 > nul 2>&1")
    print(f"  ping 结果: {'可达' if ret == 0 else '不可达'}")

    # 测试 1: 基本连通
    await test_connection()

    # 测试 2: 用代码中的方式 (字段名 "files" + 全部参数)
    await test_parse(
        pdf_path,
        field_name="files",
        extra_data={
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_md": "true",
            "return_images": "false",
            "start_page_id": "0",
            "end_page_id": "99999",
            "parse_method": "auto",
            "lang_list": "ch",
            "output_dir": "./test_output",
            "backend": "vllm-async-engine",
        },
        label="测试 2: 当前代码方式 (field=files, 全部参数)",
    )

    # 测试 3: 字段名改为 "file" (单数)
    await test_parse(
        pdf_path,
        field_name="file",
        extra_data={
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_md": "true",
            "return_images": "false",
            "start_page_id": "0",
            "end_page_id": "99999",
            "parse_method": "auto",
            "lang_list": "ch",
            "output_dir": "./test_output",
            "backend": "vllm-async-engine",
        },
        label="测试 3: 字段名改��� file (单数)",
    )

    # 测试 4: 最小参数 (只传文件，不传额外 data)
    await test_parse(
        pdf_path,
        field_name="files",
        extra_data={},
        label="测试 4: 最小参数 (field=files, 无额外参数)",
    )

    # 测试 5: 最小参数 + file 字段名
    await test_parse(
        pdf_path,
        field_name="file",
        extra_data={},
        label="测试 5: 最小参数 (field=file, 无额外参数)",
    )

    # 测试 6: 去掉 backend 参数
    await test_parse(
        pdf_path,
        field_name="files",
        extra_data={
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_md": "true",
            "return_images": "false",
            "start_page_id": "0",
            "end_page_id": "99999",
            "parse_method": "auto",
            "lang_list": "ch",
            "output_dir": "./test_output",
        },
        label="测试 6: 去掉 backend 参数",
    )

    print()
    print("=" * 60)
    print("测试完成。对比哪些返回 200，哪些返回 502，即可定位问题。")


if __name__ == "__main__":
    asyncio.run(main())
