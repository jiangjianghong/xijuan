/**
 * 规则配置模块
 */

const RuleConfig = {
    state: {
        currentTab: 'fields',
        fields: [],
        rules: [],
        editingField: null,
        editingRule: null,
        modalType: null, // 'field' | 'rule'
        loaded: { fields: false, rules: false },
        debugMode: false,
        debugTestRunning: false,
        ruleExtractionResults: [],
        // 当前字段表单是否为「进阶字段」（可引用普通字段的提取结果）
        formIsAdvanced: false,
    },

    els: {},

    // text / table 抽取默认提示词（新建字段时预填，方便用户在此基础上改写）。
    // 用户提示词须包含 <search_result>...</search_result> 占位符（use_llm=1 时后端强制校验）。
    EXTRACT_DEFAULTS: {
        SYSTEM_PROMPT: '你是一个抽取以及逻辑分析专家，你擅长对检索到的文本进行分析提取',
        USER_PROMPT: '你需要在检索到的内容中提取xxx。\n\n检索到的内容：<search_result>检索标签</search_result>',
    },

    // VL 默认提示词（与后端 service/vl_service/_defaults.py 严格保持一致）。
    VL_DEFAULTS: {
        EXTRACT_PROMPT: '请基于以上图片提取相关信息。\n请只返回 JSON 格式：{"value": "提取到的内容（多个用逗号分隔）", "reason": "简要说明依据，例如在哪一页或哪个位置看到"}\n如果未找到，返回：{"value": "", "reason": "未找到"}',
    },

    // 提示词默认模板缓存，来源为后端 GET /extraction/match-prompt-defaults。
    // 不在前端硬编码副本：副本落后于后端时，用户一保存就会把旧模板固化进库。
    PROMPT_DEFAULTS: null,

    async loadPromptDefaults() {
        if (this.PROMPT_DEFAULTS) return this.PROMPT_DEFAULTS;
        try {
            const resp = await fetch('/extraction/match-prompt-defaults');
            const body = await resp.json();
            this.PROMPT_DEFAULTS = body.data || {};
        } catch (e) {
            console.error('拉取默认提示词模板失败', e);
            this.PROMPT_DEFAULTS = {};
        }
        return this.PROMPT_DEFAULTS;
    },

    // 文本框为空时填入系统默认模板供用户直接编辑；同时渲染固定输出段说明。
    async fillMatchPromptDefaults(kind) {
        const defaults = await this.loadPromptDefaults();
        const ta = document.getElementById(`fm-${kind}-match-prompt`);
        if (ta && !ta.value.trim() && defaults[kind]) ta.value = defaults[kind];
        const suffix = document.getElementById(`fm-${kind}-match-prompt-suffix`);
        if (suffix) suffix.textContent = (defaults.output_instruction || '').trim();
    },

    async resetMatchPrompt(elementId, kind) {
        const defaults = await this.loadPromptDefaults();
        const ta = document.getElementById(elementId);
        if (ta && defaults[kind]) ta.value = defaults[kind];
    },

    // 「LLM 匹配高级设置」折叠面板。默认收起——绝大多数字段用系统默认模板即可，
    // 展开后才拉取默认模板（收起状态无需发这个请求）。
    async toggleMatchPromptPanel(kind) {
        const body = document.getElementById(`fm-${kind}-match-prompt-body`);
        if (!body) return;
        const opening = body.style.display === 'none';
        body.style.display = opening ? '' : 'none';
        this.setMatchPromptToggleLabel(kind, opening);
        if (opening) await this.fillMatchPromptDefaults(kind);
    },

    setMatchPromptToggleLabel(kind, expanded) {
        const btn = document.getElementById(`fm-${kind}-match-prompt-toggle`);
        if (btn) btn.textContent = expanded ? '▾ LLM 匹配高级设置' : '▸ LLM 匹配高级设置';
    },

    collapseMatchPromptPanel(kind) {
        const body = document.getElementById(`fm-${kind}-match-prompt-body`);
        if (body) body.style.display = 'none';
        this.setMatchPromptToggleLabel(kind, false);
    },

    onTableMatchTypeChange(value) {
        const group = document.getElementById('fm-table-match-prompt-group');
        if (group) group.style.display = (value === 'llm') ? '' : 'none';
        if (value !== 'llm') {
            // 切走 LLM 时收回面板，避免下次切回来仍是展开态
            this.collapseMatchPromptPanel('table');
            return;
        }
        // 已配置过模板的字段初始即展开，此时需补渲染固定输出段说明
        const body = document.getElementById('fm-table-match-prompt-body');
        if (body && body.style.display !== 'none') this.fillMatchPromptDefaults('table');
    },

    init() {
        this.cacheElements();
        this.bindPassiveRefresh();
    },

    // 被动探测：切回浏览器 tab / 窗口重新获得焦点时立刻探一次，
    // 不必干等下一个轮询周期。checkConfigVersion 内部有 watch.active 守卫，
    // 因此不在配置页时这两个监听不会产生任何请求。
    bindPassiveRefresh() {
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) this.checkConfigVersion();
        });
        window.addEventListener('focus', () => this.checkConfigVersion());
    },

    cacheElements() {
        this.els = {
            fieldListBody: document.getElementById('field-list-body'),
            ruleListBody: document.getElementById('rule-list-body'),
            fieldEmpty: document.getElementById('field-empty'),
            ruleEmpty: document.getElementById('rule-empty'),
            modalOverlay: document.getElementById('rule-modal-overlay'),
            modalTitle: document.getElementById('rule-modal-title'),
            modalBody: document.getElementById('rule-modal-body'),
            sectionFields: document.getElementById('section-fields'),
            sectionAdvancedFields: document.getElementById('section-advanced-fields'),
            sectionRules: document.getElementById('section-rules'),
            sectionParams: document.getElementById('section-params'),
            debugBtn: document.getElementById('debug-field-btn'),
        };
    },

    // ─────────────────────────────────────────────────────────
    // Tab 切换
    // ─────────────────────────────────────────────────────────

    switchTab(tab) {
        this.state.currentTab = tab;

        document.querySelectorAll('.sub-tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.rtab === tab);
        });

        // 进阶字段是独立卡片，与普通字段区同属「字段提取配置」标签页，一起显隐
        this.els.sectionFields.classList.toggle('active', tab === 'fields');
        this.els.sectionAdvancedFields.classList.toggle('active', tab === 'fields');
        this.els.sectionRules.classList.toggle('active', tab === 'rules');
        if (this.els.sectionParams) {
            this.els.sectionParams.classList.toggle('active', tab === 'params');
        }

        if (tab === 'fields' && !this.state.loaded.fields) {
            this.loadFields();
        } else if (tab === 'rules' && !this.state.loaded.rules) {
            this.loadRules();
        } else if (tab === 'params') {
            // 每次进入都重拉：入参改动会影响字段/规则表单里的 P 按钮，不做缓存
            TypeParams.renderManageList('type-param-manage');
        }
    },

    // ─────────────────────────────────────────────────────────
    // 数据加载
    // ─────────────────────────────────────────────────────────

    async loadFields() {
        try {
            // 入参清单要先于字段列表就绪：表单渲染时按「该类型有没有入参」决定
            // 是否显示 P 按钮，晚一步会让首次打开的表单少一个按钮
            await TypeParams.load(undefined, { force: true });
            this.state.fields = await API.getExtractionFields();
            this.state.loaded.fields = true;
            this.renderFieldList();
            // 自己触发的加载一并对齐基线，避免把自己的保存误报成「他人更新」。
            // 故意不 await：列表该立刻渲染，基线晚一个 RTT 无影响。
            if (this.watch.active) this.syncBaseline();
        } catch (error) {
            Toast.error('加载字段配置失败: ' + error.message);
        }
    },

    async loadRules() {
        try {
            // 规则表达式同样可引用入参，P 按钮依赖清单先就绪
            await TypeParams.load();
            this.state.rules = await API.getAnalysisRules();
            this.state.loaded.rules = true;
            this.renderRuleList();
            if (this.watch.active) this.syncBaseline();
        } catch (error) {
            Toast.error('加载规则配置失败: ' + error.message);
        }
    },

    // ─────────────────────────────────────────────────────────
    // 列表渲染
    // ─────────────────────────────────────────────────────────

    renderFieldList() {
        const all = this.state.fields || [];
        const basic = all.filter(f => !f.is_advanced);
        const advanced = all.filter(f => f.is_advanced);

        const sourceTypeText = { table: '表格', text: '文本', vl: 'VL' };
        const rowHtml = (f) => {
            const sourceTypeCell = f.source_type === 'vl'
                ? `VL · ${Utils.escapeHtml(f.vl_method || '')}`
                : (sourceTypeText[f.source_type] || f.source_type);
            // 依赖列表按字段中文名展示（找不到的退回 ID）
            const nameById = {};
            all.forEach(x => { nameById[x.field_id] = x.field_name; });
            const dependCell = (f.depend_fields && f.depend_fields.length)
                ? `<div class="form-hint">引用: ${Utils.escapeHtml(f.depend_fields.map(d => nameById[d] || d).join('、'))}</div>`
                : '';
            return `
                <tr class="${f.enabled ? '' : 'row-disabled'}">
                    <td>${Utils.escapeHtml(f.field_id)}</td>
                    <td>${Utils.escapeHtml(f.field_name)}${dependCell}</td>
                    <td>${sourceTypeCell}</td>
                    <td>${f.priority}</td>
                    <td>
                        <label class="toggle-switch" onclick="event.stopPropagation()">
                            <input type="checkbox" ${f.enabled ? 'checked' : ''} onchange="RuleConfig.toggleFieldEnabled('${f.field_id}', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </td>
                    <td>
                        <div class="action-btns">
                            <button class="action-btn" onclick="RuleConfig.openFieldForm(${JSON.stringify(f).replace(/"/g, '&quot;')})" title="编辑">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                </svg>
                            </button>
                            <button class="action-btn delete" onclick="RuleConfig.deleteField('${f.field_id}')" title="删除">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <polyline points="3 6 5 6 21 6"></polyline>
                                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                </svg>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        };

        // 普通字段
        this.els.fieldListBody.innerHTML = basic.map(rowHtml).join('');
        this.els.fieldEmpty.style.display = basic.length ? 'none' : 'block';

        // 进阶字段
        const advBody = document.getElementById('advanced-field-list-body');
        const advEmpty = document.getElementById('advanced-field-empty');
        if (advBody) advBody.innerHTML = advanced.map(rowHtml).join('');
        if (advEmpty) advEmpty.style.display = advanced.length ? 'none' : 'block';
    },

    renderRuleList() {
        const rules = this.state.rules;
        if (rules.length === 0) {
            this.els.ruleListBody.innerHTML = '';
            this.els.ruleEmpty.style.display = 'block';
            return;
        }
        this.els.ruleEmpty.style.display = 'none';

        const ruleTypeText = { judge: '判断', calc: '计算', custom: '自定义' };
        let html = '';
        rules.forEach(r => {
            html += `
                <tr class="${r.enabled ? '' : 'row-disabled'}">
                    <td>${Utils.escapeHtml(r.rule_id)}</td>
                    <td>${Utils.escapeHtml(r.rule_name)}</td>
                    <td>${ruleTypeText[r.rule_type] || r.rule_type}</td>
                    <td>${r.priority}</td>
                    <td>
                        <label class="toggle-switch" onclick="event.stopPropagation()">
                            <input type="checkbox" ${r.enabled ? 'checked' : ''} onchange="RuleConfig.toggleRuleEnabled('${r.rule_id}', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </td>
                    <td>
                        <div class="action-btns">
                            <button class="action-btn" onclick="RuleConfig.openRuleForm(${JSON.stringify(r).replace(/"/g, '&quot;')})" title="编辑">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                </svg>
                            </button>
                            <button class="action-btn delete" onclick="RuleConfig.deleteRule('${r.rule_id}')" title="删除">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <polyline points="3 6 5 6 21 6"></polyline>
                                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                </svg>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        });
        this.els.ruleListBody.innerHTML = html;
    },

    // ─────────────────────────────────────────────────────────
    // 配置变更探测（多人协作：他人改了配置，本页自动感知）
    //
    // 只在配置页可见时轮询探针接口 GET /doctype/{type_id}/config_version，
    // 版本快照变了才拉全量。表单开着时绝不自动重载——那会吞掉用户填了半天的
    // 内容，比看到旧数据糟得多，只挂一条横幅让用户自己决定。
    // ─────────────────────────────────────────────────────────

    POLL_INTERVAL_MS: 8000,

    watch: {
        active: false,       // 是否停在配置页
        timer: null,
        baseline: null,      // 上次确认过的版本快照（JSON 字符串）；null=尚未对齐
        pendingStale: false, // 表单开着期间探到的变更，关表单后补刷
    },

    activate() {
        this.watch.active = true;
        // 进页面先对齐基线再开轮询，否则首次探测必然「发现变化」而误报
        this.syncBaseline();
        this.startPolling();
    },

    deactivate() {
        this.watch.active = false;
        this.stopPolling();
    },

    startPolling() {
        this.stopPolling();
        this.watch.timer = setInterval(() => this.checkConfigVersion(), this.POLL_INTERVAL_MS);
    },

    stopPolling() {
        if (this.watch.timer) {
            clearInterval(this.watch.timer);
            this.watch.timer = null;
        }
    },

    // 把当前版本记为基线但不触发任何刷新。
    // 用于：进入配置页、切换文档类型、以及自己保存之后
    // （自己的保存也会推高 updated_at，不对齐就会把自己的改动误报成「他人更新」）
    async syncBaseline() {
        try {
            this.watch.baseline = JSON.stringify(await API.getConfigVersion());
        } catch (e) {
            // 探针失败就放弃本次对齐，下一轮再来；不弹提示以免网络抖动时反复打扰
            this.watch.baseline = null;
        }
    },

    async checkConfigVersion() {
        if (!this.watch.active) return;
        if (document.hidden) return;   // 浏览器 tab 不可见时不打扰后端

        let snapshot;
        try {
            snapshot = JSON.stringify(await API.getConfigVersion());
        } catch (e) {
            return;   // 网络抖动静默跳过，等下一轮
        }

        if (this.watch.baseline === null) {
            this.watch.baseline = snapshot;   // 之前没对齐上，这次补齐，不当作变更
            return;
        }
        if (snapshot === this.watch.baseline) return;

        this.watch.baseline = snapshot;
        this.onConfigChanged();
    },

    onConfigChanged() {
        if (this.state.modalType) {
            // 表单开着：一个字都不动，只挂横幅
            this.watch.pendingStale = true;
            this.showStaleBanner();
            return;
        }
        this.reloadCurrentTab().then(() => {
            Toast.info('配置已由他人更新，列表已刷新');
        });
    },

    // 重载当前可见标签页；另一个标签标记为未加载，等切过去时走既有懒加载
    async reloadCurrentTab() {
        if (this.state.currentTab === 'rules') {
            this.state.loaded.fields = false;
            await this.loadRules();
        } else {
            this.state.loaded.rules = false;
            await this.loadFields();
        }
    },

    showStaleBanner() {
        if (document.getElementById('config-stale-banner')) return;
        const header = this.els.modalOverlay.querySelector('.rule-modal-header');
        if (!header) return;
        const banner = document.createElement('div');
        banner.id = 'config-stale-banner';
        banner.className = 'config-stale-banner';
        banner.innerHTML = '<span>⚠️ 他人刚修改了本类型的配置，你保存后会覆盖对方的改动。</span>'
            + '<button class="btn btn-ghost" onclick="RuleConfig.discardAndReload()">放弃我的修改并加载最新</button>';
        header.insertAdjacentElement('afterend', banner);
    },

    hideStaleBanner() {
        const el = document.getElementById('config-stale-banner');
        if (el) el.remove();
    },

    async discardAndReload() {
        this.closeModal();   // closeModal 内部会清 editing* / modalType 并摘掉横幅
        await this.reloadCurrentTab();
        Toast.info('已加载最新配置');
    },

    async manualRefresh() {
        await this.reloadCurrentTab();
        await this.syncBaseline();
        Toast.success('已刷新');
    },

    // ─────────────────────────────────────────────────────────
    // 弹窗管理
    // ─────────────────────────────────────────────────────────

    showModal() {
        this.els.modalOverlay.classList.add('active');
    },

    closeModal() {
        if (this.state.debugMode) {
            this.exitDebugMode();
        }
        this.els.modalOverlay.classList.remove('active');
        this.els.modalOverlay.classList.remove('debug-overlay');
        if (this.els.debugBtn) this.els.debugBtn.style.display = 'none';
        this.state.editingField = null;
        this.state.editingRule = null;
        this.state.modalType = null;
        this.hideStaleBanner();
        // 表单开着期间探到的他人改动，关表单后补做刷新
        if (this.watch.pendingStale) {
            this.watch.pendingStale = false;
            this.reloadCurrentTab();
        }
    },

    // ─────────────────────────────────────────────────────────
    // Tag 标签式输入
    // ─────────────────────────────────────────────────────────

    /**
     * 把文本里的 <field_result>字段ID</field_result> 换成「字段中文名」用于**展示**。
     * 仅影响界面显示，实际提交的值始终是原始占位符（存在 data-value 里）。
     */
    displayFieldRefs(text) {
        if (typeof text !== 'string' || text.indexOf('<field_result>') === -1) return text;
        const byId = {};
        (this.state.fields || []).forEach(f => { byId[f.field_id] = f.field_name; });
        return text.replace(/<field_result>(.+?)<\/field_result>/g, (m, fid) => {
            const key = String(fid).trim();
            return byId[key] ? `「${byId[key]}」` : `「${key}?」`;
        });
    },

    /**
     * chip 是否承载占位符（字段引用或入参引用）——决定是否显示中文名而非原文。
     */
    isRefValue(v) {
        return typeof v === 'string'
            && (v.indexOf('<field_result>') !== -1 || v.indexOf('<param>') !== -1);
    },

    /** 占位符 → 中文名，用于 chip 展示；提交时仍取 data-value 里的原文。 */
    displayRefs(text) {
        let out = this.displayFieldRefs(text);
        if (typeof TypeParams !== 'undefined') out = TypeParams.display(out);
        return out;
    },

    buildKeywordTagsHtml(id, label, values, placeholder, allowRef) {
        values = values || [];
        let tagsHtml = '';
        for (const v of values) {
            const isRef = this.isRefValue(v);
            const shown = isRef ? this.displayRefs(v) : v;
            tagsHtml += `<span class="keyword-tag${isRef ? ' field-ref-tag' : ''}" data-value="${Utils.escapeHtml(v)}">${Utils.escapeHtml(shown)}<button type="button" class="keyword-tag-remove" onclick="RuleConfig.removeKeywordTag(this)">&times;</button></span>`;
        }
        // 关键词可引用普通字段的提取结果（进阶字段）与该类型的入参。
        // 不看 allowRef：K 的「仅进阶字段」门禁在 fieldRefBtnHtml 里，而入参对
        // 普通字段同样可用（它来自外部传入，不依赖别的字段先跑完）。
        const btns = this.refBtnsHtml('tag', id);
        const labelHtml = btns
            ? `<div class="form-label-row"><label class="form-label">${Utils.escapeHtml(label)}</label>${btns}</div>`
            : `<label class="form-label">${Utils.escapeHtml(label)}</label>`;
        return `
            <div class="form-group">
                ${labelHtml}
                <div class="keyword-tags-container" id="${id}">
                    <div class="keyword-tags-list">${tagsHtml}</div>
                    <div class="keyword-input-row">
                        <input type="text" placeholder="${Utils.escapeHtml(placeholder || '输入后按回车或点击添加')}" onkeydown="if(event.key==='Enter'){event.preventDefault();RuleConfig.addKeywordTag('${id}',this.value);this.value='';}">
                        <button type="button" onclick="RuleConfig.addKeywordTag('${id}',this.previousElementSibling.value);this.previousElementSibling.value='';">+ 添加</button>
                    </div>
                </div>
            </div>
        `;
    },

    addKeywordTag(containerId, value) {
        value = (value || '').trim();
        if (!value) return;
        const container = document.getElementById(containerId);
        if (!container) return;
        const list = container.querySelector('.keyword-tags-list');
        // Avoid duplicates
        const existing = this.getKeywordTags(containerId);
        if (existing.includes(value)) return;
        const isRef = this.isRefValue(value);
        const span = document.createElement('span');
        span.className = isRef ? 'keyword-tag field-ref-tag' : 'keyword-tag';
        // 原始值放 data-value，界面只显示中文名，提交时按 data-value 取
        span.dataset.value = value;
        const shown = isRef ? this.displayRefs(value) : value;
        span.innerHTML = `${Utils.escapeHtml(shown)}<button type="button" class="keyword-tag-remove" onclick="RuleConfig.removeKeywordTag(this)">&times;</button>`;
        list.appendChild(span);
    },

    removeKeywordTag(button) {
        const tag = button.parentElement;
        if (tag) tag.remove();
    },

    getKeywordTags(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return [];
        const tags = container.querySelectorAll('.keyword-tag');
        const values = [];
        tags.forEach(tag => {
            // 字段引用 chip 显示的是中文名，真实值在 data-value 里
            if (tag.dataset && tag.dataset.value) {
                values.push(tag.dataset.value);
                return;
            }
            // Get text content excluding the remove button
            const clone = tag.cloneNode(true);
            const btn = clone.querySelector('.keyword-tag-remove');
            if (btn) btn.remove();
            const text = clone.textContent.trim();
            if (text) values.push(text);
        });
        return values;
    },

    // ─────────────────────────────────────────────────────────
    // 占位符快捷插入
    // ─────────────────────────────────────────────────────────

    showInsertTagDropdown(textareaId, tagType, btnEl) {
        // 先关闭已有 dropdown
        this.closeInsertTagDropdown();

        // 收集标签列表；emptyHint 说明"为什么没有"以及该去哪儿填
        let labels = [];
        let emptyHint = '暂无可用标签';
        if (textareaId === 'fm-table-extract-prompt') {
            const val = (document.getElementById('fm-table-name-pattern') || {}).value;
            // 后端 label = table_name_pattern or "表格"，表名留空时占位符标签就是"表格"
            labels = (val && val.trim()) ? [val.trim()] : ['表格'];
        } else if (textareaId === 'fm-text-extract-prompt') {
            const searchTypeEl = document.getElementById('fm-search-type');
            const searchType = searchTypeEl ? searchTypeEl.value : '';
            if (searchType === 'page') {
                labels = ['page_content'];
            } else if (searchType === 'section') {
                const val = (document.getElementById('fm-sc-section-pattern') || {}).value;
                if (val && val.trim()) labels = [val.trim()];
                emptyHint = '请先填写上方「章节模式」';
            } else if (searchType === 'vector_db') {
                // 每行一个查询词 = 一个独立占位符标签（多路检索）
                const val = (document.getElementById('fm-sc-query-text') || {}).value;
                labels = (val || '').split('\n').map(s => s.trim()).filter(Boolean);
                emptyHint = '请先填写上方「查询文本」';
            } else {
                labels = this.getKeywordTags('fm-sc-keywords');
                emptyHint = '请先在上方「关键词」里添加关键词（输入后按回车或点「+ 添加」）';
            }
        } else if (textareaId === 'fm-expression' || textareaId === 'fm-expression-calc' || textareaId === 'fm-ws-query' || textareaId === 'fm-custom-expression') {
            labels = this.getDependFields();
        }

        // 创建 dropdown
        const dropdown = document.createElement('div');
        dropdown.className = 'insert-tag-dropdown';
        dropdown.id = '_insert-tag-dropdown';

        if (labels.length === 0) {
            dropdown.innerHTML = `<div class="dropdown-empty">${Utils.escapeHtml(emptyHint)}</div>`;
        } else {
            labels.forEach(label => {
                const item = document.createElement('div');
                item.className = 'dropdown-item';
                // 含字段引用的标签显示成中文名，插入的仍是原始占位符
                item.textContent = this.displayFieldRefs(label);
                item.addEventListener('click', (e) => {
                    e.stopPropagation();
                    RuleConfig.insertTagAtCursor(textareaId, tagType, label);
                });
                dropdown.appendChild(item);
            });
        }

        // 插入到按钮的父元素（insert-tag-wrap）
        const wrap = btnEl.closest('.insert-tag-wrap');
        if (wrap) wrap.appendChild(dropdown);

        // 点击外部关闭
        const closeHandler = (e) => {
            if (!dropdown.contains(e.target) && e.target !== btnEl) {
                RuleConfig.closeInsertTagDropdown();
            }
        };
        document.addEventListener('click', closeHandler, true);
        dropdown._closeHandler = closeHandler;
    },

    insertTagAtCursor(textareaId, tagType, label) {
        const textarea = document.getElementById(textareaId);
        if (!textarea) return;

        const text = '<' + tagType + '>' + label + '</' + tagType + '>';
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const before = textarea.value.substring(0, start);
        const after = textarea.value.substring(end);

        textarea.value = before + text + after;
        textarea.focus();

        const cursorPos = start + text.length;
        textarea.selectionStart = cursorPos;
        textarea.selectionEnd = cursorPos;

        this.closeInsertTagDropdown();
    },

    closeInsertTagDropdown() {
        const dropdown = document.getElementById('_insert-tag-dropdown');
        if (dropdown) {
            if (dropdown._closeHandler) {
                document.removeEventListener('click', dropdown._closeHandler, true);
            }
            dropdown.remove();
        }
    },

    // ─────────────────────────────────────────────────────────
    // 依赖字段下拉多选
    // ─────────────────────────────────────────────────────────

    buildDependFieldTag(fid, name) {
        const missing = !name;
        const title = missing ? `${fid}（字段不存在或已删除）` : fid;
        return `<span class="keyword-tag${missing ? ' depend-field-missing' : ''}" data-fid="${Utils.escapeHtml(fid)}" title="${Utils.escapeHtml(title)}">${Utils.escapeHtml(name || fid)}<button type="button" class="keyword-tag-remove" onclick="RuleConfig.removeKeywordTag(this)">&times;</button></span>`;
    },

    getDependFields() {
        const list = document.getElementById('fm-depend-fields-list');
        if (!list) return [];
        return Array.from(list.querySelectorAll('.keyword-tag[data-fid]')).map(t => t.dataset.fid);
    },

    addDependFieldTag(fid, name) {
        const list = document.getElementById('fm-depend-fields-list');
        if (!list || this.getDependFields().includes(fid)) return;
        const temp = document.createElement('div');
        temp.innerHTML = this.buildDependFieldTag(fid, name);
        list.appendChild(temp.firstChild);
    },

    showDependFieldDropdown(btnEl) {
        this.closeInsertTagDropdown();

        const selected = new Set(this.getDependFields());
        const available = (this.state.fields || []).filter(f => !selected.has(f.field_id));

        const dropdown = document.createElement('div');
        dropdown.className = 'insert-tag-dropdown';
        dropdown.id = '_insert-tag-dropdown';

        if (available.length === 0) {
            dropdown.innerHTML = '<div class="dropdown-empty">暂无可选字段</div>';
        } else {
            // 搜索框：字段较多时可按名称 / ID 模糊过滤
            const search = document.createElement('input');
            search.type = 'text';
            search.className = 'dropdown-search';
            search.placeholder = '搜索字段名称 / ID…';
            dropdown.appendChild(search);

            const itemsWrap = document.createElement('div');
            itemsWrap.className = 'dropdown-items';
            dropdown.appendChild(itemsWrap);

            const emptyHint = document.createElement('div');
            emptyHint.className = 'dropdown-empty';
            emptyHint.textContent = '无匹配字段';
            emptyHint.style.display = 'none';
            dropdown.appendChild(emptyHint);

            available.forEach(f => {
                const item = document.createElement('div');
                item.className = 'dropdown-item';
                item.textContent = `${f.field_name} (${f.field_id})`;
                item._search = `${f.field_name} ${f.field_id}`.toLowerCase();
                item.addEventListener('click', (e) => {
                    e.stopPropagation();
                    RuleConfig.addDependFieldTag(f.field_id, f.field_name);
                    RuleConfig.closeInsertTagDropdown();
                });
                itemsWrap.appendChild(item);
            });

            search.addEventListener('input', () => {
                const kw = search.value.trim().toLowerCase();
                let visible = 0;
                itemsWrap.querySelectorAll('.dropdown-item').forEach(item => {
                    const hit = !kw || item._search.includes(kw);
                    item.style.display = hit ? '' : 'none';
                    if (hit) visible++;
                });
                emptyHint.style.display = visible === 0 ? '' : 'none';
            });
            // 阻止点击搜索框时冒泡触发关闭
            search.addEventListener('click', (e) => e.stopPropagation());
        }

        const wrap = btnEl.closest('.insert-tag-wrap');
        if (wrap) wrap.appendChild(dropdown);

        // 打开后自动聚焦搜索框，便于直接输入过滤
        const searchEl = dropdown.querySelector('.dropdown-search');
        if (searchEl) setTimeout(() => searchEl.focus(), 0);

        const closeHandler = (e) => {
            if (!dropdown.contains(e.target) && e.target !== btnEl) {
                RuleConfig.closeInsertTagDropdown();
            }
        };
        document.addEventListener('click', closeHandler, true);
        dropdown._closeHandler = closeHandler;
    },

    // ─────────────────────────────────────────────────────────
    // 字段表单
    // ─────────────────────────────────────────────────────────

    /**
     * 进阶字段：引用普通字段的提取结果。
     * mode='tag'  → 往关键词标签容器插一个 <field_result>id</field_result> 标签
     * mode='text' → 往输入框 / 文本域光标处插占位符
     */
    showFieldRefDropdown(btnEl, mode, targetId) {
        this.closeInsertTagDropdown();

        // 只能引用普通字段（进阶不能引用进阶），且不能引用自己
        const selfId = (document.getElementById('fm-field-id') || {}).value || '';
        const basics = (this.state.fields || []).filter(
            f => !f.is_advanced && f.field_id !== selfId
        );

        const dropdown = document.createElement('div');
        dropdown.className = 'insert-tag-dropdown';
        dropdown.id = '_insert-tag-dropdown';

        if (basics.length === 0) {
            dropdown.innerHTML = '<div class="dropdown-empty">暂无可引用的普通字段</div>';
        } else {
            const search = document.createElement('input');
            search.type = 'text';
            search.className = 'dropdown-search';
            search.placeholder = '搜索字段名称 / ID…';
            dropdown.appendChild(search);

            const itemsWrap = document.createElement('div');
            itemsWrap.className = 'dropdown-items';
            dropdown.appendChild(itemsWrap);

            const emptyHint = document.createElement('div');
            emptyHint.className = 'dropdown-empty';
            emptyHint.textContent = '无匹配字段';
            emptyHint.style.display = 'none';
            dropdown.appendChild(emptyHint);

            basics.forEach(f => {
                const item = document.createElement('div');
                item.className = 'dropdown-item';
                item.textContent = `${f.field_name} (${f.field_id})`;
                item._search = `${f.field_name} ${f.field_id}`.toLowerCase();
                item.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (mode === 'tag') {
                        RuleConfig.addKeywordTag(targetId, `<field_result>${f.field_id}</field_result>`);
                    } else {
                        RuleConfig.insertTagAtCursor(targetId, 'field_result', f.field_id);
                    }
                    RuleConfig.closeInsertTagDropdown();
                });
                itemsWrap.appendChild(item);
            });

            search.addEventListener('input', () => {
                const kw = search.value.trim().toLowerCase();
                let visible = 0;
                itemsWrap.querySelectorAll('.dropdown-item').forEach(item => {
                    const hit = !kw || item._search.includes(kw);
                    item.style.display = hit ? '' : 'none';
                    if (hit) visible++;
                });
                emptyHint.style.display = visible === 0 ? '' : 'none';
            });
            search.addEventListener('click', (e) => e.stopPropagation());
        }

        const wrap = btnEl.closest('.insert-tag-wrap');
        if (wrap) wrap.appendChild(dropdown);

        const searchEl = dropdown.querySelector('.dropdown-search');
        if (searchEl) setTimeout(() => searchEl.focus(), 0);

        const closeHandler = (e) => {
            if (!dropdown.contains(e.target) && e.target !== btnEl) {
                RuleConfig.closeInsertTagDropdown();
            }
        };
        document.addEventListener('click', closeHandler, true);
        dropdown._closeHandler = closeHandler;
    },

    async openFieldForm(field, forceAdvanced) {
        this.state.modalType = 'field';
        this.state.editingField = field || null;
        this.state.formIsAdvanced = field ? !!field.is_advanced : !!forceAdvanced;
        const isEdit = !!field;

        // 进阶表单需要普通字段列表供「引用字段」下拉使用
        if (this.state.formIsAdvanced && !this.state.loaded.fields) {
            await this.loadFields();
        }

        this.els.modalTitle.textContent = this.state.formIsAdvanced
            ? (isEdit ? '编辑进阶字段配置' : '新增进阶字段配置')
            : (isEdit ? '编辑字段配置' : '新增字段配置');
        this.els.modalBody.innerHTML = this.buildFieldForm(field || {});
        if (this.els.debugBtn) this.els.debugBtn.style.display = '';
        this.showModal();

        // 初始化动态区域
        const sourceType = (field && field.source_type) || 'table';
        this.onSourceTypeChange(sourceType);

        if (sourceType === 'table') {
            this.onTableMatchTypeChange((field && field.table_match_type) || 'contains');
        }

        if (sourceType === 'text') {
            const searchType = (field && field.search_type) || 'context';
            this.onSearchTypeChange(searchType);
        }
    },

    // 进阶表单里「引用字段」按钮的 HTML（普通表单返回空串）
    fieldRefBtnHtml(mode, targetId) {
        if (!this.state.formIsAdvanced) return '';
        return `<div class="insert-tag-wrap"><button type="button" class="insert-tag-btn" onclick="RuleConfig.showFieldRefDropdown(this,'${mode}','${targetId}')" title="引用普通字段的提取结果">K</button></div>`;
    },

    /**
     * 占位符按钮组：字段引用（K，仅进阶字段）+ 入参引用（P，该类型定义了入参时）。
     * 入参对普通字段同样可用——它来自外部传入，不依赖其它字段先跑完。
     */
    refBtnsHtml(mode, targetId) {
        return this.fieldRefBtnHtml(mode, targetId)
            + (typeof TypeParams !== 'undefined' ? TypeParams.btnHtml(mode, targetId) : '');
    },

    buildFieldForm(field) {
        const isEdit = !!field.field_id;
        const sourceType = field.source_type || 'table';
        const searchType = field.search_type || 'context';
        const skipLlmChecked = field.use_llm === 0 ? 'checked' : '';
        // 进阶字段：在普通字段全部抽完后执行，可引用普通字段结果
        const advancedBanner = this.state.formIsAdvanced ? `
            <div class="advanced-form-banner">
                进阶字段：在全部普通字段抽取完成后执行，可用 K 按钮引用普通字段的提取结果（仅能引用普通字段）
            </div>
        ` : '';

        return advancedBanner + `
            <div class="form-row">
                <div class="form-group">
                    <label class="form-label">字段 ID</label>
                    <input class="form-input" id="fm-field-id" value="${Utils.escapeHtml(field.field_id || '')}" ${isEdit ? 'disabled' : ''} placeholder="英文字母、数字、下划线">
                    <div class="form-hint">唯一标识，保存后不可修改</div>
                </div>
                <div class="form-group">
                    <label class="form-label">字段名称</label>
                    <input class="form-input" id="fm-field-name" value="${Utils.escapeHtml(field.field_name || '')}" placeholder="中文或英文名称">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label class="form-label">来源类型</label>
                    <select class="form-select" id="fm-source-type" onchange="RuleConfig.onSourceTypeChange(this.value)">
                        <option value="table" ${sourceType === 'table' ? 'selected' : ''}>表格</option>
                        <option value="text" ${sourceType === 'text' ? 'selected' : ''}>文本</option>
                        <option value="vl" ${sourceType === 'vl' ? 'selected' : ''}>VL（PDF 视觉模型）</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">优先级</label>
                    <input class="form-input" id="fm-priority" type="number" value="${field.priority ?? 0}" min="0">
                </div>
            </div>

            <!-- LLM 开关（仅表格 / 文本类生效） -->
            <div class="form-group" id="fm-use-llm-group">
                <label class="form-label" style="display:flex;align-items:center;gap:8px;cursor:pointer;">
                    <input type="checkbox" id="fm-skip-llm" ${skipLlmChecked} onchange="RuleConfig.onSkipLlmChange(this.checked)">
                    跳过 LLM，直接返回检索原文
                </label>
                <div class="form-hint">勾选后不调用大模型，直接把检索/匹配到的原文作为字段值（无需填写提示词）</div>
            </div>

            <!-- 表格配置区 -->
            <div id="fm-table-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">表格配置</div>
                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label">表名</label>
                        <input class="form-input" id="fm-table-name-pattern" value="${Utils.escapeHtml(field.table_name_pattern || '')}" placeholder="用于提示词占位符的标签名">
                        <div class="form-hint">作为 &lt;search_result&gt; 占位符的标签，所有匹配到的表格内容会填充到此标签中</div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">匹配方式</label>
                        <select class="form-select" id="fm-table-match-type" onchange="RuleConfig.onTableMatchTypeChange(this.value)">
                            <option value="contains" ${(field.table_match_type || 'contains') === 'contains' ? 'selected' : ''}>包含匹配</option>
                            <option value="exact" ${field.table_match_type === 'exact' ? 'selected' : ''}>精确匹配</option>
                            <option value="fuzzy" ${field.table_match_type === 'fuzzy' ? 'selected' : ''}>模糊匹配</option>
                            <option value="llm" ${field.table_match_type === 'llm' ? 'selected' : ''}>LLM 匹配</option>
                        </select>
                    </div>
                </div>
                ${this.buildKeywordTagsHtml('fm-table-match-keywords', '表格匹配词', field.table_match_keywords || [], '输入匹配词后按回车或点击添加，支持多个匹配词检索表格', this.state.formIsAdvanced)}
                <div class="form-group">
                    <label class="form-label">最大返回数量</label>
                    <input class="form-input" type="number" id="fm-table-match-max-results" min="0" placeholder="0 表示不限制" value="${field.table_match_max_results ?? ''}">
                    <div class="form-hint">匹配后最多返回的表格数量，0 或空表示不限制</div>
                </div>
                <div class="form-group" id="fm-table-match-prompt-group" style="${(field.table_match_type === 'llm') ? '' : 'display:none'}">
                    <button type="button" class="insert-tag-btn" id="fm-table-match-prompt-toggle" onclick="RuleConfig.toggleMatchPromptPanel('table')" style="width:100%;text-align:left;">${field.table_match_prompt ? '▾' : '▸'} LLM 匹配高级设置</button>
                    <div id="fm-table-match-prompt-body" style="${field.table_match_prompt ? '' : 'display:none'}">
                        <div class="form-label-row" style="margin-top:8px;">
                            <label class="form-label">LLM 匹配提示词</label>
                            <button type="button" class="insert-tag-btn" onclick="RuleConfig.resetMatchPrompt('fm-table-match-prompt','table')" title="恢复系统默认模板">↺</button>
                        </div>
                        <textarea class="form-textarea" id="fm-table-match-prompt" rows="6">${Utils.escapeHtml(field.table_match_prompt || '')}</textarea>
                        <div class="form-hint">
                            告诉模型<b>要找什么、怎么找</b>。可用占位符：<code>{table_list}</code>（候选表格清单，必填）、<code>{query}</code>（匹配词）、<code>{quantity_hint}</code>（按最大返回数量生成的数量约束句）。<br>
                            <b>输出格式由系统固定追加，请勿在此改写</b> —— 系统会在模板末尾自动附上：<code id="fm-table-match-prompt-suffix"></code>
                        </div>
                    </div>
                </div>
                <div id="fm-table-prompt-wrap">
                <div class="form-group">
                    <label class="form-label">系统提示词</label>
                    <textarea class="form-textarea" id="fm-table-system-prompt" rows="3" placeholder="可选，设置 LLM 的角色和行为约束">${Utils.escapeHtml(field.table_system_prompt || (isEdit ? '' : this.EXTRACT_DEFAULTS.SYSTEM_PROMPT))}</textarea>
                    <div class="form-hint">作为 system message 发送给 LLM，用于定义角色、输出格式等全局约束</div>
                </div>
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">用户提示词</label>
                        <div class="insert-tag-wrap">
                            <button type="button" class="insert-tag-btn" onclick="RuleConfig.showInsertTagDropdown('fm-table-extract-prompt','search_result',this)" title="插入占位符">{x}</button>
                        </div>
                        ${this.refBtnsHtml('text', 'fm-table-extract-prompt')}
                    </div>
                    <textarea class="form-textarea" id="fm-table-extract-prompt" rows="4" placeholder="须包含 <search_result>...</search_result> 占位符">${Utils.escapeHtml(field.table_extract_prompt || (isEdit ? '' : this.EXTRACT_DEFAULTS.USER_PROMPT))}</textarea>
                    <div class="form-hint">作为 user message 发送给 LLM，用 &lt;search_result&gt;...&lt;/search_result&gt; 引用检索结果</div>
                </div>
                </div>
            </div>

            <!-- 文本配置区 -->
            <div id="fm-text-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">文本配置</div>
                <div class="form-group">
                    <label class="form-label">检索方式</label>
                    <select class="form-select" id="fm-search-type" onchange="RuleConfig.onSearchTypeChange(this.value)">
                        <option value="context" ${searchType === 'context' ? 'selected' : ''}>上下文检索</option>
                        <option value="section" ${searchType === 'section' ? 'selected' : ''}>章节检索</option>
                        <option value="rule" ${searchType === 'rule' ? 'selected' : ''}>规则检索</option>
                        <option value="chunk_db" ${searchType === 'chunk_db' ? 'selected' : ''}>分块数据库</option>
                        <option value="vector_db" ${searchType === 'vector_db' ? 'selected' : ''}>向量数据库</option>
                        <option value="page" ${searchType === 'page' ? 'selected' : ''}>按页码取文</option>
                    </select>
                </div>
                <div id="fm-search-config-area">
                    ${this.buildSearchConfigFields(searchType, field.search_config || {})}
                </div>
                <div id="fm-text-prompt-wrap">
                <div class="form-group">
                    <label class="form-label">系统提示词</label>
                    <textarea class="form-textarea" id="fm-text-system-prompt" rows="3" placeholder="可选，设置 LLM 的角色和行为约束">${Utils.escapeHtml(field.text_system_prompt || (isEdit ? '' : this.EXTRACT_DEFAULTS.SYSTEM_PROMPT))}</textarea>
                    <div class="form-hint">作为 system message 发送给 LLM，用于定义角色、输出格式等全局约束</div>
                </div>
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">用户提示词</label>
                        <div class="insert-tag-wrap">
                            <button type="button" class="insert-tag-btn" onclick="RuleConfig.showInsertTagDropdown('fm-text-extract-prompt','search_result',this)" title="插入占位符">{x}</button>
                        </div>
                        ${this.refBtnsHtml('text', 'fm-text-extract-prompt')}
                    </div>
                    <textarea class="form-textarea" id="fm-text-extract-prompt" rows="4" placeholder="须包含 <search_result>...</search_result> 占位符">${Utils.escapeHtml(field.text_extract_prompt || (isEdit ? '' : this.EXTRACT_DEFAULTS.USER_PROMPT))}</textarea>
                    <div class="form-hint">作为 user message 发送给 LLM，用 &lt;search_result&gt;...&lt;/search_result&gt; 引用检索结果</div>
                </div>
                </div>
            </div>

            <!-- VL 配置区 -->
            <div id="fm-vl-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">VL 配置</div>
                <div class="form-group">
                    <label class="form-label">VL 方法</label>
                    <select class="form-select" id="fm-vl-method" onchange="RuleConfig.onVLMethodChange(this.value)">
                        <option value="vl_model" ${(field.vl_method || 'vl_locate') === 'vl_model' ? 'selected' : ''}>vl_model（全量）</option>
                        <option value="vl_progressive" ${field.vl_method === 'vl_progressive' ? 'selected' : ''}>vl_progressive（逐批扫描）</option>
                        <option value="vl_locate" ${(field.vl_method || 'vl_locate') === 'vl_locate' ? 'selected' : ''}>vl_locate（定位+提取）</option>
                    </select>
                </div>
                <div id="fm-vl-config-area">
                    ${this.buildVLConfigFields(field.vl_method || 'vl_locate', field.vl_config || {})}
                </div>
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">系统提示词（可选）</label>
                        ${this.refBtnsHtml('text', 'fm-vl-system-prompt')}
                    </div>
                    <textarea class="form-textarea" id="fm-vl-system-prompt" rows="3" placeholder="可选，VL 调用的系统提示">${Utils.escapeHtml(field.vl_system_prompt || '')}</textarea>
                </div>
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">最终提取提示词</label>
                        ${this.refBtnsHtml('text', 'fm-vl-extract-prompt')}
                    </div>
                    <textarea class="form-textarea" id="fm-vl-extract-prompt" rows="6" placeholder='必须含 value/reason 关键字，要求 VL 直接输出 {"value":..., "reason":...} JSON'>${Utils.escapeHtml(field.vl_extract_prompt || this.VL_DEFAULTS.EXTRACT_PROMPT)}</textarea>
                    <div class="form-hint">VL 直接产出 JSON，不再走第二次文本 LLM。提示词中需明确要求 value/reason 两个键。</div>
                </div>
            </div>
        `;
    },

    buildSearchConfigFields(searchType, config) {
        config = config || {};
        let html = '';

        switch (searchType) {
            case 'context':
                html = `
                    ${this.buildKeywordTagsHtml('fm-sc-keywords', '关键词', config.keywords || [], '输入关键词后按回车或点击添加', this.state.formIsAdvanced)}
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">上文字数</label>
                            <input class="form-input" id="fm-sc-context-before" type="number" value="${config.context_before ?? 200}" min="0">
                        </div>
                        <div class="form-group">
                            <label class="form-label">下文字数</label>
                            <input class="form-input" id="fm-sc-context-after" type="number" value="${config.context_after ?? 200}" min="0">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">每关键词最大结果数</label>
                            <input class="form-input" id="fm-sc-max-results" type="number" value="${config.max_results ?? 5}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">总结果数上限</label>
                            <input class="form-input" id="fm-sc-max-total-results" type="number" value="${config.max_total_results ?? ''}" min="0" placeholder="留空 = 与每关键词上限相同；0 = 不限">
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">排序方式</label>
                        <select class="form-select" id="fm-sc-sort-order">
                            <option value="relevance" ${(config.sort_order || 'relevance') === 'relevance' ? 'selected' : ''}>相关度</option>
                            <option value="asc" ${config.sort_order === 'asc' ? 'selected' : ''}>正序</option>
                            <option value="desc" ${config.sort_order === 'desc' ? 'selected' : ''}>倒序</option>
                        </select>
                        <div class="form-hint">相关度 = 命中片段里出现的不同关键词的稀有度之和；配单个关键词时等价于正序。总量上限按关键词轮流裁剪，保证每个关键词的占位符都有内容。留空时总量等于每关键词上限，与旧版本的总量一致。</div>
                    </div>
                `;
                break;

            case 'section': {
                const sectionMatchType = config.section_match_type || 'contains';
                html = `
                    <div class="form-group">
                        <div class="form-label-row">
                            <label class="form-label">章节模式</label>
                            ${this.refBtnsHtml('text', 'fm-sc-section-pattern')}
                        </div>
                        <input class="form-input" id="fm-sc-section-pattern" value="${Utils.escapeHtml(config.section_pattern || '')}" placeholder="章节标题或关键词">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">匹配方式</label>
                            <select class="form-select" id="fm-sc-section-match-type" onchange="RuleConfig.onSectionMatchTypeChange(this.value)">
                                <option value="contains" ${sectionMatchType === 'contains' ? 'selected' : ''}>包含匹配</option>
                                <option value="exact" ${sectionMatchType === 'exact' ? 'selected' : ''}>精确匹配</option>
                                <option value="fuzzy" ${sectionMatchType === 'fuzzy' ? 'selected' : ''}>模糊匹配</option>
                                <option value="llm" ${sectionMatchType === 'llm' ? 'selected' : ''}>LLM 匹配</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">最大结果数</label>
                            <input class="form-input" id="fm-sc-max-results" type="number" value="${config.max_results ?? 5}" min="1">
                        </div>
                    </div>
                    <div class="form-group" id="fm-sc-section-threshold-group" style="${sectionMatchType === 'fuzzy' ? '' : 'display:none'}">
                        <label class="form-label">相似度阈值</label>
                        <input class="form-input" id="fm-sc-section-threshold" type="number" step="0.01" value="${config.threshold ?? 0.8}" min="0" max="1">
                        <div class="form-hint">章节标题与"章节模式"的相似度 ≥ 该值才命中（0-1）</div>
                    </div>
                    <div class="form-group" id="fm-section-match-prompt-group" style="${sectionMatchType === 'llm' ? '' : 'display:none'}">
                        <button type="button" class="insert-tag-btn" id="fm-section-match-prompt-toggle" onclick="RuleConfig.toggleMatchPromptPanel('section')" style="width:100%;text-align:left;">${config.section_match_prompt ? '▾' : '▸'} LLM 匹配高级设置</button>
                        <div id="fm-section-match-prompt-body" style="${config.section_match_prompt ? '' : 'display:none'}">
                            <div class="form-label-row" style="margin-top:8px;">
                                <label class="form-label">LLM 匹配提示词</label>
                                <button type="button" class="insert-tag-btn" onclick="RuleConfig.resetMatchPrompt('fm-section-match-prompt','section')" title="恢复系统默认模板">↺</button>
                            </div>
                            <textarea class="form-textarea" id="fm-section-match-prompt" rows="6">${Utils.escapeHtml(config.section_match_prompt || '')}</textarea>
                            <div class="form-hint">
                                告诉模型<b>要找什么、怎么找</b>。可用占位符：<code>{section_list}</code>（候选章节清单，必填）、<code>{query}</code>（章节模式）、<code>{quantity_hint}</code>（按最大结果数生成的数量约束句，默认模板未使用）。<br>
                                <b>输出格式由系统固定追加，请勿在此改写</b> —— 系统会在模板末尾自动附上：<code id="fm-section-match-prompt-suffix"></code>
                            </div>
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">排序方式</label>
                        <select class="form-select" id="fm-sc-sort-order">
                            <option value="asc" ${(config.sort_order || 'asc') === 'asc' ? 'selected' : ''}>正序</option>
                            <option value="desc" ${config.sort_order === 'desc' ? 'selected' : ''}>倒序</option>
                        </select>
                    </div>
                `;
                break;
            }

            case 'rule':
                html = `
                    ${this.buildKeywordTagsHtml('fm-sc-keywords', '关键词', config.keywords || [], '输入关键词后按回车或点击添加', this.state.formIsAdvanced)}
                    ${this.buildKeywordTagsHtml('fm-sc-stop-words', '停用词', config.stop_words || [], '输入停用词后按回车或点击添加', this.state.formIsAdvanced)}
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">方向</label>
                            <select class="form-select" id="fm-sc-direction">
                                <option value="forward" ${(config.direction || 'forward') === 'forward' ? 'selected' : ''}>向后（取关键词之后的内容）</option>
                                <option value="backward" ${config.direction === 'backward' ? 'selected' : ''}>向前（取关键词之前的内容）</option>
                                <option value="both" ${config.direction === 'both' ? 'selected' : ''}>双向（前后都取）</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">每关键词最大结果数</label>
                            <input class="form-input" id="fm-sc-max-results" type="number" value="${config.max_results ?? 5}" min="1">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">最小长度</label>
                            <input class="form-input" id="fm-sc-min-length" type="number" value="${config.min_length ?? 0}" min="0">
                        </div>
                        <div class="form-group">
                            <label class="form-label">最大长度</label>
                            <input class="form-input" id="fm-sc-max-length" type="number" value="${config.max_length ?? 1000}" min="0">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">总结果数上限</label>
                            <input class="form-input" id="fm-sc-max-total-results" type="number" value="${config.max_total_results ?? ''}" min="0" placeholder="留空 = 与每关键词上限相同；0 = 不限">
                        </div>
                        <div class="form-group">
                            <label class="form-label">排序方式</label>
                            <select class="form-select" id="fm-sc-sort-order">
                                <option value="relevance" ${(config.sort_order || 'relevance') === 'relevance' ? 'selected' : ''}>相关度</option>
                                <option value="asc" ${config.sort_order === 'asc' ? 'selected' : ''}>正序</option>
                                <option value="desc" ${config.sort_order === 'desc' ? 'selected' : ''}>倒序</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-hint">相关度按抽取片段里出现的不同关键词的稀有度之和排序；配单个关键词时等价于正序。</div>
                `;
                break;

            case 'chunk_db': {
                // Backward compat: prefer config.keywords, fallback to config.keyword_filter (comma-split)
                let chunkKeywords = config.keywords || [];
                if (chunkKeywords.length === 0 && config.keyword_filter) {
                    chunkKeywords = config.keyword_filter.split(/[,，]/).map(s => s.trim()).filter(Boolean);
                }
                html = `
                    ${this.buildKeywordTagsHtml('fm-sc-keywords', '关键词', chunkKeywords, '输入关键词后按回车或点击添加', this.state.formIsAdvanced)}
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">每关键词最大结果数</label>
                            <input class="form-input" id="fm-sc-max-results" type="number" value="${config.max_results ?? 5}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">总结果数上限</label>
                            <input class="form-input" id="fm-sc-max-total-results" type="number" value="${config.max_total_results ?? ''}" min="0" placeholder="留空 = 与每关键词上限相同；0 = 不限">
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">排序方式</label>
                        <select class="form-select" id="fm-sc-sort-order">
                            <option value="relevance" ${(config.sort_order || 'relevance') === 'relevance' ? 'selected' : ''}>相关度</option>
                            <option value="asc" ${config.sort_order === 'asc' ? 'selected' : ''}>正序</option>
                            <option value="desc" ${config.sort_order === 'desc' ? 'selected' : ''}>倒序</option>
                        </select>
                    </div>
                `;
                break;
            }

            case 'vector_db':
                html = `
                    <div class="form-group">
                        <div class="form-label-row">
                            <label class="form-label">查询文本</label>
                            ${this.refBtnsHtml('text', 'fm-sc-query-text')}
                        </div>
                        <textarea class="form-input" id="fm-sc-query-text" rows="3"
                            placeholder="每行一个查询词，多行=多路检索。例：&#10;项目名称&#10;工程名称&#10;本项目名称为">${Utils.escapeHtml(
                                Array.isArray(config.query_text)
                                    ? config.query_text.join('\n')
                                    : (config.query_text || '')
                            )}</textarea>
                        <div class="form-hint">每行一个查询词，多行=多路检索。<b>每路 query 各需在提取提示词里写一个 &lt;search_result&gt;该查询词&lt;/search_result&gt; 占位符</b>，缺了保存会报错</div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Top K</label>
                            <input class="form-input" id="fm-sc-top-k" type="number" value="${config.top_k ?? ''}" min="1" placeholder="留空=按阈值+相对分差">
                        </div>
                        <div class="form-group">
                            <label class="form-label">分数阈值</label>
                            <input class="form-input" id="fm-sc-score-threshold" type="number" step="0.01" value="${config.score_threshold ?? ''}" min="0" max="1" placeholder="留空=不设绝对下限">
                        </div>
                        <div class="form-group">
                            <label class="form-label">相对分差</label>
                            <input class="form-input" id="fm-sc-score-ratio" type="number" step="0.01" value="${config.score_ratio ?? ''}" min="0" max="1" placeholder="留空=用默认 0.85">
                        </div>
                    </div>
                `;
                break;

            case 'page': {
                // 进阶字段：可由前序普通字段的模型自报页码派生取文区间
                const advExtra = this.state.formIsAdvanced ? `
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">页码来源字段（进阶）</label>
                            <select class="form-select" id="fm-sc-page-source">
                                <option value="">（不联动，手填页码）</option>
                                ${(this.state.fields || [])
                                    .filter(f => !f.is_advanced && (f.source_type === 'text' || f.source_type === 'table'))
                                    .map(f => `<option value="${Utils.escapeHtml(f.field_id)}" ${config.page_source_field === f.field_id ? 'selected' : ''}>${Utils.escapeHtml(f.field_name)} (${Utils.escapeHtml(f.field_id)})</option>`)
                                    .join('')}
                            </select>
                            <div class="form-hint">取该字段模型自报页码（_model_pages）派生取文区间</div>
                        </div>
                        <div class="form-group">
                            <label class="form-label">最大页数</label>
                            <input class="form-input" id="fm-sc-page-max-pages" type="number" min="1" value="${config.max_pages ?? ''}" placeholder="不限">
                            <div class="form-hint">派生区间跨度超过该值时，从最小页起收敛</div>
                        </div>
                    </div>` : '';
                html = advExtra + `
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">页码范围</label>
                            <input class="form-input" id="fm-sc-page-range" value="${Utils.escapeHtml(config.page_range || '')}" placeholder="如 5-7 或单页 5">
                            <div class="form-hint">单一连续区间，1 起。直接把这些页的解析文本喂给 LLM${this.state.formIsAdvanced ? '（已选来源字段时此项被忽略）' : ''}</div>
                        </div>
                        <div class="form-group">
                            <label class="form-label">最大字符数</label>
                            <input class="form-input" id="fm-sc-page-max-length" type="number" value="${config.max_length ?? 30000}" min="1">
                            <div class="form-hint">超过则末尾截断，避免超过 LLM 上下文上限</div>
                        </div>
                    </div>
                `;
                break;
            }
        }

        return html;
    },

    // 三种 VL 方法共用的页码配置块：手填范围 + 最大页数 +（进阶）页码来源字段
    buildVLPageFields(vlConfig) {
        const pageRange = vlConfig.page_range || 'all';
        let pageFrom = '', pageTo = '';
        if (pageRange !== 'all') {
            const m = pageRange.match(/^(\d+)-(\d+)$/);
            if (m) { pageFrom = m[1]; pageTo = m[2]; }
            else {
                const n = pageRange.match(/^(\d+)$/);
                if (n) { pageFrom = n[1]; pageTo = n[1]; }
            }
        }

        const advExtra = this.state.formIsAdvanced ? `
            <div class="form-group">
                <label class="form-label">页码来源字段（进阶）</label>
                <select class="form-select" id="fm-vl-page-source">
                    <option value="">（不联动，手填页码）</option>
                    ${(this.state.fields || [])
                        .filter(f => !f.is_advanced && (f.source_type === 'text' || f.source_type === 'table'))
                        .map(f => `<option value="${Utils.escapeHtml(f.field_id)}" ${vlConfig.page_source_field === f.field_id ? 'selected' : ''}>${Utils.escapeHtml(f.field_name)} (${Utils.escapeHtml(f.field_id)})</option>`)
                        .join('')}
                </select>
                <div class="form-hint">取该字段模型自报页码（_model_pages），只看这几页（离散，不含中间页）</div>
            </div>` : '';

        const disabled = this.state.formIsAdvanced && vlConfig.page_source_field ? 'disabled' : '';

        return advExtra + `
            <div class="form-row">
                <div class="form-group">
                    <label class="form-label">页面范围</label>
                    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                        <span style="white-space: nowrap;">从第</span>
                        <input class="form-input" id="fm-vl-page-from" type="number" min="1" value="${pageFrom}" placeholder="1" style="width: 90px;" ${disabled}>
                        <span style="white-space: nowrap;">页 到第</span>
                        <input class="form-input" id="fm-vl-page-to" type="number" min="1" value="${pageTo}" placeholder="末页" style="width: 90px;" ${disabled}>
                        <span style="white-space: nowrap;">页</span>
                    </div>
                    <div class="form-hint">两个都留空 = 全部页面；只填"从"不填"到" = 从该页到末页${this.state.formIsAdvanced ? '（已选来源字段时此项被忽略）' : ''}</div>
                </div>
                <div class="form-group">
                    <label class="form-label">最大页数</label>
                    <input class="form-input" id="fm-vl-max-pages" type="number" min="1" value="${vlConfig.max_pages ?? ''}" placeholder="不限">
                    <div class="form-hint">候选页超过该值时只取前 N 页</div>
                </div>
            </div>
        `;
    },

    buildVLConfigFields(method, vlConfig) {
        vlConfig = vlConfig || {};
        let html = '';

        switch (method) {
            case 'vl_model': {
                html = this.buildVLPageFields(vlConfig) + `
                    <div class="form-group">
                        <label class="form-label">最大像素数</label>
                        <input class="form-input" id="fm-vl-max-pixels" type="number" value="${vlConfig.max_pixels ?? 4000000}" min="100000">
                    </div>
                `;
                break;
            }

            case 'vl_progressive':
                html = this.buildVLPageFields(vlConfig) + `
                    <div class="form-group">
                        <label class="form-label">字段提示（提示要找的字段）</label>
                        <input class="form-input" id="fm-vl-field-hints" value="${Utils.escapeHtml(vlConfig.field_hints || '')}" placeholder="例：投资金额、签署日期、股东姓名">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">批大小</label>
                            <input class="form-input" id="fm-vl-batch-size" type="number" value="${vlConfig.batch_size ?? 2}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">最大像素数</label>
                            <input class="form-input" id="fm-vl-max-pixels" type="number" value="${vlConfig.max_pixels ?? 4000000}" min="100000">
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">批量 prompt 模板</label>
                        <textarea class="form-textarea" id="fm-vl-batch-prompt-template" rows="8" placeholder="必须含占位符 {field_hints} {page_label} {total_pages} {history}">${Utils.escapeHtml(vlConfig.batch_prompt_template || '')}</textarea>
                        <div class="form-hint">已填默认模板，如需调整请直接编辑；与默认完全一致则不会落库。</div>
                    </div>
                `;
                break;

            case 'vl_locate':
                html = this.buildVLPageFields(vlConfig) + `
                    <div class="form-group">
                        <label class="form-label">字段提示</label>
                        <input class="form-input" id="fm-vl-field-hints" value="${Utils.escapeHtml(vlConfig.field_hints || '')}" placeholder="例：资产总额、负债总额、净利润">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">网格页数</label>
                            <input class="form-input" id="fm-vl-grid-pages" type="number" value="${vlConfig.grid_pages ?? 6}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">网格列数</label>
                            <input class="form-input" id="fm-vl-grid-cols" type="number" value="${vlConfig.grid_cols ?? 3}" min="1">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">最大并发数</label>
                            <input class="form-input" id="fm-vl-max-concurrent" type="number" value="${vlConfig.max_concurrent ?? 20}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">缩略图缩放</label>
                            <input class="form-input" id="fm-vl-thumb-scale" type="number" step="0.05" value="${vlConfig.thumb_scale ?? 0.75}" min="0.1" max="2.0">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">关键页上限</label>
                            <input class="form-input" id="fm-vl-key-pages-limit" type="number" value="${vlConfig.key_pages_limit ?? 6}" min="1">
                            <div class="form-hint">定位<b>之后</b>看几页高清；上面的「最大页数」管定位<b>之前</b>扫几页缩略图</div>
                        </div>
                        <div class="form-group">
                            <label class="form-label">兜底页数</label>
                            <input class="form-input" id="fm-vl-fallback-pages" type="number" value="${vlConfig.fallback_pages ?? 3}" min="0">
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">最大像素数</label>
                        <input class="form-input" id="fm-vl-max-pixels" type="number" value="${vlConfig.max_pixels ?? 4000000}" min="100000">
                    </div>
                    <div class="form-group">
                        <label class="form-label">定位 prompt 模板</label>
                        <textarea class="form-textarea" id="fm-vl-locate-prompt-template" rows="10" placeholder="必须含占位符 {field_hints} {page_labels} {position_map} {grid_rows} {grid_cols}">${Utils.escapeHtml(vlConfig.locate_prompt_template || '')}</textarea>
                        <div class="form-hint">已填默认模板，如需调整请直接编辑；与默认完全一致则不会落库。</div>
                    </div>
                `;
                break;
        }
        return html;
    },

    onVLMethodChange(method) {
        const area = document.getElementById('fm-vl-config-area');
        if (!area) return;
        const config = (this.state.editingField && this.state.editingField.vl_method === method)
            ? (this.state.editingField.vl_config || {})
            : {};
        area.innerHTML = this.buildVLConfigFields(method, config);
        this.fillVLPromptDefaults();
    },

    // VL 模板文本框为空时填入后端默认值（模板不在前端存副本，见 loadPromptDefaults）
    async fillVLPromptDefaults() {
        const defaults = await this.loadPromptDefaults();
        const batch = document.getElementById('fm-vl-batch-prompt-template');
        if (batch && !batch.value.trim() && defaults.vl_batch) batch.value = defaults.vl_batch;
        const locate = document.getElementById('fm-vl-locate-prompt-template');
        if (locate && !locate.value.trim() && defaults.vl_locate) locate.value = defaults.vl_locate;
    },

    _parseIntValue(value) {
        const raw = String(value ?? '').trim();
        if (raw === '') return null;
        const parsed = Number.parseInt(raw, 10);
        return Number.isFinite(parsed) ? parsed : null;
    },

    _parseFloatValue(value) {
        const raw = String(value ?? '').trim();
        if (raw === '') return null;
        const parsed = Number.parseFloat(raw);
        return Number.isFinite(parsed) ? parsed : null;
    },

    parseIntOrDefault(id, def) {
        const el = document.getElementById(id);
        if (!el) return def;
        const parsed = this._parseIntValue(el.value);
        return parsed === null ? def : parsed;
    },

    parseFloatOrDefault(id, def) {
        const el = document.getElementById(id);
        if (!el) return def;
        const parsed = this._parseFloatValue(el.value);
        return parsed === null ? def : parsed;
    },

    parseIntOrNull(id) {
        const el = document.getElementById(id);
        if (!el) return null;
        return this._parseIntValue(el.value);
    },

    collectVLConfig(method) {
        const config = {};
        const getVal = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
        const getInt = (id, def) => this.parseIntOrDefault(id, def);
        const getFloat = (id, def) => this.parseFloatOrDefault(id, def);

        // 三种方法共用的页码配置
        const pageSource = getVal('fm-vl-page-source');
        if (pageSource) config.page_source_field = pageSource;
        const fromV = getVal('fm-vl-page-from');
        const toV = getVal('fm-vl-page-to');
        if (!fromV && !toV) {
            config.page_range = 'all';
        } else {
            const from = this._parseIntValue(fromV) ?? 1;
            const to = toV ? (this._parseIntValue(toV) ?? from) : 9999;
            config.page_range = `${from}-${to}`;
        }
        const maxPages = this._parseIntValue(getVal('fm-vl-max-pages'));
        if (maxPages) config.max_pages = maxPages;

        config.max_pixels = getInt('fm-vl-max-pixels', 4000000);

        switch (method) {
            case 'vl_model':
                break;
            case 'vl_progressive':
                config.field_hints = getVal('fm-vl-field-hints');
                config.batch_size = getInt('fm-vl-batch-size', 2);
                {
                    const tpl = getVal('fm-vl-batch-prompt-template');
                    const def = (this.PROMPT_DEFAULTS && this.PROMPT_DEFAULTS.vl_batch) || '';
                    if (tpl && tpl !== def) {
                        config.batch_prompt_template = tpl;
                    }
                }
                break;
            case 'vl_locate':
                config.field_hints = getVal('fm-vl-field-hints');
                config.grid_pages = getInt('fm-vl-grid-pages', 6);
                config.grid_cols = getInt('fm-vl-grid-cols', 3);
                config.max_concurrent = getInt('fm-vl-max-concurrent', 20);
                config.thumb_scale = getFloat('fm-vl-thumb-scale', 0.75);
                config.key_pages_limit = getInt('fm-vl-key-pages-limit', 6);
                config.fallback_pages = getInt('fm-vl-fallback-pages', 3);
                {
                    const tpl = getVal('fm-vl-locate-prompt-template');
                    const def = (this.PROMPT_DEFAULTS && this.PROMPT_DEFAULTS.vl_locate) || '';
                    if (tpl && tpl !== def) {
                        config.locate_prompt_template = tpl;
                    }
                }
                break;
        }
        return config;
    },

    onSourceTypeChange(type) {
        const tableSection = document.getElementById('fm-table-section');
        const textSection = document.getElementById('fm-text-section');
        const vlSection = document.getElementById('fm-vl-section');
        if (!tableSection || !textSection) return;

        tableSection.style.display = type === 'table' ? 'block' : 'none';
        textSection.style.display = type === 'text' ? 'block' : 'none';
        if (vlSection) {
            vlSection.style.display = type === 'vl' ? 'block' : 'none';
        }
        if (type === 'vl') this.fillVLPromptDefaults();
        // LLM 开关仅对表格 / 文本类有意义，VL 恒需模型
        const useLlmGroup = document.getElementById('fm-use-llm-group');
        if (useLlmGroup) {
            useLlmGroup.style.display = type === 'vl' ? 'none' : 'block';
        }
        // 同步「跳过 LLM」对提示词区的显隐
        const skipLlm = document.getElementById('fm-skip-llm');
        this.onSkipLlmChange(skipLlm ? skipLlm.checked : false);
    },

    onSkipLlmChange(skip) {
        // 勾选「跳过 LLM」时隐藏系统/用户提示词区（表格 + 文本两处）
        const display = skip ? 'none' : '';
        ['fm-table-prompt-wrap', 'fm-text-prompt-wrap'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.style.display = display;
        });
    },

    onSearchTypeChange(type) {
        const area = document.getElementById('fm-search-config-area');
        if (!area) return;

        // Preserve current search_config if editing
        const config = (this.state.editingField && this.state.editingField.search_type === type)
            ? (this.state.editingField.search_config || {})
            : {};

        area.innerHTML = this.buildSearchConfigFields(type, config);

        if (type === 'section') {
            const mt = config.section_match_type || config.match_type || 'contains';
            // 只在面板初始即展开（已存过模板）时补渲染，收起态不必发请求
            const body = document.getElementById('fm-section-match-prompt-body');
            if (mt === 'llm' && body && body.style.display !== 'none') {
                this.fillMatchPromptDefaults('section');
            }
        }
    },

    onSectionMatchTypeChange(matchType) {
        const group = document.getElementById('fm-sc-section-threshold-group');
        if (group) {
            group.style.display = matchType === 'fuzzy' ? '' : 'none';
        }
        const promptGroup = document.getElementById('fm-section-match-prompt-group');
        if (promptGroup) {
            promptGroup.style.display = matchType === 'llm' ? '' : 'none';
        }
        if (matchType !== 'llm') {
            this.collapseMatchPromptPanel('section');
            return;
        }
        const body = document.getElementById('fm-section-match-prompt-body');
        if (body && body.style.display !== 'none') this.fillMatchPromptDefaults('section');
    },

    // ─────────────────────────────────────────────────────────
    // 规则表单
    // ─────────────────────────────────────────────────────────

    async openRuleForm(rule) {
        this.state.modalType = 'rule';
        this.state.editingRule = rule || null;
        const isEdit = !!rule;

        // 依赖字段下拉需要字段列表，未加载时先拉取
        if (!this.state.loaded.fields) {
            await this.loadFields();
        }

        this.els.modalTitle.textContent = isEdit ? '编辑规则配置' : '新增规则配置';
        this.els.modalBody.innerHTML = this.buildRuleForm(rule || {});
        if (this.els.debugBtn) this.els.debugBtn.style.display = '';
        this.showModal();

        this.onRuleTypeChange((rule && rule.rule_type) || 'judge');
        SchemaBuilder.mount('fm-custom-schema-area');
    },

    buildRuleForm(rule) {
        const isEdit = !!rule.rule_id;
        const ruleType = rule.rule_type || 'judge';
        const ws = rule.web_search || {};
        const fmtChecked = rule.is_formatted ? 'checked' : '';
        const schemaJson = rule.output_schema ? Utils.escapeHtml(JSON.stringify(rule.output_schema)) : '[]';

        const fieldNameMap = {};
        (this.state.fields || []).forEach(f => { fieldNameMap[f.field_id] = f.field_name; });
        let dependTagsHtml = '';
        (rule.depend_fields || []).forEach(fid => {
            dependTagsHtml += this.buildDependFieldTag(fid, fieldNameMap[fid]);
        });

        return `
            <div class="form-row">
                <div class="form-group">
                    <label class="form-label">规则 ID</label>
                    <input class="form-input" id="fm-rule-id" value="${Utils.escapeHtml(rule.rule_id || '')}" ${isEdit ? 'disabled' : ''} placeholder="英文字母、数字、下划线">
                    <div class="form-hint">唯一标识，保存后不可修改</div>
                </div>
                <div class="form-group">
                    <label class="form-label">规则名称</label>
                    <input class="form-input" id="fm-rule-name" value="${Utils.escapeHtml(rule.rule_name || '')}" placeholder="中文或英文名称">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label class="form-label">规则类型</label>
                    <select class="form-select" id="fm-rule-type" onchange="RuleConfig.onRuleTypeChange(this.value)">
                        <option value="judge" ${ruleType === 'judge' ? 'selected' : ''}>判断 (judge)</option>
                        <option value="calc" ${ruleType === 'calc' ? 'selected' : ''}>计算 (calc)</option>
                        <option value="custom" ${ruleType === 'custom' ? 'selected' : ''}>自定义 (custom)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">优先级</label>
                    <input class="form-input" id="fm-rule-priority" type="number" value="${rule.priority ?? 0}" min="0">
                </div>
            </div>
            <div class="form-group">
                <div class="form-label-row">
                    <label class="form-label">依赖字段</label>
                    <div class="insert-tag-wrap">
                        <button type="button" class="insert-tag-btn" onclick="RuleConfig.showDependFieldDropdown(this)" title="选择依赖字段">{x}</button>
                    </div>
                </div>
                <div class="keyword-tags-container" id="fm-depend-fields-box">
                    <div class="keyword-tags-list" id="fm-depend-fields-list">${dependTagsHtml}</div>
                </div>
                <div class="form-hint">此规则依赖的提取字段，点击右上角 {x} 从当前类型的字段中选择</div>
            </div>

            <!-- 判断型配置区 -->
            <div id="fm-judge-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">判断配置</div>
                <div class="form-group">
                    <label class="form-label">系统提示词</label>
                    <textarea class="form-textarea" id="fm-system-prompt" rows="3" placeholder="可选，设置 LLM 的角色和行为约束">${Utils.escapeHtml(rule.system_prompt || '')}</textarea>
                    <div class="form-hint">作为 system message 发送给 LLM，用于定义角色、输出格式等全局约束</div>
                </div>
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">用户提示词</label>
                        <div class="insert-tag-wrap">
                            <button type="button" class="insert-tag-btn" onclick="RuleConfig.showInsertTagDropdown('fm-expression','field_result',this)" title="插入占位符">{x}</button>
                        </div>${TypeParams.btnHtml('text','fm-expression')}
                    </div>
                    <textarea class="form-textarea" id="fm-expression" rows="5" placeholder="须包含 <field_result>...</field_result> 占位符">${Utils.escapeHtml(ruleType === 'judge' ? (rule.expression || '') : '')}</textarea>
                    <div class="form-hint">用 &lt;field_result&gt;字段ID&lt;/field_result&gt; 引用字段值，LLM 返回 true/false 及原因</div>
                </div>
            </div>

            <!-- 计算型配置区 -->
            <div id="fm-calc-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">计算配置</div>
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">计算表达式</label>
                        <div class="insert-tag-wrap">
                            <button type="button" class="insert-tag-btn" onclick="RuleConfig.showInsertTagDropdown('fm-expression-calc','field_result',this)" title="插入占位符">{x}</button>
                        </div>${TypeParams.btnHtml('text','fm-expression-calc')}
                    </div>
                    <textarea class="form-textarea" id="fm-expression-calc" rows="5" placeholder="须包含 <field_result>...</field_result> 占位符">${Utils.escapeHtml(ruleType === 'calc' ? (rule.expression || '') : '')}</textarea>
                    <div class="form-hint">用 &lt;field_result&gt;字段ID&lt;/field_result&gt; 引用字段值，系统执行数值计算并返回结果</div>
                </div>
            </div>

            <!-- 自定义型配置区 -->
            <div id="fm-custom-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">自定义配置</div>
                <div class="form-group">
                    <label class="form-label">系统提示词</label>
                    <textarea class="form-textarea" id="fm-custom-system-prompt" rows="3" placeholder="可选，设置 LLM 的角色和行为约束">${Utils.escapeHtml(ruleType === 'custom' ? (rule.system_prompt || '') : '')}</textarea>
                </div>
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">用户提示词</label>
                        <div class="insert-tag-wrap">
                            <button type="button" class="insert-tag-btn" onclick="RuleConfig.showInsertTagDropdown('fm-custom-expression','field_result',this)" title="插入占位符">{x}</button>
                        </div>${TypeParams.btnHtml('text','fm-custom-expression')}
                    </div>
                    <textarea class="form-textarea" id="fm-custom-expression" rows="5" placeholder="须包含 <field_result>...</field_result> 占位符">${Utils.escapeHtml(ruleType === 'custom' ? (rule.expression || '') : '')}</textarea>
                    <div class="form-hint">用 &lt;field_result&gt;字段ID&lt;/field_result&gt; 引用字段值，让模型自由生成结果</div>
                </div>
                <div class="form-group">
                    <label class="form-label" style="display:flex;align-items:center;gap:8px;cursor:pointer;">
                        <input type="checkbox" id="fm-custom-formatted" ${fmtChecked} onchange="RuleConfig.onCustomFormattedToggle()">
                        格式化输出（按字段结构返回 JSON）
                    </label>
                    <div class="form-hint">关闭时模型返回纯文本值；开启时按下方字段结构返回结构化 JSON</div>
                </div>
                <div class="form-group" id="fm-custom-schema-group" style="display:none">
                    <label class="form-label">输出字段结构</label>
                    <div id="fm-custom-schema-area" style="padding-left:13px">
                        <textarea class="sb-json" id="fm-custom-schema-json" style="display:none">${schemaJson}</textarea>
                        <div class="sb-editor"></div>
                        <button type="button" class="sb-add-btn sb-add-lvl0" style="margin-top:8px;margin-left:-13px" title="添加字段" onclick="SchemaBuilder.addRootField()">+</button>
                        <div class="form-label" style="margin-top:10px;margin-left:-13px">实时预览</div>
                        <pre class="sb-preview debug-code-block" style="white-space:pre-wrap;max-height:220px;overflow:auto;margin-left:-13px"></pre>
                    </div>
                </div>
            </div>

            <!-- 网络搜索配置区（judge / custom 共享） -->
            <div id="fm-websearch-section">
                <div class="form-section-divider"></div>
                <div class="form-group">
                    <div class="form-label-row">
                        <label class="form-label">网络搜索</label>
                        <label class="toggle-switch">
                            <input type="checkbox" id="fm-ws-enabled" ${ws.enabled ? 'checked' : ''} onchange="RuleConfig.onWebSearchToggle(this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="form-hint">开启后执行前先联网搜索（博查），结果替换提示词中的 &lt;web_search_result/&gt; 占位符</div>
                </div>
                <div id="fm-ws-config" style="display:${ws.enabled ? 'block' : 'none'}">
                    <div class="form-group">
                        <div class="form-label-row">
                            <label class="form-label">搜索词</label>
                            <div class="insert-tag-wrap">
                                <button type="button" class="insert-tag-btn" onclick="RuleConfig.showInsertTagDropdown('fm-ws-query','field_result',this)" title="插入占位符">{x}</button>
                            </div>${TypeParams.btnHtml('text','fm-ws-query')}
                        </div>
                        <textarea class="form-textarea" id="fm-ws-query" rows="2" placeholder="可用 <field_result>字段ID</field_result> 拼接依赖字段的提取值">${Utils.escapeHtml(ws.query || '')}</textarea>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">返回条数</label>
                            <input class="form-input" id="fm-ws-count" type="number" value="${Utils.escapeHtml(String(ws.count ?? 5))}" min="1" max="50">
                        </div>
                        <div class="form-group">
                            <label class="form-label">时间范围</label>
                            <select class="form-select" id="fm-ws-freshness">
                                <option value="noLimit" ${(ws.freshness || 'noLimit') === 'noLimit' ? 'selected' : ''}>不限</option>
                                <option value="oneDay" ${ws.freshness === 'oneDay' ? 'selected' : ''}>一天内</option>
                                <option value="oneWeek" ${ws.freshness === 'oneWeek' ? 'selected' : ''}>一周内</option>
                                <option value="oneMonth" ${ws.freshness === 'oneMonth' ? 'selected' : ''}>一月内</option>
                                <option value="oneYear" ${ws.freshness === 'oneYear' ? 'selected' : ''}>一年内</option>
                            </select>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    onRuleTypeChange(type) {
        const judgeSection = document.getElementById('fm-judge-section');
        const calcSection = document.getElementById('fm-calc-section');
        const customSection = document.getElementById('fm-custom-section');
        const wsSection = document.getElementById('fm-websearch-section');
        if (!judgeSection || !calcSection) return;

        judgeSection.style.display = type === 'judge' ? 'block' : 'none';
        calcSection.style.display = type === 'calc' ? 'block' : 'none';
        if (customSection) customSection.style.display = type === 'custom' ? 'block' : 'none';
        if (wsSection) wsSection.style.display = (type === 'judge' || type === 'custom') ? 'block' : 'none';
        if (type === 'custom') this.onCustomFormattedToggle();
    },

    onCustomFormattedToggle() {
        const fmt = document.getElementById('fm-custom-formatted');
        const group = document.getElementById('fm-custom-schema-group');
        if (group) group.style.display = (fmt && fmt.checked) ? 'block' : 'none';
    },

    onWebSearchToggle(checked) {
        const area = document.getElementById('fm-ws-config');
        if (area) area.style.display = checked ? 'block' : 'none';

        if (checked) {
            const ruleType = (document.getElementById('fm-rule-type') || {}).value;
            const exprId = ruleType === 'custom' ? 'fm-custom-expression' : 'fm-expression';
            const expr = document.getElementById(exprId);
            if (expr && !expr.value.includes('<web_search_result/>')) {
                expr.value = expr.value ? expr.value + '\n<web_search_result/>' : '<web_search_result/>';
            }
        }
    },

    // ─────────────────────────────────────────────────────────
    // 数据收集
    // ─────────────────────────────────────────────────────────

    collectFieldFormData() {
        const sourceType = document.getElementById('fm-source-type').value;
        const existingField = this.state.editingField;
        const data = {
            field_id: document.getElementById('fm-field-id').value.trim(),
            field_name: document.getElementById('fm-field-name').value.trim(),
            source_type: sourceType,
            enabled: existingField ? existingField.enabled : 1,
            priority: this.parseIntOrDefault('fm-priority', 0),
            use_llm: (sourceType === 'vl' || !document.getElementById('fm-skip-llm').checked) ? 1 : 0,
            // 进阶字段标志（depend_fields 由服务端扫描配置算出，前端不传）
            is_advanced: this.state.formIsAdvanced ? 1 : 0,
            table_name_pattern: null,
            table_match_type: null,
            table_match_keywords: null,
            table_match_max_results: null,
            table_system_prompt: null,
            table_extract_prompt: null,
            search_type: null,
            search_config: null,
            text_system_prompt: null,
            text_extract_prompt: null,
            vl_method: null,
            vl_config: null,
            vl_system_prompt: null,
            vl_extract_prompt: null,
        };

        if (sourceType === 'table') {
            data.table_name_pattern = document.getElementById('fm-table-name-pattern').value.trim() || null;
            data.table_match_type = document.getElementById('fm-table-match-type').value;
            data.table_match_keywords = this.getKeywordTags('fm-table-match-keywords');
            data.table_match_max_results = this.parseIntOrNull('fm-table-match-max-results');
            data.table_system_prompt = document.getElementById('fm-table-system-prompt').value.trim() || null;
            data.table_extract_prompt = document.getElementById('fm-table-extract-prompt').value.trim() || null;
            {
                const tpl = document.getElementById('fm-table-match-prompt');
                const val = tpl ? tpl.value.trim() : '';
                const def = (this.PROMPT_DEFAULTS && this.PROMPT_DEFAULTS.table) || '';
                // 与后端默认值逐字相同则不入库，留空表示「用系统默认」
                data.table_match_prompt = (val && val !== def) ? val : null;
            }
        } else if (sourceType === 'vl') {
            data.vl_method = document.getElementById('fm-vl-method').value;
            data.vl_config = this.collectVLConfig(data.vl_method);
            data.vl_system_prompt = document.getElementById('fm-vl-system-prompt').value.trim() || null;
            data.vl_extract_prompt = document.getElementById('fm-vl-extract-prompt').value.trim() || null;
        } else {
            const searchType = document.getElementById('fm-search-type').value;
            data.search_type = searchType;
            data.search_config = this.collectSearchConfig(searchType);
            data.text_system_prompt = document.getElementById('fm-text-system-prompt').value.trim() || null;
            data.text_extract_prompt = document.getElementById('fm-text-extract-prompt').value.trim() || null;
        }

        return data;
    },

    collectSearchConfig(searchType) {
        const config = {};

        const getVal = (id) => {
            const el = document.getElementById(id);
            return el ? el.value.trim() : '';
        };
        const getInt = (id, def) => this.parseIntOrDefault(id, def);
        const getFloat = (id, def) => this.parseFloatOrDefault(id, def);
        const getList = (id) => {
            const val = getVal(id);
            return val ? val.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [];
        };

        switch (searchType) {
            case 'context':
                config.keywords = this.getKeywordTags('fm-sc-keywords');
                config.context_before = getInt('fm-sc-context-before', 200);
                config.context_after = getInt('fm-sc-context-after', 200);
                config.max_results = getInt('fm-sc-max-results', 5);
                {
                    const mt = getVal('fm-sc-max-total-results');
                    // 留空 = 不写该键，后端缺省时按 max_results 处理（总量不涨）
                    if (mt !== '') config.max_total_results = parseInt(mt, 10);
                }
                config.sort_order = getVal('fm-sc-sort-order') || 'relevance';
                break;
            case 'section':
                config.section_pattern = getVal('fm-sc-section-pattern');
                config.section_match_type = getVal('fm-sc-section-match-type') || 'contains';
                {
                    const tpl = document.getElementById('fm-section-match-prompt');
                    const val = tpl ? tpl.value.trim() : '';
                    const def = (this.PROMPT_DEFAULTS && this.PROMPT_DEFAULTS.section) || '';
                    if (val && val !== def) config.section_match_prompt = val;
                }
                config.max_results = getInt('fm-sc-max-results', 5);
                config.sort_order = getVal('fm-sc-sort-order') || 'asc';
                if (config.section_match_type === 'fuzzy') {
                    config.threshold = getFloat('fm-sc-section-threshold', 0.8);
                }
                break;
            case 'rule':
                config.keywords = this.getKeywordTags('fm-sc-keywords');
                config.stop_words = this.getKeywordTags('fm-sc-stop-words');
                config.direction = getVal('fm-sc-direction') || 'forward';
                config.min_length = getInt('fm-sc-min-length', 0);
                config.max_length = getInt('fm-sc-max-length', 1000);
                config.max_results = getInt('fm-sc-max-results', 5);
                {
                    const mt = getVal('fm-sc-max-total-results');
                    // 留空 = 不写该键，后端缺省时按 max_results 处理（总量不涨）
                    if (mt !== '') config.max_total_results = parseInt(mt, 10);
                }
                config.sort_order = getVal('fm-sc-sort-order') || 'relevance';
                break;
            case 'chunk_db':
                config.keywords = this.getKeywordTags('fm-sc-keywords');
                config.max_results = getInt('fm-sc-max-results', 5);
                {
                    const mt = getVal('fm-sc-max-total-results');
                    // 留空 = 不写该键，后端缺省时按 max_results 处理（总量不涨）
                    if (mt !== '') config.max_total_results = parseInt(mt, 10);
                }
                config.sort_order = getVal('fm-sc-sort-order') || 'relevance';
                break;
            case 'vector_db': {
                // 多行 → 数组；单行仍存单串，保持存量配置形态不变
                const queryLines = (getVal('fm-sc-query-text') || '')
                    .split('\n')
                    .map(s => s.trim())
                    .filter(Boolean);
                config.query_text = queryLines.length > 1 ? queryLines : (queryLines[0] || '');
                // 三项均「留空 = 不写该键」：后端缺省时走阈值 + 相对分差（默认 0.85）
                // + max_results 兜底。硬写 top_k 会让这条新路径永远不生效。
                const tk = getVal('fm-sc-top-k');
                if (tk !== '') config.top_k = parseInt(tk, 10);
                const st = getVal('fm-sc-score-threshold');
                if (st !== '') config.score_threshold = parseFloat(st);
                const sr = getVal('fm-sc-score-ratio');
                if (sr !== '') config.score_ratio = parseFloat(sr);
                break;
            }
            case 'page':
                config.page_range = getVal('fm-sc-page-range');
                config.max_length = getInt('fm-sc-page-max-length', 30000);
                // 进阶字段：按来源字段的模型自报页码联动取文
                if (this.state.formIsAdvanced) {
                    const src = getVal('fm-sc-page-source');
                    if (src) config.page_source_field = src;
                    const mp = this.parseIntOrNull('fm-sc-page-max-pages');
                    if (mp) config.max_pages = mp;
                }
                break;
        }

        return config;
    },

    collectRuleFormData() {
        const existingRule = this.state.editingRule;
        const ruleType = document.getElementById('fm-rule-type').value;

        let expression;
        if (ruleType === 'calc') {
            expression = document.getElementById('fm-expression-calc').value.trim();
        } else if (ruleType === 'custom') {
            expression = document.getElementById('fm-custom-expression').value.trim();
        } else {
            expression = document.getElementById('fm-expression').value.trim();
        }

        let webSearch = null;
        const wsEnabledEl = document.getElementById('fm-ws-enabled');
        if ((ruleType === 'judge' || ruleType === 'custom') && wsEnabledEl && wsEnabledEl.checked) {
            const wsQueryEl = document.getElementById('fm-ws-query');
            const wsFreshnessEl = document.getElementById('fm-ws-freshness');
            webSearch = {
                enabled: true,
                query: wsQueryEl ? wsQueryEl.value.trim() : '',
                count: this.parseIntOrDefault('fm-ws-count', 5),
                freshness: wsFreshnessEl ? (wsFreshnessEl.value || 'noLimit') : 'noLimit',
            };
        }

        let systemPrompt = null;
        if (ruleType === 'judge') {
            systemPrompt = document.getElementById('fm-system-prompt').value.trim() || null;
        } else if (ruleType === 'custom') {
            systemPrompt = document.getElementById('fm-custom-system-prompt').value.trim() || null;
        }

        let isFormatted = 0;
        let outputSchema = null;
        if (ruleType === 'custom') {
            const fmt = document.getElementById('fm-custom-formatted');
            isFormatted = fmt && fmt.checked ? 1 : 0;
            if (isFormatted) outputSchema = SchemaBuilder.collect();
        }

        return {
            rule_id: document.getElementById('fm-rule-id').value.trim(),
            rule_name: document.getElementById('fm-rule-name').value.trim(),
            rule_type: ruleType,
            expression: expression,
            system_prompt: systemPrompt,
            depend_fields: this.getDependFields(),
            web_search: webSearch,
            is_formatted: isFormatted,
            output_schema: outputSchema,
            enabled: existingRule ? existingRule.enabled : 1,
            priority: this.parseIntOrDefault('fm-rule-priority', 0),
        };
    },

    // ─────────────────────────────────────────────────────────
    // 表单验证
    // ─────────────────────────────────────────────────────────

    validateFieldForm(data) {
        const idPattern = /^[a-zA-Z0-9_]+$/;

        if (!data.field_id) {
            Toast.error('字段 ID 不能为空');
            return false;
        }
        if (data.field_id.length > 100) {
            Toast.error('字段 ID 最长 100 个字符');
            return false;
        }
        if (!idPattern.test(data.field_id)) {
            Toast.error('字段 ID 只能包含英文字母、数字和下划线');
            return false;
        }
        if (!data.field_name) {
            Toast.error('字段名称不能为空');
            return false;
        }
        if (data.field_name.length > 200) {
            Toast.error('字段名称最长 200 个字符');
            return false;
        }
        if (!data.source_type) {
            Toast.error('来源类型不能为空');
            return false;
        }

        if (data.source_type === 'table') {
            if (data.use_llm !== 0) {
                if (!data.table_extract_prompt) {
                    Toast.error('表格提取 Prompt 不能为空');
                    return false;
                }
                if (!/<search_result>[\s\S]+?<\/search_result>/.test(data.table_extract_prompt)) {
                    Toast.error('表格提取 Prompt 须包含 <search_result>...</search_result> 占位符');
                    return false;
                }
            }
        } else if (data.source_type === 'vl') {
            if (!data.vl_method) {
                Toast.error('请选择 VL 方法');
                return false;
            }
            if (!data.vl_extract_prompt) {
                Toast.error('最终提取 Prompt 不能为空');
                return false;
            }
            const lower = data.vl_extract_prompt.toLowerCase();
            if (!lower.includes('value') || !lower.includes('reason')) {
                Toast.error('最终提取 Prompt 必须包含 value 与 reason 关键字');
                return false;
            }
        } else {
            if (data.use_llm !== 0) {
                if (!data.text_extract_prompt) {
                    Toast.error('文本提取 Prompt 不能为空');
                    return false;
                }
                if (!/<search_result>[\s\S]+?<\/search_result>/.test(data.text_extract_prompt)) {
                    Toast.error('文本提取 Prompt 须包含 <search_result>...</search_result> 占位符');
                    return false;
                }
            }
        }

        return true;
    },

    validateRuleForm(data) {
        const idPattern = /^[a-zA-Z0-9_]+$/;

        if (!data.rule_id) {
            Toast.error('规则 ID 不能为空');
            return false;
        }
        if (data.rule_id.length > 100) {
            Toast.error('规则 ID 最长 100 个字符');
            return false;
        }
        if (!idPattern.test(data.rule_id)) {
            Toast.error('规则 ID 只能包含英文字母、数字和下划线');
            return false;
        }
        if (!data.rule_name) {
            Toast.error('规则名称不能为空');
            return false;
        }
        if (data.rule_name.length > 200) {
            Toast.error('规则名称最长 200 个字符');
            return false;
        }
        if (!data.rule_type) {
            Toast.error('规则类型不能为空');
            return false;
        }
        if (!data.expression) {
            const label = data.rule_type === 'calc' ? '计算表达式' : '用户提示词';
            Toast.error(label + '不能为空');
            return false;
        }
        if (!data.expression.includes('<field_result>')) {
            const label = data.rule_type === 'calc' ? '计算表达式' : '用户提示词';
            Toast.error(label + '须包含 <field_result>...</field_result> 占位符');
            return false;
        }
        if (data.web_search && data.web_search.enabled) {
            if (!data.web_search.query) {
                Toast.error('开启网络搜索时搜索词不能为空');
                return false;
            }
            if (!data.expression.includes('<web_search_result/>')) {
                Toast.error('开启网络搜索时用户提示词须包含 <web_search_result/> 占位符');
                return false;
            }
        }

        if (data.rule_type === 'custom' && data.is_formatted) {
            if (!data.output_schema || data.output_schema.length === 0) {
                Toast.error('开启格式化输出时请至少添加一个输出字段');
                return false;
            }
            const missingKey = (nodes) => nodes.some(n =>
                !n.key || !n.key.trim() ||
                ((n.type === 'object' || n.type === 'array') && missingKey(n.children || []))
            );
            if (missingKey(data.output_schema)) {
                Toast.error('输出字段的名称(key)不能为空');
                return false;
            }
            // 对象/数组类型必须含非空子字段（与后端 validate_output_schema 一致，避免 422）
            const emptyContainer = (nodes) => nodes.some(n =>
                ((n.type === 'object' || n.type === 'array') &&
                    (!Array.isArray(n.children) || n.children.length === 0)) ||
                (Array.isArray(n.children) && emptyContainer(n.children))
            );
            if (emptyContainer(data.output_schema)) {
                Toast.error('对象/数组类型的字段必须至少添加一个子字段');
                return false;
            }
        }

        return true;
    },

    // ─────────────────────────────────────────────────────────
    // 保存
    // ─────────────────────────────────────────────────────────

    async saveForm() {
        if (this.state.modalType === 'field') {
            const data = this.collectFieldFormData();
            if (!this.validateFieldForm(data)) return;

            try {
                await API.saveExtractionField(data);
                Toast.success(this.state.editingField ? '字段配置已更新' : '字段配置已创建');
                this.closeModal();
                await this.loadFields();
            } catch (error) {
                Toast.error('保存失败: ' + error.message);
            }
        } else if (this.state.modalType === 'rule') {
            const data = this.collectRuleFormData();
            if (!this.validateRuleForm(data)) return;

            try {
                await API.saveAnalysisRule(data);
                Toast.success(this.state.editingRule ? '规则配置已更新' : '规则配置已创建');
                this.closeModal();
                await this.loadRules();
            } catch (error) {
                Toast.error('保存失败: ' + error.message);
            }
        }
    },

    // ─────────────────────────────────────────────────────────
    // 启用/禁用切换
    // ─────────────────────────────────────────────────────────

    async toggleFieldEnabled(fieldId, enabled) {
        const field = this.state.fields.find(f => f.field_id === fieldId);
        if (!field) return;

        const data = Object.assign({}, field, { enabled: enabled ? 1 : 0 });
        try {
            await API.saveExtractionField(data);
            Toast.success(enabled ? '字段已启用' : '字段已禁用');
            await this.loadFields();
        } catch (error) {
            Toast.error('操作失败: ' + error.message);
            await this.loadFields();
        }
    },

    async toggleRuleEnabled(ruleId, enabled) {
        const rule = this.state.rules.find(r => r.rule_id === ruleId);
        if (!rule) return;

        const data = Object.assign({}, rule, { enabled: enabled ? 1 : 0 });
        try {
            await API.saveAnalysisRule(data);
            Toast.success(enabled ? '规则已启用' : '规则已禁用');
            await this.loadRules();
        } catch (error) {
            Toast.error('操作失败: ' + error.message);
            await this.loadRules();
        }
    },

    // ─────────────────────────────────────────────────────────
    // 删除
    // ─────────────────────────────────────────────────────────

    async deleteField(id) {
        if (!confirm('确定要删除此字段配置吗？')) return;
        try {
            await API.deleteExtractionField(id);
            Toast.success('字段配置已删除');
            await this.loadFields();
        } catch (error) {
            // 被进阶字段引用时后端返回 409，确认后可强制删除
            const msg = error.message || '';
            if (msg.includes('正被进阶字段')) {
                if (!confirm(msg + '\n\n仍要强制删除吗？')) return;
                try {
                    await API.deleteExtractionField(id, true);
                    Toast.success('字段配置已强制删除');
                    await this.loadFields();
                } catch (e2) {
                    Toast.error('删除失败: ' + e2.message);
                }
                return;
            }
            Toast.error('删除失败: ' + msg);
        }
    },

    async deleteRule(id) {
        if (!confirm('确定要删除此规则配置吗？')) return;
        try {
            await API.deleteAnalysisRule(id);
            Toast.success('规则配置已删除');
            await this.loadRules();
        } catch (error) {
            Toast.error('删除失败: ' + error.message);
        }
    },

    // ─────────────────────────────────────────────────────────
    // 调试模式
    // ─────────────────────────────────────────────────────────

    toggleDebugMode() {
        if (this.state.debugMode) {
            this.exitDebugMode();
        } else {
            this.enterDebugMode();
        }
    },

    enterDebugMode() {
        this.state.debugMode = true;

        // 保存关键词 tags 值（innerHTML 重写前）
        const savedKeywords = this._saveKeywordTagsState();

        // 获取当前表单内容
        const formHtml = this.els.modalBody.innerHTML;

        // 获取表单元素当前值
        const formValues = this._saveFormValues();

        // 构建分屏布局
        this.els.modalBody.innerHTML = `
            <div class="debug-split">
                <div class="debug-left">${formHtml}</div>
                <div class="debug-right">${this.state.modalType === 'rule' ? this.buildRuleDebugPanel() : this.buildDebugPanel()}</div>
            </div>
        `;

        // 恢复表单元素值
        this._restoreFormValues(formValues);

        // 恢复关键词 tags
        this._restoreKeywordTagsState(savedKeywords);

        // 添加 debug-mode 类实现全屏动画
        const modal = this.els.modalOverlay.querySelector('.rule-modal');
        if (modal) modal.classList.add('debug-mode');
        this.els.modalOverlay.classList.add('debug-overlay');

        // 更新按钮文字
        if (this.els.debugBtn) this.els.debugBtn.textContent = '退出调试';

        this._restoreDynamicVisibilityOnly();

        // 加载已完成文件列表
        this.loadDebugFileList();

        // 入参输入区：该类型定义了入参时渲染，预填 default_value。
        // 后端对调试接口同样做 required 校验，不给输入框就没法调通。
        TypeParams.renderDebugInputs('debug-params');
    },

    exitDebugMode() {
        this.state.debugMode = false;
        this.state.debugTestRunning = false;
        this.state.ruleExtractionResults = [];

        // 保存关键词 tags 值
        const savedKeywords = this._saveKeywordTagsState();

        // 取出左侧表单内容
        const debugLeft = this.els.modalBody.querySelector('.debug-left');
        const formHtml = debugLeft ? debugLeft.innerHTML : '';

        // 获取表单值
        const formValues = this._saveFormValues();

        // 还原 body
        this.els.modalBody.innerHTML = formHtml;

        // 恢复表单值
        this._restoreFormValues(formValues);

        // 恢复关键词 tags
        this._restoreKeywordTagsState(savedKeywords);

        // 移除 debug-mode 类
        const modal = this.els.modalOverlay.querySelector('.rule-modal');
        if (modal) modal.classList.remove('debug-mode');
        this.els.modalOverlay.classList.remove('debug-overlay');

        // 更新按钮文字
        if (this.els.debugBtn) this.els.debugBtn.textContent = '调试';

        this._restoreDynamicVisibilityOnly();
    },

    _restoreDynamicVisibilityOnly() {
        const sourceType = document.getElementById('fm-source-type');
        if (sourceType) {
            this.onSourceTypeChange(sourceType.value);
        }

        const sectionMatchType = document.getElementById('fm-sc-section-match-type');
        if (sectionMatchType) {
            this.onSectionMatchTypeChange(sectionMatchType.value);
        }

        const ruleType = document.getElementById('fm-rule-type');
        if (ruleType) {
            this.onRuleTypeChange(ruleType.value);
            SchemaBuilder.mount('fm-custom-schema-area');
        }

        const wsEnabled = document.getElementById('fm-ws-enabled');
        if (wsEnabled) {
            const wsArea = document.getElementById('fm-ws-config');
            if (wsArea) wsArea.style.display = wsEnabled.checked ? 'block' : 'none';
        }
    },

    /**
     * 保存所有表单 input/select/textarea 的当前值
     */
    _saveFormValues() {
        const values = {};
        const inputs = this.els.modalBody.querySelectorAll('input[id], select[id], textarea[id]');
        inputs.forEach(el => {
            if (el.type === 'checkbox') {
                values[el.id] = el.checked;
            } else {
                values[el.id] = el.value;
            }
        });
        return values;
    },

    /**
     * 恢复表单 input/select/textarea 值
     */
    _restoreFormValues(values) {
        for (const [id, val] of Object.entries(values)) {
            const el = document.getElementById(id);
            if (!el) continue;
            if (el.type === 'checkbox') {
                el.checked = val;
            } else {
                el.value = val;
            }
        }
    },

    /**
     * 保存关键词 tag 组件的状态
     */
    _saveKeywordTagsState() {
        const state = {};
        const containers = this.els.modalBody.querySelectorAll('.keyword-tags-container[id]');
        containers.forEach(c => {
            // 依赖字段组件的标签带 data-fid，随 innerHTML 复制天然保留，
            // 走通用恢复（纯文本重建）反而会丢失属性，故跳过
            if (c.id === 'fm-depend-fields-box') return;
            state[c.id] = this.getKeywordTags(c.id);
        });
        return state;
    },

    /**
     * 恢复关键词 tag 组件的状态
     */
    _restoreKeywordTagsState(state) {
        for (const [containerId, tags] of Object.entries(state)) {
            const container = document.getElementById(containerId);
            if (!container) continue;
            // 清除已有 tags
            const list = container.querySelector('.keyword-tags-list');
            if (list) list.innerHTML = '';
            // 重建
            for (const tag of tags) {
                this.addKeywordTag(containerId, tag);
            }
        }
    },

    buildDebugPanel() {
        return `
            <div class="debug-panel">
                <div class="debug-controls">
                    <select id="debug-file-select" class="form-select">
                        <option value="">-- 选择测试文件 --</option>
                    </select>
                    <button class="debug-test-btn" id="debug-test-btn" onclick="RuleConfig.runFieldTest()" disabled>测试</button>
                </div>

                <!-- 入参输入区：该类型定义了入参时才渲染内容 -->
                <div id="debug-params"></div>

                <div class="debug-section" id="debug-sec-config" style="display:none;">
                    <div class="debug-section-header">检索配置</div>
                    <div class="debug-section-body">
                        <div class="debug-code-block" id="debug-config-preview"></div>
                    </div>
                </div>

                <div class="debug-section" id="debug-sec-resolved-refs" style="display:none;">
                    <div class="debug-section-header">进阶字段引用解析</div>
                    <div class="debug-section-body" id="debug-resolved-refs-content"></div>
                </div>

                <div class="debug-section" id="debug-sec-match-llm" style="display:none;">
                    <div class="debug-section-header">LLM 表格匹配</div>
                    <div class="debug-section-body" id="debug-match-llm-content"></div>
                </div>

                <div class="debug-section" id="debug-sec-search" style="display:none;">
                    <div class="debug-section-header">检索结果</div>
                    <div class="debug-section-body" id="debug-search-results"></div>
                </div>

                <div class="debug-section" id="debug-sec-prompt" style="display:none;">
                    <div class="debug-section-header">LLM 提示词</div>
                    <div class="debug-section-body" id="debug-prompt-content"></div>
                </div>

                <div class="debug-section" id="debug-sec-llm" style="display:none;">
                    <div class="debug-section-header">LLM 原始响应</div>
                    <div class="debug-section-body">
                        <div class="debug-code-block" id="debug-llm-response"></div>
                    </div>
                </div>

                <div class="debug-section" id="debug-sec-result" style="display:none;">
                    <div class="debug-section-header">提取结果</div>
                    <div class="debug-section-body" id="debug-result-content"></div>
                </div>

                <div id="debug-error-area"></div>
                <div id="debug-loading-area"></div>
            </div>
        `;
    },

    async loadDebugFileList() {
        const select = document.getElementById('debug-file-select');
        const testBtn = document.getElementById('debug-test-btn');
        if (!select) return;

        try {
            const data = await API.getCompletedFiles();
            const files = data.items || data || [];

            select.innerHTML = '<option value="">-- 选择测试文件 --</option>';
            files.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f.file_id;
                opt.textContent = f.file_name || f.file_id;
                select.appendChild(opt);
            });

            // 选择文件后启用测试按钮
            select.onchange = () => {
                if (testBtn) testBtn.disabled = !select.value;
            };
        } catch (error) {
            console.error('加载文件列表失败:', error);
            select.innerHTML = '<option value="">加载失败</option>';
        }
    },

    async runFieldTest() {
        const fileSelect = document.getElementById('debug-file-select');
        const fileId = fileSelect ? fileSelect.value : '';
        if (!fileId) {
            Toast.error('请先选择测试文件');
            return;
        }

        if (this.state.debugTestRunning) return;
        this.state.debugTestRunning = true;

        // 收集当前表单数据
        const formData = this.collectFieldFormData();
        // 记录是否跳过 LLM，供调试事件渲染时避免误导性的「构建提示词/调用 LLM」提示
        this.state.debugSkipLlm = formData.use_llm === 0;

        // 显示检索配置预览
        this.showDebugConfigPreview(formData);

        // 重置结果区域
        this.resetDebugResults();

        // 显示 loading
        this._showDebugLoading('正在执行测试...');

        // 禁用测试按钮
        const testBtn = document.getElementById('debug-test-btn');
        if (testBtn) {
            testBtn.disabled = true;
            testBtn.textContent = '测试中...';
        }

        const payload = {
            file_id: fileId,
            config: formData,
            params: TypeParams.collectDebugValues(),
        };

        try {
            await API.testFieldStream(payload, (evt) => {
                this.handleDebugEvent(evt);
            });
        } catch (error) {
            this.showDebugError(error.message);
        } finally {
            this.state.debugTestRunning = false;
            this._hideDebugLoading();
            if (testBtn) {
                testBtn.disabled = !fileSelect.value;
                testBtn.textContent = '测试';
            }
        }
    },

    showDebugConfigPreview(config) {
        const section = document.getElementById('debug-sec-config');
        const preview = document.getElementById('debug-config-preview');
        if (!section || !preview) return;

        // 构建精简的配置预览
        const displayConfig = {};
        displayConfig.source_type = config.source_type;
        if (config.source_type !== 'vl') {
            displayConfig.use_llm = config.use_llm === 0 ? 0 : 1;
        }
        if (config.source_type === 'table') {
            displayConfig.table_name_pattern = config.table_name_pattern;
            displayConfig.table_match_type = config.table_match_type;
        } else if (config.source_type === 'vl') {
            displayConfig.vl_method = config.vl_method;
            displayConfig.vl_config = config.vl_config;
        } else {
            displayConfig.search_type = config.search_type;
            displayConfig.search_config = config.search_config;
        }

        preview.textContent = JSON.stringify(displayConfig, null, 2);
        section.style.display = '';
    },

    handleDebugEvent(evt) {
        const { event, data } = evt;
        switch (event) {
            case 'resolved_refs':
                this.renderDebugResolvedRefs(data);
                break;
            case 'search_results':
                this._hideDebugLoading();
                this.renderDebugSearchResults(data);
                // 跳过 LLM 时后端会直接给 result，不再有提示词/LLM 步骤，避免误导性 loading
                if (!this.state.debugSkipLlm) {
                    this._showDebugLoading('正在构建提示词...');
                }
                break;
            case 'match_llm':
                this.renderMatchLlm(data);
                break;
            case 'pdf_loaded':
            case 'progressive_batch':
            case 'locate_locate':
            case 'locate_extract':
                this.renderVLProgress(event, data);
                break;
            case 'prompt':
                this._hideDebugLoading();
                this._showDebugLoading('正在调用 LLM...');
                this.renderDebugPrompt(data);
                break;
            case 'llm_response':
                this._hideDebugLoading();
                this._showDebugLoading('正在解析结果...');
                this.renderDebugLlmResponse(data);
                break;
            case 'result':
                this._hideDebugLoading();
                this.renderDebugResult(data);
                break;
            case 'error':
                this._hideDebugLoading();
                this.showDebugError(data.message);
                break;
            case 'done':
                this._hideDebugLoading();
                break;
        }
    },

    // 进阶字段调试：展示各引用实际填入的值与页码联动派生结果
    renderDebugResolvedRefs(data) {
        const section = document.getElementById('debug-sec-resolved-refs');
        const container = document.getElementById('debug-resolved-refs-content');
        if (!section || !container) return;
        section.style.display = '';

        const refs = (data && data._resolved_refs) || {};
        const link = data && data._page_link;
        let rows = Object.keys(refs).map(fid => {
            const val = refs[fid];
            const shown = (val === null || val === undefined || val === '')
                ? '<span style="color:#c00;">（空 — 该字段未抽到值）</span>'
                : Utils.escapeHtml(String(val));
            return `<div class="debug-result-item-content" style="font-size:12px;">${Utils.escapeHtml(fid)} → ${shown}</div>`;
        }).join('');

        if (link) {
            const pages = (link.model_pages || []).join(', ');
            // mode: 'discrete'（VL，只看这几页）/ 'range'（text，连续区间）
            // 老数据无 mode 键，按 range 容错
            const taken = link.mode === 'discrete'
                ? (link.derived_pages || []).join(', ')
                : (link.derived_range || []).join('-');
            rows += `<div class="debug-result-item-content" style="font-size:12px;">页码联动：${Utils.escapeHtml(link.source_field || '')} 自报 [${Utils.escapeHtml(pages)}] → 取第 ${Utils.escapeHtml(taken)} 页${link.capped ? '（已按最大页数收敛）' : ''}</div>`;
        }
        if (!rows) rows = '<div class="debug-result-item-content" style="font-size:12px;">无字段引用</div>';

        container.innerHTML = rows;
    },

    renderVLProgress(event, data) {
        const section = document.getElementById('debug-sec-search');
        const container = document.getElementById('debug-search-results');
        if (!section || !container) return;
        section.style.display = '';

        let row = '';
        if (event === 'pdf_loaded') {
            // 首次：清空旧内容并加 header
            container.innerHTML = `<div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">VL 方法: ${Utils.escapeHtml(data.vl_method || '')} · 共 ${data.total_pages} 页</div>`;
            this._showDebugLoading('VL 抽取中...');
            return;
        }
        if (event === 'progressive_batch') {
            const icon = data.has_info ? '✓' : '✗';
            const color = data.has_info ? '#5b8d6a' : '#999';
            const preview = data.has_info ? Utils.escapeHtml(data.summary_preview || '') : '无相关';
            const idx = (data.batch_index ?? 0) + 1;
            const total = data.total_batches ?? '?';
            row = `
                <div class="debug-result-group">
                    <div class="debug-result-group-header" style="color: ${color};">[${idx}/${total}] ${Utils.escapeHtml(data.page_label || '')} ${icon}</div>
                    <div class="debug-result-item-content" style="font-size: 12px;">${preview}</div>
                </div>
            `;
        } else if (event === 'locate_locate') {
            const found = (data.found_pages || []).join(', ');
            row = `
                <div class="debug-result-group">
                    <div class="debug-result-group-header">网格 ${data.grid_idx}/${data.total_grids} · 页码 ${Utils.escapeHtml(data.page_labels || '')}</div>
                    <div class="debug-result-item-content" style="font-size: 12px;">命中: [${Utils.escapeHtml(found)}]</div>
                </div>
            `;
        } else if (event === 'locate_extract') {
            row = `
                <div class="debug-result-group">
                    <div class="debug-result-group-header" style="color: #5b8d6a;">关键页确定：[${(data.key_pages || []).join(', ')}]</div>
                    <div class="debug-result-item-content" style="font-size: 12px;">开始第二轮高清提取...</div>
                </div>
            `;
        }
        container.innerHTML += row;
    },

    renderMatchLlm(data) {
        const section = document.getElementById('debug-sec-match-llm');
        const container = document.getElementById('debug-match-llm-content');
        if (!section || !container) return;

        section.style.display = '';
        const step = data.step;

        if (step === 'prompt') {
            this._showDebugLoading('正在执行 LLM 表格匹配...');
            container.innerHTML = `
                <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">匹配 Prompt：</div>
                <div class="debug-code-block">${Utils.escapeHtml(data.prompt)}</div>
            `;
        } else if (step === 'response') {
            this._hideDebugLoading();
            const indices = data.matched_indices || [];
            // 在已有内容后追加 LLM 返回和解析结果
            container.innerHTML += `
                <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; margin-top: 8px;">LLM 返回：</div>
                <div class="debug-code-block">${Utils.escapeHtml(data.llm_response || '(无)')}</div>
                <div style="font-size: 12px; color: var(--text-secondary); margin-top: 8px;">解析序号: ${indices.length > 0 ? indices.join(', ') : '(无)'}</div>
            `;
        } else if (step === 'error') {
            this._hideDebugLoading();
            container.innerHTML += `
                <div style="color: #e74c3c; font-size: 12px; margin-top: 8px;">匹配失败: ${Utils.escapeHtml(data.error)}</div>
            `;
        }
    },

    renderDebugSearchResults(data) {
        const section = document.getElementById('debug-sec-search');
        const container = document.getElementById('debug-search-results');
        if (!section || !container) return;

        let html = '';

        if (data.source_type === 'table') {
            // 表格匹配结果
            const tables = data.matched_tables || [];
            if (tables.length === 0) {
                html = '<div style="color: var(--text-secondary); font-size: 13px;">未匹配到表格</div>';
            } else {
                html += `<div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">匹配到 ${tables.length} 个表格</div>`;
                tables.forEach(t => {
                    html += `
                        <div class="debug-result-group">
                            <div class="debug-result-group-header">${Utils.escapeHtml(t.table_name || '未命名表格')}</div>
                            <div class="debug-result-item-content">${Utils.escapeHtml(t.table_content)}</div>
                        </div>
                    `;
                });
            }
        } else {
            // 文本检索结果（按关键词分组）
            const resultsByLabel = data.results_by_label || {};
            const labels = Object.keys(resultsByLabel);
            if (labels.length === 0) {
                html = '<div style="color: var(--text-secondary); font-size: 13px;">未检索到结果</div>';
            } else {
                html += `<div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">检索类型: ${data.search_type || '-'}，共 ${(data.results || []).length} 条结果</div>`;
                labels.forEach(label => {
                    html += `
                        <div class="debug-result-group">
                            <div class="debug-result-group-header">${Utils.escapeHtml(label)}</div>
                            <div class="debug-result-item-content">${Utils.escapeHtml(resultsByLabel[label])}</div>
                        </div>
                    `;
                });
            }
        }

        container.innerHTML = html;
        section.style.display = '';
    },

    renderDebugPrompt(data) {
        const section = document.getElementById('debug-sec-prompt');
        const container = document.getElementById('debug-prompt-content');
        if (!section || !container) return;

        let html = '';

        if (data.system_prompt) {
            html += `
                <div class="debug-prompt-block">
                    <div class="debug-prompt-label">System Prompt</div>
                    <div class="debug-code-block">${Utils.escapeHtml(data.system_prompt)}</div>
                </div>
            `;
        }

        html += `
            <div class="debug-prompt-block">
                <div class="debug-prompt-label">User Prompt</div>
                <div class="debug-code-block">${Utils.escapeHtml(data.user_prompt)}</div>
            </div>
        `;

        container.innerHTML = html;
        section.style.display = '';
    },

    renderDebugLlmResponse(data) {
        const section = document.getElementById('debug-sec-llm');
        const container = document.getElementById('debug-llm-response');
        if (!section || !container) return;

        container.textContent = data.raw_response || '(空响应)';
        section.style.display = '';
    },

    renderDebugResult(data) {
        const section = document.getElementById('debug-sec-result');
        const container = document.getElementById('debug-result-content');
        if (!section || !container) return;

        // 模型自报页码（parse_llm_json_response 归一后的去重升序整数数组，可能缺省/为空）
        const pages = Array.isArray(data.pages)
            ? data.pages.map(p => parseInt(p)).filter(n => Number.isInteger(n) && n >= 1)
            : [];
        const pagesRow = pages.length > 0 ? `
                <div class="debug-result-row">
                    <span class="label">模型自报页码:</span>
                    <span class="value">${pages.map(n => `第 ${n} 页`).join('、')}</span>
                </div>
            ` : '';

        container.innerHTML = `
            <div class="debug-result-card">
                <div class="debug-result-row">
                    <span class="label">提取值:</span>
                    <span class="value">${Utils.escapeHtml(data.extracted_value || '(空)')}</span>
                </div>
                <div class="debug-result-row">
                    <span class="label">理由:</span>
                    <span class="reason">${Utils.escapeHtml(data.reason || '(无)')}</span>
                </div>
                ${pagesRow}
            </div>
        `;
        section.style.display = '';
    },

    showDebugError(msg) {
        const area = document.getElementById('debug-error-area');
        if (!area) return;
        area.innerHTML = `<div class="debug-error-banner">${Utils.escapeHtml(msg)}</div>`;
    },

    resetDebugResults() {
        // 隐藏所有结果区块
        ['debug-sec-resolved-refs', 'debug-sec-match-llm', 'debug-sec-search', 'debug-sec-prompt', 'debug-sec-llm', 'debug-sec-result'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.display = 'none';
        });
        // 清除错误
        const errorArea = document.getElementById('debug-error-area');
        if (errorArea) errorArea.innerHTML = '';
    },

    _showDebugLoading(msg) {
        const area = document.getElementById('debug-loading-area');
        if (!area) return;
        area.innerHTML = `<div class="debug-loading"><div class="spinner"></div>${Utils.escapeHtml(msg)}</div>`;
    },

    _hideDebugLoading() {
        const area = document.getElementById('debug-loading-area');
        if (area) area.innerHTML = '';
    },

    // ─────────────────────────────────────────────────────────
    // 规则调试模式
    // ─────────────────────────────────────────────────────────

    buildRuleDebugPanel() {
        return `
            <div class="debug-panel">
                <div class="debug-controls">
                    <select id="debug-file-select" class="form-select">
                        <option value="">-- 选择测试文件 --</option>
                    </select>
                    <button class="debug-test-btn" id="debug-test-btn" onclick="RuleConfig.runRuleTest()" disabled>测试</button>
                </div>

                <!-- 入参输入区：该类型定义了入参时才渲染内容 -->
                <div id="debug-params"></div>

                <label class="debug-reextract-option">
                    <span>重新抽取依赖字段</span>
                    <span class="toggle-switch">
                        <input type="checkbox" id="debug-reextract-toggle">
                        <span class="toggle-slider"></span>
                    </span>
                </label>

                <div class="debug-section" id="debug-sec-config" style="display:none;">
                    <div class="debug-section-header">规则配置</div>
                    <div class="debug-section-body">
                        <div class="debug-code-block" id="debug-config-preview"></div>
                    </div>
                </div>

                <div class="debug-section" id="debug-sec-extraction-results" style="display:none;">
                    <div class="debug-section-header">本次抽取结果</div>
                    <div class="debug-section-body" id="debug-extraction-results-content"></div>
                </div>

                <div class="debug-section" id="debug-sec-input-values" style="display:none;">
                    <div class="debug-section-header">依赖字段值</div>
                    <div class="debug-section-body" id="debug-input-values-content"></div>
                </div>

                <div class="debug-section" id="debug-sec-resolved" style="display:none;">
                    <div class="debug-section-header">表达式解析</div>
                    <div class="debug-section-body" id="debug-resolved-content"></div>
                </div>

                <div class="debug-section" id="debug-sec-web-search" style="display:none;">
                    <div class="debug-section-header">网络搜索</div>
                    <div class="debug-section-body" id="debug-web-search-content"></div>
                </div>

                <div class="debug-section" id="debug-sec-prompt" style="display:none;">
                    <div class="debug-section-header">LLM 提示词</div>
                    <div class="debug-section-body" id="debug-prompt-content"></div>
                </div>

                <div class="debug-section" id="debug-sec-llm" style="display:none;">
                    <div class="debug-section-header">LLM 原始响应</div>
                    <div class="debug-section-body">
                        <div class="debug-code-block" id="debug-llm-response"></div>
                    </div>
                </div>

                <div class="debug-section" id="debug-sec-result" style="display:none;">
                    <div class="debug-section-header">分析结果</div>
                    <div class="debug-section-body" id="debug-result-content"></div>
                </div>

                <div id="debug-error-area"></div>
                <div id="debug-loading-area"></div>
            </div>
        `;
    },

    async runRuleTest() {
        const fileSelect = document.getElementById('debug-file-select');
        const reExtractToggle = document.getElementById('debug-reextract-toggle');
        const fileId = fileSelect ? fileSelect.value : '';
        if (!fileId) {
            Toast.error('请先选择测试文件');
            return;
        }

        if (this.state.debugTestRunning) return;
        this.state.debugTestRunning = true;

        // 收集当前表单数据
        const formData = this.collectRuleFormData();

        // 显示规则配置预览
        this.showRuleDebugConfigPreview(formData);

        // 重置结果区域
        this.resetRuleDebugResults();

        // 显示 loading
        this._showDebugLoading(reExtractToggle?.checked ? '准备重新抽取依赖字段...' : '正在获取依赖字段值...');

        // 锁定本次请求上下文
        const testBtn = document.getElementById('debug-test-btn');
        if (fileSelect) fileSelect.disabled = true;
        if (reExtractToggle) reExtractToggle.disabled = true;
        if (testBtn) {
            testBtn.disabled = true;
            testBtn.textContent = '测试中...';
        }

        const payload = {
            file_id: fileId,
            config: formData,
            re_extract: !!reExtractToggle?.checked,
            params: TypeParams.collectDebugValues(),
        };

        try {
            await API.testRuleStream(payload, (evt) => {
                this.handleRuleDebugEvent(evt);
            });
        } catch (error) {
            this.showDebugError(error.message);
        } finally {
            this.state.debugTestRunning = false;
            this._hideDebugLoading();
            if (fileSelect) fileSelect.disabled = false;
            if (reExtractToggle) reExtractToggle.disabled = false;
            if (testBtn) {
                testBtn.disabled = !fileSelect.value;
                testBtn.textContent = '测试';
            }
        }
    },

    showRuleDebugConfigPreview(config) {
        const section = document.getElementById('debug-sec-config');
        const preview = document.getElementById('debug-config-preview');
        if (!section || !preview) return;

        const displayConfig = {
            rule_type: config.rule_type,
            depend_fields: config.depend_fields,
            expression: config.expression,
        };

        preview.textContent = JSON.stringify(displayConfig, null, 2);
        section.style.display = '';
    },

    handleRuleDebugEvent(evt) {
        const { event, data } = evt;
        switch (event) {
            case 'extraction_started':
                this.startRuleExtractionResults(data);
                this._showDebugLoading(`正在重新抽取依赖字段（0/${data.total || 0}）...`);
                break;
            case 'extraction_field':
                this.renderRuleExtractionField(data);
                this._showDebugLoading(`正在重新抽取依赖字段（${data.index || 0}/${data.total || 0}）...`);
                break;
            case 'extraction_done':
                this._hideDebugLoading();
                this._showDebugLoading('正在获取依赖字段值...');
                break;
            case 'input_values':
                this._hideDebugLoading();
                this._showDebugLoading('正在解析表达式...');
                this.renderRuleInputValues(data);
                break;
            case 'resolved_expression':
                this._hideDebugLoading();
                this._showDebugLoading('正在执行分析...');
                this.renderRuleResolvedExpression(data);
                break;
            case 'web_search':
                this._hideDebugLoading();
                this._showDebugLoading('正在调用 LLM...');
                this.renderRuleWebSearch(data);
                break;
            case 'prompt':
                this._hideDebugLoading();
                this._showDebugLoading('正在调用 LLM...');
                this.renderDebugPrompt(data);
                break;
            case 'llm_response':
                this._hideDebugLoading();
                this._showDebugLoading('正在解析结果...');
                this.renderDebugLlmResponse(data);
                break;
            case 'result':
                this._hideDebugLoading();
                this.renderRuleDebugResult(data);
                break;
            case 'error':
                this._hideDebugLoading();
                this.showDebugError(data.message);
                break;
            case 'done':
                this._hideDebugLoading();
                break;
        }
    },

    startRuleExtractionResults(data) {
        const total = Number(data.total) || 0;
        this.state.ruleExtractionResults = Array(total).fill(null);
        this.renderRuleExtractionResults();
    },

    renderRuleExtractionField(data) {
        if (!Array.isArray(this.state.ruleExtractionResults)) {
            this.state.ruleExtractionResults = [];
        }
        const index = Math.max(0, (Number(data.index) || 1) - 1);
        this.state.ruleExtractionResults[index] = data;
        this.renderRuleExtractionResults();
    },

    renderRuleExtractionResults() {
        const section = document.getElementById('debug-sec-extraction-results');
        const container = document.getElementById('debug-extraction-results-content');
        if (!section || !container) return;

        const results = Array.isArray(this.state.ruleExtractionResults)
            ? this.state.ruleExtractionResults
            : [];
        container.innerHTML = results.map((item, index) => {
            if (!item) {
                return `
                    <div class="debug-result-group debug-extraction-pending">
                        <div class="debug-result-group-header">字段 ${index + 1} · 等待抽取</div>
                    </div>
                `;
            }
            const statusText = item.success ? '成功' : '失败';
            const dependencyText = item.is_direct_dependency ? '规则依赖' : '前置字段';
            const pages = Array.isArray(item.source_pages) && item.source_pages.length
                ? item.source_pages.join(', ')
                : '(无)';
            return `
                <div class="debug-result-group debug-extraction-result ${item.success ? 'is-success' : 'is-failed'}">
                    <div class="debug-result-group-header">
                        <span>${Utils.escapeHtml(item.field_name || item.field_id || '')}</span>
                        <span class="debug-extraction-kind">${dependencyText}</span>
                        <span class="debug-extraction-status">${statusText}</span>
                    </div>
                    <div class="debug-result-item-content">
                        <div class="debug-extraction-meta">字段 ID：${Utils.escapeHtml(item.field_id || '')}</div>
                        <div><strong>抽取值：</strong>${Utils.escapeHtml(item.value || '(空)')}</div>
                        <div><strong>理由：</strong>${Utils.escapeHtml(item.reason || '(无)')}</div>
                        <div><strong>可用页码：</strong>${Utils.escapeHtml(pages)}</div>
                    </div>
                </div>
            `;
        }).join('');
        section.style.display = '';
    },

    renderRuleInputValues(data) {
        const section = document.getElementById('debug-sec-input-values');
        const container = document.getElementById('debug-input-values-content');
        if (!section || !container) return;

        const inputValues = data.input_values || {};
        const dependFields = data.depend_fields || [];

        let html = '';
        if (dependFields.length === 0) {
            html = '<div style="color: var(--text-secondary); font-size: 13px;">无依赖字段</div>';
        } else {
            html += '<div class="debug-result-card">';
            dependFields.forEach(fid => {
                const val = inputValues[fid] || '';
                const isEmpty = !val || !val.trim();
                html += `
                    <div class="debug-result-row">
                        <span class="label">${Utils.escapeHtml(fid)}:</span>
                        <span class="${isEmpty ? 'value' : 'value'}" style="${isEmpty ? 'color: var(--color-danger);' : ''}">${Utils.escapeHtml(val || '(空)')}</span>
                    </div>
                `;
            });
            html += '</div>';
        }

        container.innerHTML = html;
        section.style.display = '';
    },

    renderRuleResolvedExpression(data) {
        const section = document.getElementById('debug-sec-resolved');
        const container = document.getElementById('debug-resolved-content');
        if (!section || !container) return;

        container.innerHTML = `
            <div class="debug-prompt-block">
                <div class="debug-prompt-label">原始表达式</div>
                <div class="debug-code-block">${Utils.escapeHtml(data.original_expression || '')}</div>
            </div>
            <div class="debug-prompt-block">
                <div class="debug-prompt-label">解析后表达式</div>
                <div class="debug-code-block">${Utils.escapeHtml(data.resolved_expression || '')}</div>
            </div>
        `;
        section.style.display = '';
    },

    renderRuleWebSearch(data) {
        const section = document.getElementById('debug-sec-web-search');
        const container = document.getElementById('debug-web-search-content');
        if (!section || !container) return;

        let html = `<div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">搜索词: ${Utils.escapeHtml(data.query || '')}</div>`;
        if (data.error) {
            html += `<div style="color: #e74c3c; font-size: 12px;">搜索失败: ${Utils.escapeHtml(data.error)}</div>`;
        } else {
            const results = data.results || [];
            if (results.length === 0) {
                html += '<div style="color: var(--text-secondary); font-size: 13px;">无搜索结果</div>';
            }
            results.forEach((r, i) => {
                const date = (r.datePublished || '').slice(0, 10);
                const meta = [r.siteName, date].filter(Boolean).join(' · ');
                html += `
                    <div class="debug-result-group">
                        <div class="debug-result-group-header">[${i + 1}] ${Utils.escapeHtml(r.name || '')}${meta ? ` <span style="font-weight: normal; color: var(--text-secondary);">${Utils.escapeHtml(meta)}</span>` : ''}</div>
                        <div class="debug-result-item-content" style="font-size: 12px;">${Utils.escapeHtml(r.summary || '')}</div>
                    </div>
                `;
            });
        }
        container.innerHTML = html;
        section.style.display = '';
    },

    renderRuleDebugResult(data) {
        const section = document.getElementById('debug-sec-result');
        const container = document.getElementById('debug-result-content');
        if (!section || !container) return;

        container.innerHTML = `
            <div class="debug-result-card">
                <div class="debug-result-row">
                    <span class="label">分析结果:</span>
                    <span class="value">${Utils.escapeHtml(data.result_value || '(空)')}</span>
                </div>
                <div class="debug-result-row">
                    <span class="label">理由:</span>
                    <span class="reason">${Utils.escapeHtml(data.reason || '(无)')}</span>
                </div>
            </div>
        `;
        section.style.display = '';
    },

    resetRuleDebugResults() {
        this.state.ruleExtractionResults = [];
        ['debug-sec-extraction-results', 'debug-sec-input-values', 'debug-sec-resolved', 'debug-sec-web-search', 'debug-sec-prompt', 'debug-sec-llm', 'debug-sec-result'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.display = 'none';
        });
        const errorArea = document.getElementById('debug-error-area');
        if (errorArea) errorArea.innerHTML = '';
    },
};
