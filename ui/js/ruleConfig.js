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
        this.els.modalOverlay.classList.remove('active');
        this.state.editingField = null;
        this.state.editingRule = null;
        this.state.modalType = null;
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
                    <div class="form-group">
                        <label class="form-label">关键词</label>
                        <input class="form-input" id="fm-sc-keywords" value="${Utils.escapeHtml((config.keywords || []).join(', '))}" placeholder="多个关键词用逗号分隔">
                    </div>
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
                    <div class="form-group">
                        <label class="form-label">关键词</label>
                        <input class="form-input" id="fm-sc-keywords" value="${Utils.escapeHtml((config.keywords || []).join(', '))}" placeholder="多个关键词用逗号分隔">
                    </div>
                    <div class="form-group">
                        <label class="form-label">停用词</label>
                        <input class="form-input" id="fm-sc-stop-words" value="${Utils.escapeHtml((config.stop_words || []).join(', '))}" placeholder="多个停用词用逗号分隔">
                    </div>
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

            case 'chunk_db':
                html = `
                    <div class="form-group">
                        <label class="form-label">关键词过滤</label>
                        <input class="form-input" id="fm-sc-keyword-filter" value="${Utils.escapeHtml(config.keyword_filter || '')}" placeholder="过滤关键词">
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
            <div class="form-group">
                <label class="form-label">表达式</label>
                <textarea class="form-textarea" id="fm-expression" rows="5" placeholder="须包含 <field_result>...</field_result> 占位符">${Utils.escapeHtml(rule.expression || '')}</textarea>
                <div class="form-hint" id="fm-expression-hint"></div>
            </div>
        `;
    },

    onRuleTypeChange(type) {
        const hint = document.getElementById('fm-expression-hint');
        if (!hint) return;

        if (type === 'judge') {
            hint.textContent = '判断型：用 <field_result>字段ID</field_result> 引用字段值，LLM 返回 true/false 及原因';
        } else {
            hint.textContent = '计算型：用 <field_result>字段ID</field_result> 引用字段值，LLM 执行数值计算并返回结果';
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
                config.keywords = getList('fm-sc-keywords');
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
                config.keywords = getList('fm-sc-keywords');
                config.stop_words = getList('fm-sc-stop-words');
                config.direction = getVal('fm-sc-direction') || 'forward';
                config.min_length = getInt('fm-sc-min-length', 0);
                config.max_length = getInt('fm-sc-max-length', 1000);
                config.max_results = getInt('fm-sc-max-results', 5);
                config.sort_order = getVal('fm-sc-sort-order') || 'first';
                break;
            case 'chunk_db':
                config.keyword_filter = getVal('fm-sc-keyword-filter') || null;
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
        return {
            rule_id: document.getElementById('fm-rule-id').value.trim(),
            rule_name: document.getElementById('fm-rule-name').value.trim(),
            rule_type: document.getElementById('fm-rule-type').value,
            expression: document.getElementById('fm-expression').value.trim(),
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
            Toast.error('表达式不能为空');
            return false;
        }
        if (!data.expression.includes('<field_result>')) {
            Toast.error('表达式须包含 <field_result>...</field_result> 占位符');
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
};
