/**
 * API 手册阅读器：读取 /api-reference 并渲染 docs/API_FULL_REFERENCE.md。
 */
const ApiDocs = {
    state: {
        loaded: false,
        loading: false,
        raw: '',
        sections: [],
    },

    els: {},

    activate() {
        this.cacheEls();
        this.bindEvents();
        if (!this.state.loaded && !this.state.loading) {
            this.load();
        }
    },

    cacheEls() {
        this.els = {
            meta: document.getElementById('api-docs-meta'),
            search: document.getElementById('api-docs-search'),
            clear: document.getElementById('api-docs-clear'),
            refresh: document.getElementById('api-docs-refresh'),
            toc: document.getElementById('api-docs-toc'),
            body: document.getElementById('api-docs-body'),
        };
    },

    bindEvents() {
        if (this._bound) return;
        this._bound = true;

        this.els.search?.addEventListener('input', () => this.applySearch());
        this.els.clear?.addEventListener('click', () => {
            this.els.search.value = '';
            this.applySearch();
            this.els.search.focus();
        });
        this.els.refresh?.addEventListener('click', () => this.load(true));
    },

    async load(force = false) {
        if (this.state.loading) return;
        if (this.state.loaded && !force) return;

        this.state.loading = true;
        this.setLoading();

        try {
            const response = await fetch('/api-reference');
            const payload = await response.json();
            if (!response.ok || payload.code !== 200) {
                throw new Error(payload.detail || payload.message || 'API 手册加载失败');
            }

            const data = payload.data || {};
            this.state.raw = String(data.content || '');
            this.state.sections = this.buildSections(this.state.raw);
            this.state.loaded = true;
            this.render(data);
            this.applySearch();
        } catch (error) {
            this.setError(error.message || 'API 手册加载失败');
        } finally {
            this.state.loading = false;
        }
    },

    setLoading() {
        if (this.els.meta) this.els.meta.textContent = '加载中';
        if (this.els.toc) this.els.toc.innerHTML = '<div class="api-docs-empty">等待加载...</div>';
        if (this.els.body) this.els.body.innerHTML = '<div class="api-docs-loading">正在加载 API 手册...</div>';
    },

    setError(message) {
        if (this.els.meta) this.els.meta.textContent = '加载失败';
        if (this.els.toc) this.els.toc.innerHTML = '<div class="api-docs-empty">加载失败</div>';
        if (this.els.body) {
            this.els.body.innerHTML = `<div class="api-docs-error">${Utils.escapeHtml(message)}</div>`;
        }
    },

    render(data) {
        const updatedAt = data.updated_at ? Utils.formatDateTime(data.updated_at) : '-';
        const endpointCount = (this.state.raw.match(/^\| (GET|POST|PUT|DELETE|PATCH) \| `/gm) || []).length;
        if (this.els.meta) {
            this.els.meta.textContent = `${endpointCount} 个接口 · ${Utils.formatFileSize(data.size || 0)} · 更新 ${updatedAt}`;
        }
        this.renderToc(this.state.sections);
        this.renderBody(this.state.raw);
        if (typeof lucide !== 'undefined') lucide.createIcons();
    },

    buildSections(markdown) {
        const seen = new Map();
        const sections = [];
        let inFence = false;
        markdown.split(/\r?\n/).forEach((line) => {
            const trimmed = line.trim();
            if (/^```/.test(trimmed)) {
                inFence = !inFence;
                return;
            }
            if (inFence) return;
            const match = /^(#{1,6})\s+(.+)$/.exec(trimmed);
            if (!match) return;
            const level = match[1].length;
            const title = match[2].replace(/`/g, '').trim();
            const baseId = this.slugify(title) || `section-${seen.size + 1}`;
            const count = seen.get(baseId) || 0;
            seen.set(baseId, count + 1);
                sections.push({
                    level,
                    title,
                    id: count ? `${baseId}-${count + 1}` : baseId,
                    haystack: title.toLowerCase(),
                });
            });
        return sections;
    },

    slugify(text) {
        return String(text)
            .toLowerCase()
            .replace(/[`*_~[\](){}]/g, '')
            .replace(/[^\w\u4e00-\u9fa5/-]+/g, '-')
            .replace(/^-+|-+$/g, '')
            .slice(0, 96);
    },

    renderToc(sections) {
        if (!this.els.toc) return;
        if (!sections.length) {
            this.els.toc.innerHTML = '<div class="api-docs-empty">暂无目录</div>';
            return;
        }
        this.els.toc.innerHTML = sections.map((section) => `
            <a class="api-docs-toc-item level-${section.level}" href="#${Utils.escapeHtml(section.id)}" data-title="${Utils.escapeHtml(section.haystack)}">
                ${Utils.escapeHtml(section.title)}
            </a>
        `).join('');
    },

    renderBody(markdown) {
        if (!this.els.body) return;
        const html = this.markdownToHtml(markdown);
        this.els.body.innerHTML = html || '<div class="api-docs-empty">API 手册为空</div>';
    },

    markdownToHtml(markdown) {
        const lines = markdown.split(/\r?\n/);
        const html = [];
        const seen = new Map();
        let i = 0;

        while (i < lines.length) {
            const line = lines[i];

            if (/^```/.test(line.trim())) {
                const lang = line.trim().slice(3).trim();
                const code = [];
                i += 1;
                while (i < lines.length && !/^```/.test(lines[i].trim())) {
                    code.push(lines[i]);
                    i += 1;
                }
                html.push(`<pre class="api-code"><code data-lang="${Utils.escapeHtml(lang)}">${Utils.escapeHtml(code.join('\n'))}</code></pre>`);
                i += 1;
                continue;
            }

            if (!line.trim()) {
                i += 1;
                continue;
            }

            const heading = /^(#{1,6})\s+(.+)$/.exec(line.trim());
            if (heading) {
                const level = heading[1].length;
                const rawTitle = heading[2].trim();
                const cleanTitle = rawTitle.replace(/`/g, '').trim();
                const baseId = this.slugify(cleanTitle) || `section-${seen.size + 1}`;
                const count = seen.get(baseId) || 0;
                seen.set(baseId, count + 1);
                const id = count ? `${baseId}-${count + 1}` : baseId;
                html.push(`<h${level} id="${Utils.escapeHtml(id)}" data-search-title="${Utils.escapeHtml(cleanTitle.toLowerCase())}">${this.renderInline(rawTitle)}</h${level}>`);
                i += 1;
                continue;
            }

            if (/^\s*---+\s*$/.test(line)) {
                html.push('<hr>');
                i += 1;
                continue;
            }

            if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?/.test(lines[i + 1])) {
                const tableLines = [];
                while (i < lines.length && lines[i].trim().startsWith('|')) {
                    tableLines.push(lines[i]);
                    i += 1;
                }
                html.push(this.renderTable(tableLines));
                continue;
            }

            if (line.trim().startsWith('>')) {
                const quote = [];
                while (i < lines.length && lines[i].trim().startsWith('>')) {
                    quote.push(lines[i].trim().replace(/^>\s?/, ''));
                    i += 1;
                }
                html.push(`<blockquote>${quote.map((item) => this.renderInline(item)).join('<br>')}</blockquote>`);
                continue;
            }

            if (/^\s*-\s+/.test(line)) {
                const items = [];
                while (i < lines.length && /^\s*-\s+/.test(lines[i])) {
                    items.push(lines[i].replace(/^\s*-\s+/, ''));
                    i += 1;
                }
                html.push(`<ul>${items.map((item) => `<li>${this.renderInline(item)}</li>`).join('')}</ul>`);
                continue;
            }

            const paragraph = [line.trim()];
            i += 1;
            while (i < lines.length && lines[i].trim() && !this.isBlockStart(lines, i)) {
                paragraph.push(lines[i].trim());
                i += 1;
            }
            html.push(`<p>${this.renderInline(paragraph.join(' '))}</p>`);
        }

        return html.join('\n');
    },

    isBlockStart(lines, index) {
        const line = lines[index] || '';
        if (/^```/.test(line.trim())) return true;
        if (/^(#{1,6})\s+/.test(line.trim())) return true;
        if (/^\s*---+\s*$/.test(line)) return true;
        if (line.trim().startsWith('>')) return true;
        if (/^\s*-\s+/.test(line)) return true;
        if (line.trim().startsWith('|') && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?/.test(lines[index + 1])) return true;
        return false;
    },

    renderTable(tableLines) {
        if (tableLines.length < 2) return '';
        const rows = tableLines.map((line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim()));
        const header = rows[0] || [];
        const body = rows.slice(2);
        return `
            <div class="api-table-wrap">
                <table class="api-table">
                    <thead><tr>${header.map((cell) => `<th>${this.renderInline(cell)}</th>`).join('')}</tr></thead>
                    <tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${this.renderInline(cell)}</td>`).join('')}</tr>`).join('')}</tbody>
                </table>
            </div>
        `;
    },

    renderInline(text) {
        let html = Utils.escapeHtml(text == null ? '' : String(text));
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\[([^\]]+)\]\((#[^)]+)\)/g, '<a href="$2">$1</a>');
        return html;
    },

    applySearch() {
        const query = (this.els.search?.value || '').trim().toLowerCase();
        this.filterToc(query);
        this.filterBody(query);
    },

    filterToc(query) {
        if (!this.els.toc) return;
        const items = this.els.toc.querySelectorAll('.api-docs-toc-item');
        items.forEach((item) => {
            const text = item.dataset.title || item.textContent.toLowerCase();
            item.classList.toggle('is-hidden', Boolean(query) && !text.includes(query));
        });
    },

    filterBody(query) {
        if (!this.els.body) return;
        this.els.body.classList.toggle('is-searching', Boolean(query));
        const blocks = this.els.body.querySelectorAll('h1,h2,h3,h4,h5,h6,p,blockquote,li,tr,pre');
        let visibleCount = 0;
        blocks.forEach((block) => {
            const text = block.textContent.toLowerCase();
            const matched = !query || text.includes(query);
            block.classList.toggle('api-search-hidden', !matched);
            if (matched) visibleCount += 1;
        });

        let empty = this.els.body.querySelector('.api-docs-no-results');
        if (query && visibleCount === 0) {
            if (!empty) {
                empty = document.createElement('div');
                empty.className = 'api-docs-no-results';
                this.els.body.prepend(empty);
            }
            empty.textContent = `没有找到“${this.els.search.value.trim()}”`;
        } else if (empty) {
            empty.remove();
        }
    },
};

if (typeof window !== 'undefined') {
    window.ApiDocs = ApiDocs;
}
