/**
 * 系统运行时设置：密码会话、脱敏配置表单和差量保存。
 */
const SettingsManager = {
    payload: null,
    original: {},
    draftBeforeAuth: null,
    dirty: false,
    pendingOpen: false,
    secretState: {},
    scrollSpyHandler: null,

    GROUPS: [
        { id: 'mineru', label: 'MinerU', icon: 'file-scan', description: 'PDF 解析服务与上传限制' },
        { id: 'chunking', label: '文本分块', icon: 'split', description: '文本切分长度、重叠与分隔符' },
        { id: 'embedding', label: '向量化', icon: 'binary', description: 'Embedding 服务连接与只读运行参数' },
        { id: 'extraction', label: '字段提取', icon: 'scan-text', description: '字段提取使用的语言模型' },
        { id: 'table_name_validation', label: '表格名校验', icon: 'table-properties', description: '独立的表格名称识别模型配置' },
        { id: 'analysis', label: '逻辑分析', icon: 'waypoints', description: '计算精度、判定超时与并发' },
        { id: 'vl_model', label: 'VL 视觉模型', icon: 'scan-eye', description: '视觉模型、图像限制与并发' },
        { id: 'web_search', label: '博查网络搜索', icon: 'search', description: 'Judge 规则使用的联网搜索服务' },
        { id: 'storage', label: 'PDF 存储', icon: 'hard-drive', description: '原始 PDF 容量和保留时间' },
        { id: 'concurrency', label: '模型并发', icon: 'gauge', description: '模型通道、业务阶段与单任务并发上限' },
    ],

    FIELDS: {
        mineru: [
            ['base_url', '服务地址', 'url'], ['backend', '解析后端', 'text'],
            ['queue_width', '队列宽度', 'number', { min: 1 }],
            ['parse_timeout', '解析超时', 'number', { min: 1, unit: '秒' }],
            ['max_file_size', '单文件上限', 'number', { min: 1, unit: '字节' }],
        ],
        chunking: [
            ['chunk_size', '目标块大小', 'number', { min: 1, unit: '字符' }],
            ['chunk_overlap', '块间重叠', 'number', { min: 0, unit: '字符' }],
            ['max_chunk_size', '最大块大小', 'number', { min: 1, unit: '字符' }],
            ['separators', '分隔符优先级', 'json', { rows: 3 }],
        ],
        embedding: [
            ['base_url', '服务地址', 'url'], ['model', '模型名称', 'text'],
            ['api_key', 'API Key', 'secret'],
            ['embedding_dim', '向量维度', 'number', { readonly: true }],
            ['batch_size', '批量请求数', 'number', { readonly: true }],
            ['timeout', '请求超时', 'number', { readonly: true, unit: '秒' }],
            ['retry_count', '重试次数', 'number', { readonly: true }],
        ],
        extraction: [
            ['base_url', '服务地址', 'url'], ['model', '模型名称', 'text'],
            ['api_key', 'API Key', 'secret'],
            ['timeout', '请求超时', 'number', { min: 1, unit: '秒' }],
            ['retry_count', '重试次数', 'number', { min: 1 }],
            ['max_context_length', '最大上下文长度', 'number', { min: 1, unit: '字符' }],
            ['extra_body', '额外请求参数', 'json', { rows: 4 }],
        ],
        table_name_validation: [
            ['base_url', '服务地址', 'url', { nullable: true }],
            ['model', '模型名称', 'text', { nullable: true }],
            ['api_key', 'API Key', 'secret'],
            ['timeout', '请求超时', 'number', { min: 1, unit: '秒', nullable: true }],
            ['retry_count', '重试次数', 'number', { min: 1, nullable: true }],
            ['max_context_length', '最大上下文长度', 'number', { min: 1, unit: '字符', nullable: true }],
            ['max_context_lines', '参考行数', 'number', { min: 1, nullable: true }],
            ['extra_body', '额外请求参数', 'json', { rows: 4, nullable: true }],
        ],
        analysis: [
            ['calc_precision', '计算精度', 'number', { min: 0, unit: '位小数' }],
            ['judge_timeout', '判定超时', 'number', { min: 1, unit: '秒' }],
        ],
        vl_model: [
            ['base_url', '服务地址', 'url'], ['model', '模型名称', 'text'],
            ['api_key', 'API Key', 'secret'],
            ['temperature', '温度', 'number', { min: 0, step: 0.1 }],
            ['max_tokens', '最大输出 Token', 'number', { min: 1 }],
            ['timeout', '请求超时', 'number', { min: 1, unit: '秒' }],
            ['default_max_pixels', '单页最大像素', 'number', { min: 1, unit: '像素' }],
            ['pdf_storage_dir', 'PDF 存储目录', 'text'],
            ['extra_body', '额外请求参数', 'json', { rows: 4 }],
        ],
        web_search: [
            ['base_url', '服务地址', 'url'], ['api_key', 'API Key', 'secret'],
            ['count', '默认结果数', 'number', { min: 1 }],
            ['summary', '返回长摘要', 'boolean'],
            ['freshness', '默认时间范围', 'select', { options: ['noLimit', 'oneDay', 'oneWeek', 'oneMonth', 'oneYear'] }],
            ['timeout', '请求超时', 'number', { min: 1, unit: '秒' }],
            ['retry_count', '重试次数', 'number', { min: 1 }],
            ['max_result_length', '搜索文本上限', 'number', { min: 1, unit: '字符' }],
        ],
        storage: [
            ['max_total_bytes', 'PDF 总容量上限', 'number', { min: 0, unit: '字节，0 为不限' }],
            ['max_retention_minutes', '最长保留时间', 'number', { min: 0, unit: '分钟，0 为不限' }],
            ['cleanup_interval_minutes', '清理扫描周期', 'number', { min: 1, unit: '分钟' }],
        ],
        concurrency: [
            ['global_llm', '全局文本 LLM 并发', 'number', { min: 1 }],
            ['global_embedding', '全局 Embedding 并发', 'number', { min: 1 }],
            ['global_vl', '全局 VL 并发', 'number', { min: 1 }],
            ['global_table_validation', '全局表格名校验并发', 'number', { min: 1 }],
            ['global_extraction', '全局字段抽取并发', 'number', { min: 1 }],
            ['global_analysis', '全局逻辑分析并发', 'number', { min: 1 }],
            ['task_table_validation', '单任务表格名校验并发', 'number', { min: 1 }],
            ['task_extraction', '单任务字段抽取并发', 'number', { min: 1 }],
            ['task_analysis', '单任务逻辑分析并发', 'number', { min: 1 }],
            ['global_pipeline', '全局文件管线并发', 'number', { min: 1 }],
        ],
    },

    escape(value) {
        return Utils.escapeHtml(String(value ?? ''));
    },

    async open() {
        this.pendingOpen = true;
        try {
            const session = await API.getSettingsSession();
            if (!session.authenticated) return this.openLogin();
            await this.loadAndShow();
        } catch (error) {
            Toast.error('检查设置会话失败: ' + error.message);
        }
    },

    openLogin() {
        document.getElementById('settings-login-error').textContent = '';
        document.getElementById('settings-login-overlay').classList.add('active');
        const password = document.getElementById('settings-password');
        password.value = '';
        setTimeout(() => password.focus(), 0);
    },

    closeLogin() {
        document.getElementById('settings-login-overlay').classList.remove('active');
        this.pendingOpen = false;
    },

    async login(event) {
        event.preventDefault();
        const input = document.getElementById('settings-password');
        const button = document.getElementById('settings-login-submit');
        const errorEl = document.getElementById('settings-login-error');
        button.disabled = true;
        errorEl.textContent = '';
        try {
            await API.settingsLogin(input.value);
            document.getElementById('settings-login-overlay').classList.remove('active');
            input.value = '';
            if (this.pendingOpen || this.draftBeforeAuth) await this.loadAndShow(true);
        } catch (error) {
            errorEl.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    },

    async loadAndShow(afterRelogin = false) {
        const oldDraft = afterRelogin ? this.draftBeforeAuth : null;
        const data = await API.getRuntimeSettings();
        this.payload = data;
        this.original = structuredClone(data.config);
        this.secretState = {};
        this.render();
        const restoredDraft = Boolean(oldDraft && oldDraft.version === data.version);
        if (restoredDraft) this.restoreDraft(oldDraft);
        else if (oldDraft) Toast.info('配置已变化，请重新应用之前的修改');
        this.draftBeforeAuth = null;
        this.pendingOpen = false;
        document.getElementById('settings-modal-overlay').classList.add('active');
        this.setDirty(restoredDraft);
        lucide.createIcons();
    },

    close() {
        if (this.dirty && !confirm('有尚未保存的设置，确定关闭吗？')) return;
        document.getElementById('settings-modal-overlay').classList.remove('active');
        this.setDirty(false);
    },

    render() {
        const nav = document.getElementById('settings-group-nav');
        const body = document.getElementById('settings-form-body');
        nav.innerHTML = this.GROUPS.map((group, index) => `
            <button type="button" class="settings-nav-item${index === 0 ? ' active' : ''}"
                    data-settings-nav="${group.id}" onclick="SettingsManager.scrollToGroup('${group.id}')">
                <i data-lucide="${group.icon}" class="w-4 h-4"></i><span>${group.label}</span>
            </button>`).join('');
        body.innerHTML = this.GROUPS.map(group => this.renderGroup(group)).join('');
        body.querySelectorAll('input, select, textarea').forEach(el => {
            el.addEventListener('input', () => this.setDirty(true));
            el.addEventListener('change', () => this.setDirty(true));
        });
        this.bindScrollSpy();
    },

    renderGroup(group) {
        const values = this.payload.config[group.id] || {};
        const fields = (this.FIELDS[group.id] || []).map(field => this.renderField(group.id, field, values[field[0]])).join('');
        return `<section class="settings-section" id="settings-group-${group.id}" data-settings-group="${group.id}">
            <header class="settings-section-header">
                <div class="settings-section-icon"><i data-lucide="${group.icon}" class="w-5 h-5"></i></div>
                <div><h4>${group.label}</h4><p>${group.description}</p></div>
            </header>
            <div class="settings-fields">${fields}</div>
        </section>`;
    },

    renderField(group, definition, value) {
        const [name, label, type, options = {}] = definition;
        const path = `${group}.${name}`;
        if (type === 'secret') return this.renderSecret(path, label, value);
        const readonly = options.readonly ? ' readonly disabled' : '';
        const nullable = options.nullable ? ' data-nullable="true"' : '';
        const inputId = `setting-${group}-${name}`;
        let control;
        if (type === 'boolean') {
            control = `<label class="settings-toggle"><input id="${inputId}" data-path="${path}" type="checkbox"${value ? ' checked' : ''}${readonly}><span></span></label>`;
        } else if (type === 'select') {
            control = `<select id="${inputId}" data-path="${path}" class="form-select"${readonly}>${options.options.map(item => `<option value="${this.escape(item)}"${item === value ? ' selected' : ''}>${this.escape(item)}</option>`).join('')}</select>`;
        } else if (type === 'json') {
            const shown = value == null ? '' : JSON.stringify(value, null, 2);
            control = `<textarea id="${inputId}" data-path="${path}" data-type="json" class="form-textarea settings-json" rows="${options.rows || 3}"${nullable}${readonly}>${this.escape(shown)}</textarea>`;
        } else {
            const attrs = type === 'number'
                ? `type="number" data-type="number"${options.min != null ? ` min="${options.min}"` : ''}${options.step != null ? ` step="${options.step}"` : ' step="1"'}`
                : `${type === 'url' ? 'type="url"' : 'type="text"'} data-type="text"`;
            control = `<input id="${inputId}" data-path="${path}" class="form-input" ${attrs} value="${this.escape(value == null ? '' : value)}"${nullable}${readonly}>`;
        }
        return `<div class="settings-field${options.readonly ? ' is-readonly' : ''}">
            <label for="${inputId}">${label}${options.readonly ? '<span class="settings-readonly-badge">只读</span>' : ''}</label>
            <div class="settings-control-row">${control}${options.unit ? `<span class="settings-unit">${options.unit}</span>` : ''}</div>
        </div>`;
    },

    renderSecret(path, label, status) {
        this.secretState[path] = { action: 'keep' };
        const safePath = path.replace('.', '-');
        return `<div class="settings-field settings-secret-field" data-secret-path="${path}">
            <label>${label}</label>
            <div class="settings-secret-control">
                <span class="settings-secret-status ${status.configured ? 'configured' : ''}">
                    <i data-lucide="${status.configured ? 'shield-check' : 'shield-alert'}" class="w-4 h-4"></i>
                    ${status.configured ? '已配置' : '未配置'}
                </span>
                <input id="secret-${safePath}" class="form-input settings-secret-input" type="password" autocomplete="new-password" placeholder="输入新密钥" disabled>
                <button type="button" class="btn btn-secondary settings-secret-update" onclick="SettingsManager.beginSecretUpdate('${path}')">更新</button>
                <button type="button" class="btn btn-secondary settings-secret-cancel" onclick="SettingsManager.keepSecret('${path}')" hidden>取消</button>
                <button type="button" class="btn btn-danger settings-secret-clear" onclick="SettingsManager.clearSecret('${path}')">清除</button>
            </div>
        </div>`;
    },

    beginSecretUpdate(path) {
        const row = document.querySelector(`[data-secret-path="${path}"]`);
        const input = row.querySelector('.settings-secret-input');
        this.secretState[path] = { action: 'replace' };
        input.disabled = false;
        input.value = '';
        row.querySelector('.settings-secret-update').hidden = true;
        row.querySelector('.settings-secret-cancel').hidden = false;
        input.focus();
        this.setDirty(true);
    },

    keepSecret(path) {
        const row = document.querySelector(`[data-secret-path="${path}"]`);
        this.secretState[path] = { action: 'keep' };
        const input = row.querySelector('.settings-secret-input');
        input.value = '';
        input.disabled = true;
        row.querySelector('.settings-secret-update').hidden = false;
        row.querySelector('.settings-secret-cancel').hidden = true;
        this.recomputeDirty();
    },

    clearSecret(path) {
        const row = document.querySelector(`[data-secret-path="${path}"]`);
        this.secretState[path] = { action: 'clear' };
        const input = row.querySelector('.settings-secret-input');
        input.value = '';
        input.disabled = true;
        row.querySelector('.settings-secret-update').hidden = false;
        row.querySelector('.settings-secret-cancel').hidden = true;
        this.setDirty(true);
    },

    scrollToGroup(group) {
        document.getElementById(`settings-group-${group}`).scrollIntoView({ behavior: 'smooth', block: 'start' });
        this.setActiveGroup(group);
    },

    bindScrollSpy() {
        const body = document.getElementById('settings-form-body');
        if (this.scrollSpyHandler) body.removeEventListener('scroll', this.scrollSpyHandler);
        this.scrollSpyHandler = () => this.syncActiveGroupToScroll();
        body.addEventListener('scroll', this.scrollSpyHandler, { passive: true });
    },

    syncActiveGroupToScroll() {
        const body = document.getElementById('settings-form-body');
        const sections = [...body.querySelectorAll('[data-settings-group]')];
        if (!sections.length) return;

        let activeSection = sections[0];
        const activationLine = body.getBoundingClientRect().top + 24;
        for (const section of sections) {
            if (section.getBoundingClientRect().top > activationLine) break;
            activeSection = section;
        }
        if (body.scrollTop + body.clientHeight >= body.scrollHeight - 2) {
            activeSection = sections[sections.length - 1];
        }
        this.setActiveGroup(activeSection.dataset.settingsGroup);
    },

    setActiveGroup(group) {
        const items = [...document.querySelectorAll('.settings-nav-item')];
        items.forEach(item => item.classList.toggle('active', item.dataset.settingsNav === group));
        const activeItem = items.find(item => item.dataset.settingsNav === group);
        activeItem?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    },

    parseControl(control) {
        if (control.type === 'checkbox') return control.checked;
        const raw = control.value.trim();
        if (control.dataset.nullable === 'true' && raw === '') return null;
        if (control.dataset.type === 'number') {
            const value = Number(raw);
            if (!Number.isFinite(value)) throw new Error('请输入有效数字');
            return value;
        }
        if (control.dataset.type === 'json') {
            if (raw === '' && control.dataset.nullable === 'true') return null;
            try { return JSON.parse(raw); }
            catch (_) { throw new Error('JSON 格式不正确'); }
        }
        return raw;
    },

    collectDraft() {
        const values = structuredClone(this.original);
        document.querySelectorAll('#settings-form [data-path]').forEach(control => {
            if (control.disabled) return;
            const [group, field] = control.dataset.path.split('.');
            values[group][field] = this.parseControl(control);
        });
        const secrets = {};
        Object.entries(this.secretState).forEach(([path, state]) => {
            if (state.action === 'replace') {
                const input = document.querySelector(`[data-secret-path="${path}"] .settings-secret-input`);
                const value = input.value.trim();
                if (!value) throw new Error(`${path} 的新密钥不能为空`);
                secrets[path] = { action: 'replace', value };
            } else if (state.action === 'clear') secrets[path] = { action: 'clear' };
        });
        return { version: this.payload.version, values, secrets };
    },

    buildChanges(values) {
        const changes = {};
        this.GROUPS.forEach(group => {
            (this.FIELDS[group.id] || []).forEach(([field, , type, options = {}]) => {
                if (type === 'secret' || options.readonly) return;
                const oldValue = this.original[group.id][field];
                const newValue = values[group.id][field];
                if (JSON.stringify(oldValue) !== JSON.stringify(newValue)) {
                    if (!changes[group.id]) changes[group.id] = {};
                    changes[group.id][field] = newValue;
                }
            });
        });
        return changes;
    },

    async save(event) {
        event.preventDefault();
        let draft;
        try { draft = this.collectDraft(); }
        catch (error) { Toast.error(error.message); return; }
        const payload = {
            base_version: this.payload.version,
            changes: this.buildChanges(draft.values),
            secrets: draft.secrets,
        };
        if (!Object.keys(payload.changes).length && !Object.keys(payload.secrets).length) {
            this.setDirty(false);
            return;
        }
        const button = document.getElementById('settings-save-btn');
        button.disabled = true;
        try {
            const updated = await API.updateRuntimeSettings(payload);
            this.payload = updated;
            this.original = structuredClone(updated.config);
            this.secretState = {};
            this.render();
            this.setDirty(false);
            lucide.createIcons();
            Toast.success('配置已保存并即时生效');
        } catch (error) {
            if (error.status === 401) {
                this.draftBeforeAuth = draft;
                document.getElementById('settings-modal-overlay').classList.remove('active');
                this.pendingOpen = true;
                this.openLogin();
            } else if (error.status === 409) {
                this.conflictDraft = draft;
                Toast.error('配置已被其他管理员修改，正在加载最新配置并只重新应用本次修改');
                await this.reloadConflictDraft();
            } else {
                Toast.error('保存失败: ' + error.message);
            }
        } finally {
            button.disabled = !this.dirty;
        }
    },

    async reloadConflictDraft() {
        const draft = this.conflictDraft;
        if (!draft) return;
        const changes = this.buildChanges(draft.values);
        const latest = await API.getRuntimeSettings();
        this.payload = latest;
        this.original = structuredClone(latest.config);
        this.secretState = {};
        this.render();
        const rebasedValues = structuredClone(latest.config);
        Object.entries(changes).forEach(([group, fields]) => {
            Object.assign(rebasedValues[group], fields);
        });
        this.restoreDraft({ version: latest.version, values: rebasedValues, secrets: draft.secrets });
        this.conflictDraft = null;
        this.setDirty(true);
        lucide.createIcons();
    },

    restoreDraft(draft) {
        Object.entries(draft.values).forEach(([group, fields]) => {
            Object.entries(fields).forEach(([field, value]) => {
                const control = document.querySelector(`[data-path="${group}.${field}"]`);
                if (!control || control.disabled) return;
                if (control.type === 'checkbox') control.checked = Boolean(value);
                else if (control.dataset.type === 'json') control.value = value == null ? '' : JSON.stringify(value, null, 2);
                else control.value = value == null ? '' : value;
            });
        });
        Object.entries(draft.secrets || {}).forEach(([path, operation]) => {
            if (operation.action === 'clear') this.clearSecret(path);
            else if (operation.action === 'replace') {
                this.beginSecretUpdate(path);
                document.querySelector(`[data-secret-path="${path}"] .settings-secret-input`).value = operation.value;
            }
        });
        this.setDirty(true);
    },

    recomputeDirty() {
        try {
            const draft = this.collectDraft();
            const changed = Object.keys(this.buildChanges(draft.values)).length > 0 || Object.keys(draft.secrets).length > 0;
            this.setDirty(changed);
        } catch (_) {
            this.setDirty(true);
        }
    },

    setDirty(value) {
        this.dirty = value;
        document.getElementById('settings-dirty-dot')?.classList.toggle('active', value);
        const status = document.getElementById('settings-save-status');
        if (status) status.textContent = value ? '有未保存的修改' : '没有未保存的修改';
        const button = document.getElementById('settings-save-btn');
        if (button) button.disabled = !value;
    },

    async logout() {
        try { await API.settingsLogout(); }
        catch (error) { console.warn('设置退出失败', error); }
        document.getElementById('settings-modal-overlay').classList.remove('active');
        this.payload = null;
        this.original = {};
        this.secretState = {};
        this.setDirty(false);
        Toast.success('已退出系统设置');
    },
};
