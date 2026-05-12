"""启动配置自检单测。"""
from __future__ import annotations

from service.startup_check import CheckResult


def test_check_result_defaults():
    r = CheckResult(name="x", ok=True, elapsed_ms=12, detail="d")
    assert r.name == "x"
    assert r.ok is True
    assert r.elapsed_ms == 12
    assert r.detail == "d"
    assert r.error is None


def test_check_result_with_error():
    r = CheckResult(name="x", ok=False, elapsed_ms=0, detail="", error="boom")
    assert r.ok is False
    assert r.error == "boom"
