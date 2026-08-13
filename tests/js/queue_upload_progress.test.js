const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');

function loadApp(overrides = {}) {
    const source = fs.readFileSync(path.join(root, 'ui/js/app.js'), 'utf8');
    const windowListeners = {};
    const timers = [];
    let focused = true;
    const context = {
        document: {
            addEventListener() {},
            querySelector() { return null; },
            querySelectorAll() { return []; },
            hasFocus() { return focused; },
        },
        window: {
            addEventListener(event, callback) { windowListeners[event] = callback; },
        },
        setInterval() { return 1; },
        clearInterval() {},
        setTimeout(callback, delay) {
            const timer = { callback, delay, cleared: false };
            timers.push(timer);
            return timer;
        },
        clearTimeout(timer) {
            if (timer) timer.cleared = true;
        },
        requestAnimationFrame(callback) { callback(); },
        console,
        Utils: {
            generateId: () => 'temp-upload',
            getStageProgress: () => 15,
            getStatusText: stage => stage,
            escapeHtml: value => String(value == null ? '' : value),
            isFailed: stage => stage.endsWith('_failed'),
        },
        API: {
            uploadFileAsync: async () => ({ data: { file_id: 'file-1' } }),
            getProcessing: async () => [],
            getCurrentTypeId: () => 'default',
        },
        QueueProgress: { updateCard() {} },
        Toast: { init() {}, info() {}, success() {}, error() {} },
        RuleConfig: { init() {} },
        ...overrides,
    };
    vm.createContext(context);
    vm.runInContext(`${source}\nthis.__APP__ = App;`, context);
    const app = context.__APP__;
    app.state.queue = new Map();
    app.els = {
        fileInput: { value: 'selected' },
        queueContainer: { innerHTML: '', querySelector() { return null; } },
    };
    app.renderQueue = () => {};
    app.loadFileList = () => {};
    return {
        app,
        context,
        timers,
        windowListeners,
        setFocused(value) { focused = value; },
    };
}

test('buildPathData returns one path from the fixed first sample', () => {
    const QueueProgress = require(path.join(root, 'ui/js/queueProgress.js'));
    const samples = [
        { x: 10, y: 2 },
        { x: 20, y: 2 },
        { x: 30, y: 8 },
        { x: 30, y: 18 },
        { x: 20, y: 24 },
    ];

    assert.equal(QueueProgress.buildPathData(samples, 0), '');
    assert.equal(QueueProgress.buildPathData(samples, 0.5), 'M10 2 L20 2 L30 8');
    assert.equal(QueueProgress.buildPathData(samples, 1), 'M10 2 L20 2 L30 8 L30 18 L20 24');
    assert.equal((QueueProgress.buildPathData(samples, 1).match(/M/g) || []).length, 1);
});

test('uploadFileAsync reports real XHR upload progress and resolves JSON', async () => {
    const instances = [];

    class FakeXHR {
        constructor() {
            this.upload = {};
            instances.push(this);
        }
        open(method, url) {
            this.method = method;
            this.url = url;
        }
        send(body) {
            this.body = body;
            this.upload.onprogress({ lengthComputable: true, loaded: 25, total: 100 });
            this.status = 200;
            this.responseText = JSON.stringify({ data: { file_id: 'file-1' } });
            this.onload();
        }
    }

    class FakeFormData {
        append(name, value) {
            this.entry = [name, value];
        }
    }

    const source = fs.readFileSync(path.join(root, 'ui/js/api.js'), 'utf8');
    const context = {
        XMLHttpRequest: FakeXHR,
        FormData: FakeFormData,
        localStorage: { getItem: () => 'default', setItem: () => {} },
        URLSearchParams,
        console,
        fetch: () => { throw new Error('uploadFileAsync must not use fetch'); },
    };
    vm.createContext(context);
    vm.runInContext(`${source}\nthis.__API__ = API;`, context);

    const progress = [];
    const result = await context.__API__.uploadFileAsync(
        { name: 'demo.pdf' },
        'contract type',
        value => progress.push(value),
    );

    assert.deepEqual(progress, [25, 100]);
    assert.equal(result.data.file_id, 'file-1');
    assert.equal(instances[0].method, 'POST');
    assert.equal(instances[0].url, '/file/parse?mode=async&type_id=contract%20type');
    assert.equal(instances[0].body.entry[0], 'file');
});

test('uploadFileAsync surfaces backend detail from failed XHR response', async () => {
    class FailedXHR {
        constructor() { this.upload = {}; }
        open() {}
        send() {
            this.status = 413;
            this.responseText = JSON.stringify({ detail: '文件过大' });
            this.onload();
        }
    }

    const source = fs.readFileSync(path.join(root, 'ui/js/api.js'), 'utf8');
    const context = {
        XMLHttpRequest: FailedXHR,
        FormData: class { append() {} },
        localStorage: { getItem: () => 'default', setItem: () => {} },
        URLSearchParams,
        console,
        fetch: () => {},
    };
    vm.createContext(context);
    vm.runInContext(`${source}\nthis.__API__ = API;`, context);

    await assert.rejects(
        context.__API__.uploadFileAsync({ name: 'large.pdf' }, 'default', () => {}),
        /文件过大/,
    );
});

test('replaceQueueId keeps the upload card in its original queue position', () => {
    const { app } = loadApp();
    app.state.queue = new Map([
        ['before', { fileName: 'before.pdf' }],
        ['temp-upload', { fileName: 'upload.pdf' }],
        ['after', { fileName: 'after.pdf' }],
    ]);

    app.replaceQueueId('temp-upload', 'file-1', {
        fileName: 'upload.pdf',
        stage: 'parsing',
        progress: 15,
    });

    assert.deepEqual(Array.from(app.state.queue.keys()), ['before', 'file-1', 'after']);
    assert.equal(app.state.queue.get('file-1').stage, 'parsing');
});

test('failed upload remains in queue with its file available for retry', async () => {
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => { throw new Error('连接中断'); },
            getProcessing: async () => [],
            getCurrentTypeId: () => 'default',
        },
    });
    const file = { name: '失败.pdf' };

    await app.uploadFile(file);

    const item = app.state.queue.get('temp-upload');
    assert.equal(item.stage, 'upload_failed');
    assert.equal(item.error, '连接中断');
    assert.equal(item.file, file);
});

test('updateUploadProgress redraws only the matching upload card', () => {
    const calls = [];
    const card = {};
    const { app } = loadApp({ QueueProgress: { updateCard: (...args) => calls.push(args) } });
    app.state.queue.set('temp-upload', {
        fileName: 'demo.pdf', stage: 'uploading', uploadProgress: 0,
    });
    app.els.queueContainer.querySelector = selector => {
        assert.equal(selector, '[data-id="temp-upload"]');
        return card;
    };

    app.updateUploadProgress('temp-upload', 42.5);

    assert.equal(app.state.queue.get('temp-upload').uploadProgress, 42.5);
    assert.deepEqual(calls, [[card, 42.5]]);
});

test('polling reconciles the current type while preserving local upload cards', async () => {
    let processingCalls = 0;
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => ({}),
            getProcessing: async () => { processingCalls += 1; return []; },
            getCurrentTypeId: () => 'default',
        },
    });
    app.state.queue = new Map([
        ['active', { stage: 'uploading' }],
        ['failed', { stage: 'upload_failed' }],
    ]);

    await app.pollQueueStatus();

    assert.equal(processingCalls, 1);
    assert.equal(app.state.queue.has('active'), true);
    assert.equal(app.state.queue.has('failed'), true);
});

test('polling discovers a processing task started by another browser', async () => {
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => ({}),
            getProcessing: async typeId => {
                assert.equal(typeId, 'contract');
                return [{
                    file_id: 'remote-file', file_name: 'remote.pdf', progress: 'extracting',
                    type_id: 'contract',
                }];
            },
            getCurrentTypeId: () => 'contract',
        },
    });

    await app.pollQueueStatus();

    const item = app.state.queue.get('remote-file');
    assert.equal(item.fileName, 'remote.pdf');
    assert.equal(item.stage, 'extracting');
    assert.equal(item.progress, 15);
    assert.equal(item.typeId, 'contract');
});

test('polling ignores unknown tasks from a stale current-type response', async () => {
    let currentType = 'before';
    let resolveProcessing;
    const processing = new Promise(resolve => { resolveProcessing = resolve; });
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => ({}),
            getProcessing: async () => processing,
            getCurrentTypeId: () => currentType,
        },
    });

    const poll = app.pollQueueStatus();
    currentType = 'after';
    resolveProcessing([{
        file_id: 'stale-file', file_name: 'stale.pdf', progress: 'parsing', type_id: 'before',
    }]);
    await poll;

    assert.equal(app.state.queue.has('stale-file'), false);
});

test('restoring a new type preserves tracked pipeline tasks from the previous type', async () => {
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => ({}),
            getProcessing: async () => [],
            getCurrentTypeId: () => 'after',
        },
    });
    app.state.queue.set('old-file', {
        fileName: 'old.pdf', stage: 'embedding', progress: 60, typeId: 'before',
    });

    await app.restoreProcessingQueue();

    assert.equal(app.state.queue.has('old-file'), true);
});

test('an older restore response cannot overwrite a newer restore', async () => {
    let currentType = 'same';
    let resolveFirst;
    const firstResponse = new Promise(resolve => { resolveFirst = resolve; });
    let requestCount = 0;
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => ({}),
            getProcessing: async () => {
                requestCount += 1;
                if (requestCount === 1) return firstResponse;
                return [{ file_id: 'new-file', file_name: 'new.pdf', progress: 'extracting' }];
            },
            getCurrentTypeId: () => currentType,
        },
    });

    const first = app.restoreProcessingQueue();
    await app.restoreProcessingQueue();
    resolveFirst([{ file_id: 'old-file', file_name: 'old.pdf', progress: 'parsing' }]);
    await first;

    assert.equal(app.state.queue.has('new-file'), true);
    assert.equal(app.state.queue.has('old-file'), false);
});

test('an in-flight restore cannot remove an upload that just entered the pipeline', async () => {
    let resolveProcessing;
    const processing = new Promise(resolve => { resolveProcessing = resolve; });
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => ({}),
            getProcessing: async () => processing,
            getCurrentTypeId: () => 'default',
        },
    });
    app.state.queue.set('temp-upload', {
        fileName: 'new.pdf', stage: 'uploading', progress: 0, typeId: 'default',
    });

    const restore = app.restoreProcessingQueue();
    app.replaceQueueId('temp-upload', 'new-file', {
        fileName: 'new.pdf', stage: 'parsing', progress: 15, typeId: 'default',
    });
    resolveProcessing([]);
    await restore;

    assert.equal(app.state.queue.has('new-file'), true);
});

test('retry restarts upload progress from zero', async () => {
    let seenBeforeRequest;
    let requestedTypeId;
    const { app } = loadApp({
        API: {
            uploadFileAsync: async (file, typeId) => {
                seenBeforeRequest = app.state.queue.get('failed').uploadProgress;
                requestedTypeId = typeId;
                return { data: {} };
            },
            getProcessing: async () => [],
            getCurrentTypeId: () => 'new-current-type',
        },
    });
    const file = { name: 'retry.pdf' };
    app.state.queue.set('failed', {
        fileName: file.name,
        file,
        typeId: 'original-type',
        stage: 'upload_failed',
        uploadProgress: 63,
    });

    await app.retryUpload('failed');

    assert.equal(seenBeforeRequest, 0);
    assert.equal(requestedTypeId, 'original-type');
});

test('pipeline polling follows the upload item original document type', async () => {
    const requestedTypes = [];
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => ({}),
            getProcessing: async typeId => {
                requestedTypes.push(typeId);
                if (typeId === 'new-current-type') return [];
                return [{ file_id: 'file-1', file_name: 'demo.pdf', progress: 'parsing' }];
            },
            getCurrentTypeId: () => 'new-current-type',
        },
    });
    app.state.queue.set('file-1', {
        fileName: 'demo.pdf', stage: 'parsing', progress: 15, typeId: 'original-type',
    });

    await app.pollQueueStatus();

    assert.deepEqual(requestedTypes, ['new-current-type', 'original-type']);
    assert.equal(app.state.queue.has('file-1'), true);
});

test('polling removes a completed tracked task and refreshes the file list', async () => {
    let listRefreshes = 0;
    const successes = [];
    const { app } = loadApp({
        API: {
            uploadFileAsync: async () => ({}),
            getProcessing: async () => [],
            getFileStatus: async () => ({ progress: 'complete' }),
            getCurrentTypeId: () => 'default',
        },
        Toast: { init() {}, info() {}, success: message => successes.push(message), error() {} },
    });
    app.loadFileList = () => { listRefreshes += 1; };
    app.state.queue.set('done-file', {
        fileName: 'done.pdf', stage: 'analyzing', progress: 90, typeId: 'default',
    });

    await app.pollQueueStatus();

    assert.equal(app.state.queue.has('done-file'), false);
    assert.deepEqual(successes, ['done.pdf 处理完成']);
    assert.equal(listRefreshes, 1);
});

test('queue polling uses two seconds with focus and five seconds without focus', () => {
    const focusedHarness = loadApp();
    focusedHarness.app.startPolling();
    assert.equal(focusedHarness.timers.at(-1).delay, 2000);

    const blurredHarness = loadApp();
    blurredHarness.setFocused(false);
    blurredHarness.app.startPolling();
    assert.equal(blurredHarness.timers.at(-1).delay, 5000);
});

test('focus immediately polls and blur reschedules with the slower interval', async () => {
    let polls = 0;
    const harness = loadApp();
    harness.app.pollQueueStatus = async () => { polls += 1; };
    harness.app.startPolling();

    harness.setFocused(false);
    harness.windowListeners.blur();
    assert.equal(harness.timers.at(-1).delay, 5000);

    harness.setFocused(true);
    harness.windowListeners.focus();
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(polls, 1);
    assert.equal(harness.timers.at(-1).delay, 2000);
});

test('queue polling does not overlap slow requests', async () => {
    let polls = 0;
    let resolvePoll;
    const pending = new Promise(resolve => { resolvePoll = resolve; });
    const harness = loadApp();
    harness.app.pollQueueStatus = async () => { polls += 1; await pending; };

    const first = harness.app.runQueuePoll();
    const second = harness.app.runQueuePoll();
    assert.equal(polls, 1);

    resolvePoll();
    await Promise.all([first, second]);
    assert.equal(polls, 1);
});

test('focus during an in-flight poll queues one immediate follow-up poll', async () => {
    let polls = 0;
    let resolveFirst;
    const firstPending = new Promise(resolve => { resolveFirst = resolve; });
    const harness = loadApp();
    harness.app.pollQueueStatus = async () => {
        polls += 1;
        if (polls === 1) await firstPending;
    };
    harness.app.startPolling();

    const first = harness.app.runQueuePoll();
    harness.windowListeners.focus();
    assert.equal(polls, 1);

    resolveFirst();
    await first;
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(polls, 2);
});
