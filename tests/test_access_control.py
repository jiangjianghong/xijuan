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
from blue_print.settings_router import auth_router, get_settings_auth, router as settings_router
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


async def test_keys_are_one_time_only_and_do_not_grant_admin(http, environment):
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
        assert (await http.request(method, path, headers=headers)).status_code == 403
    assert (await http.get("/ui/", headers=headers)).status_code == 303


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
async def test_ip_allowlist_all_ports_business_only(http, environment, network):
    await login(http)
    version = environment[2].read()["version"]
    response = await http.post("/settings/access/allowlist", headers=SAFE_HEADERS,
                               json={"base_version": version, "name": "内网调用", "network": network})
    assert response.status_code == 200
    http.cookies.clear()
    assert (await http.get("/file/list")).status_code == 200
    assert (await http.get("/settings/access")).status_code == 403
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
async def test_program_cannot_redirect_server_credentials(http, environment, group, field):
    await login(http)
    key = (await create_key(http)).json()["data"]["key"]
    http.cookies.clear()
    response = await http.post("/file/test/retry/parsing", headers={"Authorization": f"Bearer {key}"},
                               json={"model_config": {group: {field: "https://caller.invalid/v1"}}})
    assert response.status_code == 422
    assert "仅管理员" in response.text


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


def test_native_yaml_timestamps_are_supported(environment):
    service, path = environment[2:]
    service.mutate(version=service.read()["version"], kind="api_keys", operation="create", values={"name": "test"})
    text = path.read_text(encoding="utf-8")
    import re
    text = re.sub(r"created_at: ['\"]([^'\"]+)['\"]", r"created_at: \1", text)
    path.write_text(text, encoding="utf-8")
    assert service.read()["api_keys"][0]["created_at"]
