"""快照的向量接入测试：need_vectors 开关与惰性行为。"""

from service import extraction_snapshot


class FakeSession:
    """只回空结果的假会话：本测试只关心 vector_index 分支。"""

    async def execute(self, stmt):
        class Result:
            def scalar_one_or_none(self):
                return None

            def scalars(self):
                class Scalars:
                    def all(self):
                        return []

                return Scalars()

        return Result()


async def test_snapshot_skips_vector_load_by_default(monkeypatch):
    """need_vectors=False 时不碰 Milvus，vector_index 为 None。"""
    async def fail_load(file_id, parent_chunks):
        raise AssertionError("need_vectors=False 不应拉取向量")

    monkeypatch.setattr(extraction_snapshot, "load_file_vector_index", fail_load)

    snapshot = await extraction_snapshot.load_extraction_snapshot("f1", FakeSession())

    assert snapshot.vector_index is None


async def test_snapshot_loads_vectors_when_requested(monkeypatch):
    """need_vectors=True 时拉一次向量，并把父块传进去建查找表。"""
    captured = {}

    async def fake_load(file_id, parent_chunks):
        captured["file_id"] = file_id
        captured["parents"] = list(parent_chunks)
        return "INDEX"

    monkeypatch.setattr(extraction_snapshot, "load_file_vector_index", fake_load)

    snapshot = await extraction_snapshot.load_extraction_snapshot(
        "f1", FakeSession(), need_vectors=True
    )

    assert snapshot.vector_index == "INDEX"
    assert captured["file_id"] == "f1"
    assert captured["parents"] == []  # FakeSession 无 chunk


async def test_snapshot_vector_load_failure_does_not_kill_extraction(monkeypatch):
    """Milvus 挂了不该让整个提取阶段失败——vector_db 字段失败即可，
    其他 search_type 的字段照常跑。"""
    async def boom(file_id, parent_chunks):
        raise RuntimeError("Milvus 连接失败")

    monkeypatch.setattr(extraction_snapshot, "load_file_vector_index", boom)

    snapshot = await extraction_snapshot.load_extraction_snapshot(
        "f1", FakeSession(), need_vectors=True
    )

    assert snapshot.vector_index is None
