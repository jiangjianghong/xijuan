/**
 * 文档类型管理模块
 *
 * - 顶部下拉切换当前类型，localStorage 持久化
 * - 管理弹窗：搜索 / 范围筛选 / 分页；选用；新建（空白/派生/导入）；改名/导出/删除/模板标记
 *
 * 切换/选用类型后，刷新文件列表与字段/规则列表。
 */
const DocTypeManager = {
    selectorTypes: [],     // 选择器用（模板 + 默认）
    _currentName: '',      // 当前类型显示名（选用时记录，供选择器回显非模板类型）
    manage: {
        items: [], total: 0, page: 1, pageSize: 20,
        q: '', scope: 'all', selected: new Set(), menuOpenId: null,
    },
    typeForm: { mode: 'create' },
    _importPayload: null,

    async init() {
        await this.refresh();
        const sel = document.getElementById('doctype-selector');
        if (sel) sel.addEventListener('change', () => this.onSelectorChange());
        // 点击空白处关闭行内 ⋯ 菜单
        document.addEventListener('click', (e) => {
            if (this.manage.menuOpenId && !e.target.closest('.dt-menu')) this.closeRowMenu();
        });
    },

    async refresh() {
        try {
            this.selectorTypes = await API.listDocTypes({ scope: 'template' });
        } catch (e) {
            console.error('加载文档类型失败:', e);
            this.selectorTypes = [{ type_id: 'default', type_name: '默认类型', is_default: 1, file_count: 0 }];
        }
        this.renderSelector();
    },

    renderSelector() {
        const sel = document.getElementById('doctype-selector');
        if (!sel) return;
        const current = API.getCurrentTypeId();
        const list = (this.selectorTypes || []).slice();
        if (current && !list.some(t => t.type_id === current)) {
            list.push({ type_id: current, type_name: this._currentName || current, file_count: 0 });
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
        this._currentName = sel.options[sel.selectedIndex]?.text || sel.value;
        this._reloadCurrentType();
        Toast.success('已切换文档类型: ' + sel.options[sel.selectedIndex].text);
    },

    // 选用/切换当前类型后，刷新依赖「当前类型」的页面区域
    _reloadCurrentType() {
        if (typeof App !== 'undefined' && App.loadFileList) {
            try { App.loadFileList(); } catch (e) { console.warn(e); }
        }
        if (typeof RuleConfig !== 'undefined') {
            try { RuleConfig.loadFields && RuleConfig.loadFields(); } catch (e) { console.warn(e); }
            try { RuleConfig.loadRules && RuleConfig.loadRules(); } catch (e) { console.warn(e); }
        }
    },

    // ─── 管理弹窗 ───

    openManageDialog() {
        document.getElementById('doctype-modal-overlay').classList.add('active');
        this.manage.page = 1;
        this.manage.selected = new Set();
        this.manage.menuOpenId = null;
        this.loadManage();
    },
    closeManageDialog() {
        document.getElementById('doctype-modal-overlay').classList.remove('active');
    },

    async loadManage() {
        const m = this.manage;
        let data;
        try {
            data = await API.listDocTypes({ q: m.q, scope: m.scope, page: m.page, pageSize: m.pageSize });
        } catch (e) {
            Toast.error('加载失败: ' + e.message);
            return;
        }
        m.items = data.items || [];
        m.total = data.total || 0;
        this.renderManageTable();
    },

    // type_id -> type_name（当前页 + 选择器里的模板/默认），用于「来源」列显示名称
    _nameMap() {
        const map = {};
        (this.selectorTypes || []).forEach(t => { map[t.type_id] = t.type_name; });
        (this.manage.items || []).forEach(t => { map[t.type_id] = t.type_name; });
        return map;
    },

    renderManageTable() {
        const m = this.manage;
        const tbody = document.getElementById('doctype-table-body');
        if (!tbody) return;
        const nameMap = this._nameMap();
        tbody.innerHTML = m.items.map(t => {
            const isDef = t.is_default === 1;
            const isTpl = t.is_template === 1;
            const checked = m.selected.has(t.type_id) ? 'checked' : '';
            let tag;
            if (isDef) tag = '<span class="dt-tag dt-tag-default">默认</span>';
            else if (isTpl) tag = '<span class="dt-tag dt-tag-template">模板</span>';
            else if (t.parent_type_id) tag = '<span class="dt-tag dt-tag-copy">副本</span>';
            else tag = '<span class="dt-tag dt-tag-plain">普通</span>';
            const src = t.parent_type_id
                ? '←' + escapeHtml(nameMap[t.parent_type_id] || t.parent_type_id)
                : '—';
            const checkbox = isDef ? '' :
                `<input type="checkbox" ${checked} onclick="DocTypeManager.toggleSelect('${escapeAttr(t.type_id)}', this.checked)">`;
            const tplItem = isDef ? '' : (isTpl
                ? `<button onclick="DocTypeManager.demote('${escapeAttr(t.type_id)}')">取消模板</button>`
                : `<button onclick="DocTypeManager.promote('${escapeAttr(t.type_id)}')">设为模板</button>`);
            const renameItem = isDef ? '' :
                `<button onclick="DocTypeManager.openRenameForm('${escapeAttr(t.type_id)}')">改名</button>`;
            const delItem = isDef
                ? `<button disabled title="默认类型不可删除">删除</button>`
                : `<button class="danger" onclick="DocTypeManager.deleteType('${escapeAttr(t.type_id)}')">删除</button>`;
            const menuOpen = m.menuOpenId === t.type_id;
            return `<tr>
                <td>${checkbox}</td>
                <td>
                    <div class="dt-name">${escapeHtml(t.type_name)}</div>
                    <div class="dt-id-sub"><code>${escapeHtml(t.type_id)}</code></div>
                </td>
                <td>${tag}</td>
                <td>${src}</td>
                <td>${t.file_count || 0}</td>
                <td>${t.field_count || 0}</td>
                <td>${t.rule_count || 0}</td>
                <td class="dt-actions">
                    <button class="btn btn-secondary btn-sm" onclick="DocTypeManager.selectType('${escapeAttr(t.type_id)}')">选用</button>
                    <span class="dt-menu">
                        <button class="btn btn-ghost btn-sm" onclick="DocTypeManager.toggleRowMenu('${escapeAttr(t.type_id)}')">⋯</button>
                        <div class="dt-menu-pop" style="display:${menuOpen ? 'block' : 'none'};">
                            <button onclick="DocTypeManager.viewType('${escapeAttr(t.type_id)}')">查看配置</button>
                            <button onclick="DocTypeManager.openDeriveForm('${escapeAttr(t.type_id)}')">复制为新类型</button>
                            ${renameItem}
                            ${tplItem}
                            <button onclick="DocTypeManager.exportType('${escapeAttr(t.type_id)}')">导出</button>
                            ${delItem}
                        </div>
                    </span>
                </td>
            </tr>`;
        }).join('');

        const totalPages = Math.max(1, Math.ceil(m.total / m.pageSize));
        document.getElementById('dt-page-info').textContent = `${m.page} / ${totalPages}`;
        document.getElementById('dt-total').textContent = `共 ${m.total} 个`;
        this._renderBatchBar();
        const allBox = document.getElementById('dt-check-all');
        if (allBox) allBox.checked = m.items.length > 0 &&
            m.items.filter(t => t.is_default !== 1).every(t => m.selected.has(t.type_id));
    },

    _renderBatchBar() {
        const n = this.manage.selected.size;
        const bar = document.getElementById('dt-batch-bar');
        if (bar) bar.style.display = n > 0 ? 'flex' : 'none';
        const cnt = document.getElementById('dt-selected-count');
        if (cnt) cnt.textContent = `已选 ${n} 项`;
    },

    onSearchInput() {
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => {
            this.manage.q = document.getElementById('dt-search').value.trim();
            this.manage.page = 1;
            this.loadManage();
        }, 300);
    },
    onScopeChange() {
        this.manage.scope = document.getElementById('dt-scope').value;
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
        this._renderBatchBar();
    },
    toggleSelectAll(checked) {
        this.manage.items.filter(t => t.is_default !== 1).forEach(t => {
            if (checked) this.manage.selected.add(t.type_id); else this.manage.selected.delete(t.type_id);
        });
        this.renderManageTable();
    },

    toggleRowMenu(typeId) {
        this.manage.menuOpenId = (this.manage.menuOpenId === typeId) ? null : typeId;
        this.renderManageTable();
    },
    closeRowMenu() {
        if (this.manage.menuOpenId !== null) {
            this.manage.menuOpenId = null;
            this.renderManageTable();
        }
    },

    // 选用 = 设为当前类型（任何类型都可，打破"只有模板能用"的死循环）
    async selectType(typeId) {
        const t = this.manage.items.find(x => x.type_id === typeId);
        this._currentName = t ? t.type_name : typeId;
        API.setCurrentTypeId(typeId);
        await this.refresh();
        this._reloadCurrentType();
        Toast.success('已选用文档类型：' + this._currentName);
        this.closeManageDialog();
    },

    async promote(typeId) {
        this.closeRowMenu();
        try { await API.promoteType(typeId); Toast.success('已设为模板'); await this.loadManage(); await this.refresh(); }
        catch (e) { Toast.error('操作失败: ' + e.message); }
    },
    async demote(typeId) {
        this.closeRowMenu();
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

    async deleteType(typeId) {
        this.closeRowMenu();
        const t = this.manage.items.find(x => x.type_id === typeId);
        if (!t) return;
        const hasData = (t.file_count || 0) + (t.field_count || 0) + (t.rule_count || 0) > 0;
        let force = false;
        if (hasData) {
            if (!confirm(`类型 "${t.type_name}" 下有 ${t.file_count} 个文件、${t.field_count} 个字段、${t.rule_count} 条规则。\n\n确认级联删除全部数据？此操作不可撤销！`)) return;
            force = true;
        } else {
            if (!confirm(`确认删除类型 "${t.type_name}"？`)) return;
        }
        try {
            await API.deleteDocType(typeId, force);
            Toast.success('类型已删除');
            if (API.getCurrentTypeId() === typeId) API.setCurrentTypeId('default');
            this.manage.selected.delete(typeId);
            await this.loadManage();
            await this.refresh();
        } catch (e) { Toast.error('删除失败: ' + e.message); }
    },

    async viewType(typeId) {
        this.closeRowMenu();
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

    async exportType(typeId) {
        this.closeRowMenu();
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
        } catch (e) { Toast.error('导出失败: ' + e.message); }
    },

    // ─── 从其他类型复制配置（灌入当前类型；与「派生新类型」方向相反，二者并存） ───

    openCopyDialog() {
        const current = API.getCurrentTypeId();
        const pool = this.selectorTypes || [];
        const currentType = pool.find(t => t.type_id === current);
        document.getElementById('copy-target-display').value =
            currentType ? `${currentType.type_name} (${currentType.type_id})` : current;

        const candidates = pool.filter(t => t.type_id !== current);
        if (candidates.length === 0) { Toast.error('没有其他文档类型可供复制'); return; }
        const sourceSel = document.getElementById('copy-source-select');
        sourceSel.innerHTML = candidates.map(t =>
            `<option value="${escapeHtml(t.type_id)}">${escapeHtml(t.type_name)} (字段 ${t.field_count || 0}, 规则 ${t.rule_count || 0})</option>`
        ).join('');

        document.getElementById('copy-fields-flag').checked = true;
        document.getElementById('copy-rules-flag').checked = true;
        document.getElementById('copy-conflict').value = 'rename';
        this.onSourceTypeChange();
        document.getElementById('copy-modal-overlay').classList.add('active');
    },
    closeCopyDialog() { document.getElementById('copy-modal-overlay').classList.remove('active'); },

    onSourceTypeChange() {
        const tid = document.getElementById('copy-source-select').value;
        const t = (this.selectorTypes || []).find(x => x.type_id === tid);
        const summary = document.getElementById('copy-source-summary');
        summary.textContent = t
            ? `源类型 "${t.type_name}" 当前有 ${t.field_count || 0} 个字段、${t.rule_count || 0} 条规则。复制后两份完全独立，目标类型修改不会影响源。`
            : '';
    },

    async executeCopy() {
        const target = API.getCurrentTypeId();
        const source = document.getElementById('copy-source-select').value;
        const copyFields = document.getElementById('copy-fields-flag').checked;
        const copyRules = document.getElementById('copy-rules-flag').checked;
        const onConflict = document.getElementById('copy-conflict').value;

        if (!source) { Toast.error('请选择源类型'); return; }
        if (!copyFields && !copyRules) { Toast.error('请至少勾选一种内容'); return; }

        const payload = { source_type_id: source, on_conflict: onConflict };
        if (!copyFields) payload.field_ids = [];   // 留空=全部；显式 [] = 一个不选
        if (!copyRules) payload.rule_ids = [];

        try {
            const result = await API.copyConfigs(target, payload);
            Toast.success(`复制完成：字段 ${result.copied_fields} 复制 / ${result.skipped_fields} 跳过；规则 ${result.copied_rules} 复制 / ${result.skipped_rules} 跳过`);
            if (result.missing_dependencies && result.missing_dependencies.length > 0) {
                alert('部分规则的依赖字段在目标类型中不存在，已自动剔除：\n' + result.missing_dependencies.join('\n'));
            }
            this.closeCopyDialog();
            await this.refresh();
            if (typeof RuleConfig !== 'undefined') {
                try { RuleConfig.loadFields && RuleConfig.loadFields(); } catch (e) {}
                try { RuleConfig.loadRules && RuleConfig.loadRules(); } catch (e) {}
            }
        } catch (e) { Toast.error('复制失败: ' + e.message); }
    },

    // ─── 类型表单：新建（空白/派生/导入）/ 改名 ───

    openCreateForm() { this._openTypeForm({ mode: 'create', sourceMode: 'blank' }); },
    openDeriveForm(sourceId) { this._openTypeForm({ mode: 'create', sourceMode: 'derive', sourceId }); },
    openRenameForm(typeId) {
        const t = this.manage.items.find(x => x.type_id === typeId);
        this._openTypeForm({ mode: 'edit', typeId, typeName: t ? t.type_name : '' });
    },

    _openTypeForm({ mode, sourceMode, sourceId, typeId, typeName }) {
        this.closeRowMenu();
        this.typeForm = { mode };
        this._importPayload = null;
        const isEdit = mode === 'edit';
        document.getElementById('typeform-title').textContent = isEdit ? '重命名类型' : '新建文档类型';
        const idEl = document.getElementById('typeform-id');
        const nameEl = document.getElementById('typeform-name');
        idEl.value = isEdit ? typeId : '';
        idEl.disabled = isEdit;
        nameEl.value = isEdit ? (typeName || '') : '';

        // 来源区仅新建可见
        document.getElementById('typeform-source-block').style.display = isEdit ? 'none' : '';
        document.getElementById('typeform-derive-block').style.display = 'none';
        document.getElementById('typeform-import-block').style.display = 'none';
        document.getElementById('typeform-conflict-block').style.display = 'none';

        if (!isEdit) {
            // 源类型下拉：当前页全部类型（含默认/模板），排除将要新建的（提交时再校验不等于自身）
            const pool = this.manage.items.length ? this.manage.items : this.selectorTypes;
            const srcSel = document.getElementById('typeform-source-type');
            srcSel.innerHTML = pool.map(t =>
                `<option value="${escapeHtml(t.type_id)}">${escapeHtml(t.type_name)} (${escapeHtml(t.type_id)})</option>`
            ).join('');
            document.getElementById('typeform-source-mode').value = sourceMode || 'blank';
            if (sourceId) srcSel.value = sourceId;
            document.getElementById('typeform-copy-fields').checked = true;
            document.getElementById('typeform-copy-rules').checked = true;
            document.getElementById('typeform-conflict').value = 'rename';
            document.getElementById('typeform-import-file').value = '';
            document.getElementById('typeform-import-hint').textContent = '';
            this.onCreateSourceModeChange();
        }
        document.getElementById('typeform-modal-overlay').classList.add('active');
    },
    closeTypeForm() { document.getElementById('typeform-modal-overlay').classList.remove('active'); },

    onCreateSourceModeChange() {
        const mode = document.getElementById('typeform-source-mode').value;
        document.getElementById('typeform-derive-block').style.display = mode === 'derive' ? '' : 'none';
        document.getElementById('typeform-import-block').style.display = mode === 'import' ? '' : 'none';
        document.getElementById('typeform-conflict-block').style.display = (mode === 'derive' || mode === 'import') ? '' : 'none';
    },

    async submitTypeForm() {
        const mode = this.typeForm.mode;
        const id = (document.getElementById('typeform-id').value || '').trim();
        const name = (document.getElementById('typeform-name').value || '').trim();
        if (!name) { Toast.error('类型名称必填'); return; }

        // 改名：仅 upsert 名称
        if (mode === 'edit') {
            try {
                await API.saveDocType({ type_id: id, type_name: name, enabled: 1 });
                Toast.success('已重命名');
                this.closeTypeForm();
                await this.loadManage();
                await this.refresh();
            } catch (e) { Toast.error('保存失败: ' + e.message); }
            return;
        }

        // 新建
        if (!/^[a-zA-Z0-9_-]+$/.test(id)) { Toast.error('类型 ID 只能包含英文/数字/_/-'); return; }
        const sourceMode = document.getElementById('typeform-source-mode').value;
        const onConflict = document.getElementById('typeform-conflict').value;

        let importPayload = null;
        if (sourceMode === 'import') {
            const file = document.getElementById('typeform-import-file').files[0];
            if (!file) { Toast.error('请选择 JSON 文件'); return; }
            try {
                importPayload = JSON.parse(await file.text());
            } catch (e) { Toast.error('JSON 解析失败: ' + e.message); return; }
            if (!importPayload || !importPayload.type_id) { Toast.error('无效的导出文件：缺少 type_id'); return; }
        }
        let source = null;
        if (sourceMode === 'derive') {
            source = document.getElementById('typeform-source-type').value;
            if (!source) { Toast.error('请选择源类型'); return; }
            if (source === id) { Toast.error('源类型不能是自己'); return; }
        }

        try {
            // 1. 建空白类型（带目标名称；upsert 语义）
            await API.saveDocType({ type_id: id, type_name: name, enabled: 1 });

            // 2. 按来源灌配置
            if (sourceMode === 'derive') {
                const payload = { source_type_id: source, on_conflict: onConflict };
                if (!document.getElementById('typeform-copy-fields').checked) payload.field_ids = [];
                if (!document.getElementById('typeform-copy-rules').checked) payload.rule_ids = [];
                const r = await API.copyConfigs(id, payload);
                Toast.success(`已派生：字段 ${r.copied_fields} / 规则 ${r.copied_rules}`);
                if (r.missing_dependencies && r.missing_dependencies.length)
                    alert('部分规则依赖字段缺失，已剔除：\n' + r.missing_dependencies.join('\n'));
            } else if (sourceMode === 'import') {
                const r = await API.importDocType(importPayload, { targetTypeId: id, createTypeIfMissing: false, onConflict });
                Toast.success(`已导入：字段 ${r.copied_fields} / 规则 ${r.copied_rules}`);
                if (r.missing_dependencies && r.missing_dependencies.length)
                    alert('部分规则依赖字段缺失，已剔除：\n' + r.missing_dependencies.join('\n'));
            } else {
                Toast.success('类型已创建');
            }

            this.closeTypeForm();
            await this.loadManage();
            await this.refresh();
        } catch (e) { Toast.error('创建失败: ' + e.message); }
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
