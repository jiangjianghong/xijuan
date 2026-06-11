# 提取结果 PDF 高亮定位 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理 UI 提取结果 tab 改左右分栏，点字段「定位」按钮在右侧 PDF 预览中跳到命中页并按 `source_refs.bboxes` 画高亮框。

**Architecture:** 后端加一个薄路由 `GET /file/{file_id}/pdf` 下发已持久化的 `uploads/{file_id}.pdf`；前端 vendor pdf.js（本地，不走 CDN），新模块 `ui/js/pdfViewer.js` 负责渲染 canvas + 绝对定位叠加层画框，`app.js` 的 extraction 分支只搭分栏骨架、收集命中数据并调用 viewer。Spec: `docs/superpowers/specs/2026-06-11-pdf-highlight-design.md`。

**Tech Stack:** FastAPI / pytest（后端）；原生 JS + pdfjs-dist 3.11.174 UMD 构建（前端，无构建系统、无前端测试框架）。

**关键代码事实（写代码前不用再找）：**
- PDF 落盘路径：`utils/vl_client.py:pdf_path(file_id)` 返回 `{pdf_storage_dir}/{file_id}.pdf` 的绝对 Path（上传时已写盘）。
- `blue_print/file_router.py` 第 9 行现有 `from fastapi.responses import StreamingResponse`；404 惯例 `raise HTTPException(status_code=404, detail="...")`。
- UI 详情弹窗 tab 按钮在 `ui/index.html:580-583`（outline/tables/extraction/analysis），内容容器 `#tab-content`；脚本引入块在 778-785 行。
- `ui/js/app.js`：`loadTabContent(fileId, tab)` 的 switch 中 `case 'extraction':` 在约 600-626 行；`renderSourceRefs` 在约 714 行（遍历 source_refs 跳过 `_texts`/`_vl` 的写法可参考）；表格 tab 的事件绑定模式在约 664-703 行（innerHTML 后 querySelectorAll + addEventListener）。
- `bboxes` 数据形状：ref 内 `bboxes: [{page_num: int, bbox: [x0,y0,x1,y1], page_size: [w,h]}]`；ref 级 `page_num` 是字符串（`"3"` 或 `"3-5"`）。老数据 ref 无 `bboxes` 键；vl 类只有 `_vl` 键；失败字段 `source_refs=null`。
- CSS 变量 `--border` 等已存在（`ui/css/style.css`）；表格 tab 分栏类 `.table-split*` 在 768-835 行可参考但不复用（提取分栏左栏是宽卡片，单独建类）。
- 测试惯例：`tests/test_file_router.py` 用 `@pytest.mark.anyio` + `client: AsyncClient` fixture（conftest 提供，ASGITransport）。

---

### Task 1: 后端 `GET /file/{file_id}/pdf`

**Files:**
- Modify: `blue_print/file_router.py`
- Test: `tests/test_file_router.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_file_router.py` 末尾追加：

```python
@pytest.mark.anyio
async def test_get_file_pdf_200(client: AsyncClient):
    """uploads 下存在 PDF 时应 200 并返回 application/pdf 原始字节。"""
    from utils import vl_client

    file_id = "test_pdf_endpoint_file"
    pdf = vl_client.pdf_path(file_id)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 minimal")
    try:
        resp = await client.get(f"/file/{file_id}/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        assert resp.content == b"%PDF-1.4 minimal"
    finally:
        pdf.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_get_file_pdf_404(client: AsyncClient):
    """PDF 不存在时应 404。"""
    resp = await client.get("/file/nonexistent_pdf_file/pdf")
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_file_router.py -v -k pdf`
Expected: `test_get_file_pdf_200` FAIL（404，路由不存在）

- [ ] **Step 3: 实现路由**

3a. `blue_print/file_router.py` 第 9 行 import 改为：

```python
from fastapi.responses import FileResponse, StreamingResponse
```

3b. 在文件中现有 `GET /{file_id}/...` 路由邻近处（如 `get_file_status` 之后）新增：

```python
@router.get("/{file_id}/pdf")
async def get_file_pdf(file_id: str):
    """下发原始 PDF（uploads/{file_id}.pdf 原始字节），供前端定位预览。

    历史文件可能未持久化 PDF（vl 持久化机制上线前上传），此时 404。
    """
    from utils import vl_client

    pdf = vl_client.pdf_path(file_id)
    if not pdf.is_file():
        raise HTTPException(status_code=404, detail="原始 PDF 不存在")
    return FileResponse(pdf, media_type="application/pdf", filename=f"{file_id}.pdf")
```

（`vl_client` 用函数内 import，与本文件 226-238 行处理 PDF 落盘的既有写法一致。）

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_file_router.py -v`
Expected: 全部 PASS（含 2 个新测试）

- [ ] **Step 5: 提交**

```bash
git add blue_print/file_router.py tests/test_file_router.py
git commit -m "feat: 新增 GET /file/{file_id}/pdf 下发原始 PDF"
```

---

### Task 2: vendor pdf.js + index.html 引入

**Files:**
- Create: `ui/vendor/pdfjs/pdf.min.js`、`ui/vendor/pdfjs/pdf.worker.min.js`（下载）
- Modify: `ui/index.html`

- [ ] **Step 1: 下载 pdfjs-dist 3.11.174 UMD 构建（最后一个 UMD 大版本，全局暴露 `pdfjsLib`）**

```bash
mkdir -p ui/vendor/pdfjs
curl -L -o ui/vendor/pdfjs/pdf.min.js https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js
curl -L -o ui/vendor/pdfjs/pdf.worker.min.js https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js
```

- [ ] **Step 2: 验证下载产物**

```bash
ls -la ui/vendor/pdfjs/ && head -c 80 ui/vendor/pdfjs/pdf.min.js; echo; head -c 80 ui/vendor/pdfjs/pdf.worker.min.js
```

Expected: 两个文件均 >100KB，开头是 JS 代码（含版权注释/`!function`），**不是** HTML 错误页。若下载失败，备用源：`https://unpkg.com/pdfjs-dist@3.11.174/build/pdf.min.js` 与 `https://unpkg.com/pdfjs-dist@3.11.174/build/pdf.worker.min.js`。

- [ ] **Step 3: index.html 引入**

`ui/index.html` 脚本块（778 行附近，`<script src="js/utils.js"></script>` 之前）插入：

```html
    <script src="vendor/pdfjs/pdf.min.js"></script>
    <script>
        if (window.pdfjsLib) {
            pdfjsLib.GlobalWorkerOptions.workerSrc = 'vendor/pdfjs/pdf.worker.min.js';
        }
    </script>
```

同时在 `<script src="js/app.js"></script>` 之前插入（pdfViewer.js 在 Task 3 创建，先占位引入不报错——浏览器对 404 的 script 静默跳过，且本任务后立即做 Task 3）：

```html
    <script src="js/pdfViewer.js"></script>
```

- [ ] **Step 4: 提交**

```bash
git add ui/vendor/pdfjs/ ui/index.html
git commit -m "feat: vendor pdf.js 3.11.174 并接入 index.html"
```

---

### Task 3: pdfViewer.js 模块 + CSS

**Files:**
- Create: `ui/js/pdfViewer.js`
- Modify: `ui/css/style.css`（末尾追加）

- [ ] **Step 1: 创建 `ui/js/pdfViewer.js`**

```javascript
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
```

- [ ] **Step 2: `ui/css/style.css` 末尾追加样式**

```css
/* ── PDF 定位分栏（提取结果 tab）── */
.pdf-split {
    display: flex;
    flex: 1;
    min-height: 0;
    height: 100%;
    overflow: hidden;
}

.pdf-split-left {
    width: 42%;
    min-width: 300px;
    overflow-y: auto;
    padding: 12px;
    border-right: 1px solid var(--border);
}

.pdf-split-right {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.pdf-viewer-placeholder {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-muted, #888);
    font-size: 13px;
    padding: 24px;
    text-align: center;
}

.pdf-viewer-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
    flex-shrink: 0;
}

.pdf-nav-btn {
    border: 1px solid var(--border);
    background: transparent;
    border-radius: 4px;
    padding: 2px 10px;
    cursor: pointer;
    font-size: 12px;
}

.pdf-nav-btn:hover { background: rgba(128, 128, 128, 0.12); }

.pdf-page-indicator { font-size: 12px; min-width: 56px; text-align: center; }

.pdf-hit-label { font-size: 12px; color: var(--text-muted, #888); }

.pdf-hit-badge {
    cursor: pointer;
    padding: 1px 8px;
    border-radius: 10px;
    border: 1px solid var(--border);
    font-size: 12px;
}

.pdf-hit-badge.active {
    background: #f59e0b;
    border-color: #f59e0b;
    color: #fff;
}

.pdf-canvas-wrap {
    flex: 1;
    overflow: auto;
    padding: 8px;
}

.pdf-canvas-inner {
    position: relative;
    width: max-content;
    margin: 0 auto;
}

.pdf-canvas-inner canvas { display: block; }

.pdf-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
}

.pdf-highlight-box {
    position: absolute;
    background: rgba(255, 170, 0, 0.28);
    border: 2px solid #f59e0b;
    border-radius: 2px;
}

.pdf-locate-btn {
    margin-left: auto;
    border: 1px solid var(--border);
    background: transparent;
    border-radius: 4px;
    padding: 1px 8px;
    cursor: pointer;
    font-size: 12px;
    flex-shrink: 0;
}

.pdf-locate-btn:hover:not([disabled]) { background: rgba(245, 158, 11, 0.15); }

.pdf-locate-btn[disabled] { opacity: 0.4; cursor: not-allowed; }
```

- [ ] **Step 3: 语法自检**

```bash
node --check ui/js/pdfViewer.js
```

Expected: 无输出（语法合法）。若环境无 node，跳过并在报告中注明。

- [ ] **Step 4: 提交**

```bash
git add ui/js/pdfViewer.js ui/css/style.css
git commit -m "feat: PdfViewer 模块(渲染+高亮叠加层+命中页导航)"
```

---

### Task 4: app.js 提取结果分栏 + 定位按钮接线

**Files:**
- Modify: `ui/js/app.js`（`case 'extraction':` 约 600-626 行；事件绑定区约 664-703 行；`renderSourceRefs` 附近新增方法）

- [ ] **Step 1: 替换 `case 'extraction':` 分支**

将现有分支（约 600-626 行）整体替换为：

```javascript
                case 'extraction':
                    data = await API.getExtractionResults(fileId);
                    if (data.length === 0) {
                        html = '<div class="tab-content-empty">暂无提取结果</div>';
                    } else {
                        let cards = '';
                        data.forEach((item, idx) => {
                            const title = item.field_name || item.field_id;
                            const subtitle = item.field_name ? item.field_id : '';
                            const hasHits = Object.keys(this.collectLocateHits(item.source_refs)).length > 0;
                            const locateBtn = `<button class="pdf-locate-btn" data-fidx="${idx}"${hasHits ? '' : ' disabled title="该字段无定位信息"'}>📍 定位</button>`;
                            cards += `
                                <div class="data-card">
                                    <div class="data-card-title">${this.escapeHtml(title)}${subtitle ? `<span class="data-card-subtitle">${this.escapeHtml(subtitle)}</span>` : ''}${locateBtn}</div>
                                    <div class="data-card-field">
                                        <span class="data-card-field-label">值:</span>
                                        <span class="data-card-field-value">${this.escapeHtml(item.extracted_value) || '-'}</span>
                                    </div>
                                    ${item.reason ? `
                                        <div class="data-card-field">
                                            <span class="data-card-field-label">原因:</span>
                                            <span class="data-card-field-value">${this.escapeHtml(item.reason)}</span>
                                        </div>
                                    ` : ''}
                                    ${this.renderSourceRefs(item.source_refs)}
                                </div>
                            `;
                        });
                        html = `
                            <div class="pdf-split">
                                <div class="pdf-split-left">${cards}</div>
                                <div class="pdf-split-right" id="pdf-panel"></div>
                            </div>
                        `;
                        this._extractionData = data;
                    }
                    break;
```

注意：`.data-card-title` 现有样式若非 flex，定位按钮的 `margin-left: auto` 不生效——在 Task 3 的 CSS 基础上确认 `style.css` 中 `.data-card-title` 是否 `display:flex`；若不是，本任务在 `style.css` 末尾追加：

```css
.pdf-split-left .data-card-title {
    display: flex;
    align-items: center;
    gap: 6px;
}
```

- [ ] **Step 2: innerHTML 后绑定定位按钮事件**

在 `this.els.tabContent.innerHTML = html;` 之后、现有 `if (tab === 'tables' ...)` 绑定块附近，追加：

```javascript
            // Bind extraction locate buttons
            if (tab === 'extraction' && this._extractionData && this._extractionData.length > 0) {
                const panel = this.els.tabContent.querySelector('#pdf-panel');
                if (panel) PdfViewer.init(panel);
                this.els.tabContent.querySelectorAll('.pdf-locate-btn:not([disabled])').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const item = this._extractionData[parseInt(btn.dataset.fidx)];
                        if (!item || !panel) return;
                        const hits = this.collectLocateHits(item.source_refs);
                        PdfViewer.openAndLocate(`/file/${fileId}/pdf`, hits);
                    });
                });
            }
```

- [ ] **Step 3: 新增 `collectLocateHits` 方法**

在 `renderSourceRefs` 方法之后追加：

```javascript
    // 从 source_refs 收集定位命中：{页码int: [{bbox, page_size}...]}
    // 有 bboxes 的 ref 进框列表；仅有 page_num 的老数据 ref 只登记页码（空数组=跳页无框）
    collectLocateHits(sourceRefs) {
        const hits = {};
        if (!sourceRefs || typeof sourceRefs !== 'object') return hits;
        for (const [label, refs] of Object.entries(sourceRefs)) {
            if (label === '_texts' || label === '_vl' || !Array.isArray(refs)) continue;
            refs.forEach(ref => {
                if (!ref) return;
                if (Array.isArray(ref.bboxes) && ref.bboxes.length > 0) {
                    ref.bboxes.forEach(b => {
                        if (!b || !Array.isArray(b.bbox)) return;
                        (hits[b.page_num] = hits[b.page_num] || []).push({ bbox: b.bbox, page_size: b.page_size });
                    });
                } else if (ref.page_num) {
                    const m = String(ref.page_num).match(/^(\d+)/);
                    if (m) {
                        const p = parseInt(m[1]);
                        hits[p] = hits[p] || [];
                    }
                }
            });
        }
        return hits;
    },
```

- [ ] **Step 4: 语法自检**

```bash
node --check ui/js/app.js
```

Expected: 无输出。若环境无 node，跳过并注明。

- [ ] **Step 5: 提交**

```bash
git add ui/js/app.js ui/css/style.css
git commit -m "feat: 提取结果 tab 左右分栏接入 PDF 定位"
```

---

### Task 5: 文档同步

**Files:**
- Modify: `CLAUDE.md`（`### Layer Structure` 的 `ui/` bullet）
- Modify: `scripts/gen_openapi.py`（enrichment 映射）
- Regenerate: `docs/openapi.json`

- [ ] **Step 1: CLAUDE.md**

`### Layer Structure` 中 `ui/` bullet 末尾追加一句（原文保持不动，只在句末加）：

> Extraction tab is a left/right split: field cards (with 📍 locate buttons) + pdf.js viewer that jumps to hit pages and draws `source_refs.bboxes` highlight boxes (`ui/js/pdfViewer.js`, served via `GET /file/{id}/pdf`).

- [ ] **Step 2: gen_openapi.py**

在包含 `"/file/{file_id}/extraction"` 的 enrichment 映射中新增同级键：

```python
    "/file/{file_id}/pdf": {
        "get": {
            "summary": "下载原始 PDF",
            "description": (
                "下发 `uploads/{file_id}.pdf` 原始字节（`application/pdf`），供前端 PDF 定位预览。\n\n"
                "历史文件（vl 持久化机制上线前上传）可能无落盘 PDF，此时 404。"
            ),
        }
    },
```

- [ ] **Step 3: 重新生成产物**

```bash
uv run python scripts/gen_openapi.py
```

Expected: 无报错；`docs/openapi.json` diff 中新增 `/file/{file_id}/pdf` 路径。脚本原有的 4 个 uncovered operations 警告应消失或减少为不含本接口（本接口已 enrich）。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md scripts/gen_openapi.py docs/openapi.json
git commit -m "docs: PDF 下发接口与 UI 定位功能说明同步"
```

---

## 手工验收清单（实施完成后人工过一遍）

- [ ] `python app.py` 启动，`/ui` 打开，上传 PDF 走完管线
- [ ] 提取结果 tab 呈左右分栏；text/table 类字段「定位」可点，vl 类置灰
- [ ] 点定位：右栏加载 PDF、跳首个命中页、画出橙色框，框的位置与命中文字段落吻合
- [ ] 多命中页徽标显示且可点跳转；◀ ▶ 翻页后框只在命中页出现
- [ ] 手动改库造一条只有 `page_num` 无 `bboxes` 的 ref → 定位跳页无框
- [ ] 临时移走 `uploads/{file_id}.pdf` → 点定位右栏提示「原始 PDF 不存在」
- [ ] 窗口缩放后再点定位/翻页,框位置仍正确（按 canvas 实际尺寸缩放）
