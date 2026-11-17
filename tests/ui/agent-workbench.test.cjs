// 覆盖研究工作台的异步边界，防止旧请求污染新研究或丢失文件选择。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

function setup() {
  const html = fs.readFileSync(path.join(__dirname, '../../xijuan_agent/ui/agent.html'), 'utf8');
  const script = fs.readFileSync(path.join(__dirname, '../../ui/js/agent.js'), 'utf8');
  const dom = new JSDOM(html, { url: 'http://localhost/agent', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.TextDecoder = TextDecoder;
  w.matchMedia = () => ({ matches: false, addEventListener() {} });
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  w.eval(script.replace(/\}\)\(\);\s*$/, 'window.agentTest = { state, els, handleEvent, createAssistantMessage, renderReferences, renderAnswer, submit, clearConversation, consumeStream, loadFiles, addSelectedFiles, syncComposer, removeFile }; })();'));
  return { dom, w, ...w.agentTest };
}
const file = (id, name = `研究文档${id}.pdf`) => ({ file_id: id, file_name: name, create_time: '2026-11-17T10:00:00' });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function stream(events, split = false) {
  const encoded = new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\r\n\r\n`).join(''));
  return new Response(new ReadableStream({ start(controller) {
    if (split) for (let i = 0; i < encoded.length; i += 7) controller.enqueue(encoded.slice(i, i + 7));
    else controller.enqueue(encoded);
    controller.close();
  } }), { headers: { 'Content-Type': 'text/event-stream' } });
}

test('无文档时发送会打开选择器并保留研究问题', async () => {
  const x = setup();
  try {
    let requests = 0;
    x.w.fetch = async () => { requests++; return Response.json({ items: [], total: 0 }); };
    x.els.question.value = '请核对收入数据';
    await x.submit({ preventDefault() {} });
    assert.equal(x.els['file-picker'].open, true);
    assert.equal(x.els.question.value, '请核对收入数据');
    assert.equal(x.state.hasStarted, false);
    assert.equal(requests, 1);
    await new Promise(resolve => setImmediate(resolve));
  } finally { x.dom.window.close(); }
});

test('SSE 分块解析保留中文、工具结果、历史和最终 Markdown', async () => {
  const x = setup();
  try {
    x.state.conversationFiles = [file('a')];
    x.els.question.value = '比较两项指标';
    const requests = [];
    const history = [{ role: 'user', content: '比较两项指标' }, { role: 'assistant', content: '完整回答' }];
    x.w.fetch = async (url, options) => {
      requests.push({ url, ...options });
      return stream([
        { type: 'context_ready', files: [file('a')] },
        { type: 'context_window', estimated_request_tokens: 12000, max_tokens: 24000 },
        { type: 'tool_call_delta', round: 1, index: 0, name_delta: 'read_', arguments_delta: '{' },
        { type: 'tool_call_delta', round: 1, index: 0, name_delta: 'table', arguments_delta: '}' },
        { type: 'tool_call', round: 1, index: 0, tool_call_id: 'call-a', name: 'read_table', arguments: { file_id: 'a', table_index: 0 } },
        { type: 'tool_result', round: 1, tool_call_id: 'call-a', name: 'read_table', content: '{"value":12}' },
        { type: 'token', text: '## 数据核对\n\n**结论**：金额一致。\n\n| 指标 | 结果 |\n| --- | --- |\n| 收入 | 12 |' },
        { type: 'history_state', messages: history }, { type: 'done' },
      ], true);
    };
    await x.submit({ preventDefault() {} });
    assert.deepEqual(JSON.parse(requests[0].body).file_ids, ['a']);
    assert.equal(requests[0].headers['X-Requested-With'], 'XMLHttpRequest');
    assert.equal(x.els['chat-inner'].querySelectorAll('.tool-card').length, 1);
    assert.equal(x.els['chat-inner'].querySelector('.tool-state').textContent, '已完成');
    assert.equal(x.els['chat-inner'].querySelector('.tool-name').textContent, '表格精读');
    assert.equal(x.els['chat-inner'].querySelectorAll('.answer-text table td').length, 2);
    assert.equal(x.els['context-usage'].textContent, '约 50%');
    assert.equal(x.state.history.length, 2);
    assert.equal(x.state.controller, null);
    assert.equal(x.els['export-chat'].disabled, false);
    x.els.question.value = '继续核对';
    await x.submit({ preventDefault() {} });
    assert.deepEqual(JSON.parse(requests[1].body).history, history);
  } finally { x.dom.window.close(); }
});

test('流提前结束会明确提示不完整，而不是维持永久执行状态', async () => {
  const x = setup();
  try {
    x.state.conversationFiles = [file('a')]; x.els.question.value = '核对';
    x.w.fetch = async () => stream([{ type: 'token', text: '部分回答' }]);
    await x.submit({ preventDefault() {} });
    assert.match(x.els['chat-inner'].querySelector('.error-note').textContent, /连接提前结束/);
    assert.equal(x.state.controller, null);
    assert.equal(x.els['chat-inner'].querySelector('.answer-text').textContent, '部分回答');
    assert.equal(x.els['chat-inner'].querySelector('.research-status').dataset.phase, 'done');
  } finally { x.dom.window.close(); }
});

test('停止研究会保留部分回答并恢复输入状态', async () => {
  const x = setup();
  try {
    x.state.conversationFiles = [file('a')]; x.els.question.value = '阅读';
    x.w.fetch = (url, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new x.w.DOMException('停止', 'AbortError'))));
    const request = x.submit({ preventDefault() {} });
    const run = x.state.currentRun;
    x.handleEvent(run, { type: 'token', text: '已经找到的依据' });
    x.els['stop-button'].click(); await request;
    assert.equal(x.state.controller, null);
    assert.equal(run.view.answer.textContent, '已经找到的依据');
    assert.equal(x.els['conversation-status'].textContent, '已停止');
    assert.equal(run.view.progress.dataset.phase, 'done');
  } finally { x.dom.window.close(); }
});

test('清空后晚到的旧请求不能关闭新请求或写入旧历史', async () => {
  const x = setup();
  try {
    const old = deferred(), current = deferred();
    x.w.fetch = () => old.promise;
    x.state.conversationFiles = [file('old')]; x.els.question.value = '旧问题';
    const first = x.submit({ preventDefault() {} });
    const oldRun = x.state.currentRun;
    x.clearConversation();
    x.w.fetch = () => current.promise;
    x.state.conversationFiles = [file('new')]; x.els.question.value = '新问题';
    const second = x.submit({ preventDefault() {} }); const controller = x.state.controller;
    x.handleEvent(oldRun, { type: 'history_state', messages: [{ role: 'assistant', content: '不应写入' }] });
    old.resolve(stream([{ type: 'done' }])); await first;
    assert.equal(x.state.controller, controller);
    assert.equal(x.state.history.length, 0);
    assert.equal(x.els['chat-inner'].querySelectorAll('.message').length, 2);
    current.resolve(stream([{ type: 'token', text: '新回答' }, { type: 'done' }])); await second;
    assert.equal(x.els['chat-inner'].querySelector('.answer-text').textContent, '新回答');
  } finally { x.dom.window.close(); }
});

test('文件搜索的慢旧响应不会覆盖新的结果', async () => {
  const x = setup();
  try {
    const old = deferred(), current = deferred(); let count = 0;
    x.w.fetch = () => count++ ? current.promise : old.promise;
    x.els['file-search'].value = '旧关键词'; const first = x.loadFiles();
    x.els['file-search'].value = '新关键词'; const second = x.loadFiles();
    current.resolve(Response.json({ items: [file('new')], total: 1 })); await second;
    old.resolve(Response.json({ items: [file('old')], total: 1 })); await first;
    assert.match(x.els['file-list'].textContent, /研究文档new/);
    assert.doesNotMatch(x.els['file-list'].textContent, /研究文档old/);
    assert.equal(x.state.pickerLoading, false);
  } finally { x.dom.window.close(); }
});

test('跨搜索选择的文档都能加入研究，开始对话后不移除历史依赖', async () => {
  const x = setup();
  try {
    x.w.fetch = async () => Response.json({ items: [file('a')], total: 1 });
    await x.loadFiles();
    const first = x.els['file-list'].querySelector('input'); first.checked = true; first.dispatchEvent(new x.w.Event('change'));
    x.w.fetch = async () => Response.json({ items: [file('b')], total: 1 });
    x.els['file-search'].value = 'b'; await x.loadFiles();
    const second = x.els['file-list'].querySelector('input'); second.checked = true; second.dispatchEvent(new x.w.Event('change'));
    x.addSelectedFiles();
    assert.deepEqual(Array.from(x.state.conversationFiles, f => f.file_id), ['a', 'b']);
    x.removeFile('a'); assert.equal(x.state.conversationFiles.length, 1);
    x.state.hasStarted = true; x.removeFile('b'); assert.equal(x.state.conversationFiles.length, 1);
  } finally { x.dom.window.close(); }
});

test('达到 20 份时禁选额外文件，取消选择后恢复', async () => {
  const x = setup();
  try {
    x.state.conversationFiles = Array.from({ length: 19 }, (_, i) => file(`existing-${i}`));
    x.w.fetch = async () => Response.json({ items: [file('a'), file('b')], total: 2 }); await x.loadFiles();
    const [a, b] = x.els['file-list'].querySelectorAll('input');
    a.checked = true; a.dispatchEvent(new x.w.Event('change')); assert.equal(b.disabled, true);
    a.checked = false; a.dispatchEvent(new x.w.Event('change')); assert.equal(b.disabled, false);
  } finally { x.dom.window.close(); }
});

test('答案格式化把 HTML 当文本处理，代码中的 Markdown 不误解析', () => {
  const x = setup();
  try {
    const output = x.renderAnswer('# 研究结论\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n`**原样保留**`\n\n- **有依据**\n- 第二项');
    const holder = x.w.document.createElement('div'); holder.innerHTML = output;
    assert.equal(holder.querySelectorAll('script,img').length, 0);
    assert.equal(holder.querySelector('code').textContent, '**原样保留**');
    assert.equal(holder.querySelectorAll('li').length, 2);
    assert.equal(holder.querySelector('h2').textContent, '研究结论');
  } finally { x.dom.window.close(); }
});

test('表格里的转义和代码竖线不会丢失数值，列数异常时保留原文', () => {
  const x = setup();
  try {
    const holder = x.w.document.createElement('div');
    holder.innerHTML = x.renderAnswer('| 指标 | 数值 |\n| --- | --- |\n| A\\|B | 12 |\n| `C|D` | 34 |');
    assert.deepEqual([...holder.querySelectorAll('td')].map(cell => cell.textContent), ['A|B', '12', 'C|D', '34']);
    const malformed = '| 指标 | 数值 |\n| --- | --- |\n| 多出 | 一列 | 56 |';
    holder.innerHTML = x.renderAnswer(malformed);
    assert.equal(holder.querySelectorAll('table').length, 0);
    assert.equal(holder.textContent, malformed);
  } finally { x.dom.window.close(); }
});

test('第二个工具直接返回错误时，仍按 delta 的调用 ID 关联正确证据', () => {
  const x = setup();
  try {
    const run = { view: x.createAssistantMessage(), toolCards: new Map() };
    x.handleEvent(run, { type: 'tool_call_delta', round: 1, index: 0, id: 'first', name_delta: 'read_table' });
    x.handleEvent(run, { type: 'tool_call_delta', round: 1, index: 1, id: 'second', name_delta: 'read_section' });
    x.handleEvent(run, { type: 'tool_call', round: 1, index: 0, tool_call_id: 'first', name: 'read_table', arguments: {} });
    x.handleEvent(run, { type: 'tool_result', round: 1, tool_call_id: 'first', content: '{"value":12}' });
    x.handleEvent(run, { type: 'tool_result', round: 1, tool_call_id: 'second', content: '{"error":"invalid arguments"}' });
    const cards = [...run.view.evidence.querySelectorAll('.tool-card')];
    assert.equal(cards.length, 2);
    assert.equal(cards[0].querySelector('.tool-state').textContent, '已完成');
    assert.equal(cards[1].querySelector('.tool-state').textContent, '未完成');
    assert.match(cards[1].textContent, /invalid arguments/);
  } finally { x.dom.window.close(); }
});
