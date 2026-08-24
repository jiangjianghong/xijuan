const test = require('node:test');
const assert = require('node:assert/strict');
const RuntimeMonitor = require('../../ui/js/runtime-monitor.js');

test('normalizeSnapshot keeps global pools and drops hidden task-scoped records', () => {
    const normalized = RuntimeMonitor.normalizeSnapshot({
        updated_at: '2026-08-17T10:00:00+08:00',
        scope: 'single-process',
        summary: { active: 2, capacity: 8, queued: 1, hot_pools: 1, total_wait_p95_ms: 35 },
        pools: [
            { id: 'global_llm', label: '文本 LLM', group: '模型通道', scope: 'global', limit: 4, active: 2, queued: 1, status: 'pressure', gate_wait_p95_ms: 12, total_wait_p95_ms: 35 },
            { id: 'global_pipeline', label: '文件管线', scope: 'global', limit: 8, active: 0, queued: 0, status: 'offline', connected: false },
        ],
        events: [{ pool_id: 'global_llm', type: 'queued', at: 1, context: { file_id: 'f-1' } }],
    });

    assert.equal(normalized.scope, 'single-process');
    assert.equal(normalized.globalPools.length, 1);
    assert.equal(normalized.globalPools[0].gate_wait_p95_ms, 12);
    assert.equal(normalized.globalPools[0].total_wait_p95_ms, 35);
    assert.equal(normalized.summary.total_wait_p95_ms, 35);
    assert.equal(normalized.pipeline.status, 'offline');
    assert.equal(normalized.events[0].pool_id, 'global_llm');
    // 单文件池已从快照收起，前端不再有 taskPools 这一维
    assert.equal('taskPools' in normalized, false);
});

test('normalizeSnapshot tolerates an empty or malformed response', () => {
    const normalized = RuntimeMonitor.normalizeSnapshot(null);
    assert.deepEqual(normalized.globalPools, []);
    assert.equal(normalized.pipeline.status, 'offline');
    assert.equal(normalized.summary.total_wait_p95_ms, 0);
    assert.equal(normalized.error, false);
});

// 压力曲线自「历史改由后端下发」后不再前端累积，原 recordHistory 测试已随
// 该函数一起作废，这里改测后端 history 的透传与容错。
test('normalizeSnapshot passes backend history through and tolerates its absence', () => {
    const withHistory = RuntimeMonitor.normalizeSnapshot({
        summary: { active: 3, capacity: 10 },
        pools: [{ id: 'global_llm', scope: 'global', limit: 10, active: 3, queued: 0, status: 'normal' }],
        history: { window: '5m', points: [null, { at: 1, overall: 30, pools: { global_llm: 30 } }] },
    });
    assert.equal(withHistory.history.window, '5m');
    assert.equal(withHistory.history.points.length, 2);
    // 空桶为 null，渲染方必须容错
    assert.equal(withHistory.history.points[0], null);

    const withoutHistory = RuntimeMonitor.normalizeSnapshot({ pools: [] });
    assert.equal(withoutHistory.history, null);
});

test('orderedPools uses canonical group order regardless of API order', () => {
    const ids = [
        'independent_analysis', 'global_vl', 'global_analysis',
        'global_llm', 'global_pipeline', 'global_extraction',
        'global_embedding', 'global_table_validation',
    ];
    const snapshot = RuntimeMonitor.normalizeSnapshot({
        pools: ids.map(id => ({ id, scope: 'global', limit: 4, active: 0, queued: 0 })),
    });

    assert.deepEqual(RuntimeMonitor.orderedPools(snapshot).map(pool => pool.id), [
        'global_llm', 'global_embedding', 'global_vl',
        'global_table_validation', 'global_extraction', 'global_analysis',
        'independent_analysis', 'global_pipeline',
    ]);
});
