/**
 * 文档类型入参（type_param）：清单管理、占位符插入、调试入参输入。
 *
 * 单独成文件而不是塞进 ruleConfig.js —— 后者已有 3200+ 行，字段与规则表单都在
 * 里面。这里只在 ruleConfig / doctype 留调用点。
 *
 * 占位符与 <field_result> 同族：chip 显示参数中文名，原始 <param>key</param>
 * 存 data-value，提交时按 data-value 取（复用 RuleConfig 的 keyword-tag 机制）。
 */
const TypeParams = {
    state: {
        typeId: null,
        params: [],
        loaded: false,
        editingKey: null,
    },

    // ── 数据 ────────────────────────────────────────────────

    /** 拉取并缓存当前类型的入参清单。切类型时会重拉。 */
    async load(typeId, { force = false } = {}) {
        const tid = typeId !== undefined ? typeId : API.getCurrentTypeId();
        if (!force && this.state.loaded && this.state.typeId === tid) {
            return this.state.params;
        }
        try {
            this.state.params = (await API.listTypeParams(tid)) || [];
        } catch (e) {
            // 入参是增量能力，拉不到不该让整个配置页挂掉
            console.warn('加载入参清单失败', e);
            this.state.params = [];
        }
        this.state.typeId = tid;
        this.state.loaded = true;
        return this.state.params;
    },

    /** 切换文档类型后作废缓存，下次 load 会重拉。 */
    invalidate() {
        this.state.loaded = false;
        this.state.params = [];
    },

    has() {
        return (this.state.params || []).length > 0;
    },

    nameOf(key) {
        const hit = (this.state.params || []).find(p => p.param_key === key);
        return hit ? hit.param_name : null;
    },

    // ── 展示 ────────────────────────────────────────────────

    /**
     * 把 <param>key</param> 换成「参数中文名」用于**展示**。
     * 未定义的 key 显示「key?」，与 displayFieldRefs 的处理一致。
     */
    display(text) {
        if (typeof text !== 'string' || text.indexOf('<param>') === -1) return text;
        return text.replace(/<param>(.+?)<\/param>/g, (m, key) => {
            const k = String(key).trim();
            const name = this.nameOf(k);
            return name ? `「${name}」` : `「${k}?」`;
        });
    },

    /** 插入按钮：与字段引用的 K 按钮同构，标 P。 */
    btnHtml(mode, targetId) {
        if (!this.has()) return '';
        return `<div class="insert-tag-wrap"><button type="button" class="insert-tag-btn" onclick="TypeParams.showDropdown(this,'${mode}','${targetId}')" title="引用该类型的入参">P</button></div>`;
    },

    /** 参数选择浮层。mode='tag' 插标签容器，'text' 插光标处。 */
    showDropdown(btnEl, mode, targetId) {
        RuleConfig.closeInsertTagDropdown();

        const dropdown = document.createElement('div');
        dropdown.className = 'insert-tag-dropdown';
        dropdown.id = '_insert-tag-dropdown';

        const items = this.state.params || [];
        if (items.length === 0) {
            dropdown.innerHTML = '<div class="dropdown-empty">该类型尚未定义入参</div>';
        } else {
            const itemsWrap = document.createElement('div');
            itemsWrap.className = 'dropdown-items';
            dropdown.appendChild(itemsWrap);

            items.forEach(p => {
                const item = document.createElement('div');
                item.className = 'dropdown-item';
                item.textContent = `${p.param_name} (${p.param_key})`;
                item.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (mode === 'tag') {
                        RuleConfig.addKeywordTag(targetId, `<param>${p.param_key}</param>`);
                    } else {
                        RuleConfig.insertTagAtCursor(targetId, 'param', p.param_key);
                    }
                    RuleConfig.closeInsertTagDropdown();
                });
                itemsWrap.appendChild(item);
            });
        }

        const wrap = btnEl.closest('.insert-tag-wrap');
        if (wrap) wrap.appendChild(dropdown);

        const closeHandler = (e) => {
            if (!dropdown.contains(e.target) && e.target !== btnEl) {
                RuleConfig.closeInsertTagDropdown();
            }
        };
        document.addEventListener('click', closeHandler, true);
        dropdown._closeHandler = closeHandler;
    },

    // ── 管理区 ──────────────────────────────────────────────

    /** 在容器里渲染入参清单 + 新增表单入口。 */
    async renderManageList(containerId, typeId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        await this.load(typeId, { force: true });

        const rows = (this.state.params || []).map(p => `
            <div class="type-param-row">
                <div class="type-param-main">
                    <span class="type-param-name">${Utils.escapeHtml(p.param_name)}</span>
                    <code class="type-param-key">${Utils.escapeHtml(p.param_key)}</code>
                    ${p.required ? '<span class="type-param-required">必填</span>' : ''}
                </div>
                <div class="type-param-meta">
                    ${p.default_value ? `默认值：${Utils.escapeHtml(p.default_value)}` : '<span class="type-param-muted">无默认值</span>'}
                    ${p.description ? ` · ${Utils.escapeHtml(p.description)}` : ''}
                </div>
                <div class="type-param-actions">
                    <button type="button" class="btn btn-secondary btn-sm" onclick="TypeParams.openForm('${Utils.escapeHtml(p.param_key)}')">编辑</button>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="TypeParams.remove('${Utils.escapeHtml(p.param_key)}')">删除</button>
                </div>
            </div>
        `).join('');

        container.innerHTML = `
            <div class="type-param-list">
                ${rows || '<div class="type-param-empty">尚未定义入参。定义后可在字段与规则配置里用 &lt;param&gt; 引用，提交解析时由调用方传值。</div>'}
            </div>
            <button type="button" class="btn btn-secondary" onclick="TypeParams.openForm(null)">+ 新增入参</button>
            <div id="type-param-form-wrap"></div>
        `;
    },

    openForm(paramKey) {
        this.state.editingKey = paramKey;
        const existing = paramKey
            ? (this.state.params || []).find(p => p.param_key === paramKey)
            : null;
        const wrap = document.getElementById('type-param-form-wrap');
        if (!wrap) return;

        wrap.innerHTML = `
            <div class="type-param-form">
                <div class="form-group">
                    <label class="form-label">参数标识（占位符里写的就是它）</label>
                    <input type="text" class="form-input" id="tp-key"
                           value="${Utils.escapeHtml(existing ? existing.param_key : '')}"
                           ${existing ? 'disabled' : ''}
                           placeholder="仅限字母、数字、下划线，如 current_date">
                </div>
                <div class="form-group">
                    <label class="form-label">中文名</label>
                    <input type="text" class="form-input" id="tp-name"
                           value="${Utils.escapeHtml(existing ? existing.param_name : '')}"
                           placeholder="如 当前日期">
                </div>
                <div class="form-group">
                    <label class="form-label">说明（告诉上游该传什么）</label>
                    <input type="text" class="form-input" id="tp-desc"
                           value="${Utils.escapeHtml(existing && existing.description ? existing.description : '')}">
                </div>
                <div class="form-group">
                    <label class="form-label">默认值（调用方不传时使用）</label>
                    <input type="text" class="form-input" id="tp-default"
                           value="${Utils.escapeHtml(existing && existing.default_value ? existing.default_value : '')}">
                </div>
                <div class="form-group">
                    <label class="form-checkbox">
                        <input type="checkbox" id="tp-required" ${existing && existing.required ? 'checked' : ''}>
                        必填（未传且无默认值时直接拒绝提交）
                    </label>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-primary" onclick="TypeParams.save()">保存</button>
                    <button type="button" class="btn btn-secondary" onclick="TypeParams.closeForm()">取消</button>
                </div>
            </div>
        `;
    },

    closeForm() {
        const wrap = document.getElementById('type-param-form-wrap');
        if (wrap) wrap.innerHTML = '';
        this.state.editingKey = null;
    },

    async save() {
        const key = (this.state.editingKey
            || (document.getElementById('tp-key') || {}).value || '').trim();
        const name = ((document.getElementById('tp-name') || {}).value || '').trim();
        if (!key) { Toast.error('参数标识不能为空'); return; }
        if (!/^[A-Za-z0-9_]+$/.test(key)) {
            Toast.error('参数标识只能包含字母、数字和下划线');
            return;
        }
        if (!name) { Toast.error('中文名不能为空'); return; }

        try {
            await API.upsertTypeParam({
                param_key: key,
                param_name: name,
                description: ((document.getElementById('tp-desc') || {}).value || '').trim() || null,
                default_value: ((document.getElementById('tp-default') || {}).value || '') || null,
                required: (document.getElementById('tp-required') || {}).checked ? 1 : 0,
            });
            Toast.success('入参已保存');
            this.closeForm();
            await this.renderManageList('type-param-manage', this.state.typeId);
        } catch (e) {
            Toast.error(e.message || '保存失败');
        }
    },

    async remove(paramKey) {
        try {
            await API.deleteTypeParam(paramKey, false);
            Toast.success('入参已删除');
            await this.renderManageList('type-param-manage', this.state.typeId);
        } catch (e) {
            // 409 = 仍被字段/规则引用，把后端列出的引用方原样告诉用户再让其决定
            const msg = e.message || '删除失败';
            if (!confirm(`${msg}\n\n仍要删除吗？`)) return;
            try {
                await API.deleteTypeParam(paramKey, true);
                Toast.success('入参已强制删除');
                await this.renderManageList('type-param-manage', this.state.typeId);
            } catch (e2) {
                Toast.error(e2.message || '删除失败');
            }
        }
    },

    // ── 调试面板 ────────────────────────────────────────────

    /**
     * 调试面板的入参输入区：每个参数一个输入框，预填 default_value。
     * 后端对调试接口同样做 required 校验（避免「调试能过、正式跑不了」），
     * 所以这里必须让人能填。
     */
    async renderDebugInputs(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        await this.load();

        if (!this.has()) {
            container.innerHTML = '';
            return;
        }

        const rows = this.state.params.map(p => `
            <div class="debug-param-item">
                <label class="debug-param-label" title="${Utils.escapeHtml(p.description || '')}">
                    ${Utils.escapeHtml(p.param_name)}
                    ${p.required ? '<span class="debug-param-required">*</span>' : ''}
                </label>
                <input type="text" class="form-input debug-param-input"
                       data-param-key="${Utils.escapeHtml(p.param_key)}"
                       value="${Utils.escapeHtml(p.default_value || '')}"
                       placeholder="${Utils.escapeHtml(p.param_key)}">
            </div>
        `).join('');

        container.innerHTML = `
            <div class="debug-param-block">
                <div class="debug-param-title">入参（正式提交时由调用方传入）</div>
                <div class="debug-param-grid">${rows}</div>
            </div>
        `;
    },

    /** 收集调试面板里填的入参，形如 {param_key: value}。 */
    collectDebugValues() {
        const values = {};
        document.querySelectorAll('.debug-param-input').forEach(input => {
            const key = input.dataset.paramKey;
            if (key) values[key] = input.value;
        });
        return values;
    },
};
