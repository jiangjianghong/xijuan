"""接口访问控制配置：来源 IP、程序密钥、可信代理及外部访问入口。"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

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


def normalize_origin(value: str) -> str:
    """规范外部访问来源，只接受明确的 HTTP(S) 协议、主机和端口。"""
    if not value or any(char.isspace() or char in "\\,*@?#" for char in value):
        raise ValueError("访问来源必须是完整的 http(s)://主机[:端口]，不支持通配符、路径或认证信息")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.path not in {"", "/"} or parsed.netloc.endswith(":")):
            raise ValueError("无效来源")
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        else:
            host = host.encode("idna").decode("ascii").lower()
            if not all(char.isalnum() or char in ".-" for char in host):
                raise ValueError("无效主机")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("无效端口")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("访问来源必须是完整的 http(s)://主机[:端口]") from exc
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


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
    # 反向代理改写 Host 时的外部访问入口；不授予登录或业务访问权限。
    allowed_origins: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_origin(value) for value in values))

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
