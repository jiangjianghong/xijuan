const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const uiRoot = path.resolve(__dirname, '../../ui');
const outputDir = path.join(os.tmpdir(), 'wanz-runtime-monitor');
const contentTypes = {
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.woff2': 'font/woff2',
};

const server = http.createServer((request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const requested = pathname === '/' ? '/index.html' : pathname;
    const filename = path.resolve(uiRoot, `.${requested}`);
    if (!filename.startsWith(`${uiRoot}${path.sep}`) || !fs.existsSync(filename)) {
        response.writeHead(404).end('not found');
        return;
    }
    response.writeHead(200, {
        'content-type': contentTypes[path.extname(filename)] || 'application/octet-stream',
    });
    fs.createReadStream(filename).pipe(response);
});

const globalPool = (id, label, group, limit, active, queued, constraints = []) => ({
    id, label, group, scope: 'global', limit, active, queued,
    completed: 12, wait_p95_ms: 18, status: queued ? 'pressure' : 'normal',
    constraints, tasks: [{ task_id: `${id}-task`, stage: 'analyzing' }],
});
const taskPool = (id, label, limit, active, queued, constraints) => ({
    id, label, group: '文件内任务', scope: 'task', per_instance_limit: limit,
    instance_count: 1, busiest_active: active, aggregate_active: active,
    aggregate_queued: queued, status: queued ? 'pressure' : 'normal', constraints,
    instances: [{ instance_id: 'file-1', active, queued, limit }],
});
const pools = [
    globalPool('global_llm', '文本 LLM', '模型通道', 16, 7, 0),
    globalPool('global_embedding', 'Embedding', '模型通道', 4, 2, 0),
    globalPool('global_vl', 'VL 视觉', '模型通道', 8, 3, 0),
    globalPool('global_table_validation', '表名校验', '业务阶段', 10, 4, 0, ['global_llm']),
    globalPool('global_extraction', '字段抽取', '业务阶段', 8, 5, 1, ['global_llm', 'global_embedding', 'global_vl']),
    globalPool('global_analysis', '逻辑分析总池', '业务阶段', 8, 6, 1, ['global_llm']),
    taskPool('task_table_validation', '文件内表名校验', 4, 3, 0, ['global_table_validation', 'global_llm']),
    taskPool('task_extraction', '文件内字段抽取', 4, 1, 0, ['global_extraction']),
    taskPool('task_file_analysis', '文件内逻辑分析', 4, 4, 1, ['global_analysis']),
    globalPool('independent_analysis', '独立分析', '独立接口', 4, 2, 1, ['global_analysis']),
    {
        id: 'global_pipeline', label: '文件管线', group: '管线', scope: 'global',
        limit: 4, active: 0, queued: 0, completed: 0, wait_p95_ms: 0,
        status: 'offline', connected: false, constraints: [], tasks: [],
    },
];
const connectedGlobals = pools.filter(pool => pool.scope === 'global' && pool.connected !== false);
const snapshot = {
    updated_at: '2026-08-18T12:00:00+08:00',
    scope: 'single-process',
    summary: {
        active: connectedGlobals.reduce((sum, pool) => sum + pool.active, 0),
        capacity: connectedGlobals.reduce((sum, pool) => sum + pool.limit, 0),
        queued: connectedGlobals.reduce((sum, pool) => sum + pool.queued, 0),
        hot_pools: connectedGlobals.filter(pool => pool.status === 'pressure').length,
        wait_p95_ms: 18,
    },
    pools,
    events: [{
        pool_id: 'global_analysis', type: 'queued', at: 1787025600,
        wait_ms: 18, context: { file_id: 'file-1', rule_id: 'rule-1' },
    }],
};

async function assertNoHorizontalOverflow(page) {
    const metrics = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        document: document.documentElement.scrollWidth,
        matrixClient: document.querySelector('.runtime-design-matrix-shell').clientWidth,
        matrixScroll: document.querySelector('.runtime-design-matrix-shell').scrollWidth,
    }));
    assert.ok(metrics.document <= metrics.viewport + 1, JSON.stringify(metrics));
    assert.ok(metrics.matrixScroll <= metrics.matrixClient + 1, JSON.stringify(metrics));
}

async function assertPoolCanvasRendered(page) {
    const canvas = page.locator('#runtime-pool-chart canvas').first();
    const box = await canvas.boundingBox();
    assert.ok(box && box.width > 100 && box.height > 100, JSON.stringify(box));
    const uniquePixels = await canvas.evaluate(element => {
        const context = element.getContext('2d');
        const pixels = new Set();
        for (let y = 0; y < element.height; y += Math.max(1, Math.floor(element.height / 20))) {
            for (let x = 0; x < element.width; x += Math.max(1, Math.floor(element.width / 30))) {
                pixels.add(Array.from(context.getImageData(x, y, 1, 1).data).join(','));
            }
        }
        return pixels.size;
    });
    assert.ok(uniquePixels > 2, `pool chart only contains ${uniquePixels} sampled colors`);
}

async function main() {
    fs.mkdirSync(outputDir, { recursive: true });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    const baseUrl = `http://127.0.0.1:${port}`;
    const browser = await chromium.launch({ headless: true });
    try {
        for (const viewport of [
            { width: 1440, height: 900 },
            { width: 1280, height: 720 },
            { width: 500, height: 900 },
        ]) {
            const page = await browser.newPage({ viewport });
            await page.route('**/runtime/concurrency', route => route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ code: 200, message: 'success', data: snapshot }),
            }));
            await page.route('**/file/list*', route => route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    code: 200,
                    message: 'success',
                    data: { items: [], total: 0, page: 1, page_size: 20 },
                }),
            }));
            await page.route('**/file/processing*', route => route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ code: 200, message: 'success', data: [] }),
            }));
            for (const endpoint of ['**/doctype/list*', '**/doctype/projects*', '**/log/files*']) {
                await page.route(endpoint, route => route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify({ code: 200, message: 'success', data: [] }),
                }));
            }
            await page.goto(`${baseUrl}/index.html?page=runtime-monitor`);
            await page.waitForSelector('#runtime-pool-chart canvas');
            await assertNoHorizontalOverflow(page);
            await assertPoolCanvasRendered(page);

            await page.click('#runtime-help-open');
            await page.waitForSelector('#runtime-help-dialog[aria-hidden="false"]');
            await page.waitForFunction(() => document.activeElement?.id === 'runtime-help-close');
            assert.equal(await page.evaluate(() => document.activeElement.id), 'runtime-help-close');
            const dialogBox = await page.locator('#runtime-help-dialog').boundingBox();
            assert.ok(dialogBox && dialogBox.x >= 0 && dialogBox.y >= 0, JSON.stringify(dialogBox));
            assert.ok(dialogBox.x + dialogBox.width <= viewport.width + 1, JSON.stringify(dialogBox));
            assert.ok(dialogBox.y + dialogBox.height <= viewport.height + 1, JSON.stringify(dialogBox));
            await page.keyboard.press('Escape');
            assert.equal(await page.evaluate(() => document.activeElement.id), 'runtime-help-open');

            await page.click('#runtime-help-open');
            await page.click('#runtime-help-backdrop', { position: { x: 4, y: 4 } });
            assert.equal(await page.getAttribute('#runtime-help-dialog', 'aria-hidden'), 'true');

            await page.click('#runtime-help-open');
            await page.evaluate(() => App.switchPage('file-processing'));
            assert.equal(await page.getAttribute('#runtime-help-dialog', 'aria-hidden'), 'true');
            assert.equal(
                await page.evaluate(() => document.body.classList.contains('runtime-modal-open')),
                false,
            );

            await page.evaluate(() => App.switchPage('runtime-monitor'));
            await page.waitForTimeout(300);
            await assertNoHorizontalOverflow(page);
            await assertPoolCanvasRendered(page);
            await page.screenshot({
                path: path.join(outputDir, `runtime-${viewport.width}x${viewport.height}.png`),
                fullPage: true,
            });
            await page.close();
        }
    } finally {
        await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
