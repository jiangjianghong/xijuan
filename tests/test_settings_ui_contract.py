from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_settings_button_is_immediately_after_manage_button():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")

    manage = html.index("DocTypeManager.openManageDialog()")
    settings = html.index("SettingsManager.open()")

    assert manage < settings
    assert settings - manage < 400


def test_settings_dialog_and_script_are_present():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")

    assert 'id="settings-login-overlay"' in html
    assert 'id="settings-modal-overlay"' in html
    assert 'id="settings-form"' in html
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
        "vl_model",
        "web_search",
        "storage",
    ):
        assert group in script
    for action in ("keep", "replace", "clear"):
        assert action in script


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
