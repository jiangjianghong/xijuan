// 在真实 DOM 中验证组合表单，避免只检查 HTML 字符串。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../../ui/js/ruleConfig.js'), 'utf8');

function setup(items, strategy = 'fallback', advanced = false) {
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
    rc.state.formIsAdvanced = advanced;
    w.document.getElementById('editor').innerHTML = rc.buildSearchConfigFields('hybrid', { strategy, items });
    const rows = () => [...w.document.querySelectorAll('.hybrid-item')];
    const data = () => JSON.parse(JSON.stringify(rc.collectSearchConfig('hybrid')));
    return { w, rc, rows, data, errors };
}
const item = (id, source_type, method, config) => ({ id, source_type, method, config });
const control = (row, id) => row.querySelector(`[id$="${id}"]`);
const change = (w, el, value) => { el.value = value; el.dispatchEvent(new w.Event('change', { bubbles: true })); };

test('字段空值重试开关、次数可保存并在重新打开时恢复', () => {
    const { w, rc } = setup([]);
    const config = {field_id: 'retry_field', field_name: '字段', source_type: 'text', search_type: 'context', use_llm: 0};
    const editor = w.document.getElementById('editor');
    editor.innerHTML = rc.buildFieldForm(config);
    const toggle = w.document.getElementById('fm-empty-retry-enabled');
    const count = w.document.getElementById('fm-empty-retry-count');
    assert.equal(toggle.checked, false);
    assert.equal(count.disabled, true);
    toggle.click();
    assert.equal(count.disabled, false);
    count.value = '3';
    const data = rc.collectFieldFormData();
    assert.equal(data.empty_retry_enabled, true);
    assert.equal(data.empty_retry_count, 3);
    editor.innerHTML = rc.buildFieldForm(data);
    assert.equal(w.document.getElementById('fm-empty-retry-enabled').checked, true);
    assert.equal(w.document.getElementById('fm-empty-retry-count').value, '3');
});

for (const strategy of ['union', 'fallback']) {
    test(`组合检索 ${strategy} 的占位符菜单始终提供固定标签并在光标处插入`, () => {
        const { w, rc } = setup([]);
        w.document.getElementById('editor').innerHTML = rc.buildFieldForm({
            field_id: 'tag_test', source_type: 'text', search_type: 'hybrid',
            search_config: { strategy, items: [item('a', 'text', 'context', {})] },
        });
        const textarea = w.document.getElementById('fm-text-extract-prompt');
        const button = textarea.closest('.form-group').querySelector('.insert-tag-btn');
        textarea.value = '前文待替换后文';
        textarea.setSelectionRange(2, 5);
        button.click();
        const choices = [...w.document.querySelectorAll('#_insert-tag-dropdown .dropdown-item')];
        assert.deepEqual(choices.map(el => el.textContent), ['混合检索结果']);
        choices[0].click();
        assert.equal(textarea.value, '前文<search_result>混合检索结果</search_result>后文');
        assert.equal(w.document.getElementById('_insert-tag-dropdown'), null);

        // 增加子通道及关键词不会变成多个占位符标签。
        rc.addHybridItem();
        const row = w.document.querySelector('.hybrid-item');
        const input = control(row, 'fm-sc-keywords').querySelector('input');
        input.value = '投资金额';
        input.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        button.click();
        assert.deepEqual([...w.document.querySelectorAll('#_insert-tag-dropdown .dropdown-item')]
            .map(el => el.textContent), ['混合检索结果']);
        w.close();
    });
}

test('普通文本与组合检索切换后占位符菜单使用当前模式的标签', () => {
    const { w, rc } = setup([]);
    w.document.getElementById('editor').innerHTML = rc.buildFieldForm({
        field_id: 'tag_switch', source_type: 'text', search_type: 'context',
        search_config: { keywords: ['原关键词'] },
    });
    const button = w.document.getElementById('fm-text-extract-prompt')
        .closest('.form-group').querySelector('.insert-tag-btn');
    for (const [source, expected] of [['hybrid', '混合检索结果'], ['text', '原关键词']]) {
        change(w, w.document.getElementById('fm-source-type'), source);
        button.click();
        assert.deepEqual([...w.document.querySelectorAll('#_insert-tag-dropdown .dropdown-item')]
            .map(el => el.textContent), [expected]);
    }
    w.close();
});

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

test('保存后的 hybrid 字段重新打开时来源仍显示组合检索并有配置操作', () => {
    const { w, rc } = setup([]);
    const html = rc.buildFieldForm({
        field_id: 'f1', field_name: '金额', source_type: 'text', search_type: 'hybrid',
        search_config: { strategy: 'union', items: [item('a', 'text', 'context', { keywords: ['金额'] })] },
        text_extract_prompt: '<search_result>混合检索结果</search_result>',
    });
    w.document.getElementById('editor').innerHTML = html;
    const source = w.document.getElementById('fm-source-type');
    assert.equal(source.value, 'hybrid');
    assert.match(w.document.getElementById('fm-hybrid-tabs').textContent, /配置 1/);
    assert.match(w.document.querySelector('#fm-search-config-area').textContent, /添加配置/);
});

test('保存后的多个 hybrid 配置重新打开不会退回单个文本配置', () => {
    const { w, rc } = setup([]);
    const field = {
        field_id: 'f2', field_name: '金额', source_type: 'text', search_type: 'hybrid',
        search_config: { strategy: 'fallback', items: [
            item('a', 'text', 'context', { keywords: ['甲'] }),
            item('b', 'text', 'vector_db', { query_text: '乙', top_k: 3 }),
            item('c', 'table', 'table_match', { table_match_keywords: ['表'] }),
        ] },
    };
    const html = rc.buildFieldForm(field);
    w.document.getElementById('editor').innerHTML = html;
    // 模拟 openFieldForm 的动态区域初始化
    rc.state.editingField = field;
    rc.onSourceTypeChange('hybrid');
    rc.onSearchTypeChange('hybrid');
    assert.equal(w.document.querySelectorAll('#fm-hybrid-items > .hybrid-item').length, 3);
    assert.match(w.document.getElementById('fm-hybrid-tabs').textContent, /配置 1/);
    assert.match(w.document.getElementById('fm-hybrid-tabs').textContent, /配置 2/);
    assert.match(w.document.getElementById('fm-hybrid-tabs').textContent, /配置 3/);
});

test('从普通文本切换组合检索后可连续添加配置并显示提示词', () => {
    const { w, rc } = setup([]);
    const html = rc.buildFieldForm({ field_id: 'f3', field_name: '字段', source_type: 'text', search_type: 'context', search_config: {} });
    w.document.getElementById('editor').innerHTML = html;
    rc.state.editingField = null;
    rc.onSourceTypeChange('hybrid');
    rc.addHybridItem();
    rc.addHybridItem();
    assert.equal(w.document.querySelectorAll('#fm-hybrid-items > .hybrid-item').length, 3);
    assert.equal(w.document.getElementById('fm-text-system-prompt').closest('#fm-text-prompt-wrap').style.display, '');
    assert.ok(w.document.getElementById('fm-text-extract-prompt'));
    assert.equal(w.document.querySelector('.hybrid-drag-handle'), null);
    const tabs = w.document.querySelectorAll('.hybrid-tab');
    assert.equal(tabs.length, 3);
    assert.equal(tabs[0].getAttribute('draggable'), 'true');
});

test('组合检索切回文本时恢复普通方法和两边未保存草稿', () => {
    const { w, rc } = setup([]);
    w.document.getElementById('editor').innerHTML = rc.buildFieldForm({
        field_id: 'f4', field_name: '字段', source_type: 'text', search_type: 'context',
        search_config: { keywords: ['原关键词'], context_before: 100 },
    });
    rc.state.editingField = null;
    control(w.document, 'fm-sc-context-before').value = '321';
    change(w, w.document.getElementById('fm-source-type'), 'hybrid');
    const hybridRow = w.document.querySelector('.hybrid-item');
    control(hybridRow, 'fm-sc-context-before').value = '654';

    change(w, w.document.getElementById('fm-source-type'), 'text');
    assert.equal(w.document.getElementById('fm-search-type-group').style.display, '');
    assert.equal(w.document.getElementById('fm-search-type').value, 'context');
    assert.equal(control(w.document, 'fm-sc-context-before').value, '321');
    assert.equal(rc.collectFieldFormData().search_type, 'context');

    change(w, w.document.getElementById('fm-source-type'), 'hybrid');
    assert.equal(control(w.document.querySelector('.hybrid-item'), 'fm-sc-context-before').value, '654');
    rc.onSourceTypeChange('hybrid');
    assert.equal(control(w.document.querySelector('.hybrid-item'), 'fm-sc-context-before').value, '654');
});

test('组合检索显示真实 LLM 开关并按开关控制提示词和保存值', () => {
    const { w, rc } = setup([]);
    w.document.getElementById('editor').innerHTML = rc.buildFieldForm({
        field_id: 'f5', field_name: '字段', source_type: 'text', search_type: 'hybrid', use_llm: 0,
        search_config: { strategy: 'fallback', items: [item('a', 'text', 'context', {})] },
    });
    rc.state.editingField = null;
    rc.onSourceTypeChange('hybrid');
    assert.equal(w.document.getElementById('fm-use-llm-group').style.display, 'block');
    assert.equal(w.document.getElementById('fm-skip-llm').checked, true);
    assert.equal(w.document.getElementById('fm-text-prompt-wrap').style.display, 'none');
    assert.equal(rc.collectFieldFormData().use_llm, 0);

    const skip = w.document.getElementById('fm-skip-llm');
    skip.checked = false;
    rc.onSkipLlmChange(false);
    assert.equal(w.document.getElementById('fm-text-prompt-wrap').style.display, '');
    assert.equal(rc.collectFieldFormData().use_llm, 1);
});

test('嵌套 VL 配置清空可选字段后不复制旧值并保留兼容字段', () => {
    const { rows, data } = setup([item('v', 'vl', 'vl_locate', {
        vl_config: { max_pages: 9, page_source_field: 'old', locate_prompt_template: '旧模板', legacy_flag: '保留' },
        vl_extract_prompt: 'reason value',
    })], 'fallback', true);
    const row = rows()[0];
    control(row, 'fm-vl-max-pages').value = '';
    control(row, 'fm-vl-page-source').value = '';
    control(row, 'fm-vl-locate-prompt-template').value = '';
    const config = data().items[0].config;
    assert.equal(config.vl_config.max_pages, undefined);
    assert.equal(config.vl_config.page_source_field, undefined);
    assert.equal(config.vl_config.locate_prompt_template, undefined);
    assert.equal(config.vl_config.legacy_flag, '保留');
});

test('初次打开的组合标签立即具备拖动绑定', () => {
    const { w } = setup([
        item('a', 'text', 'context', {}),
        item('b', 'text', 'vector_db', {}),
    ]);
    for (const tab of w.document.querySelectorAll('.hybrid-tab')) {
        assert.equal(tab.getAttribute('draggable'), 'true');
        assert.match(tab.getAttribute('ondragstart') || '', /onHybridTabDragStart/);
        assert.match(tab.getAttribute('ondrop') || '', /onHybridTabDrop/);
    }
});

test('扁平 VL 配置清空可选字段后也不恢复旧值', () => {
    const { rows, data } = setup([item('v', 'vl', 'vl_locate', {
        max_pages: 9, page_source_field: 'old', locate_prompt_template: '旧模板',
        legacy_flag: '保留', vl_extract_prompt: 'reason value',
    })], 'fallback', true);
    const row = rows()[0];
    control(row, 'fm-vl-max-pages').value = '';
    control(row, 'fm-vl-page-source').value = '';
    control(row, 'fm-vl-locate-prompt-template').value = '';
    const config = data().items[0].config;
    assert.equal(config.max_pages, undefined);
    assert.equal(config.page_source_field, undefined);
    assert.equal(config.locate_prompt_template, undefined);
    assert.equal(config.legacy_flag, '保留');
});

test('删除当前组合标签后激活相邻项并保持一个面板可见', () => {
    const { rc, rows, w } = setup([
        item('a', 'text', 'context', {}),
        item('b', 'text', 'vector_db', {}),
        item('c', 'table', 'table_match', {}),
    ]);
    rc.showHybridItem(1);
    rc.removeHybridItem(1);
    const remaining = rows();
    assert.equal(remaining.filter(row => row.style.display !== 'none').length, 1);
    assert.equal(remaining[1].dataset.id, 'c');
    assert.equal(remaining[1].style.display, '');
    assert.equal(w.document.querySelectorAll('.hybrid-tab.active').length, 1);
    assert.equal(w.document.querySelector('.hybrid-tab.active').dataset.index, '1');
});
