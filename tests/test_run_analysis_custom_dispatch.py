"""run_analysis 对 custom 规则的分派冒烟：mock 掉 execute_custom 与 DB 写入。"""

from __future__ import annotations

from types import SimpleNamespace

from service import analysis_service


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Session:
    """按 execute 调用序返回预设结果：file 行 → 规则 → 提取结果 → 查已存结果。"""

    def __init__(self, rule):
        self._rule = rule
        self._n = 0
        self.added = []

    async def execute(self, statement):
        self._n += 1
        if self._n == 1:      # File 行
            return _Result([SimpleNamespace(type_id="default")])
        if self._n == 2:      # 规则列表
            return _Result([self._rule])
        if self._n == 3:      # 提取结果
            return _Result([SimpleNamespace(field_id="a", extracted_value="200", source_refs=None)])
        return _Result([])    # 查已存 AnalysisResult / update File

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


async def test_run_analysis_dispatches_custom(monkeypatch):
    called = {}

    async def fake_custom(resolved, *, is_formatted, output_schema, system_prompt):
        called["is_formatted"] = is_formatted
        called["output_schema"] = output_schema
        return "生成值", "生成理由"

    async def noop_callback(*a, **k):
        return None

    monkeypatch.setattr(analysis_service, "execute_custom", fake_custom)
    monkeypatch.setattr(analysis_service, "notify_callback", noop_callback)

    rule = SimpleNamespace(
        rule_id="c1", rule_name="自定义", rule_type="custom",
        expression="根据<field_result>a</field_result>生成", system_prompt="",
        depend_fields=["a"], web_search=None, priority=0,
        is_formatted=1, output_schema=[{"key": "k", "type": "string"}],
    )
    session = _Session(rule)
    await analysis_service.run_analysis("file1", session)

    assert called["is_formatted"] is True
    assert called["output_schema"] == [{"key": "k", "type": "string"}]
    assert any(getattr(o, "result_value", None) == "生成值" for o in session.added)
