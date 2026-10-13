/** 正式业务调用日志：横条概要、展开详情与结果 JSON 下载。 */
const InvocationLogs = {
    page: 1, pageSize: 20, total: 0, active: false, timer: null, kind: '',
    expanded: new Set(), details: new Map(), generation: 0, loading: false,
    kinds: { file_parse: '文件上传解析', file_retry: '文件阶段重试', independent_analysis: '独立逻辑分析' },
    statuses: { queued: '排队中', running: '处理中', complete: '已完成', partial_failed: '部分失败', failed: '失败' },
    stages: { parsing: '解析', tableing: '表格校验', chunking: '分块', embedding: '向量化', extracting: '字段抽取', analyzing: '逻辑分析' },

    init() {
        if (this.list) return;
        this.list = document.getElementById('invocation-list');
        this.dialog = document.getElementById('invocation-result-dialog');
        if (!this.list) return;
        document.getElementById('invocation-filters').addEventListener('submit', event => {
            event.preventDefault(); this.page = 1; this.expanded.clear(); this.load();
        });
        document.getElementById('invocation-status').addEventListener('change', () => {
            this.page = 1; this.expanded.clear(); this.load();
        });
        document.getElementById('invocation-kind-tabs').addEventListener('click', event => {
            const tab = event.target.closest('[data-invocation-kind]');
            if (!tab) return;
            this.kind = tab.dataset.invocationKind || '';
            document.querySelectorAll('#invocation-kind-tabs [data-invocation-kind]').forEach(item => {
                const active = item === tab;
                item.classList.toggle('is-active', active);
                item.setAttribute('aria-pressed', String(active));
            });
            this.page = 1; this.expanded.clear(); this.load();
        });
        document.getElementById('invocation-refresh').addEventListener('click', () => this.load());
        document.getElementById('invocation-prev').addEventListener('click', () => this.changePage(-1));
        document.getElementById('invocation-next').addEventListener('click', () => this.changePage(1));
        this.list.addEventListener('click', event => {
            const resultButton = event.target.closest('[data-invocation-result]');
            if (resultButton) { this.openResult(resultButton.dataset.invocationResult); return; }
            const toggle = event.target.closest('[data-invocation-toggle]');
            if (toggle) this.toggle(toggle.dataset.invocationToggle);
        });
        document.getElementById('invocation-result-close').addEventListener('click', () => this.dialog.close());
        this.dialog.addEventListener('click', event => { if (event.target === this.dialog) this.dialog.close(); });
        this.dialog.addEventListener('close', () => document.body.classList.remove('invocation-modal-open'));
        document.getElementById('invocation-result-tabs').addEventListener('click', event => {
            const button = event.target.closest('[data-result-part]');
            if (button) this.showResultPart(button.dataset.resultPart);
        });
    },
    activate() {
        if (!this.list) this.init();
        this.active = true;
        this.load();
        clearInterval(this.timer);
        this.timer = setInterval(() => { if (!document.hidden && !this.loading) this.load(true); }, 10000);
    },
    deactivate() {
        this.active = false; clearInterval(this.timer); this.timer = null;
        if (this.dialog?.open) this.dialog.close();
    },
    changePage(delta) {
        const pages = Math.max(1, Math.ceil(this.total / this.pageSize));
        this.page = Math.max(1, Math.min(pages, this.page + delta));
        this.expanded.clear(); this.load();
    },
    async request(url) {
        const response = await fetch(url);
        const body = await response.json();
        if (!response.ok || body.code !== 200) throw new Error(body.detail || body.message || '加载调用日志失败');
        return body.data;
    },
    async load(silent = false) {
        if (silent && this.dialog?.open) return;
        const generation = ++this.generation;
        this.loading = true; this.list.setAttribute('aria-busy', 'true');
        const params = new URLSearchParams({ page: this.page, page_size: this.pageSize });
        [['kind', null], ['status', 'invocation-status'], ['q', 'invocation-query']].forEach(([key, id]) => {
            const value = id ? document.getElementById(id).value.trim() : (this.kind || ''); if (value) params.set(key, value);
        });
        try {
            const data = await this.request(`/invocations?${params}`);
            if (generation !== this.generation) return;
            this.total = data.total;
            this.updateRows(data.items);
            document.getElementById('invocation-total').textContent = `共 ${data.total} 次调用`;
            const pages = Math.max(1, Math.ceil(data.total / this.pageSize));
            document.getElementById('invocation-page-number').textContent = `${this.page} / ${pages}`;
            document.getElementById('invocation-prev').disabled = this.page <= 1;
            document.getElementById('invocation-next').disabled = this.page >= pages;
            this.icons();
            await Promise.allSettled([...this.expanded].filter(id => {
                const item = data.items.find(item => item.log_id === id);
                const cached = this.details.get(id);
                return !cached || (item && (item.status !== cached.status || item.updated_at !== cached.updated_at));
            }).map(id => this.loadDetail(id, generation)));
        } catch (error) {
            if (generation !== this.generation) return;
            if (!this.list.children.length) this.list.innerHTML = '<div class="invocation-empty">调用日志加载失败，请点击刷新重试。</div>';
            if (!silent) window.Toast?.error?.(error.message);
        } finally {
            if (generation === this.generation) { this.loading = false; this.list.setAttribute('aria-busy', 'false'); }
        }
    },
    updateRows(items) {
        // 展开行原地保留：输入区的开关、选中内容和内部滚动均不重置。
        const rows = [...this.list.querySelectorAll('.invocation-row')];
        const existing = new Map(rows.map(row => [row.dataset.logId, row]));
        const anchor = rows.find(row => this.expanded.has(row.dataset.logId) && row.getBoundingClientRect().bottom > 0);
        const anchorTop = anchor?.getBoundingClientRect().top;
        const scrollY = window.scrollY;
        const incoming = new Map(items.map(item => [item.log_id, item]));
        const orderedIds = items.map(item => item.log_id);
        // 新调用可能把阅读中的记录挤出当前分页，仍保留到用户主动收起。
        rows.forEach((row, index) => {
            if (this.expanded.has(row.dataset.logId) && !incoming.has(row.dataset.logId)) {
                orderedIds.splice(Math.min(index, orderedIds.length), 0, row.dataset.logId);
            }
        });
        this.list.querySelectorAll('.invocation-empty').forEach(node => node.remove());
        const desired = new Set(orderedIds);
        rows.forEach(row => { if (!desired.has(row.dataset.logId)) row.remove(); });
        let cursor = this.list.firstElementChild;
        orderedIds.forEach(id => {
            let row = existing.get(id);
            const item = incoming.get(id);
            if (!row) {
                const template = document.createElement('template');
                template.innerHTML = this.renderRow(item);
                row = template.content.firstElementChild;
            } else if (item) {
                const template = document.createElement('template');
                template.innerHTML = this.renderRow(item);
                // 只替换概要中的信息单元，保留按钮焦点和详情 DOM。
                ['.invocation-row-main', '.invocation-row-time', '.invocation-row-state'].forEach(selector => {
                    row.querySelector(selector).innerHTML = template.content.querySelector(selector).innerHTML;
                });
            }
            const open = this.expanded.has(id);
            row.classList.toggle('is-open', open);
            row.querySelector('[data-invocation-toggle]').setAttribute('aria-expanded', String(open));
            row.querySelector('.invocation-detail').hidden = !open;
            if (row !== cursor) this.list.insertBefore(row, cursor);
            cursor = row.nextElementSibling;
        });
        if (!orderedIds.length) this.list.innerHTML = '<div class="invocation-empty">暂无调用日志。提交文件解析或独立逻辑分析后，记录将显示在这里。</div>';
        const retained = new Set(orderedIds);
        this.details.forEach((_, id) => { if (!retained.has(id)) this.details.delete(id); });
        if (anchor?.isConnected) this.restoreAnchor(anchor, anchorTop, scrollY);
        else if (!this.expanded.size) this.list.style.paddingBottom = '';
    },
    restoreAnchor(element, previousTop, previousScroll) {
        const offset = element.getBoundingClientRect().top - previousTop;
        if (Math.abs(offset) <= 1) return;
        const target = Math.max(0, previousScroll + offset);
        const maxScroll = Math.max(0, document.scrollingElement.scrollHeight - window.innerHeight);
        // 阅读中的行接近列表末尾时，补充底部空间以避免浏览器截断滚动补偿。
        if (target > maxScroll) {
            const padding = parseFloat(this.list.style.paddingBottom) || 0;
            this.list.style.paddingBottom = `${padding + target - maxScroll}px`;
        }
        window.scrollTo({ top: target, behavior: 'instant' });
    },
    renderRow(item) {
        const isFile = item.kind !== 'independent_analysis';
        const title = isFile ? item.file_name : (item.biz_ids.join('、') || '独立逻辑分析');
        const id = item.log_id, open = this.expanded.has(id);
        const cachedDetail = this.details.get(id);
        return `<article class="invocation-row ${open ? 'is-open' : ''}" data-log-id="${this.escape(id)}">
            <button type="button" class="invocation-row-summary" data-invocation-toggle="${this.escape(id)}" aria-expanded="${open}" aria-controls="invocation-detail-${this.escape(id)}">
                <i data-lucide="chevron-right" class="invocation-chevron"></i>
                <span class="invocation-row-main"><span class="invocation-kind">${this.escape(this.kinds[item.kind] || item.kind)}${item.stage ? ` · ${this.escape(this.stages[item.stage] || item.stage)}` : ''}</span><strong title="${this.escape(title)}">${this.escape(title)}</strong><code>${this.escape(item.file_id || item.task_id || id)}</code></span>
                <span class="invocation-row-time"><span>${this.time(item.created_at)}</span><small>${isFile ? this.size(item.file_size) : `${item.item_count} 组输入`} · ${this.escape(item.mode || '—')}</small></span>
                <span class="invocation-row-state"><span class="invocation-badge status-${this.escape(item.status)}">${this.escape(this.statuses[item.status] || item.status)}</span><small>总耗时 ${this.duration(item.duration_ms)}</small></span>
            </button><div id="invocation-detail-${this.escape(id)}" class="invocation-detail" ${open ? '' : 'hidden'}>${open && cachedDetail ? this.renderDetail(cachedDetail) : '正在加载详情…'}</div>
        </article>`;
    },
    async toggle(id) {
        const row = this.list.querySelector(`[data-log-id="${CSS.escape(id)}"]`);
        if (!row) return;
        const open = !this.expanded.has(id);
        if (open) this.expanded.add(id); else this.expanded.delete(id);
        row.classList.toggle('is-open', open);
        row.querySelector('[data-invocation-toggle]').setAttribute('aria-expanded', String(open));
        row.querySelector('.invocation-detail').hidden = !open;
        if (open) await this.loadDetail(id, this.generation);
        else this.details.delete(id);
    },
    async loadDetail(id, generation) {
        try {
            const detail = await this.request(`/invocations/${encodeURIComponent(id)}`);
            if (generation !== this.generation || !this.expanded.has(id)) return;
            const container = document.getElementById(`invocation-detail-${id}`);
            if (container) {
                if (this.details.has(id)) {
                    // 运行中状态可更新，原始输入区域始终保留，避免影响正在阅读的内容。
                    const input = container.querySelector('.invocation-inputs');
                    const inputTop = input?.getBoundingClientRect().top;
                    const scrollY = window.scrollY;
                    const updated = document.createElement('div');
                    updated.innerHTML = this.renderDetail(detail);
                    ['.invocation-detail-top', '.invocation-error', '.invocation-stage-timeline'].forEach(selector => {
                        const before = container.querySelector(selector);
                        const after = updated.querySelector(selector);
                        if (before && after) before.replaceWith(after);
                        else if (before) before.remove();
                        else if (after) container.insertBefore(after, input);
                    });
                    if (input) this.restoreAnchor(input, inputTop, scrollY);
                } else container.innerHTML = this.renderDetail(detail);
                this.icons();
            }
            this.details.set(id, detail);
        } catch (error) {
            const container = document.getElementById(`invocation-detail-${id}`);
            if (generation === this.generation && container && !this.details.has(id)) container.textContent = `${error.message}，请收起后重新展开。`;
        }
    },
    renderDetail(detail) {
        const stages = Object.entries(detail.stage_times || {});
        const stageHtml = stages.length ? `<div class="invocation-stage-timeline">${stages.map(([key, stage]) => {
            const elapsed = stage.duration_ms ?? (stage.start && stage.end ? new Date(stage.end) - new Date(stage.start) : null);
            return `<div class="invocation-stage ${stage.end ? 'is-done' : stage.start ? 'is-running' : ''}"><strong>${this.escape(this.stages[key] || key)}</strong><span>${elapsed != null ? this.duration(elapsed) : stage.start ? '进行中 / 未结束' : '未执行'}</span><small>开始 ${this.time(stage.start)}</small><small>结束 ${this.time(stage.end)}</small></div>`;
        }).join('')}</div>` : '';
        const items = detail.request_input?.body?.items || [];
        const itemsHtml = items.length ? `<div class="invocation-items">${items.map(item => `<div><strong>${this.escape(item.biz_id)}</strong><span>类型 ${this.escape(item.type_id || '按文件类型')}</span>${item.file_id ? `<code>文件 ${this.escape(item.file_id)}</code>` : `<span>${Object.keys(item.field_values || {}).length} 个输入字段</span>`}<span>${item.rule_ids == null ? '全部启用规则' : `${item.rule_ids.length} 条指定规则`}</span></div>`).join('')}</div>` : '';
        return `<div class="invocation-detail-top"><div class="invocation-metadata">${this.meta('日志 ID', detail.log_id)}${this.meta('文档类型 ID', detail.type_id || (items.length > 1 ? '多类型批次' : '按文件类型'))}${this.meta('调用时间', this.time(detail.created_at))}${this.meta('开始处理', this.time(detail.started_at))}${this.meta('完成时间', this.time(detail.finished_at))}${this.meta('执行耗时', detail.started_at && detail.finished_at ? this.duration(new Date(detail.finished_at) - new Date(detail.started_at)) : '—')}${detail.source ? this.meta('输入来源', detail.source === 'file' ? '文件抽取结果' : '外部字段值') : ''}</div><button type="button" class="runtime-log-btn primary" data-invocation-result="${this.escape(detail.log_id)}" ${detail.result == null ? 'disabled' : ''}><i data-lucide="braces" class="w-4 h-4"></i>查看结果</button></div>
            ${detail.error ? `<div class="invocation-error">${this.escape(detail.error)}</div>` : ''}${stageHtml}${itemsHtml}
            <div class="invocation-inputs"><details open><summary>接口原始输入</summary><pre class="invocation-json" tabindex="0">${this.escape(JSON.stringify(this.originalInput(detail), null, 2))}</pre></details></div>`;
    },
    originalInput(detail) {
        const input = detail.request_input || {};
        // 独立分析直接显示原始 JSON 请求体，不重复展示转义后的 raw_body。
        if (input.body && !(input.query || []).length) return input.body;
        const { raw_body, ...original } = input;
        // 兼容旧记录：旧版存储时直接略去了 multipart 的文件字段。
        if (detail.kind === 'file_parse') {
            return { ...original, file: 'xxx', ...(original.form ? {
                form: original.form.map(([key, value]) => [key, key === 'file' ? 'xxx' : value]),
            } : {}) };
        }
        return original;
    },
    async openResult(id) {
        try {
            const detail = await this.request(`/invocations/${encodeURIComponent(id)}`);
            if (detail.result == null) throw new Error('当前调用尚无结果');
            this.result = detail.result;
            document.getElementById('invocation-result-subtitle').textContent = detail.file_name || detail.task_id || id;
            document.getElementById('invocation-result-download').href = `/invocations/${encodeURIComponent(id)}/result.json`;
            document.getElementById('invocation-result-tabs').innerHTML = Object.keys(this.result).map(key => `<button type="button" class="runtime-log-btn" data-result-part="${this.escape(key)}" aria-pressed="false">${key === 'extraction' ? '字段抽取' : '逻辑分析'}</button>`).join('');
            this.showResultPart(Object.keys(this.result)[0]);
            this.dialog.showModal(); document.body.classList.add('invocation-modal-open'); this.icons();
        } catch (error) { window.Toast?.error?.(error.message); }
    },
    showResultPart(part) {
        document.getElementById('invocation-result-json').textContent = JSON.stringify(this.result[part], null, 2);
        document.querySelectorAll('[data-result-part]').forEach(button => {
            const selected = button.dataset.resultPart === part;
            button.classList.toggle('primary', selected); button.setAttribute('aria-pressed', String(selected));
        });
    },
    meta(label, value) { return `<div><span>${this.escape(label)}</span><strong>${this.escape(value)}</strong></div>`; },
    escape(value) {
        const text = String(value ?? '');
        if (window.Utils?.escapeHtml) return Utils.escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        const node = document.createElement('div'); node.textContent = text;
        return node.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },
    time(value) { return value ? String(value).replace('T', ' ').replace(/\.\d+$/, '') : '—'; },
    size(value) { return value == null ? '—' : value >= 1048576 ? `${(value / 1048576).toFixed(2)} MB` : `${(value / 1024).toFixed(1)} KB`; },
    duration(ms) { if (ms == null) return '—'; const seconds = Math.max(0, ms / 1000); return seconds < 60 ? `${seconds.toFixed(1)} 秒` : `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`; },
    icons() { window.lucide?.createIcons(); },
};
window.InvocationLogs = InvocationLogs;
document.addEventListener('DOMContentLoaded', () => InvocationLogs.init());
