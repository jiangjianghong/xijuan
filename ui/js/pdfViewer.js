/**
 * PDF 高亮定位查看器（基于 pdf.js，依赖全局 pdfjsLib）。
 *
 * 对外接口：
 *   PdfViewer.init(container)            绑定容器并显示占位提示
 *   PdfViewer.openAndLocate(url, hits)   加载 PDF（同 url 缓存）→ 跳首个命中页 → 画框
 *   PdfViewer.gotoPage(n)                翻页（保留当前 hits 的框）
 *
 * hits 形如 {1: [{bbox:[x0,y0,x1,y1], page_size:[w,h]}], 7: []}
 * 页码为 int；空数组表示该页有命中但无框（老数据仅 page_num），只跳页。
 */
const PdfViewer = {
    container: null,
    pdfDoc: null,
    url: null,
    currentPage: 1,
    totalPages: 0,
    hits: {},
    _rendering: false,

    init(container) {
        this.container = container;
        this.pdfDoc = null;
        this.url = null;
        this.hits = {};
        this._showMessage('点击字段的「定位」按钮在 PDF 中查看命中位置');
    },

    async openAndLocate(url, hits) {
        if (!window.pdfjsLib) {
            this._showMessage('pdf.js 未加载，无法预览');
            return;
        }
        this.hits = hits || {};
        try {
            if (!this.pdfDoc || this.url !== url) {
                this._showMessage('PDF 加载中...');
                this.pdfDoc = await pdfjsLib.getDocument({ url }).promise;
                this.url = url;
                this.totalPages = this.pdfDoc.numPages;
            }
            const pages = Object.keys(this.hits).map(Number).sort((a, b) => a - b);
            this._buildSkeleton();
            await this.gotoPage(pages.length > 0 ? pages[0] : 1);
        } catch (err) {
            console.error('PDF 加载失败:', err);
            const msg = err && err.name === 'MissingPDFException'
                ? '原始 PDF 不存在（历史文件），重新上传后可使用定位'
                : 'PDF 加载失败: ' + (err && err.message ? err.message : err);
            this._showMessage(msg);
            this.pdfDoc = null;
            this.url = null;
        }
    },

    async gotoPage(n) {
        if (!this.pdfDoc || this._rendering) return;
        n = Math.min(Math.max(1, n), this.totalPages);
        this._rendering = true;
        try {
            const page = await this.pdfDoc.getPage(n);
            this.currentPage = n;
            const wrap = this.container.querySelector('.pdf-canvas-wrap');
            const canvas = this.container.querySelector('canvas');
            if (!wrap || !canvas) return;
            const base = page.getViewport({ scale: 1 });
            const scale = Math.max(0.1, (wrap.clientWidth - 16) / base.width);
            const viewport = page.getViewport({ scale });
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
            this._drawHighlights(n);
            this._updateToolbar();
        } catch (err) {
            console.error('PDF 渲染失败:', err);
        } finally {
            this._rendering = false;
        }
    },

    _buildSkeleton() {
        this.container.innerHTML = `
            <div class="pdf-viewer-toolbar">
                <button class="pdf-nav-btn" data-nav="prev">◀</button>
                <span class="pdf-page-indicator"></span>
                <button class="pdf-nav-btn" data-nav="next">▶</button>
                <span class="pdf-hit-label">命中页:</span>
                <span class="pdf-hit-badges"></span>
            </div>
            <div class="pdf-canvas-wrap">
                <div class="pdf-canvas-inner"><canvas></canvas><div class="pdf-overlay"></div></div>
            </div>
        `;
        const badges = this.container.querySelector('.pdf-hit-badges');
        const pages = Object.keys(this.hits).map(Number).sort((a, b) => a - b);
        if (pages.length === 0) {
            this.container.querySelector('.pdf-hit-label').style.display = 'none';
        }
        pages.forEach(p => {
            const b = document.createElement('span');
            b.className = 'pdf-hit-badge';
            b.textContent = p;
            b.dataset.page = p;
            b.addEventListener('click', () => this.gotoPage(p));
            badges.appendChild(b);
        });
        this.container.querySelector('[data-nav="prev"]')
            .addEventListener('click', () => this.gotoPage(this.currentPage - 1));
        this.container.querySelector('[data-nav="next"]')
            .addEventListener('click', () => this.gotoPage(this.currentPage + 1));
    },

    _drawHighlights(pageNum) {
        const canvas = this.container.querySelector('canvas');
        const overlay = this.container.querySelector('.pdf-overlay');
        if (!canvas || !overlay) return;
        overlay.innerHTML = '';
        (this.hits[pageNum] || []).forEach(h => {
            if (!Array.isArray(h.bbox) || h.bbox.length !== 4) return;
            // bbox 与 page_size 同坐标系（左上原点）→ 按 canvas 实际尺寸线性缩放
            const ps = Array.isArray(h.page_size) && h.page_size[0] && h.page_size[1] ? h.page_size : null;
            const sx = ps ? canvas.width / ps[0] : 1;
            const sy = ps ? canvas.height / ps[1] : 1;
            const x0 = h.bbox[0], y0 = h.bbox[1], x1 = h.bbox[2], y1 = h.bbox[3];
            const box = document.createElement('div');
            box.className = 'pdf-highlight-box';
            box.style.left = (x0 * sx) + 'px';
            box.style.top = (y0 * sy) + 'px';
            box.style.width = ((x1 - x0) * sx) + 'px';
            box.style.height = ((y1 - y0) * sy) + 'px';
            overlay.appendChild(box);
        });
    },

    _updateToolbar() {
        const ind = this.container.querySelector('.pdf-page-indicator');
        if (ind) ind.textContent = `${this.currentPage} / ${this.totalPages}`;
        this.container.querySelectorAll('.pdf-hit-badge').forEach(b => {
            b.classList.toggle('active', parseInt(b.dataset.page) === this.currentPage);
        });
    },

    _showMessage(msg) {
        if (!this.container) return;
        const div = document.createElement('div');
        div.className = 'pdf-viewer-placeholder';
        div.textContent = msg;
        this.container.innerHTML = '';
        this.container.appendChild(div);
    },
};
