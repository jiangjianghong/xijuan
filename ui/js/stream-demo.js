/**
 * 流式处理演示页面逻辑
 */

const StreamDemo = {
    // ── State ──
    selectedFile: null,
    isProcessing: false,
    stageTimes: {},
    parsedContent: '',

    // Stage mapping: SSE event → pipeline stage key
    stageMap: {
        parsing_start: 'parsing',
        parsing: 'parsing',
        content_saved: 'parsing',
        md_content: 'parsing',
        tables_extracted: 'parsing',
        chunking_start: 'chunking',
        chunking: 'chunking',
        chunks_saving: 'chunking',
        chunks_saved: 'chunking',
        embedding_start: 'embedding',
        embedding: 'embedding',
        milvus_submitting: 'embedding',
        milvus_submitted: 'embedding',
        tasks_loading: 'extracting',
        tasks_loaded: 'extracting',
        extraction_start: 'extracting',
        field_extracted: 'extracting',
        extraction: 'extracting',
        analysis_start: 'analyzing',
        rule_analyzed: 'analyzing',
        analysis: 'analyzing',
    },

    // Stages that mark completion of a pipeline stage
    stageCompletionEvents: {
        tables_extracted: 'parsing',
        chunks_saved: 'chunking',
        milvus_submitted: 'embedding',
        extraction: 'extracting',
        analysis: 'analyzing',
    },

    // Stages that mark beginning of a pipeline stage
    stageStartEvents: {
        parsing_start: 'parsing',
        chunking_start: 'chunking',
        embedding_start: 'embedding',
        extraction_start: 'extracting',
        analysis_start: 'analyzing',
    },

    // Ordered stages for progress calculation
    stageOrder: ['parsing', 'chunking', 'embedding', 'extracting', 'analyzing'],

    // ── Init ──
    init() {
        this.bindEvents();
    },

    bindEvents() {
        const uploadArea = document.getElementById('stream-upload-area');
        const fileInput = document.getElementById('stream-file-input');
        const startBtn = document.getElementById('stream-start-btn');
        const removeFile = document.getElementById('stream-remove-file');
        const clearLog = document.getElementById('clear-log');

        // Upload area click
        uploadArea.addEventListener('click', () => {
            if (!this.isProcessing && !this.selectedFile) {
                fileInput.click();
            }
        });

        // File input change
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                this.selectFile(e.target.files[0]);
            }
        });

        // Drag & drop
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            if (!this.isProcessing) {
                uploadArea.classList.add('drag-over');
            }
        });

        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('drag-over');
        });

        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('drag-over');
            if (!this.isProcessing && e.dataTransfer.files.length > 0) {
                const file = e.dataTransfer.files[0];
                if (file.type === 'application/pdf' || file.name.endsWith('.pdf')) {
                    this.selectFile(file);
                } else {
                    this.showToast('请选择 PDF 文件', 'error');
                }
            }
        });

        // Start button
        startBtn.addEventListener('click', () => {
            if (this.selectedFile && !this.isProcessing) {
                this.startProcessing();
            }
        });

        // Remove file
        removeFile.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!this.isProcessing) {
                this.clearFile();
            }
        });

        // Clear log
        clearLog.addEventListener('click', () => {
            this.clearLog();
        });

        // Expand/collapse event delegation for result tables
        document.getElementById('pipeline-stages').addEventListener('click', (e) => {
            const toggle = e.target.closest('.expandable-toggle');
            if (!toggle) return;
            const body = toggle.closest('.stage-expandable').querySelector('.expandable-body');
            const expanded = toggle.classList.toggle('expanded');
            body.style.display = expanded ? 'block' : 'none';
            // Re-initialize lucide icons for the chevron
            if (typeof lucide !== 'undefined') lucide.createIcons();
        });
    },

    // ── File Selection ──
    selectFile(file) {
        this.selectedFile = file;

        const uploadArea = document.getElementById('stream-upload-area');
        const fileInfo = document.getElementById('stream-file-info');
        const fileName = document.getElementById('stream-file-name');
        const fileSize = document.getElementById('stream-file-size');
        const startBtn = document.getElementById('stream-start-btn');

        uploadArea.classList.add('has-file');
        fileInfo.style.display = 'flex';
        fileName.textContent = file.name;
        fileSize.textContent = this.formatFileSize(file.size);
        startBtn.disabled = false;
    },

    clearFile() {
        this.selectedFile = null;

        const uploadArea = document.getElementById('stream-upload-area');
        const fileInfo = document.getElementById('stream-file-info');
        const fileInput = document.getElementById('stream-file-input');
        const startBtn = document.getElementById('stream-start-btn');

        uploadArea.classList.remove('has-file');
        fileInfo.style.display = 'none';
        fileInput.value = '';
        startBtn.disabled = true;
    },

    // ── Processing ──
    startProcessing() {
        if (!this.selectedFile) return;

        this.isProcessing = true;

        const startBtn = document.getElementById('stream-start-btn');
        startBtn.classList.add('processing');
        startBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 style="animation: spin 1s linear infinite;">
                <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
            </svg>
            处理中...
        `;

        // Reset pipeline UI
        this.resetPipeline();

        // Show overall progress
        const overallProgress = document.getElementById('overall-progress');
        overallProgress.style.display = 'block';

        // Clear old status
        document.getElementById('completion-banner').style.display = 'none';
        document.getElementById('error-banner').style.display = 'none';

        // Clear log and add start entry
        this.clearLog();
        this.addLogEntry('info', 'start', `开始上传并处理文件: ${this.selectedFile.name}`);

        // Upload with streaming
        const formData = new FormData();
        formData.append('file', this.selectedFile);

        fetch(`/file/parse?mode=stream`, {
            method: 'POST',
            body: formData,
        }).then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.detail || `上传失败: ${response.status}`);
                }).catch(err => {
                    if (err.message.startsWith('{')) throw new Error(`上传失败: ${response.status}`);
                    throw err;
                });
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            const read = () => {
                reader.read().then(({ done, value }) => {
                    if (done) {
                        this.onStreamEnd();
                        return;
                    }

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const data = JSON.parse(line.slice(6));
                                this.handleEvent(data);
                            } catch (e) {
                                console.warn('SSE parse error:', e);
                            }
                        }
                    }

                    read();
                }).catch(err => {
                    this.onStreamError(err);
                });
            };

            read();
        }).catch(err => {
            this.onStreamError(err);
        });
    },

    handleEvent(data) {
        const stage = data.stage;
        const message = data.message || '';

        // Determine log event class
        let eventClass = 'event-info';
        if (this.stageStartEvents[stage]) {
            eventClass = 'event-start';
        } else if (this.stageCompletionEvents[stage]) {
            eventClass = 'event-done';
        } else if (stage === 'field_extracted' || stage === 'rule_analyzed') {
            eventClass = 'event-progress';
        } else if (stage === 'complete') {
            eventClass = 'event-done';
        } else if (stage === 'error') {
            eventClass = 'event-error';
        }

        // Build log message with extra details
        let logMsg = message;
        if (stage === 'field_extracted' && data.field_name) {
            const status = data.success ? 'OK' : 'FAIL';
            const val = data.extracted_value || '-';
            logMsg = `[${data.current}/${data.total}] ${data.field_name}: ${val} (${status})`;
        } else if (stage === 'rule_analyzed' && data.rule_name) {
            const status = data.success ? 'OK' : 'FAIL';
            const val = data.result_value || '-';
            logMsg = `[${data.current}/${data.total}] ${data.rule_name}: ${val} (${status})`;
        } else if (data.content_length) {
            logMsg += ` (${data.content_length} 字符)`;
        } else if (data.chunk_count) {
            logMsg += ` (${data.chunk_count} 块)`;
        } else if (data.table_count !== undefined) {
            logMsg += ` (${data.table_count} 个表格)`;
        } else if (data.embedding_count) {
            logMsg += ` (${data.embedding_count} 条向量)`;
        }

        this.addLogEntry(eventClass, stage, logMsg);

        // Update pipeline stages
        if (stage === 'error') {
            this.onPipelineError(message);
            return;
        }

        if (stage === 'complete') {
            this.onPipelineComplete();
            return;
        }

        // Mark stage active
        const pipelineStage = this.stageMap[stage];
        if (pipelineStage) {
            if (this.stageStartEvents[stage]) {
                this.setStageActive(pipelineStage);
                this.updateStageDetail(pipelineStage, message);
                this.stageTimes[pipelineStage] = { start: Date.now() };

                // Show expandable section when extraction/analysis starts
                if (stage === 'extraction_start' || stage === 'analysis_start') {
                    const stageEl = document.querySelector(`.pipeline-stage[data-stage="${pipelineStage}"]`);
                    const expandable = stageEl ? stageEl.querySelector('.stage-expandable') : null;
                    if (expandable) expandable.style.display = 'block';
                }
            } else if (this.stageCompletionEvents[stage]) {
                this.setStageCompleted(pipelineStage);
                if (this.stageTimes[pipelineStage]) {
                    this.stageTimes[pipelineStage].end = Date.now();
                    this.showStageDuration(pipelineStage);
                }
            } else {
                this.updateStageDetail(pipelineStage, message);
            }

            // Handle md_content event: show content preview
            if (stage === 'md_content' && data.content) {
                this.showContentPreview(data.content);
            }

            // Handle field_extracted: add row to extraction table
            if (stage === 'field_extracted' && data.field_name) {
                this.addExtractionRow(data);
            }

            // Handle rule_analyzed: add row to analysis table
            if (stage === 'rule_analyzed' && data.rule_name) {
                this.addAnalysisRow(data);
            }

            // Update sub-progress for extraction/analysis
            if (stage === 'field_extracted' && data.current && data.total) {
                this.updateStageSubProgress(pipelineStage, data.current, data.total);
            } else if (stage === 'rule_analyzed' && data.current && data.total) {
                this.updateStageSubProgress(pipelineStage, data.current, data.total);
            }
        }

        // Update overall progress
        this.updateOverallProgress();
    },

    // ── Pipeline UI Updates ──
    resetPipeline() {
        const stages = document.querySelectorAll('.pipeline-stage');
        stages.forEach(el => {
            el.className = 'pipeline-stage waiting';
            el.querySelector('.stage-status').textContent = '';
            el.querySelector('.stage-detail').textContent = '';
            el.querySelector('.stage-progress-fill').style.width = '';

            // Reset duration badge
            const duration = el.querySelector('.stage-duration');
            if (duration) {
                duration.style.display = 'none';
                duration.textContent = '';
            }
        });

        // Reset content preview
        const preview = document.querySelector('.stage-content-preview');
        if (preview) {
            preview.style.display = 'none';
            preview.querySelector('.content-preview-text').textContent = '';
        }

        // Reset expandable sections
        document.querySelectorAll('.stage-expandable').forEach(el => {
            el.style.display = 'none';
            const toggle = el.querySelector('.expandable-toggle');
            if (toggle) toggle.classList.remove('expanded');
            const body = el.querySelector('.expandable-body');
            if (body) body.style.display = 'none';
            const tbody = el.querySelector('.result-table-body');
            if (tbody) tbody.innerHTML = '';
            const count = el.querySelector('.expandable-toggle-count');
            if (count) count.textContent = '';
        });

        this.stageTimes = {};
        this.parsedContent = '';
        this.updateOverallProgress();
    },

    setStageActive(stageKey) {
        // Complete any previous active stage that comes before this one
        const idx = this.stageOrder.indexOf(stageKey);
        for (let i = 0; i < idx; i++) {
            const prev = this.stageOrder[i];
            const prevEl = document.querySelector(`.pipeline-stage[data-stage="${prev}"]`);
            if (prevEl && !prevEl.classList.contains('completed')) {
                prevEl.className = 'pipeline-stage completed';
                prevEl.querySelector('.stage-status').textContent = '完成';
            }
        }

        const el = document.querySelector(`.pipeline-stage[data-stage="${stageKey}"]`);
        if (el && !el.classList.contains('completed')) {
            el.className = 'pipeline-stage active';
            el.querySelector('.stage-status').textContent = '处理中';
        }
    },

    setStageCompleted(stageKey) {
        const el = document.querySelector(`.pipeline-stage[data-stage="${stageKey}"]`);
        if (el) {
            el.className = 'pipeline-stage completed';
            el.querySelector('.stage-status').textContent = '完成';
            el.querySelector('.stage-progress-fill').style.width = '100%';
        }
    },

    setStageFailed(stageKey) {
        const el = document.querySelector(`.pipeline-stage[data-stage="${stageKey}"]`);
        if (el) {
            el.className = 'pipeline-stage failed';
            el.querySelector('.stage-status').textContent = '失败';
        }
    },

    updateStageDetail(stageKey, message) {
        const el = document.querySelector(`.pipeline-stage[data-stage="${stageKey}"]`);
        if (el) {
            el.querySelector('.stage-detail').textContent = message;
        }
    },

    updateStageSubProgress(stageKey, current, total) {
        const el = document.querySelector(`.pipeline-stage[data-stage="${stageKey}"]`);
        if (el) {
            const pct = Math.round((current / total) * 100);
            const fill = el.querySelector('.stage-progress-fill');
            fill.style.width = pct + '%';
            fill.style.animation = 'none';

            el.querySelector('.stage-status').textContent = `${current}/${total}`;
        }
    },

    updateOverallProgress() {
        const completed = document.querySelectorAll('.pipeline-stage.completed').length;
        const active = document.querySelectorAll('.pipeline-stage.active').length;
        const total = this.stageOrder.length;

        // Each completed stage = 1, active stage = 0.5
        const progress = ((completed + active * 0.3) / total) * 100;
        const pct = Math.min(Math.round(progress), 100);

        document.getElementById('overall-fill').style.width = pct + '%';
        document.getElementById('overall-percent').textContent = pct + '%';
    },

    // ── Pipeline Enhancement Methods ──
    showStageDuration(stageKey) {
        const el = document.querySelector(`.pipeline-stage[data-stage="${stageKey}"]`);
        if (!el) return;
        const badge = el.querySelector('.stage-duration');
        if (!badge) return;
        const t = this.stageTimes[stageKey];
        if (!t || !t.start || !t.end) return;
        const seconds = ((t.end - t.start) / 1000).toFixed(1);
        badge.textContent = `耗时 ${seconds}s`;
        badge.style.display = 'inline-block';
    },

    showContentPreview(content) {
        this.parsedContent = content;
        const preview = document.querySelector('.stage-content-preview');
        if (!preview) return;
        const textEl = preview.querySelector('.content-preview-text');
        textEl.textContent = content.substring(0, 500);
        preview.style.display = 'block';
    },

    addExtractionRow(data) {
        const stageEl = document.querySelector('.pipeline-stage[data-stage="extracting"]');
        if (!stageEl) return;
        const tbody = stageEl.querySelector('.result-table-body');
        if (!tbody) return;

        const tr = document.createElement('tr');
        const statusClass = data.success ? 'success' : 'fail';
        const statusText = data.success ? 'OK' : 'FAIL';
        const val = data.extracted_value || '-';
        const reason = data.reason || '-';

        tr.innerHTML = `
            <td title="${this.escapeHtml(data.field_name)}">${this.escapeHtml(data.field_name)}</td>
            <td title="${this.escapeHtml(String(val))}">${this.escapeHtml(String(val))}</td>
            <td title="${this.escapeHtml(String(reason))}">${this.escapeHtml(String(reason))}</td>
            <td><span class="result-status ${statusClass}">${statusText}</span></td>
        `;
        tbody.appendChild(tr);

        // Update count
        const count = stageEl.querySelector('.expandable-toggle-count');
        if (count && data.current && data.total) {
            count.textContent = `${data.current}/${data.total}`;
        }
    },

    addAnalysisRow(data) {
        const stageEl = document.querySelector('.pipeline-stage[data-stage="analyzing"]');
        if (!stageEl) return;
        const tbody = stageEl.querySelector('.result-table-body');
        if (!tbody) return;

        const tr = document.createElement('tr');
        const statusClass = data.success ? 'success' : 'fail';
        const statusText = data.success ? 'OK' : 'FAIL';
        const val = data.result_value || '-';
        const ruleType = data.rule_type === 'judge' ? '判断' : data.rule_type === 'calc' ? '计算' : (data.rule_type || '-');
        const reason = data.reason || '-';

        tr.innerHTML = `
            <td title="${this.escapeHtml(data.rule_name)}">${this.escapeHtml(data.rule_name)}</td>
            <td>${this.escapeHtml(ruleType)}</td>
            <td title="${this.escapeHtml(String(val))}">${this.escapeHtml(String(val))}</td>
            <td title="${this.escapeHtml(String(reason))}">${this.escapeHtml(String(reason))}</td>
            <td><span class="result-status ${statusClass}">${statusText}</span></td>
        `;
        tbody.appendChild(tr);

        // Update count
        const count = stageEl.querySelector('.expandable-toggle-count');
        if (count && data.current && data.total) {
            count.textContent = `${data.current}/${data.total}`;
        }
    },

    // ── Stream Lifecycle ──
    onStreamEnd() {
        // Stream closed normally — if we haven't received "complete", it might be OK
    },

    onStreamError(err) {
        this.addLogEntry('event-error', 'error', `连接错误: ${err.message}`);
        this.onPipelineError(err.message);
    },

    onPipelineComplete() {
        this.isProcessing = false;

        // Complete all stages
        this.stageOrder.forEach(s => this.setStageCompleted(s));

        // Update progress to 100%
        document.getElementById('overall-fill').style.width = '100%';
        document.getElementById('overall-percent').textContent = '100%';

        // Show completion banner
        document.getElementById('completion-banner').style.display = 'flex';

        // Reset button
        const startBtn = document.getElementById('stream-start-btn');
        startBtn.classList.remove('processing');
        startBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
            开始流式处理
        `;
        startBtn.disabled = true;

        // Allow re-upload
        this.clearFile();

        this.showToast('文件处理完成', 'success');
    },

    onPipelineError(message) {
        this.isProcessing = false;

        // Find the currently active stage and mark it failed
        const activeStage = document.querySelector('.pipeline-stage.active');
        if (activeStage) {
            const stageKey = activeStage.dataset.stage;
            this.setStageFailed(stageKey);
        }

        // Show error banner
        document.getElementById('error-text').textContent = message;
        document.getElementById('error-banner').style.display = 'flex';

        // Reset button
        const startBtn = document.getElementById('stream-start-btn');
        startBtn.classList.remove('processing');
        startBtn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
            开始流式处理
        `;
        startBtn.disabled = true;

        this.clearFile();

        this.showToast('处理失败: ' + message, 'error');
    },

    // ── Log ──
    addLogEntry(eventClass, event, message) {
        const container = document.getElementById('log-container');

        // Remove empty placeholder
        const empty = container.querySelector('.log-empty');
        if (empty) empty.remove();

        const entry = document.createElement('div');
        entry.className = 'log-entry';

        const now = new Date();
        const time = [
            String(now.getHours()).padStart(2, '0'),
            String(now.getMinutes()).padStart(2, '0'),
            String(now.getSeconds()).padStart(2, '0'),
        ].join(':') + '.' + String(now.getMilliseconds()).padStart(3, '0');

        entry.innerHTML = `
            <span class="log-time">${time}</span>
            <span class="log-event ${eventClass}">${event}</span>
            <span class="log-message">${this.escapeHtml(message)}</span>
        `;

        container.appendChild(entry);
        container.scrollTop = container.scrollHeight;
    },

    clearLog() {
        const container = document.getElementById('log-container');
        container.innerHTML = '<div class="log-empty">等待处理开始...</div>';
    },

    // ── Toast ──
    showToast(message, type = 'info') {
        // Use main page Toast module if available
        if (typeof Toast !== 'undefined' && Toast[type]) {
            Toast[type](message);
            return;
        }
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('removing');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    },

    // ── Utilities ──
    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    },

    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
};

// ── Boot ──
document.addEventListener('DOMContentLoaded', () => {
    StreamDemo.init();
});
