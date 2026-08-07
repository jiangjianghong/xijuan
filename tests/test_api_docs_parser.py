"""API 手册前端解析回归测试。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_api_docs_sections_ignore_code_fences():
    script = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('ui/js/api-docs.js', 'utf8');
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const markdown = [
  '# 顶层标题',
  '',
  '```python',
  '# 这不是标题',
  '```',
  '',
  '## 正文标题',
].join('\n');

const sections = sandbox.window.ApiDocs.buildSections(markdown).map((item) => item.title);
process.stdout.write(JSON.stringify(sections));
"""

    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert json.loads(result.stdout) == ["顶层标题", "正文标题"]


def test_api_docs_body_renders_hash_inside_code_as_code_text():
    script = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('ui/js/api-docs.js', 'utf8');
const sandbox = {
  window: {},
  console,
  Utils: {
    escapeHtml(text) {
      return String(text || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }
  }
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const markdown = [
  '# 顶层标题',
  '',
  '```python',
  'if pages is None:',
  '    # vl_progressive：全文扫描，未定位具体页',
  '```',
].join('\n');

process.stdout.write(sandbox.window.ApiDocs.markdownToHtml(markdown));
"""

    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert '<pre class="api-code">' in result.stdout
    assert '# vl_progressive：全文扫描，未定位具体页' in result.stdout
    assert '<h1 id="vl_progressive' not in result.stdout
