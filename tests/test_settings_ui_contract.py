from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_settings_and_type_management_remain_accessible_in_new_shell():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    sidebar = html.split('id="workspace-sidebar"', 1)[1].split('</aside>', 1)[0]
    topbar = html.split('class="header workbench-topbar"', 1)[1].split('</header>', 1)[0]
    assert 'data-page="settings"' in sidebar
    assert "App.switchPage('settings')" in sidebar
    assert 'DocTypeManager.openManageDialog()' in topbar
    assert 'aria-label="管理项目与文档类型"' in topbar


def test_settings_is_inside_workspace_and_only_relogin_uses_dialog():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")

    assert 'id="settings-login-overlay"' in html
    sheet = html.split('id="workspace-sheet"', 1)[1].split('<!-- /workspace-sheet -->', 1)[0]
    settings = sheet.split('id="page-settings" class="page-container"', 1)[1]
    assert 'id="settings-modal-overlay"' not in html
    assert 'role="dialog"' not in settings
    assert 'id="settings-form"' in settings
    assert '<script src="js/settings.js"></script>' in html


def test_settings_script_defines_all_groups_and_secret_actions():
    script = (ROOT / "ui/js/settings.js").read_text(encoding="utf-8")

    for group in (
        "mineru",
        "chunking",
        "embedding",
        "extraction",
        "table_name_validation",
        "analysis",
        "agent",
        "rerank",
        "vl_model",
        "web_search",
        "storage",
    ):
        assert group in script
    for action in ("keep", "replace", "clear"):
        assert action in script
    agent_fields = script.split("agent: [", 1)[1].split("rerank: [", 1)[0]
    assert "['extra_params', '附加参数', 'json'" in agent_fields
    assert "['tool_result_max_chars', '单次工具结果上限', 'number'" in agent_fields
    assert "默认 20000" in agent_fields


def test_api_client_exposes_settings_methods_and_error_status():
    script = (ROOT / "ui/js/api.js").read_text(encoding="utf-8")

    for method in (
        "settingsLogin",
        "getSettingsSession",
        "getRuntimeSettings",
        "updateRuntimeSettings",
        "settingsLogout",
    ):
        assert method in script
    assert "requestError.status = response.status" in script


def test_relogin_and_conflict_paths_preserve_dirty_draft():
    script = (ROOT / "ui/js/settings.js").read_text(encoding="utf-8")

    assert "this.setDirty(restoredDraft)" in script
    assert "reloadConflictDraft" in script
    assert "只重新应用本次修改" in script


def test_settings_groups_are_nested_in_the_workspace_sidebar():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    script = (ROOT / "ui/js/settings.js").read_text(encoding="utf-8")

    assert 'id="settings-group-nav"' not in html
    assert 'id="settings-group-select"' not in html
    assert 'id="settings-directory"' not in html
    sidebar = html.split('id="workspace-sidebar"', 1)[1].split('</aside>', 1)[0]
    assert 'id="settings-subnav"' in sidebar
    assert 'aria-expanded="false" aria-controls="settings-subnav"' in sidebar
    assert 'data-jump-group=' in script
    assert 'syncActiveGroup()' in script
    assert 'jumpToGroup(group)' in script
