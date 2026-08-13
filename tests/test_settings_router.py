from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from blue_print.settings_router import (
    get_settings_auth,
    get_settings_service,
    router,
)
from service.settings_auth_service import SettingsAuthService
from service.settings_service import SettingsService


ROUTER_CONFIG = """\
mineru:
  base_url: http://mineru.test
  backend: vllm-async-engine
  queue_width: 1
  parse_timeout: 1200
  max_file_size: 104857600
embedding:
  base_url: http://embedding.test/v1
  model_name: embedding-old
  api_key: router-secret-key
  embedding_dim: 1024
  batch_size: 8
  timeout: 60
  retry_count: 3
mysql:
  password: hidden-mysql-password
settings:
  password: admin-password
  session_minutes: 30
  secure_cookie: false
"""


@pytest.fixture
def router_dependencies(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(ROUTER_CONFIG, encoding="utf-8")
    settings_service = SettingsService(config_path)
    auth_service = SettingsAuthService(
        password_provider=lambda: "admin-password",
        session_minutes_provider=lambda: 30,
    )
    return settings_service, auth_service


@pytest.fixture
async def settings_client(router_dependencies):
    settings_service, auth_service = router_dependencies
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings_service] = lambda: settings_service
    app.dependency_overrides[get_settings_auth] = lambda: auth_service
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def login(client: AsyncClient, password: str = "admin-password"):
    return await client.post("/settings/login", json={"password": password})


async def test_login_sets_protected_session_cookie(settings_client: AsyncClient):
    response = await login(settings_client)

    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "settings_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/settings" in cookie
    assert "max-age=1800" in cookie


async def test_config_requires_valid_session(settings_client: AsyncClient):
    response = await settings_client.get("/settings/config")

    assert response.status_code == 401


async def test_session_endpoint_reports_login_state(settings_client: AsyncClient):
    before = await settings_client.get("/settings/session")
    await login(settings_client)
    after = await settings_client.get("/settings/session")

    assert before.status_code == 200
    assert before.json()["data"] == {"authenticated": False}
    assert after.json()["data"] == {"authenticated": True}


async def test_config_response_never_contains_secrets(settings_client: AsyncClient):
    await login(settings_client)
    response = await settings_client.get("/settings/config")

    assert response.status_code == 200
    body = response.text
    assert "router-secret-key" not in body
    assert "hidden-mysql-password" not in body
    assert "admin-password" not in body
    assert response.json()["data"]["config"]["embedding"]["api_key"] == {
        "configured": True
    }


async def test_patch_updates_config_and_rejects_stale_version(
    settings_client: AsyncClient, router_dependencies
):
    config_path = router_dependencies[0].config_path
    await login(settings_client)
    current = (await settings_client.get("/settings/config")).json()["data"]
    payload = {
        "base_version": current["version"],
        "changes": {"mineru": {"queue_width": 2}},
        "secrets": {},
    }

    updated = await settings_client.patch("/settings/config", json=payload)
    stale = await settings_client.patch("/settings/config", json=payload)

    assert updated.status_code == 200
    assert updated.json()["data"]["config"]["mineru"]["queue_width"] == 2
    assert stale.status_code == 409
    assert "queue_width: 2" in config_path.read_text(encoding="utf-8")


async def test_patch_rejects_forbidden_and_readonly_fields(settings_client: AsyncClient):
    await login(settings_client)
    version = (await settings_client.get("/settings/config")).json()["data"]["version"]

    forbidden = await settings_client.patch(
        "/settings/config",
        json={
            "base_version": version,
            "changes": {"mysql": {"host": "attacker"}},
            "secrets": {},
        },
    )
    readonly = await settings_client.patch(
        "/settings/config",
        json={
            "base_version": version,
            "changes": {"embedding": {"embedding_dim": 4096}},
            "secrets": {},
        },
    )

    assert forbidden.status_code == 422
    assert readonly.status_code == 422


async def test_fifth_failed_login_is_rate_limited(settings_client: AsyncClient):
    responses = [await login(settings_client, "wrong") for _ in range(5)]

    assert [response.status_code for response in responses[:4]] == [401] * 4
    assert responses[4].status_code == 429


async def test_logout_revokes_session_and_clears_cookie(settings_client: AsyncClient):
    await login(settings_client)

    logout = await settings_client.post("/settings/logout")
    config = await settings_client.get("/settings/config")

    assert logout.status_code == 200
    assert "settings_session=" in logout.headers["set-cookie"].lower()
    assert config.status_code == 401


async def test_invalid_config_response_does_not_echo_bad_input(
    settings_client: AsyncClient, router_dependencies
):
    marker = "private-invalid-input"
    path = router_dependencies[0].config_path
    path.write_text(
        path.read_text(encoding="utf-8").replace("queue_width: 1", f"queue_width: {marker}"),
        encoding="utf-8",
    )
    await login(settings_client)

    response = await settings_client.get("/settings/config")

    assert response.status_code == 500
    assert marker not in response.text
    assert "mineru.queue_width" in response.text
