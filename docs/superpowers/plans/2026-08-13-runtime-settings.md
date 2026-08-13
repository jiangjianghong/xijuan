# Runtime Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a password-protected settings dialog that safely edits the approved sections of `configs/config.yaml` and applies valid changes to subsequent requests without restarting the service.

**Architecture:** A focused configuration service validates allowlisted patches, preserves YAML comments during atomic writes, and swaps one immutable in-memory `AppConfig` reference. A separate in-memory authentication service protects a dedicated FastAPI settings router. The static UI uses a standalone `settings.js` module and opens from a button immediately to the right of the existing type-management button.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, ruamel.yaml, pytest/httpx, static HTML/CSS/JavaScript, Lucide icons.

## Global Constraints

- Use only `configs/config.yaml`; do not add a runtime override file or configuration database.
- The settings password remains plaintext in YAML and is never returned or editable through the API.
- Expose only `mineru`, `chunking`, `embedding`, `extraction`, `table_name_validation`, `analysis`, `vl_model`, `web_search`, and `storage`.
- In `embedding`, only `base_url`, `api_key`, and `model_name` are editable; all other fields are read-only.
- Never return secret values. Secret changes use explicit `keep`, `replace`, or `clear` operations.
- MySQL, Milvus, and server configuration remain hidden and immutable through this API.
- Sessions last 30 minutes by default and use an HttpOnly, SameSite=Strict cookie.
- New requests see new configuration; already running work is not forced to change snapshots.
- Preserve YAML comments, ordering, unknown nodes, and all closed configuration groups.
- Do not log passwords, session tokens, or API keys.

---

### Task 1: Runtime Configuration Store

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `utils/config.py`
- Create: `service/settings_service.py`
- Create: `tests/test_settings_service.py`

**Interfaces:**
- Produces: `get_config() -> AppConfig`, `replace_config(config: AppConfig) -> None`, `SettingsService.read_public_config()`, and `SettingsService.update_config(request)`.
- Consumes: `APP_CONFIG_PATH` and the existing Pydantic section models.

- [ ] **Step 1: Add failing configuration service tests**

Create tests using a temporary commented YAML file. Assert that public output contains only allowed groups, masks secrets as `{configured: bool}`, marks embedding fields read-only, rejects forbidden fields, preserves `mysql`, comments and passwords, enforces version conflicts, and supports secret keep/replace/clear.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run pytest tests/test_settings_service.py -v`

Expected: collection or import failure because `SettingsService` and request models do not exist.

- [ ] **Step 3: Add round-trip YAML dependency**

Run: `uv add "ruamel-yaml>=0.18,<0.19"`

Expected: `pyproject.toml` and `uv.lock` include `ruamel-yaml`.

- [ ] **Step 4: Implement replaceable validated config state**

Replace the `lru_cache`-only implementation with a lock-protected module reference:

```python
_current_config: AppConfig | None = None
_config_lock = threading.RLock()

def get_config() -> AppConfig:
    global _current_config
    with _config_lock:
        if _current_config is None:
            _current_config = load_config()
        return _current_config

def replace_config(config: AppConfig) -> None:
    global _current_config
    with _config_lock:
        _current_config = config
```

Add `SettingsSecurityConfig` with `password`, `session_minutes=30`, and `secure_cookie=False`.

- [ ] **Step 5: Implement allowlisted, atomic settings persistence**

In `service/settings_service.py`, define explicit editable paths and secret paths. Use `ruamel.yaml.YAML()` in round-trip mode, merge only submitted values, build `AppConfig(**plain_data)`, prebuild a new VL semaphore, write a same-directory temporary file, flush and `os.fsync`, then `os.replace`. Only after replacement, swap config and semaphore references.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/test_settings_service.py -v`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock utils/config.py service/settings_service.py tests/test_settings_service.py
git commit -m "feat: add runtime configuration store"
```

### Task 2: Administrator Session Security

**Files:**
- Create: `service/settings_auth_service.py`
- Create: `tests/test_settings_auth_service.py`

**Interfaces:**
- Produces: `authenticate(password, client_ip) -> str`, `validate(token) -> bool`, `revoke(token) -> None`, and `is_rate_limited(client_ip) -> bool`.
- Consumes: `get_config().settings`.

- [ ] **Step 1: Add failing authentication tests**

Cover correct and incorrect password, constant-time comparison through behavioral tests, five-failure lockout for five minutes, successful-login counter reset, token expiry at configured session duration, token digest-only storage, and revocation.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/test_settings_auth_service.py -v`

Expected: import failure because the authentication service does not exist.

- [ ] **Step 3: Implement in-memory auth service**

Use `secrets.token_urlsafe(32)`, store only `sha256(token).digest()`, compare passwords with `secrets.compare_digest`, and guard session/failure dictionaries with a lock. Accept an injectable clock for deterministic tests.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_settings_auth_service.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add service/settings_auth_service.py tests/test_settings_auth_service.py
git commit -m "feat: protect settings with admin sessions"
```

### Task 3: Settings HTTP API

**Files:**
- Create: `blue_print/settings_router.py`
- Modify: `blue_print/__init__.py`
- Create: `tests/test_settings_router.py`

**Interfaces:**
- Produces: `POST /settings/login`, `GET /settings/session`, `GET /settings/config`, `PATCH /settings/config`, and `POST /settings/logout`.
- Consumes: settings service and auth service from Tasks 1 and 2.

- [ ] **Step 1: Add failing router tests**

Use a temporary config path and the ASGI client. Cover HttpOnly/SameSite cookies, 401 without a session, 429 on lockout, sanitized GET output, successful PATCH, 409 stale version, 422 forbidden/read-only fields, logout, and absence of secrets in response bodies.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/test_settings_router.py -v`

Expected: 404 responses because routes are not registered.

- [ ] **Step 3: Implement and register router**

Use actual HTTP status codes for authentication, lockout, conflict, validation, and write errors. Set cookie name `settings_session`, `httponly=True`, `samesite="strict"`, `max_age=session_minutes*60`, and `secure=get_config().settings.secure_cookie`. Restrict cookie path to `/settings`.

- [ ] **Step 4: Run router and service tests**

Run: `uv run pytest tests/test_settings_router.py tests/test_settings_service.py tests/test_settings_auth_service.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add blue_print/settings_router.py blue_print/__init__.py tests/test_settings_router.py
git commit -m "feat: expose protected settings API"
```

### Task 4: Settings Dialog UI

**Files:**
- Modify: `ui/index.html`
- Modify: `ui/js/api.js`
- Create: `ui/js/settings.js`
- Modify: `ui/css/style.css`

**Interfaces:**
- Produces: global `SettingsManager` with `open()`, `login()`, `save()`, `logout()`, and modal state management.
- Consumes: the five `/settings` endpoints and existing `Toast`, `Utils`, and Lucide assets.

- [ ] **Step 1: Add the header entry and dialog skeleton**

Place an icon-and-text “设置” button immediately after “管理”. Add password and full settings modal markup with accessible labels, close buttons, group navigation, form body, dirty-state footer, save, and logout controls. Load `settings.js` after `api.js`.

- [ ] **Step 2: Add API client methods**

Add `settingsLogin`, `getSettingsSession`, `getRuntimeSettings`, `updateRuntimeSettings`, and `settingsLogout`. Preserve response status on thrown errors so the UI can distinguish 401, 409, 422, and 429.

- [ ] **Step 3: Implement form schema and secret controls**

In `settings.js`, maintain fixed Chinese labels, units and control types for all nine groups. Render embedding non-editable fields as read-only. Secret controls track `keep`, `replace`, and `clear` without ever putting existing values into an input.

- [ ] **Step 4: Implement authentication, dirty state, save and conflict behavior**

On 401, keep the local draft and reopen login. On 409, keep the draft and offer a clear reload action. Send only changed editable fields plus explicit changed secret actions and `base_version`.

- [ ] **Step 5: Add responsive styling**

Use an unframed modal section layout with a compact group sidebar on desktop and horizontal group tabs on narrow screens. Keep controls and button text within their containers; use existing colors and spacing rather than introducing a new palette.

- [ ] **Step 6: Run static syntax checks**

Run: `node --check ui/js/api.js`

Run: `node --check ui/js/settings.js`

Expected: both exit 0.

- [ ] **Step 7: Commit**

```bash
git add ui/index.html ui/js/api.js ui/js/settings.js ui/css/style.css
git commit -m "feat: add runtime settings dialog"
```

### Task 5: Integration Verification and Documentation

**Files:**
- Modify: `configs/config.yaml`
- Modify: `configs/config.yaml.example`
- Modify: `docs/guides/configuration.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: all earlier tasks.
- Produces: deployable default settings configuration and documented behavior.

- [ ] **Step 1: Add settings defaults and documentation**

Add `settings.password`, `settings.session_minutes: 30`, and `settings.secure_cookie: false` to both YAML files. Document the nine open groups, secret write-only semantics, single-worker limitation, and atomic persistence behavior.

- [ ] **Step 2: Run focused and full automated verification**

Run: `uv run pytest tests/test_settings_service.py tests/test_settings_auth_service.py tests/test_settings_router.py -v`

Run: `uv run pytest`

Run: `python -m compileall blue_print service utils`

Run: `node --check ui/js/api.js`

Run: `node --check ui/js/settings.js`

Expected: all commands exit 0.

- [ ] **Step 3: Run the server and browser checks**

Start the app on an unused port. Verify desktop and mobile screenshots show the “设置” button after “管理”, password gate, nine groups, read-only embedding fields, secret state controls, and no overlapping or clipped UI. Verify login, save, logout, expiry handling, and that the network response never contains a secret.

- [ ] **Step 4: Inspect the final diff for secrets and unrelated changes**

Run: `git diff --check`

Run: `rg -n "actual-test-secret" . --glob '!configs/config.yaml' --glob '!logs/**'`

Expected: no whitespace errors and no test secret outside explicitly isolated fixtures.

- [ ] **Step 5: Commit**

```bash
git add configs/config.yaml configs/config.yaml.example docs/guides/configuration.md AGENTS.md
git commit -m "docs: document runtime settings"
```
