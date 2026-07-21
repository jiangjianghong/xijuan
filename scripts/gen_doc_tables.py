"""从 docs/openapi.json 生成 / 刷新 Markdown 里的 AUTOGEN 表格区块。

区块语法:
    <!-- AUTOGEN:<kind> <METHOD> <path> [status=200] -->
    ...(生成内容, 勿手改)...
    <!-- /AUTOGEN:<kind> -->

kind ∈ path-params | query-params | request-body | response | endpoint-index
键用「方法 + 路径」(人读、稳定, 不依赖 operationId)。生成器只改写标记之间的内容,
标记之外(一句话 / 示例 / curl / 备注)由人手写。

用法:
    uv run python scripts/gen_doc_tables.py          # 写入
    uv run python scripts/gen_doc_tables.py --check   # 只校验, 有 diff 退出码 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OPENAPI = DOCS / "openapi.json"

# 路径前缀 → (分组名, 文档相对路径)
PREFIX_MAP = [
    ("/doctype", "文档类型", "api/doctype.md"),
    ("/file", "文件处理", "api/file.md"),
    ("/extraction", "字段提取", "api/extraction.md"),
    ("/analysis", "逻辑分析", "api/analysis.md"),
    ("/search", "向量检索", "api/search.md"),
    ("/log", "日志", "api/logs.md"),
]

# openapi 里为裸 object / Any 的复杂字段 → 只出类型 + 指向唯一权威页链接
AUTHORITY = {
    "source_refs": "../guides/source-refs.md",
    "vl_config": "../reference/data-model.md#extraction_field",
    "search_config": "../reference/data-model.md#extraction_field",
    "web_search": "../guides/analysis-config.md",
    "page_mapping": "../reference/data-model.md#file_content",
    "middle_json": "../reference/data-model.md#file_content",
    "input_values": "../reference/data-model.md#analysis_result",
}

BLOCK_RE = re.compile(
    r"(?P<open><!--\s*AUTOGEN:(?P<kind>[\w-]+)(?P<args>[^>]*?)-->)"
    r".*?"
    r"(?P<close><!--\s*/AUTOGEN:(?P=kind)\s*-->)",
    re.DOTALL,
)


def load_openapi() -> dict:
    return json.loads(OPENAPI.read_text(encoding="utf-8"))


def _parse_args(argstr: str) -> dict:
    toks = argstr.split()
    out = {"method": None, "path": None, "status": "200"}
    if toks and toks[0].isupper():
        out["method"] = toks[0].lower()
        if len(toks) > 1:
            out["path"] = toks[1]
    for tok in toks:
        if tok.startswith("status="):
            out["status"] = tok.split("=", 1)[1]
    return out


def _resolve(spec: dict, schema) -> dict:
    """跟随 $ref 解析到 components 里的实体 schema。"""
    if not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return _resolve(spec, spec.get("components", {}).get("schemas", {}).get(name, {}))
    return schema


def _type_str(schema) -> str:
    if not isinstance(schema, dict):
        return "object"
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "anyOf" in schema:
        parts = [_type_str(s) for s in schema["anyOf"] if s.get("type") != "null"]
        return " | ".join(dict.fromkeys(parts)) or "object"
    t = schema.get("type")
    if t == "array":
        return f"array[{_type_str(schema.get('items', {}))}]"
    if isinstance(t, list):
        return " | ".join(x for x in t if x != "null")
    return t or "object"


def _nullable(schema, name, required) -> bool:
    if name not in required:
        return True
    if isinstance(schema, dict) and "anyOf" in schema:
        return any(s.get("type") == "null" for s in schema["anyOf"])
    return False


def _desc(name, schema) -> str:
    d = schema.get("description", "") if isinstance(schema, dict) else ""
    if name in AUTHORITY:
        link = f"[{name}]({AUTHORITY[name]})"
        return f"{d}（结构详见 {link}）" if d else f"结构详见 {link}"
    return d


def _props_table(spec, schema, required_col: bool) -> str:
    schema = _resolve(spec, schema)
    props = schema.get("properties")
    if not props:
        return "无结构化字段（见示例 / 权威页）"
    required = set(schema.get("required", []))
    if required_col:
        lines = ["| 字段 | 类型 | 必填 | 默认 | 说明 |", "|---|---|:--:|---|---|"]
        for name, p in props.items():
            p = p if isinstance(p, dict) else {}
            default = p.get("default", "—")
            lines.append(f"| {name} | {_type_str(p)} | {'是' if name in required else '否'} | {default} | {_desc(name, p)} |")
    else:
        lines = ["| 字段 | 类型 | 可空 | 说明 |", "|---|---|:--:|---|"]
        for name, p in props.items():
            p = p if isinstance(p, dict) else {}
            lines.append(f"| {name} | {_type_str(p)} | {'是' if _nullable(p, name, required) else '否'} | {_desc(name, p)} |")
    return "\n".join(lines)


def _params_table(op, loc: str) -> str:
    params = [p for p in op.get("parameters", []) if p.get("in") == loc]
    if not params:
        return "无"
    if loc == "path":
        lines = ["| 参数 | 类型 | 必填 | 说明 |", "|---|---|:--:|---|"]
        for p in params:
            lines.append(f"| {p['name']} | {_type_str(p.get('schema', {}))} | {'是' if p.get('required') else '否'} | {p.get('description', '')} |")
        return "\n".join(lines)
    lines = ["| 参数 | 类型 | 必填 | 默认 | 说明 |", "|---|---|:--:|---|---|"]
    for p in params:
        sc = p.get("schema", {})
        default = sc.get("default", "—")
        desc = p.get("description", "")
        enum = sc.get("enum")
        if enum:
            desc = f"{desc}（可选: {' / '.join(map(str, enum))}）".strip()
        lines.append(f"| {p['name']} | {_type_str(sc)} | {'是' if p.get('required') else '否'} | {default} | {desc} |")
    return "\n".join(lines)


def _response_table(spec, op, status) -> str:
    resp = op.get("responses", {}).get(status) or op.get("responses", {}).get(str(status), {})
    schema = _resolve(spec, resp.get("content", {}).get("application/json", {}).get("schema", {}))
    data = schema.get("properties", {}).get("data")
    if data is None:
        return "无（该接口 data 为空 / 见备注）"
    data = _resolve(spec, data)
    if data.get("type") == "array":
        items = _resolve(spec, data.get("items", {}))
        return "_data 为数组，每个元素：_\n\n" + _props_table(spec, items, required_col=False)
    return _props_table(spec, data, required_col=False)


def _group_doc(path):
    for pre, grp, doc in PREFIX_MAP:
        if path.startswith(pre):
            return grp, doc
    return "其他", "api/overview.md"


def _endpoint_index(spec) -> str:
    lines = ["| 方法 | 路径 | 分组 | 摘要 | 文档 |", "|---|---|---|---|---|"]
    for path, item in sorted(spec.get("paths", {}).items()):
        for method, op in sorted(item.items()):
            grp, doc = _group_doc(path)
            lines.append(f"| {method.upper()} | `{path}` | {grp} | {op.get('summary', '')} | [{doc}]({doc}) |")
    return "\n".join(lines)


def render_table(spec, kind, method=None, path=None, status="200") -> str:
    if kind == "endpoint-index":
        return _endpoint_index(spec)
    method = (method or "").lower()
    op = spec.get("paths", {}).get(path, {}).get(method, {})
    if kind == "path-params":
        return _params_table(op, "path")
    if kind == "query-params":
        return _params_table(op, "query")
    if kind == "request-body":
        schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        return _props_table(spec, schema, required_col=True)
    if kind == "response":
        return _response_table(spec, op, status)
    raise ValueError(f"unknown AUTOGEN kind: {kind}")


def fill_blocks(md: str, spec: dict) -> str:
    def repl(m):
        a = _parse_args(m.group("args"))
        table = render_table(spec, m.group("kind"), a["method"], a["path"], a["status"])
        return f"{m.group('open')}\n{table}\n{m.group('close')}"

    return BLOCK_RE.sub(repl, md)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验有无过期,不写入")
    args = ap.parse_args()
    spec = load_openapi()
    changed = []
    for md in DOCS.rglob("*.md"):
        if "superpowers" in md.parts:
            continue
        old = md.read_text(encoding="utf-8")
        new = fill_blocks(old, spec)
        if new != old:
            changed.append(md.relative_to(ROOT))
            if not args.check:
                md.write_text(new, encoding="utf-8")
    if args.check and changed:
        print("AUTOGEN 过期(需跑 gen_doc_tables):\n" + "\n".join(f"  {p}" for p in changed))
        return 1
    print(("需更新" if args.check else "已更新") + f" {len(changed)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
