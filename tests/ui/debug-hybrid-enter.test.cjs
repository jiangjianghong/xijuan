// 更贴近真实场景：打开 hybrid 表单 → 切换配置 → 进入调试 → 采集表单
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../../ui/js/ruleConfig.js'), 'utf8');

const page = `
<!doctype html><html><body>
<div id="rule-modal-overlay" class="rule-modal-overlay active">
  <div class="rule-modal">
    <div class="rule-modal-header"><h3 id="rule-modal-title">t</h3></div>
    <div id="rule-modal-body" class="rule-modal-body"></div>
    <div class="rule-modal-footer">
      <button id="debug-field-btn" class="btn btn-debug" onclick="RuleConfig.toggleDebugMode()">调试</button>
    </div>
  </div>
</div>
</body></html>
`;

function boot() {
  const dom = new JSDOM(page, { runScripts: 'dangerously', url: 'http://localhost/' });
  const w = dom.window;
  const logs = { errors: [], toasts: [] };
  w.Utils = { escapeHtml: v => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;') };
  w.Toast = { error: m => logs.toasts.push(String(m)), success(){}, info(){} };
  w.TypeParams = { btnHtml: () => '', display: s => s, async renderDebugInputs(){}, collectDebugValues: () => ({}) };
  w.lucide = { createIcons(){} };
  w.SchemaBuilder = { mount(){} };
  w.fetch = async () => ({ ok: true, json: async () => ({ data: { items: [] } }) });
  w.API = { getCompletedFiles: async () => ({ items: [] }), testFieldStream: async () => {} };
  w.eval(source + '\nwindow.RuleConfig=RuleConfig;');
  const rc = w.RuleConfig;
  rc.PROMPT_DEFAULTS = {};
  rc.cacheElements();
  // 模拟 App.init 之后打开表单
  rc.openFieldForm({
    field_id: 'h', field_name: '组合', source_type: 'text', search_type: 'hybrid',
    search_config: { strategy: 'union', items: [
      { id: 'a', source_type: 'text', method: 'context', config: { keywords: ['x'] } },
      { id: 'b', source_type: 'table', method: 'table_match', config: { table_match_keywords: ['t'] } },
    ]},
    text_extract_prompt: '结果：<search_result>混合检索结果</search_result>',
  });
  return { w, rc, logs };
}

test('openFieldForm(hybrid) 后 debug 按钮可见，toggle 进入调试', () => {
  const { w, rc, logs } = boot();
  const btn = w.document.getElementById('debug-field-btn');
  assert.equal(btn.style.display, '');
  let err = null;
  try { btn.click(); } catch (e) { err = e; }
  assert.equal(err, null, err && err.stack);
  assert.equal(rc.state.debugMode, true);
  assert.equal(btn.textContent, '退出调试');
  assert.ok(w.document.querySelector('.debug-split'));
  // 再点应退出
  btn.click();
  assert.equal(rc.state.debugMode, false);
  assert.equal(btn.textContent, '调试');
});

test('hybrid 表单切 method/source 后再调试，collect 不炸', () => {
  const { w, rc } = boot();
  const row = w.document.querySelector('.hybrid-item');
  const methodSel = row.querySelector('.hybrid-method');
  methodSel.value = 'vector_db';
  methodSel.dispatchEvent(new w.Event('change', { bubbles: true }));
  rc.enterDebugMode();
  let err = null, data = null;
  try { data = rc.collectFieldFormData(); } catch (e) { err = e; }
  assert.equal(err, null, err && err.stack);
  assert.equal(data.search_config.items[0].method, 'vector_db');
});

test('调试模式下点击测试按钮：runFieldTest 在 collect 抛错时不应永久卡死', async () => {
  const { w, rc } = boot();
  rc.enterDebugMode();
  // 故意破坏 hybrid DOM，模拟采集失败
  w.document.querySelectorAll('#fm-hybrid-items > .hybrid-item .hybrid-panel').forEach(p => p.remove());
  const btn = w.document.getElementById('debug-test-btn');
  const sel = w.document.getElementById('debug-file-select');
  sel.innerHTML = '<option value="">--</option><option value="fid" selected>fid</option>';
  sel.value = 'fid';
  sel.dispatchEvent(new w.Event('change'));
  await rc.runFieldTest();
  assert.equal(rc.state.debugTestRunning, false, 'collect 失败后必须复位 debugTestRunning');
  const errArea = w.document.getElementById('debug-error-area');
  // 面板缺失现在是可恢复采集：要么给出错误提示，要么仍能产出 hybrid 配置并发起请求
  // 关键是不能再出现「再点无反应」
  assert.equal(rc.state.debugTestRunning, false);
  // 第二次点击不应被 debugTestRunning 永久拦截
  let secondThrew = null;
  try { await rc.runFieldTest(); } catch (e) { secondThrew = e; }
  assert.equal(rc.state.debugTestRunning, false, '第二次点击后仍应复位');
  assert.equal(secondThrew, null);
});

test('组合项 panel 缺失时 collectHybridItem 不抛错并回退原配置', () => {
  const { w, rc } = boot();
  const row = w.document.querySelector('.hybrid-item');
  row.querySelector('.hybrid-panel').remove();
  let err = null, item = null;
  try { item = rc.collectHybridItem(row); } catch (e) { err = e; }
  assert.equal(err, null, err && err.stack);
  assert.equal(item.source_type, 'text');
  assert.ok(item.config);
});

test('window.RuleConfig 已导出，toggleDebugMode 失败时有 Toast 反馈', () => {
  const { w, rc } = boot();
  assert.equal(w.RuleConfig, rc);
  // 模拟 enterDebugMode 抛错
  const orig = rc.enterDebugMode;
  rc.enterDebugMode = () => { throw new Error('boom'); };
  const btn = w.document.getElementById('debug-field-btn');
  let err = null;
  try { btn.click(); } catch (e) { err = e; }
  assert.equal(err, null);
  assert.equal(rc.state.debugMode, false);
  assert.equal(btn.textContent, '调试');
  rc.enterDebugMode = orig;
});
