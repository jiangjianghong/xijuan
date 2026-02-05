/**
 * 主应用模块
 */

const App = {
    // 状态
    state: {
        currentPage: 1,
        pageSize: 20,
        statusFilter: '',
        selectedIds: new Set(),
        queue: new Map(), // fileId -> { fileName, stage, progress }
        pollingFiles: new Set(), // 需要轮询的文件 ID
        currentFileId: null,
    },

    // DOM 元素引用
    els: {},

    /**
     * 初始化应用
     */
    init() {
        this.cacheElements();
        this.bindEvents();
        Toast.init();
        this.loadFileList();
        this.startPolling();
    },

    /**
     * 缓存 DOM 元素
     */
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

    /**
     * 绑定事件
     */
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

        // 抽屉关闭
        this.els.drawerClose.addEventListener('click', () => this.closeDrawer());
        this.els.drawerOverlay.addEventListener('click', () => this.closeDrawer());

        // 重试按钮
        this.els.retryBtn.addEventListener('click', () => this.retryCurrentFile());

        // Tab 切换
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.switchTab(e.target.dataset.tab));
        });
    },

    // ─────────────────────────────────────────────────────────
    // 上传处理
    // ─────────────────────────────────────────────────────────

    handleFiles(files) {
        const pdfFiles = Array.from(files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
        if (pdfFiles.length === 0) {
            Toast.error('请选择 PDF 文件');
            return;
        }

        // 依次上传
        this.uploadQueue(pdfFiles);
    },

    async uploadQueue(files) {
        for (const file of files) {
            await this.uploadFile(file);
        }
    },

    async uploadFile(file) {
        const tempId = Utils.generateId();

        // 添加到队列 UI
        this.addToQueue(tempId, file.name, 'uploading', 10);

        try {
            let fileId = null;

            await API.uploadFileStream(file, (event) => {
                if (event.file_id) fileId = event.file_id;

                if (event.stage) {
                    const progress = Utils.getStageProgress(event.stage);
                    this.updateQueueItem(fileId || tempId, file.name, event.stage, progress);

                    if (event.stage === 'complete') {
                        this.removeFromQueue(fileId || tempId);
                        this.loadFileList();
                        Toast.success(`${file.name} 处理完成`);
                    }
                }

                if (event.error) {
                    this.removeFromQueue(fileId || tempId);
                    Toast.error(`${file.name}: ${event.error}`);
                    this.loadFileList();
                }
            });
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

    updateQueueItem(id, fileName, stage, progress) {
        if (this.state.queue.has(id)) {
            this.state.queue.set(id, { fileName, stage, progress });
        } else {
            // 可能是从 tempId 切换到真实 fileId
            this.state.queue.set(id, { fileName, stage, progress });
        }
        this.renderQueue();
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
            const stageText = item.stage === 'uploading' ? '上传中' : Utils.getStageText(item.stage);
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

            // 找出处理中的文件加入轮询
            this.state.pollingFiles.clear();
            data.items.forEach(item => {
                if (Utils.isProcessing(item.progress)) {
                    this.state.pollingFiles.add(item.file_id);
                }
            });
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
        data.items.forEach((item, index) => {
            const statusClass = Utils.getStatusClass(item.progress);
            const statusText = Utils.getStatusText(item.progress);
            const isFailed = Utils.isFailed(item.progress);
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
                            ${isFailed ? `
                                <button class="action-btn" onclick="App.retryFile('${item.file_id}', '${item.progress}')" title="重试">
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <polyline points="23 4 23 10 17 10"></polyline>
                                        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                                    </svg>
                                </button>
                            ` : ''}
                            <button class="action-btn delete" onclick="App.deleteFile('${item.file_id}')" title="删除">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <polyline points="3 6 5 6 21 6"></polyline>
                                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
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

        // 上一页
        html += `<button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="App.goToPage(${page - 1})">&lt;</button>`;

        // 页码
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

        // 下一页
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
        if (row) {
            row.classList.toggle('selected', checked);
        }
    },

    updateSelectAllState() {
        const rows = this.els.fileListBody.querySelectorAll('tr[data-id]');
        const allChecked = rows.length > 0 && this.state.selectedIds.size === rows.length;
        this.els.selectAll.checked = allChecked;
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
    // 重试
    // ─────────────────────────────────────────────────────────

    async retryFile(fileId, progress) {
        const stage = Utils.getRetryStage(progress);
        if (!stage) return;

        // 添加到队列
        const row = this.els.fileListBody.querySelector(`tr[data-id="${fileId}"]`);
        const fileName = row ? row.querySelector('.file-name-cell').textContent : 'unknown';
        this.addToQueue(fileId, fileName, stage, Utils.getStageProgress(stage));

        try {
            await API.retryFileStream(fileId, stage, (event) => {
                if (event.stage) {
                    const progress = Utils.getStageProgress(event.stage);
                    this.updateQueueItem(fileId, fileName, event.stage, progress);

                    if (event.stage === 'complete') {
                        this.removeFromQueue(fileId);
                        this.loadFileList();
                        Toast.success(`${fileName} 处理完成`);

                        // 如果抽屉正在显示此文件，刷新详情
                        if (this.state.currentFileId === fileId) {
                            this.loadDrawerContent(fileId);
                        }
                    }
                }

                if (event.error) {
                    this.removeFromQueue(fileId);
                    Toast.error(`重试失败: ${event.error}`);
                    this.loadFileList();
                }
            });
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
        this.els.detailDrawer.classList.add('open');
        await this.loadDrawerContent(fileId);
    },

    closeDrawer() {
        this.els.drawerOverlay.classList.remove('active');
        this.els.detailDrawer.classList.remove('open');
        this.state.currentFileId = null;
    },

    async loadDrawerContent(fileId) {
        try {
            const detail = await API.getFileDetail(fileId);

            this.els.drawerFilename.textContent = detail.file_name;
            this.els.drawerFilesize.textContent = Utils.formatFileSize(detail.file_size);

            this.renderTimeline(detail);
            this.renderErrorSection(detail);

            // 加载默认 Tab
            this.switchTab('tables');
        } catch (error) {
            Toast.error('加载详情失败');
        }
    },

    renderTimeline(detail) {
        const stages = [
            { key: 'parsing', label: '解析', start: 'start_parsing_time', end: 'end_parsing_time' },
            { key: 'chunking', label: '分块', start: 'start_chunking_time', end: 'end_chunking_time' },
            { key: 'embedding', label: '向量化', start: 'start_embedding_time', end: 'end_embedding_time' },
            { key: 'extracting', label: '提取', start: null, end: 'end_extracting_time' },
            { key: 'analyzing', label: '分析', start: null, end: 'end_analyzing_time' },
        ];

        const currentStage = detail.progress.replace('_failed', '');
        const isFailed = Utils.isFailed(detail.progress);
        const isComplete = detail.progress === 'complete';

        let html = '';
        let prevEndTime = detail.start_parsing_time;

        stages.forEach((stage, index) => {
            let status = 'pending';
            let duration = null;

            const startTime = stage.start ? detail[stage.start] : prevEndTime;
            const endTime = detail[stage.end];

            if (endTime) {
                status = 'completed';
                duration = Utils.calcDuration(startTime, endTime);
                prevEndTime = endTime;
            } else if (currentStage === stage.key) {
                status = isFailed ? 'failed' : 'processing';
            }

            html += `
                <div class="timeline-item">
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

    async switchTab(tab) {
        // 更新 Tab 状态
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
                case 'tables':
                    data = await API.getFileTables(fileId);
                    if (data.length === 0) {
                        html = '<div class="tab-content-empty">暂无表格数据</div>';
                    } else {
                        data.forEach(item => {
                            html += `
                                <div class="data-card">
                                    <div class="data-card-title">${item.table_name || `表格 ${item.table_index + 1}`}</div>
                                    <div class="data-card-content">${this.escapeHtml(item.table_content)}</div>
                                </div>
                            `;
                        });
                    }
                    break;

                case 'extraction':
                    data = await API.getExtractionResults(fileId);
                    if (data.length === 0) {
                        html = '<div class="tab-content-empty">暂无提取结果</div>';
                    } else {
                        data.forEach(item => {
                            html += `
                                <div class="data-card">
                                    <div class="data-card-title">${item.field_id}</div>
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
                                </div>
                            `;
                        });
                    }
                    break;

                case 'analysis':
                    data = await API.getAnalysisResults(fileId);
                    if (data.length === 0) {
                        html = '<div class="tab-content-empty">暂无分析结果</div>';
                    } else {
                        data.forEach(item => {
                            html += `
                                <div class="data-card">
                                    <div class="data-card-title">${item.rule_id}</div>
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
                                </div>
                            `;
                        });
                    }
                    break;
            }

            this.els.tabContent.innerHTML = html;
        } catch (error) {
            this.els.tabContent.innerHTML = '<div class="tab-content-empty">加载失败</div>';
        }
    },

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    // ─────────────────────────────────────────────────────────
    // 轮询
    // ─────────────────────────────────────────────────────────

    startPolling() {
        setInterval(() => this.pollProcessingFiles(), 3000);
    },

    async pollProcessingFiles() {
        if (this.state.pollingFiles.size === 0) return;

        const fileIds = Array.from(this.state.pollingFiles);

        for (const fileId of fileIds) {
            try {
                const status = await API.getFileStatus(fileId);

                if (!Utils.isProcessing(status.progress)) {
                    this.state.pollingFiles.delete(fileId);
                    this.loadFileList();

                    if (status.progress === 'complete') {
                        Toast.success(`${status.file_name} 处理完成`);
                    }
                }
            } catch (error) {
                // 文件可能已被删除
                this.state.pollingFiles.delete(fileId);
            }
        }
    },
};

// 启动应用
document.addEventListener('DOMContentLoaded', () => App.init());
