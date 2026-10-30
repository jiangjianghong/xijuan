/** 接口访问控制：列表只保存密钥提示，完整密钥仅留在一次性展示框。 */
const AccessControl = {
    payload: null,
    tab: 'keys',
    busy: false,
    root: null,
    generation: 0,
    async request(path = '', options = {}) {
        return (await API.request('/settings/access' + path, options)).data;
    },
    mount() {
        this.unmount();
        this.root = document.getElementById('access-settings');
        if (this.root) {
            this.root.querySelectorAll('button').forEach(button => button.disabled = false);
            return this.reload();
        }
    },
    unmount() {
        this.clearKey();
        this.generation += 1;
        this.root = null;
        this.payload = null;
        this.busy = false;
    },
    isCurrent(generation) {
        return generation === this.generation && this.root?.isConnected && !Auth.locked
            && this.root === document.getElementById('access-settings')
            && document.getElementById('settings-modal-overlay').classList.contains('active');
    },
    async reload() {
        if (!this.root) return;
        const generation = this.generation;
        document.getElementById('access-body').textContent = '正在加载访问控制…';
        try {
            const payload = await this.request();
            if (!this.isCurrent(generation)) return;
            this.payload = payload;
            this.error();
            this.render();
        } catch (error) {
            if (!this.isCurrent(generation)) return;
            document.getElementById('access-body').innerHTML = '<button type="button" class="access-row-action" onclick="AccessControl.reload()">重新加载访问控制</button>';
            this.error(error.message);
            if (error.status === 401) this.unmount();
        }
    },
    error(message = '') { document.getElementById('access-error').textContent = message; },
    select(tab) {
        if (this.busy || !this.payload) return;
        this.tab = tab;
        this.error();
        this.render();
    },
    render() {
        const esc = value => Utils.escapeHtml(String(value ?? ''));
        this.root.querySelectorAll('[data-access-tab]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.accessTab === this.tab)));
        const keys = this.tab === 'keys';
        const entries = keys ? this.payload.api_keys : this.payload.ip_allowlist;
        const rows = entries.map(entry => {
            const expired = keys && entry.expires_at && new Date(entry.expires_at) <= new Date();
            const status = expired ? '已过期' : entry.enabled ? '启用' : '已禁用';
            return `<tr><td>${esc(entry.name)}</td><td>${esc(keys ? entry.hint : entry.network)}</td>
                ${keys ? `<td>${entry.expires_at ? esc(new Date(entry.expires_at).toLocaleString()) : '永久'}</td>` : ''}
                <td>${status}</td><td>
                <button type="button" class="access-row-action" onclick="AccessControl.toggle('${entry.id}', ${!entry.enabled})">${entry.enabled ? '禁用' : '启用'}</button>
                ${keys && !expired ? `<button type="button" class="access-row-action" onclick="AccessControl.rotate('${entry.id}')">重新生成</button>` : ''}
                <button type="button" class="access-row-action danger" onclick="AccessControl.remove('${entry.id}')">删除</button>
                </td></tr>`;
        }).join('');
        document.getElementById('access-body').innerHTML = `
            <div class="access-create" role="group" aria-label="${keys ? '创建 API Key' : '添加白名单'}" onkeydown="AccessControl.handleCreateKeydown(event)">
                <label>调用方名称<input form="" name="name" required maxlength="80" placeholder="例如：财务系统"></label>
                ${keys ? '<label>有效期（留空为永久）<input form="" name="expiry" type="datetime-local"></label>' : '<label>IP 地址或 CIDR 网段<input form="" name="network" required maxlength="80" placeholder="例如：192.168.1.10"></label>'}
                <button class="auth-primary" type="button" onclick="AccessControl.create()">${keys ? '创建 API Key' : '添加白名单'}</button>
            </div>
            ${entries.length ? `<div class="access-table-wrap"><table class="access-table"><thead><tr><th>调用方</th><th>${keys ? '密钥标识' : '来源范围'}</th>${keys ? '<th>有效期</th>' : ''}<th>状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="access-empty">${keys ? '尚未创建 API Key。为每个调用程序创建独立密钥，便于单独停用。' : '尚未添加白名单。调用程序需要携带有效 API Key。'}</div>`}
            <p class="access-note">${keys ? '完整密钥只在创建或重新生成时显示一次。程序通过 Authorization: Bearer &lt;key&gt; 调用。' : '支持 IPv4、IPv6 和 CIDR 网段，不限制来源端口。范围内所有程序共享免密调用权限；共用出口 IP 的程序无法区分。'}</p>`;
    },
    handleCreateKeydown(event) {
        if (event.key !== 'Enter' || event.isComposing || event.target.tagName !== 'INPUT') return;
        event.preventDefault();
        this.create();
    },
    async create() {
        if (this.busy || !this.payload) return;
        // 独立校验本分组；form="" 避免空白输入阻挡普通设置保存或触发外层表单。
        const controls = this.root.querySelector('.access-create');
        for (const input of controls.querySelectorAll('input')) {
            if (!input.reportValidity()) return;
        }
        const values = { name: controls.querySelector('[name="name"]').value.trim() };
        if (this.tab === 'keys') {
            const expiry = controls.querySelector('[name="expiry"]').value;
            values.expires_at = expiry ? new Date(expiry).toISOString() : null;
        } else values.network = controls.querySelector('[name="network"]').value.trim();
        await this.mutate('/' + this.tab, 'POST', values);
    },
    async toggle(id, enabled) { await this.mutate(`/${this.tab}/${id}`, 'PATCH', { enabled }); },
    async remove(id) {
        if (!confirm('删除后，该条目的调用权限将立即失效。确定删除吗？')) return;
        await this.mutate(`/${this.tab}/${id}`, 'DELETE');
    },
    async rotate(id) {
        if (!confirm('重新生成后旧密钥立即失效，原有效期保持不变。确定继续吗？')) return;
        await this.mutate(`/keys/${id}/rotate`, 'POST');
    },
    async mutate(path, method, values = {}) {
        const generation = this.generation;
        if (this.busy || !this.payload || !this.isCurrent(generation)) return;
        this.busy = true;
        this.error();
        this.root.querySelectorAll('button').forEach(button => button.disabled = true);
        try {
            const { key, ...payload } = await this.request(path, {
                method, body: JSON.stringify({ base_version: this.payload.version, ...values }),
            });
            if (!this.isCurrent(generation)) return;
            this.payload = payload;
            this.clearKey();
            this.render();
            if (key) {
                document.getElementById('access-key-value').value = key;
                document.getElementById('access-key-panel').hidden = false;
                document.getElementById('access-key-value').focus();
                document.getElementById('access-key-value').select();
            }
        } catch (error) {
            if (!this.isCurrent(generation)) return;
            if (error.status === 409) await this.reload();
            if (!this.isCurrent(generation)) return;
            this.error(error.status === 409 ? '配置已变化，已加载最新列表，请检查后重新操作。' : error.message);
            if (error.status === 401) this.unmount();
        } finally {
            if (generation === this.generation) {
                this.busy = false;
                this.root?.querySelectorAll('button').forEach(button => button.disabled = false);
            }
        }
    },
    clearKey() {
        const field = document.getElementById('access-key-value');
        if (field) field.value = '';
        const status = document.getElementById('access-copy-status');
        if (status) status.textContent = '';
        const panel = document.getElementById('access-key-panel');
        if (panel) panel.hidden = true;
    },
    async copyKey() {
        const generation = this.generation;
        if (!this.isCurrent(generation)) return;
        const field = document.getElementById('access-key-value');
        try {
            await navigator.clipboard.writeText(field.value);
            if (!this.isCurrent(generation) || !field.value) return;
            document.getElementById('access-copy-status').textContent = '已复制';
        } catch (_) {
            if (!this.isCurrent(generation) || !field.value) return;
            field.focus(); field.select();
            document.getElementById('access-copy-status').textContent = '请按 Ctrl+C 或长按复制';
        }
    },
};
