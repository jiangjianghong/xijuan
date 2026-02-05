"""MinerU 解析客户端：调用 MinerU 服务解析 PDF 文件。"""

from __future__ import annotations

from typing import Optional

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
) -> str:
    """调用 MinerU 服务解析 PDF 文件。

    请求方式: POST {base_url}/file_parse
    - files: multipart 文件上传
    - data: 表单参数

    Args:
        file_name: 文件名。
        file_content: 文件二进制内容。
        base_url: MinerU 服务地址，默认从配置读取。
        timeout: 超时秒数，默认从配置读取。

    Returns:
        解析后的 Markdown 文本。
    """
    cfg = get_config().mineru
    base_url = base_url or cfg.base_url
    timeout = timeout or cfg.parse_timeout

    url = f"{base_url.rstrip('/')}/file_parse"

    files = {"files": (file_name, file_content, "application/pdf")}
    data = {
        "return_middle_json": "false",
        "return_model_output": "false",
        "return_md": "true",
        "return_images": "false",
        "start_page_id": "0",
        "end_page_id": "99999",
        "parse_method": "auto",
        "lang_list": "ch",
        "output_dir": f"./{file_id}" if file_id else ".",
        "backend": cfg.backend,
    }

    logger.info("调用 MinerU 解析: file_name={}", file_name)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, files=files, data=data)
        resp.raise_for_status()
        result = resp.json()

        # 从 results 中提取 md_content
        # 响应格式: {"results": {"文件名(无后缀)": {"md_content": "..."}}}
        results = result.get("results", {})
        if results:
            # 获取第一个结果的 md_content
            first_result = next(iter(results.values()), {})
            return first_result.get("md_content", "")
        return ""
