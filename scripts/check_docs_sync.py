"""校验手写 Markdown 与生成 OpenAPI 是否统一。退出码非 0 即失败。

前置(必须): 先跑 `scripts/gen_openapi.py` 从运行中的 app 重生成 docs/openapi.json,
否则整条链在拿陈旧快照自证,代码↔openapi 的偏差会漏网。

校验项:
    1. 接口全集: 每个 openapi operation 至少被一个 AUTOGEN 块引用(无漏记),
       且无 AUTOGEN 块引用 openapi 不存在的接口(无幽灵)。
    2. 版本: pyproject == openapi.info.version == 各 md 头「对应服务版本」。
    3. AUTOGEN 新鲜度: gen_doc_tables 重新生成后无 diff。

用法:
    uv run python scripts/check_docs_sync.py
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
OPENAPI = DOCS / "openapi.json"

REF_RE = re.compile(r"<!--\s*AUTOGEN:[\w-]+\s+([A-Z]+)\s+(\S+)")
VER_RE = re.compile(r"对应服务版本\s*([0-9]+\.[0-9]+\.[0-9]+)")


def _md_files():
    for md in DOCS.rglob("*.md"):
        if "superpowers" not in md.parts:
            yield md


def referenced_ops() -> set:
    ops = set()
    for md in _md_files():
        for method, path in REF_RE.findall(md.read_text(encoding="utf-8")):
            ops.add((method, path))
    return ops


def openapi_ops(spec: dict) -> set:
    return {(m.upper(), p) for p, item in spec.get("paths", {}).items() for m in item}


def diff_coverage(spec: dict, referenced: set):
    live = openapi_ops(spec)
    return live - referenced, referenced - live  # (missing 漏记, phantom 幽灵)


def version_ok(*versions: str) -> bool:
    return len(set(versions)) == 1


def pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def main() -> int:
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    problems = []

    missing, phantom = diff_coverage(spec, referenced_ops())
    if missing:
        problems.append("文档漏记接口(openapi 有、AUTOGEN 未引用):\n" + "\n".join(f"  {m} {p}" for m, p in sorted(missing)))
    if phantom:
        problems.append("文档幽灵接口(AUTOGEN 引用、openapi 没有):\n" + "\n".join(f"  {m} {p}" for m, p in sorted(phantom)))

    py_v = pyproject_version()
    oa_v = spec.get("info", {}).get("version", "")
    md_vs = {v for md in _md_files() for v in VER_RE.findall(md.read_text(encoding="utf-8"))}
    if md_vs and not version_ok(py_v, oa_v, *md_vs):
        problems.append(f"版本不一致: pyproject={py_v} openapi={oa_v} md={sorted(md_vs)}")

    try:
        from scripts import gen_doc_tables

        stale = []
        for md in _md_files():
            old = md.read_text(encoding="utf-8")
            if gen_doc_tables.fill_blocks(old, spec) != old:
                stale.append(md.relative_to(ROOT))
        if stale:
            problems.append("AUTOGEN 过期(需跑 gen_doc_tables):\n" + "\n".join(f"  {p}" for p in stale))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"AUTOGEN 新鲜度检查失败: {exc}")

    if problems:
        print("\n\n".join(problems))
        return 1
    print("docs sync OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
