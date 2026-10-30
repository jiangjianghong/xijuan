"""接口访问控制管理；仅管理员会话可访问，完整密钥只在创建/重新生成时返回。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from blue_print.settings_router import require_settings_session
from model.schemas import ResponseWrapper
from service.access_control_service import AccessControlService
from service.settings_service import ConfigConflictError, ConfigFieldError, ConfigWriteError
from utils.auth_config import normalize_network

router = APIRouter(prefix="/settings/access", tags=["接口访问控制"],
                   dependencies=[Depends(require_settings_session)])


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version: str = Field(min_length=1, max_length=128)


class KeyCreate(VersionRequest):
    name: str = Field(min_length=1, max_length=80)
    expires_at: AwareDatetime | None = None


class AllowCreate(VersionRequest):
    name: str = Field(min_length=1, max_length=80)
    network: str = Field(max_length=80)

    _normalize = field_validator("network")(normalize_network)


class EnableRequest(VersionRequest):
    enabled: bool


def get_access_service() -> AccessControlService:
    return AccessControlService()


def _call(service: AccessControlService, **kwargs):
    try:
        return service.mutate(**kwargs) if kwargs else service.read()
    except ConfigConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ConfigFieldError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ConfigWriteError as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("", response_model=ResponseWrapper)
def read_access(response: Response, service: AccessControlService = Depends(get_access_service)):
    response.headers["Cache-Control"] = "no-store"
    return ResponseWrapper(data=_call(service))


@router.post("/keys", response_model=ResponseWrapper)
def create_key(payload: KeyCreate, response: Response,
               service: AccessControlService = Depends(get_access_service)):
    response.headers["Cache-Control"] = "no-store"
    return ResponseWrapper(data=_call(service, version=payload.base_version, kind="api_keys",
        operation="create", values=payload.model_dump(mode="json", exclude={"base_version"})))


@router.post("/allowlist", response_model=ResponseWrapper)
def create_allow(payload: AllowCreate, service: AccessControlService = Depends(get_access_service)):
    return ResponseWrapper(data=_call(service, version=payload.base_version, kind="ip_allowlist",
        operation="create", values=payload.model_dump(exclude={"base_version"})))


@router.patch("/{kind}/{entry_id}", response_model=ResponseWrapper)
def enable_entry(kind: Literal["keys", "allowlist"], entry_id: str, payload: EnableRequest,
                 service: AccessControlService = Depends(get_access_service)):
    return ResponseWrapper(data=_call(service, version=payload.base_version,
        kind="api_keys" if kind == "keys" else "ip_allowlist", operation="update",
        entry_id=entry_id, values={"enabled": payload.enabled}))


@router.delete("/{kind}/{entry_id}", response_model=ResponseWrapper)
def delete_entry(kind: Literal["keys", "allowlist"], entry_id: str, payload: VersionRequest,
                 service: AccessControlService = Depends(get_access_service)):
    return ResponseWrapper(data=_call(service, version=payload.base_version,
        kind="api_keys" if kind == "keys" else "ip_allowlist", operation="delete", entry_id=entry_id))


@router.post("/keys/{entry_id}/rotate", response_model=ResponseWrapper)
def rotate_key(entry_id: str, payload: VersionRequest, response: Response,
               service: AccessControlService = Depends(get_access_service)):
    response.headers["Cache-Control"] = "no-store"
    return ResponseWrapper(data=_call(service, version=payload.base_version,
        kind="api_keys", operation="rotate", entry_id=entry_id))
