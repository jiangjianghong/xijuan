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
    },

    els: {},

    init() {
        this.cacheElements();
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
            sectionRules: document.getElementById('section-rules'),
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

        this.els.sectionFields.classList.toggle('active', tab === 'fields');
        this.els.sectionRules.classList.toggle('active', tab === 'rules');

        if (tab === 'fields' && !this.state.loaded.fields) {
            this.loadFields();
        } else if (tab === 'rules' && !this.state.loaded.rules) {
            this.loadRules();
        }
    },

    // ─────────────────────────────────────────────────────────
    // 数据加载
    // ─────────────────────────────────────────────────────────

    async loadFields() {
        try {
            this.state.fields = await API.getExtractionFields();
            this.state.loaded.fields = true;
            this.renderFieldList();
        } catch (error) {
            Toast.error('加载字段配置失败: ' + error.message);
        }
    },

    async loadRules() {
        try {
            this.state.rules = await API.getAnalysisRules();
            this.state.loaded.rules = true;
            this.renderRuleList();
        } catch (error) {
            Toast.error('加载规则配置失败: ' + error.message);
        }
    },

    // ─────────────────────────────────────────────────────────
    // 列表渲染
    // ─────────────────────────────────────────────────────────

    renderFieldList() {
        const fields = this.state.fields;
        if (fields.length === 0) {
            this.els.fieldListBody.innerHTML = '';
            this.els.fieldEmpty.style.display = 'block';
            return;
        }
        this.els.fieldEmpty.style.display = 'none';

        const sourceTypeText = { table: '表格', text: '文本' };
        let html = '';
        fields.forEach(f => {
            html += `
                <tr class="${f.enabled ? '' : 'row-disabled'}">
                    <td>${Utils.escapeHtml(f.field_id)}</td>
                    <td>${Utils.escapeHtml(f.field_name)}</td>
                    <td>${sourceTypeText[f.source_type] || f.source_type}</td>
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
        });
        this.els.fieldListBody.innerHTML = html;
    },

    renderRuleList() {
        const rules = this.state.rules;
        if (rules.length === 0) {
            this.els.ruleListBody.innerHTML = '';
            this.els.ruleEmpty.style.display = 'block';
            return;
        }
        this.els.ruleEmpty.style.display = 'none';

        const ruleTypeText = { judge: '判断', calc: '计算' };
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
    },

    // ─────────────────────────────────────────────────────────
    // Tag 标签式输入
    // ─────────────────────────────────────────────────────────

    buildKeywordTagsHtml(id, label, values, placeholder) {
        values = values || [];
        let tagsHtml = '';
        for (const v of values) {
            tagsHtml += `<span class="keyword-tag">${Utils.escapeHtml(v)}<button type="button" class="keyword-tag-remove" onclick="RuleConfig.removeKeywordTag(this)">&times;</button></span>`;
        }
        return `
            <div class="form-group">
                <label class="form-label">${Utils.escapeHtml(label)}</label>
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
        const span = document.createElement('span');
        span.className = 'keyword-tag';
        span.innerHTML = `${Utils.escapeHtml(value)}<button type="button" class="keyword-tag-remove" onclick="RuleConfig.removeKeywordTag(this)">&times;</button>`;
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
    // 字段表单
    // ─────────────────────────────────────────────────────────

    openFieldForm(field) {
        this.state.modalType = 'field';
        this.state.editingField = field || null;
        const isEdit = !!field;

        this.els.modalTitle.textContent = isEdit ? '编辑字段配置' : '新增字段配置';
        this.els.modalBody.innerHTML = this.buildFieldForm(field || {});
        if (this.els.debugBtn) this.els.debugBtn.style.display = '';
        this.showModal();

        // 初始化动态区域
        const sourceType = (field && field.source_type) || 'table';
        this.onSourceTypeChange(sourceType);

        if (sourceType === 'text') {
            const searchType = (field && field.search_type) || 'context';
            this.onSearchTypeChange(searchType);
        }
    },

    buildFieldForm(field) {
        const isEdit = !!field.field_id;
        const sourceType = field.source_type || 'table';
        const searchType = field.search_type || 'context';

        return `
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
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">优先级</label>
                    <input class="form-input" id="fm-priority" type="number" value="${field.priority ?? 0}" min="0">
                </div>
            </div>

            <!-- 表格配置区 -->
            <div id="fm-table-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">表格配置</div>
                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label">表名匹配模式</label>
                        <input class="form-input" id="fm-table-name-pattern" value="${Utils.escapeHtml(field.table_name_pattern || '')}" placeholder="正则表达式">
                    </div>
                    <div class="form-group">
                        <label class="form-label">匹配方式</label>
                        <select class="form-select" id="fm-table-match-type">
                            <option value="regex" ${(field.table_match_type || 'regex') === 'regex' ? 'selected' : ''}>正则匹配</option>
                            <option value="exact" ${field.table_match_type === 'exact' ? 'selected' : ''}>精确匹配</option>
                            <option value="contains" ${field.table_match_type === 'contains' ? 'selected' : ''}>包含匹配</option>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label class="form-label">系统提示词</label>
                    <textarea class="form-textarea" id="fm-table-system-prompt" rows="3" placeholder="可选，设置 LLM 的角色和行为约束">${Utils.escapeHtml(field.table_system_prompt || '')}</textarea>
                    <div class="form-hint">作为 system message 发送给 LLM，用于定义角色、输出格式等全局约束</div>
                </div>
                <div class="form-group">
                    <label class="form-label">用户提示词</label>
                    <textarea class="form-textarea" id="fm-table-extract-prompt" rows="4" placeholder="须包含 <search_result>...</search_result> 占位符">${Utils.escapeHtml(field.table_extract_prompt || '')}</textarea>
                    <div class="form-hint">作为 user message 发送给 LLM，用 &lt;search_result&gt;...&lt;/search_result&gt; 引用检索结果</div>
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
                    </select>
                </div>
                <div id="fm-search-config-area">
                    ${this.buildSearchConfigFields(searchType, field.search_config || {})}
                </div>
                <div class="form-group">
                    <label class="form-label">系统提示词</label>
                    <textarea class="form-textarea" id="fm-text-system-prompt" rows="3" placeholder="可选，设置 LLM 的角色和行为约束">${Utils.escapeHtml(field.text_system_prompt || '')}</textarea>
                    <div class="form-hint">作为 system message 发送给 LLM，用于定义角色、输出格式等全局约束</div>
                </div>
                <div class="form-group">
                    <label class="form-label">用户提示词</label>
                    <textarea class="form-textarea" id="fm-text-extract-prompt" rows="4" placeholder="须包含 <search_result>...</search_result> 占位符">${Utils.escapeHtml(field.text_extract_prompt || '')}</textarea>
                    <div class="form-hint">作为 user message 发送给 LLM，用 &lt;search_result&gt;...&lt;/search_result&gt; 引用检索结果</div>
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
                    ${this.buildKeywordTagsHtml('fm-sc-keywords', '关键词', config.keywords || [], '输入关键词后按回车或点击添加')}
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">上文行数</label>
                            <input class="form-input" id="fm-sc-context-before" type="number" value="${config.context_before ?? 3}" min="0">
                        </div>
                        <div class="form-group">
                            <label class="form-label">下文行数</label>
                            <input class="form-input" id="fm-sc-context-after" type="number" value="${config.context_after ?? 3}" min="0">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">最大结果数</label>
                            <input class="form-input" id="fm-sc-max-results" type="number" value="${config.max_results ?? 5}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">排序方式</label>
                            <select class="form-select" id="fm-sc-sort-order">
                                <option value="first" ${(config.sort_order || 'first') === 'first' ? 'selected' : ''}>出现顺序</option>
                                <option value="relevance" ${config.sort_order === 'relevance' ? 'selected' : ''}>相关度</option>
                            </select>
                        </div>
                    </div>
                `;
                break;

            case 'section':
                html = `
                    <div class="form-group">
                        <label class="form-label">章节模式</label>
                        <input class="form-input" id="fm-sc-section-pattern" value="${Utils.escapeHtml(config.section_pattern || '')}" placeholder="正则表达式">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">匹配方式</label>
                            <select class="form-select" id="fm-sc-section-match-type">
                                <option value="regex" ${(config.section_match_type || 'regex') === 'regex' ? 'selected' : ''}>正则匹配</option>
                                <option value="exact" ${config.section_match_type === 'exact' ? 'selected' : ''}>精确匹配</option>
                                <option value="contains" ${config.section_match_type === 'contains' ? 'selected' : ''}>包含匹配</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">最大结果数</label>
                            <input class="form-input" id="fm-sc-max-results" type="number" value="${config.max_results ?? 5}" min="1">
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">排序方式</label>
                        <select class="form-select" id="fm-sc-sort-order">
                            <option value="first" ${(config.sort_order || 'first') === 'first' ? 'selected' : ''}>出现顺序</option>
                            <option value="relevance" ${config.sort_order === 'relevance' ? 'selected' : ''}>相关度</option>
                        </select>
                    </div>
                `;
                break;

            case 'rule':
                html = `
                    ${this.buildKeywordTagsHtml('fm-sc-keywords', '关键词', config.keywords || [], '输入关键词后按回车或点击添加')}
                    ${this.buildKeywordTagsHtml('fm-sc-stop-words', '停用词', config.stop_words || [], '输入停用词后按回车或点击添加')}
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">方向</label>
                            <select class="form-select" id="fm-sc-direction">
                                <option value="forward" ${(config.direction || 'forward') === 'forward' ? 'selected' : ''}>向前</option>
                                <option value="backward" ${config.direction === 'backward' ? 'selected' : ''}>向后</option>
                                <option value="both" ${config.direction === 'both' ? 'selected' : ''}>双向</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">最大结果数</label>
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
                    <div class="form-group">
                        <label class="form-label">排序方式</label>
                        <select class="form-select" id="fm-sc-sort-order">
                            <option value="first" ${(config.sort_order || 'first') === 'first' ? 'selected' : ''}>出现顺序</option>
                            <option value="relevance" ${config.sort_order === 'relevance' ? 'selected' : ''}>相关度</option>
                        </select>
                    </div>
                `;
                break;

            case 'chunk_db': {
                // Backward compat: prefer config.keywords, fallback to config.keyword_filter (comma-split)
                let chunkKeywords = config.keywords || [];
                if (chunkKeywords.length === 0 && config.keyword_filter) {
                    chunkKeywords = config.keyword_filter.split(/[,，]/).map(s => s.trim()).filter(Boolean);
                }
                html = `
                    ${this.buildKeywordTagsHtml('fm-sc-keywords', '关键词', chunkKeywords, '输入关键词后按回车或点击添加')}
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">最大结果数</label>
                            <input class="form-input" id="fm-sc-max-results" type="number" value="${config.max_results ?? 5}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">排序方式</label>
                            <select class="form-select" id="fm-sc-sort-order">
                                <option value="first" ${(config.sort_order || 'first') === 'first' ? 'selected' : ''}>出现顺序</option>
                                <option value="relevance" ${config.sort_order === 'relevance' ? 'selected' : ''}>相关度</option>
                            </select>
                        </div>
                    </div>
                `;
                break;
            }

            case 'vector_db':
                html = `
                    <div class="form-group">
                        <label class="form-label">查询文本</label>
                        <input class="form-input" id="fm-sc-query-text" value="${Utils.escapeHtml(config.query_text || '')}" placeholder="用于向量检索的查询文本">
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Top K</label>
                            <input class="form-input" id="fm-sc-top-k" type="number" value="${config.top_k ?? 5}" min="1">
                        </div>
                        <div class="form-group">
                            <label class="form-label">分数阈值</label>
                            <input class="form-input" id="fm-sc-score-threshold" type="number" step="0.01" value="${config.score_threshold ?? 0.5}" min="0" max="1">
                        </div>
                    </div>
                `;
                break;
        }

        return html;
    },

    onSourceTypeChange(type) {
        const tableSection = document.getElementById('fm-table-section');
        const textSection = document.getElementById('fm-text-section');
        if (!tableSection || !textSection) return;

        tableSection.style.display = type === 'table' ? 'block' : 'none';
        textSection.style.display = type === 'text' ? 'block' : 'none';
    },

    onSearchTypeChange(type) {
        const area = document.getElementById('fm-search-config-area');
        if (!area) return;

        // Preserve current search_config if editing
        const config = (this.state.editingField && this.state.editingField.search_type === type)
            ? (this.state.editingField.search_config || {})
            : {};

        area.innerHTML = this.buildSearchConfigFields(type, config);
    },

    // ─────────────────────────────────────────────────────────
    // 规则表单
    // ─────────────────────────────────────────────────────────

    openRuleForm(rule) {
        this.state.modalType = 'rule';
        this.state.editingRule = rule || null;
        const isEdit = !!rule;

        this.els.modalTitle.textContent = isEdit ? '编辑规则配置' : '新增规则配置';
        this.els.modalBody.innerHTML = this.buildRuleForm(rule || {});
        if (this.els.debugBtn) this.els.debugBtn.style.display = 'none';
        this.showModal();

        this.onRuleTypeChange((rule && rule.rule_type) || 'judge');
    },

    buildRuleForm(rule) {
        const isEdit = !!rule.rule_id;
        const ruleType = rule.rule_type || 'judge';
        const dependFields = (rule.depend_fields || []).join(', ');

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
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">优先级</label>
                    <input class="form-input" id="fm-rule-priority" type="number" value="${rule.priority ?? 0}" min="0">
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">依赖字段</label>
                <input class="form-input" id="fm-depend-fields" value="${Utils.escapeHtml(dependFields)}" placeholder="多个 field_id 用逗号分隔">
                <div class="form-hint">此规则依赖的提取字段 ID 列表</div>
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
                    <label class="form-label">用户提示词</label>
                    <textarea class="form-textarea" id="fm-expression" rows="5" placeholder="须包含 <field_result>...</field_result> 占位符">${Utils.escapeHtml(rule.expression || '')}</textarea>
                    <div class="form-hint">作为 user message 发送给 LLM，用 &lt;field_result&gt;字段ID&lt;/field_result&gt; 引用字段值，LLM 返回 true/false 及原因</div>
                </div>
            </div>

            <!-- 计算型配置区 -->
            <div id="fm-calc-section">
                <div class="form-section-divider"></div>
                <div class="form-section-title">计算配置</div>
                <div class="form-group">
                    <label class="form-label">计算表达式</label>
                    <textarea class="form-textarea" id="fm-expression-calc" rows="5" placeholder="须包含 <field_result>...</field_result> 占位符">${Utils.escapeHtml(rule.expression || '')}</textarea>
                    <div class="form-hint">用 &lt;field_result&gt;字段ID&lt;/field_result&gt; 引用字段值，系统执行数值计算并返回结果</div>
                </div>
            </div>
        `;
    },

    onRuleTypeChange(type) {
        const judgeSection = document.getElementById('fm-judge-section');
        const calcSection = document.getElementById('fm-calc-section');
        if (!judgeSection || !calcSection) return;

        judgeSection.style.display = type === 'judge' ? 'block' : 'none';
        calcSection.style.display = type === 'calc' ? 'block' : 'none';
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
            priority: parseInt(document.getElementById('fm-priority').value) || 0,
            table_name_pattern: null,
            table_match_type: null,
            table_system_prompt: null,
            table_extract_prompt: null,
            search_type: null,
            search_config: null,
            text_system_prompt: null,
            text_extract_prompt: null,
        };

        if (sourceType === 'table') {
            data.table_name_pattern = document.getElementById('fm-table-name-pattern').value.trim() || null;
            data.table_match_type = document.getElementById('fm-table-match-type').value;
            data.table_system_prompt = document.getElementById('fm-table-system-prompt').value.trim() || null;
            data.table_extract_prompt = document.getElementById('fm-table-extract-prompt').value.trim() || null;
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
        const getInt = (id, def) => {
            const el = document.getElementById(id);
            return el ? (parseInt(el.value) || def) : def;
        };
        const getFloat = (id, def) => {
            const el = document.getElementById(id);
            return el ? (parseFloat(el.value) || def) : def;
        };
        const getList = (id) => {
            const val = getVal(id);
            return val ? val.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [];
        };

        switch (searchType) {
            case 'context':
                config.keywords = this.getKeywordTags('fm-sc-keywords');
                config.context_before = getInt('fm-sc-context-before', 3);
                config.context_after = getInt('fm-sc-context-after', 3);
                config.max_results = getInt('fm-sc-max-results', 5);
                config.sort_order = getVal('fm-sc-sort-order') || 'first';
                break;
            case 'section':
                config.section_pattern = getVal('fm-sc-section-pattern');
                config.section_match_type = getVal('fm-sc-section-match-type') || 'regex';
                config.max_results = getInt('fm-sc-max-results', 5);
                config.sort_order = getVal('fm-sc-sort-order') || 'first';
                break;
            case 'rule':
                config.keywords = this.getKeywordTags('fm-sc-keywords');
                config.stop_words = this.getKeywordTags('fm-sc-stop-words');
                config.direction = getVal('fm-sc-direction') || 'forward';
                config.min_length = getInt('fm-sc-min-length', 0);
                config.max_length = getInt('fm-sc-max-length', 1000);
                config.max_results = getInt('fm-sc-max-results', 5);
                config.sort_order = getVal('fm-sc-sort-order') || 'first';
                break;
            case 'chunk_db':
                config.keywords = this.getKeywordTags('fm-sc-keywords');
                config.max_results = getInt('fm-sc-max-results', 5);
                config.sort_order = getVal('fm-sc-sort-order') || 'first';
                break;
            case 'vector_db':
                config.query_text = getVal('fm-sc-query-text');
                config.top_k = getInt('fm-sc-top-k', 5);
                config.score_threshold = getFloat('fm-sc-score-threshold', 0.5);
                break;
        }

        return config;
    },

    collectRuleFormData() {
        const dependFieldsStr = document.getElementById('fm-depend-fields').value.trim();
        const existingRule = this.state.editingRule;
        const ruleType = document.getElementById('fm-rule-type').value;

        // judge 用 fm-expression, calc 用 fm-expression-calc
        const expression = ruleType === 'calc'
            ? document.getElementById('fm-expression-calc').value.trim()
            : document.getElementById('fm-expression').value.trim();

        return {
            rule_id: document.getElementById('fm-rule-id').value.trim(),
            rule_name: document.getElementById('fm-rule-name').value.trim(),
            rule_type: ruleType,
            expression: expression,
            system_prompt: ruleType === 'judge'
                ? (document.getElementById('fm-system-prompt').value.trim() || null)
                : null,
            depend_fields: dependFieldsStr ? dependFieldsStr.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [],
            enabled: existingRule ? existingRule.enabled : 1,
            priority: parseInt(document.getElementById('fm-rule-priority').value) || 0,
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
            if (data.table_extract_prompt && !data.table_extract_prompt.includes('<search_result>')) {
                Toast.error('表格提取 Prompt 须包含 <search_result>...</search_result> 占位符');
                return false;
            }
        } else {
            if (data.text_extract_prompt && !data.text_extract_prompt.includes('<search_result>')) {
                Toast.error('文本提取 Prompt 须包含 <search_result>...</search_result> 占位符');
                return false;
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
            const label = data.rule_type === 'judge' ? '用户提示词' : '计算表达式';
            Toast.error(label + '不能为空');
            return false;
        }
        if (!data.expression.includes('<field_result>')) {
            const label = data.rule_type === 'judge' ? '用户提示词' : '计算表达式';
            Toast.error(label + '须包含 <field_result>...</field_result> 占位符');
            return false;
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
            Toast.error('删除失败: ' + error.message);
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
                <div class="debug-right">${this.buildDebugPanel()}</div>
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

        // 恢复表单动态区域显隐
        const sourceType = document.getElementById('fm-source-type');
        if (sourceType) {
            this.onSourceTypeChange(sourceType.value);
            if (sourceType.value === 'text') {
                const searchType = document.getElementById('fm-search-type');
                if (searchType) this.onSearchTypeChange(searchType.value);
            }
        }

        // 加载已完成文件列表
        this.loadDebugFileList();
    },

    exitDebugMode() {
        this.state.debugMode = false;
        this.state.debugTestRunning = false;

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

        // 恢复表单动态区域
        const sourceType = document.getElementById('fm-source-type');
        if (sourceType) {
            this.onSourceTypeChange(sourceType.value);
            if (sourceType.value === 'text') {
                const searchType = document.getElementById('fm-search-type');
                if (searchType) this.onSearchTypeChange(searchType.value);
            }
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
                    <select id="debug-file-select">
                        <option value="">-- 选择测试文件 --</option>
                    </select>
                    <button class="debug-test-btn" id="debug-test-btn" onclick="RuleConfig.runFieldTest()" disabled>测试</button>
                </div>

                <div class="debug-section" id="debug-sec-config" style="display:none;">
                    <div class="debug-section-header">检索配置</div>
                    <div class="debug-section-body">
                        <div class="debug-code-block" id="debug-config-preview"></div>
                    </div>
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
        if (config.source_type === 'table') {
            displayConfig.table_name_pattern = config.table_name_pattern;
            displayConfig.table_match_type = config.table_match_type;
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
            case 'search_results':
                this._hideDebugLoading();
                this._showDebugLoading('正在构建提示词...');
                this.renderDebugSearchResults(data);
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
        ['debug-sec-search', 'debug-sec-prompt', 'debug-sec-llm', 'debug-sec-result'].forEach(id => {
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
};
