"""真实 ASGI 请求验证权限边界与配置持久化，不依赖 MySQL/外部模型。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from argon2 import PasswordHasher
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.responses import StreamingResponse
from starlette.staticfiles import StaticFiles

from blue_print.access_control_router import get_access_service, router as access_router
from blue_print.settings_router import auth_router, get_settings_auth, get_settings_service, router as settings_router
from service.access_control_service import AccessControlService
from service.settings_auth_service import SettingsAuthService
from service.settings_service import ConfigWriteError
from utils import config as config_module
from utils.auth_middleware import AuthMiddleware, COOKIE_NAME, client_ip
from utils.config import load_config
from utils.model_config import ModelConfigRequest

PASSWORD = "仅用于自动化测试的密码"
SAFE_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"settings": {"username": "admin", "password": PASSWORD}}), encoding="utf-8")
    monkeypatch.setattr(config_module, "_current_config", load_config(path))
    auth = SettingsAuthService()
    service = AccessControlService(path)
    app = FastAPI()
    app.add_middleware(AuthMiddleware, auth=auth)
    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(access_router)
    app.dependency_overrides[get_settings_auth] = lambda: auth
    app.dependency_overrides[get_access_service] = lambda: service
    app.dependency_overrides[get_settings_service] = lambda: service

    @app.get("/analysis/rules")
    @app.post("/analysis/rules")
    @app.delete("/analysis/rules/test")
    @app.get("/file/list")
    @app.get("/file/abc/pdf")
    @app.get("/ui/")
    @app.get("/health")
    @app.get("/ui/login")
    @app.delete("/file/abc")
    @app.get("/logs/recent")
    @app.post("/doctype")
    @app.post("/new-admin-operation")
    async def probe(request: Request):
        return {"identity": request.state.auth_identity, "client": client_ip(request)}

    @app.post("/analysis/run")
    async def stream(request: Request):
        body = await request.body()
        async def events():
            yield b"data: " + body + b"\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")

    app.mount("/ui", StaticFiles(directory=Path(__file__).resolve().parents[1] / "ui", html=True))
    @app.post("/file/test/retry/parsing")
    async def model_config_probe(payload: ModelConfigRequest):
        return {"config": payload.model_overrides}

    return app, auth, service, path


@pytest.fixture
async def http(environment):
    async with AsyncClient(transport=ASGITransport(app=environment[0], client=("10.20.30.40", 43210)),
                           base_url="http://test") as client:
        yield client


async def login(http, **kwargs):
    return await http.post("/auth/login", json={"username": "admin", "password": PASSWORD, **kwargs}, headers=SAFE_HEADERS)


async def create_key(http, **kwargs):
    version = (await http.get("/settings/access")).json()["data"]["version"]
    return await http.post("/settings/access/keys", headers=SAFE_HEADERS,
                           json={"base_version": version, "name": "财务系统", **kwargs})


@pytest.mark.parametrize("path", ["/file/list", "/file/abc/pdf", "/docs", "/openapi.json", "/logs/recent", "/settings/access"])
async def test_anonymous_requests_are_denied(http, path):
    response = await http.get(path)
    assert response.status_code == 401


async def test_login_page_health_and_session_are_public(http):
    for path in ("/health", "/ui/login", "/auth/session"):
        assert (await http.get(path)).status_code == 200
    response = await http.get("/ui/")
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login"


async def test_real_app_login_route_redirects_and_assets(environment):
    """验证真实路由先于静态挂载生效，匿名访问及相对资源地址均可用。"""
    from html.parser import HTMLParser
    from urllib.parse import urljoin
    from app import app

    class Assets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag in {"link", "script", "img"}:
                self.urls.add(attrs.get("src") or attrs.get("href"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ui/login")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "登录工作台" in response.text
        assert "no-store" in response.headers["cache-control"]
        head = await client.head("/ui/login")
        assert head.status_code == 200 and not head.content
        for path in ("/", "/ui/", "/ui/login.html", "/ui/login/"):
            redirected = await client.get(path)
            assert redirected.status_code == 303
            assert redirected.headers["location"] == "/ui/login"
            assert (await client.get(path, follow_redirects=True)).url.path == "/ui/login"
        assets = Assets()
        assets.feed(response.text)
        assert assets.urls
        for asset in assets.urls:
            assert (await client.get(urljoin(str(response.url), asset))).status_code == 200


async def test_login_cookie_admin_access_and_logout(http, environment):
    response = await login(http)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "path=/;" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    token = http.cookies.get(COOKIE_NAME)
    assert (await http.get("/file/list")).json()["identity"] == "admin"
    assert (await http.post("/new-admin-operation", headers=SAFE_HEADERS)).status_code == 200
    assert (await http.post("/auth/logout", headers=SAFE_HEADERS)).status_code == 200
    assert not environment[1].validate(token)
    assert (await http.get("/file/list")).status_code == 401


async def test_login_rejects_wrong_username_and_rotates_session(http, environment):
    assert (await login(http, username="not-admin")).status_code == 401
    await login(http)
    old = http.cookies.get(COOKIE_NAME)
    await login(http)
    assert old != http.cookies.get(COOKIE_NAME)
    assert not environment[1].validate(old)


async def test_keys_are_one_time_only_and_grant_all_api_access(http, environment):
    await login(http)
    response = await create_key(http)
    assert response.status_code == 200
    payload = response.json()["data"]
    key = payload["key"]
    assert key.startswith("xj_") and len(key) >= 40
    assert "no-store" in response.headers["cache-control"]
    assert key not in environment[3].read_text(encoding="utf-8")
    listing = (await http.get("/settings/access")).text
    assert key not in listing and "key_hash" not in listing
    http.cookies.clear()
    headers = {"Authorization": f"Bearer {key}"}
    assert (await http.get("/file/list", headers=headers)).status_code == 200
    assert (await http.get("/file/abc/pdf", headers=headers)).status_code == 200
    for method, path in (("GET", "/settings/access"), ("GET", "/logs/recent"), ("DELETE", "/file/abc"),
                         ("POST", "/doctype"), ("POST", "/new-admin-operation")):
        assert (await http.request(method, path, headers=headers)).status_code == 200
    assert (await http.get("/ui/", headers=headers)).status_code == 200


@pytest.mark.parametrize("credential", ["key", "ip"])
async def test_all_api_permissions_include_rules_settings_and_key_management(http, environment, credential):
    await login(http)
    key = (await create_key(http)).json()["data"]["key"]
    headers = {"Authorization": f"Bearer {key}"} if credential == "key" else {}
    service = environment[2]
    if credential == "ip":
        service.mutate(version=service.read()["version"], kind="ip_allowlist", operation="create",
                       values={"name": "全接口调用", "network": "10.20.30.40"})
    http.cookies.clear()
    for method, path in (("GET", "/analysis/rules?type_id=yigongdaizhen"),
                         ("POST", "/analysis/rules"), ("DELETE", "/analysis/rules/test"),
                         ("DELETE", "/file/abc"), ("GET", "/logs/recent"),
                         ("POST", "/new-admin-operation"), ("GET", "/openapi.json")):
        assert (await http.request(method, path, headers=headers)).status_code == 200
    # 未登记的新路径直接进入路由匹配，不再受旧业务路由白名单限制。
    assert (await http.get("/future-api", headers=headers)).status_code == 404
    settings = (await http.get("/settings/config", headers=headers)).json()["data"]
    updated = await http.patch("/settings/config", headers=headers,
        json={"base_version": settings["version"], "changes": {"mineru": {"queue_width": 2}}})
    assert updated.status_code == 200
    assert updated.json()["data"]["config"]["mineru"]["queue_width"] == 2
    version = service.read()["version"]
    created = await http.post("/settings/access/keys", headers=headers,
        json={"base_version": version, "name": "程序创建的新密钥"})
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["key"].startswith("xj_")
    entry_id = data["api_keys"][-1]["id"]
    revoked = await http.patch(f"/settings/access/keys/{entry_id}", headers=headers,
        json={"base_version": data["version"], "enabled": False})
    assert revoked.status_code == 200
    removed = await http.request("DELETE", f"/settings/access/keys/{entry_id}", headers=headers,
        json={"base_version": revoked.json()["data"]["version"]})
    assert removed.status_code == 200
    # 开放所有接口不跳过浏览器来源校验。
    denied = await http.get("/analysis/rules", headers={**headers, "Origin": "https://evil.test"})
    assert denied.status_code == 403


async def test_revoked_program_credentials_no_longer_access_management(http, environment):
    await login(http)
    data = (await create_key(http)).json()["data"]
    key = data["key"]
    entry_id = data["api_keys"][0]["id"]
    http.cookies.clear()
    headers = {"Authorization": f"Bearer {key}"}
    assert (await http.get("/analysis/rules", headers=headers)).status_code == 200
    config_module.get_config().access_control.api_keys[0].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert (await http.get("/analysis/rules", headers=headers)).status_code == 401
    config_module.get_config().access_control.api_keys[0].expires_at = None
    result = await http.patch(f"/settings/access/keys/{entry_id}", headers=headers,
        json={"base_version": data["version"], "enabled": False})
    assert result.status_code == 200
    assert (await http.get("/settings/access", headers=headers)).status_code == 401
    service = environment[2]
    allowed = service.mutate(version=service.read()["version"], kind="ip_allowlist", operation="create",
                            values={"name": "测试来源", "network": "10.20.30.40"})
    assert (await http.get("/analysis/rules")).status_code == 200
    service.mutate(version=allowed["version"], kind="ip_allowlist", operation="delete",
                   entry_id=allowed["ip_allowlist"][0]["id"])
    assert (await http.get("/analysis/rules")).status_code == 401


async def test_rotation_disable_and_delete_take_effect_immediately(http, environment):
    await login(http)
    data = (await create_key(http)).json()["data"]
    original = data["key"]
    entry_id = data["api_keys"][0]["id"]
    rotated = await http.post(f"/settings/access/keys/{entry_id}/rotate", headers=SAFE_HEADERS,
                              json={"base_version": data["version"]})
    data = rotated.json()["data"]
    new_key = data["key"]
    from utils.auth_middleware import api_key_identity
    assert api_key_identity(f"Bearer {original}") is None
    assert api_key_identity(f"Bearer {new_key}") == entry_id
    disabled = await http.patch(f"/settings/access/keys/{entry_id}", headers=SAFE_HEADERS,
                                json={"base_version": data["version"], "enabled": False})
    assert disabled.status_code == 200
    assert api_key_identity(f"Bearer {new_key}") is None
    data = disabled.json()["data"]
    deleted = await http.request("DELETE", f"/settings/access/keys/{entry_id}", headers=SAFE_HEADERS,
                                 json={"base_version": data["version"]})
    assert deleted.status_code == 200
    assert deleted.json()["data"]["api_keys"] == []


@pytest.mark.parametrize("network", ["10.20.30.40", "10.20.30.0/24", "::ffff:10.20.30.40"])
async def test_ip_allowlist_all_ports_all_apis(http, environment, network):
    await login(http)
    version = environment[2].read()["version"]
    response = await http.post("/settings/access/allowlist", headers=SAFE_HEADERS,
                               json={"base_version": version, "name": "内网调用", "network": network})
    assert response.status_code == 200
    http.cookies.clear()
    assert (await http.get("/file/list")).status_code == 200
    assert (await http.get("/settings/access")).status_code == 200
    async with AsyncClient(transport=ASGITransport(app=environment[0], client=("10.20.30.40", 53100)), base_url="http://test") as other:
        assert (await other.get("/file/list")).status_code == 200


@pytest.mark.parametrize("network", ["10.20.30.40:8080", "not-an-ip", "10.20.30.4/24", "127.0.0.1:*", "[::1]:8080"])
async def test_invalid_networks_are_rejected(http, environment, network):
    await login(http)
    version = environment[2].read()["version"]
    result = await http.post("/settings/access/allowlist", headers=SAFE_HEADERS,
                             json={"base_version": version, "name": "invalid", "network": network})
    assert result.status_code == 422
    assert environment[2].read()["ip_allowlist"] == []


async def test_expiry_and_version_conflict(http, environment):
    await login(http)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert (await create_key(http, expires_at=expired)).status_code == 422
    old_version = environment[2].read()["version"]
    future = datetime.now(timezone.utc) + timedelta(days=1)
    data = (await create_key(http, expires_at=future.isoformat())).json()["data"]
    result = await http.post("/settings/access/keys", headers=SAFE_HEADERS,
                             json={"base_version": old_version, "name": "过期提交"})
    assert result.status_code == 409
    from utils.auth_middleware import api_key_identity
    cfg = config_module.get_config()
    cfg.access_control.api_keys[0].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert api_key_identity(f"Bearer {data['key']}") is None


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.test"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}])
async def test_cross_site_requests_rejected_even_with_cookie(http, headers):
    await login(http)
    assert (await http.get("/file/list", headers=headers)).status_code == 403
    assert (await http.post("/analysis/run", json={}, headers={**SAFE_HEADERS, **headers})).status_code == 403


async def test_cookie_mutation_and_login_require_custom_header(http):
    assert (await http.post("/auth/login", json={"username": "admin", "password": PASSWORD})).status_code == 403
    await login(http)
    assert (await http.post("/analysis/run", json={})).status_code == 403
    result = await http.post("/analysis/run", content=b'{"stream":true}', headers={**SAFE_HEADERS, "Origin": "http://test"})
    assert result.status_code == 200
    assert result.text == 'data: {"stream":true}\n\n'


async def test_reverse_proxy_origin_with_rewritten_host(http, environment):
    """模拟浏览器访问外部端口，而代理向 Docker 转发内网 Host。"""
    origin = "http://59.203.14.205:8121"
    headers = {**SAFE_HEADERS, "Origin": origin, "Host": "10.5.0.103:5019"}
    credentials = {"username": "admin", "password": PASSWORD}
    response = await http.post("/auth/login", json=credentials, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "不允许跨站访问"

    from utils.auth_config import AccessControlConfig
    config_module.get_config().access_control = AccessControlConfig(allowed_origins=[origin])
    assert (await http.get("/file/list", headers=headers)).status_code == 401
    response = await http.post("/auth/login", json=credentials, headers=headers)
    assert response.status_code == 200
    assert http.cookies.get(COOKIE_NAME)
    assert (await http.get("/settings/access", headers=headers)).status_code == 200
    assert (await http.post("/new-admin-operation", headers=headers)).status_code == 200
    assert (await http.post("/auth/logout", headers=headers)).status_code == 200
    assert (await http.get("/settings/access", headers=headers)).status_code == 401


async def test_external_origin_survives_access_settings_save(http, environment):
    """YAML 配置可加载，保存白名单不会丢掉外部入口或将其设为免密 IP。"""
    _, _, service, path = environment
    origin = "http://59.203.14.205:8121"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["access_control"] = {"allowed_origins": [origin]}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    config_module.replace_config(load_config(path))
    service.mutate(version=service.read()["version"], kind="ip_allowlist", operation="create",
                   values={"name": "测试业务来源", "network": "192.0.2.1"})
    assert config_module.get_config().access_control.allowed_origins == [origin]
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["access_control"]["allowed_origins"] == [origin]
    headers = {**SAFE_HEADERS, "Origin": origin, "Host": "10.5.0.103:5019"}
    assert (await http.get("/file/list", headers=headers)).status_code == 401
    assert (await http.post("/auth/login", json={"username": "admin", "password": PASSWORD}, headers=headers)).status_code == 200


async def test_external_origin_keeps_csrf_and_password_checks(http, environment):
    origin = "http://59.203.14.205:8121"
    config_module.get_config().access_control.allowed_origins = [origin]
    headers = {"Origin": origin, "Host": "10.5.0.103:5019"}
    credentials = {"username": "admin", "password": PASSWORD}
    assert (await http.post("/auth/login", json=credentials, headers=headers)).status_code == 403
    secure = {**headers, **SAFE_HEADERS}
    assert (await http.post("/auth/login", json={**credentials, "password": "wrong"}, headers=secure)).status_code == 401
    assert (await http.post("/auth/login", json=credentials, headers=secure)).status_code == 200
    assert (await http.post("/new-admin-operation", headers=headers)).status_code == 403
    assert (await http.post("/new-admin-operation", headers={**secure, "Sec-Fetch-Site": "cross-site"})).status_code == 403
    for untrusted in ("http://59.203.14.205:8122", "https://59.203.14.205:8121", "https://evil.test", "null"):
        response = await http.post("/new-admin-operation", headers={**secure, "Origin": untrusted})
        assert response.status_code == 403
    # 不能通过客户端自填的转发 Host 或协议绕过来源检查。
    spoofed = {**secure, "Origin": "https://evil.test", "X-Forwarded-Host": "evil.test", "X-Forwarded-Proto": "https"}
    assert (await http.post("/new-admin-operation", headers=spoofed)).status_code == 403


async def test_preserved_host_login_needs_no_external_origin_config(http):
    headers = {**SAFE_HEADERS, "Origin": "http://59.203.14.205:8121", "Host": "59.203.14.205:8121"}
    response = await http.post("/auth/login", json={"username": "admin", "password": PASSWORD}, headers=headers)
    assert response.status_code == 200


async def test_trusted_proxy_https_origin_still_supported(http, environment):
    headers = {**SAFE_HEADERS, "Origin": "https://docs.example.com", "Host": "docs.example.com",
               "X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.9"}
    credentials = {"username": "admin", "password": PASSWORD}
    assert (await http.post("/auth/login", json=credentials, headers=headers)).status_code == 403
    config_module.get_config().access_control.trusted_proxies = ["10.20.30.40/32"]
    assert (await http.post("/auth/login", json=credentials, headers=headers)).status_code == 200


@pytest.mark.parametrize("value,expected", [
    ("http://59.203.14.205:8121/", "http://59.203.14.205:8121"),
    ("HTTPS://Docs.Example.COM:443", "https://docs.example.com"),
    ("http://localhost:80", "http://localhost"),
    ("https://[::1]:8121", "https://[::1]:8121"),
])
def test_external_origin_config_normalizes_explicit_origins(value, expected):
    from utils.auth_config import AccessControlConfig
    config = AccessControlConfig(allowed_origins=[value, expected])
    assert config.allowed_origins == [expected]


@pytest.mark.parametrize("value", [
    "*", "http://*.example.com", "//example.com", "example.com", "null", "ftp://example.com",
    "http://name:secret@example.com", "http://example.com/ui/", "http://example.com?x=1",
    "http://example.com#", "http://example.com:", "http://example.com:0", "http://example.com:65536",
    "http://example.com,http://evil.test", "http://example.com\\evil.test", "http://example.com ",
])
def test_external_origin_config_rejects_ambiguous_sources(value):
    from utils.auth_config import AccessControlConfig
    with pytest.raises(ValueError):
        AccessControlConfig(allowed_origins=[value])


@pytest.mark.parametrize("origin,host,scheme,allowed", [
    ("http://59.203.14.205:8121", "59.203.14.205:8121", "http", True),
    ("http://59.203.14.205:8121", "10.5.0.103:5019", "http", False),
    ("http://59.203.14.205:8121", "59.203.14.205", "http", False),
    ("http://docs.example.com", "DOCS.example.com:80", "http", True),
    ("https://docs.example.com:443", "docs.example.com", "https", True),
    ("http://docs.example.com", "docs.example.com", "https", False),
])
def test_same_origin_compares_normalized_endpoint(environment, origin, host, scheme, allowed):
    from utils.auth_middleware import same_origin
    request = Request({"type": "http", "method": "POST", "path": "/auth/login",
                       "scheme": scheme, "query_string": b"", "client": ("192.0.2.9", 50000),
                       "headers": [(b"origin", origin.encode()), (b"host", host.encode())]})
    assert same_origin(request) is allowed


@pytest.mark.parametrize("origin,shown", [
    ("http://59.203.14.205:8121", "http://59.203.14.205:8121"),
    ("http://name:secret-in-origin@evil.test", "<无效来源>"),
])
async def test_rejected_login_logs_proxy_evidence_without_secrets(http, environment, monkeypatch, origin, shown):
    from utils import auth_middleware as middleware
    messages = []
    monkeypatch.setattr(middleware.logger, "warning", lambda message, *args: messages.append(message.format(*args)))
    headers = {**SAFE_HEADERS, "Origin": origin, "Host": "10.5.0.103:5019",
               "X-Forwarded-Proto": "http", "Sec-Fetch-Site": "same-origin",
               "Authorization": "Bearer secret-api-token", "Cookie": "xijuan_session=secret-cookie"}
    result = await http.post("/auth/login", json={"username": "admin", "password": PASSWORD}, headers=headers)
    assert result.status_code == 403
    assert result.json() == {"detail": "不允许跨站访问"}
    assert len(messages) == 1
    message = messages[0]
    for part in (f"origin={shown}", "host_origin=http://10.5.0.103:5019", "transport_scheme=http",
                 "forwarded_proto=http", "fetch_site=same-origin", "peer=10.20.30.40", "trusted_proxy=False"):
        assert part in message
    for secret in (PASSWORD, "secret-api-token", "secret-cookie", "secret-in-origin"):
        assert secret not in message


async def test_spoofed_forwarded_header_cannot_bypass_allowlist(http, environment):
    service = environment[2]
    service.mutate(version=service.read()["version"], kind="ip_allowlist", operation="create",
                   values={"name": "trusted", "network": "192.0.2.8"})
    assert (await http.get("/file/list", headers={"X-Forwarded-For": "192.0.2.8"})).status_code == 401
    config_module.get_config().access_control.trusted_proxies = ["10.20.30.40/32"]
    assert (await http.get("/file/list", headers={"X-Forwarded-For": "192.0.2.8"})).status_code == 200
    assert (await http.get("/file/list", headers={"X-Forwarded-For": "192.0.2.8, 203.0.113.9"})).status_code == 401
    assert (await http.get("/file/list", headers={"X-Forwarded-For": "malformed"})).status_code == 401


@pytest.mark.parametrize("path", ["/ui//index.html", "/ui/vendor/%2e%2e/index.html", "/ui/js/%2e%2e%5cindex.html",
                                  "/ui/js/%23/%2e%2e/%2e%2e/index.html", "/ui/js/%3F/%2e%2e/%2e%2e/index.html"])
async def test_static_paths_do_not_bypass_admin(http, path):
    assert (await http.get(path)).status_code == 303


async def test_external_link_can_reach_login_but_not_api(http):
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    for path in ("/ui/", "/ui/login.html"):
        response = await http.get(path, headers=headers)
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"
    assert (await http.get("/ui/login", headers=headers)).status_code == 200
    assert (await http.get("/file/list", headers=headers)).status_code == 403


async def test_outer_uvicorn_proxy_headers_cannot_spoof_allowlist(environment):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    service = environment[2]
    service.mutate(version=service.read()["version"], kind="ip_allowlist", operation="create",
                   values={"name": "受信来源", "network": "192.0.2.8"})
    outer = ProxyHeadersMiddleware(environment[0], trusted_hosts="*")
    async with AsyncClient(transport=ASGITransport(app=outer, client=("127.0.0.1", 50000)), base_url="http://test") as client:
        response = await client.get("/file/list", headers={"X-Forwarded-For": "192.0.2.8"})
        assert response.status_code == 503


@pytest.mark.parametrize("forwarded", [None, "", "x" * 2050, "not-an-ip"])
async def test_trusted_proxy_never_falls_back_to_allowlisted_peer(http, environment, forwarded):
    service = environment[2]
    service.mutate(version=service.read()["version"], kind="ip_allowlist", operation="create",
                   values={"name": "内网来源", "network": "10.0.0.0/8"})
    config_module.get_config().access_control.trusted_proxies = ["10.20.30.40/32"]
    headers = {} if forwarded is None else {"X-Forwarded-For": forwarded}
    assert (await http.get("/file/list", headers=headers)).status_code == 401


def test_password_migration_and_credential_change_invalidate_session(environment):
    _, auth, service, path = environment
    service.migrate_admin_password()
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "password" not in document["settings"]
    assert PasswordHasher().verify(document["settings"]["password_hash"], PASSWORD)
    token = auth.authenticate(PASSWORD, "127.0.0.1", "admin")
    assert auth.validate(token)
    config_module.get_config().settings.password_hash = PasswordHasher().hash("new-password")
    assert not auth.validate(token)


def test_failed_write_does_not_change_runtime_or_file(environment, monkeypatch):
    service, path = environment[2:]
    old = path.read_bytes()
    def fail(document):
        raise ConfigWriteError("测试写入失败")
    monkeypatch.setattr(service, "_write_atomic", fail)
    with pytest.raises(ConfigWriteError):
        service.mutate(version=service.read()["version"], kind="api_keys", operation="create", values={"name": "test"})
    assert not config_module.get_config().access_control.api_keys
    assert path.read_bytes() == old


@pytest.mark.parametrize("group,field", [("extraction", "base_url"), ("extraction", "llm_base_url"),
    ("analysis", "base_url"), ("embedding", "base_url"), ("vl_model", "base_url"),
    ("table_name_validation", "base_url"), ("table_name_validation", "llm_api_key"), ("analysis", "api_key")])
async def test_program_can_override_model_connections(http, environment, group, field):
    await login(http)
    key = (await create_key(http)).json()["data"]["key"]
    http.cookies.clear()
    response = await http.post("/file/test/retry/parsing", headers={"Authorization": f"Bearer {key}"},
                               json={"model_config": {group: {field: "https://caller.invalid/v1"}}})
    assert response.status_code == 200
    canonical = {"llm_base_url": "base_url", "llm_api_key": "api_key"}.get(field, field)
    assert response.json()["config"][group][canonical] == "https://caller.invalid/v1"


async def test_safe_model_overrides_and_admin_connection_override(http):
    await login(http)
    response = await http.post("/file/test/retry/parsing", headers=SAFE_HEADERS,
        json={"model_config": {"analysis": {"base_url": "https://admin.invalid/v1"}}})
    assert response.status_code == 200
    key = (await create_key(http)).json()["data"]["key"]
    http.cookies.clear()
    response = await http.post("/file/test/retry/parsing", headers={"Authorization": f"Bearer {key}"},
        json={"model_config": {"analysis": {"model": "my-model", "temperature": 0.2}}})
    assert response.status_code == 200


async def test_infinite_log_stream_ends_after_logout(environment, monkeypatch):
    import importlib
    log_router = importlib.import_module("blue_print.log_router")
    from service import settings_auth_service
    auth = environment[1]
    monkeypatch.setattr(settings_auth_service, "settings_auth", auth)
    monkeypatch.setattr(log_router, "_resolve_log_file", lambda _: None)
    token = auth.authenticate(PASSWORD, "127.0.0.1", "admin")
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    request = Request({"type": "http", "headers": [(b"cookie", f"{COOKIE_NAME}={token}".encode())],
                       "state": {"auth_identity": "admin"}}, receive)
    response = await log_router.stream_logs(request, tail=0)
    assert 'event: ready' in await anext(response.body_iterator)
    auth.revoke(token)
    assert 'event: auth_required' in await anext(response.body_iterator)
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)


@pytest.mark.parametrize("credential", ["key", "ip"])
async def test_log_stream_ends_after_program_credential_revocation(environment, monkeypatch, credential):
    import importlib
    log_router = importlib.import_module("blue_print.log_router")
    monkeypatch.setattr(log_router, "_resolve_log_file", lambda _: None)
    service = environment[2]
    if credential == "key":
        data = service.mutate(version=service.read()["version"], kind="api_keys", operation="create",
                              values={"name": "日志程序"})
        identity = f"key:{data['api_keys'][0]['id']}"
        headers = [(b"authorization", f"Bearer {data['key']}".encode())]
    else:
        service.mutate(version=service.read()["version"], kind="ip_allowlist", operation="create",
                       values={"name": "日志来源", "network": "10.20.30.40"})
        identity = "ip:10.20.30.40"
        headers = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    request = Request({"type": "http", "headers": headers, "client": ("10.20.30.40", 43000),
                       "state": {"auth_identity": identity}}, receive)
    response = await log_router.stream_logs(request, tail=0)
    assert 'event: ready' in await anext(response.body_iterator)
    config = config_module.get_config().access_control
    if credential == "key":
        config.api_keys[0].enabled = False
    else:
        config.ip_allowlist.clear()
    assert 'event: auth_required' in await anext(response.body_iterator)
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)


def test_openapi_exposes_all_protected_operations_to_programs():
    from scripts.gen_openapi import build_app
    from utils.openapi_enrich import enrich
    spec = enrich(build_app().openapi())
    public = {("/auth/login", "post"), ("/settings/login", "post"),
              ("/auth/session", "get"), ("/settings/session", "get")}
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete", "head"}:
                continue
            expected = [] if (path, method) in public else [{"AdminSession": []}, {"APIKey": []}]
            assert operation["security"] == expected, (method, path)
    rules = spec["paths"]["/analysis/rules"]["get"]
    assert "IP 白名单" in rules["description"]
    patch = spec["paths"]["/settings/config"]["patch"]
    header = next(item for item in patch["parameters"] if item["name"] == "X-Requested-With")
    assert header["required"] is False


def test_native_yaml_timestamps_are_supported(environment):
    service, path = environment[2:]
    service.mutate(version=service.read()["version"], kind="api_keys", operation="create", values={"name": "test"})
    text = path.read_text(encoding="utf-8")
    import re
    text = re.sub(r"created_at: ['\"]([^'\"]+)['\"]", r"created_at: \1", text)
    path.write_text(text, encoding="utf-8")
    assert service.read()["api_keys"][0]["created_at"]
