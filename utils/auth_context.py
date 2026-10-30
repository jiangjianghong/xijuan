"""请求身份上下文，用于服务内部限制低权限调用者的连接配置覆盖。"""

from contextvars import ContextVar

request_identity: ContextVar[str | None] = ContextVar("request_auth_identity", default=None)
