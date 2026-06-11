# 提取结果 PDF 高亮定位 — 设计文档

日期：2026-06-11
状态：已确认（与用户对齐布局 A / 交互 A）

## 背景与目标

字段抽取结果的 `source_refs` 已携带块级 `bboxes: [{page_num, bbox: [x0,y0,x1,y1], page_size: [w,h]}]`（MinerU 块级框，2026-06-11 上线）。本功能让自带管理 UI（`ui/`）能利用这份数据：在文件详情弹窗的「提取结果」tab 中，点击字段的「定位」按钮，右侧 PDF 预览跳到命中页并画出高亮框。

**用户决策记录：**
- 布局：提取结果 tab 内左右分栏（左字段列表 + 右 PDF 预览常驻），不用全屏 overlay / 独立 tab。
- 多命中交互：点定位后**所有命中页都画框**，跳到首个命中页，导航条列出命中页徽标可点跳转。不做逐条 ref 单独跳转。

## 总体架构

```
ui/js/app.js (extraction tab)        blue_print/file_router.py
  ├─ 左栏: 字段卡片 + 定位按钮          └─ GET /file/{file_id}/pdf
  └─ 右栏: <div id="pdf-panel">            → FileResponse(uploads/{file_id}.pdf)
        ↓ 调用                              → 404 当文件不存在
ui/js/pdfViewer.js (新模块)
  ├─ pdf.js 加载/渲染 canvas
  ├─ bbox 叠加层画框
  └─ 页码导航 + 命中页徽标
ui/vendor/pdfjs/ (本地 vendor)
  ├─ pdf.min.js
  └─ pdf.worker.min.js
```

## 组件设计

### 1. 后端：`GET /file/{file_id}/pdf`

- 位置：`blue_print/file_router.py`。
- 行为：`uploads/{file_id}.pdf` 存在 → `FileResponse(path, media_type="application/pdf")`；不存在 → 404（`ResponseWrapper` 风格错误体与现有接口一致）。
- 不校验 files 表记录是否存在（文件在即可下发；孤儿 PDF 由既有 `cleanup_orphan_pdfs` 治理）。

### 2. pdf.js 本地 vendor

- 取 pdfjs-dist 的 UMD/legacy 构建两个文件：`pdf.min.js`、`pdf.worker.min.js`，放 `ui/vendor/pdfjs/`。
- `index.html` 本地 `<script>` 引入；`workerSrc` 指向本地 worker。**不走 CDN**（Docker 离线部署）。
- 版本固定（vendor 落仓库），升级靠手动替换。

### 3. 前端：提取结果 tab 分栏

- `app.js` extraction 分支改为分栏骨架（复用表格 tab 的 split 布局模式）：
  - 左栏：现有字段卡片（值/原因/检索原文折叠块）不变，标题行追加「📍 定位」按钮。
  - 右栏：`pdf-panel` 容器，初始显示占位提示「点击字段的定位按钮在 PDF 中查看」。
- 定位按钮可用性：该字段 `source_refs` 中**任一 ref 含非空 `bboxes` 或非空 `page_num`** → 可用；否则置灰（含 vl 类 `_vl`、`source_refs=null` 的失败字段）。
- 点定位：
  1. 懒加载：右栏首次使用时才 fetch `/file/{id}/pdf` 并初始化 pdf.js 文档对象（per 弹窗实例缓存，切 tab 不重复下载）。
  2. 遍历该字段所有 ref（`_tables` 数组 + 各关键词数组，跳过 `_texts`/`_vl`），收集 `bboxes` 按 `page_num` 分组成 `{page: [bbox...]}`；同时收集仅有 `page_num` 的 ref 的页码（解析 `"3"` / `"3-5"` 取起始页）。
  3. 跳到最小命中页渲染；导航条显示 `◀ n/N ▶` + 命中页徽标（升序，可点）。
  4. 当前页有框则画框；只有页码无框的命中页，徽标可跳但该页无框。

### 4. pdfViewer.js 模块（新文件，约 200 行）

职责单一：给定 PDF url + `{page: [ {bbox, page_size} ]}` 命中数据，负责渲染与交互。对外接口：

```js
PdfViewer.init(containerEl)                 // 绑定容器，画骨架
PdfViewer.open(pdfUrl)                      // 加载文档（缓存），失败显示降级提示
PdfViewer.locate(hits)                      // hits = {pageNum: [{bbox, page_size}], ...}，跳首页+画框+更新徽标
PdfViewer.gotoPage(n)                       // 翻页（保留当前 hits 的框）
```

- 渲染：canvas 按容器宽度计算 scale（`viewport = page.getViewport({scale: containerWidth / page.getViewport({scale:1}).width})`）。
- 高亮：canvas 外层 `position:relative` 容器内放绝对定位叠加层 div；每个框一个 div，`left/top/width/height = bbox 各值 × (canvas宽 / page_size[0])`。MinerU bbox 与 pdf.js viewport 同为左上原点、page_size 即 1 倍 scale 下的视口尺寸，纯线性缩放即可。翻页只清/重建叠加层。
- 样式：半透明橙色填充 + 2px 边框（与 mockup 一致），css 类 `pdf-highlight-box`。

### 5. 降级路径

| 情况 | 行为 |
|---|---|
| ref 有 `page_num` 无 `bboxes`（存量老数据） | 该页进徽标列表，跳页不画框 |
| 字段所有 ref 均无定位信息（vl 类、失败字段） | 定位按钮置灰，title 提示「该字段无定位信息」 |
| `GET /file/{id}/pdf` 404（老文件未持久化 PDF） | 右栏显示「原始 PDF 不存在（历史文件），重新上传后可用定位」 |
| pdf.js 加载/渲染异常 | 右栏显示错误信息，不影响左栏字段列表 |

## 错误处理

- 后端 404 走统一 `ResponseWrapper` 错误格式（HTTP 404）。
- 前端 fetch/渲染所有异常 catch 后落到右栏提示区，控制台保留原始错误。

## 测试

- 后端 pytest（`tests/test_file_router.py` 或新文件）：上传持久化的 PDF 路径存在 → 200 + `application/pdf`；不存在 → 404。
- 前端无测试框架：手工验收清单——新文件走完管线后定位画框正确；翻页框随页变化；多命中页徽标跳转；vl 字段按钮置灰；删除 uploads 下 PDF 后提示 404 降级。

## 范围外（刻意不做）

- vl 类按 `_vl.key_pages` 跳页（后续可加）。
- 表格 / 分块 tab 的定位入口。
- 行级精度高亮、框的 hover 联动检索原文。
