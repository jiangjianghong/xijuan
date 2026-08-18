from pathlib import Path


ROOT = Path(__file__).parents[1]
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
javascript = (ROOT / "ui" / "js" / "runtime-monitor.js").read_text(
    encoding="utf-8"
)
css = (ROOT / "ui" / "css" / "style.css").read_text(encoding="utf-8")
compact_css = "".join(css.split())


def test_runtime_help_dialog_is_static_accessible_and_read_only():
    assert 'id="runtime-help-open"' in html
    assert 'data-lucide="circle-help"' in html
    assert 'id="runtime-help-dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'id="runtime-help-close"' in html
    assert "默认配置示例 · 当前 worker · 只读监控" in html
    assert "所有独立分析请求合计最多同时处理 4 个 item" in html
    assert "两类来源合计最多同时执行 8 条规则" in html
    assert "openHelp()" in javascript
    assert "closeHelp(" in javascript
    assert "task_analysis" not in html + javascript


def test_runtime_matrix_uses_five_groups_without_horizontal_scroll():
    for label in ("模型通道", "业务阶段", "文件内任务", "独立接口", "管线"):
        assert label in html
    assert "grid-template-columns:3fr3fr3fr1fr1fr" in compact_css
    assert ".runtime-design-matrix-shell{min-width:0;overflow-x:hidden" in compact_css
