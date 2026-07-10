"""vl_service 内部共享工具。"""

from __future__ import annotations

import json
import re
from typing import Any

from utils.text_utils import normalize_cjk_quotes


_THINK_PATTERN = re.compile(r"<think>[\s\S]*?</think>", re.DOTALL)


def strip_think_tags(text: str) -> str:
    """去掉 Qwen 思考类模型输出的 <think>...</think> 块。"""
    return _THINK_PATTERN.sub("", text).strip()


def parse_vl_json_response(response: str) -> tuple[str, str]:
    """解析 VL 输出为 (value, reason)。

    解析顺序：
    1. 剥 <think> 标签
    2. 尝试从 ```json ... ``` 围栏取 JSON
    3. 尝试 json.loads 整段
    4. 兜底正则找 {"value":...} 子串
    5. 解析失败 → (raw_text, "")
    """
    text = strip_think_tags(response)

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            return _extract_value_reason(data)
        except json.JSONDecodeError:
            pass

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _extract_value_reason(data)
    except json.JSONDecodeError:
        pass

    obj = re.search(r"\{[^{}]*\"value\"[^{}]*\}", text, re.DOTALL)
    if obj:
        try:
            data = json.loads(obj.group())
            return _extract_value_reason(data)
        except json.JSONDecodeError:
            pass

    return text.strip(), ""


def _extract_value_reason(data: dict) -> tuple[str, str]:
    raw_value = data.get("value", "")
    if isinstance(raw_value, (list, dict)):
        # list/dict 序列化为 JSON，结构性双引号必须保留，不做引号规范化
        value = json.dumps(raw_value, ensure_ascii=False)
    else:
        value = normalize_cjk_quotes(str(raw_value).strip())
    reason = normalize_cjk_quotes(str(data.get("reason", "")).strip())
    return value, reason


def build_image_messages(
    *,
    prompt: str,
    b64_images: list[str],
    system_prompt: str | None,
    image_mime: str = "image/png",
) -> list[dict[str, Any]]:
    """构建 OpenAI 兼容的多图消息体。

    - 无图：text-only message
    - 有图：图片块在前，text 在最后
    - system_prompt 非空时插入 system 消息
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if not b64_images:
        messages.append({"role": "user", "content": prompt})
        return messages

    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b}"}}
        for b in b64_images
    ]
    content.append({"type": "text", "text": prompt})
    messages.append({"role": "user", "content": content})
    return messages
