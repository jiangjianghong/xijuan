const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');

function loadApp(overrides = {}) {
    const source = fs.readFileSync(path.join(root, 'ui/js/app.js'), 'utf8');
    const context = {
        document: {
            addEventListener() {},
            querySelector() { return null; },
            querySelectorAll() { return []; },
        },
        window: { addEventListener() {} },
        setInterval() { return 1; },
        clearInterval() {},
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
    return { app, context };
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

test('polling ignores both active and failed local upload cards', async () => {
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

    assert.equal(processingCalls, 0);
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
                return [{ file_id: 'file-1', file_name: 'demo.pdf', progress: 'parsing' }];
            },
            getCurrentTypeId: () => 'new-current-type',
        },
    });
    app.state.queue.set('file-1', {
        fileName: 'demo.pdf', stage: 'parsing', progress: 15, typeId: 'original-type',
    });

    await app.pollQueueStatus();

    assert.deepEqual(requestedTypes, ['original-type']);
    assert.equal(app.state.queue.has('file-1'), true);
});
