const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../../ui/js/ruleConfig.js'), 'utf8');

function setup() {
  const dom = new JSDOM('<div id="debug-sec-hybrid"><div id="debug-hybrid-content"></div></div>', { runScripts: 'dangerously' });
  const w = dom.window;
  w.Utils = { escapeHtml: v => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;') };
  w.eval(source + '\nwindow.rc = RuleConfig;');
  return { w, rc: w.rc, content: w.document.getElementById('debug-hybrid-content') };
}

test('按配置顺序渲染通道并合并状态、命中、采用和转义内容', () => {
  const { rc, content } = setup();
  rc.handleHybridDebugEvent('hybrid_start', { strategy:'union', items:[{id:'b',index:2,method:'<表格>'},{id:'a',index:1,method:'文本'}] });
  rc.handleHybridDebugEvent('hybrid_item_done', { id:'b', status:'matched', hit_count:2, adopted_count:1, preview:'<script>' });
  const cards = content.querySelectorAll('details');
  assert.equal(cards.length, 2); assert.equal(cards[0].dataset.id, 'a');
  assert.equal(content.querySelector('script'), null); assert.match(content.textContent, /&lt;script&gt;|<script>/);
});

test('VL progress appends to item and preserves expanded details across updates', () => {
  const { rc, content } = setup();
  rc.handleHybridDebugEvent('hybrid_start', { items:[{id:'v',index:1,method:'VL'}] });
  rc.handleHybridDebugEvent('hybrid_item_progress', { id:'v', event:'progressive_batch', data:{ batch_index:1 } });
  content.querySelector('details').open = true;
  rc.handleHybridDebugEvent('hybrid_item_progress', { id:'v', event:'pdf_loaded', data:{ total_pages:3 } });
  assert.equal(content.querySelector('details').open, true); assert.equal(content.querySelectorAll('.hybrid-debug-progress').length, 2);
});

test('错误收尾会将运行中标记错误、待执行标记跳过，重置清空', () => {
  const { rc, content } = setup();
  rc.handleHybridDebugEvent('hybrid_start', { items:[{id:'a',index:1},{id:'b',index:2}] });
  rc.handleHybridDebugEvent('hybrid_item_start', { id:'a' });
  rc.handleDebugEvent({ event:'error', data:{ message:'fail' } });
  assert.match(content.textContent, /error/); assert.match(content.textContent, /skipped/);
  rc.resetDebugResults(); assert.equal(content.innerHTML, '');
});
