from pathlib import Path


HTML_PATH = Path(__file__).parents[1] / "design-mockups" / "concurrency-pools.html"
POOL_IDS = {
    "global_llm",
    "global_embedding",
    "global_vl",
    "global_table_validation",
    "global_extraction",
    "global_analysis",
    "task_table_validation",
    "task_extraction",
    "task_analysis",
    "global_pipeline",
}


def test_concurrency_monitor_contains_required_runtime_contract():
    html = HTML_PATH.read_text(encoding="utf-8")
    assert not any(url in html for url in ("https://", "http://"))
    for pool_id in POOL_IDS:
        assert f"id: '{pool_id}'" in html
    for control_id in (
        "toggle-live",
        "refresh-now",
        "detail-drawer",
        "pressure-chart",
        "pool-pressure-grid",
    ):
        assert f'id="{control_id}"' in html
    assert "stack: 'load'" in html
    assert "name: '排队中'" in html
    assert "type: 'custom'" in html
    assert "prefers-reduced-motion" in html
    assert "pressureHistory:" in html
    assert "function renderPoolPressureCharts()" in html
    assert "function pressureAxisRange(history)" in html
    assert "name: '当前采样'" in html
    assert 'style="height:74px;width:100%"' in html
    assert "grid-cols-1" in html
    assert "sm:grid-cols-2" in html
