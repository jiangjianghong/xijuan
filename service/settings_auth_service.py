"""系统设置管理员的短期内存会话与登录限速。"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

from utils.config import get_config


class AuthenticationError(RuntimeError):
    """设置密码校验失败。"""


class LoginRateLimitedError(AuthenticationError):
    """客户端因连续失败被临时锁定。"""


@dataclass
class _FailureState:
    attempts: int = 0
    locked_until: float = 0.0
    last_failed_at: float = 0.0


class SettingsAuthService:
    MAX_FAILURES = 5
    LOCK_SECONDS = 5 * 60

    def __init__(
        self,
        *,
        password_provider: Callable[[], str] | None = None,
        session_minutes_provider: Callable[[], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._password_provider = password_provider or (
            lambda: get_config().settings.password
        )
        self._session_minutes_provider = session_minutes_provider or (
            lambda: get_config().settings.session_minutes
        )
        self._clock = clock
        self._sessions: dict[bytes, float] = {}
        self._failures: dict[str, _FailureState] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def _cleanup_sessions(self, now: float) -> None:
        expired = [digest for digest, expires_at in self._sessions.items() if expires_at <= now]
        for digest in expired:
            self._sessions.pop(digest, None)

    def _get_active_failure(self, client_ip: str, now: float) -> _FailureState:
        state = self._failures.setdefault(client_ip, _FailureState())
        if state.locked_until and state.locked_until <= now:
            state.attempts = 0
            state.locked_until = 0.0
        elif state.attempts and now - state.last_failed_at >= self.LOCK_SECONDS:
            state.attempts = 0
        return state

    def is_rate_limited(self, client_ip: str) -> bool:
        with self._lock:
            now = self._clock()
            return self._get_active_failure(client_ip, now).locked_until > now

    def authenticate(self, password: str, client_ip: str) -> str:
        with self._lock:
            now = self._clock()
            state = self._get_active_failure(client_ip, now)
            if state.locked_until > now:
                raise LoginRateLimitedError("登录尝试过多，请稍后重试")

            expected = self._password_provider()
            supplied = password if isinstance(password, str) else ""
            valid = bool(expected) and secrets.compare_digest(
                supplied.encode("utf-8"), expected.encode("utf-8")
            )
            if not valid:
                state.attempts += 1
                state.last_failed_at = now
                if state.attempts >= self.MAX_FAILURES:
                    state.locked_until = now + self.LOCK_SECONDS
                    raise LoginRateLimitedError("登录尝试过多，请稍后重试")
                raise AuthenticationError("密码错误或暂时无法登录")

            self._failures.pop(client_ip, None)
            self._cleanup_sessions(now)
            token = secrets.token_urlsafe(32)
            minutes = max(1, int(self._session_minutes_provider()))
            self._sessions[self._digest(token)] = now + minutes * 60
            return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            now = self._clock()
            self._cleanup_sessions(now)
            expires_at = self._sessions.get(self._digest(token))
            return expires_at is not None and expires_at > now

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(self._digest(token), None)


settings_auth = SettingsAuthService()
