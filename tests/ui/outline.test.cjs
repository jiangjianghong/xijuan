const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

test('大纲缩进与后端子树一致，深层和无编号子节都能体现父子关系', async () => {
    const dom = new JSDOM('<div id="content"></div>', { runScripts: 'outside-only' });
    const w = dom.window;
    const data = [
        { level: 1, numbered: true, start_pos: 0, tree_end_pos: 60 },
        { level: 3, numbered: true, start_pos: 10, tree_end_pos: 50 },
        { level: 4, numbered: true, start_pos: 20, tree_end_pos: 50 },
        { level: 5, numbered: true, start_pos: 30, tree_end_pos: 50 },
        { level: 90, numbered: false, start_pos: 40, tree_end_pos: 50 },
        { level: 3, numbered: true, start_pos: 50, tree_end_pos: 60 },
        { level: 1, numbered: true, start_pos: 60, tree_end_pos: 70 },
    ].map((item, i) => ({ ...item, title: `标题${i}`, content: `正文${i}` }));
    w.API = { getFileOutline: async () => data };
    const source = fs.readFileSync(path.join(__dirname, '../../ui/js/app.js'), 'utf8');
    w.eval(source + '\nApp.init = () => {}; window.app = App;');
    const app = w.app;
    app.escapeHtml = value => String(value ?? '');
    app.state.currentFileId = 'fixture';
    app.els.tabContent = w.document.getElementById('content');
    await app.switchTab('outline');
    const padding = [...w.document.querySelectorAll('.outline-item')].map(item => item.style.paddingLeft);
    assert.deepEqual(padding, ['8px', '24px', '40px', '56px', '72px', '24px', '8px']);
    dom.window.close();
});
