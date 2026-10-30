"""受统一鉴权保护的运行时设置 API。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from model.schemas import ResponseWrapper
from service.settings_auth_service import (
    AuthenticationError,
    LoginRateLimitedError,
    SettingsAuthService,
    settings_auth,
)
from service.settings_service import (
    ConfigConflictError,
    ConfigFieldError,
    ConfigWriteError,
    SettingsService,
)
from utils.config import get_config
from utils.auth_middleware import COOKIE_NAME, client_ip


router = APIRouter(prefix="/settings", tags=["系统设置"])
auth_router = APIRouter(prefix="/auth", tags=["管理员登录"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


class SecretOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["keep", "replace", "clear"]
    value: str | None = None


class SettingsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: str = Field(..., min_length=1)
    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    secrets: dict[str, SecretOperation] = Field(default_factory=dict)


def get_settings_service() -> SettingsService:
    return SettingsService()


def get_settings_auth() -> SettingsAuthService:
    return settings_auth


def _client_ip(request: Request) -> str:
    return client_ip(request)


def require_settings_session(
    request: Request,
    settings_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    auth: SettingsAuthService = Depends(get_settings_auth),
) -> str:
    # 身份只取自统一中间件，不接受调用方请求头自行声明身份。
    identity = getattr(request.state, "auth_identity", None)
    if isinstance(identity, str) and identity.startswith(("key:", "ip:")):
        return identity
    if not auth.validate(settings_session):
        raise HTTPException(status_code=401, detail="设置会话已过期，请重新登录")
    return settings_session or ""


@auth_router.post("/login", response_model=ResponseWrapper)
@router.post("/login", response_model=ResponseWrapper, deprecated=True)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth: SettingsAuthService = Depends(get_settings_auth),
):
    try:
        token = auth.authenticate(payload.password, _client_ip(request), payload.username)
    except LoginRateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    cfg = get_config().settings
    auth.revoke(request.cookies.get(COOKIE_NAME))
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max(1, cfg.session_minutes) * 60,
        httponly=True,
        secure=cfg.secure_cookie,
        samesite="strict",
        path="/",
    )
    return ResponseWrapper(message="登录成功", data={"authenticated": True})


@auth_router.get("/session", response_model=ResponseWrapper)
@router.get("/session", response_model=ResponseWrapper, deprecated=True)
async def session_status(
    settings_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    auth: SettingsAuthService = Depends(get_settings_auth),
):
    return ResponseWrapper(data={"authenticated": auth.validate(settings_session)})


@router.get("/config", response_model=ResponseWrapper)
async def read_config(
    _: str = Depends(require_settings_session),
    service: SettingsService = Depends(get_settings_service),
):
    try:
        return ResponseWrapper(data=service.read_public_config())
    except (ConfigFieldError, ConfigWriteError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/config", response_model=ResponseWrapper)
async def update_config(
    payload: SettingsPatchRequest,
    _: str = Depends(require_settings_session),
    service: SettingsService = Depends(get_settings_service),
):
    secret_payload = {
        path: operation.model_dump(exclude_none=True)
        for path, operation in payload.secrets.items()
    }
    try:
        data = service.update_config(
            base_version=payload.base_version,
            changes=payload.changes,
            secrets=secret_payload,
        )
    except ConfigConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigFieldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConfigWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ResponseWrapper(message="配置已保存并即时生效", data=data)


@auth_router.post("/logout", response_model=ResponseWrapper)
@router.post("/logout", response_model=ResponseWrapper, deprecated=True)
async def logout(
    response: Response,
    settings_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    auth: SettingsAuthService = Depends(get_settings_auth),
):
    auth.revoke(settings_session)
    response.delete_cookie(key=COOKIE_NAME, path="/", samesite="strict")
    response.delete_cookie(key="settings_session", path="/settings", samesite="strict")
    return ResponseWrapper(message="已退出登录", data={"authenticated": False})
