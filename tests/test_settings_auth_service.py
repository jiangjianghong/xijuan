from __future__ import annotations

from dataclasses import dataclass

import pytest

from service.settings_auth_service import (
    AuthenticationError,
    LoginRateLimitedError,
    SettingsAuthService,
)


@dataclass
class FakeClock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_service(clock: FakeClock) -> SettingsAuthService:
    return SettingsAuthService(
        password_provider=lambda: "admin-password",
        session_minutes_provider=lambda: 30,
        clock=clock,
    )


def test_authenticate_creates_valid_session_and_stores_only_digest():
    clock = FakeClock()
    service = make_service(clock)

    token = service.authenticate("admin-password", "127.0.0.1")

    assert service.validate(token) is True
    assert token.encode() not in service._sessions
    assert all(len(digest) == 32 for digest in service._sessions)


def test_session_expires_after_configured_duration():
    clock = FakeClock()
    service = make_service(clock)
    token = service.authenticate("admin-password", "127.0.0.1")

    clock.advance(30 * 60 - 1)
    assert service.validate(token) is True
    clock.advance(1)
    assert service.validate(token) is False


def test_revoke_invalidates_session():
    clock = FakeClock()
    service = make_service(clock)
    token = service.authenticate("admin-password", "127.0.0.1")

    service.revoke(token)

    assert service.validate(token) is False


def test_five_failures_lock_ip_for_five_minutes():
    clock = FakeClock()
    service = make_service(clock)

    for _ in range(4):
        with pytest.raises(AuthenticationError):
            service.authenticate("wrong", "10.0.0.1")

    with pytest.raises(LoginRateLimitedError):
        service.authenticate("wrong", "10.0.0.1")
    with pytest.raises(LoginRateLimitedError):
        service.authenticate("admin-password", "10.0.0.1")

    clock.advance(5 * 60)
    token = service.authenticate("admin-password", "10.0.0.1")
    assert service.validate(token) is True


def test_successful_login_clears_previous_failures():
    clock = FakeClock()
    service = make_service(clock)

    for _ in range(4):
        with pytest.raises(AuthenticationError):
            service.authenticate("wrong", "10.0.0.2")
    service.authenticate("admin-password", "10.0.0.2")

    for _ in range(4):
        with pytest.raises(AuthenticationError):
            service.authenticate("wrong", "10.0.0.2")
    token = service.authenticate("admin-password", "10.0.0.2")
    assert service.validate(token) is True


def test_old_failures_do_not_count_as_consecutive_attempts():
    clock = FakeClock()
    service = make_service(clock)

    for _ in range(4):
        with pytest.raises(AuthenticationError):
            service.authenticate("wrong", "10.0.0.3")
    clock.advance(5 * 60)

    with pytest.raises(AuthenticationError):
        service.authenticate("wrong", "10.0.0.3")
    assert service.is_rate_limited("10.0.0.3") is False


def test_empty_configured_password_never_authenticates():
    service = SettingsAuthService(
        password_provider=lambda: "",
        session_minutes_provider=lambda: 30,
        clock=FakeClock(),
    )

    with pytest.raises(AuthenticationError):
        service.authenticate("", "127.0.0.1")


def test_unicode_password_authenticates_and_unicode_failures_are_counted():
    clock = FakeClock()
    service = SettingsAuthService(
        password_provider=lambda: "管理员密码",
        session_minutes_provider=lambda: 30,
        clock=clock,
    )

    token = service.authenticate("管理员密码", "10.0.0.4")
    assert service.validate(token) is True
    for _ in range(4):
        with pytest.raises(AuthenticationError):
            service.authenticate("错误密码", "10.0.0.5")
    with pytest.raises(LoginRateLimitedError):
        service.authenticate("错误密码", "10.0.0.5")
