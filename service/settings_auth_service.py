"""系统设置管理员的短期内存会话与登录限速。"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

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
    MAX_TRACKED = 4096

    def __init__(
        self,
        *,
        password_provider: Callable[[], str] | None = None,
        session_minutes_provider: Callable[[], int] | None = None,
        username_provider: Callable[[], str] | None = None,
        password_hash_provider: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._password_provider = password_provider or (
            lambda: get_config().settings.password
        )
        self._session_minutes_provider = session_minutes_provider or (
            lambda: get_config().settings.session_minutes
        )
        self._username_provider = username_provider or (lambda: get_config().settings.username)
        self._password_hash_provider = password_hash_provider or (
            (lambda: "") if password_provider else (lambda: get_config().settings.password_hash)
        )
        self._clock = clock
        self._sessions: dict[bytes, tuple[float, bytes]] = {}
        self._failures: dict[str, _FailureState] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def _cleanup_sessions(self, now: float) -> None:
        expired = [digest for digest, (expires_at, _) in self._sessions.items() if expires_at <= now]
        for digest in expired:
            self._sessions.pop(digest, None)

    def _get_active_failure(self, client_ip: str, now: float) -> _FailureState:
        if client_ip not in self._failures and len(self._failures) >= self.MAX_TRACKED:
            self._failures = {ip: state for ip, state in self._failures.items()
                              if now - state.last_failed_at < self.LOCK_SECONDS}
            if len(self._failures) >= self.MAX_TRACKED:
                raise LoginRateLimitedError("登录尝试过多，请稍后重试")
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

    def _credential_fingerprint(self) -> bytes:
        return self._digest(repr((self._username_provider(), self._password_provider(),
                                 self._password_hash_provider())))

    def authenticate(self, password: str, client_ip: str, username: str | None = None) -> str:
        with self._lock:
            now = self._clock()
            state = self._get_active_failure(client_ip, now)
            if state.locked_until > now:
                raise LoginRateLimitedError("登录尝试过多，请稍后重试")

            expected = self._password_provider()
            encoded = self._password_hash_provider()
            supplied = password if isinstance(password, str) else ""
            if encoded:
                try:
                    valid = PasswordHasher().verify(encoded, supplied)
                except (VerificationError, InvalidHashError):
                    valid = False
            else:
                valid = bool(expected) and secrets.compare_digest(
                    supplied.encode("utf-8"), expected.encode("utf-8")
                )
            expected_user = self._username_provider()
            valid_user = secrets.compare_digest(
                (expected_user if username is None else username).encode(), expected_user.encode()
            )
            valid = valid and valid_user
            if not valid:
                state.attempts += 1
                state.last_failed_at = now
                if state.attempts >= self.MAX_FAILURES:
                    state.locked_until = now + self.LOCK_SECONDS
                    raise LoginRateLimitedError("登录尝试过多，请稍后重试")
                raise AuthenticationError("账号或密码错误，或尚未配置管理员")

            self._failures.pop(client_ip, None)
            self._cleanup_sessions(now)
            if len(self._sessions) >= self.MAX_TRACKED:
                self._sessions.pop(next(iter(self._sessions)))
            token = secrets.token_urlsafe(32)
            minutes = max(1, int(self._session_minutes_provider()))
            self._sessions[self._digest(token)] = (now + minutes * 60, self._credential_fingerprint())
            return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            now = self._clock()
            self._cleanup_sessions(now)
            session = self._sessions.get(self._digest(token))
            return (session is not None and session[0] > now
                    and secrets.compare_digest(session[1], self._credential_fingerprint()))

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(self._digest(token), None)


settings_auth = SettingsAuthService()
