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
        this.startPolling();
        if (typeof lucide !== 'undefined') lucide.createIcons();
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
            this.switchTab('tables');
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
            { key: 'extracting', label: '提取', start: null, end: 'end_extracting_time' },
            { key: 'analyzing', label: '分析', start: null, end: 'end_analyzing_time' },
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
            const startTime = stage.start ? detail[stage.start] : prevEndTime;
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
        } catch (error) {
            this.els.tabContent.innerHTML = '<div class="tab-content-empty">加载失败</div>';
        }
    },

    escapeHtml(text) {
        return Utils.escapeHtml(text);
    },

    // ─────────────────────────────────────────────────────────
    // 页面切换
    // ─────────────────────────────────────────────────────────

    switchPage(page) {
        // 切换导航按钮
        document.querySelectorAll('.nav-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.page === page);
        });

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
