const test = require('node:test');
const assert = require('node:assert/strict');
const RuntimeMonitor = require('../../ui/js/runtime-monitor.js');

test('normalizeSnapshot separates global capacity from task instance pressure', () => {
    const normalized = RuntimeMonitor.normalizeSnapshot({
        updated_at: '2026-08-17T10:00:00+08:00',
        scope: 'single-process',
        summary: { active: 2, capacity: 8, queued: 1, hot_pools: 1, wait_p95_ms: 35 },
        pools: [
            { id: 'global_llm', label: '文本 LLM', group: '模型通道', scope: 'global', limit: 4, active: 2, queued: 1, status: 'pressure' },
            { id: 'task_extraction', label: '单文件抽取', group: '单任务限制', scope: 'task', per_instance_limit: 3, instance_count: 2, busiest_active: 3, aggregate_active: 3, aggregate_queued: 1, status: 'pressure', instances: [] },
            { id: 'global_pipeline', label: '文件管线', scope: 'global', limit: 8, active: 0, queued: 0, status: 'offline', connected: false },
        ],
        events: [{ pool_id: 'global_llm', type: 'queued', at: 1, context: { file_id: 'f-1' } }],
    });

    assert.equal(normalized.scope, 'single-process');
    assert.equal(normalized.globalPools.length, 1);
    assert.equal(normalized.taskPools[0].capacity, 3);
    assert.equal(normalized.taskPools[0].active, 3);
    assert.equal(normalized.taskPools[0].queued, 1);
    assert.equal(normalized.pipeline.status, 'offline');
    assert.equal(normalized.events[0].pool_id, 'global_llm');
});

test('normalizeSnapshot tolerates an empty or malformed response', () => {
    const normalized = RuntimeMonitor.normalizeSnapshot(null);
    assert.deepEqual(normalized.globalPools, []);
    assert.deepEqual(normalized.taskPools, []);
    assert.equal(normalized.pipeline.status, 'offline');
    assert.equal(normalized.error, false);
});

test('recordHistory keeps a fixed sampling window from the first refresh', () => {
    RuntimeMonitor.state.history = [];
    RuntimeMonitor.state.poolHistory = {};

    const snapshot = RuntimeMonitor.normalizeSnapshot({
        summary: { active: 3, capacity: 10 },
        pools: [{ id: 'global_llm', scope: 'global', limit: 10, active: 3, queued: 0, status: 'normal' }],
    });

    RuntimeMonitor.recordHistory(snapshot);

    assert.equal(RuntimeMonitor.state.history.length, 60);
    assert.equal(RuntimeMonitor.state.history.at(-1), 30);
    assert.equal(RuntimeMonitor.state.history.slice(0, -1).every(value => value === null), true);
    assert.equal(RuntimeMonitor.state.poolHistory.global_llm.length, 60);
    assert.equal(RuntimeMonitor.state.poolHistory.global_llm.at(-1), 30);
});

test('orderedPools uses canonical five-group order regardless of API order', () => {
    const ids = [
        'independent_analysis', 'task_file_analysis', 'global_vl',
        'global_analysis', 'global_llm', 'global_pipeline',
        'task_extraction', 'global_extraction', 'global_embedding',
        'task_table_validation', 'global_table_validation',
    ];
    const snapshot = RuntimeMonitor.normalizeSnapshot({
        pools: ids.map(id => ({
            id,
            scope: id.startsWith('task_') ? 'task' : 'global',
            limit: 4,
            per_instance_limit: 4,
            active: 0,
            queued: 0,
        })),
    });

    assert.deepEqual(RuntimeMonitor.orderedPools(snapshot).map(pool => pool.id), [
        'global_llm', 'global_embedding', 'global_vl',
        'global_table_validation', 'global_extraction', 'global_analysis',
        'task_table_validation', 'task_extraction', 'task_file_analysis',
        'independent_analysis', 'global_pipeline',
    ]);
});
