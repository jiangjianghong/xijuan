"""请求身份上下文，记录会话、API Key 或来源 IP 的认证身份。"""

from contextvars import ContextVar

request_identity: ContextVar[str | None] = ContextVar("request_auth_identity", default=None)
