/**
 * 自定义规则「格式化输出」字段树编辑器。
 * 数据驱动：内部维护 _schema 数组，编辑后同步到隐藏 textarea(.sb-json)与实时预览(.sb-preview)。
 * 示例 JSON 构建逻辑与后端 utils/output_schema.build_example_json 一致。
 */
const SchemaBuilder = {
    _schema: [],
    _editorEl: null,
    _hiddenEl: null,
    _previewEl: null,

    _newNode() {
        return { key: '', type: 'string', example: '', desc: '', children: [] };
    },

    _typeCn(t) {
        return { string: '字符串', number: '数字', boolean: '布尔', object: '对象', array: '数组' }[t] || t;
    },

    mount(areaId) {
        const area = document.getElementById(areaId);
        if (!area) return;
        this._editorEl = area.querySelector('.sb-editor');
        this._hiddenEl = area.querySelector('.sb-json');
        this._previewEl = area.querySelector('.sb-preview');
        let init = [];
        try { init = JSON.parse((this._hiddenEl && this._hiddenEl.value) || '[]'); } catch (e) { init = []; }
        this._schema = Array.isArray(init) ? init : [];
        this._render();
    },

    _parsePath(s) { return s.split('.').map(Number); },

    _nodeAt(path) {
        let list = this._schema, node = null;
        for (const i of path) {
            node = list[i];
            if (!node) return null;
            list = node.children || (node.children = []);
        }
        return node;
    },

    addRootField() { this._schema.push(this._newNode()); this._render(); },

    addChild(pathStr) {
        const n = this._nodeAt(this._parsePath(pathStr));
        if (!n) return;
        if (!n.children) n.children = [];
        n.children.push(this._newNode());
        this._render();
    },

    remove(pathStr) {
        const p = this._parsePath(pathStr);
        const idx = p.pop();
        const list = p.length ? this._nodeAt(p).children : this._schema;
        list.splice(idx, 1);
        this._render();
    },

    update(pathStr, field, value) {
        const node = this._nodeAt(this._parsePath(pathStr));
        if (!node) return;
        node[field] = value;
        if (field === 'type') {
            if ((value === 'object' || value === 'array') && !node.children) node.children = [];
            this._render();
        } else {
            this._sync();
        }
    },

    _render() {
        if (!this._editorEl) return;
        this._editorEl.innerHTML = this._renderNodes(this._schema, '');
        this._sync();
    },

    _renderNodes(nodes, prefix, depth) {
        depth = depth || 0;
        let html = '';
        (nodes || []).forEach((n, i) => {
            const path = prefix === '' ? String(i) : `${prefix}.${i}`;
            const isContainer = n.type === 'object' || n.type === 'array';
            const typeOptions = ['string', 'number', 'boolean', 'object', 'array']
                .map(t => `<option value="${t}" ${n.type === t ? 'selected' : ''}>${this._typeCn(t)}</option>`)
                .join('');
            html += `
                <div class="sb-node" style="border-left:2px solid var(--border-color,#e0e0e0);padding-left:8px;margin:4px 0;">
                    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
                        <input class="form-input" style="width:130px;flex:0 0 auto" placeholder="字段名"
                               value="${Utils.escapeHtml(n.key || '')}"
                               oninput="SchemaBuilder.update('${path}','key',this.value)">
                        <select class="form-select" style="width:110px;flex:0 0 auto;padding-right:22px;background-position:right 6px center"
                                onchange="SchemaBuilder.update('${path}','type',this.value)">${typeOptions}</select>
                        ${isContainer ? '' : `<input class="form-input" style="width:120px;flex:0 0 auto" placeholder="示例值"
                               value="${Utils.escapeHtml(n.example || '')}"
                               oninput="SchemaBuilder.update('${path}','example',this.value)">`}
                        <input class="form-input" style="flex:1;min-width:120px" placeholder="说明"
                               value="${Utils.escapeHtml(n.desc || '')}"
                               oninput="SchemaBuilder.update('${path}','desc',this.value)">
                        <button type="button" class="action-btn delete" title="删除"
                                onclick="SchemaBuilder.remove('${path}')">✕</button>
                    </div>
                    ${isContainer ? `<div style="margin-left:12px">
                        ${this._renderNodes(n.children || [], path, depth + 1)}
                        <button type="button" class="sb-add-btn sb-add-lvl${Math.min(depth + 1, 4)}" title="添加子字段"
                                onclick="SchemaBuilder.addChild('${path}')">+</button>
                    </div>` : ''}
                </div>`;
        });
        return html;
    },

    _sync() {
        const clean = (nodes) => (nodes || []).map(n => {
            const o = { key: n.key || '', type: n.type || 'string' };
            if (n.type === 'object' || n.type === 'array') {
                o.children = clean(n.children || []);
            } else if (n.example !== undefined && n.example !== '') {
                o.example = n.example;
            }
            if (n.desc) o.desc = n.desc;
            return o;
        });
        if (this._hiddenEl) this._hiddenEl.value = JSON.stringify(clean(this._schema));
        if (this._previewEl) {
            try {
                this._previewEl.textContent = JSON.stringify(this._buildExample(this._schema), null, 2);
            } catch (e) {
                this._previewEl.textContent = '(预览生成失败)';
            }
        }
    },

    _buildExample(schema) {
        const obj = {};
        (schema || []).forEach(n => { obj[n.key || ''] = this._buildNode(n); });
        return obj;
    },

    _buildNode(n) {
        if (n.type === 'object') return this._buildExample(n.children || []);
        if (n.type === 'array') {
            const ch = n.children || [];
            if (ch.length === 1 && ['string', 'number', 'boolean'].includes(ch[0].type)) {
                return [this._scalar(ch[0])];
            }
            return [this._buildExample(ch)];
        }
        return this._scalar(n);
    },

    _scalar(n) {
        if (n.example !== undefined && n.example !== '') return n.example;
        return n.type === 'number' ? 0 : n.type === 'boolean' ? false : '';
    },

    collect() {
        if (!this._hiddenEl) return null;
        try {
            const s = JSON.parse(this._hiddenEl.value || '[]');
            return Array.isArray(s) && s.length ? s : null;
        } catch (e) {
            return null;
        }
    },
};
