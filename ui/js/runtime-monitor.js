/**
 * 并发运行台：当前 worker 的只读运行时快照。
 * 轮询故意独立于文件队列，离开页面即停止，不提供后端控制操作。
 */
(function (root) {
    'use strict';

    const POLL_MS = 2000;
    const MAX_HISTORY = 60;
    const STATUS_LABELS = { idle: '空闲', normal: '正常', pressure: '有压力', saturated: '已饱和', offline: '未接入' };
    const STATUS_CLASS = { idle: 'is-idle', normal: 'is-normal', pressure: 'is-pressure', saturated: 'is-saturated', offline: 'is-offline' };

    function asNumber(value, fallback = 0) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function normalizeSnapshot(snapshot) {
        const data = snapshot && typeof snapshot === 'object' ? (snapshot.data || snapshot) : {};
        const pools = Array.isArray(data.pools) ? data.pools : [];
        const globalPools = pools.filter(pool => pool && pool.scope === 'global' && pool.id !== 'global_pipeline')
            .map(pool => ({ ...pool, limit: asNumber(pool.limit, 1), active: asNumber(pool.active), queued: asNumber(pool.queued), completed: asNumber(pool.completed), wait_p95_ms: asNumber(pool.wait_p95_ms) }));
        const taskPools = pools.filter(pool => pool && pool.scope === 'task')
            .map(pool => ({ ...pool, capacity: asNumber(pool.per_instance_limit || pool.limit, 1), active: asNumber(pool.busiest_active), queued: asNumber(pool.aggregate_queued), aggregate_active: asNumber(pool.aggregate_active), instance_count: asNumber(pool.instance_count), instances: Array.isArray(pool.instances) ? pool.instances : [] }));
        const pipeline = pools.find(pool => pool && pool.id === 'global_pipeline') || { id: 'global_pipeline', label: '文件管线', status: 'offline', connected: false, limit: 0, active: 0, queued: 0 };
        const summary = data.summary && typeof data.summary === 'object' ? data.summary : {};
        return {
            updated_at: data.updated_at || '',
            scope: data.scope || 'single-process',
            summary: {
                active: asNumber(summary.active, globalPools.reduce((sum, pool) => sum + pool.active, 0)),
                capacity: asNumber(summary.capacity, globalPools.reduce((sum, pool) => sum + pool.limit, 0)),
                queued: asNumber(summary.queued, globalPools.reduce((sum, pool) => sum + pool.queued, 0)),
                hot_pools: asNumber(summary.hot_pools, globalPools.filter(pool => ['pressure', 'saturated'].includes(pool.status)).length),
                wait_p95_ms: asNumber(summary.wait_p95_ms),
            },
            globalPools,
            taskPools,
            pipeline: { ...pipeline, status: pipeline.status || 'offline', connected: pipeline.connected !== false },
            events: Array.isArray(data.events) ? data.events : [],
            error: false,
        };
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
    }

    function formatTime(timestamp) {
        if (!timestamp) return '--';
        const date = new Date(typeof timestamp === 'number' ? timestamp * 1000 : timestamp);
        return Number.isNaN(date.getTime()) ? '--' : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function formatEvent(event) {
        event = event && typeof event === 'object' ? event : {};
        const context = event && event.context ? event.context : {};
        const subject = context.file_name || context.file_id || context.field_id || context.rule_id || context.task_id || '';
        const suffix = context.model ? ` · ${context.model}` : '';
        return `${event.type || 'event'}${subject ? ` · ${subject}` : ''}${suffix}`;
    }

    const RuntimeMonitor = {
        state: { active: false, loading: false, timer: null, last: null, history: [], charts: {}, selectedPool: null },
        normalizeSnapshot,

        activate() {
            if (this.state.active) return;
            this.state.active = true;
            this.state.resizeHandler = () => Object.values(this.state.charts).forEach(chart => chart && chart.resize && chart.resize());
            window.addEventListener('resize', this.state.resizeHandler);
            this.state.visibilityHandler = () => {
                if (document.hidden) {
                    if (this.state.timer) clearInterval(this.state.timer);
                    this.state.timer = null;
                } else if (this.state.active && !this.state.timer) {
                    this.refresh();
                    this.state.timer = setInterval(() => this.refresh(), POLL_MS);
                }
            };
            document.addEventListener('visibilitychange', this.state.visibilityHandler);
            this.refresh();
            if (!document.hidden) this.state.timer = setInterval(() => this.refresh(), POLL_MS);
        },

        deactivate() {
            this.state.active = false;
            if (this.state.timer) clearInterval(this.state.timer);
            this.state.timer = null;
            if (this.state.resizeHandler) window.removeEventListener('resize', this.state.resizeHandler);
            this.state.resizeHandler = null;
            if (this.state.visibilityHandler) document.removeEventListener('visibilitychange', this.state.visibilityHandler);
            this.state.visibilityHandler = null;
            Object.values(this.state.charts).forEach(chart => chart && chart.dispose && chart.dispose());
            this.state.charts = {};
            this.closePool();
        },

        async refresh() {
            if (!this.state.active || this.state.loading || typeof API === 'undefined') return;
            this.state.loading = true;
            try {
                const snapshot = normalizeSnapshot(await API.getRuntimeConcurrency());
                this.state.last = snapshot;
                this.state.history.push({ at: snapshot.updated_at || new Date().toISOString(), active: snapshot.summary.active, capacity: snapshot.summary.capacity, queued: snapshot.summary.queued });
                if (this.state.history.length > MAX_HISTORY) this.state.history.shift();
                this.render(snapshot);
            } catch (error) {
                // 保留最后一次成功快照，只更新连接状态，避免瞬断清空监控内容。
                const snapshot = this.state.last || normalizeSnapshot(null);
                this.render({ ...snapshot, error: true, error_message: error && error.message ? error.message : '请求失败' });
            } finally {
                this.state.loading = false;
            }
        },

        render(snapshot) {
            const page = document.getElementById('page-runtime-monitor');
            if (!page) return;
            this.renderSummary(snapshot);
            this.renderGlobalPools(snapshot);
            this.renderTaskPools(snapshot);
            this.renderEvents(snapshot.events);
            this.renderCharts(snapshot);
            const updated = document.getElementById('runtime-monitor-updated');
            if (updated) updated.textContent = snapshot.error
                ? `连接异常 · 最后成功 ${formatTime(snapshot.updated_at)}`
                : `更新于 ${formatTime(snapshot.updated_at)}`;
            page.classList.toggle('has-error', Boolean(snapshot.error));
        },

        renderSummary(snapshot) {
            const summary = snapshot.summary;
            const values = {
                'runtime-summary-active': `${summary.active} / ${summary.capacity}`,
                'runtime-summary-queued': summary.queued,
                'runtime-summary-hot': summary.hot_pools,
                'runtime-summary-wait': `${Math.round(summary.wait_p95_ms)} ms`,
            };
            Object.entries(values).forEach(([id, value]) => { const el = document.getElementById(id); if (el) el.textContent = value; });
        },

        renderGlobalPools(snapshot) {
            const container = document.getElementById('runtime-global-pools');
            if (!container) return;
            const pools = [...snapshot.globalPools, snapshot.pipeline];
            container.innerHTML = pools.map(pool => {
                const limit = asNumber(pool.limit, 0);
                const active = asNumber(pool.active);
                const ratio = limit ? Math.min(100, Math.round(active / limit * 100)) : 0;
                const status = pool.status || 'idle';
                return `<button class="runtime-pool-row ${STATUS_CLASS[status] || ''}" type="button" data-pool-id="${escapeHtml(pool.id)}" title="查看 ${escapeHtml(pool.label || pool.id)} 详情">
                    <span class="runtime-pool-name"><strong>${escapeHtml(pool.label || pool.id)}</strong><small>${escapeHtml(pool.group || '')}</small></span>
                    <span class="runtime-pool-meter"><span style="width:${ratio}%"></span></span>
                    <span class="runtime-pool-count">${active} / ${limit || '—'}</span>
                    <span class="runtime-status">${STATUS_LABELS[status] || status}</span>
                </button>`;
            }).join('');
            container.querySelectorAll('[data-pool-id]').forEach(button => button.addEventListener('click', () => this.openPool(button.dataset.poolId, snapshot)));
        },

        renderTaskPools(snapshot) {
            const container = document.getElementById('runtime-task-pools');
            if (!container) return;
            container.innerHTML = snapshot.taskPools.map(pool => `<button class="runtime-task-row ${STATUS_CLASS[pool.status] || ''}" type="button" data-pool-id="${escapeHtml(pool.id)}">
                <span><strong>${escapeHtml(pool.label || pool.id)}</strong><small>${pool.instance_count} 个活动实例</small></span>
                <span class="runtime-task-pressure"><b>${pool.active} / ${pool.capacity}</b><small>最忙实例 · 队列 ${pool.queued}</small></span>
                <span class="runtime-status">${STATUS_LABELS[pool.status] || pool.status}</span>
            </button>`).join('') || '<div class="runtime-monitor-empty">暂无局部任务实例</div>';
            container.querySelectorAll('[data-pool-id]').forEach(button => button.addEventListener('click', () => this.openPool(button.dataset.poolId, snapshot)));
        },

        renderEvents(events) {
            const container = document.getElementById('runtime-events');
            if (!container) return;
            container.innerHTML = events.length ? events.slice(0, 20).map(rawEvent => {
                const event = rawEvent && typeof rawEvent === 'object' ? rawEvent : {};
                return `<div class="runtime-event-row"><time>${formatTime(event.at)}</time><span class="runtime-event-type">${escapeHtml(event.pool_id || '')}</span><span>${escapeHtml(formatEvent(event))}</span>${event.wait_ms != null ? `<small>${Math.round(asNumber(event.wait_ms))} ms</small>` : ''}</div>`;
            }).join('') : '<div class="runtime-monitor-empty">暂无事件</div>';
        },

        renderCharts(snapshot) {
            if (typeof echarts === 'undefined') return;
            const capacityEl = document.getElementById('runtime-capacity-chart');
            const trendEl = document.getElementById('runtime-pressure-chart');
            if (capacityEl) {
                const chart = this.state.charts.capacity || (this.state.charts.capacity = echarts.init(capacityEl));
                const pools = snapshot.globalPools;
                chart.setOption({ animation: false, grid: { left: 110, right: 20, top: 10, bottom: 24 }, xAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#e3ece4' } } }, yAxis: { type: 'category', data: pools.map(pool => pool.label), axisLabel: { color: '#41634c' } }, series: [{ type: 'bar', data: pools.map(pool => ({ value: pool.limit ? Math.round(pool.active / pool.limit * 100) : 0, itemStyle: { color: pool.status === 'saturated' ? '#b65c4b' : pool.status === 'pressure' ? '#c58a42' : '#5d8b68' } })), barMaxWidth: 16, showBackground: true, backgroundStyle: { color: '#edf3ed' }, label: { show: true, position: 'right', formatter: '{c}%' } }] });
            }
            if (trendEl) {
                const chart = this.state.charts.trend || (this.state.charts.trend = echarts.init(trendEl));
                const history = this.state.history;
                chart.setOption({ animation: false, grid: { left: 38, right: 16, top: 18, bottom: 26 }, tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: history.map(item => formatTime(item.at)), boundaryGap: false, axisLabel: { color: '#6b8371', hideOverlap: true } }, yAxis: { type: 'value', min: 0, max: value => Math.max(value.max, 1), splitLine: { lineStyle: { color: '#e3ece4' } } }, series: [{ name: '活动', type: 'line', smooth: true, symbol: 'none', data: history.map(item => item.active), lineStyle: { color: '#4f775d', width: 2 }, areaStyle: { color: 'rgba(79,119,93,.12)' } }, { name: '排队', type: 'line', smooth: true, symbol: 'none', data: history.map(item => item.queued), lineStyle: { color: '#c58a42', width: 2 } }] });
            }
        },

        openPool(poolId, snapshot) {
            const pool = [...snapshot.globalPools, ...snapshot.taskPools, snapshot.pipeline].find(item => item.id === poolId);
            if (!pool) return;
            this.state.selectedPool = pool;
            const drawer = document.getElementById('runtime-pool-drawer');
            const body = document.getElementById('runtime-pool-drawer-body');
            if (!drawer || !body) return;
            const title = document.getElementById('runtime-pool-drawer-title');
            if (title) title.textContent = pool.label || pool.id;
            const isTask = pool.scope === 'task';
            body.innerHTML = `<div class="runtime-drawer-kv"><span>状态</span><strong class="${STATUS_CLASS[pool.status] || ''}">${STATUS_LABELS[pool.status] || pool.status}</strong></div>
                <div class="runtime-drawer-kv"><span>${isTask ? '最忙实例' : '活动 / 容量'}</span><strong>${pool.active} / ${isTask ? pool.capacity : pool.limit || '—'}</strong></div>
                <div class="runtime-drawer-kv"><span>排队</span><strong>${pool.queued}</strong></div>
                ${isTask ? `<div class="runtime-drawer-kv"><span>实例数</span><strong>${pool.instance_count}</strong></div><div class="runtime-drawer-subtitle">实例明细</div>${(pool.instances || []).map(instance => `<div class="runtime-instance-row"><span>${escapeHtml(instance.instance_id || instance.id || '实例')}</span><span>${instance.active || 0} / ${pool.capacity}</span></div>`).join('') || '<div class="runtime-monitor-empty">暂无实例</div>'}` : `<div class="runtime-drawer-kv"><span>P95 等待</span><strong>${Math.round(pool.wait_p95_ms || 0)} ms</strong></div><div class="runtime-drawer-subtitle">当前占用</div>${(pool.tasks || []).map(task => `<div class="runtime-instance-row"><span>${escapeHtml(task.file_name || task.file_id || task.field_id || task.rule_id || task.task_id || '任务')}</span><span>${escapeHtml(task.stage || task.model || '')}</span></div>`).join('') || '<div class="runtime-monitor-empty">暂无活动占用</div>'}<div class="runtime-drawer-subtitle">约束关系</div><p class="runtime-drawer-note">${escapeHtml((pool.constraints || []).join('、') || '无')}</p>`}`;
            drawer.classList.add('open');
            drawer.setAttribute('aria-hidden', 'false');
        },

        closePool() {
            const drawer = document.getElementById('runtime-pool-drawer');
            if (drawer) { drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true'); }
        },
    };

    root.RuntimeMonitor = RuntimeMonitor;
    if (typeof module !== 'undefined' && module.exports) module.exports = RuntimeMonitor;
})(typeof window !== 'undefined' ? window : globalThis);
