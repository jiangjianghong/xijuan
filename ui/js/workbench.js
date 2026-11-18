/** 工作台外壳：导航、键盘操作和动效。业务数据仍由原有模块负责。 */
const Workbench = {
    pages: {
        'file-processing': { title: '文件处理', description: '将 PDF 转化为结构化信息，让每一份文档都清晰可用。', icon: 'files', group: '工作空间' },
        'rule-config': { title: '规则配置', description: '定义需要提取的信息，以及从信息到结论的规则。', icon: 'sliders-horizontal', group: '工作空间' },
        'stream-demo': { title: '流式演示', description: '沿着六个处理阶段，实时观察一份文档如何成为答案。', icon: 'workflow', group: '工作空间' },
        statistics: { title: '数据统计', description: '从处理规模、状态分布到阶段耗时，了解全部文档的运行情况。', icon: 'chart-no-axes-combined', group: '洞察与运行' },
        'runtime-monitor': { title: '并发运行台', description: '查看当前进程的任务、通道容量与等待情况。', icon: 'activity', group: '洞察与运行' },
        invocations: { title: '调用日志', description: '追溯每一次调用，从输入、处理过程到最终结果。', icon: 'list-checks', group: '洞察与运行' },
        'runtime-logs': { title: '实时日志', description: '连接运行日志流，让执行细节与异常即时可见。', icon: 'square-terminal', group: '洞察与运行' },
        'api-docs': { title: 'API 手册', description: '查阅接口、参数与返回结构，将文档能力接入你的业务。', icon: 'book-open', group: '开发与管理' },
        settings: { title: '系统设置', description: '保存后对新请求和新任务生效。', icon: 'settings-2', group: '开发与管理' },
    },
    animations: [],
    commandIndex: 0,

    init() {
        this.sidebar = document.getElementById('workspace-sidebar');
        this.settingsNav = document.getElementById('settings-subnav');
        this.settingsNavButton = document.getElementById('settings-nav-button');
        SettingsManager.renderNavigation(this.settingsNav);
        this.dialog = document.getElementById('workspace-command');
        this.commandInput = document.getElementById('workspace-command-input');
        this.compact = window.matchMedia('(max-width: 1100px)');
        this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
        this.syncSidebar();
        this.compact.addEventListener('change', () => { this.closeSidebar(false); this.syncSidebar(); });
        this.reduced.addEventListener('change', () => { if (this.reduced.matches) this.cancelAnimations(); });
        this.commands = [
            ...Object.entries(this.pages).map(([page, meta]) => ({ ...meta, action: () => App.switchPage(page) })),
            { title: '上传 PDF 文件', icon: 'file-up', group: '文件处理', action: () => { App.switchPage('file-processing'); document.getElementById('file-input').click(); } },
            { title: '管理项目与文档类型', icon: 'folder-cog', group: '开发与管理', action: () => DocTypeManager.openManageDialog() },
            { title: '智能体工作台', icon: 'bot', group: 'Beta', action: () => { location.href = '/agent'; } },
        ];
        document.getElementById('sidebar-toggle').addEventListener('click', () => this.openSidebar());
        document.getElementById('sidebar-close').addEventListener('click', () => this.closeSidebar());
        document.getElementById('sidebar-backdrop').addEventListener('click', () => this.closeSidebar());
        this.sidebar.addEventListener('click', event => {
            const settingGroup = event.target.closest('#settings-subnav [data-jump-group]');
            if (settingGroup) {
                this.closeSidebar(false);
                SettingsManager.jumpToGroup(settingGroup.dataset.jumpGroup);
                return;
            }
            if (event.target.closest('#settings-nav-button')) {
                this.revealSettingsNavigation();
                return;
            }
            const samePage = document.body.classList.contains('nav-open') && event.target.closest('.nav-btn[data-page].active');
            if (event.target.closest('.nav-btn,.sidebar-account,.runtime-monitor-entry,#app-title')) this.closeSidebar(false);
            if (samePage) document.getElementById('workspace-title').focus({ preventScroll: true });
        });
        document.getElementById('workspace-command-open').addEventListener('click', () => this.openCommand());
        document.getElementById('workspace-command-close').addEventListener('click', () => this.dialog.close());
        this.dialog.addEventListener('click', event => { if (event.target === this.dialog) this.dialog.close(); });
        this.dialog.addEventListener('close', () => {
            if (this.commandReturnFocus?.isConnected && !this.commandReturnFocus.closest('[inert]')) this.commandReturnFocus.focus({ preventScroll: true });
        });
        this.commandInput.addEventListener('input', () => this.renderCommands());
        this.commandInput.addEventListener('keydown', event => this.commandKeydown(event));
        document.getElementById('workspace-command-results').addEventListener('click', event => {
            const button = event.target.closest('[data-command-index]');
            if (button) this.runCommand(Number(button.dataset.commandIndex));
        });
        document.getElementById('workspace-refresh').addEventListener('click', async event => {
            const button = event.currentTarget;
            if (button.disabled) return;
            button.disabled = true;
            button.classList.add('is-refreshing');
            try { await App.loadFileList(); await App.restoreProcessingQueue(); }
            finally { button.classList.remove('is-refreshing'); button.disabled = false; }
        });
        document.addEventListener('keydown', event => this.keydown(event));
        document.addEventListener('workbench:page', event => this.showPage(event.detail));
        document.addEventListener('workbench:queue', event => this.updateQueue(event.detail.items));
        document.addEventListener('workbench:files', event => {
            if (event.detail.error) {
                document.getElementById('workspace-file-total').textContent = '加载失败 · 请刷新重试';
                return;
            }
            const { total, count } = event.detail;
            document.getElementById('workspace-file-total').textContent = `共 ${Number(total ?? count).toLocaleString()} 份`;
        });
        window.addEventListener('popstate', () => {
            const page = new URLSearchParams(location.search).get('page') || 'file-processing';
            App.switchPage(this.pages[page] ? page : 'file-processing', { history: 'none' });
        });
        if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
        window.addEventListener('resize', () => {
            cancelAnimationFrame(this.resizeFrame);
            this.resizeFrame = requestAnimationFrame(() => App.updateNavIndicator(document.querySelector('.nav-btn.active')));
        });
        const current = document.querySelector('.page-container.active')?.id.replace('page-', '') || 'file-processing';
        this.showPage({ page: current, animate: false });
        this.reveal(document.querySelector('.workspace-page-heading'), 0, 7);
        this.reveal(document.getElementById(`page-${current}`), 50, 10);
        this.updateQueue([...App.state.queue.values()]);
        document.addEventListener('click', event => {
            if (event.target.closest('.sub-tab-btn')) {
                document.querySelectorAll('#page-rule-config .rule-section.active').forEach((section, index) => this.reveal(section, index * 35, 5));
            }
        });
        if (typeof lucide !== 'undefined') lucide.createIcons();
    },

    showPage({ page, animate = true }) {
        const meta = this.pages[page];
        if (!meta) return;
        const fromSidebar = document.body.classList.contains('nav-open');
        const settings = page === 'settings';
        this.sidebar.classList.toggle('settings-open', settings);
        this.settingsNav.hidden = !settings;
        this.settingsNavButton.setAttribute('aria-expanded', String(settings));
        if (!settings) this.closeSidebar(false);
        document.getElementById('workspace-title').textContent = meta.title;
        if (fromSidebar && !settings) document.getElementById('workspace-title').focus({ preventScroll: true });
        if (settings) {
            SettingsManager.syncActiveGroup();
            this.revealSettingsNavigation();
        }
        document.getElementById('workspace-description').textContent = meta.description;
        document.getElementById('workspace-breadcrumb-current').textContent = meta.title;
        document.getElementById('workspace-file-actions').hidden = page !== 'file-processing';
        const scoped = ['file-processing', 'rule-config', 'stream-demo'].includes(page);
        document.querySelector('.doctype-bar').hidden = !scoped;
        const scopeNote = document.getElementById('workspace-scope-note');
        scopeNote.hidden = scoped;
        scopeNote.querySelector('span').textContent = {
            statistics: '全部文档 · 按时间范围统计', invocations: '全部调用 · 跨文档类型',
            'runtime-monitor': '当前 worker · 实时监控', 'runtime-logs': '运行日志 · 全局视图',
            'api-docs': '开发者参考 · API',
            settings: '全局设置',
        }[page] || '';
        document.getElementById('workspace-announcer').textContent = `已切换到${meta.title}`;
        document.title = `${meta.title} · 析卷 AI`;
        this.cancelAnimations();
        // 仅用户切换时播放；轮询刷新不会重新入场或抖动。
        if (animate && !this.reduced.matches) {
            const target = document.getElementById(`page-${page}`);
            const heading = document.querySelector('.workspace-page-heading');
            [heading, target].forEach((element, index) => this.reveal(element, index * 35, index ? 10 : 5));
        }
    },

    reveal(element, delay = 0, distance = 6) {
        if (!element?.animate || this.reduced.matches) return;
        this.animations = this.animations.filter(animation => animation.playState !== 'finished');
        this.animations.push(element.animate([
            { opacity: 0, transform: `translateY(${distance}px)` },
            { opacity: 1, transform: 'translateY(0)' },
        ], { duration: 340, delay, easing: 'cubic-bezier(.22,1,.36,1)', fill: 'backwards' }));
    },

    cancelAnimations() {
        this.animations.forEach(animation => animation.cancel());
        this.animations = [];
    },

    updateQueue(items) {
        const current = items.filter(item => !item.typeId || item.typeId === API.getCurrentTypeId());
        const active = current.filter(item => item.stage !== 'upload_failed' && item.stage !== 'complete' && !item.stage.endsWith('_failed'));
        const badge = document.getElementById('nav-queue-count');
        badge.hidden = !active.length;
        badge.textContent = String(active.length);
        badge.setAttribute('aria-label', `${active.length} 个处理任务`);
        document.getElementById('workspace-queue-count').textContent = `${current.length} 个任务`;
        document.querySelector('.queue-section').classList.toggle('has-tasks', active.length > 0);
        document.querySelectorAll('[data-flow-stage]').forEach(element => {
            const count = active.filter(item => item.stage === element.dataset.flowStage).length;
            element.classList.toggle('is-running', count > 0);
            element.title = count ? `${count} 个任务正在此阶段处理` : '处理流程阶段';
        });
    },

    syncSidebar() {
        this.sidebar.inert = this.compact.matches && !document.body.classList.contains('nav-open');
        if (!this.compact.matches) this.sidebar.removeAttribute('aria-modal');
    },

    revealSettingsNavigation() {
        if (this.settingsNav.hidden || this.sidebar.inert) return;
        const nav = this.settingsNav.closest('.header-nav');
        const current = this.settingsNav.querySelector('[aria-current="location"]');
        // 首次展开露出几项入口；重开抽屉时定位到正文所在分组。
        const target = current && current.dataset.jumpGroup !== 'access_control'
            ? current : this.settingsNav.querySelector('ul li:nth-child(3)');
        if (!target) return;
        const rail = nav.getBoundingClientRect();
        const row = target.getBoundingClientRect();
        if (row.bottom > rail.bottom - 8) nav.scrollTop += row.bottom - rail.bottom + 8;
        else if (row.top < rail.top + 8) nav.scrollTop += row.top - rail.top - 8;
    },

    openSidebar() {
        if (!this.compact.matches) return;
        this.sidebarReturnFocus = document.activeElement;
        document.body.classList.add('nav-open');
        document.getElementById('sidebar-backdrop').hidden = false;
        document.getElementById('sidebar-toggle').setAttribute('aria-expanded', 'true');
        this.sidebar.inert = false;
        this.revealSettingsNavigation();
        this.sidebar.setAttribute('role', 'dialog');
        this.sidebar.setAttribute('aria-modal', 'true');
        document.getElementById('workspace-sheet').inert = true;
        document.querySelector('.workbench-topbar').inert = true;
        document.getElementById('sidebar-close').focus({ preventScroll: true });
        requestAnimationFrame(() => App.updateNavIndicator(document.querySelector('.nav-btn.active')));
    },

    closeSidebar(restoreFocus = true) {
        const wasOpen = document.body.classList.contains('nav-open');
        document.body.classList.remove('nav-open');
        document.getElementById('sidebar-backdrop').hidden = true;
        document.getElementById('sidebar-toggle').setAttribute('aria-expanded', 'false');
        this.sidebar.removeAttribute('role');
        this.sidebar.removeAttribute('aria-modal');
        document.getElementById('workspace-sheet').inert = false;
        document.querySelector('.workbench-topbar').inert = false;
        this.sidebar.inert = this.compact.matches;
        if (wasOpen && restoreFocus) (this.sidebarReturnFocus || document.getElementById('sidebar-toggle')).focus({ preventScroll: true });
    },

    openCommand() {
        if (typeof Auth !== 'undefined' && Auth.locked) return;
        if (document.querySelector('dialog[open],.rule-modal-overlay.active,#drawer-overlay.active,.runtime-detail-drawer.open,.runtime-help-backdrop.open')) return;
        this.commandReturnFocus = document.activeElement;
        this.closeSidebar(false);
        if (this.commandReturnFocus?.closest('[inert]')) this.commandReturnFocus = document.getElementById('sidebar-toggle');
        this.commandInput.value = '';
        this.renderCommands();
        this.dialog.showModal();
        this.commandInput.focus();
    },

    renderCommands() {
        const query = this.commandInput.value.trim().toLocaleLowerCase();
        this.matches = this.commands.filter(command => `${command.title} ${command.group}`.toLocaleLowerCase().includes(query));
        this.commandIndex = 0;
        const results = document.getElementById('workspace-command-results');
        results.innerHTML = this.matches.length ? this.matches.map((command, index) => `<button id="workspace-command-${index}" type="button" class="command-item${index === 0 ? ' is-active' : ''}" data-command-index="${index}"><i data-lucide="${command.icon}"></i><span>${command.title}</span><small>${command.group}</small></button>`).join('') : '<p class="command-empty">未找到相关功能，试试“文件”“规则”或“日志”。</p>';
        if (typeof lucide !== 'undefined') lucide.createIcons({ root: results });
    },

    commandKeydown(event) {
        if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key) || !this.matches.length) return;
        event.preventDefault();
        if (event.key === 'Enter') { this.runCommand(this.commandIndex); return; }
        this.commandIndex = (this.commandIndex + (event.key === 'ArrowDown' ? 1 : -1) + this.matches.length) % this.matches.length;
        this.dialog.querySelectorAll('[data-command-index]').forEach((button, index) => button.classList.toggle('is-active', index === this.commandIndex));
        document.getElementById(`workspace-command-${this.commandIndex}`).scrollIntoView({ block: 'nearest' });
    },

    runCommand(index) {
        const command = this.matches[index];
        if (!command) return;
        this.commandReturnFocus = null;
        this.dialog.close();
        command.action();
        if (!document.querySelector('dialog[open],.rule-modal-overlay.active')) document.getElementById('workspace-title').focus({ preventScroll: true });
    },

    keydown(event) {
        if (typeof Auth !== 'undefined' && Auth.locked) return;
        // Escape 优先关闭正在操作的下拉菜单，再处理其所在浮层。
        if (event.key === 'Escape' && typeof CustomSelect !== 'undefined' && CustomSelect.state.activeDropdown) {
            event.preventDefault();
            CustomSelect.close();
            return;
        }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            if (this.dialog.open) this.dialog.close(); else this.openCommand();
            return;
        }
        if (document.getElementById('drawer-overlay').classList.contains('active') && !document.querySelector('dialog[open],.rule-modal-overlay.active')) {
            if (event.key === 'Escape') { event.preventDefault(); App.closeDrawer(); return; }
            if (event.key === 'Tab') {
                const items = [...document.getElementById('detail-drawer').querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter(element => !element.disabled && element.getClientRects().length);
                const first = items[0], last = items[items.length - 1];
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
            }
            return;
        }
        if (!document.body.classList.contains('nav-open')) return;
        if (event.key === 'Escape') { event.preventDefault(); this.closeSidebar(); return; }
        if (event.key === 'Tab') {
            const items = [...this.sidebar.querySelectorAll('button,a[href],[tabindex="0"]')].filter(element => element.getClientRects().length);
            const first = items[0], last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    },
};
document.addEventListener('DOMContentLoaded', () => Workbench.init());
