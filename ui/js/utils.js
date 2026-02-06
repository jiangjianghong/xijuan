/**
 * 工具函数模块
 */

const Utils = {
    /**
     * 格式化文件大小
     */
    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },

    /**
     * 格式化日期时间
     */
    formatDateTime(dateStr) {
        if (!dateStr) return '--';
        const date = new Date(dateStr);
        const pad = n => String(n).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    },

    /**
     * 格式化持续时间 (秒)
     */
    formatDuration(seconds) {
        if (seconds === null || seconds === undefined) return '--:--';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    },

    /**
     * 计算两个时间之间的持续时间 (秒)
     */
    calcDuration(startTime, endTime) {
        if (!startTime || !endTime) return null;
        const start = new Date(startTime);
        const end = new Date(endTime);
        return (end - start) / 1000;
    },

    /**
     * 获取状态显示文本
     */
    getStatusText(progress) {
        const map = {
            parsing: '解析中',
            chunking: '分块中',
            embedding: '向量化中',
            extracting: '提取中',
            analyzing: '分析中',
            complete: '已完成',
            parsing_failed: '解析失败',
            chunking_failed: '分块失败',
            embedding_failed: '向量化失败',
            extracting_failed: '提取失败',
            analyzing_failed: '分析失败',
        };
        return map[progress] || progress;
    },

    /**
     * 获取状态 CSS 类名
     */
    getStatusClass(progress) {
        if (progress === 'complete') return 'status-complete';
        if (progress.endsWith('_failed')) return 'status-failed';
        return 'status-processing';
    },

    /**
     * 获取阶段进度百分比
     */
    getStageProgress(stage) {
        const map = {
            parsing: 20,
            chunking: 40,
            embedding: 60,
            extracting: 80,
            analyzing: 90,
            complete: 100,
        };
        // 处理失败状态
        const baseStage = stage.replace('_failed', '');
        return map[baseStage] || 0;
    },

    /**
     * 获取阶段显示文本
     */
    getStageText(stage) {
        const map = {
            parsing: '解析',
            chunking: '分块',
            embedding: '向量化',
            extracting: '提取',
            analyzing: '分析',
        };
        return map[stage] || stage;
    },

    /**
     * 判断是否为处理中状态
     */
    isProcessing(progress) {
        return ['parsing', 'chunking', 'embedding', 'extracting', 'analyzing'].includes(progress);
    },

    /**
     * 判断是否为失败状态
     */
    isFailed(progress) {
        return progress && progress.endsWith('_failed');
    },

    /**
     * 获取失败状态对应的重试阶段
     */
    getRetryStage(progress) {
        const map = {
            parsing_failed: 'parsing',
            chunking_failed: 'chunking',
            embedding_failed: 'embedding',
            extracting_failed: 'extracting',
            analyzing_failed: 'analyzing',
        };
        return map[progress];
    },

    /**
     * 防抖函数
     */
    debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    },

    /**
     * 生成唯一 ID
     */
    generateId() {
        return Date.now().toString(36) + Math.random().toString(36).substr(2);
    },

    /**
     * HTML 转义
     */
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
};

/**
 * Toast 通知管理
 */
const Toast = {
    container: null,

    init() {
        this.container = document.getElementById('toast-container');
    },

    show(message, type = 'info', duration = 3000) {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        this.container.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('removing');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },

    success(message) {
        this.show(message, 'success');
    },

    error(message) {
        this.show(message, 'error', 5000);
    },

    info(message) {
        this.show(message, 'info');
    },
};
