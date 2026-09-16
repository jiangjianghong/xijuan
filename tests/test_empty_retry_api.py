"""字段空值重试配置在保存、复制和导出导入中的保留。"""
import uuid


async def test_retry_config_roundtrip(client):
    prefix = "retry_" + uuid.uuid4().hex[:10]
    types = [prefix, prefix + "_copy", prefix + "_import"]
    try:
        for type_id in types:
            response = await client.post("/doctype", json={"type_id": type_id, "type_name": type_id})
            assert response.status_code == 200, response.text
        config = dict(field_id=prefix + "_field", type_id=prefix, field_name="重试字段", source_type="text", use_llm=0, empty_retry_enabled=True, empty_retry_count=3)
        response = await client.post("/extraction/fields", json=config)
        assert response.status_code == 200, response.text
        # 覆盖更新路径。
        config["empty_retry_count"] = 4
        response = await client.post("/extraction/fields", json=config)
        assert response.status_code == 200, response.text
        payload = (await client.get(f"/doctype/{prefix}/export")).json()["data"]
        response = await client.post(f"/doctype/{types[1]}/copy_from", json={"source_type_id": prefix})
        assert response.status_code == 200, response.text
        response = await client.post("/doctype/import", json={"target_type_id": types[2], "payload": payload})
        assert response.status_code == 200, response.text
        for type_id in types:
            response = await client.get("/extraction/fields", params={"type_id": type_id})
            assert response.status_code == 200, response.text
            fields = response.json()["data"]
            assert len(fields) == 1
            assert fields[0]["empty_retry_enabled"] is True
            assert fields[0]["empty_retry_count"] == 4
    finally:
        for type_id in reversed(types):
            await client.delete(f"/doctype/{type_id}?force=true")
