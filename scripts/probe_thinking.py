"""临时探针：用 config.yaml 里的配置发一个最小请求，验证
extra_body.chat_template_kwargs.enable_thinking 能否被服务端接受并正常返回。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from utils.config import get_config
from utils.llm_client import chat_completion
from utils.vl_client import vl_chat


async def raw_probe(label: str, base_url: str, model: str, api_key: str) -> None:
    """直接 httpx 打 chat/completions，打印完整 payload + 返回，方便对比 curl。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hello, 一句话介绍你自己"}],
        "max_tokens": 100,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"{base_url.rstrip('/')}/chat/completions"
    print(f"\n=== [{label}] RAW probe ===")
    print(f"POST {url}")
    print(f"payload = {json.dumps(payload, ensure_ascii=False)}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
        print(f"status = {resp.status_code}")
        print(f"body   = {resp.text[:600]}")
    except Exception as e:
        print(f"RAW probe error: {type(e).__name__}: {e}")


async def via_chat_completion(label: str, **overrides) -> None:
    print(f"\n=== [{label}] via chat_completion ===")
    try:
        text = await chat_completion("hello, 一句话介绍你自己", **overrides)
        print(f"OK -> {text!r}")
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")


async def via_vl_chat() -> None:
    print("\n=== [vl_model] via vl_chat (text-only) ===")
    try:
        resp = await vl_chat(
            [{"role": "user", "content": "hello, 一句话介绍你自己"}],
            max_tokens=100,
        )
        content = resp["choices"][0]["message"]["content"]
        print(f"OK -> {content!r}")
        print(f"usage = {resp.get('usage')}")
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")


async def main() -> None:
    cfg = get_config()
    ex = cfg.extraction
    tn = cfg.table_name_validation
    vl = cfg.vl_model

    print("配置摘要:")
    print(f"  extraction.base_url = {ex.llm_base_url}")
    print(f"  extraction.model    = {ex.llm_model}")
    print(f"  extraction.thinking = {ex.enable_thinking}")
    print(f"  table_name.base_url = {tn.llm_base_url}")
    print(f"  table_name.model    = {tn.llm_model}")
    print(f"  table_name.thinking = {tn.enable_thinking}")
    print(f"  vl_model.base_url   = {vl.base_url}")
    print(f"  vl_model.model      = {vl.model}")
    print(f"  vl_model.thinking   = {vl.enable_thinking}")

    await via_chat_completion("extraction(默认)")

    tn_base = tn.llm_base_url or ex.llm_base_url
    tn_model = tn.llm_model or ex.llm_model
    tn_key = tn.llm_api_key or ex.llm_api_key
    await via_chat_completion(
        "table_name_validation(自部署 qwen3.5-122b)",
        base_url=tn_base,
        model=tn_model,
        api_key=tn_key,
    )
    await raw_probe("table_name_validation 原生 curl 对照", tn_base, tn_model, tn_key)

    await via_vl_chat()


if __name__ == "__main__":
    asyncio.run(main())
