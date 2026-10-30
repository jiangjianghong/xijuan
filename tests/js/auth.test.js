const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const source = name => fs.readFileSync(path.join(root, 'ui/js', name), 'utf8');

function authContext(pathname = '/ui/') {
    const requests = [];
    const redirects = [];
    const timers = [];
    const classes = new Set();
    const key = { value: 'xj_once_only' };
    const dialogs = Array.from({ length: 3 }, () => ({ open: true, close() { this.open = false; } }));
    const context = {
        URL, Headers, Request, console,
        location: { origin: 'http://test', href: 'http://test' + pathname, pathname, replace: value => redirects.push(value) },
        document: {
            body: { classList: { add: value => classes.add(value), remove: value => classes.delete(value) } },
            querySelectorAll: () => dialogs.filter(dialog => dialog.open),
            getElementById: id => id === 'access-key-value' ? key : { classList: { add() {}, remove() {} }, focus() {} },
            addEventListener() {},
        },
        window: { addEventListener() {}, fetch: async (...args) => { requests.push(args); return { status: 200, ok: true }; } },
        setInterval: callback => timers.push(callback),
    };
    vm.createContext(context);
    vm.runInContext(source('auth.js') + '\nthis.Auth = Auth;', context);
    context.fetch = context.window.fetch;
    return { context, requests, dialogs, key, classes, redirects, timers };
}

test('同源写请求附加安全头，读取和跨域请求保持原样', async () => {
    const { context, requests } = authContext();
    await context.window.fetch('/file/parse', { method: 'POST', body: 'bytes' });
    assert.equal(requests[0][1].headers.get('X-Requested-With'), 'XMLHttpRequest');
    assert.equal(requests[0][1].body, 'bytes');
    await context.window.fetch('/file/list');
    assert.equal(requests[1][1].headers.has('X-Requested-With'), false);
    const external = { method: 'POST', body: 'external' };
    await context.window.fetch('https://other.invalid/test', external);
    assert.equal(requests[2][1], external);
});

test('过期锁定关闭全部原生弹窗并清除密钥，重新登录恢复界面', () => {
    const { context, dialogs, key, classes } = authContext();
    context.Auth.lock();
    assert.ok(dialogs.every(dialog => !dialog.open));
    assert.equal(key.value, '');
    assert.ok(classes.has('auth-locked'));
    context.Auth.unlock();
    assert.equal(context.Auth.locked, false);
    assert.equal(classes.has('auth-locked'), false);
});

test('无扩展名登录页不启动会话轮询，退出登录跳转到统一入口', async () => {
    const login = authContext('/ui/login');
    await login.context.Auth.check();
    login.context.Auth.lock();
    assert.equal(login.requests.length, 0);
    assert.equal(login.timers.length, 0);
    assert.equal(login.context.Auth.locked, false);
    const workspace = authContext();
    await workspace.context.Auth.logout();
    assert.deepEqual(workspace.redirects, ['/ui/login']);
});

function accessContext() {
    const payload = { version: 'old', api_keys: [], ip_allowlist: [] };
    const buttons = [{ disabled: false }];
    const tabs = ['keys', 'allowlist'].map(tab => ({
        dataset: { accessTab: tab }, setAttribute(name, value) { this[name] = value; },
    }));
    const inputs = Object.fromEntries(['name', 'expiry', 'network'].map(name => [name, {
        value: '', valid: true, reportValidity() { return this.valid; },
    }]));
    const controls = {
        querySelectorAll: () => Object.values(inputs),
        querySelector: selector => inputs[selector.match(/name="(\w+)"/)[1]],
    };
    const rootElement = {
        isConnected: true,
        querySelectorAll: selector => selector === 'button' ? buttons : tabs,
        querySelector: () => controls,
    };
    const overlay = { active: true, classList: {
        contains: () => overlay.active, remove: () => { overlay.active = false; },
    } };
    const elements = {
        'access-settings': rootElement, 'settings-modal-overlay': overlay,
        'access-body': { innerHTML: '', textContent: '' }, 'access-error': { textContent: '' },
        'access-key-panel': { hidden: true }, 'access-copy-status': { textContent: '' },
        'access-key-value': { value: '', focus() {}, select() {} },
    };
    const context = {
        document: { getElementById: id => elements[id] || null },
        Auth: { locked: false }, Utils: { escapeHtml: String },
        API: { request: async () => ({ data: payload }) },
    };
    vm.createContext(context);
    vm.runInContext(source('accessControl.js') + '\nthis.AccessControl = AccessControl;', context);
    return { context, access: context.AccessControl, elements, inputs, tabs, buttons };
}

test('访问控制嵌入设置分组，密钥在分组内展示，关闭设置清除明文', async () => {
    const { context, access, elements } = accessContext();
    const html = fs.readFileSync(path.join(root, 'ui/index.html'), 'utf8');
    elements['access-settings-template'] = { innerHTML: html.match(/<template id="access-settings-template">([\s\S]*?)<\/template>/)[1] };
    vm.runInContext(source('settings.js') + '\nthis.SettingsManager = SettingsManager;', context);
    context.SettingsManager.payload = { config: {} };
    const section = context.SettingsManager.renderGroup(context.SettingsManager.GROUPS[0]);
    assert.ok(section.includes('data-settings-group="access_control"'));
    assert.ok(section.includes('id="access-settings"'));
    assert.equal(/<dialog|<form\b/.test(section), false);
    await access.mount();
    assert.ok(elements['access-body'].innerHTML.includes('创建 API Key'));
    assert.equal(/<form\b|type="submit"/.test(elements['access-body'].innerHTML), false);
    assert.equal((elements['access-body'].innerHTML.match(/<input form=""/g) || []).length, 2);
    context.API.request = async () => ({ data: { key: 'xj_once_only', version: 'new', api_keys: [], ip_allowlist: [] } });
    await access.mutate('/keys', 'POST', { name: 'test' });
    assert.equal(elements['access-key-panel'].hidden, false);
    assert.equal(elements['access-key-value'].value, 'xj_once_only');
    assert.equal(access.payload.key, undefined);
    context.SettingsManager.setDirty = () => {};
    context.SettingsManager.close();
    assert.equal(elements['access-key-value'].value, '');
    assert.equal(elements['access-key-panel'].hidden, true);
    assert.equal(access.payload, null);
});

for (const action of ['过期锁定', '关闭后重新打开']) {
    test(`${action}后迟到的创建响应不能泄露密钥或覆盖新列表`, async () => {
        const { context, access, elements, buttons } = accessContext();
        await access.mount();
        let resolve;
        context.API.request = () => new Promise(done => { resolve = done; });
        const pending = access.mutate('/keys', 'POST', { name: 'test' });
        assert.equal(buttons[0].disabled, true);
        access.unmount();
        if (action === '过期锁定') context.Auth.locked = true;
        else {
            context.API.request = async () => ({ data: { version: 'reopened', api_keys: [], ip_allowlist: [] } });
            await access.mount();
            assert.equal(buttons[0].disabled, false);
        }
        resolve({ data: { key: 'xj_delayed_secret', version: 'stale', api_keys: [], ip_allowlist: [] } });
        await pending;
        assert.equal(elements['access-key-value'].value, '');
        assert.equal(elements['access-key-panel'].hidden, true);
        assert.equal(access.payload?.version, action === '过期锁定' ? undefined : 'reopened');
        assert.equal(access.busy, false);
    });
}

test('新一轮设置加载不会被关闭前的迟到列表覆盖', async () => {
    const { context, access } = accessContext();
    let resolve;
    context.API.request = () => new Promise(done => { resolve = done; });
    const oldLoad = access.mount();
    access.unmount();
    context.API.request = async () => ({ data: { version: 'fresh', api_keys: [], ip_allowlist: [] } });
    await access.mount();
    resolve({ data: { version: 'stale', api_keys: [], ip_allowlist: [] } });
    await oldLoad;
    assert.equal(access.payload.version, 'fresh');
});

test('创建只校验当前分组，回车不提交外层设置且不打断中文输入', async () => {
    const { access, inputs, tabs } = accessContext();
    await access.mount();
    const calls = [];
    access.mutate = async (...args) => calls.push(args);
    inputs.name.valid = false;
    await access.create();
    assert.equal(calls.length, 0);
    inputs.name.valid = true;
    inputs.name.value = ' 内网系统 ';
    inputs.network.value = '192.168.1.2';
    access.select('allowlist');
    await access.create();
    assert.equal(calls[0][0], '/allowlist');
    assert.equal(calls[0][2].name, '内网系统');
    assert.equal(calls[0][2].network, '192.168.1.2');
    assert.equal(tabs[1]['aria-pressed'], 'true');
    let prevented = false;
    let created = false;
    access.create = () => { created = true; };
    const event = { key: 'Enter', target: { tagName: 'INPUT' }, preventDefault() { prevented = true; } };
    access.handleCreateKeydown({ ...event, isComposing: true });
    assert.equal(created, false);
    access.handleCreateKeydown(event);
    assert.equal(prevented, true);
    assert.equal(created, true);
});

test('低权限上传者的文件名只渲染成文本，不产生标签或逃逸 title 属性', () => {
    const context = {
        document: { addEventListener() {} }, window: { addEventListener() {} },
        console, setInterval() {}, clearInterval() {}, setTimeout() {},
    };
    vm.createContext(context);
    vm.runInContext(source('utils.js') + '\n' + source('app.js') + '\nthis.App = App; this.Utils = Utils;', context);
    context.App.els.fileListBody = { innerHTML: '' };
    context.App.updateBatchDeleteBtn = () => {};
    const name = '\"><img src=x onerror=window.hacked=1>.pdf';
    context.App.renderFileList({ items: [{ file_id: 'abc', file_name: name, file_size: 5, progress: 'complete' }] });
    const html = context.App.els.fileListBody.innerHTML;
    assert.equal(html.includes('<img'), false);
    assert.ok(html.includes('&quot;&gt;&lt;img'));
    assert.equal(context.Utils.escapeHtml("'\"&<>"), '&#39;&quot;&amp;&lt;&gt;');
});
