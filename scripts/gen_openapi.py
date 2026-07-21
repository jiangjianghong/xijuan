"""导出富化后的 docs/openapi.json（唯一 schema 权威，勿手改）。

用法:
    uv run python scripts/gen_openapi.py

行为:
    构造仅注册路由的 FastAPI（无 lifespan，避免启动 DB/Milvus）→ app.openapi()
    → utils.openapi_enrich.enrich 富化 → 写 docs/openapi.json。
    富化逻辑与版本号与活的 /docs 共用 utils.openapi_enrich，保证两者永远一致。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI

from blue_print import register_routers
from utils.openapi_enrich import enrich, get_version


def build_app() -> FastAPI:
    """仅注册路由的 app，用于导出 schema（不带 lifespan，不连外部依赖）。"""
    app = FastAPI(title="析卷 AI", version=get_version())
    register_routers(app)
    return app


def main() -> None:
    out_path = _ROOT / "docs" / "openapi.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("构造 FastAPI app...")
    schema = build_app().openapi()

    print("富化 schema...")
    schema = enrich(schema)

    out_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"done. {out_path} ({len(schema.get('paths', {}))} paths, v{schema['info']['version']}, {out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
