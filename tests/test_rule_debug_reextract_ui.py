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
