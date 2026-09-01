"""上传前的入参弹窗交互（ui/js/typeParams.js）。

跑真实的 typeParams.js —— 后端对配了必填入参的类型会直接 400，这道弹窗是
UI 上传路径能不能用的唯一保障，逻辑坏了没有别的地方会报警。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

_RUNNER = r"""
const fs = require('fs');
const vm = require('vm');

function makeEl(id) {
  return {
    id, value: '', textContent: '', innerHTML: '',
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    dataset: {}, focus() {},
    querySelector() { return null; },
  };
}

const els = {};
['upload-params-modal-overlay', 'upload-params-hint',
 'upload-params-fields', 'upload-params-error'].forEach(id => els[id] = makeEl(id));

// 输入框实际由 innerHTML 渲染，这里按 param_key 提供可写桩
const inputs = {};
els['upload-params-modal-overlay'].querySelector = () => inputs['current_date'] || null;

const sandbox = {
  console, setTimeout, clearTimeout, Promise,
  document: {
    getElementById: id => els[id] || null,
    querySelector: sel => {
      const m = sel.match(/data-param-key="([^"]+)"/);
      return m ? (inputs[m[1]] || null) : null;
    },
    querySelectorAll: () => [],
  },
  Utils: { escapeHtml: v => String(v ?? '') },
  Toast: { error: () => {}, success: () => {} },
  API: {
    getCurrentTypeId: () => 'demo',
    listTypeParams: async () => ([
      { param_key: 'current_date', param_name: '当前日期', description: '系统日期',
        default_value: '', required: 1, priority: 0 },
    ]),
  },
  RuleConfig: { closeInsertTagDropdown() {} },
};
sandbox.sandbox = sandbox;
sandbox.els = els;
sandbox.inputs = inputs;
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync('ui/js/typeParams.js', 'utf8') + '\n;globalThis.__TP = TypeParams;',
  sandbox,
);
const TP = sandbox.__TP;

(async () => {
SCRIPT
})().catch(error => {
  console.error(error);
  process.exit(1);
});
"""


def _run(script: str):
    runner = _RUNNER.replace("SCRIPT", script)
    result = subprocess.run(
        ["node", "-e", runner],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)


def test_upload_dialog_blocks_missing_required_then_resolves():
    """必填未填时拦住不放行；填上后 resolve 出值并关闭弹窗。"""
    result = _run(
        r"""
const out = {};
inputs['current_date'] = { value: '   ', focus() {} };
const p = TP.promptForUpload('demo');
await new Promise(r => setTimeout(r, 0));
out.dialogOpened = els['upload-params-modal-overlay'].classList.contains('active');

TP.confirmUploadDialog();
out.blockedOnMissing = els['upload-params-modal-overlay'].classList.contains('active');
out.errorText = els['upload-params-error'].textContent;

inputs['current_date'] = { value: '2026-09-01', focus() {} };
TP.confirmUploadDialog();
out.resolved = await p;
out.dialogClosed = !els['upload-params-modal-overlay'].classList.contains('active');
console.log(JSON.stringify(out));
"""
    )

    assert result["dialogOpened"] is True
    assert result["blockedOnMissing"] is True
    assert "当前日期" in result["errorText"]
    assert result["resolved"] == {"current_date": "2026-09-01"}
    assert result["dialogClosed"] is True


def test_upload_dialog_cancel_resolves_null():
    """取消返回 null，调用方据此中止上传（而不是当成空参数传上去）。"""
    result = _run(
        r"""
inputs['current_date'] = { value: '2026-09-01', focus() {} };
const p = TP.promptForUpload('demo');
await new Promise(r => setTimeout(r, 0));
TP.cancelUploadDialog();
console.log(JSON.stringify({ cancelled: await p }));
"""
    )

    assert result["cancelled"] is None


def test_upload_dialog_skipped_when_type_has_no_params():
    """没定义入参的类型不弹窗，直接返回空对象——不打扰绝大多数上传。"""
    result = _run(
        r"""
sandbox.API.listTypeParams = async () => [];
const values = await TP.promptForUpload('other');
console.log(JSON.stringify({
  values,
  dialogShown: els['upload-params-modal-overlay'].classList.contains('active'),
}));
"""
    )

    assert result["values"] == {}
    assert result["dialogShown"] is False
