/**
 * API 手册阅读器：读取 /api-reference 并渲染 docs/API_FULL_REFERENCE.md。
 */
const ApiDocs = {
    state: {
        loaded: false,
        loading: false,
        raw: '',
        sections: [],
        tocSections: [],
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

        // 防抖：避免每敲一个字符就重跑一遍全量匹配。
        this.els.search?.addEventListener('input', () => {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => this.applySearch(), 150);
        });
        this.els.clear?.addEventListener('click', () => {
            this.els.search.value = '';
            clearTimeout(this._searchTimer);
            this.applySearch();
            this.els.search.focus();
        });
        this.els.refresh?.addEventListener('click', () => this.load(true));

        // 目录跳转：目标段可能还没渲染，先补渲染再滚过去，否则跳到空占位上。
        this.els.toc?.addEventListener('click', (event) => {
            const link = event.target.closest('.api-docs-toc-item');
            if (!link) return;
            const href = link.getAttribute('href') || '';
            if (!href.startsWith('#')) return;
            const id = decodeURIComponent(href.slice(1));
            const index = this.state.sections.findIndex((section) => section.id === id);
            if (index < 0) return;
            event.preventDefault();
            this.renderSection(index);
            this.state.sections[index].el?.scrollIntoView({ block: 'start' });
        });
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
            this.state.sections = this.splitSections(this.state.raw);
            this.state.tocSections = this.state.sections.filter((section) => section.level > 0);
            this.state.loaded = true;
            this.render(data);
            // 骨架天然全部可见，空搜索无需再过滤一遍；
            // 但重新加载（refresh）时搜索框可能仍有内容，这时要把过滤恢复回去。
            if (this.els.search?.value.trim()) this.applySearch();
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
        this.renderToc(this.state.tocSections);
        this.renderBody(this.state.sections);
        // 手册正文是 markdown 转换产物，不含 data-lucide 图标；
        // 限定 root 后这次扫描只覆盖空容器，避免在万级节点的 document 上全量 querySelectorAll。
        if (typeof lucide !== 'undefined' && this.els.body) {
            lucide.createIcons({ root: this.els.body });
        }
    },

    /**
     * 按标题把手册切成段：每段 = 一个标题行 + 到下一个标题之前的全部行。
     * 段是懒渲染与搜索的最小单位，id 与 TOC 锚点在此单点生成，避免两处各算一份而漂移。
     */
    splitSections(markdown) {
        const sections = [];
        const seen = new Map();
        let current = null;
        let inFence = false;

        const ensure = () => {
            if (!current) {
                current = {
                    level: 0, rawTitle: '', title: '',
                    id: 'api-docs-preamble', haystack: '', lines: [],
                };
            }
            return current;
        };

        markdown.split(/\r?\n/).forEach((line) => {
            const trimmed = line.trim();

            if (/^```/.test(trimmed)) {
                inFence = !inFence;
                ensure().lines.push(line);
                return;
            }
            if (inFence) {
                ensure().lines.push(line);
                return;
            }

            const match = /^(#{1,6})\s+(.+)$/.exec(trimmed);
            if (!match) {
                ensure().lines.push(line);
                return;
            }

            if (current) sections.push(current);
            const rawTitle = match[2].trim();
            const title = rawTitle.replace(/`/g, '').trim();
            const baseId = this.slugify(title) || `section-${seen.size + 1}`;
            const count = seen.get(baseId) || 0;
            seen.set(baseId, count + 1);
            current = {
                level: match[1].length,
                rawTitle,
                title,
                id: count ? `${baseId}-${count + 1}` : baseId,
                haystack: title.toLowerCase(),
                lines: [line],
            };
        });

        if (current) sections.push(current);

        return sections
            .filter((section) => section.level > 0 || section.lines.some((line) => line.trim()))
            .map((section) => {
                section.text = section.lines.join('\n').toLowerCase();
                section.estHeight = this.estimateHeight(section);
                section.rendered = false;
                section.el = null;
                return section;
            });
    },

    /**
     * 估算段渲染后的高度，作为占位 min-height，避免懒渲染时滚动条长度剧烈跳变。
     * 只求量级正确：表格行比正文行高，代码围栏本身不占高度。
     */
    estimateHeight(section) {
        let px = 0;
        section.lines.forEach((line) => {
            const trimmed = line.trim();
            if (!trimmed) return;
            if (trimmed.startsWith('```')) px += 12;
            else if (trimmed.startsWith('|')) px += 34;
            else px += 26;
        });
        return Math.min(4000, Math.max(80, px));
    },

    /** TOC 数据 = 分段结果里有标题的那些段，保证目录锚点与正文段 id 同源。 */
    buildSections(markdown) {
        return this.splitSections(markdown)
            .filter((section) => section.level > 0)
            .map(({ level, title, id, haystack }) => ({ level, title, id, haystack }));
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

    renderBody(sections) {
        if (!this.els.body) return;
        if (!sections.length) {
            this.els.body.innerHTML = '<div class="api-docs-empty">API 手册为空</div>';
            return;
        }

        // 只注入骨架：标题立刻可见（TOC 锚点可用），正文等滚入视口再生成。
        this.els.body.innerHTML = sections.map((section, index) => {
            const head = section.level
                ? `<h${section.level} id="${Utils.escapeHtml(section.id)}" data-search-title="${Utils.escapeHtml(section.haystack)}">${this.renderInline(section.rawTitle)}</h${section.level}>`
                : '';
            return `<section class="api-doc-section" data-index="${index}" style="min-height:${section.estHeight}px">`
                + head
                + '<div class="api-doc-section-body"></div>'
                + '</section>';
        }).join('');

        // 缓存宿主元素，后续搜索/跳转不必反复 querySelector。
        this.els.body.querySelectorAll('.api-doc-section').forEach((el) => {
            const index = Number(el.dataset.index);
            if (sections[index]) sections[index].el = el;
        });

        this.observeSections();
    },

    /** 渲染单段正文。标题已在骨架里，这里只补标题之后的内容。 */
    renderSection(index) {
        const section = this.state.sections[index];
        if (!section || section.rendered) return;
        const host = section.el;
        if (!host) return;
        const holder = host.querySelector('.api-doc-section-body');
        if (!holder) return;

        const bodyLines = section.level ? section.lines.slice(1) : section.lines;
        holder.innerHTML = this.markdownToHtml(bodyLines.join('\n'));
        section.rendered = true;
        host.style.minHeight = '';
    },

    /** 滚入视口前 600px 就开始渲染，滚动时基本感知不到空白。 */
    observeSections() {
        if (this._observer) this._observer.disconnect();

        if (typeof IntersectionObserver === 'undefined') {
            this.state.sections.forEach((_, index) => this.renderSection(index));
            return;
        }

        this._observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                this.renderSection(Number(entry.target.dataset.index));
                this._observer.unobserve(entry.target);
            });
        }, { rootMargin: '600px 0px' });

        this.state.sections.forEach((section) => {
            if (section.el) this._observer.observe(section.el);
        });
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

    /** 纯函数：返回每段是否命中。空查询全命中，不做任何逐段匹配。 */
    matchSections(sections, query) {
        const needle = String(query == null ? '' : query).trim().toLowerCase();
        if (!needle) return sections.map(() => true);
        return sections.map((section) => section.text.includes(needle));
    },

    applySearch() {
        const query = (this.els.search?.value || '').trim().toLowerCase();
        const matched = this.matchSections(this.state.sections, query);
        this.filterBody(query, matched);
        this.filterToc(query, matched);
    },

    filterBody(query, matched) {
        if (!this.els.body) return;
        this.els.body.classList.toggle('is-searching', Boolean(query));

        let visibleCount = 0;
        this.state.sections.forEach((section, index) => {
            const hit = matched[index];
            if (hit) visibleCount += 1;
            if (!section.el) return;
            section.el.classList.toggle('api-search-hidden', !hit);
            // 命中的段必须立刻渲染：它可能还没滚进过视口。
            if (hit && query) this.renderSection(index);
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

    filterToc(query, matched) {
        if (!this.els.toc) return;
        // 目录与正文用同一份命中结果，口径一致。
        const hitIds = new Set();
        this.state.sections.forEach((section, index) => {
            if (matched[index]) hitIds.add(section.id);
        });
        this.els.toc.querySelectorAll('.api-docs-toc-item').forEach((item) => {
            const id = decodeURIComponent((item.getAttribute('href') || '').slice(1));
            item.classList.toggle('is-hidden', Boolean(query) && !hitIds.has(id));
        });
    },
};

if (typeof window !== 'undefined') {
    window.ApiDocs = ApiDocs;
}
