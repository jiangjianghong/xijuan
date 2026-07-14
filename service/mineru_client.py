"""MinerU 解析客户端：调用 MinerU 服务解析 PDF 文件。"""

from __future__ import annotations

import json
from typing import Dict, Optional

import httpx
from loguru import logger

from utils.config import get_config


async def parse_pdf(
    file_name: str,
    file_content: bytes,
    *,
    file_id: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[int] = None,
    max_parse_pages: Optional[int] = None,
) -> Dict[str, str]:
    """调用 MinerU 服务解析 PDF 文件。

    请求方式: POST {base_url}/file_parse
    - files: multipart 文件上传
    - data: 表单参数

    Args:
        file_name: 文件名。
        file_content: 文件二进制内容。
        base_url: MinerU 服务地址，默认从配置读取。
        timeout: 超时秒数，默认从配置读取。
        max_parse_pages: 最大解析页数；为空时解析全部页。

    Returns:
        包含 md_content 和 middle_json 的字典。
    """
    cfg = get_config().mineru
    base_url = base_url or cfg.base_url
    timeout = timeout or cfg.parse_timeout
    if max_parse_pages is not None and max_parse_pages <= 0:
        max_parse_pages = None

    url = f"{base_url.rstrip('/')}/file_parse"

    files = {"files": (file_name, file_content, "application/pdf")}
    end_page_id = str(max_parse_pages - 1) if max_parse_pages else "99999"
    data = {
        "return_middle_json": "true",
        "return_model_output": "false",
        "return_md": "true",
        "return_images": "false",
        "start_page_id": "0",
        "end_page_id": end_page_id,
        "parse_method": "auto",
        "lang_list": "ch",
        "output_dir": f"./{file_id}" if file_id else ".",
        "backend": cfg.backend,
    }

    page_limit = f"前 {max_parse_pages} 页" if max_parse_pages else "全部页"
    logger.info("调用 MinerU 解析: file_name={}, page_limit={}", file_name, page_limit)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, files=files, data=data)
        resp.raise_for_status()
        result = resp.json()

        # 从 results 中提取 md_content 和 middle_json
        # 响应格式: {"results": {"文件名(无后缀)": {"md_content": "...", "middle_json": "..."}}}
        results = result.get("results", {})
        if results:
            first_result = next(iter(results.values()), {})
            md_content = first_result.get("md_content", "")
            middle_json_raw = first_result.get("middle_json")
            # middle_json 可能是 dict 或 str，统一转为 str 存储
            if middle_json_raw and not isinstance(middle_json_raw, str):
                middle_json_str = json.dumps(middle_json_raw, ensure_ascii=False)
            else:
                middle_json_str = middle_json_raw or ""
            return {
                "md_content": md_content,
                "middle_json": middle_json_str,
            }
        return {"md_content": "", "middle_json": ""}
