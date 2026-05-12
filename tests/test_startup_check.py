"""启动配置自检单测。"""
from __future__ import annotations

import asyncio

import pytest

from service.startup_check import CheckResult, _run_one


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


@pytest.mark.asyncio
async def test_run_one_returns_ok_result():
    async def good():
        return CheckResult(name="good", ok=True, elapsed_ms=5, detail="ok")

    r = await _run_one("good", good)
    assert r.ok is True
    assert r.name == "good"


@pytest.mark.asyncio
async def test_run_one_swallows_exception():
    async def boom():
        raise RuntimeError("nope")

    r = await _run_one("boom", boom)
    assert r.ok is False
    assert r.name == "boom"
    assert "RuntimeError" in r.error
    assert "nope" in r.error


@pytest.mark.asyncio
async def test_run_one_respects_timeout(monkeypatch):
    monkeypatch.setattr("service.startup_check._CHECK_TIMEOUT", 0.1)

    async def slow():
        await asyncio.sleep(5)
        return CheckResult(name="slow", ok=True, elapsed_ms=0, detail="")

    r = await _run_one("slow", slow)
    assert r.ok is False
    assert "Timeout" in r.error
