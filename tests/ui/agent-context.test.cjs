const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

function setup() {
  const html = fs.readFileSync(path.join(__dirname, '../../xijuan_agent/ui/agent.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true });
  const script = fs.readFileSync(path.join(__dirname, '../../ui/js/agent.js'), 'utf8');
  dom.window.eval(script.replace(/\}\)\(\);\s*$/, 'window.agentTest = { handleEvent, createAssistantMessage }; })();'));
  const view = dom.window.agentTest.createAssistantMessage();
  const run = { view, toolCards: new Map() };
  const event = data => dom.window.agentTest.handleEvent(run, { type: 'context_compact', ...data });
  return { dom, run, event };
}

test('background compaction updates one status instead of appending repeated notes', () => {
  const { dom, run, event } = setup();
  event({ state: 'started', task_id: 1 });
  const note = run.compactionNote;
  assert.match(note.textContent, /后台/);
  for (let i = 0; i < 4; i++) event({ state: 'started', task_id: 1 });
  event({ state: 'waiting', task_id: 1 });
  assert.equal(run.compactionNote, note);
  assert.match(note.textContent, /等待/);
  event({ state: 'completed', task_id: 1 });
  event({ state: 'started', task_id: 2 });
  event({ state: 'completed', task_id: 1 });
  assert.equal(note.dataset.state, 'started');
  event({ state: 'completed', task_id: 2 });
  assert.equal(run.view.evidence.querySelectorAll('.context-note').length, 1);
  assert.match(note.textContent, /已整理/);
  dom.window.close();
});

test('cancelled, skipped and failed runs clear unfinished compaction state', () => {
  for (const state of ['cancelled', 'skipped', 'error', 'done']) {
    const { dom, run, event } = setup();
    event({ state: 'started', task_id: 1 });
    if (state === 'error' || state === 'done') dom.window.agentTest.handleEvent(run, { type: state });
    else event({ state, task_id: 1 });
    assert.equal(run.view.evidence.querySelectorAll('.context-note').length, 0);
    dom.window.close();
  }
});
