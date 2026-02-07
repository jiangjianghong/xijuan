/**
 * API 封装模块
 */

const API = {
    baseUrl: '',

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
    uploadFileStream(file, onEvent) {
        return new Promise((resolve, reject) => {
            const formData = new FormData();
            formData.append('file', file);

            fetch(`${this.baseUrl}/file/parse?mode=stream`, {
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
    async uploadFileAsync(file) {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(`${this.baseUrl}/file/parse?mode=async`, {
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
    async getFileList(page = 1, pageSize = 20, status = '') {
        const params = new URLSearchParams({ page, page_size: pageSize });
        if (status) params.append('status', status);
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
    async getExtractionFields() {
        const result = await this.request('/extraction/fields');
        return result.data;
    },

    /**
     * 新增/更新字段提取配置
     */
    async saveExtractionField(data) {
        return this.request('/extraction/fields', {
            method: 'POST',
            body: JSON.stringify(data),
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
    async getAnalysisRules() {
        const result = await this.request('/analysis/rules');
        return result.data;
    },

    /**
     * 新增/更新逻辑分析配置
     */
    async saveAnalysisRule(data) {
        return this.request('/analysis/rules', {
            method: 'POST',
            body: JSON.stringify(data),
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
        const result = await this.request('/file/list?page=1&page_size=100&status=complete');
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

                function read() {
                    reader.read().then(({ done, value }) => {
                        if (done) {
                            resolve();
                            return;
                        }

                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop() || '';

                        let currentEvent = null;
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
