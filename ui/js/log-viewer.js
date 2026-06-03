/**
 * 实时日志查看器：通过 EventSource 订阅 /log/stream。
 */
const LogViewer = {
    source: null,
    active: false,
    paused: false,
    bufferedLines: [],
    renderedCount: 0,
    maxEntries: 2000,
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
        const timestamp = this.extractTimestamp(rawLine);
        const level = (data.level || 'INFO').toLowerCase();

        const entry = document.createElement('div');
        entry.className = `runtime-log-entry level-${level}`;
        entry.dataset.raw = rawLine;
        entry.innerHTML = `
            <span class="runtime-log-time">${this.escapeHtml(timestamp)}</span>
            <span class="runtime-log-level">${this.escapeHtml(data.level || 'INFO')}</span>
            <span class="runtime-log-text">${this.escapeHtml(rawLine)}</span>
        `;

        this.els.container.appendChild(entry);
        this.renderedCount += 1;
        this.pruneEntries();
        this.updateCount();
        this.els.container.scrollTop = this.els.container.scrollHeight;
    },

    pruneEntries() {
        const entries = this.els.container.querySelectorAll('.runtime-log-entry');
        const overflow = entries.length - this.maxEntries;
        for (let i = 0; i < overflow; i += 1) {
            entries[i].remove();
            this.renderedCount -= 1;
        }
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
        this.bufferedLines = [];
        this.els.container.innerHTML = '<div class="runtime-log-empty">等待日志连接...</div>';
        this.updateCount();
        this.updatePauseButton();
    },

    async copyVisibleLogs() {
        const lines = Array.from(this.els.container.querySelectorAll('.runtime-log-entry'))
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
        if (this.els.count) {
            this.els.count.textContent = `${Math.max(0, this.renderedCount)} 行`;
        }
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
};

window.LogViewer = LogViewer;

document.addEventListener('DOMContentLoaded', () => {
    LogViewer.init();
});
