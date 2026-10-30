"""让在线与导出的 OpenAPI 同时声明真实鉴权要求。"""

import re

from utils.auth_middleware import BUSINESS_ROUTES, COOKIE_NAME


def apply_auth_schema(schema: dict) -> set[tuple[str, str]]:
    schema.setdefault("components", {}).setdefault("securitySchemes", {}).update({
        "AdminSession": {"type": "apiKey", "in": "cookie", "name": COOKIE_NAME,
                         "description": "通过 /auth/login 获取管理员会话；写请求附加 X-Requested-With: XMLHttpRequest。"},
        "APIKey": {"type": "http", "scheme": "bearer",
                   "description": "设置 → 接口访问控制创建的 xj_ 密钥，仅允许业务接口。"},
    })
    summaries = {
        ("/auth/login", "post"): "管理员登录",
        ("/auth/session", "get"): "查询管理员会话",
        ("/auth/logout", "post"): "退出管理员登录",
        ("/settings/access", "get"): "查看 API Key 与 IP 白名单",
        ("/settings/access/keys", "post"): "创建 API Key（完整密钥仅展示一次）",
        ("/settings/access/allowlist", "post"): "添加 IP/CIDR 白名单",
        ("/settings/access/{kind}/{entry_id}", "patch"): "启用或禁用访问条目",
        ("/settings/access/{kind}/{entry_id}", "delete"): "删除访问条目",
        ("/settings/access/keys/{entry_id}/rotate", "post"): "重新生成 API Key（旧密钥立即失效）",
    }
    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete", "head"}:
                continue
            if (path, method) in summaries:
                operation["summary"] = summaries[path, method]
            public = (path in {"/auth/login", "/settings/login"} and method == "post") or (
                path in {"/auth/session", "/settings/session"} and method == "get")
            business = any(method.upper() == verb and re.fullmatch(pattern, path.replace("{stage}", "parsing"))
                           for verb, pattern in BUSINESS_ROUTES)
            operation["security"] = [] if public else ([{"AdminSession": []}, {"APIKey": []}] if business else [{"AdminSession": []}])
            note = ""
            if business:
                note = "**访问权限**：管理员会话、API Key 或 IP 白名单。白名单匹配来源 IP/CIDR，不匹配端口。"
            elif not public:
                note = "**访问权限**：仅管理员会话，API Key 与 IP 白名单不授予管理权限。"
            if method in {"post", "put", "patch", "delete"}:
                note += " 登录请求及使用 Cookie 的写请求必须携带 `X-Requested-With: XMLHttpRequest`。"
                operation.setdefault("parameters", []).append({
                    "name": "X-Requested-With", "in": "header", "required": not business,
                    "schema": {"type": "string", "default": "XMLHttpRequest"},
                    "description": "管理员登录/会话写请求必填；程序以 API Key 或 IP 白名单调用时可省略。",
                })
            operation["description"] = operation.get("description", "") + "\n\n" + note
    return set(summaries)
