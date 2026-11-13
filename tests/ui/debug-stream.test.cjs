const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../../ui/js/ruleConfig.js'), 'utf8');

function setup() {
  const dom = new JSDOM(`
    <div id="debug-scroll-container">
      <div id="debug-sec-prompt"><div id="debug-prompt-content"></div></div>
      <div id="debug-sec-llm">
        <div id="debug-llm-reasoning-section"><div id="debug-llm-reasoning"></div></div>
        <div id="debug-llm-response"></div>
      </div>
      <div id="debug-loading-area"></div>
    </div>
  `, { runScripts: 'dangerously' });
  const w = dom.window;
  w.Utils = {
    escapeHtml: value => String(value ?? '')
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
  };
  w.eval(source + '\nwindow.rc = RuleConfig;');
  return { w, rc: w.rc };
}

test('field debug appends model deltas as text and preserves the final response', () => {
  const { rc, w } = setup();
  rc.handleDebugEvent({ event: 'prompt', data: { user_prompt: 'extract' } });
  rc.handleDebugEvent({ event: 'llm_reasoning_delta', data: { content: 'considering' } });
  rc.handleDebugEvent({ event: 'llm_reasoning_delta', data: { content: ' fields' } });
  assert.equal(w.document.getElementById('debug-loading-area').innerHTML, '');
  rc.handleDebugEvent({ event: 'llm_delta', data: { content: '<script>' } });
  rc.handleDebugEvent({ event: 'llm_delta', data: { content: 'done' } });

  const response = w.document.getElementById('debug-llm-response');
  assert.equal(w.document.getElementById('debug-llm-reasoning').textContent, 'considering fields');
  assert.equal(response.textContent, '<script>done');
  assert.equal(response.querySelector('script'), null);

  rc.handleDebugEvent({ event: 'llm_response', data: { raw_response: '<script>done' } });
  assert.equal(response.textContent, '<script>done');
});

test('analysis debug dispatches streamed model deltas', () => {
  const { rc, w } = setup();
  rc.handleRuleDebugEvent({ event: 'prompt', data: { user_prompt: 'judge' } });
  rc.handleRuleDebugEvent({ event: 'llm_reasoning_delta', data: { content: 'checking' } });
  rc.handleRuleDebugEvent({ event: 'llm_delta', data: { content: '{"result":' } });
  rc.handleRuleDebugEvent({ event: 'llm_delta', data: { content: 'true}' } });

  assert.equal(w.document.getElementById('debug-llm-reasoning').textContent, 'checking');
  assert.equal(w.document.getElementById('debug-llm-response').textContent, '{"result":true}');
});

test('debug output follows new stages until the user scrolls away', async () => {
  const { rc, w } = setup();
  const scroller = w.document.getElementById('debug-scroll-container');
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 900 });
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, value: 400 });
  rc.state.debugTestRunning = true;
  rc.setupDebugNavigation();

  rc.handleDebugEvent({ event: 'llm_delta', data: { content: 'first stage' } });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(scroller.scrollTop, 900);

  rc.markDebugScrollIntent();
  scroller.scrollTop = 120;
  rc.handleDebugEvent({ event: 'llm_delta', data: { content: 'next stage' } });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(scroller.scrollTop, 120);
  rc.disconnectDebugAutoScroll();
});
