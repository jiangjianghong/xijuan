/**
 * 文档类型管理模块
 *
 * - 顶部下拉切换当前类型，localStorage 持久化
 * - 类型管理对话框：列表 / 新增 / 删除（含级联）
 * - 复制配置对话框：从源类型复制字段+规则到当前类型
 *
 * 切换类型后，会刷新文件列表与字段/规则列表。
 */
const DocTypeManager = {
    types: [],
    selectorTypes: [],     // 选择器用（模板+默认）
    manage: {              // 管理弹窗状态
        items: [], total: 0, page: 1, pageSize: 20,
        q: '', scope: 'all', projectId: '', selected: new Set(),
    },
    projects: [],

    async init() {
        await this.refresh();
        const sel = document.getElementById('doctype-selector');
        if (sel) {
            sel.addEventListener('change', () => this.onSelectorChange());
        }
    },

    async refresh() {
        // 选择器只需要模板 + 默认（数组形态）
        try {
            this.selectorTypes = await API.listDocTypes({ scope: 'template' });
        } catch (e) {
            console.error('加载文档类型失败:', e);
            this.selectorTypes = [{ type_id: 'default', type_name: '默认类型', is_default: 1, file_count: 0 }];
        }
        // 保留 this.types 供复制弹窗等旧逻辑使用（来源候选 = 模板+默认，合理）
        this.types = this.selectorTypes;
        this.renderSelector();
    },

    renderSelector() {
        const sel = document.getElementById('doctype-selector');
        if (!sel) return;
        const current = API.getCurrentTypeId();
        const list = (this.selectorTypes || []).slice();
        // 当前选中若不是模板/默认，则补进选择器，避免"看不到自己"
        if (current && !list.some(t => t.type_id === current)) {
            list.push({ type_id: current, type_name: current, file_count: 0 });
        }
        sel.innerHTML = list.map(t =>
            `<option value="${escapeHtml(t.type_id)}">${escapeHtml(t.type_name)} (${t.file_count || 0})</option>`
        ).join('');
        const exists = list.some(t => t.type_id === current);
        sel.value = exists ? current : 'default';
        if (!exists) API.setCurrentTypeId('default');
    },

    async onSelectorChange() {
        const sel = document.getElementById('doctype-selector');
        if (!sel) return;
        API.setCurrentTypeId(sel.value);
        // 触发当前页刷新
        if (typeof App !== 'undefined' && App.loadFileList) {
            try { App.loadFileList(); } catch (e) { console.warn(e); }
        }
        if (typeof RuleConfig !== 'undefined') {
            try { RuleConfig.loadFields && RuleConfig.loadFields(); } catch (e) { console.warn(e); }
            try { RuleConfig.loadRules && RuleConfig.loadRules(); } catch (e) { console.warn(e); }
        }
        Toast.success('已切换文档类型: ' + sel.options[sel.selectedIndex].text);
    },

    openManageDialog() {
        document.getElementById('doctype-modal-overlay').classList.add('active');
        this.manage.page = 1;
        this.manage.selected = new Set();
        this.loadProjectsIntoFilters();
        this.loadManage();
    },

    async loadProjectsIntoFilters() {
        try {
            this.projects = await API.getProjects();
        } catch (e) { this.projects = []; }
        const opts = this.projects.map(p =>
            `<option value="${escapeHtml(p.project_id)}">${escapeHtml(p.project_name)} (${p.type_count})</option>`
        ).join('');
        const projFilter = document.getElementById('dt-project');
        if (projFilter) projFilter.innerHTML =
            `<option value="">全部项目</option><option value="__ungrouped__">未分组</option>` + opts;
        const newProj = document.getElementById('doctype-new-project');
        if (newProj) newProj.innerHTML = `<option value="">未分组</option>` + opts;
        const batchProj = document.getElementById('dt-batch-project');
        if (batchProj) batchProj.innerHTML = `<option value="">移动到…(未分组)</option>` + opts;
    },

    async loadManage() {
        const m = this.manage;
        let data;
        try {
            data = await API.listDocTypes({
                q: m.q, scope: m.scope, projectId: m.projectId,
                page: m.page, pageSize: m.pageSize,
            });
        } catch (e) {
            Toast.error('加载失败: ' + e.message);
            return;
        }
        m.items = data.items || [];
        m.total = data.total || 0;
        this.renderManageTable();
    },

    renderManageTable() {
        const m = this.manage;
        const tbody = document.getElementById('doctype-table-body');
        if (!tbody) return;
        tbody.innerHTML = m.items.map(t => {
            const isDef = t.is_default === 1;
            const checked = m.selected.has(t.type_id) ? 'checked' : '';
            const tag = t.is_template === 1
                ? '<span style="color:#8DAA91;font-size:11px;">模板</span>'
                : (isDef ? '<span style="color:#8DAA91;font-size:11px;">默认</span>' : '—');
            const proj = t.project_name ? escapeHtml(t.project_name) : '—';
            const src = t.parent_type_id ? '←' + escapeHtml(t.parent_type_id) : '—';
            const tplBtn = isDef ? '' : (t.is_template === 1
                ? `<button class="btn btn-ghost" onclick="DocTypeManager.demote('${escapeAttr(t.type_id)}')">取消模板</button>`
                : `<button class="btn btn-ghost" onclick="DocTypeManager.promote('${escapeAttr(t.type_id)}')">设为模板</button>`);
            const delBtn = isDef
                ? `<button class="btn btn-ghost" disabled title="默认类型不可删除">删除</button>`
                : `<button class="btn btn-danger" onclick="DocTypeManager.deleteType('${escapeAttr(t.type_id)}')">删除</button>`;
            const checkbox = isDef ? '' :
                `<input type="checkbox" ${checked} onclick="DocTypeManager.toggleSelect('${escapeAttr(t.type_id)}', this.checked)">`;
            return `<tr>
                <td>${checkbox}</td>
                <td><code>${escapeHtml(t.type_id)}</code></td>
                <td>${escapeHtml(t.type_name)}</td>
                <td>${tag}</td>
                <td>${proj}</td>
                <td>${src}</td>
                <td>${t.file_count || 0}</td>
                <td>${t.field_count || 0}</td>
                <td>${t.rule_count || 0}</td>
                <td style="white-space:nowrap;">
                    <button class="btn btn-ghost" onclick="DocTypeManager.viewType('${escapeAttr(t.type_id)}')">查看</button>
                    ${tplBtn}
                    <button class="btn btn-ghost" onclick="DocTypeManager.exportType('${escapeAttr(t.type_id)}')">导出</button>
                    ${delBtn}
                </td>
            </tr>`;
        }).join('');

        const totalPages = Math.max(1, Math.ceil(m.total / m.pageSize));
        document.getElementById('dt-page-info').textContent = `${m.page} / ${totalPages}`;
        document.getElementById('dt-total').textContent = `共 ${m.total} 个`;
        document.getElementById('dt-selected-count').textContent = `已选 ${m.selected.size} 项`;
        const allBox = document.getElementById('dt-check-all');
        if (allBox) allBox.checked = m.items.length > 0 &&
            m.items.filter(t => t.is_default !== 1).every(t => m.selected.has(t.type_id));
    },

    onSearchInput() {
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => {
            this.manage.q = document.getElementById('dt-search').value.trim();
            this.manage.page = 1;
            this.loadManage();
        }, 300);
    },

    onFilterChange() {
        this.manage.scope = document.getElementById('dt-scope').value;
        this.manage.projectId = document.getElementById('dt-project').value;
        this.manage.page = 1;
        this.loadManage();
    },

    prevPage() { if (this.manage.page > 1) { this.manage.page--; this.loadManage(); } },
    nextPage() {
        const totalPages = Math.max(1, Math.ceil(this.manage.total / this.manage.pageSize));
        if (this.manage.page < totalPages) { this.manage.page++; this.loadManage(); }
    },

    toggleSelect(typeId, checked) {
        if (checked) this.manage.selected.add(typeId); else this.manage.selected.delete(typeId);
        document.getElementById('dt-selected-count').textContent = `已选 ${this.manage.selected.size} 项`;
    },
    toggleSelectAll(checked) {
        this.manage.items.filter(t => t.is_default !== 1).forEach(t => {
            if (checked) this.manage.selected.add(t.type_id); else this.manage.selected.delete(t.type_id);
        });
        this.renderManageTable();
    },

    async promote(typeId) {
        try { await API.promoteType(typeId); Toast.success('已设为模板'); await this.loadManage(); await this.refresh(); }
        catch (e) { Toast.error('操作失败: ' + e.message); }
    },
    async demote(typeId) {
        try { await API.demoteType(typeId); Toast.success('已取消模板'); await this.loadManage(); await this.refresh(); }
        catch (e) { Toast.error('操作失败: ' + e.message); }
    },

    async batchDelete() {
        const ids = Array.from(this.manage.selected);
        if (ids.length === 0) { Toast.error('未选择任何类型'); return; }
        if (!confirm(`确认删除选中的 ${ids.length} 个类型？有数据的将级联删除，不可撤销！`)) return;
        try {
            const res = await API.batchDeleteTypes(ids, true);
            Toast.success(`批量删除：成功 ${res.deleted}/${ids.length}`);
            const failed = res.results.filter(r => !r.ok);
            if (failed.length) alert('以下未删除：\n' + failed.map(f => `${f.type_id}：${f.reason}`).join('\n'));
            this.manage.selected = new Set();
            await this.loadManage();
            await this.refresh();
        } catch (e) { Toast.error('批量删除失败: ' + e.message); }
    },

    async batchMove() {
        const ids = Array.from(this.manage.selected);
        if (ids.length === 0) { Toast.error('未选择任何类型'); return; }
        const pid = document.getElementById('dt-batch-project').value || null;
        try {
            await API.batchAssignProject(ids, pid);
            Toast.success(`已移动 ${ids.length} 个类型`);
            this.manage.selected = new Set();
            await this.loadProjectsIntoFilters();
            await this.loadManage();
        } catch (e) { Toast.error('移动失败: ' + e.message); }
    },

    async viewType(typeId) {
        try {
            const payload = await API.exportDocType(typeId);
            const fields = (payload.fields || []).map(f =>
                `<li><code>${escapeHtml(f.field_name)}</code> · ${escapeHtml(f.source_type)}${f.search_type ? '/' + escapeHtml(f.search_type) : ''}</li>`
            ).join('') || '<li style="color:#999;">无字段</li>';
            const rules = (payload.rules || []).map(r =>
                `<li><code>${escapeHtml(r.rule_name)}</code> · ${escapeHtml(r.rule_type)}</li>`
            ).join('') || '<li style="color:#999;">无规则</li>';
            document.getElementById('typeview-title').textContent = `查看配置：${payload.type_name || typeId}`;
            document.getElementById('typeview-body').innerHTML =
                `<h4 style="margin:0 0 8px;">字段（${(payload.fields || []).length}）</h4><ul>${fields}</ul>
                 <h4 style="margin:16px 0 8px;">规则（${(payload.rules || []).length}）</h4><ul>${rules}</ul>`;
            document.getElementById('typeview-modal-overlay').classList.add('active');
        } catch (e) { Toast.error('查看失败: ' + e.message); }
    },
    closeTypeView() { document.getElementById('typeview-modal-overlay').classList.remove('active'); },

    // ─── 项目管理子弹窗 ───
    async openProjectDialog() {
        document.getElementById('project-modal-overlay').classList.add('active');
        await this.renderProjectTable();
    },
    closeProjectDialog() {
        document.getElementById('project-modal-overlay').classList.remove('active');
        this.loadProjectsIntoFilters();
    },
    async renderProjectTable() {
        try { this.projects = await API.getProjects(); } catch (e) { this.projects = []; }
        const tbody = document.getElementById('project-table-body');
        tbody.innerHTML = this.projects.map(p => `<tr>
            <td><code>${escapeHtml(p.project_id)}</code></td>
            <td>${escapeHtml(p.project_name)}</td>
            <td>${p.type_count}</td>
            <td><button class="btn btn-danger" onclick="DocTypeManager.deleteProjectById('${escapeAttr(p.project_id)}')">删除</button></td>
        </tr>`).join('') || '<tr><td colspan="4" style="color:#999;">暂无项目</td></tr>';
    },
    async createProjectFromForm() {
        const id = (document.getElementById('project-new-id').value || '').trim();
        const name = (document.getElementById('project-new-name').value || '').trim();
        if (!id || !name) { Toast.error('项目 ID 和名称都是必填'); return; }
        if (!/^[a-zA-Z0-9_-]+$/.test(id)) { Toast.error('项目 ID 只能含英文/数字/_/-'); return; }
        try {
            await API.saveProject({ project_id: id, project_name: name });
            document.getElementById('project-new-id').value = '';
            document.getElementById('project-new-name').value = '';
            Toast.success('项目已保存');
            await this.renderProjectTable();
        } catch (e) { Toast.error('保存失败: ' + e.message); }
    },
    async deleteProjectById(projectId) {
        if (!confirm(`删除项目 "${projectId}"？其下类型将变为未分组（类型本身不删）。`)) return;
        try {
            await API.deleteProject(projectId);
            Toast.success('项目已删除');
            await this.renderProjectTable();
            await this.loadManage();
        } catch (e) { Toast.error('删除失败: ' + e.message); }
    },

    closeManageDialog() {
        document.getElementById('doctype-modal-overlay').classList.remove('active');
    },

    async createTypeFromForm() {
        const idEl = document.getElementById('doctype-new-id');
        const nameEl = document.getElementById('doctype-new-name');
        const type_id = (idEl.value || '').trim();
        const type_name = (nameEl.value || '').trim();
        if (!type_id || !type_name) {
            Toast.error('类型 ID 和名称都是必填');
            return;
        }
        if (!/^[a-zA-Z0-9_-]+$/.test(type_id)) {
            Toast.error('类型 ID 只能包含英文/数字/_/-');
            return;
        }
        try {
            await API.saveDocType({ type_id, type_name, enabled: 1 });
            const pid = document.getElementById('doctype-new-project')?.value || null;
            if (pid) { try { await API.batchAssignProject([type_id], pid); } catch (e) {} }
            idEl.value = '';
            nameEl.value = '';
            Toast.success('类型已创建');
            await this.loadProjectsIntoFilters();
            await this.loadManage();
            await this.refresh();
        } catch (e) {
            Toast.error('创建失败: ' + e.message);
        }
    },

    async deleteType(typeId) {
        const t = this.manage.items.find(x => x.type_id === typeId);
        if (!t) return;
        const hasData = (t.file_count || 0) + (t.field_count || 0) + (t.rule_count || 0) > 0;
        let force = false;
        if (hasData) {
            if (!confirm(`类型 "${t.type_name}" 下有 ${t.file_count} 个文件、${t.field_count} 个字段、${t.rule_count} 条规则。\n\n确认级联删除全部数据？此操作不可撤销！`)) {
                return;
            }
            force = true;
        } else {
            if (!confirm(`确认删除类型 "${t.type_name}"？`)) return;
        }
        try {
            await API.deleteDocType(typeId, force);
            Toast.success('类型已删除');
            // 若删除的是当前类型，切回 default
            if (API.getCurrentTypeId() === typeId) {
                API.setCurrentTypeId('default');
            }
            this.manage.selected.delete(typeId);
            await this.loadManage();
            await this.refresh();
        } catch (e) {
            Toast.error('删除失败: ' + e.message);
        }
    },

    // ─── 复制配置 ───

    openCopyDialog() {
        const current = API.getCurrentTypeId();
        const currentType = this.types.find(t => t.type_id === current);
        document.getElementById('copy-target-display').value =
            currentType ? `${currentType.type_name} (${currentType.type_id})` : current;

        // 源类型下拉：排除当前
        const sourceSel = document.getElementById('copy-source-select');
        const candidates = this.types.filter(t => t.type_id !== current);
        if (candidates.length === 0) {
            Toast.error('没有其他文档类型可供复制');
            return;
        }
        sourceSel.innerHTML = candidates.map(t =>
            `<option value="${escapeHtml(t.type_id)}">${escapeHtml(t.type_name)} (字段 ${t.field_count}, 规则 ${t.rule_count})</option>`
        ).join('');

        document.getElementById('copy-fields-flag').checked = true;
        document.getElementById('copy-rules-flag').checked = true;
        document.getElementById('copy-conflict').value = 'rename';
        this.onSourceTypeChange();
        document.getElementById('copy-modal-overlay').classList.add('active');
    },

    closeCopyDialog() {
        document.getElementById('copy-modal-overlay').classList.remove('active');
    },

    onSourceTypeChange() {
        const sourceSel = document.getElementById('copy-source-select');
        const tid = sourceSel.value;
        const t = this.types.find(x => x.type_id === tid);
        const summary = document.getElementById('copy-source-summary');
        if (t) {
            summary.textContent = `源类型 "${t.type_name}" 当前有 ${t.field_count} 个字段、${t.rule_count} 条规则。复制后两份完全独立，目标类型修改不会影响源。`;
        } else {
            summary.textContent = '';
        }
    },

    async executeCopy() {
        const target = API.getCurrentTypeId();
        const source = document.getElementById('copy-source-select').value;
        const copyFields = document.getElementById('copy-fields-flag').checked;
        const copyRules = document.getElementById('copy-rules-flag').checked;
        const onConflict = document.getElementById('copy-conflict').value;

        if (!source) {
            Toast.error('请选择源类型');
            return;
        }
        if (!copyFields && !copyRules) {
            Toast.error('请至少勾选一种内容');
            return;
        }

        const payload = {
            source_type_id: source,
            on_conflict: onConflict,
        };
        // field_ids/rule_ids 留空表示全部；显式传 [] 表示一个都不选，所以这里要不传
        if (!copyFields) payload.field_ids = [];
        if (!copyRules) payload.rule_ids = [];

        try {
            const result = await API.copyConfigs(target, payload);
            const msg = `复制完成：字段 ${result.copied_fields} 已复制 / ${result.skipped_fields} 已跳过；规则 ${result.copied_rules} 已复制 / ${result.skipped_rules} 已跳过`;
            Toast.success(msg);
            if (result.missing_dependencies && result.missing_dependencies.length > 0) {
                alert('部分规则的依赖字段在目标类型中不存在，已自动剔除：\n' + result.missing_dependencies.join('\n'));
            }
            this.closeCopyDialog();
            await this.refresh();
            // 刷新规则配置页
            if (typeof RuleConfig !== 'undefined') {
                try { RuleConfig.loadFields && RuleConfig.loadFields(); } catch (e) {}
                try { RuleConfig.loadRules && RuleConfig.loadRules(); } catch (e) {}
            }
        } catch (e) {
            Toast.error('复制失败: ' + e.message);
        }
    },

    // ─── 导出 / 导入 ───

    async exportType(typeId) {
        try {
            const payload = await API.exportDocType(typeId);
            const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `doctype_${typeId}_${new Date().toISOString().slice(0,10)}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            Toast.success(`已导出 ${payload.fields.length} 个字段、${payload.rules.length} 条规则`);
        } catch (e) {
            Toast.error('导出失败: ' + e.message);
        }
    },

    triggerImport() {
        // 触发隐藏的 file input
        let input = document.getElementById('doctype-import-input');
        if (!input) {
            input = document.createElement('input');
            input.type = 'file';
            input.id = 'doctype-import-input';
            input.accept = '.json,application/json';
            input.style.display = 'none';
            input.addEventListener('change', (e) => this.handleImportFile(e));
            document.body.appendChild(input);
        }
        input.value = '';
        input.click();
    },

    async handleImportFile(event) {
        const file = event.target.files && event.target.files[0];
        if (!file) return;
        let payload;
        try {
            const text = await file.text();
            payload = JSON.parse(text);
        } catch (e) {
            Toast.error('JSON 解析失败: ' + e.message);
            return;
        }

        if (!payload || !payload.type_id) {
            Toast.error('无效的导出文件：缺少 type_id');
            return;
        }

        // 让用户选导入到新类型还是现有类型
        const targetTypeId = prompt(
            `导入到目标类型 ID（留空使用文件中的 "${payload.type_id}"）：`,
            payload.type_id
        );
        if (targetTypeId === null) return;
        const finalTarget = (targetTypeId || payload.type_id).trim();
        if (!/^[a-zA-Z0-9_-]+$/.test(finalTarget)) {
            Toast.error('类型 ID 只能包含英文/数字/_/-');
            return;
        }

        const onConflict = confirm('遇到同名字段/规则时，点击"确定"自动改名（追加"(副本)"）；点击"取消"跳过同名项')
            ? 'rename'
            : 'skip';

        try {
            const result = await API.importDocType(payload, {
                targetTypeId: finalTarget,
                createTypeIfMissing: true,
                onConflict,
            });
            const lines = [
                `目标类型: ${result.target_type_id}${result.created_type ? '（已新建）' : ''}`,
                `字段: 已导入 ${result.copied_fields} / 已跳过 ${result.skipped_fields}`,
                `规则: 已导入 ${result.copied_rules} / 已跳过 ${result.skipped_rules}`,
            ];
            Toast.success(lines.join('；'));
            if (result.missing_dependencies && result.missing_dependencies.length > 0) {
                alert('部分规则的依赖字段在目标类型中找不到，已自动剔除：\n' + result.missing_dependencies.join('\n'));
            }
            await this.refresh();
            if (typeof RuleConfig !== 'undefined') {
                try { RuleConfig.loadFields && RuleConfig.loadFields(); } catch (e) {}
                try { RuleConfig.loadRules && RuleConfig.loadRules(); } catch (e) {}
            }
        } catch (e) {
            Toast.error('导入失败: ' + e.message);
        }
    },
};

// HTML 转义辅助
function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeAttr(s) {
    return escapeHtml(s).replace(/`/g, '&#96;');
}
