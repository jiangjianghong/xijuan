/**
 * 主应用模块
 * 上传和重试均使用异步接口，队列进度通过轮询 /file/{id}/status 更新
 */

const App = {
    // 状态
    state: {
        currentPage: 1,
        pageSize: 20,
        statusFilter: '',
        selectedIds: new Set(),
        queue: new Map(), // fileId -> { fileName, stage, progress }
        pollingInterval: null,
        currentFileId: null,
    },

    // DOM 元素引用
    els: {},

    init() {
        this.cacheElements();
        this.bindEvents();
        Toast.init();
        RuleConfig.init();
        this.loadFileList();
        this.restoreProcessingQueue();
        this.startPolling();
        if (typeof lucide !== 'undefined') lucide.createIcons();

        // 初始化导航滑动指示器位置
        const activeBtn = document.querySelector('.nav-btn.active');
        if (activeBtn) {
            // 延迟一帧，确保布局已计算完成
            requestAnimationFrame(() => this.updateNavIndicator(activeBtn));
        }
    },

    /**
     * 页面加载时恢复正在处理中的文件到队列
     */
    async restoreProcessingQueue() {
        try {
            const data = await API.getFileList(1, 100);
            data.items.forEach(item => {
                if (Utils.isProcessing(item.progress)) {
                    this.addToQueue(item.file_id, item.file_name, item.progress,
                        Utils.getStageProgress(item.progress));
                }
            });
        } catch (e) { /* 静默失败 */ }
    },

    cacheElements() {
        this.els = {
            uploadArea: document.getElementById('upload-area'),
            fileInput: document.getElementById('file-input'),
            queueContainer: document.getElementById('queue-container'),
            statusFilter: document.getElementById('status-filter'),
            batchDeleteBtn: document.getElementById('batch-delete-btn'),
            selectAll: document.getElementById('select-all'),
            fileListBody: document.getElementById('file-list-body'),
            pagination: document.getElementById('pagination'),
            drawerOverlay: document.getElementById('drawer-overlay'),
            detailDrawer: document.getElementById('detail-drawer'),
            drawerClose: document.getElementById('drawer-close'),
            drawerFilename: document.getElementById('drawer-filename'),
            drawerFilesize: document.getElementById('drawer-filesize'),
            timeline: document.getElementById('timeline'),
            errorSection: document.getElementById('error-section'),
            errorMessage: document.getElementById('error-message'),
            retryBtn: document.getElementById('retry-btn'),
            tabContent: document.getElementById('tab-content'),
        };
    },

    bindEvents() {
        // 上传区域
        this.els.uploadArea.addEventListener('click', () => this.els.fileInput.click());
        this.els.fileInput.addEventListener('change', (e) => this.handleFiles(e.target.files));

        // 拖拽
        this.els.uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            this.els.uploadArea.classList.add('drag-over');
        });
        this.els.uploadArea.addEventListener('dragleave', () => {
            this.els.uploadArea.classList.remove('drag-over');
        });
        this.els.uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            this.els.uploadArea.classList.remove('drag-over');
            this.handleFiles(e.dataTransfer.files);
        });

        // 筛选
        this.els.statusFilter.addEventListener('change', () => {
            this.state.statusFilter = this.els.statusFilter.value;
            this.state.currentPage = 1;
            this.loadFileList();
        });

        // 全选
        this.els.selectAll.addEventListener('change', (e) => {
            this.toggleSelectAll(e.target.checked);
        });

        // 批量删除
        this.els.batchDeleteBtn.addEventListener('click', () => this.batchDelete());

        // 弹框关闭
        this.els.drawerClose.addEventListener('click', () => this.closeDrawer());
        this.els.drawerOverlay.addEventListener('click', (e) => {
            if (e.target === this.els.drawerOverlay) this.closeDrawer();
        });

        // 重试按钮
        this.els.retryBtn.addEventListener('click', () => this.retryCurrentFile());

        // 行操作 ⋯ 菜单：点击空白 / 滚动 / 缩放时关闭
        document.addEventListener('click', (e) => {
            const menu = document.getElementById('file-action-menu');
            if (menu && menu.classList.contains('open') && !menu.contains(e.target)) {
                this.closeActionMenu();
            }
        });
        window.addEventListener('scroll', () => this.closeActionMenu(), true);
        window.addEventListener('resize', () => this.closeActionMenu());

        // Tab 切换
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.switchTab(e.target.dataset.tab));
        });
    },

    // ─────────────────────────────────────────────────────────
    // 上传处理（异步接口 + 轮询）
    // ─────────────────────────────────────────────────────────

    handleFiles(files) {
        const pdfFiles = Array.from(files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
        if (pdfFiles.length === 0) {
            Toast.error('请选择 PDF 文件');
            return;
        }
        pdfFiles.forEach(file => this.uploadFile(file));
    },

    async uploadFile(file) {
        const tempId = Utils.generateId();
        this.addToQueue(tempId, file.name, 'uploading', 5);

        try {
            const result = await API.uploadFileAsync(file);
            const fileId = result.data && result.data.file_id;

            // 移除临时上传项
            this.removeFromQueue(tempId);

            if (fileId) {
                // 添加到队列，由轮询驱动后续进度
                this.addToQueue(fileId, file.name, 'parsing', Utils.getStageProgress('parsing'));
                Toast.info(`${file.name} 已提交处理`);
            } else {
                // 可能是已完成的文件
                Toast.info(result.message || `${file.name} 已提交`);
            }

            this.loadFileList();
        } catch (error) {
            this.removeFromQueue(tempId);
            Toast.error(`上传失败: ${error.message}`);
        }

        this.els.fileInput.value = '';
    },

    // ─────────────────────────────────────────────────────────
    // 队列管理
    // ─────────────────────────────────────────────────────────

    addToQueue(id, fileName, stage, progress) {
        this.state.queue.set(id, { fileName, stage, progress });
        this.renderQueue();
    },

    updateQueueItem(id, stage, progress) {
        const item = this.state.queue.get(id);
        if (item) {
            item.stage = stage;
            item.progress = progress;
            this.renderQueue();
        }
    },

    removeFromQueue(id) {
        this.state.queue.delete(id);
        this.renderQueue();
    },

    renderQueue() {
        if (this.state.queue.size === 0) {
            this.els.queueContainer.innerHTML = '<div class="queue-empty">暂无处理任务</div>';
            return;
        }

        let html = '';
        this.state.queue.forEach((item, id) => {
            const stageText = item.stage === 'uploading' ? '上传中' : Utils.getStatusText(item.stage);
            html += `
                <div class="queue-card" data-id="${id}">
                    <div class="queue-card-name" title="${item.fileName}">${item.fileName}</div>
                    <div class="queue-progress">
                        <div class="queue-progress-bar" style="width: ${item.progress}%"></div>
                    </div>
                    <div class="queue-card-stage">${stageText}</div>
                </div>
            `;
        });
        this.els.queueContainer.innerHTML = html;
    },

    // ─────────────────────────────────────────────────────────
    // 文件列表
    // ─────────────────────────────────────────────────────────

    async loadFileList() {
        try {
            const data = await API.getFileList(
                this.state.currentPage,
                this.state.pageSize,
                this.state.statusFilter
            );
            this.renderFileList(data);
            this.renderPagination(data);
        } catch (error) {
            Toast.error('加载文件列表失败');
        }
    },

    renderFileList(data) {
        if (data.items.length === 0) {
            this.els.fileListBody.innerHTML = `
                <tr><td colspan="6" style="text-align: center; padding: 40px; color: var(--text-secondary);">
                    暂无文件
                </td></tr>
            `;
            return;
        }

        let html = '';
        this._fileItems = {};
        data.items.forEach((item, index) => {
            this._fileItems[item.file_id] = item;
            const statusClass = Utils.getStatusClass(item.progress);
            const statusText = Utils.getStatusText(item.progress);
            const isSelected = this.state.selectedIds.has(item.file_id);

            html += `
                <tr data-id="${item.file_id}" class="${isSelected ? 'selected' : ''}" style="animation-delay: ${index * 50}ms">
                    <td class="col-checkbox" onclick="event.stopPropagation()">
                        <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="App.toggleSelect('${item.file_id}', this.checked)">
                    </td>
                    <td class="col-name" onclick="App.openDrawer('${item.file_id}')">
                        <span class="file-name-cell" title="${item.file_name}">${item.file_name}</span>
                    </td>
                    <td class="col-size">${Utils.formatFileSize(item.file_size)}</td>
                    <td class="col-time">${Utils.formatDateTime(item.create_time)}</td>
                    <td class="col-status">
                        <span class="status-badge ${statusClass}">${statusText}</span>
                    </td>
                    <td class="col-actions" onclick="event.stopPropagation()">
                        <div class="action-btns">
                            <button class="action-btn" onclick="App.openDrawer('${item.file_id}')" title="查看详情">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                                    <circle cx="12" cy="12" r="3"></circle>
                                </svg>
                            </button>
                            <button class="action-btn" onclick="App.toggleActionMenu(event, '${item.file_id}')" title="更多操作">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                                    <circle cx="12" cy="5" r="2"></circle>
                                    <circle cx="12" cy="12" r="2"></circle>
                                    <circle cx="12" cy="19" r="2"></circle>
                                </svg>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        });
        this.els.fileListBody.innerHTML = html;
        this.updateBatchDeleteBtn();
    },

    renderPagination(data) {
        const { page, total_pages } = data;
        if (total_pages <= 1) {
            this.els.pagination.innerHTML = '';
            return;
        }

        let html = '';
        html += `<button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="App.goToPage(${page - 1})">&lt;</button>`;

        const start = Math.max(1, page - 2);
        const end = Math.min(total_pages, page + 2);

        if (start > 1) {
            html += `<button class="page-btn" onclick="App.goToPage(1)">1</button>`;
            if (start > 2) html += `<span style="padding: 0 4px">...</span>`;
        }

        for (let i = start; i <= end; i++) {
            html += `<button class="page-btn ${i === page ? 'active' : ''}" onclick="App.goToPage(${i})">${i}</button>`;
        }

        if (end < total_pages) {
            if (end < total_pages - 1) html += `<span style="padding: 0 4px">...</span>`;
            html += `<button class="page-btn" onclick="App.goToPage(${total_pages})">${total_pages}</button>`;
        }

        html += `<button class="page-btn" ${page >= total_pages ? 'disabled' : ''} onclick="App.goToPage(${page + 1})">&gt;</button>`;
        this.els.pagination.innerHTML = html;
    },

    goToPage(page) {
        this.state.currentPage = page;
        this.state.selectedIds.clear();
        this.els.selectAll.checked = false;
        this.loadFileList();
    },

    // ─────────────────────────────────────────────────────────
    // 选择与删除
    // ─────────────────────────────────────────────────────────

    toggleSelect(fileId, checked) {
        if (checked) {
            this.state.selectedIds.add(fileId);
        } else {
            this.state.selectedIds.delete(fileId);
        }
        this.updateRowSelection(fileId, checked);
        this.updateBatchDeleteBtn();
        this.updateSelectAllState();
    },

    toggleSelectAll(checked) {
        const rows = this.els.fileListBody.querySelectorAll('tr[data-id]');
        rows.forEach(row => {
            const fileId = row.dataset.id;
            if (checked) {
                this.state.selectedIds.add(fileId);
            } else {
                this.state.selectedIds.delete(fileId);
            }
            row.classList.toggle('selected', checked);
            row.querySelector('input[type="checkbox"]').checked = checked;
        });
        this.updateBatchDeleteBtn();
    },

    updateRowSelection(fileId, checked) {
        const row = this.els.fileListBody.querySelector(`tr[data-id="${fileId}"]`);
        if (row) row.classList.toggle('selected', checked);
    },

    updateSelectAllState() {
        const rows = this.els.fileListBody.querySelectorAll('tr[data-id]');
        this.els.selectAll.checked = rows.length > 0 && this.state.selectedIds.size === rows.length;
    },

    updateBatchDeleteBtn() {
        this.els.batchDeleteBtn.disabled = this.state.selectedIds.size === 0;
    },

    async deleteFile(fileId) {
        if (!confirm('确定要删除此文件吗？')) return;
        try {
            await API.deleteFile(fileId);
            Toast.success('文件已删除');
            this.state.selectedIds.delete(fileId);
            this.removeFromQueue(fileId);
            this.loadFileList();
        } catch (error) {
            Toast.error(`删除失败: ${error.message}`);
        }
    },

    async batchDelete() {
        if (this.state.selectedIds.size === 0) return;
        if (!confirm(`确定要删除选中的 ${this.state.selectedIds.size} 个文件吗？`)) return;
        try {
            const result = await API.batchDeleteFiles(Array.from(this.state.selectedIds));
            Toast.success(`成功删除 ${result.deleted_count} 个文件`);
            if (result.failed_ids.length > 0) {
                Toast.error(`${result.failed_ids.length} 个文件删除失败`);
            }
            this.state.selectedIds.clear();
            this.els.selectAll.checked = false;
            this.loadFileList();
        } catch (error) {
            Toast.error(`批量删除失败: ${error.message}`);
        }
    },

    // ─────────────────────────────────────────────────────────
    // 重试（异步接口 + 轮询）
    // ─────────────────────────────────────────────────────────

    async retryFile(fileId, progress) {
        const stage = Utils.getRetryStage(progress);
        if (!stage) {
            Toast.error(`当前状态不支持重试: ${progress || 'unknown'}`);
            return;
        }

        const row = this.els.fileListBody.querySelector(`tr[data-id="${fileId}"]`);
        const fileName = row ? row.querySelector('.file-name-cell').textContent : 'unknown';

        this.addToQueue(fileId, fileName, stage, Utils.getStageProgress(stage));

        try {
            await API.retryFileAsync(fileId, stage);
            Toast.info(`${fileName} 已开始重试`);
            this.loadFileList();
        } catch (error) {
            this.removeFromQueue(fileId);
            Toast.error(`重试失败: ${error.message}`);
        }
    },

    async retryCurrentFile() {
        if (!this.state.currentFileId) return;
        try {
            const detail = await API.getFileDetail(this.state.currentFileId);
            if (Utils.isFailed(detail.progress)) {
                this.closeDrawer();
                await this.retryFile(this.state.currentFileId, detail.progress);
            }
        } catch (error) {
            Toast.error(`重试失败: ${error.message}`);
        }
    },

    // ─────────────────────────────────────────────────────────
    // 详情抽屉
    // ─────────────────────────────────────────────────────────

    async openDrawer(fileId) {
        this.state.currentFileId = fileId;
        this.els.drawerOverlay.classList.add('active');
        await this.loadDrawerContent(fileId);
    },

    closeDrawer() {
        this.els.drawerOverlay.classList.remove('active');
        this.state.currentFileId = null;
    },

    async loadDrawerContent(fileId) {
        try {
            const detail = await API.getFileDetail(fileId);
            this.els.drawerFilename.textContent = detail.file_name;
            this.els.drawerFilesize.textContent = Utils.formatFileSize(detail.file_size);
            this.renderTimeline(detail);
            this.renderErrorSection(detail);
            this.switchTab('outline');
        } catch (error) {
            Toast.error('加载详情失败');
        }
    },

    renderTimeline(detail) {
        const stages = [
            { key: 'parsing', label: '解析', start: 'start_parsing_time', end: 'end_parsing_time' },
            { key: 'tableing', label: 'AI校验表格名', start: 'start_tableing_time', end: 'end_tableing_time' },
            { key: 'chunking', label: '分块', start: 'start_chunking_time', end: 'end_chunking_time' },
            { key: 'embedding', label: '向量化', start: 'start_embedding_time', end: 'end_embedding_time' },
            { key: 'extracting', label: '提取', start: 'start_extracting_time', end: 'end_extracting_time' },
            { key: 'analyzing', label: '分析', start: 'start_analyzing_time', end: 'end_analyzing_time' },
        ];

        let currentStage = (detail.progress || '').replace('_failed', '');
        if (currentStage === 'table_name_validating') {
            currentStage = 'tableing';
        }
        const isFailed = Utils.isFailed(detail.progress);
        const currentStageIndex = stages.findIndex(s => s.key === currentStage);
        let prevEndTime = detail.start_parsing_time;
        let html = '';

        stages.forEach((stage, index) => {
            let status = 'pending';
            let duration = null;
            // 存量老数据无 start_extracting/analyzing_time，回退用上一阶段结束时间近似
            const startTime = detail[stage.start] || prevEndTime;
            const endTime = stage.end ? detail[stage.end] : null;

            if (endTime) {
                status = 'completed';
                duration = Utils.calcDuration(startTime, endTime);
                prevEndTime = endTime;
            } else if (isFailed && currentStage === stage.key) {
                status = isFailed ? 'failed' : 'processing';
            } else if (!isFailed && currentStage === stage.key) {
                status = 'processing';
            } else if (currentStageIndex !== -1 && index < currentStageIndex) {
                status = 'completed';
            }

            html += `
                <div class="timeline-item ${status}">
                    <div class="timeline-dot ${status}"></div>
                    <span class="timeline-label">${stage.label}</span>
                    <span class="timeline-duration">${Utils.formatDuration(duration)}</span>
                </div>
            `;
        });

        this.els.timeline.innerHTML = html;
    },

    renderErrorSection(detail) {
        if (Utils.isFailed(detail.progress) && detail.error) {
            this.els.errorSection.style.display = 'block';
            this.els.errorMessage.textContent = detail.error;
        } else {
            this.els.errorSection.style.display = 'none';
        }
    },

    // 行操作 ⋯ 菜单：根据文件状态构造菜单项（删除 / 重新提取 / 重新分析 / 重试）
    buildActionMenu(item) {
        const id = item.file_id;
        const isFailed = Utils.isFailed(item.progress);
        const isComplete = item.progress === 'complete';
        let html = '';
        if (isFailed) {
            html += `<button onclick="App.closeActionMenu(); App.retryFile('${id}', '${item.progress}')">重试</button>`;
        }
        html += `<button class="danger" onclick="App.closeActionMenu(); App.deleteFile('${id}')">删除</button>`;
        if (isComplete) {
            html += `<button onclick="App.closeActionMenu(); App.rerunStage('extracting', '${id}')">重新提取</button>`;
            html += `<button onclick="App.closeActionMenu(); App.rerunStage('analyzing', '${id}')">重新分析</button>`;
        }
        return html;
    },

    toggleActionMenu(event, fileId) {
        event.stopPropagation();
        const menu = document.getElementById('file-action-menu');
        if (!menu) return;
        // 再次点击同一按钮 → 关闭
        if (menu.classList.contains('open') && menu.dataset.fileId === fileId) {
            this.closeActionMenu();
            return;
        }
        const item = this._fileItems && this._fileItems[fileId];
        if (!item) return;
        menu.innerHTML = this.buildActionMenu(item);
        menu.dataset.fileId = fileId;
        menu.classList.add('open');
        // 显示后测量尺寸再定位：右对齐 ⋯ 按钮，底部空间不足则向上弹
        const rect = event.currentTarget.getBoundingClientRect();
        const menuRect = menu.getBoundingClientRect();
        let top = rect.bottom + 4;
        if (top + menuRect.height > window.innerHeight - 8) {
            top = Math.max(8, rect.top - menuRect.height - 4);
        }
        let left = rect.right - menuRect.width;
        if (left < 8) left = 8;
        menu.style.top = top + 'px';
        menu.style.left = left + 'px';
    },

    closeActionMenu() {
        const menu = document.getElementById('file-action-menu');
        if (menu) {
            menu.classList.remove('open');
            delete menu.dataset.fileId;
        }
    },

    async rerunStage(stage, fileId) {
        fileId = fileId || this.state.currentFileId;
        if (!fileId) return;

        const isExtract = stage === 'extracting';
        const label = isExtract ? '重新提取' : '重新分析';
        const confirmMsg = isExtract
            ? '重新提取会清空现有「提取结果」和「分析结果」，并重新执行提取 → 分析，确定继续？'
            : '重新分析会清空现有「分析结果」并重新执行分析，确定继续？';
        if (!confirm(confirmMsg)) return;

        const item = this._fileItems && this._fileItems[fileId];
        const fileName = (item && item.file_name) || this.els.drawerFilename.textContent || 'unknown';
        this.closeDrawer();
        this.addToQueue(fileId, fileName, stage, Utils.getStageProgress(stage));

        try {
            await API.retryFileAsync(fileId, stage);
            Toast.info(`${fileName} 已开始${label}`);
            this.loadFileList();
        } catch (error) {
            this.removeFromQueue(fileId);
            Toast.error(`${label}失败: ${error.message}`);
        }
    },

    async switchTab(tab) {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tab);
        });

        if (!this.state.currentFileId) return;

        const fileId = this.state.currentFileId;
        this.els.tabContent.innerHTML = '<div class="tab-content-empty"><span class="spinner"></span></div>';

        try {
            let data;
            let html = '';

            switch (tab) {
                case 'outline':
                    data = await API.getFileOutline(fileId);
                    if (data.length === 0) {
                        html = '<div class="tab-content-empty">暂无章节(文档无标题)</div>';
                    } else {
                        // 显示深度：编号标题按 level（封顶 4 级）缩进，无编号标题顶格
                        const depthOf = (it) => (it.numbered ? Math.min(it.level, 4) - 1 : 0);
                        let sidebar = '';
                        data.forEach((item, idx) => {
                            const label = item.number ? `${item.number} ${item.title}` : item.title;
                            const depth = depthOf(item);
                            const numHtml = item.number
                                ? `<span class="outline-num">${this.escapeHtml(item.number)}</span> `
                                : '';
                            sidebar += `<div class="table-split-name outline-item${idx === 0 ? ' active' : ''}" data-oidx="${idx}" style="padding-left:${8 + depth * 16}px" title="${this.escapeHtml(label)}">${numHtml}${this.escapeHtml(item.title)}</div>`;
                        });
                        const first = data[0];
                        const firstLabel = first.number ? `${first.number} ${first.title}` : first.title;
                        html = `
                            <div class="table-split">
                                <div class="table-split-sidebar">${sidebar}</div>
                                <div class="split-resizer" title="拖动调整宽度"></div>
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

                case 'tables':
                    data = await API.getFileTables(fileId);
                    if (data.length === 0) {
                        html = '<div class="tab-content-empty">暂无表格数据</div>';
                    } else {
                        let sidebar = '';
                        data.forEach((item, idx) => {
                            const name = item.table_name || `表格 ${item.table_index}`;
                            const page = item.page_num ? `<span class="table-split-page">P${item.page_num}</span>` : '';
                            sidebar += `<div class="table-split-name${idx === 0 ? ' active' : ''}" data-tidx="${idx}" title="${this.escapeHtml(name)}">${this.escapeHtml(name)}${page}</div>`;
                        });
                        const first = data[0];
                        const firstPage = first.page_num ? `<span class="data-card-page">第 ${first.page_num} 页</span>` : '';
                        html = `
                            <div class="table-split">
                                <div class="table-split-sidebar">${sidebar}</div>
                                <div class="split-resizer" title="拖动调整宽度"></div>
                                <div class="table-split-content">
                                    <div class="data-card">
                                        <div class="data-card-title">${this.escapeHtml(first.table_name || `表格 ${first.table_index}`)}${firstPage}</div>
                                        <div class="data-card-content table-rendered">${Utils.sanitizeTableHtml(first.table_content)}</div>
                                    </div>
                                </div>
                            </div>
                        `;
                        // Store table data for click switching
                        this._tablesData = data;
                    }
                    break;

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
                                <div class="split-resizer" title="拖动调整宽度"></div>
                                <div class="pdf-split-right" id="pdf-panel"></div>
                            </div>
                        `;
                        this._extractionData = data;
                    }
                    break;

                case 'analysis':
                    data = await API.getAnalysisResults(fileId);
                    if (data.length === 0) {
                        html = '<div class="tab-content-empty">暂无分析结果</div>';
                    } else {
                        data.forEach(item => {
                            const title = item.rule_name || item.rule_id;
                            const subtitle = item.rule_name ? item.rule_id : '';
                            html += `
                                <div class="data-card">
                                    <div class="data-card-title">${this.escapeHtml(title)}${subtitle ? `<span class="data-card-subtitle">${this.escapeHtml(subtitle)}</span>` : ''}</div>
                                    <div class="data-card-field">
                                        <span class="data-card-field-label">结果:</span>
                                        <span class="data-card-field-value">${this.escapeHtml(item.result_value) || '-'}</span>
                                    </div>
                                    ${item.input_values ? `
                                        <div class="data-card-field">
                                            <span class="data-card-field-label">输入:</span>
                                            <span class="data-card-field-value">${this.escapeHtml(JSON.stringify(item.input_values))}</span>
                                        </div>
                                    ` : ''}
                                    ${item.reason ? `
                                        <div class="data-card-field">
                                            <span class="data-card-field-label">原因:</span>
                                            <span class="data-card-field-value">${this.escapeHtml(item.reason)}</span>
                                        </div>
                                    ` : ''}
                                    ${this.renderWebSearchRefs(item.source_refs)}
                                </div>
                            `;
                        });
                    }
                    break;
            }

            this.els.tabContent.innerHTML = html;

            // 绑定分栏拖拽条（大纲 / 表格 / 提取 PDF 均为左右分栏）
            this.initSplitResizers();

            // Bind table sidebar click events
            if (tab === 'tables' && this._tablesData && this._tablesData.length > 0) {
                this.els.tabContent.querySelectorAll('.table-split-name').forEach(el => {
                    el.addEventListener('click', () => {
                        const idx = parseInt(el.dataset.tidx);
                        const item = this._tablesData[idx];
                        if (!item) return;
                        this.els.tabContent.querySelectorAll('.table-split-name').forEach(n => n.classList.remove('active'));
                        el.classList.add('active');
                        const contentArea = this.els.tabContent.querySelector('.table-split-content');
                        const pageTag = item.page_num ? `<span class="data-card-page">第 ${item.page_num} 页</span>` : '';
                        contentArea.innerHTML = `
                            <div class="data-card">
                                <div class="data-card-title">${this.escapeHtml(item.table_name || `表格 ${item.table_index}`)}${pageTag}</div>
                                <div class="data-card-content table-rendered">${Utils.sanitizeTableHtml(item.table_content)}</div>
                            </div>
                        `;
                    });
                });
            }

            // Bind extraction locate buttons
            if (tab === 'extraction' && this._extractionData && this._extractionData.length > 0) {
                const panel = this.els.tabContent.querySelector('#pdf-panel');
                if (panel) PdfViewer.init(panel);
                this.els.tabContent.querySelectorAll('.pdf-locate-btn:not([disabled])').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const item = this._extractionData[parseInt(btn.dataset.fidx)];
                        if (!item || !panel) return;
                        const hits = this.collectLocateHits(item.source_refs);
                        const preferPage = this.preferredLocatePage(item.source_refs);
                        PdfViewer.openAndLocate(`/file/${fileId}/pdf`, hits, preferPage);
                    });
                });
            }

            if (tab === 'outline' && this._outlineData && this._outlineData.length > 0) {
                this.els.tabContent.querySelectorAll('.table-split-name').forEach(el => {
                    el.addEventListener('click', () => {
                        const idx = parseInt(el.dataset.oidx);
                        const item = this._outlineData[idx];
                        if (!item) return;
                        this.els.tabContent.querySelectorAll('.table-split-name').forEach(n => n.classList.remove('active'));
                        el.classList.add('active');
                        const contentArea = this.els.tabContent.querySelector('.table-split-content');
                        const label = item.number ? `${item.number} ${item.title}` : item.title;
                        contentArea.innerHTML = `
                            <div class="data-card">
                                <div class="data-card-title">${this.escapeHtml(label)}</div>
                                <div class="data-card-content">${this.escapeHtml(item.content)}</div>
                            </div>
                        `;
                    });
                });
            }
        } catch (error) {
            this.els.tabContent.innerHTML = '<div class="tab-content-empty">加载失败</div>';
        }
    },

    escapeHtml(text) {
        return Utils.escapeHtml(text);
    },

    // 分栏左侧栏拖拽调宽：拖动 .split-resizer 改变其前一个兄弟（侧栏/左栏）的宽度
    initSplitResizers() {
        this.els.tabContent.querySelectorAll('.split-resizer').forEach(resizer => {
            const sidebar = resizer.previousElementSibling;
            if (!sidebar) return;
            const isPdf = sidebar.classList.contains('pdf-split-left');
            resizer.addEventListener('mousedown', (e) => {
                e.preventDefault();
                const startX = e.clientX;
                const startW = sidebar.getBoundingClientRect().width;
                const parentW = resizer.parentElement.getBoundingClientRect().width;
                const maxW = Math.max(200, parentW - 260);  // 给右侧内容留出最小空间
                const onMove = (ev) => {
                    let w = startW + (ev.clientX - startX);
                    w = Math.max(140, Math.min(w, maxW));
                    sidebar.style.width = w + 'px';
                    sidebar.style.minWidth = w + 'px';
                    if (isPdf) sidebar.style.flex = 'none';  // pdf-split-left 原本 width:42%，改为固定宽
                };
                const onUp = () => {
                    document.removeEventListener('mousemove', onMove);
                    document.removeEventListener('mouseup', onUp);
                    document.body.style.userSelect = '';
                    document.body.style.cursor = '';
                    // PDF 预览按容器宽渲染，拖动后重绘当前页以适配新宽度
                    if (isPdf && PdfViewer.pdfDoc) PdfViewer.gotoPage(PdfViewer.currentPage);
                };
                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onUp);
                document.body.style.userSelect = 'none';
                document.body.style.cursor = 'col-resize';
            });
        });
    },

    // 渲染 source_refs 的「检索原文」折叠区块（老数据无 text/_texts 时返回空串）
    renderSourceRefs(sourceRefs) {
        if (!sourceRefs || typeof sourceRefs !== 'object') return '';
        const segs = [];
        for (const [label, refs] of Object.entries(sourceRefs)) {
            if (label === '_texts' || label === '_vl' || !Array.isArray(refs)) continue;
            refs.forEach(ref => {
                if (!ref || !ref.text) return;
                segs.push({ label, type: ref.type || '', page: ref.page_num || '', text: ref.text });
            });
        }
        if (segs.length === 0) return '';
        let inner = '';
        segs.forEach(seg => {
            const labelText = seg.label && seg.label !== '_tables' ? seg.label : '';
            const meta = [labelText, seg.type, seg.page ? `第 ${seg.page} 页` : '']
                .filter(Boolean).map(m => this.escapeHtml(m)).join(' · ');
            inner += `
                <div class="source-ref-seg">
                    <div class="source-ref-meta">${meta}</div>
                    <div class="source-ref-text">${this.escapeHtml(seg.text)}</div>
                </div>
            `;
        });
        return `
            <details class="source-refs">
                <summary>检索原文（${segs.length} 段）</summary>
                ${inner}
            </details>
        `;
    },

    // 取「最佳定位页」：source_refs 各分组已按包含相似度降序排好，
    // 取首条带整数 page_num 的 ref 所在页作为默认落地页（vl 的 _vl 无 page_num 跳过）。
    // 返回 null 表示无可用页码，调用方回退到最小命中页。
    preferredLocatePage(sourceRefs) {
        if (!sourceRefs || typeof sourceRefs !== 'object') return null;
        for (const [label, refs] of Object.entries(sourceRefs)) {
            if (label === '_texts' || label === '_vl' || !Array.isArray(refs)) continue;
            for (const ref of refs) {
                if (!ref) continue;
                const m = String(ref.page_num || '').match(/^(\d+)/);
                if (m) return parseInt(m[1]);
            }
        }
        return null;
    },

    // 从 source_refs 收集定位命中：{页码int: [{bbox, page_size}...]}
    // 有 bboxes 的 ref 进框列表；仅有 page_num 的老数据 ref 只登记页码（空数组=跳页无框）；
    // vl 类读 _vl.key_pages 登记跳页（vl_progressive 无 key_pages 仍不可定位）
    collectLocateHits(sourceRefs) {
        const hits = {};
        if (!sourceRefs || typeof sourceRefs !== 'object') return hits;
        for (const [label, refs] of Object.entries(sourceRefs)) {
            if (label === '_vl') {
                const keyPages = refs && Array.isArray(refs.key_pages) ? refs.key_pages : [];
                keyPages.forEach(p => {
                    const n = parseInt(p);
                    if (n >= 1) hits[n] = hits[n] || [];
                });
                continue;
            }
            if (label === '_texts' || !Array.isArray(refs)) continue;
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

    // 渲染分析结果 source_refs._web_search 的「网络搜索」折叠区块（无搜索数据返回空串）
    renderWebSearchRefs(sourceRefs) {
        if (!sourceRefs || typeof sourceRefs !== 'object') return '';
        const ws = sourceRefs._web_search;
        if (!ws || !ws.query) return '';
        const results = ws.results || [];
        let inner = `
            <div class="source-ref-seg">
                <div class="source-ref-meta">搜索词${ws.error ? ' · 搜索失败' : ''}</div>
                <div class="source-ref-text">${this.escapeHtml(ws.query)}</div>
            </div>
        `;
        results.forEach((r, i) => {
            const date = (r.datePublished || '').slice(0, 10);
            const meta = [`[${i + 1}] ${r.name || ''}`, r.siteName || '', date]
                .filter(Boolean).map(m => this.escapeHtml(m)).join(' · ');
            inner += `
                <div class="source-ref-seg">
                    <div class="source-ref-meta">${meta}</div>
                    <div class="source-ref-text">${this.escapeHtml(r.summary || '')}</div>
                </div>
            `;
        });
        return `
            <details class="source-refs">
                <summary>网络搜索（${results.length} 条）</summary>
                ${inner}
            </details>
        `;
    },

    // ─────────────────────────────────────────────────────────
    // 页面切换
    // ─────────────────────────────────────────────────────────

    switchPage(page) {
        // 切换导航按钮
        let targetBtn = null;
        document.querySelectorAll('.nav-btn').forEach(btn => {
            const match = btn.dataset.page === page;
            btn.classList.toggle('active', match);
            if (match) targetBtn = btn;
        });

        // 滑动指示器
        if (targetBtn) this.updateNavIndicator(targetBtn);

        // 切换页面容器
        document.querySelectorAll('.page-container').forEach(el => {
            el.classList.remove('active');
        });
        const target = document.getElementById('page-' + page);
        if (target) target.classList.add('active');

        // 首次进入规则配置时加载数据
        if (page === 'rule-config' && !RuleConfig.state.loaded.fields) {
            RuleConfig.loadFields();
        }

        if (typeof LogViewer !== 'undefined') {
            if (page === 'runtime-logs') {
                LogViewer.activate();
            } else {
                LogViewer.deactivate();
            }
        }
    },

    updateNavIndicator(btn) {
        const indicator = document.querySelector('.nav-indicator');
        const nav = document.querySelector('.header-nav');
        if (!indicator || !nav || !btn) return;
        const navRect = nav.getBoundingClientRect();
        const btnRect = btn.getBoundingClientRect();
        const left = btnRect.left - navRect.left;
        indicator.style.width = btnRect.width + 'px';
        indicator.style.transform = `translateX(${left}px)`;
    },

    // ─────────────────────────────────────────────────────────
    // 轮询（统一驱动队列状态更新）
    // ─────────────────────────────────────────────────────────

    startPolling() {
        this.state.pollingInterval = setInterval(() => this.pollQueueStatus(), 3000);
    },

    async pollQueueStatus() {
        if (this.state.queue.size === 0) return;

        // 只轮询真实 file_id，跳过临时 uploading ID（长度较短）
        const entries = Array.from(this.state.queue.entries()).filter(
            ([id, item]) => item.stage !== 'uploading'
        );

        for (const [fileId, item] of entries) {
            try {
                const status = await API.getFileStatus(fileId);

                if (Utils.isProcessing(status.progress)) {
                    // 更新队列中的阶段和进度
                    this.updateQueueItem(fileId, status.progress, Utils.getStageProgress(status.progress));
                } else {
                    // 完成或失败，从队列移除
                    this.removeFromQueue(fileId);
                    this.loadFileList();

                    if (status.progress === 'complete') {
                        Toast.success(`${item.fileName} 处理完成`);
                    } else if (Utils.isFailed(status.progress)) {
                        Toast.error(`${item.fileName} 处理失败`);
                    }

                    // 如果抽屉正在显示此文件，刷新详情
                    if (this.state.currentFileId === fileId) {
                        this.loadDrawerContent(fileId);
                    }
                }
            } catch (error) {
                // 文件可能已被删除
                this.removeFromQueue(fileId);
            }
        }
    },
};

// 启动应用
document.addEventListener('DOMContentLoaded', () => App.init());
