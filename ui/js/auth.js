/** 全站会话：为同源写请求附加防跨站请求头，过期时遮住后台并保留未保存表单。 */
const Auth = {
    locked: false,
    isLoginPage() {
        return ['/ui/login', '/ui/login.html'].includes(location.pathname.replace(/\/+$/, ''));
    },
    lock() {
        if (this.isLoginPage()) return;
        if (typeof AccessControl !== 'undefined') AccessControl.unmount();
        // 原生 dialog 在顶层，必须先关闭，否则会挡住重新登录表单并锁定焦点。
        const keyField = document.getElementById('access-key-value');
        if (keyField) keyField.value = '';
        document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
        if (typeof LogViewer !== 'undefined' && LogViewer.source) LogViewer.disconnect();
        if (this.locked) return;
        this.locked = true;
        document.body.classList.add('auth-locked');
        const overlay = document.getElementById('settings-login-overlay');
        overlay?.classList.add('active');
        document.getElementById('settings-password')?.focus();
    },
    unlock() {
        this.locked = false;
        document.body.classList.remove('auth-locked');
        document.getElementById('settings-login-overlay')?.classList.remove('active');
        if (typeof AccessControl !== 'undefined' && document.getElementById('settings-modal-overlay')?.classList.contains('active')) {
            AccessControl.mount();
        }
    },
    async login(event) {
        event.preventDefault();
        const form = event.target;
        const button = form.querySelector('button[type="submit"]');
        const error = form.querySelector('[role="alert"]');
        button.disabled = true;
        error.textContent = '';
        try {
            const response = await fetch('/auth/login', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: form.elements.username.value, password: form.elements.password.value }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查账号和密码');
            form.elements.password.value = '';
            location.replace('/ui/');
        } catch (failure) {
            error.textContent = failure.message || '暂时无法连接服务器';
        } finally {
            button.disabled = false;
        }
    },
    async logout() {
        try {
            const response = await fetch('/auth/logout', { method: 'POST' });
            if (!response.ok && response.status !== 401) throw new Error('退出失败，请重试');
            location.replace('/ui/login');
        } catch (error) {
            if (typeof Toast !== 'undefined') Toast.error(error.message);
        }
    },
    async check() {
        if (this.isLoginPage()) return;
        try {
            const response = await fetch('/auth/session');
            const result = await response.json();
            if (!result.data?.authenticated) this.lock();
        } catch (_) { /* 网络暂时断开时由具体业务请求提示，不清空当前表单。 */ }
    },
};

// 覆盖现有 fetch 调用；带进度的 XHR 上传在 api.js 中单独设置相同请求头。
const authFetch = window.fetch.bind(window);
window.fetch = async (resource, options = {}) => {
    const url = new URL(resource instanceof Request ? resource.url : resource, location.href);
    if (url.origin !== location.origin) return authFetch(resource, options);
    const method = (options.method || (resource instanceof Request ? resource.method : 'GET')).toUpperCase();
    const headers = new Headers(options.headers || (resource instanceof Request ? resource.headers : undefined));
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-Requested-With', 'XMLHttpRequest');
    const response = await authFetch(resource, { ...options, headers });
    if (response.status === 401 && !['/auth/login', '/settings/login'].includes(url.pathname)) Auth.lock();
    return response;
};
window.addEventListener('pageshow', () => Auth.check());
document.addEventListener('visibilitychange', () => { if (!document.hidden) Auth.check(); });
if (!Auth.isLoginPage()) setInterval(() => Auth.check(), 30000);
