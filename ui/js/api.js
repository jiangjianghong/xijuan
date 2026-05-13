/**
 * API 封装模块
 */

const API = {
    baseUrl: '',

    /**
     * 当前选中的文档类型 ID（持久化在 localStorage）
     */
    getCurrentTypeId() {
        return localStorage.getItem('currentTypeId') || 'default';
    },

    setCurrentTypeId(typeId) {
        localStorage.setItem('currentTypeId', typeId || 'default');
    },

    /**
     * 通用请求方法
     */
    async request(url, options = {}) {
        const response = await fetch(this.baseUrl + url, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
            ...options,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `请求失败: ${response.status}`);
        }

        return response.json();
    },

    /**
     * 上传文件 (流式)
     */
    uploadFileStream(file, onEvent, typeId) {
        return new Promise((resolve, reject) => {
            const formData = new FormData();
            formData.append('file', file);

            const tid = encodeURIComponent(typeId || this.getCurrentTypeId());
            fetch(`${this.baseUrl}/file/parse?mode=stream&type_id=${tid}`, {
                method: 'POST',
                body: formData,
            }).then(response => {
                if (!response.ok) {
                    response.json().then(data => {
                        reject(new Error(data.detail || `上传失败: ${response.status}`));
                    }).catch(() => {
                        reject(new Error(`上传失败: ${response.status}`));
                    });
                    return;
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                function read() {
                    reader.read().then(({ done, value }) => {
                        if (done) {
                            resolve();
                            return;
                        }

                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop() || '';

                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                try {
                                    const data = JSON.parse(line.slice(6));
                                    onEvent(data);
                                } catch (e) {
                                    console.warn('SSE parse error:', e);
                                }
                            }
                        }

                        read();
                    }).catch(reject);
                }

                read();
            }).catch(reject);
        });
    },

    /**
     * 上传文件 (异步)
     */
    async uploadFileAsync(file, typeId) {
        const formData = new FormData();
        formData.append('file', file);

        const tid = encodeURIComponent(typeId || this.getCurrentTypeId());
        const response = await fetch(`${this.baseUrl}/file/parse?mode=async&type_id=${tid}`, {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `上传失败: ${response.status}`);
        }

        return response.json();
    },

    /**
     * 获取文件列表
     */
    async getFileList(page = 1, pageSize = 20, status = '', typeId) {
        const params = new URLSearchParams({ page, page_size: pageSize });
        if (status) params.append('status', status);
        const tid = typeId !== undefined ? typeId : this.getCurrentTypeId();
        if (tid) params.append('type_id', tid);
        const result = await this.request(`/file/list?${params}`);
        return result.data;
    },

    /**
     * 获取文件状态
     */
    async getFileStatus(fileId) {
        const result = await this.request(`/file/${fileId}/status`);
        return result.data;
    },

    /**
     * 获取文件详情
     */
    async getFileDetail(fileId) {
        const result = await this.request(`/file/${fileId}/detail`);
        return result.data;
    },

    /**
     * 获取文件表格
     */
    async getFileTables(fileId) {
        const result = await this.request(`/file/${fileId}/tables`);
        return result.data;
    },

    /**
     * 获取提取结果
     */
    async getExtractionResults(fileId) {
        const result = await this.request(`/file/${fileId}/extraction`);
        return result.data;
    },

    /**
     * 获取分析结果
     */
    async getAnalysisResults(fileId) {
        const result = await this.request(`/file/${fileId}/analysis`);
        return result.data;
    },

    /**
     * 获取文件大纲(章节列表,含正文切片)
     */
    async getFileOutline(fileId) {
        const result = await this.request(`/file/${fileId}/outline`);
        return result.data;
    },

    /**
     * 删除单个文件
     */
    async deleteFile(fileId) {
        return this.request(`/file/${fileId}`, { method: 'DELETE' });
    },

    /**
     * 批量删除文件
     */
    async batchDeleteFiles(fileIds) {
        const result = await this.request('/file/batch', {
            method: 'DELETE',
            body: JSON.stringify({ file_ids: fileIds }),
        });
        return result.data;
    },

    /**
     * 重试处理 (流式)
     */
    retryFileStream(fileId, stage, onEvent) {
        return new Promise((resolve, reject) => {
            fetch(`${this.baseUrl}/file/${fileId}/retry/${stage}?mode=stream`, {
                method: 'POST',
            }).then(response => {
                if (!response.ok) {
                    response.json().then(data => {
                        reject(new Error(data.detail || `重试失败: ${response.status}`));
                    }).catch(() => {
                        reject(new Error(`重试失败: ${response.status}`));
                    });
                    return;
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                function read() {
                    reader.read().then(({ done, value }) => {
                        if (done) {
                            resolve();
                            return;
                        }

                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop() || '';

                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                try {
                                    const data = JSON.parse(line.slice(6));
                                    onEvent(data);
                                } catch (e) {
                                    console.warn('SSE parse error:', e);
                                }
                            }
                        }

                        read();
                    }).catch(reject);
                }

                read();
            }).catch(reject);
        });
    },

    /**
     * 重试处理 (异步)
     */
    async retryFileAsync(fileId, stage) {
        return this.request(`/file/${fileId}/retry/${stage}?mode=async`, { method: 'POST' });
    },

    // ─── 字段提取配置 ───

    /**
     * 获取字段提取配置列表
     */
    async getExtractionFields(typeId) {
        const tid = typeId !== undefined ? typeId : this.getCurrentTypeId();
        const params = tid ? `?type_id=${encodeURIComponent(tid)}` : '';
        const result = await this.request(`/extraction/fields${params}`);
        return result.data;
    },

    /**
     * 新增/更新字段提取配置（自动注入当前 type_id）
     */
    async saveExtractionField(data) {
        const payload = { type_id: this.getCurrentTypeId(), ...data };
        return this.request('/extraction/fields', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
    },

    /**
     * 删除字段提取配置
     */
    async deleteExtractionField(id) {
        return this.request(`/extraction/fields/${id}`, { method: 'DELETE' });
    },

    /**
     * 检查 field_id 是否存在
     */
    async checkExtractionField(id) {
        const result = await this.request(`/extraction/fields/${id}/check`);
        return result.data;
    },

    // ─── 逻辑分析配置 ───

    /**
     * 获取逻辑分析配置列表
     */
    async getAnalysisRules(typeId) {
        const tid = typeId !== undefined ? typeId : this.getCurrentTypeId();
        const params = tid ? `?type_id=${encodeURIComponent(tid)}` : '';
        const result = await this.request(`/analysis/rules${params}`);
        return result.data;
    },

    /**
     * 新增/更新逻辑分析配置（自动注入当前 type_id）
     */
    async saveAnalysisRule(data) {
        const payload = { type_id: this.getCurrentTypeId(), ...data };
        return this.request('/analysis/rules', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
    },

    /**
     * 删除逻辑分析配置
     */
    async deleteAnalysisRule(id) {
        return this.request(`/analysis/rules/${id}`, { method: 'DELETE' });
    },

    /**
     * 检查 rule_id 是否存在
     */
    async checkAnalysisRule(id) {
        const result = await this.request(`/analysis/rules/${id}/check`);
        return result.data;
    },

    // ─── 调试相关 ───

    /**
     * 获取已完成文件列表（用于调试面板文件选择器）
     */
    async getCompletedFiles() {
        const tid = this.getCurrentTypeId();
        const tidParam = tid ? `&type_id=${encodeURIComponent(tid)}` : '';
        const result = await this.request(`/file/list?page=1&page_size=100&status=complete${tidParam}`);
        return result.data;
    },

    // ─── 文档类型 ───

    /**
     * 获取文档类型列表
     */
    async getDocTypes() {
        const result = await this.request('/doctype/list');
        return result.data;
    },

    /**
     * 新增/更新文档类型
     */
    async saveDocType(data) {
        return this.request('/doctype', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    },

    /**
     * 删除文档类型
     */
    async deleteDocType(typeId, force = false) {
        const url = `/doctype/${encodeURIComponent(typeId)}${force ? '?force=true' : ''}`;
        return this.request(url, { method: 'DELETE' });
    },

    /**
     * 从源类型复制字段/规则到目标类型
     */
    async copyConfigs(targetTypeId, payload) {
        const result = await this.request(`/doctype/${encodeURIComponent(targetTypeId)}/copy_from`, {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        return result.data;
    },

    /**
     * 导出某类型的字段+规则为 JSON 载荷
     */
    async exportDocType(typeId) {
        const result = await this.request(`/doctype/${encodeURIComponent(typeId)}/export`);
        return result.data;
    },

    /**
     * 从 JSON 载荷导入字段+规则
     */
    async importDocType(payload, options = {}) {
        const body = {
            payload,
            target_type_id: options.targetTypeId || null,
            create_type_if_missing: options.createTypeIfMissing !== false,
            on_conflict: options.onConflict || 'rename',
        };
        const result = await this.request('/doctype/import', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        return result.data;
    },

    /**
     * 流式测试字段提取（SSE）
     */
    testFieldStream(payload, onEvent) {
        return new Promise((resolve, reject) => {
            fetch(`${this.baseUrl}/extraction/test/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            }).then(response => {
                if (!response.ok) {
                    response.json().then(data => {
                        reject(new Error(data.detail || `测试失败: ${response.status}`));
                    }).catch(() => {
                        reject(new Error(`测试失败: ${response.status}`));
                    });
                    return;
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let currentEvent = null;

                function read() {
                    reader.read().then(({ done, value }) => {
                        if (done) {
                            resolve();
                            return;
                        }

                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop() || '';

                        for (const line of lines) {
                            if (line.startsWith('event: ')) {
                                currentEvent = line.slice(7).trim();
                            } else if (line.startsWith('data: ')) {
                                try {
                                    const data = JSON.parse(line.slice(6));
                                    onEvent({ event: currentEvent || 'message', data });
                                } catch (e) {
                                    console.warn('SSE parse error:', e);
                                }
                                currentEvent = null;
                            }
                        }

                        read();
                    }).catch(reject);
                }

                read();
            }).catch(reject);
        });
    },

    /**
     * 流式测试规则分析（SSE）
     */
    testRuleStream(payload, onEvent) {
        return new Promise((resolve, reject) => {
            fetch(`${this.baseUrl}/analysis/test/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            }).then(response => {
                if (!response.ok) {
                    response.json().then(data => {
                        reject(new Error(data.detail || `测试失败: ${response.status}`));
                    }).catch(() => {
                        reject(new Error(`测试失败: ${response.status}`));
                    });
                    return;
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let currentEvent = null;

                function read() {
                    reader.read().then(({ done, value }) => {
                        if (done) {
                            resolve();
                            return;
                        }

                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop() || '';

                        for (const line of lines) {
                            if (line.startsWith('event: ')) {
                                currentEvent = line.slice(7).trim();
                            } else if (line.startsWith('data: ')) {
                                try {
                                    const data = JSON.parse(line.slice(6));
                                    onEvent({ event: currentEvent || 'message', data });
                                } catch (e) {
                                    console.warn('SSE parse error:', e);
                                }
                                currentEvent = null;
                            }
                        }

                        read();
                    }).catch(reject);
                }

                read();
            }).catch(reject);
        });
    },
};
