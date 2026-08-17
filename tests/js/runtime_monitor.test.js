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
