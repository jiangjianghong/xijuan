from pathlib import Path


ROOT = Path(__file__).parents[1]
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
javascript = (ROOT / "ui" / "js" / "runtime-monitor.js").read_text(
    encoding="utf-8"
)
css = (ROOT / "ui" / "css" / "style.css").read_text(encoding="utf-8")
app_js = (ROOT / "ui" / "js" / "app.js").read_text(encoding="utf-8")
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


def test_runtime_history_comes_from_backend_not_local_accumulation():
    # 历史一律由后端下发；本地累积会导致刷新清零、多标签页各看各的
    assert "appendFixedHistory" not in javascript
    assert "applyHistory" in javascript
    assert "getRuntimeConcurrency(this.state.window)" in javascript
    assert 'id="runtime-window-select"' in html
    for label in ("最近 60 秒", "最近 5 分钟", "最近 30 分钟"):
        assert label in html


def test_runtime_page_is_a_normal_inner_page_like_statistics():
    # 运行台不再整页接管：全站 header 与导航保持可见，与统计页一致
    assert "runtime-monitor-mode" not in html
    assert "runtime-monitor-mode" not in app_js
    assert "runtime-monitor-mode" not in css
    # 页面自带的品牌 header 已删除，工具条下沉到内容区 glass-card
    assert 'class="runtime-design-header"' not in html
    assert 'id="runtime-brand-entry"' not in html
    assert 'class="runtime-toolbar glass-card"' in html
    # 连接状态、帮助、刷新三个控件保留，只是换了位置
    assert 'id="runtime-connection-pill"' in html
    assert 'id="runtime-help-open"' in html
    assert 'id="runtime-monitor-refresh"' in html
