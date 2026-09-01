"""规则调试重新抽取前端交互测试。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_rule_config(script: str):
    runner = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('ui/js/ruleConfig.js', 'utf8');
const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  document: { getElementById: () => null },
  API: {},
  Toast: { error: () => {} },
  Utils: {
    escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }
  },
  SchemaBuilder: { mount: () => {} },
  // 调试请求会带上该类型的入参（ui/js/typeParams.js），此处给个空实现
  TypeParams: { collectDebugValues: () => ({}), btnHtml: () => '', display: (t) => t },
};
sandbox.sandbox = sandbox;
vm.createContext(sandbox);
vm.runInContext(source + '\n;globalThis.__RuleConfig = RuleConfig;', sandbox);
(async () => {
SCRIPT
})().catch(error => {
  console.error(error);
  process.exit(1);
});
""".replace("SCRIPT", script)
    result = subprocess.run(
        ["node", "-e", runner],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)


def test_rule_debug_panel_has_default_off_reextract_and_result_section():
    result = _run_rule_config(
        r"""
const html = sandbox.__RuleConfig.buildRuleDebugPanel();
const toggle = html.match(/<input[^>]+id="debug-reextract-toggle"[^>]*>/)?.[0] || '';
console.log(JSON.stringify({
  hasToggle: toggle.length > 0,
  defaultOff: !toggle.includes('checked'),
  hasResultSection: html.includes('debug-sec-extraction-results'),
  hasResultContent: html.includes('debug-extraction-results-content'),
  hasLabel: html.includes('重新抽取依赖字段'),
}));
"""
    )

    assert result == {
        "hasToggle": True,
        "defaultOff": True,
        "hasResultSection": True,
        "hasResultContent": True,
        "hasLabel": True,
    }


def test_rule_debug_request_sends_one_shot_reextract_flag_and_restores_controls():
    result = _run_rule_config(
        r"""
const payloads = [];
const elements = {
  'debug-file-select': { value: 'f1', disabled: false },
  'debug-reextract-toggle': { checked: true, disabled: false },
  'debug-test-btn': { disabled: false, textContent: '测试' },
};
sandbox.document.getElementById = id => elements[id] || null;
sandbox.API.testRuleStream = async payload => { payloads.push(payload); };
const ruleConfig = sandbox.__RuleConfig;
ruleConfig.collectRuleFormData = () => ({
  rule_type: 'calc',
  depend_fields: ['a'],
  expression: '<field_result>a</field_result>',
});
ruleConfig.showRuleDebugConfigPreview = () => {};
ruleConfig.resetRuleDebugResults = () => {};
ruleConfig._showDebugLoading = () => {};
ruleConfig._hideDebugLoading = () => {};
ruleConfig.handleRuleDebugEvent = () => {};
ruleConfig.showDebugError = () => {};
await ruleConfig.runRuleTest();
console.log(JSON.stringify({
  payload: payloads[0],
  fileDisabled: elements['debug-file-select'].disabled,
  toggleDisabled: elements['debug-reextract-toggle'].disabled,
  testDisabled: elements['debug-test-btn'].disabled,
  testText: elements['debug-test-btn'].textContent,
}));
"""
    )

    assert result["payload"]["re_extract"] is True
    assert result["fileDisabled"] is False
    assert result["toggleDisabled"] is False
    assert result["testDisabled"] is False
    assert result["testText"] == "测试"


def test_rule_debug_renders_full_escaped_extraction_result():
    result = _run_rule_config(
        r"""
const elements = {
  'debug-sec-extraction-results': { style: { display: 'none' } },
  'debug-extraction-results-content': { innerHTML: '' },
};
sandbox.document.getElementById = id => elements[id] || null;
const ruleConfig = sandbox.__RuleConfig;
ruleConfig.state.ruleExtractionResults = {};
ruleConfig.renderRuleExtractionField({
  field_id: 'field-a',
  field_name: '字段 <A>',
  value: '<本次值>',
  reason: '<本次理由>',
  source_pages: [2, 3],
  success: true,
  index: 1,
  total: 1,
  is_direct_dependency: true,
});
console.log(JSON.stringify({
  display: elements['debug-sec-extraction-results'].style.display,
  html: elements['debug-extraction-results-content'].innerHTML,
}));
"""
    )

    html = result["html"]
    assert result["display"] == ""
    assert "field-a" in html
    assert "字段 &lt;A&gt;" in html
    assert "&lt;本次值&gt;" in html
    assert "&lt;本次理由&gt;" in html
    assert "2, 3" in html
    assert "成功" in html
    assert "规则依赖" in html


def test_field_and_rule_debug_panels_include_result_navigation():
    result = _run_rule_config(
        r"""
const fieldHtml = sandbox.__RuleConfig.buildDebugPanel();
const ruleHtml = sandbox.__RuleConfig.buildRuleDebugPanel();
console.log(JSON.stringify({
  fieldHasResultJump: fieldHtml.includes('id="debug-jump-result"'),
  fieldHasTopJump: fieldHtml.includes('id="debug-jump-top"'),
  fieldLabel: fieldHtml.includes('定位提取结果'),
  ruleHasResultJump: ruleHtml.includes('id="debug-jump-result"'),
  ruleHasTopJump: ruleHtml.includes('id="debug-jump-top"'),
  ruleLabel: ruleHtml.includes('定位分析结果'),
}));
"""
    )

    assert result == {
        "fieldHasResultJump": True,
        "fieldHasTopJump": True,
        "fieldLabel": True,
        "ruleHasResultJump": True,
        "ruleHasTopJump": True,
        "ruleLabel": True,
    }


def test_field_debug_auto_scrolls_when_result_arrives_without_user_scroll():
    result = _run_rule_config(
        r"""
let scrollCalls = 0;
const classes = new Set();
const elements = {
  'debug-sec-result': {
    style: { display: 'none' },
    scrollIntoView: () => { scrollCalls += 1; },
  },
  'debug-result-content': { innerHTML: '' },
  'debug-jump-result': {
    disabled: true,
    classList: {
      add: value => classes.add(value),
      remove: value => classes.delete(value),
    },
  },
};
sandbox.document.getElementById = id => elements[id] || null;
const ruleConfig = sandbox.__RuleConfig;
ruleConfig.resetDebugNavigation();
ruleConfig.handleDebugEvent({
  event: 'result',
  data: { value: '命中值', reason: '依据', source_pages: [2] },
});
console.log(JSON.stringify({
  scrollCalls,
  resultButtonDisabled: elements['debug-jump-result'].disabled,
  hasUnread: classes.has('has-unread-result'),
}));
"""
    )

    assert result == {
        "scrollCalls": 1,
        "resultButtonDisabled": False,
        "hasUnread": False,
    }


def test_rule_debug_preserves_user_scroll_until_result_button_is_clicked():
    result = _run_rule_config(
        r"""
let scrollCalls = 0;
const classes = new Set();
const elements = {
  'debug-sec-result': {
    style: { display: 'none' },
    scrollIntoView: () => { scrollCalls += 1; },
  },
  'debug-result-content': { innerHTML: '' },
  'debug-jump-result': {
    disabled: true,
    classList: {
      add: value => classes.add(value),
      remove: value => classes.delete(value),
    },
  },
};
sandbox.document.getElementById = id => elements[id] || null;
const ruleConfig = sandbox.__RuleConfig;
ruleConfig.resetDebugNavigation();
ruleConfig.markDebugScrollIntent();
ruleConfig.handleRuleDebugEvent({
  event: 'result',
  data: { result_value: '通过', reason: '依据' },
});
const beforeClick = {
  scrollCalls,
  resultButtonDisabled: elements['debug-jump-result'].disabled,
  hasUnread: classes.has('has-unread-result'),
};
ruleConfig.jumpToDebugResult();
console.log(JSON.stringify({
  beforeClick,
  afterClick: {
    scrollCalls,
    hasUnread: classes.has('has-unread-result'),
  },
}));
"""
    )

    assert result == {
        "beforeClick": {
            "scrollCalls": 0,
            "resultButtonDisabled": False,
            "hasUnread": True,
        },
        "afterClick": {
            "scrollCalls": 1,
            "hasUnread": False,
        },
    }
