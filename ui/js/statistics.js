/**
 * 数据统计页模块
 *
 * 入口：点击左上角「析卷 AI」标题（该页没有导航按钮，App.switchPage 会把导航指示器收起）。
 * 数据：GET /file/stats —— 不受顶部项目 / 文档类型选择器影响（统计全部类型），
 *       但受工具条时间窗口约束，且该窗口**统一作用于整页所有指标**（KPI + 全部图表）。
 * 图表：本地内置 ECharts 5.5.1（ui/vendor/echarts/），无外网依赖。
 */

const Statistics = {
    state: {
        charts: {},      // dom id -> echarts 实例（跨页面切换复用，不重复 init）
        data: null,
        range: '30d',    // 统计时间窗口，作用于整页全部指标
        loading: false,
        bound: false,
        resizeTimer: null,
    },

    // 与 css/style.css 的生物亲和主题同源
    COLOR: {
        dark: '#2C4C3B',
        med: '#4F775D',
        light: '#8DAA91',
        pale: '#E8EFE6',
        earth: '#8B735B',
        error: '#C45B4A',
        warning: '#C49A3C',
        axis: 'rgba(44, 76, 59, 0.45)',
        split: 'rgba(44, 76, 59, 0.07)',
    },

    PALETTE: ['#4F775D', '#8DAA91', '#8B735B', '#C49A3C', '#6B8F7B',
              '#A8C3AC', '#C45B4A', '#2C4C3B', '#D4B483', '#7FA88B'],

    // 失败态 / 处理中态各自的渐变色带：同类状态多于一个时按序取色，避免整片同色饼图
    FAILED_COLORS: ['#C45B4A', '#D5836C', '#A8453A', '#E0A78E', '#8E3B31', '#C9705C'],
    PROC_COLORS: ['#8DAA91', '#A8C3AC', '#7FA88B', '#BCD1BE', '#6B8F7B', '#9FBCA4', '#5C7F6B'],

    STAGES: ['parsing', 'tableing', 'chunking', 'embedding', 'extracting', 'analyzing'],

    FONT: 'Nunito, sans-serif',

    // ─────────────────────────────────────────────────────────
    // 生命周期
    // ─────────────────────────────────────────────────────────

    init() {
        if (this.state.bound) return;
        this.state.bound = true;

        // 标题是 role="button"，补齐键盘可达性（点击走 index.html 的 onclick）
        const title = document.getElementById('app-title');
        if (title) {
            title.addEventListener('keydown', e => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    App.switchPage('statistics');
                }
            });
        }

        const refresh = document.getElementById('stats-refresh');
        if (refresh) refresh.addEventListener('click', () => this.load());

        const daysSel = document.getElementById('stats-range');
        if (daysSel) {
            daysSel.addEventListener('change', () => {
                this.state.range = daysSel.value || '30d';
                this.load();
            });
        }

        window.addEventListener('resize', () => {
            clearTimeout(this.state.resizeTimer);
            this.state.resizeTimer = setTimeout(() => this.resizeAll(), 150);
        });
    },

    /** 进入统计页时调用（此时容器已 active，图表才能拿到非 0 尺寸）。 */
    activate() {
        this.load();
    },

    /** 离开统计页。图表实例保留，下次进入直接 setOption，避免重复创建 canvas。 */
    deactivate() {
        clearTimeout(this.state.resizeTimer);
    },

    async load() {
        if (this.state.loading) return;
        this.state.loading = true;
        this.setMeta('加载中...');
        try {
            const data = await API.getFileStats(this.state.range);
            this.state.data = data;
            this.render(data);
            const ov = data.overview || {};
            this.setMeta(`${this.rangeLabel()} · ${ov.total_files || 0} 个文件 · `
                + `${Utils.formatDateTime(new Date())} 更新`);
        } catch (e) {
            this.setMeta('加载失败：' + e.message);
            if (typeof Toast !== 'undefined') Toast.error('统计数据加载失败: ' + e.message);
        } finally {
            this.state.loading = false;
        }
    },

    /** 当前窗口的中文名，直接取下拉选中项文本，避免另存一份会漂移的映射表。 */
    rangeLabel() {
        const sel = document.getElementById('stats-range');
        const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
        return opt ? opt.textContent.trim() : this.state.range;
    },

    setMeta(text) {
        const el = document.getElementById('stats-meta');
        if (el) el.textContent = text;
    },

    render(data) {
        this.renderKpi(data.overview || {});
        this.renderStatus(data.status_distribution || []);
        this.renderProject(data.by_project || []);
        this.renderTrend(data.trend || [], data);
        this.renderType(data.by_type || []);
        this.renderStage(data.stage_durations || []);
    },

    // ─────────────────────────────────────────────────────────
    // 图表基础设施
    // ─────────────────────────────────────────────────────────

    chart(id) {
        const el = document.getElementById(id);
        if (!el || typeof echarts === 'undefined') return null;
        let c = this.state.charts[id];
        if (!c || c.isDisposed()) {
            c = echarts.init(el);
            this.state.charts[id] = c;
        }
        return c;
    },

    /** setOption + resize：容器宽度可能在上次渲染后变过（切页/开合弹窗）。 */
    apply(c, option) {
        c.setOption(option, true);
        c.resize();
    },

    setEmpty(c, text = '暂无数据') {
        this.apply(c, {
            title: {
                text,
                left: 'center',
                top: 'center',
                textStyle: {
                    color: this.COLOR.axis, fontSize: 13,
                    fontWeight: 'normal', fontFamily: this.FONT,
                },
            },
        });
    },

    resizeAll() {
        const page = document.getElementById('page-statistics');
        if (!page || !page.classList.contains('active')) return;
        Object.values(this.state.charts).forEach(c => {
            if (c && !c.isDisposed()) c.resize();
        });
    },

    tooltipBase() {
        return {
            backgroundColor: 'rgba(255,255,255,0.96)',
            borderColor: 'rgba(44,76,59,0.12)',
            borderWidth: 1,
            padding: [8, 12],
            textStyle: { color: this.COLOR.dark, fontSize: 12, fontFamily: this.FONT },
            extraCssText: 'box-shadow: 0 4px 20px rgba(44,76,59,0.12); border-radius: 10px;',
        };
    },

    // ─────────────────────────────────────────────────────────
    // KPI 概览
    // ─────────────────────────────────────────────────────────

    renderKpi(ov) {
        const el = document.getElementById('stats-kpi');
        if (!el) return;
        // 失败 / 处理中不单独立卡：两者的构成在「处理状态分布」环形图里已经按状态拆得更细。
        const cards = [
            { label: '已处理文件', value: ov.total_files || 0, icon: 'files', tone: 'primary',
              sub: `${ov.type_count || 0} 个文档类型 · ${ov.project_count || 0} 个项目` },
            { label: '已完成', value: ov.completed || 0, icon: 'circle-check', tone: 'success',
              sub: `成功率 ${ov.success_rate || 0}%` },
            // 口径：窗口内现存记录的 file_size 之和，含失败与处理中的文件。
            // 删文件会让它减少（不是只增不减的历史累计），而 storage 保留策略只清
            // uploads 下的物理 PDF、不动数据库，所以它也不等于磁盘占用。
            { label: '已处理文件总体积', value: Utils.formatFileSize(ov.total_size || 0), icon: 'hard-drive', tone: 'earth',
              sub: '全部状态，PDF 原始大小' },
            { label: '平均全流程耗时', value: this.fmtDuration(ov.avg_total_seconds), icon: 'timer', tone: 'primary',
              sub: '六阶段实际耗时合计' },
        ];
        el.innerHTML = cards.map(c => `
            <div class="stats-kpi-card glass-card tone-${c.tone}">
                <div class="stats-kpi-icon"><i data-lucide="${c.icon}" class="w-5 h-5"></i></div>
                <div class="stats-kpi-body">
                    <div class="stats-kpi-value">${c.value}</div>
                    <div class="stats-kpi-label">${c.label}</div>
                    ${c.sub ? `<div class="stats-kpi-sub">${c.sub}</div>` : ''}
                </div>
            </div>`).join('');
        if (window.lucide) lucide.createIcons();
    },

    // ─────────────────────────────────────────────────────────
    // 各图表
    // ─────────────────────────────────────────────────────────

    /** 处理状态分布（环形图）。状态中文名统一走 Utils.getStatusText，不在本模块另存一份。 */
    renderStatus(items) {
        const c = this.chart('chart-status');
        if (!c) return;
        if (!items.length) return this.setEmpty(c);

        let failedIdx = 0, procIdx = 0;
        const data = items.map(i => {
            let color;
            if (i.key === 'complete') {
                color = this.COLOR.med;
            } else if (Utils.isFailed(i.key)) {
                color = this.FAILED_COLORS[failedIdx++ % this.FAILED_COLORS.length];
            } else {
                color = this.PROC_COLORS[procIdx++ % this.PROC_COLORS.length];
            }
            return { name: Utils.getStatusText(i.key), value: i.count, itemStyle: { color } };
        });
        const total = items.reduce((s, i) => s + (i.count || 0), 0);

        this.apply(c, {
            // 实际分布通常极度偏向「已完成」，环几乎单色；圆心补总数，让这张图仍有信息量
            title: {
                text: String(total),
                subtext: '文件总数',
                left: '50%',
                top: '36%',
                textAlign: 'center',
                textStyle: {
                    color: this.COLOR.dark, fontSize: 22,
                    fontWeight: 700, fontFamily: 'Lora, Georgia, serif',
                },
                subtextStyle: { color: this.COLOR.axis, fontSize: 11, fontFamily: this.FONT },
            },
            tooltip: {
                ...this.tooltipBase(),
                trigger: 'item',
                formatter: p => `${p.name}<br/><b>${p.value}</b> 个文件 · ${p.percent}%`,
            },
            legend: {
                type: 'scroll', bottom: 0, itemWidth: 10, itemHeight: 10,
                textStyle: { color: this.COLOR.med, fontSize: 11, fontFamily: this.FONT },
            },
            series: [{
                type: 'pie',
                radius: ['46%', '70%'],
                center: ['50%', '45%'],
                avoidLabelOverlap: true,
                itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
                label: { show: false },
                labelLine: { show: false },
                // 圆心已被总数占用，hover 只放大扇区，不再往圆心塞文字
                emphasis: { scale: true, scaleSize: 6 },
                data,
            }],
        });
    },

    /** 项目文件占比（饼图）。超过 8 个项目时其余合并为「其他」，避免图例爆炸。 */
    renderProject(items) {
        const c = this.chart('chart-project');
        if (!c) return;
        if (!items.length) return this.setEmpty(c);

        const shown = this.topN(items, 8);
        const total = shown.reduce((s, i) => s + (i.count || 0), 0) || 1;
        // 占比过小的切片挂标签只会互相压字，隐藏其标签与引导线（图例/tooltip 仍可查）
        const data = shown.map((i, idx) => {
            const big = (i.count || 0) / total >= 0.03;
            return {
                name: i.label,
                value: i.count,
                size: i.size,
                itemStyle: { color: this.PALETTE[idx % this.PALETTE.length] },
                label: { show: big },
                labelLine: { show: big },
            };
        });

        this.apply(c, {
            tooltip: {
                ...this.tooltipBase(),
                trigger: 'item',
                formatter: p => `${p.name}<br/><b>${p.value}</b> 个文件 · ${p.percent}%`
                    + `<br/>体积 ${Utils.formatFileSize(p.data.size || 0)}`,
            },
            legend: {
                type: 'scroll', bottom: 0, itemWidth: 10, itemHeight: 10,
                textStyle: { color: this.COLOR.med, fontSize: 11, fontFamily: this.FONT },
            },
            series: [{
                type: 'pie',
                radius: '62%',
                center: ['50%', '45%'],
                itemStyle: { borderColor: '#fff', borderWidth: 2 },
                label: {
                    color: this.COLOR.med, fontSize: 11, fontFamily: this.FONT,
                    formatter: '{b}\n{d}%',
                },
                labelLine: { length: 8, length2: 8, lineStyle: { color: 'rgba(44,76,59,0.25)' } },
                data,
            }],
        });
    },

    /** 处理趋势（折线 + 面积）。空桶补 0，否则折线会把没上传的时段连成斜线。 */
    renderTrend(items, meta) {
        const c = this.chart('chart-trend');
        if (!c) return;
        const byHour = (meta.granularity || 'day') === 'hour';
        const hint = document.getElementById('stats-trend-hint');
        if (hint) hint.textContent = byHour ? '按小时统计（上传时间）' : '按天统计（上传日期）';

        const filled = this.fillTrend(items, meta);
        if (!filled.length) return this.setEmpty(c);

        const dates = filled.map(i => i.date);
        const mk = (name, key, color, area) => ({
            name, type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
            showSymbol: filled.length <= 45,
            itemStyle: { color },
            lineStyle: { width: 2, color },
            areaStyle: area ? {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: color + '55' },
                    { offset: 1, color: color + '05' },
                ]),
            } : undefined,
            data: filled.map(i => i[key] || 0),
        });

        this.apply(c, {
            tooltip: { ...this.tooltipBase(), trigger: 'axis' },
            legend: {
                top: 0, right: 0, itemWidth: 12, itemHeight: 8,
                textStyle: { color: this.COLOR.med, fontSize: 11, fontFamily: this.FONT },
            },
            grid: { left: 8, right: 12, top: 34, bottom: filled.length > 45 ? 46 : 8, containLabel: true },
            xAxis: {
                type: 'category', boundaryGap: false, data: dates,
                axisLine: { lineStyle: { color: 'rgba(44,76,59,0.15)' } },
                axisTick: { show: false },
                axisLabel: {
                    color: this.COLOR.axis, fontSize: 11, fontFamily: this.FONT,
                    // 小时桶：整点显示 HH:00，跨天的 00 点改显 MM-DD 当分隔标记
                    formatter: v => byHour
                        ? (v.slice(11) === '00:00' ? v.slice(5, 10) : v.slice(11))
                        : v.slice(5),
                },
            },
            yAxis: {
                type: 'value', minInterval: 1,
                axisLine: { show: false }, axisTick: { show: false },
                axisLabel: { color: this.COLOR.axis, fontSize: 11, fontFamily: this.FONT },
                splitLine: { lineStyle: { color: this.COLOR.split } },
            },
            dataZoom: filled.length > 45 ? [
                { type: 'inside', start: 60, end: 100 },
                {
                    type: 'slider', height: 18, bottom: 8,
                    start: 60, end: 100,
                    borderColor: 'transparent',
                    fillerColor: 'rgba(79,119,93,0.15)',
                    handleStyle: { color: this.COLOR.light },
                    textStyle: { color: this.COLOR.axis, fontSize: 10 },
                },
            ] : undefined,
            series: [
                mk('上传总数', 'count', this.COLOR.med, true),
                mk('已完成', 'completed', this.COLOR.light, false),
                mk('失败', 'failed', this.COLOR.error, false),
            ],
        });
    },

    /** 文档类型排行（横向条形图，前 10）。 */
    renderType(items) {
        const c = this.chart('chart-type');
        if (!c) return;
        if (!items.length) return this.setEmpty(c);

        // 横向条形图 y 轴自下而上，倒序后最大值才落在顶部
        const top = items.slice(0, 10).reverse();
        this.apply(c, {
            tooltip: {
                ...this.tooltipBase(),
                trigger: 'item',
                formatter: p => `${p.name}<br/><b>${p.value}</b> 个文件`
                    + `<br/>体积 ${Utils.formatFileSize(top[p.dataIndex].size || 0)}`,
            },
            grid: { left: 8, right: 34, top: 10, bottom: 8, containLabel: true },
            xAxis: {
                type: 'value', minInterval: 1,
                axisLine: { show: false }, axisTick: { show: false },
                axisLabel: { color: this.COLOR.axis, fontSize: 11, fontFamily: this.FONT },
                splitLine: { lineStyle: { color: this.COLOR.split } },
            },
            yAxis: {
                type: 'category',
                data: top.map(i => i.label),
                axisLine: { show: false }, axisTick: { show: false },
                axisLabel: {
                    color: this.COLOR.med, fontSize: 11, fontFamily: this.FONT,
                    width: 96, overflow: 'truncate',
                },
            },
            series: [{
                type: 'bar',
                barMaxWidth: 18,
                itemStyle: {
                    borderRadius: [0, 6, 6, 0],
                    // 按排名深→浅渐变：颜色承载「第几名」这一信息。
                    // 若按索引取调色板，红色会随机落到某个类型上，看起来像出错了。
                    color: p => this.mix(
                        this.COLOR.dark, '#B7CDB9',
                        (top.length - 1 - p.dataIndex) / Math.max(1, top.length - 1)
                    ),
                },
                label: {
                    show: true, position: 'right',
                    color: this.COLOR.med, fontSize: 11, fontFamily: this.FONT,
                },
                data: top.map(i => i.count),
            }],
        });
    },

    /** 各阶段耗时（平均 / 最长，单位秒）。样本数在 tooltip 里给出，避免误读小样本。 */
    renderStage(items) {
        const c = this.chart('chart-stage');
        if (!c) return;
        const rows = this.STAGES
            .map(s => items.find(i => i.stage === s))
            .filter(Boolean);
        if (!rows.length || rows.every(r => !r.samples)) {
            return this.setEmpty(c, '暂无完整的阶段耗时记录');
        }

        const names = rows.map(r => Utils.getStageText(r.stage));
        this.apply(c, {
            tooltip: {
                ...this.tooltipBase(),
                trigger: 'axis',
                axisPointer: { type: 'shadow' },
                formatter: params => {
                    const i = params[0].dataIndex;
                    const r = rows[i];
                    return `${names[i]}<br/>`
                        + `平均 <b>${this.fmtDuration(r.avg_seconds)}</b><br/>`
                        + `最短 ${this.fmtDuration(r.min_seconds)} · 最长 ${this.fmtDuration(r.max_seconds)}<br/>`
                        + `样本 ${r.samples} 个 · 累计 ${this.fmtDuration(r.total_seconds)}`;
                },
            },
            legend: {
                top: 0, right: 0, itemWidth: 12, itemHeight: 8,
                textStyle: { color: this.COLOR.med, fontSize: 11, fontFamily: this.FONT },
            },
            grid: { left: 8, right: 12, top: 34, bottom: 8, containLabel: true },
            xAxis: {
                type: 'category', data: names,
                axisLine: { lineStyle: { color: 'rgba(44,76,59,0.15)' } },
                axisTick: { show: false },
                axisLabel: { color: this.COLOR.axis, fontSize: 11, fontFamily: this.FONT, interval: 0 },
            },
            // 双轴：最长耗时常是平均值的几十倍，同轴会把平均值压成贴地的一条线。
            // 轴名写进图例（「左轴/右轴」），避免右轴名与右上角图例挤在同一处。
            yAxis: [
                {
                    type: 'value',
                    axisLine: { show: false }, axisTick: { show: false },
                    axisLabel: { color: this.COLOR.axis, fontSize: 11, fontFamily: this.FONT },
                    splitLine: { lineStyle: { color: this.COLOR.split } },
                },
                {
                    type: 'value',
                    axisLine: { show: false }, axisTick: { show: false },
                    axisLabel: { color: this.COLOR.earth, fontSize: 11, fontFamily: this.FONT },
                    splitLine: { show: false },
                },
            ],
            series: [
                {
                    name: '平均耗时（左轴·秒）', type: 'bar', yAxisIndex: 0, barMaxWidth: 26,
                    itemStyle: { color: this.COLOR.med, borderRadius: [6, 6, 0, 0] },
                    data: rows.map(r => r.avg_seconds),
                },
                {
                    name: '最长耗时（右轴·秒）', type: 'line', yAxisIndex: 1,
                    symbol: 'circle', symbolSize: 7, smooth: false,
                    itemStyle: { color: this.COLOR.earth },
                    lineStyle: { width: 2, color: this.COLOR.earth, type: 'dashed' },
                    data: rows.map(r => r.max_seconds),
                },
            ],
        });
    },

    // ─────────────────────────────────────────────────────────
    // 纯函数工具
    // ─────────────────────────────────────────────────────────

    /** 取前 n 项，其余合并为「其他」。 */
    topN(items, n) {
        if (items.length <= n) return items;
        const head = items.slice(0, n);
        const rest = items.slice(n);
        head.push({
            key: '__others__',
            label: `其他（${rest.length}）`,
            count: rest.reduce((s, i) => s + (i.count || 0), 0),
            size: rest.reduce((s, i) => s + (i.size || 0), 0),
        });
        return head;
    },

    /**
     * 把服务端「只含有数据的桶」补成连续序列。
     *
     * 窗口边界取服务端回传的 `start_time` / `end_time`（同一台机器算出来的，
     * 不依赖客户端时钟）；`all` 没有下界，则从最早一条数据起画。
     * 同时把数据本身的边界并进来兜底，避免有数据却落在窗口外画不出来。
     */
    fillTrend(items, meta) {
        const byHour = (meta.granularity || 'day') === 'hour';
        const byKey = new Map(items.map(i => [i.date, i]));

        const stamps = items.map(i => this.parseStamp(i.date)).filter(Boolean);
        let start = this.parseStamp(meta.start_time) || stamps[0];
        let end = this.parseStamp(meta.end_time) || new Date();
        if (!start) return [];
        stamps.forEach(d => {
            if (d < start) start = d;
            if (d > end) end = d;
        });

        const cur = this.floorTo(start, byHour);
        const last = this.floorTo(end, byHour);
        const out = [];
        // 上限兜底：防止脏数据（如未来时间）把循环拉爆
        while (cur <= last && out.length < 1000) {
            const key = this.bucketKey(cur, byHour);
            out.push(byKey.get(key) || { date: key, count: 0, completed: 0, failed: 0 });
            if (byHour) cur.setHours(cur.getHours() + 1);
            else cur.setDate(cur.getDate() + 1);
        }
        return out;
    },

    /** 解析 `YYYY-MM-DD`、`YYYY-MM-DD HH:mm` 与 ISO `YYYY-MM-DDTHH:mm:ss`（一律按本地时区）。 */
    parseStamp(s) {
        const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(s || '');
        return m ? new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)) : null;
    },

    floorTo(d, byHour) {
        const out = new Date(d);
        out.setMinutes(0, 0, 0);
        if (!byHour) out.setHours(0);
        return out;
    },

    bucketKey(d, byHour) {
        const p = n => String(n).padStart(2, '0');
        const day = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
        return byHour ? `${day} ${p(d.getHours())}:00` : day;
    },

    /** 两个 #rrggbb 之间线性插值，t=0 取 c1，t=1 取 c2。 */
    mix(c1, c2, t) {
        const parse = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
        const [a, b] = [parse(c1), parse(c2)];
        const hex = n => Math.round(Math.min(255, Math.max(0, n))).toString(16).padStart(2, '0');
        return '#' + a.map((v, i) => hex(v + (b[i] - v) * t)).join('');
    },

    /** 秒 → 人类可读时长。null/undefined 显示为占位符。 */
    fmtDuration(seconds) {
        if (seconds === null || seconds === undefined) return '—';
        const s = Number(seconds);
        if (!isFinite(s)) return '—';
        if (s < 60) return `${Math.round(s * 10) / 10}s`;
        if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
        return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
    },
};
