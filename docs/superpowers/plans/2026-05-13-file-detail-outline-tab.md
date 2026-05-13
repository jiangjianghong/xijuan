# 文件详情页大纲(章节)Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在前端文件详情页加 "大纲" tab,展示基于现有 `parse_sections` 正则解析出来的章节列表,点击章节切到右侧显示该章节正文。

**Architecture:** 后端新增 `GET /file/{file_id}/outline`,直接复用 `service.extraction_service.parse_sections`(零改动),按 `start_pos/end_pos` 切片填 `content` 后返回 `data=[{index, number, title, content, start_pos, end_pos}]`。前端在 `ui/index.html` 现有 3 个 tab 最前面插入"大纲"按钮并设为默认,`ui/js/app.js` 的 `switchTab` 加 `case 'outline'`,UI 复用 tables tab 的左右分栏(`.table-split` / `.data-card` 等现有 CSS 类无需新增样式)。

**Tech Stack:** FastAPI + SQLAlchemy async + httpx + pytest-asyncio + 原生 JS

**关键设计取舍:**

- **口径一致性是核心目标** — 前端大纲里看到的章节,必须 1:1 等于抽取阶段 `search_section` 能匹配到的章节。所以**不**新写 `parse_outline()`,**不**改 `parse_sections` 正则,**不**用 `middle_json` 的标题树。代价:文档若无 `# X` / `# X.X` 这种数字编号标题,大纲为空 — 这是预期行为(那种文档抽取时同样匹配不到)。
- **后端一次性把 content 切好返回**(方案 A) — payload 等于全文大小,但前端零计算 / 一次请求。文档过大时可日后改为方案 B (返回全文 + 索引,前端切片)。
- **复用 tables tab 的左右分栏 UI** — 不引入新 CSS class,样式风险为零。

---

## File Structure

| 文件 | 改动类型 | 责任 |
|---|---|---|
| `blue_print/file_router.py` | Modify(+1 import,+1 路由,约 30 行) | 新增 `GET /{file_id}/outline` |
| `tests/test_file_router.py` | Modify(末尾追加,约 8 行) | 端点可达性测试 |
| `ui/js/api.js` | Modify(+6 行) | 新增 `getFileOutline(fileId)` |
| `ui/index.html` | Modify(改 4 行) | tabs-header 加 outline 按钮 + active 类迁移 |
| `ui/js/app.js` | Modify(改 1 行 + 加 2 块,约 55 行) | `switchTab` 加 `case 'outline'` + 点击绑定 |

零数据库变更,零新增依赖,零 CSS 改动。

---

## Task 1: 后端 — 新增 outline 端点(TDD)

**Files:**
- Modify: `blue_print/file_router.py`(import 区 line 36 附近 + 新路由插在 line 628 之后)
- Modify: `tests/test_file_router.py`(末尾追加)

- [ ] **Step 1: 在 `blue_print/file_router.py` 加 import**

打开 `blue_print/file_router.py`,定位 line 36 `from service.pipeline_service import ...`,在它下面新增一行:

```python
from service.extraction_service import parse_sections
```

- [ ] **Step 2: 在 `tests/test_file_router.py` 末尾追加 failing test**

```python
@pytest.mark.anyio
async def test_get_file_outline_route(client: AsyncClient):
    """测试获取文件大纲(路由可达 + 空集回退)。"""
    resp = await client.get("/file/nonexistent/outline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
```

- [ ] **Step 3: 跑测试确认它失败**

Run: `uv run pytest tests/test_file_router.py::test_get_file_outline_route -v`
Expected: FAIL (404 Not Found — 路由还没实现)

- [ ] **Step 4: 在 `blue_print/file_router.py` 实现端点**

定位 `get_file_chunks`(line 607-628),在它结束之后、`get_file_extraction`(line 629)之前,插入:

```python
@router.get("/{file_id}/outline", response_model=ResponseWrapper)
async def get_file_outline(file_id: str, db: AsyncSession = Depends(get_db)):
    """获取文件大纲(基于 parse_sections 正则解析的章节列表,含切片后的正文)。

    与抽取阶段 search_section 使用的章节口径完全一致 —
    前端看到什么 = 抽取时能匹配到什么。文件不存在或内容为空 → 返回空列表(非 404),
    与 /tables、/chunks 行为一致。
    """
    stmt = select(FileContent).where(FileContent.file_id == file_id)
    result = await db.execute(stmt)
    file_content = result.scalar_one_or_none()

    if not file_content or not file_content.file_content:
        return ResponseWrapper(data=[])

    content = file_content.file_content
    sections = parse_sections(content)

    return ResponseWrapper(
        data=[
            {
                "index": s.index,
                "number": s.number,
                "title": s.title,
                "content": content[s.start_pos:s.end_pos],
                "start_pos": s.start_pos,
                "end_pos": s.end_pos,
            }
            for s in sections
        ]
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_file_router.py::test_get_file_outline_route -v`
Expected: PASS

- [ ] **Step 6: 跑相关测试无回归**

Run: `uv run pytest tests/test_file_router.py tests/test_extraction_service.py -v`
Expected: 全部 PASS(`parse_sections` 没改,extraction_service 测试也应过)

- [ ] **Step 7: Commit**

```bash
git add blue_print/file_router.py tests/test_file_router.py
git commit -m "feat(file): add GET /file/{id}/outline reusing parse_sections"
```

---

## Task 2: 前端 — API 客户端方法

**Files:**
- Modify: `ui/js/api.js`(在 `getAnalysisResults` 后追加,约 line 167 后)

- [ ] **Step 1: 在 `ui/js/api.js` 加 `getFileOutline`**

定位 `getAnalysisResults`(line 164-167),在它的右大括号后、`deleteFile`(line 172)之前插入:

```javascript
    /**
     * 获取文件大纲(章节列表,含正文切片)
     */
    async getFileOutline(fileId) {
        const result = await this.request(`/file/${fileId}/outline`);
        return result.data;
    },

```

- [ ] **Step 2: 浏览器 console 冒烟测试**

启服务:`uv run uvicorn app:app --host 0.0.0.0 --port 5019 --reload`
打开:`http://localhost:5019/ui`,F12 console 执行(替换 file_id 为一个已完成文件的 id):

```javascript
API.getFileOutline('<your_file_id>').then(console.log)
```

Expected: 控制台打印章节数组 `[{index, number, title, content, start_pos, end_pos}, ...]`,或者空数组(如果文档无 `# X` 编号格式)。

---

## Task 3: 前端 — HTML 加 tab 按钮

**Files:**
- Modify: `ui/index.html`(line 500-504)

- [ ] **Step 1: 修改 `<div class="tabs-header">` 块**

定位 line 500-504:

```html
<div class="tabs-header">
    <button class="tab-btn active" data-tab="tables">表格</button>
    <button class="tab-btn" data-tab="extraction">提取结果</button>
    <button class="tab-btn" data-tab="analysis">分析结果</button>
</div>
```

替换为(注意 `active` 类从 `tables` 移到新的 `outline`):

```html
<div class="tabs-header">
    <button class="tab-btn active" data-tab="outline">大纲</button>
    <button class="tab-btn" data-tab="tables">表格</button>
    <button class="tab-btn" data-tab="extraction">提取结果</button>
    <button class="tab-btn" data-tab="analysis">分析结果</button>
</div>
```

---

## Task 4: 前端 — switchTab 加 outline 分支

**Files:**
- Modify: `ui/js/app.js`(line 442 / line 521 之前 / line 610 之后)

- [ ] **Step 1: 把打开详情时默认 tab 从 `'tables'` 改成 `'outline'`**

定位 line 442 `this.switchTab('tables');`,改为:

```javascript
this.switchTab('outline');
```

(HTML 默认 active 类已经在 outline 上了,这步是兜底:防止上一次会话残留状态。)

- [ ] **Step 2: 在 `switch (tab)` 块里插 outline case**

定位 line 520-548 `switch (tab)` 内的 `case 'tables':`,在 `case 'tables':` **之前**插入:

```javascript
                case 'outline':
                    data = await API.getFileOutline(fileId);
                    if (data.length === 0) {
                        html = '<div class="tab-content-empty">暂无章节(文档无 # X 编号标题格式)</div>';
                    } else {
                        let sidebar = '';
                        data.forEach((item, idx) => {
                            const label = `${item.number} ${item.title}`;
                            sidebar += `<div class="table-split-name${idx === 0 ? ' active' : ''}" data-oidx="${idx}" title="${this.escapeHtml(label)}">${this.escapeHtml(label)}</div>`;
                        });
                        const first = data[0];
                        const firstLabel = `${first.number} ${first.title}`;
                        html = `
                            <div class="table-split">
                                <div class="table-split-sidebar">${sidebar}</div>
                                <div class="table-split-content">
                                    <div class="data-card">
                                        <div class="data-card-title">${this.escapeHtml(firstLabel)}</div>
                                        <div class="data-card-content">${this.escapeHtml(first.content)}</div>
                                    </div>
                                </div>
                            </div>
                        `;
                        this._outlineData = data;
                    }
                    break;

```

- [ ] **Step 3: 在 tables 点击绑定块之后加 outline 的对称绑定**

定位 line 610-628 的 `if (tab === 'tables' && this._tablesData ...)` 整个 `if` 块结束之后,追加:

```javascript
            if (tab === 'outline' && this._outlineData && this._outlineData.length > 0) {
                this.els.tabContent.querySelectorAll('.table-split-name').forEach(el => {
                    el.addEventListener('click', () => {
                        const idx = parseInt(el.dataset.oidx);
                        const item = this._outlineData[idx];
                        if (!item) return;
                        this.els.tabContent.querySelectorAll('.table-split-name').forEach(n => n.classList.remove('active'));
                        el.classList.add('active');
                        const contentArea = this.els.tabContent.querySelector('.table-split-content');
                        const label = `${item.number} ${item.title}`;
                        contentArea.innerHTML = `
                            <div class="data-card">
                                <div class="data-card-title">${this.escapeHtml(label)}</div>
                                <div class="data-card-content">${this.escapeHtml(item.content)}</div>
                            </div>
                        `;
                    });
                });
            }
```

---

## Task 5: 端到端浏览器手测 + commit

- [ ] **Step 1: 启动服务**

Run: `uv run uvicorn app:app --host 0.0.0.0 --port 5019 --reload`

- [ ] **Step 2: 用浏览器手测以下场景**

打开 `http://localhost:5019/ui`,选一个**已完成**(progress = complete)的文件打开详情页,逐一验证:

1. 详情页默认显示"大纲" tab(active 在大纲上)
2. 左侧列出所有 `# X 标题` / `# X.X 标题` 格式章节,顺序与文档顺序一致
3. 列表第一项默认 active,右侧 `.data-card-content` 显示该章节正文(换行保留,因为 `.data-card-content` 有 `white-space: pre-wrap`)
4. 点击左侧其它章节 → 右侧切换为该章节正文,active 高亮迁移正确
5. 切到"表格" / "提取结果" / "分析结果" tab,行为与改造前一致(无回归)
6. 选一个**没有** `# X` 编号标题格式的文档,大纲 tab 应显示"暂无章节(文档无 # X 编号标题格式)"

类型检查 / 测试套件不验证 UI 渲染,所以 UI 测试必须真在浏览器里跑过。

- [ ] **Step 3: 验证口径一致性**

如果当前文档类型下存在 `search_type = "section"` 的字段,在调试面板用 `POST /extraction/test` 跑一次该字段(或直接命中页面里的"测试"按钮),核对返回的 `search_results[*].section_title` 与前端大纲里的标题**完全相同**。这是本计划的核心验收点。

- [ ] **Step 4: Commit**

```bash
git add ui/index.html ui/js/api.js ui/js/app.js
git commit -m "feat(ui): 文件详情页新增大纲 tab,基于 parse_sections 渲染章节"
```

---

## Self-Review Checklist

- [x] **Spec coverage:**
  - 复用现有正则 → Task 1 Step 4(直接 import `parse_sections`,不改正则)
  - 后端切好 content(方案 A)→ Task 1 Step 4(列表推导式里 `content[s.start_pos:s.end_pos]`)
  - 前端新 tab 默认显示 → Task 3 Step 1(HTML active) + Task 4 Step 1(JS 兜底)
  - 复用 tables tab 左右分栏 → Task 4 Step 2(沿用 `.table-split`/`.table-split-sidebar`/`.table-split-content`/`.data-card` 类)
  - 点击切换右侧 → Task 4 Step 3
  - 口径一致性 → Task 5 Step 3 验收

- [x] **Placeholder scan:** 无 TBD / TODO / "类似于 task N";所有代码片段完整可粘贴

- [x] **Type / name consistency:**
  - `data-tab="outline"` ↔ `case 'outline'` ↔ `this._outlineData` ↔ `data-oidx` ↔ `parseInt(el.dataset.oidx)`
  - 后端字段 `index/number/title/content/start_pos/end_pos` ↔ 前端 `item.number`/`item.title`/`item.content` 完全对齐
  - import `parse_sections` 来自 `service.extraction_service`,与 `extraction_router.py:30` 同源,无歧义

- [x] **零外溢:** `parse_sections` / `SectionInfo` / `search_section` 零改动;CSS 零改动;数据库零改动;无新依赖

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-file-detail-outline-tab.md`. Two execution options:

**1. Subagent-Driven(推荐)** — 每 task 派一个新 subagent,task 之间我做评审,迭代快

**2. Inline Execution** — 在当前 session 里走 executing-plans,批量执行 + checkpoint 评审

Which approach?
