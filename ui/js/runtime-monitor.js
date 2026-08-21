/** 当前 worker 并发运行台。视觉结构来自 design-mockups/concurrency-pools.html。 */
(function (root) {
    'use strict';

    const POLL_MS = 2000;
    const DEFAULT_WINDOW = '60s';
    const WINDOW_LABELS = { '60s': '最近 60 秒', '5m': '最近 5 分钟', '30m': '最近 30 分钟' };
    const STATUS = {
        idle: { label: '空闲', color: '#8DAA91', text: '#668075' },
        normal: { label: '运行正常', color: '#4F775D', text: '#2C4C3B' },
        pressure: { label: '高负载', color: '#d89122', text: '#a86610' },
        saturated: { label: '饱和排队', color: '#df5a5a', text: '#b94444' },
        offline: { label: '未接入', color: '#9aa7a0', text: '#718078' },
    };
    const POOL_META = {
        global_llm: { short: 'LLM', note: '限制当前进程内所有普通文本模型请求，表名校验、字段抽取和逻辑分析共享该额度。' },
        global_embedding: { short: 'EMB', note: '限制向量化阶段和 vector_db 查询共同使用的 Embedding 请求。' },
        global_vl: { short: 'VL', note: '限制所有 VL 请求，局部视觉模型并发最终受该池约束。' },
        global_table_validation: { short: 'TABLE', note: '限制跨文件并行执行的表格名称校验任务。' },
        global_extraction: { short: 'EXTRACT', note: '限制跨文件同时运行的字段抽取任务。' },
        global_analysis: { short: 'ANALYZE', note: '由文件管线分析和独立分析接口共享。' },
        task_table_validation: { short: 'T-TABLE', note: '展示当前最繁忙文件内部的表名校验并发。' },
        task_extraction: { short: 'T-EXTRACT', note: '展示当前最繁忙文件的字段抽取并发，详情中同时给出全部实例累计值。' },
        task_file_analysis: { short: 'T-ANALYZE', note: '展示当前最繁忙文件内部的逻辑分析规则并发。' },
        independent_analysis: { short: 'INDEPENDENT', note: '展示当前 worker 内所有独立分析请求共享的 item 并发。' },
        global_pipeline: { short: 'PIPELINE', note: '同时处理的文件数上限。上传与重试的六个入口全程持有令牌，超限文件落 queued 排队。' },
    };
    const POOL_ORDER = [
        'global_llm', 'global_embedding', 'global_vl',
        'global_table_validation', 'global_extraction', 'global_analysis',
        'task_table_validation', 'task_extraction', 'task_file_analysis',
        'independent_analysis', 'global_pipeline',
    ];
    const reduceMotion = typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function asNumber(value, fallback = 0) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function normalizeSnapshot(snapshot) {
        const data = snapshot && typeof snapshot === 'object' ? (snapshot.data || snapshot) : {};
        const pools = Array.isArray(data.pools) ? data.pools : [];
        const globalPools = pools.filter(pool => pool && pool.scope === 'global' && pool.id !== 'global_pipeline')
            .map(pool => ({ ...pool, limit: asNumber(pool.limit, 1), active: asNumber(pool.active), queued: asNumber(pool.queued), completed: asNumber(pool.completed), wait_p95_ms: asNumber(pool.wait_p95_ms), tasks: Array.isArray(pool.tasks) ? pool.tasks : [] }));
        const taskPools = pools.filter(pool => pool && pool.scope === 'task')
            .map(pool => ({ ...pool, capacity: asNumber(pool.per_instance_limit || pool.limit, 1), active: asNumber(pool.busiest_active), queued: asNumber(pool.aggregate_queued), aggregate_active: asNumber(pool.aggregate_active), instance_count: asNumber(pool.instance_count), instances: Array.isArray(pool.instances) ? pool.instances : [] }));
        const pipeline = pools.find(pool => pool && pool.id === 'global_pipeline') || { id: 'global_pipeline', label: '文件管线', group: '管线调度', scope: 'global', status: 'offline', connected: false, limit: 0, active: 0, queued: 0 };
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
            pipeline: { ...pipeline, limit: asNumber(pipeline.limit), active: asNumber(pipeline.active), queued: asNumber(pipeline.queued), status: pipeline.status || 'offline', connected: pipeline.connected !== false },
            events: Array.isArray(data.events) ? data.events : [],
            history: data.history && typeof data.history === 'object' ? data.history : null,
            error: false,
        };
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
    }

    function formatTime(timestamp) {
        if (!timestamp) return '--:--:--';
        const date = new Date(typeof timestamp === 'number' ? timestamp * 1000 : timestamp);
        return Number.isNaN(date.getTime()) ? '--:--:--' : date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function orderedPools(snapshot) {
        const byId = new Map([
            ...snapshot.globalPools.map(pool => [pool.id, { ...pool, capacity: pool.limit }]),
            ...snapshot.taskPools.map(pool => [pool.id, { ...pool, limit: pool.capacity }]),
            [snapshot.pipeline.id, { ...snapshot.pipeline, capacity: snapshot.pipeline.limit }],
        ]);
        return POOL_ORDER.map(id => byId.get(id)).filter(Boolean);
    }

    function stateFor(pool) {
        return STATUS[pool.status] || STATUS.normal;
    }

    function pressureOf(pool) {
        if (pool.status === 'offline' || !pool.limit) return 0;
        return Math.max(0, Math.min(100, Math.round((pool.active / pool.limit) * 100)));
    }

    function eventVisual(type) {
        if (type === 'complete' || type === 'acquired') return { icon: 'check', color: '#4F775D', background: '#E8EFE6' };
        if (type === 'queued' || type === 'pressure') return { icon: 'triangle-alert', color: '#c47b18', background: '#fff7e9' };
        return { icon: 'clock-3', color: '#727f78', background: '#f2f4f3' };
    }

    function subjectOf(context) {
        if (!context || typeof context !== 'object') return '当前请求';
        return context.file_name || context.file_id || context.field_id || context.rule_id || context.task_id || context.model || '当前请求';
    }

    const RuntimeMonitor = {
        state: {
            active: false, loading: false, timer: null, last: null, selectedPoolId: null,
            window: DEFAULT_WINDOW,
            history: [], poolHistory: {}, charts: { pool: null, pressure: null, mini: new Map() },
            listenersBound: false, triggerElement: null, drawerFocusTimer: null,
            helpOpen: false, helpTriggerElement: null, helpFocusTimer: null,
        },
        normalizeSnapshot,
        orderedPools,

        activate() {
            if (this.state.active) return;
            this.state.active = true;
            this.bindInteractions();
            this.state.resizeHandler = () => this.resizeCharts();
            window.addEventListener('resize', this.state.resizeHandler);
            this.state.visibilityHandler = () => {
                if (document.hidden) this.stopTimer();
                else if (this.state.active) { this.refresh(); this.startTimer(); }
            };
            document.addEventListener('visibilitychange', this.state.visibilityHandler);
            this.refresh();
            if (!document.hidden) this.startTimer();
        },

        deactivate() {
            this.state.active = false;
            this.stopTimer();
            if (this.state.resizeHandler) window.removeEventListener('resize', this.state.resizeHandler);
            if (this.state.visibilityHandler) document.removeEventListener('visibilitychange', this.state.visibilityHandler);
            this.state.resizeHandler = null;
            this.state.visibilityHandler = null;
            this.disposeCharts();
            this.closeHelp(false);
            this.closePool(false);
        },

        startTimer() {
            if (this.state.timer) return;
            this.state.timer = setInterval(() => this.refresh(), POLL_MS);
        },

        stopTimer() {
            if (this.state.timer) clearInterval(this.state.timer);
            this.state.timer = null;
        },

        bindInteractions() {
            if (this.state.listenersBound) return;
            this.state.listenersBound = true;
            const close = document.getElementById('runtime-detail-close');
            const backdrop = document.getElementById('runtime-drawer-backdrop');
            const helpOpen = document.getElementById('runtime-help-open');
            const helpClose = document.getElementById('runtime-help-close');
            const helpBackdrop = document.getElementById('runtime-help-backdrop');
            const windowSelect = document.getElementById('runtime-window-select');
            if (windowSelect) {
                windowSelect.value = this.state.window;
                windowSelect.addEventListener('change', event => this.setWindow(event.target.value));
            }
            if (close) close.addEventListener('click', () => this.closePool());
            if (backdrop) backdrop.addEventListener('click', () => this.closePool());
            if (helpOpen) helpOpen.addEventListener('click', () => this.openHelp());
            if (helpClose) helpClose.addEventListener('click', () => this.closeHelp());
            if (helpBackdrop) helpBackdrop.addEventListener('click', event => {
                if (event.target === helpBackdrop) this.closeHelp();
            });
            document.addEventListener('keydown', event => {
                if (event.key !== 'Escape') return;
                if (this.state.helpOpen) this.closeHelp();
                else if (this.state.selectedPoolId) this.closePool();
            });
        },

        async refresh() {
            if (!this.state.active || this.state.loading || typeof API === 'undefined') return;
            this.state.loading = true;
            try {
                const snapshot = normalizeSnapshot(await API.getRuntimeConcurrency(this.state.window));
                this.state.last = snapshot;
                this.applyHistory(snapshot.history);
                this.render(snapshot);
            } catch (error) {
                const snapshot = this.state.last || normalizeSnapshot(null);
                this.render({ ...snapshot, error: true, error_message: error && error.message ? error.message : '请求失败' });
            } finally {
                this.state.loading = false;
            }
        },

        /**
         * 历史全部来自后端定长桶序列（60 桶）：刷新页面、切页、多开标签都看到同一条曲线。
         * 空桶（进程刚启动或该段无采样）为 null，ECharts 会断开，视觉上等价于左侧留白。
         */
        applyHistory(history) {
            const points = history && Array.isArray(history.points) ? history.points : [];
            this.state.history = points.map(point => (point ? asNumber(point.overall) : null));
            const poolHistory = {};
            points.forEach((point, index) => {
                const pools = point && point.pools && typeof point.pools === 'object' ? point.pools : {};
                Object.keys(pools).forEach(poolId => {
                    if (!poolHistory[poolId]) poolHistory[poolId] = Array(points.length).fill(null);
                    poolHistory[poolId][index] = asNumber(pools[poolId]);
                });
            });
            this.state.poolHistory = poolHistory;
            this.renderWindowMeta(history);
        },

        /** 曲线区文案随窗口变化，避免「最近 60 个采样点」在 30 分钟窗口下说谎。 */
        renderWindowMeta(history) {
            const label = WINDOW_LABELS[this.state.window] || WINDOW_LABELS[DEFAULT_WINDOW];
            const bucket = history && history.bucket_seconds ? asNumber(history.bucket_seconds, 1) : 1;
            const detail = bucket > 1 ? `${label} · 每 ${bucket} 秒取峰值` : `${label} · 每秒采样`;
            ['runtime-pressure-window', 'runtime-trends-window'].forEach(id => {
                const element = document.getElementById(id);
                if (element) element.textContent = detail;
            });
        },

        setWindow(window) {
            this.state.window = WINDOW_LABELS[window] ? window : DEFAULT_WINDOW;
            this.refresh();
        },

        render(snapshot) {
            const page = document.querySelector('.runtime-design-page');
            if (!page) return;
            page.classList.toggle('has-error', Boolean(snapshot.error));
            this.renderConnection(snapshot);
            this.renderSummary(snapshot);
            const pools = orderedPools(snapshot);
            this.ensureCharts(pools);
            this.renderPoolChart(pools);
            this.renderPressure(snapshot);
            this.renderMiniCharts(pools);
            this.renderEvents(snapshot.events, pools);
            if (this.state.selectedPoolId) this.renderDetail(this.state.selectedPoolId);
            if (typeof lucide !== 'undefined') lucide.createIcons({ attrs: { 'stroke-width': 1.5 } });
        },

        renderConnection(snapshot) {
            const pill = document.getElementById('runtime-connection-pill');
            const label = document.getElementById('runtime-connection-label');
            if (pill) pill.classList.toggle('is-error', Boolean(snapshot.error));
            if (label) label.textContent = snapshot.error ? '监控连接异常' : '实时监控已连接';
        },

        renderSummary(snapshot) {
            const summary = snapshot.summary;
            const values = {
                'runtime-summary-active': summary.active,
                'runtime-summary-capacity': `/ ${summary.capacity}`,
                'runtime-summary-queued': summary.queued,
                'runtime-summary-hot': summary.hot_pools,
                'runtime-summary-wait': (summary.wait_p95_ms / 1000).toFixed(1),
                'runtime-monitor-updated': snapshot.error ? `最后成功 ${formatTime(snapshot.updated_at)}` : formatTime(snapshot.updated_at),
            };
            Object.entries(values).forEach(([id, value]) => { const element = document.getElementById(id); if (element) element.textContent = value; });
        },

        ensureCharts(pools) {
            if (typeof echarts === 'undefined') return;
            const poolElement = document.getElementById('runtime-pool-chart');
            const pressureElement = document.getElementById('runtime-pressure-chart');
            if (poolElement && !this.state.charts.pool) {
                this.state.charts.pool = echarts.init(poolElement, null, { renderer: 'canvas' });
                this.state.charts.pool.on('click', params => {
                    if (params.data && params.data.poolId) this.openPool(params.data.poolId);
                    else if (params.componentType === 'xAxis') {
                        const index = pools.findIndex(pool => `${(POOL_META[pool.id] || {}).short || pool.id}\n${pool.label || pool.id}` === params.value);
                        if (index >= 0) this.openPool(pools[index].id);
                    }
                });
            }
            if (pressureElement && !this.state.charts.pressure) this.state.charts.pressure = echarts.init(pressureElement, null, { renderer: 'canvas' });
            const signature = pools.map(pool => pool.id).join('|');
            if (this.state.miniSignature !== signature) {
                this.state.charts.mini.forEach(chart => chart.dispose());
                this.state.charts.mini.clear();
                this.state.miniSignature = signature;
                const grid = document.getElementById('runtime-pool-pressure-grid');
                if (grid) {
                    grid.innerHTML = pools.map(pool => `<div class="runtime-design-trend-item"><div class="runtime-design-trend-title"><span>${escapeHtml(pool.label || pool.id)}</span><strong id="runtime-mini-value-${escapeHtml(pool.id)}">—</strong></div><div id="runtime-mini-chart-${escapeHtml(pool.id)}" class="runtime-design-mini-chart"></div></div>`).join('');
                    pools.forEach(pool => {
                        const element = document.getElementById(`runtime-mini-chart-${pool.id}`);
                        if (element) this.state.charts.mini.set(pool.id, echarts.init(element, null, { renderer: 'canvas' }));
                    });
                }
                const access = document.getElementById('runtime-pool-accessibility');
                if (access) {
                    access.innerHTML = pools.map(pool => `<button type="button" data-runtime-pool="${escapeHtml(pool.id)}">查看 ${escapeHtml(pool.label || pool.id)}：${escapeHtml(stateFor(pool).label)}，运行 ${pool.active}/${pool.limit}，排队 ${pool.queued}</button>`).join('');
                    access.querySelectorAll('[data-runtime-pool]').forEach(button => button.addEventListener('click', () => this.openPool(button.dataset.runtimePool, button)));
                }
            }
        },

        renderPoolChart(pools) {
            const chart = this.state.charts.pool;
            if (!chart) return;
            const compactLabels = typeof window !== 'undefined' && window.innerWidth <= 560;
            const categories = pools.map(pool => {
                const short = (POOL_META[pool.id] || {}).short || pool.id;
                const compactShort = {
                    global_table_validation: 'TAB', global_extraction: 'EXT', global_analysis: 'ANA',
                    task_table_validation: 'TBL', task_extraction: 'T-EXT', task_file_analysis: 'T-ANA',
                    independent_analysis: 'IND',
                }[pool.id] || short;
                return compactLabels ? compactShort : `${short}\n${pool.label || pool.id}`;
            });
            const active = pools.map(pool => ({ value: pool.active, poolId: pool.id, itemStyle: { color: stateFor(pool).color, opacity: pool.status === 'offline' ? .2 : .95 }, label: { color: stateFor(pool).text } }));
            const queued = pools.map(pool => ({ value: pool.queued, poolId: pool.id, itemStyle: { color: '#aab3ae', opacity: pool.status === 'offline' ? .12 : .78 } }));
            const limits = pools.map((pool, index) => [index, pool.limit, pool.id]);
            const maxAxis = Math.max(18, ...pools.map(pool => Math.max(pool.limit, pool.active + pool.queued) + 2));
            chart.setOption({
                animation: !reduceMotion, animationDuration: 500, animationDurationUpdate: 600,
                grid: { left: 42, right: 34, top: 14, bottom: 76, containLabel: true },
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: 'rgba(44,76,59,.96)', borderWidth: 0, textStyle: { color: '#fff', fontFamily: 'Nunito, sans-serif', fontSize: 12 }, formatter: params => { const pool = pools[params[0] ? params[0].dataIndex : 0]; const state = stateFor(pool); return `<strong>${escapeHtml(pool.label)}</strong><br/>运行 ${pool.active} / ${pool.limit}<br/><span style="color:#cbd7d0">排队 ${pool.queued} · ${state.label}</span>`; } },
                xAxis: { type: 'category', data: categories, triggerEvent: true, axisTick: { show: false }, axisLine: { lineStyle: { color: '#d6dfda' } }, axisLabel: { color: '#5c7065', fontFamily: 'Nunito, sans-serif', fontSize: compactLabels ? 9 : 10, lineHeight: 13, interval: 0, margin: compactLabels ? 10 : 15 } },
                yAxis: { type: 'value', min: 0, max: maxAxis, interval: 4, axisLabel: { color: '#91a098', fontSize: 10 }, splitLine: { lineStyle: { color: '#edf1ef', type: 'dashed' } }, axisLine: { show: false }, axisTick: { show: false } },
                series: [
                    { name: '运行中', type: 'bar', stack: 'load', barWidth: '68%', barMaxWidth: 42, showBackground: true, backgroundStyle: { color: '#edf2ef' }, data: active, label: { show: true, position: 'top', distance: 7, fontSize: 10, fontWeight: 700, formatter: params => `${params.value}/${pools[params.dataIndex].limit}` }, markArea: { silent: true, itemStyle: { color: 'rgba(232,239,230,.24)' }, data: [[{ xAxis: 0 }, { xAxis: 2 }], [{ xAxis: 3 }, { xAxis: 5 }], [{ xAxis: 6 }, { xAxis: 8 }], [{ xAxis: 9 }, { xAxis: 9 }], [{ xAxis: 10 }, { xAxis: 10 }]] } },
                    { name: '排队中', type: 'bar', stack: 'load', barWidth: '68%', barMaxWidth: 42, data: queued, itemStyle: { borderRadius: [8, 8, 0, 0] }, label: { show: true, position: 'top', distance: 2, color: '#758078', fontSize: 9, fontWeight: 700, formatter: params => params.value ? `+${params.value} WAIT` : '' } },
                    { name: '容量上限', type: 'custom', silent: true, z: 10, renderItem: (params, api) => { const coord = api.coord([api.value(0), api.value(1)]); return { type: 'group', children: [{ type: 'line', shape: { x1: coord[0] - 25, y1: coord[1], x2: coord[0] + 25, y2: coord[1] }, style: { stroke: '#77877e', lineWidth: 1, lineDash: [3, 3] } }, { type: 'text', style: { x: coord[0] + 29, y: coord[1] + 3, text: `L${api.value(1)}`, fill: '#9aa69f', font: '10px Nunito' } }] }; }, data: limits },
                ],
            }, true);
        },

        renderPressure() {
            const chart = this.state.charts.pressure;
            const history = this.state.history.length ? this.state.history : [null];
            const real = history.filter(item => item != null);
            const value = real.length ? real[real.length - 1] : 0;
            const previous = real.length > 1 ? real[real.length - 2] : value;
            const delta = value - previous;
            const valueElement = document.getElementById('runtime-pressure-value');
            const deltaElement = document.getElementById('runtime-pressure-delta');
            if (valueElement) valueElement.textContent = real.length ? `${value}%` : '—';
            if (deltaElement) { deltaElement.textContent = real.length ? `较上一采样 ${delta >= 0 ? '+' : ''}${delta}%` : '等待采样'; deltaElement.style.color = delta > 3 ? '#b96e14' : '#75867d'; }
            if (!chart) return;
            chart.setOption({ animation: !reduceMotion, grid: { left: 2, right: 4, top: 6, bottom: 16 }, xAxis: { type: 'category', data: history.map((_, index) => index), show: false }, yAxis: { type: 'value', min: 0, max: 100, show: false }, series: [{ type: 'line', data: history, smooth: true, showSymbol: false, lineStyle: { color: '#4F775D', width: 2 }, itemStyle: { color: '#4F775D' }, areaStyle: { color: 'rgba(79,119,93,.12)' } }] }, true);
        },

        renderMiniCharts(pools) {
            pools.forEach(pool => {
                const history = this.state.poolHistory[pool.id] || [null];
                const chart = this.state.charts.mini.get(pool.id);
                const state = stateFor(pool);
                const real = history.filter(item => item != null);
                const value = real.length ? real[real.length - 1] : 0;
                const label = document.getElementById(`runtime-mini-value-${pool.id}`);
                if (label) { label.textContent = pool.status === 'offline' ? '未接入' : `${value}%`; label.style.color = state.text; }
                if (!chart) return;
                const minValue = real.length ? Math.min(...real) : 0;
                const maxValue = real.length ? Math.max(...real) : 0;
                const padding = Math.max(8, Math.round((maxValue - minValue) / 2));
                // 高亮点跟随最后一个「有采样」的桶：整段无采样时不画，否则会在右下角留一个孤点
                let lastIndex = -1;
                history.forEach((item, index) => { if (item != null) lastIndex = index; });
                const marker = pool.status === 'offline' || lastIndex < 0 ? [] : [[lastIndex, value]];
                chart.setOption({ animation: !reduceMotion, grid: { left: 0, right: 2, top: 5, bottom: 4 }, tooltip: { trigger: 'axis', appendToBody: true, confine: true, backgroundColor: 'rgba(44,76,59,.94)', borderWidth: 0, textStyle: { color: '#fff', fontSize: 10 }, formatter: params => `${escapeHtml(pool.label)} · ${params[0].value}%` }, xAxis: { type: 'category', data: history.map((_, index) => index), show: false, boundaryGap: false }, yAxis: { type: 'value', min: Math.max(0, minValue - padding), max: Math.min(100, Math.max(maxValue + padding, 16)), splitNumber: 2, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { show: false }, splitLine: { show: true, lineStyle: { color: '#e8eeeb', type: 'dashed' } } }, series: [{ type: 'line', data: history, smooth: .28, showSymbol: false, silent: pool.status === 'offline', lineStyle: { color: state.color, width: 2.25, type: pool.status === 'offline' ? 'dashed' : 'solid', opacity: pool.status === 'offline' ? .65 : 1 }, areaStyle: { color: state.color, opacity: pool.status === 'offline' ? .025 : .12 } }, { type: 'scatter', data: marker, symbolSize: 6, silent: true, itemStyle: { color: '#fff', borderColor: state.color, borderWidth: 2 } }] }, true);
            });
        },

        renderEvents(events, pools) {
            const host = document.getElementById('runtime-event-stream');
            const count = document.getElementById('runtime-event-count');
            const poolMap = Object.fromEntries(pools.map(pool => [pool.id, pool]));
            const eventList = Array.isArray(events) ? events : [];
            const safeEvents = eventList.filter(event => event && typeof event === 'object').slice(0, 5);
            if (count) count.textContent = `${String(eventList.length).padStart(2, '0')} EVENTS`;
            if (!host) return;
            host.innerHTML = safeEvents.length ? safeEvents.map(event => {
                const visual = eventVisual(event.type);
                const pool = poolMap[event.pool_id];
                const wait = event.wait_ms != null ? ` · ${Math.round(asNumber(event.wait_ms))} ms` : '';
                return `<div class="runtime-design-event-row"><span class="runtime-design-event-icon" style="color:${visual.color};background:${visual.background}"><i data-lucide="${visual.icon}"></i></span><span class="runtime-design-event-copy"><strong>${escapeHtml(pool ? pool.label : event.pool_id || '运行事件')}</strong><span>${escapeHtml(`${subjectOf(event.context)} · ${event.type || 'event'}${wait}`)}</span></span><time>${formatTime(event.at)}</time></div>`;
            }).join('') : '<div class="runtime-monitor-empty">当前没有调度事件</div>';
        },

        openHelp() {
            const dialog = document.getElementById('runtime-help-dialog');
            const backdrop = document.getElementById('runtime-help-backdrop');
            const close = document.getElementById('runtime-help-close');
            this.state.helpOpen = true;
            this.state.helpTriggerElement = document.activeElement;
            if (backdrop) { backdrop.classList.add('open'); backdrop.setAttribute('aria-hidden', 'false'); }
            if (dialog) { dialog.classList.add('open'); dialog.setAttribute('aria-hidden', 'false'); }
            document.body.classList.add('runtime-modal-open');
            if (this.state.helpFocusTimer) clearTimeout(this.state.helpFocusTimer);
            if (close) this.state.helpFocusTimer = setTimeout(() => {
                this.state.helpFocusTimer = null;
                if (this.state.helpOpen) close.focus({ preventScroll: true });
            }, reduceMotion ? 0 : 120);
        },

        closeHelp(restoreFocus = true) {
            if (this.state.helpFocusTimer) clearTimeout(this.state.helpFocusTimer);
            this.state.helpFocusTimer = null;
            const dialog = document.getElementById('runtime-help-dialog');
            const backdrop = document.getElementById('runtime-help-backdrop');
            if (dialog) { dialog.classList.remove('open'); dialog.setAttribute('aria-hidden', 'true'); }
            if (backdrop) { backdrop.classList.remove('open'); backdrop.setAttribute('aria-hidden', 'true'); }
            document.body.classList.remove('runtime-modal-open');
            this.state.helpOpen = false;
            if (restoreFocus && this.state.helpTriggerElement && this.state.helpTriggerElement.focus) {
                this.state.helpTriggerElement.focus();
            }
            this.state.helpTriggerElement = null;
        },

        openPool(poolId, trigger) {
            if (!this.state.last) return;
            this.state.selectedPoolId = poolId;
            this.state.triggerElement = trigger || document.activeElement;
            this.renderDetail(poolId);
            const drawer = document.getElementById('runtime-detail-drawer');
            const backdrop = document.getElementById('runtime-drawer-backdrop');
            if (drawer) { drawer.classList.add('open'); drawer.setAttribute('aria-hidden', 'false'); }
            if (backdrop) { backdrop.classList.add('open'); backdrop.setAttribute('aria-hidden', 'false'); }
            const close = document.getElementById('runtime-detail-close');
            if (this.state.drawerFocusTimer) clearTimeout(this.state.drawerFocusTimer);
            if (close) this.state.drawerFocusTimer = setTimeout(() => { if (this.state.selectedPoolId && drawer && drawer.classList.contains('open')) close.focus(); }, reduceMotion ? 0 : 180);
        },

        renderDetail(poolId) {
            if (!this.state.last) return;
            const pools = orderedPools(this.state.last);
            const pool = pools.find(item => item.id === poolId);
            if (!pool) return;
            const state = stateFor(pool);
            const meta = POOL_META[pool.id] || {};
            const isTask = pool.scope === 'task';
            const values = { 'runtime-detail-group': pool.group || '', 'runtime-detail-title': pool.label || pool.id, 'runtime-detail-id': pool.id };
            Object.entries(values).forEach(([id, value]) => { const element = document.getElementById(id); if (element) element.textContent = value; });
            const body = document.getElementById('runtime-detail-body');
            if (!body) return;
            const constraints = [pool, ...(pool.constraints || []).map(id => pools.find(item => item.id === id)).filter(Boolean)];
            const holders = isTask ? (pool.instances || []) : (pool.tasks || []);
            body.innerHTML = `<div class="runtime-detail-metrics"><div class="runtime-detail-metric"><span>${isTask ? '最忙实例' : '运行'}</span><strong>${pool.active}</strong></div><div class="runtime-detail-metric"><span>${isTask ? '每实例上限' : '容量'}</span><strong>${pool.limit}</strong></div><div class="runtime-detail-metric"><span>排队</span><strong>${pool.queued}</strong></div></div>
                ${isTask ? `<div class="runtime-detail-section"><div class="runtime-detail-section-head"><h3>实例汇总</h3><span class="runtime-detail-status" style="color:${state.text};border-color:${state.color}55;background:${state.color}12">${state.label}</span></div><div class="runtime-constraint-row"><span class="runtime-constraint-bar" style="background:${state.color}"></span><div class="runtime-constraint-copy"><div><span>${pool.instance_count} 个活动实例</span><span>${pool.aggregate_active || 0} ACTIVE</span></div><div class="runtime-constraint-meter"><i style="width:${Math.min(100, pressureOf(pool))}%;background:${state.color}"></i></div></div></div></div>` : ''}
                <div class="runtime-detail-section"><div class="runtime-detail-section-head"><h3>约束路径</h3><span class="runtime-detail-status" style="color:${state.text};border-color:${state.color}55;background:${state.color}12">${state.label}</span></div>${constraints.map(item => { const itemState = stateFor(item); return `<div class="runtime-constraint-row"><span class="runtime-constraint-bar" style="background:${itemState.color}"></span><div class="runtime-constraint-copy"><div><span>${escapeHtml(item.label || item.id)}</span><span>${item.active}/${item.limit}</span></div><div class="runtime-constraint-meter"><i style="width:${pressureOf(item)}%;background:${itemState.color}"></i></div></div></div>`; }).join('')}</div>
                <div class="runtime-detail-section"><div class="runtime-detail-section-head"><h3>${isTask ? '当前实例' : '当前占用任务'}</h3><span>${holders.length}</span></div>${holders.length ? holders.slice(0, 8).map(item => `<div class="runtime-detail-task"><span class="runtime-detail-task-icon"><i data-lucide="file-text"></i></span><span class="runtime-detail-task-copy"><strong>${escapeHtml(item.file_name || item.file_id || item.instance_id || item.task_id || '当前任务')}</strong><span>${escapeHtml(item.stage || item.model || `${item.active || 0} active · ${item.queued || 0} queued`)}</span></span></div>`).join('') : '<div class="runtime-monitor-empty">当前没有运行任务</div>'}</div>
                <p class="runtime-detail-note"><strong>运行说明</strong><br>${escapeHtml(pool.note || meta.note || '当前进程内只读并发状态。')}</p>`;
            if (typeof lucide !== 'undefined') lucide.createIcons({ attrs: { 'stroke-width': 1.5 } });
        },

        closePool(restoreFocus = true) {
            if (this.state.drawerFocusTimer) clearTimeout(this.state.drawerFocusTimer);
            this.state.drawerFocusTimer = null;
            const drawer = document.getElementById('runtime-detail-drawer');
            const backdrop = document.getElementById('runtime-drawer-backdrop');
            if (drawer) { drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true'); }
            if (backdrop) { backdrop.classList.remove('open'); backdrop.setAttribute('aria-hidden', 'true'); }
            this.state.selectedPoolId = null;
            if (restoreFocus && this.state.triggerElement && this.state.triggerElement.focus) this.state.triggerElement.focus();
            this.state.triggerElement = null;
        },

        resizeCharts() {
            if (this.state.charts.pool) this.state.charts.pool.resize();
            if (this.state.charts.pressure) this.state.charts.pressure.resize();
            this.state.charts.mini.forEach(chart => chart.resize());
        },

        disposeCharts() {
            if (this.state.charts.pool) this.state.charts.pool.dispose();
            if (this.state.charts.pressure) this.state.charts.pressure.dispose();
            this.state.charts.mini.forEach(chart => chart.dispose());
            this.state.charts = { pool: null, pressure: null, mini: new Map() };
            this.state.miniSignature = '';
        },
    };

    root.RuntimeMonitor = RuntimeMonitor;
    if (typeof module !== 'undefined' && module.exports) module.exports = RuntimeMonitor;
})(typeof window !== 'undefined' ? window : globalThis);
