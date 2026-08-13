"""受管理员短期会话保护的运行时设置 API。"""

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


router = APIRouter(prefix="/settings", tags=["系统设置"])
COOKIE_NAME = "settings_session"


class LoginRequest(BaseModel):
    password: str


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
    return request.client.host if request.client else "unknown"


def require_settings_session(
    settings_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    auth: SettingsAuthService = Depends(get_settings_auth),
) -> str:
    if not auth.validate(settings_session):
        raise HTTPException(status_code=401, detail="设置会话已过期，请重新登录")
    return settings_session or ""


@router.post("/login", response_model=ResponseWrapper)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth: SettingsAuthService = Depends(get_settings_auth),
):
    try:
        token = auth.authenticate(payload.password, _client_ip(request))
    except LoginRateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    cfg = get_config().settings
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max(1, cfg.session_minutes) * 60,
        httponly=True,
        secure=cfg.secure_cookie,
        samesite="strict",
        path="/settings",
    )
    return ResponseWrapper(message="登录成功", data={"authenticated": True})


@router.get("/session", response_model=ResponseWrapper)
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


@router.post("/logout", response_model=ResponseWrapper)
async def logout(
    response: Response,
    settings_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    auth: SettingsAuthService = Depends(get_settings_auth),
):
    auth.revoke(settings_session)
    response.delete_cookie(key=COOKIE_NAME, path="/settings", samesite="strict")
    return ResponseWrapper(message="已退出设置", data={"authenticated": False})
