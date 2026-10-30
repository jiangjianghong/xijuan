"""接口访问控制的配置模型；不接受端口或任意扩展字段。"""

from __future__ import annotations

import ipaddress
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_network(value: str) -> str:
    """统一单地址、CIDR 和 IPv4 映射地址，拒绝含端口的写法。"""
    try:
        network = ipaddress.ip_network(value.strip(), strict=True)
    except ValueError as exc:
        raise ValueError("请输入有效 IP 或规范 CIDR 网段，不支持端口") from exc
    if isinstance(network, ipaddress.IPv6Network) and network.prefixlen >= 96:
        mapped = network.network_address.ipv4_mapped
        if mapped is not None:
            network = ipaddress.ip_network(f"{mapped}/{network.prefixlen - 96}")
    return str(network)


class AccessEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-f0-9]{16}$")
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class IPAllowEntry(AccessEntry):
    network: str

    _normalize = field_validator("network")(normalize_network)


class APIKeyEntry(AccessEntry):
    key_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    hint: str = Field(pattern=r"^xj_…[A-Za-z0-9_-]{4}$")
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None


class AccessControlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_keys: list[APIKeyEntry] = Field(default_factory=list, max_length=200)
    ip_allowlist: list[IPAllowEntry] = Field(default_factory=list, max_length=200)
    # 仅在 YAML 中配置；不得把公共网段或所有地址设为可信代理。
    trusted_proxies: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("trusted_proxies")
    @classmethod
    def validate_proxies(cls, values: list[str]) -> list[str]:
        return [normalize_network(value) for value in values]

    @model_validator(mode="after")
    def unique_ids(self) -> "AccessControlConfig":
        for entries in (self.api_keys, self.ip_allowlist):
            if len({entry.id for entry in entries}) != len(entries):
                raise ValueError("访问控制条目的 ID 不得重复")
        return self
