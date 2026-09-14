// 在真实 DOM 中验证组合表单，避免只检查 HTML 字符串。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../../ui/js/ruleConfig.js'), 'utf8');

function setup(items, strategy = 'fallback') {
    const dom = new JSDOM('<div id="editor"></div>', { runScripts: 'dangerously', url: 'http://localhost/' });
    const w = dom.window;
    w.Utils = { escapeHtml: v => String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;') };
    const errors = [];
    w.Toast = { error: v => errors.push(v), success: () => {} };
    w.TypeParams = { btnHtml: () => '', display: s => s };
    w.fetch = async () => ({ json: async () => ({ data: {} }) });
    w.eval(source + '\nwindow.rc = window.RuleConfig = RuleConfig;');
    const rc = w.rc;
    rc.PROMPT_DEFAULTS = {};
    w.document.getElementById('editor').innerHTML = rc.buildSearchConfigFields('hybrid', { strategy, items });
    const rows = () => [...w.document.querySelectorAll('.hybrid-item')];
    const data = () => JSON.parse(JSON.stringify(rc.collectSearchConfig('hybrid')));
    return { w, rc, rows, data, errors };
}
const item = (id, source_type, method, config) => ({ id, source_type, method, config });
const control = (row, id) => row.querySelector(`[id$="${id}"]`);
const change = (w, el, value) => { el.value = value; el.dispatchEvent(new w.Event('change', { bubbles: true })); };

test('文本、表格、三种 VL 复用普通配置控件，不提供 JSON 或复制', () => {
    const { rows } = setup([
        item('t', 'text', 'vector_db', { query_text: '总投资', top_k: 6 }),
        item('b', 'table', 'table_match', { table_match_keywords: ['投资表'], table_match_type: 'llm', table_match_prompt: '{table_list} 自定义' }),
        item('v', 'vl', 'vl_locate', { page_range: '2-7', field_hints: '投资', vl_extract_prompt: 'reason value' }),
    ]);
    assert.equal(control(rows()[0], 'fm-sc-query-text')?.value, '总投资');
    assert.equal(control(rows()[1], 'fm-table-match-prompt')?.value, '{table_list} 自定义');
    assert.equal(control(rows()[2], 'fm-vl-field-hints')?.value, '投资');
    assert.equal(control(rows()[2], 'fm-vl-extract-prompt')?.value, 'reason value');
    for (const row of rows()) {
        assert.equal(row.querySelector('.hybrid-config'), null);
        assert.equal([...row.querySelectorAll('button')].some(b => b.textContent === '复制'), false);
        assert.equal(row.querySelector('.hybrid-method').tagName, 'SELECT');
    }
});

test('新增和排序保留原节点、未添加的关键词、提示词和数值草稿', () => {
    const { rc, rows, data } = setup([
        item('a', 'text', 'context', { keywords: ['金额'] }),
        item('b', 'vl', 'vl_progressive', { vl_extract_prompt: 'reason value', batch_size: 4 }),
    ]);
    const [a, b] = rows();
    control(a, 'fm-sc-keywords').querySelector('input').value = '尚未回车的关键词';
    control(a, 'fm-sc-context-before').value = '321';
    control(b, 'fm-vl-extract-prompt').value = 'reason value 新草稿';
    rc.addHybridItem();
    assert.equal(rows()[0], a);
    assert.equal(rows()[1], b);
    rc.moveHybridItem(0, 1);
    assert.equal(rows()[1], a);
    assert.equal(control(a, 'fm-sc-keywords').querySelector('input').value, '尚未回车的关键词');
    assert.equal(data().items[1].config.context_before, 321);
    assert.equal(data().items[0].config.vl_extract_prompt, 'reason value 新草稿');
});

test('每项可独立切换检索方法并恢复该方法的未保存草稿', () => {
    const { w, rows, data } = setup([
        item('a', 'text', 'context', { keywords: ['甲'] }),
        item('b', 'text', 'context', { keywords: ['乙'] }),
    ]);
    const [a, b] = rows();
    control(a, 'fm-sc-context-before').value = '123';
    change(w, a.querySelector('.hybrid-method'), 'vector_db');
    control(a, 'fm-sc-query-text').value = '语义查询';
    change(w, a.querySelector('.hybrid-method'), 'context');
    assert.equal(control(a, 'fm-sc-context-before').value, '123');
    assert.deepEqual(data().items[1].config.keywords, ['乙']);
    assert.equal(rows()[1], b);
    change(w, a.querySelector('.hybrid-method'), 'vector_db');
    assert.equal(control(a, 'fm-sc-query-text').value, '语义查询');
});

test('切换来源时只变更当前项，VL 配置和表格草稿可恢复', () => {
    const { w, rows, data } = setup([item('a', 'table', 'table_match', { table_match_keywords: ['投资表'] })]);
    const row = rows()[0];
    change(w, row.querySelector('.hybrid-source'), 'vl');
    control(row, 'fm-vl-extract-prompt').value = '视觉 reason value';
    change(w, row.querySelector('.hybrid-method'), 'vl_model');
    control(row, 'fm-vl-page-from').value = '3';
    change(w, row.querySelector('.hybrid-source'), 'table');
    assert.deepEqual(data().items[0].config.table_match_keywords, ['投资表']);
    change(w, row.querySelector('.hybrid-source'), 'vl');
    assert.equal(row.querySelector('.hybrid-method').value, 'vl_model');
    assert.equal(control(row, 'fm-vl-page-from').value, '3');
});

test('含 VL 时禁止切换 union，不偷偷改写 VL 配置', () => {
    const { w, rows, data, errors } = setup([item('v', 'vl', 'vl_locate', { field_hints: '原配置', vl_extract_prompt: 'reason value' })]);
    const row = rows()[0];
    const before = data();
    change(w, w.document.getElementById('fm-hybrid-strategy'), 'union');
    assert.deepEqual(data(), before);
    assert.equal(rows()[0], row);
    assert.equal(errors.length, 1);
});

test('拖动与删除只移动或删除指定项，重新打开保持配置和顺序', () => {
    const { rc, rows, data } = setup([
        item('a', 'text', 'page', { page_range: '3-5', max_length: 888 }),
        item('b', 'table', 'table_match', { table_match_keywords: ['表格'] }),
        item('c', 'text', 'rule', { keywords: ['规则'], stop_words: ['结束'] }),
    ], 'union');
    const originals = rows();
    rc.onHybridDrop({ preventDefault() {}, currentTarget: originals[0], dataTransfer: { getData: () => '2' } });
    assert.deepEqual(data().items.map(x => x.id), ['c', 'a', 'b']);
    assert.equal(rows()[1], originals[0]);
    rc.removeHybridItem(2);
    const saved = data();
    const reopened = setup(saved.items, saved.strategy);
    assert.deepEqual(reopened.data(), saved);
});

test('匹配方式和关键词事件只作用于所属项，ID 全部唯一', async () => {
    const { w, rows } = setup([
        item('a', 'text', 'section', { section_pattern: '甲' }),
        item('b', 'text', 'section', { section_pattern: '乙' }),
        item('c', 'text', 'chunk_db', { keywords: ['已有'] }),
    ]);
    change(w, control(rows()[0], 'fm-sc-section-match-type'), 'fuzzy');
    assert.equal(control(rows()[0], 'fm-sc-section-threshold-group').style.display, '');
    assert.equal(control(rows()[1], 'fm-sc-section-threshold-group').style.display, 'none');
    const input = control(rows()[2], 'fm-sc-keywords').querySelector('input');
    input.value = '新词';
    input.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    assert.equal(control(rows()[2], 'fm-sc-keywords').querySelectorAll('.keyword-tag').length, 2);
    const ids = [...w.document.querySelectorAll('[id]')].map(x => x.id);
    assert.equal(new Set(ids).size, ids.length);
});
