"""统一鉴权：后台只允许会话，业务路由允许会话、API Key 或 IP 白名单。"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
from datetime import datetime, timezone

from loguru import logger
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from service.settings_auth_service import settings_auth
from utils.config import get_config
from utils.auth_context import request_identity
from utils.auth_config import normalize_origin

COOKIE_NAME = "xijuan_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# 显式匹配方法和路径；新增接口默认只允许管理员，避免扩大程序权限。
BUSINESS_ROUTES = (
    ("POST", r"/file/parse"),
    ("POST", r"/file/context_query"),
    ("GET", r"/file/(list|processing|stats)"),
    ("GET", r"/file/[^/]+/(status|pdf|tables|chunks|outline|content|extraction|analysis|detail)"),
    ("POST", r"/file/[^/]+/retry/(parsing|tableing|chunking|embedding|extracting|analyzing)"),
    ("POST", r"/analysis/run"),
    ("GET", r"/analysis/tasks/[^/]+"),
    ("POST", r"/search"),
    ("GET", r"/doctype/list"),
)


def _address(value: str):
    try:
        address = ipaddress.ip_address(value)
        return getattr(address, "ipv4_mapped", None) or address
    except ValueError:
        return None


def in_networks(address: str, networks) -> bool:
    parsed = _address(address)
    return parsed is not None and any(parsed in ipaddress.ip_network(net) for net in networks)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    trusted = get_config().access_control.trusted_proxies
    if not in_networks(peer, trusted):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded or len(forwarded) > 2048:
        # 可信代理缺失/截断来源信息时不能回退代理自身 IP，否则可能命中内网白名单。
        return "unknown"
    chain = [part.strip() for part in forwarded.split(",")]
    if any(_address(part) is None for part in chain):
        return "unknown"
    # 从直连代理向外剥离，只接受第一个不可信节点；不接受客户端伪造的最左端。
    for candidate in reversed(chain):
        peer = candidate
        if not in_networks(peer, trusted):
            break
    return peer


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        return False
    if origin is None:
        return True
    try:
        origin = normalize_origin(origin)
    except ValueError:
        return False
    # 外部入口只通过来源检查；后续登录、CSRF 请求头和接口权限仍照常验证。
    if origin in get_config().access_control.allowed_origins:
        return True
    scheme = request.url.scheme
    peer = request.client.host if request.client else "unknown"
    if in_networks(peer, get_config().access_control.trusted_proxies):
        forwarded = request.headers.get("x-forwarded-proto")
        if forwarded in {"http", "https"}:
            scheme = forwarded
    try:
        expected_origin = normalize_origin(f"{scheme}://{request.headers.get('host', '')}")
    except ValueError:
        return False
    return origin == expected_origin


def _log_login_origin_rejection(request: Request) -> None:
    """只记录排查代理所需的来源信息，不记录凭证、请求体或完整请求头。"""
    def endpoint(value: str | None) -> str:
        if value is None:
            return "<缺失>"
        try:
            return normalize_origin(value)[:256]
        except ValueError:
            return "<无效来源>"

    peer = request.client.host if request.client else "unknown"
    forwarded_proto = request.headers.get("x-forwarded-proto")
    fetch_site = request.headers.get("sec-fetch-site")
    logger.warning(
        "登录来源校验拒绝 origin={} host_origin={} transport_scheme={} "
        "forwarded_proto={} fetch_site={} peer={} trusted_proxy={}",
        endpoint(request.headers.get("origin")),
        endpoint(f"{request.url.scheme}://{request.headers.get('host', '')}"),
        request.url.scheme,
        forwarded_proto if forwarded_proto in {None, "http", "https"} else "<无效协议>",
        fetch_site if fetch_site in {None, "same-origin", "same-site", "cross-site", "none"} else "<无效标记>",
        str(_address(peer) or "unknown"),
        in_networks(peer, get_config().access_control.trusted_proxies),
    )


def api_key_identity(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith("xj_") or len(token) > 256:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    for entry in get_config().access_control.api_keys:
        if (entry.enabled and (entry.expires_at is None or entry.expires_at > now)
                and secrets.compare_digest(entry.key_hash, digest)):
            return entry.id
    return None


class AuthMiddleware:
    """使用纯 ASGI 中间件，不读取请求体、不缓冲上传或 SSE。"""

    def __init__(self, app, auth=None):
        self.app = app
        self.auth = auth or settings_auth

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        # ASGI path 已经解码；不能再经 URL 解析，否则 %23/%3F 会截断授权路径。
        path = scope["path"]
        canonical = path.rstrip("/") or "/"
        method = request.method

        async def respond(response):
            response.headers["Cache-Control"] = "no-store"
            await response(scope, receive, send)

        # Uvicorn 的外层 ProxyHeadersMiddleware 会把原始对端覆写为 (转发 IP, 0)。
        # 原始地址丢失后无法判断可信代理，必须拒绝，不能再拿覆写后的 IP 匹配白名单。
        if request.client and request.client.port == 0 and request.headers.get("x-forwarded-for"):
            await respond(JSONResponse({"detail": "代理配置冲突，请使用 --no-proxy-headers 启动服务"}, status_code=503))
            return

        origin_allowed = same_origin(request)
        cross_site_entry = (method == "GET" and canonical in {"/", "/ui", "/ui/index.html", "/ui/login", "/ui/login.html"}
                            and request.headers.get("sec-fetch-mode") == "navigate"
                            and request.headers.get("sec-fetch-dest") == "document")
        if not origin_allowed and cross_site_entry and canonical != "/ui/login":
            await respond(RedirectResponse("/ui/login", status_code=303))
            return
        if not origin_allowed and not cross_site_entry:
            if method == "POST" and canonical in {"/auth/login", "/settings/login"}:
                _log_login_origin_rejection(request)
            await respond(JSONResponse({"detail": "不允许跨站访问"}, status_code=403))
            return

        is_login = canonical in {"/auth/login", "/settings/login"} and method == "POST"
        public = ((canonical in {"/health", "/auth/session", "/settings/session", "/ui/login", "/ui/login.html", "/favicon.ico", "/ui/favicon.svg"}
                   and method in {"GET", "HEAD"})
                  or (any(path.startswith(prefix) for prefix in ("/ui/css/", "/ui/js/", "/ui/vendor/"))
                      and method in {"GET", "HEAD"} and ".." not in path and "\\" not in path))
        if is_login:
            if request.headers.get("x-requested-with") != "XMLHttpRequest":
                await respond(JSONResponse({"detail": "登录请求缺少安全请求头"}, status_code=403))
                return
            public = True

        authenticated = self.auth.validate(request.cookies.get(COOKIE_NAME))
        identity = None
        if not public:
            if authenticated:
                identity = "admin"
                if method not in SAFE_METHODS and request.headers.get("x-requested-with") != "XMLHttpRequest":
                    await respond(JSONResponse({"detail": "请求缺少安全请求头，请刷新页面"}, status_code=403))
                    return
            else:
                business = any(method == verb and re.fullmatch(pattern, canonical)
                               for verb, pattern in BUSINESS_ROUTES)
                key_id = api_key_identity(request.headers.get("authorization"))
                allowed_ip = in_networks(client_ip(request), (
                    entry.network for entry in get_config().access_control.ip_allowlist if entry.enabled
                ))
                if business and (key_id or allowed_ip):
                    identity = f"key:{key_id}" if key_id else f"ip:{client_ip(request)}"
                else:
                    if path == "/" or path.startswith("/ui"):
                        await respond(RedirectResponse("/ui/login", status_code=303))
                    else:
                        status = 403 if key_id or allowed_ip else 401
                        await respond(JSONResponse(
                            {"detail": "此操作需要管理员登录" if status == 403 else "请登录或提供有效 API Key"},
                            status_code=status, headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
                        ))
                    return
        scope.setdefault("state", {})["auth_identity"] = identity

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                                (b"x-frame-options", b"DENY"), (b"referrer-policy", b"same-origin")])
                message = {**message, "headers": headers}
            await send(message)

        token = request_identity.set(identity)
        try:
            await self.app(scope, receive, secured_send)
        finally:
            request_identity.reset(token)
