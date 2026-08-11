"""配置版本探针测试：GET /doctype/{type_id}/config_version

探针用于多人协作场景下前端轮询判断「他人是否改过本类型的配置」。
测试一律显式写入已知的 updated_at 时间戳，不依赖真实时钟——updated_at 是秒级
datetime，靠 sleep 推进时间会让测试 flaky。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, update

from model.database import get_session_factory
from model.tables import AnalysisRule, ExtractionField

TYPE_ID = "test_cfgver_type"
OTHER_TYPE_ID = "test_cfgver_other"
OLD_TIME = datetime(2020, 1, 1, 0, 0, 0)
NEW_TIME = datetime(2021, 6, 1, 12, 0, 0)


async def _cleanup() -> None:
    """清掉本测试文件用到的两个类型下的全部配置（测试库与 dev 库共享，必须自清）。"""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            delete(ExtractionField).where(ExtractionField.type_id.in_([TYPE_ID, OTHER_TYPE_ID]))
        )
        await session.execute(
            delete(AnalysisRule).where(AnalysisRule.type_id.in_([TYPE_ID, OTHER_TYPE_ID]))
        )
        await session.commit()


async def _add_field(field_id: str, type_id: str = TYPE_ID, ts: datetime = OLD_TIME) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            ExtractionField(
                field_id=field_id,
                type_id=type_id,
                field_name=field_id,
                source_type="text",
                enabled=1,
                priority=0,
                created_at=ts,
                updated_at=ts,
            )
        )
        await session.commit()


async def _add_rule(rule_id: str, type_id: str = TYPE_ID, ts: datetime = OLD_TIME) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            AnalysisRule(
                rule_id=rule_id,
                type_id=type_id,
                rule_name=rule_id,
                rule_type="calc",
                expression="1+1",
                enabled=1,
                priority=0,
                created_at=ts,
                updated_at=ts,
            )
        )
        await session.commit()


async def _delete_field(field_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(ExtractionField).where(ExtractionField.field_id == field_id))
        await session.commit()


async def _probe(client: AsyncClient, type_id: str = TYPE_ID) -> dict:
    resp = await client.get(f"/doctype/{type_id}/config_version")
    assert resp.status_code == 200
    return resp.json()["data"]


@pytest.mark.anyio
async def test_config_version_empty_type(client: AsyncClient):
    """无任何配置的类型应返回 count=0 / latest=null，而不是 404 或 500。"""
    await _cleanup()
    data = await _probe(client)
    assert data["type_id"] == TYPE_ID
    assert data["fields"] == {"count": 0, "latest": None}
    assert data["rules"] == {"count": 0, "latest": None}


@pytest.mark.anyio
async def test_config_version_detects_add(client: AsyncClient):
    """新增字段应让 count 变化。"""
    await _cleanup()
    before = await _probe(client)
    await _add_field("cfgver_f1")
    after = await _probe(client)
    assert after["fields"]["count"] == before["fields"]["count"] + 1
    assert after["fields"] != before["fields"]
    await _cleanup()


@pytest.mark.anyio
async def test_config_version_detects_update(client: AsyncClient):
    """原地修改字段应让 latest 变化，count 不变。"""
    await _cleanup()
    await _add_field("cfgver_f1", ts=OLD_TIME)
    before = await _probe(client)

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(ExtractionField)
            .where(ExtractionField.field_id == "cfgver_f1")
            .values(field_name="改名了", updated_at=NEW_TIME)
        )
        await session.commit()

    after = await _probe(client)
    assert after["fields"]["count"] == before["fields"]["count"]
    assert after["fields"]["latest"] != before["fields"]["latest"]
    await _cleanup()


@pytest.mark.anyio
async def test_config_version_detects_delete(client: AsyncClient):
    """删除字段应让 count 变化——这正是只看 MAX(updated_at) 探不到的场景。"""
    await _cleanup()
    await _add_field("cfgver_f1")
    await _add_field("cfgver_f2")
    before = await _probe(client)
    await _delete_field("cfgver_f2")
    after = await _probe(client)
    assert after["fields"]["count"] == before["fields"]["count"] - 1
    assert after["fields"] != before["fields"]
    await _cleanup()


@pytest.mark.anyio
async def test_config_version_detects_delete_plus_add(client: AsyncClient):
    """同时删一条加一条：count 不变，但 latest 变，组合判据仍能探到。"""
    await _cleanup()
    await _add_field("cfgver_f1", ts=OLD_TIME)
    before = await _probe(client)

    await _delete_field("cfgver_f1")
    await _add_field("cfgver_f2", ts=NEW_TIME)

    after = await _probe(client)
    assert after["fields"]["count"] == before["fields"]["count"]
    assert after["fields"]["latest"] != before["fields"]["latest"]
    await _cleanup()


@pytest.mark.anyio
async def test_config_version_tracks_rules_separately(client: AsyncClient):
    """规则的改动记在 rules 上，不串到 fields。"""
    await _cleanup()
    before = await _probe(client)
    await _add_rule("cfgver_r1")
    after = await _probe(client)
    assert after["rules"]["count"] == before["rules"]["count"] + 1
    assert after["fields"] == before["fields"]
    await _cleanup()


@pytest.mark.anyio
async def test_config_version_isolated_by_type(client: AsyncClient):
    """其他类型的改动不能影响本类型的版本，否则前端会被无关改动反复打扰。"""
    await _cleanup()
    before = await _probe(client, TYPE_ID)
    await _add_field("cfgver_other_f1", type_id=OTHER_TYPE_ID, ts=NEW_TIME)
    after = await _probe(client, TYPE_ID)
    assert after == before
    await _cleanup()


@pytest.mark.anyio
async def test_config_version_changes_after_real_upsert(client: AsyncClient):
    """走真实保存接口后 latest 必须变化。

    这条守的是 updated_at 的刷新链路：两张表都没有 DDL 级 ON UPDATE CURRENT_TIMESTAMP，
    刷新完全靠 SQLAlchemy 的 onupdate=func.now()。一旦有人把 upsert 改成裸 SQL，
    探针会静默失效——那时这条测试会挂，而不是等用户报「还是要手动刷新」。
    """
    await _cleanup()
    await _add_field("cfgver_f1", ts=OLD_TIME)
    before = await _probe(client)

    payload = {
        "field_id": "cfgver_f1",
        "type_id": TYPE_ID,
        "field_name": "接口改过的名字",
        "source_type": "text",
        "enabled": 1,
        "priority": 0,
        "use_llm": 0,
        "search_type": "context",
        "search_config": {"keywords": ["测试"]},
    }
    resp = await client.post("/extraction/fields", json=payload)
    assert resp.status_code == 200, resp.text

    after = await _probe(client)
    assert after["fields"]["count"] == before["fields"]["count"]
    assert after["fields"]["latest"] != before["fields"]["latest"]
    await _cleanup()
