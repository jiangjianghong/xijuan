/**
 * 实时日志查看器：通过 EventSource 订阅 /log/stream。
 */
const LogViewer = {
    source: null,
    active: false,
    paused: false,
    bufferedLines: [],
    renderedCount: 0,
    visibleCount: 0,
    maxEntries: 5000,
    filters: { fileId: '', typeId: '' },
    els: {},

    init() {
        this.cacheElements();
        if (!this.els.container) return;

        this.bindEvents();
        this.loadFiles();
    },

    cacheElements() {
        this.els = {
            fileSelect: document.getElementById('runtime-log-file'),
            levelSelect: document.getElementById('runtime-log-level'),
            tailSelect: document.getElementById('runtime-log-tail'),
            connectBtn: document.getElementById('runtime-log-connect'),
            pauseBtn: document.getElementById('runtime-log-pause'),
            clearBtn: document.getElementById('runtime-log-clear'),
            copyBtn: document.getElementById('runtime-log-copy'),
            refreshBtn: document.getElementById('runtime-log-refresh'),
            status: document.getElementById('runtime-log-status'),
            currentFile: document.getElementById('runtime-log-current-file'),
            count: document.getElementById('runtime-log-count'),
            filterBar: document.getElementById('runtime-log-filters'),
            container: document.getElementById('runtime-log-container'),
        };
    },

    bindEvents() {
        this.els.connectBtn?.addEventListener('click', () => {
            if (this.source) {
                this.disconnect();
            } else {
                this.connect();
            }
        });

        this.els.pauseBtn?.addEventListener('click', () => this.togglePause());
        this.els.clearBtn?.addEventListener('click', () => this.clear());
        this.els.copyBtn?.addEventListener('click', () => this.copyVisibleLogs());
        this.els.refreshBtn?.addEventListener('click', () => this.loadFiles());

        [this.els.fileSelect, this.els.levelSelect, this.els.tailSelect].forEach(el => {
            el?.addEventListener('change', () => {
                if (this.source) this.connect();
            });
        });

        // 点击行内 file_id / type_id 激活筛选（事件委托，避免逐行绑定）
        this.els.container?.addEventListener('click', event => {
            const target = event.target.closest('[data-filter-key]');
            if (!target) return;
            this.toggleFilter(target.dataset.filterKey, target.dataset.filterValue || '');
        });

        // 筛选栏内的移除按钮
        this.els.filterBar?.addEventListener('click', event => {
            const chip = event.target.closest('[data-filter-clear]');
            if (!chip) return;
            this.clearFilter(chip.dataset.filterClear);
        });
    },

    async loadFiles() {
        if (!this.els.fileSelect) return;

        try {
            const response = await fetch('/log/files');
            const result = await response.json();
            if (!response.ok || result.code !== 200) {
                throw new Error(result.message || '加载日志文件失败');
            }

            const files = result.data?.items || [];
            const current = result.data?.current || '';
            const options = ['<option value="">当前最新</option>'];
            files.forEach(file => {
                options.push(`<option value="${this.escapeHtml(file.name)}">${this.escapeHtml(file.name)}</option>`);
            });
            if (files.length === 0) {
                options.push('<option value="" disabled>暂无日志文件</option>');
            }
            this.els.fileSelect.innerHTML = options.join('');
            this.setCurrentFile(current || '-');
        } catch (error) {
            this.setStatus('error', '加载失败');
            Toast?.error?.(error.message || '加载日志文件失败');
        }
    },

    activate() {
        this.active = true;
        this.loadFiles().finally(() => {
            if (!this.source) this.connect();
        });
    },

    deactivate() {
        this.active = false;
        this.disconnect(false);
    },

    connect() {
        this.disconnect(false);
        this.clear();

        const params = new URLSearchParams();
        params.set('tail', this.els.tailSelect?.value || '200');

        const file = this.els.fileSelect?.value || '';
        if (file) params.set('file', file);

        const level = this.els.levelSelect?.value || '';
        if (level) params.set('level', level);

        this.setStatus('reconnecting', '连接中');
        this.source = new EventSource(`/log/stream?${params.toString()}`);
        this.setConnectButton(true);

        this.source.addEventListener('ready', event => {
            const data = this.parseEvent(event);
            this.setStatus('connected', '已连接');
            this.setCurrentFile(data.file || '当前最新');
        });

        this.source.addEventListener('rotated', event => {
            const data = this.parseEvent(event);
            this.setCurrentFile(data.file || '当前最新');
            this.appendSystemLine(data.message || '日志文件已切换');
        });

        this.source.addEventListener('line', event => {
            this.handleLine(this.parseEvent(event));
        });

        this.source.addEventListener('heartbeat', event => {
            const data = this.parseEvent(event);
            if (data.file) this.setCurrentFile(data.file);
            if (this.source) this.setStatus('connected', '已连接');
        });

        this.source.onerror = () => {
            if (!this.source) return;
            this.setStatus('reconnecting', '重连中');
        };
    },

    disconnect(showStatus = true) {
        if (this.source) {
            this.source.close();
            this.source = null;
        }
        this.setConnectButton(false);
        if (showStatus) this.setStatus('disconnected', '已断开');
    },

    handleLine(data) {
        if (!data || !data.line) return;

        if (this.paused) {
            this.bufferedLines.push(data);
            if (this.bufferedLines.length > this.maxEntries) {
                this.bufferedLines.shift();
            }
            this.updatePauseButton();
            return;
        }

        this.appendLine(data);
    },

    appendSystemLine(message) {
        this.appendLine({
            level: 'INFO',
            line: message,
            file: this.els.currentFile?.textContent || '',
        });
    },

    appendLine(data) {
        const empty = this.els.container.querySelector('.runtime-log-empty');
        if (empty) empty.remove();

        const rawLine = data.line || '';
        const parsed = this.parseLogLine(rawLine, data);
        const level = (parsed.level || 'INFO').toLowerCase();

        const entry = document.createElement('div');
        entry.className = `runtime-log-entry level-${level}`;
        entry.dataset.raw = rawLine;
        entry.dataset.typeId = parsed.typeId;
        entry.dataset.fileId = parsed.fileId;

        const typeCell = parsed.typeId && parsed.typeId !== '-'
            ? `<span class="runtime-log-type is-clickable" role="button" tabindex="-1" data-filter-key="typeId" data-filter-value="${this.escapeAttr(parsed.typeId)}" title="按此 type_id 筛选：${this.escapeAttr(parsed.typeId)}">${this.escapeHtml(parsed.typeId)}</span>`
            : `<span class="runtime-log-type" title="${this.escapeAttr(parsed.typeId)}">${this.escapeHtml(parsed.typeId)}</span>`;
        const fileCell = parsed.fileId && parsed.fileId !== '-'
            ? `<span class="runtime-log-file is-clickable" role="button" tabindex="-1" data-filter-key="fileId" data-filter-value="${this.escapeAttr(parsed.fileId)}" title="按此 file_id 筛选：${this.escapeAttr(parsed.fileId)}">${this.escapeHtml(parsed.fileId)}</span>`
            : `<span class="runtime-log-file" title="${this.escapeAttr(parsed.fileId)}">${this.escapeHtml(parsed.fileId)}</span>`;

        entry.innerHTML = `
            <span class="runtime-log-time">${this.escapeHtml(parsed.timestamp)}</span>
            <span class="runtime-log-level">${this.escapeHtml(parsed.level)}</span>
            ${typeCell}
            ${fileCell}
            <span class="runtime-log-text">${this.escapeHtml(parsed.message)}</span>
        `;

        const hidden = !this.matchesFilter(entry);
        if (hidden) entry.classList.add('is-filtered');

        this.els.container.appendChild(entry);
        this.renderedCount += 1;
        if (!hidden) this.visibleCount += 1;
        this.pruneEntries();
        this.updateCount();
        if (!hidden) this.els.container.scrollTop = this.els.container.scrollHeight;
    },

    pruneEntries() {
        const entries = this.els.container.querySelectorAll('.runtime-log-entry');
        const overflow = entries.length - this.maxEntries;
        for (let i = 0; i < overflow; i += 1) {
            if (!entries[i].classList.contains('is-filtered')) {
                this.visibleCount -= 1;
            }
            entries[i].remove();
            this.renderedCount -= 1;
        }
    },

    matchesFilter(entry) {
        const { fileId, typeId } = this.filters;
        if (fileId && entry.dataset.fileId !== fileId) return false;
        if (typeId && entry.dataset.typeId !== typeId) return false;
        return true;
    },

    toggleFilter(key, value) {
        if (key !== 'fileId' && key !== 'typeId') return;
        this.filters[key] = this.filters[key] === value ? '' : value;
        this.applyFilters();
    },

    clearFilter(key) {
        if (key === 'all') {
            this.filters = { fileId: '', typeId: '' };
        } else if (key === 'fileId' || key === 'typeId') {
            this.filters[key] = '';
        }
        this.applyFilters();
    },

    applyFilters() {
        const entries = this.els.container.querySelectorAll('.runtime-log-entry');
        let visible = 0;
        entries.forEach(entry => {
            const match = this.matchesFilter(entry);
            entry.classList.toggle('is-filtered', !match);
            if (match) visible += 1;
        });
        this.visibleCount = visible;
        this.renderFilterBar();
        this.updateCount();
        this.els.container.scrollTop = this.els.container.scrollHeight;
    },

    renderFilterBar() {
        if (!this.els.filterBar) return;
        const { fileId, typeId } = this.filters;
        const chips = [];
        if (typeId) {
            chips.push(`<span class="runtime-log-filter-chip" data-filter-clear="typeId" title="点击移除">type_id: ${this.escapeHtml(typeId)} <i data-lucide="x" class="w-3 h-3"></i></span>`);
        }
        if (fileId) {
            chips.push(`<span class="runtime-log-filter-chip" data-filter-clear="fileId" title="点击移除">file_id: ${this.escapeHtml(fileId)} <i data-lucide="x" class="w-3 h-3"></i></span>`);
        }
        if (chips.length === 0) {
            this.els.filterBar.innerHTML = '';
            this.els.filterBar.classList.remove('active');
            return;
        }
        chips.push('<button type="button" class="runtime-log-filter-clear-all" data-filter-clear="all">清除筛选</button>');
        this.els.filterBar.innerHTML = `<span class="runtime-log-filter-label">筛选</span>${chips.join('')}`;
        this.els.filterBar.classList.add('active');
        this.refreshIcons();
    },

    togglePause() {
        this.paused = !this.paused;
        if (!this.paused && this.bufferedLines.length > 0) {
            const pending = this.bufferedLines.splice(0);
            pending.forEach(line => this.appendLine(line));
        }
        this.updatePauseButton();
    },

    updatePauseButton() {
        if (!this.els.pauseBtn) return;
        const pending = this.bufferedLines.length;
        if (this.paused) {
            this.els.pauseBtn.classList.add('active');
            this.els.pauseBtn.innerHTML = `
                <i data-lucide="play" class="w-4 h-4"></i>
                <span>${pending ? `继续 ${pending}` : '继续'}</span>
            `;
        } else {
            this.els.pauseBtn.classList.remove('active');
            this.els.pauseBtn.innerHTML = `
                <i data-lucide="pause" class="w-4 h-4"></i>
                <span>暂停</span>
            `;
        }
        this.refreshIcons();
    },

    clear() {
        if (!this.els.container) return;
        this.renderedCount = 0;
        this.visibleCount = 0;
        this.bufferedLines = [];
        this.els.container.innerHTML = '<div class="runtime-log-empty">等待日志连接...</div>';
        this.updateCount();
        this.updatePauseButton();
    },

    async copyVisibleLogs() {
        // 存在筛选时只复制可见（未被筛掉）的行
        const lines = Array.from(this.els.container.querySelectorAll('.runtime-log-entry'))
            .filter(entry => !entry.classList.contains('is-filtered'))
            .map(entry => entry.dataset.raw || '')
            .filter(Boolean);

        if (lines.length === 0) {
            Toast?.info?.('当前没有可复制的日志');
            return;
        }

        try {
            await navigator.clipboard.writeText(lines.join('\n'));
            Toast?.success?.(`已复制 ${lines.length} 行日志`);
        } catch (error) {
            Toast?.error?.('复制失败');
        }
    },

    parseEvent(event) {
        try {
            return JSON.parse(event.data || '{}');
        } catch (error) {
            return {};
        }
    },

    extractTimestamp(line) {
        const matched = String(line || '').match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/);
        if (matched) return matched[1];
        return new Date().toLocaleTimeString('zh-CN', { hour12: false });
    },

    parseLogLine(line, data = {}) {
        const rawLine = String(line || '');
        const parts = rawLine.split(' | ');
        const hasStructuredLine = parts.length >= 5 && /^[A-Z]+$/.test(parts[1].trim());
        return {
            timestamp: data.timestamp || (hasStructuredLine ? parts[0].trim() : this.extractTimestamp(rawLine)),
            level: (data.level || (hasStructuredLine ? parts[1].trim() : 'INFO')).toUpperCase(),
            typeId: data.type_id || (hasStructuredLine ? parts[2].trim() : '-') || '-',
            fileId: data.file_id || (hasStructuredLine ? parts[3].trim() : '-') || '-',
            message: data.message || (hasStructuredLine ? parts.slice(4).join(' | ') : rawLine),
        };
    },

    setStatus(state, text) {
        if (!this.els.status) return;
        this.els.status.className = `runtime-log-status ${state}`;
        this.els.status.textContent = text;
    },

    setCurrentFile(fileName) {
        if (this.els.currentFile) {
            this.els.currentFile.textContent = fileName || '-';
        }
    },

    updateCount() {
        if (!this.els.count) return;
        const total = Math.max(0, this.renderedCount);
        const hasFilter = this.filters.fileId || this.filters.typeId;
        this.els.count.textContent = hasFilter
            ? `${Math.max(0, this.visibleCount)} / ${total} 行`
            : `${total} 行`;
    },

    setConnectButton(connected) {
        if (!this.els.connectBtn) return;
        this.els.connectBtn.innerHTML = connected
            ? '<i data-lucide="unplug" class="w-4 h-4"></i><span>断开</span>'
            : '<i data-lucide="plug-zap" class="w-4 h-4"></i><span>连接</span>';
        this.els.connectBtn.classList.toggle('primary', !connected);
        this.refreshIcons();
    },

    refreshIcons() {
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    },

    escapeHtml(text) {
        if (typeof Utils !== 'undefined' && Utils.escapeHtml) {
            return Utils.escapeHtml(text);
        }
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    },

    escapeAttr(text) {
        return this.escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },
};

window.LogViewer = LogViewer;

document.addEventListener('DOMContentLoaded', () => {
    LogViewer.init();
});
