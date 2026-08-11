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


def _run_node(script: str) -> str:
    """在 node vm 沙箱里跑 ui/js/api-docs.js 并执行 script，返回 stdout。"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


_BOOT = r"""
const fs = require('fs');
const vm = require('vm');
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
vm.runInContext(fs.readFileSync('ui/js/api-docs.js', 'utf8'), sandbox);
const ApiDocs = sandbox.window.ApiDocs;
"""


def test_split_sections_preserves_every_line():
    """分段必须无损：所有段的 lines 拼回来要和原文逐字一致。"""
    script = _BOOT + r"""
const markdown = [
  '# 标题一',
  '正文 A',
  '',
  '## 标题二',
  '正文 B',
  '| a | b |',
  '| --- | --- |',
  '| 1 | 2 |',
].join('\n');

const sections = ApiDocs.splitSections(markdown);
const rebuilt = sections.map((s) => s.lines.join('\n')).join('\n');
process.stdout.write(JSON.stringify({ same: rebuilt === markdown, count: sections.length }));
"""
    assert json.loads(_run_node(script)) == {"same": True, "count": 2}


def test_split_sections_ignores_headings_inside_code_fence():
    """代码块里的 # 不能切出新段。"""
    script = _BOOT + r"""
const markdown = [
  '# 真标题',
  '```python',
  '# 这不是标题',
  '```',
  '尾部正文',
].join('\n');

const sections = ApiDocs.splitSections(markdown);
process.stdout.write(JSON.stringify(sections.map((s) => s.title)));
"""
    assert json.loads(_run_node(script)) == ["真标题"]


def test_split_sections_ids_match_build_sections():
    """段 id 必须与 TOC 锚点完全一致，否则点目录跳空。"""
    script = _BOOT + r"""
const markdown = [
  '# 概览',
  '正文',
  '## 重名',
  'a',
  '## 重名',
  'b',
].join('\n');

const fromSplit = ApiDocs.splitSections(markdown).filter((s) => s.level > 0).map((s) => s.id);
const fromToc = ApiDocs.buildSections(markdown).map((s) => s.id);
process.stdout.write(JSON.stringify({ fromSplit, fromToc }));
"""
    payload = json.loads(_run_node(script))
    assert payload["fromSplit"] == payload["fromToc"]
    assert len(set(payload["fromSplit"])) == 3


def test_split_sections_text_is_lowercase_haystack():
    """段自带小写全文，供搜索直接匹配，不必再读 DOM textContent。"""
    script = _BOOT + r"""
const markdown = ['# GET /File/List', '返回 File_ID 列表'].join('\n');
const section = ApiDocs.splitSections(markdown)[0];
process.stdout.write(JSON.stringify({
  hasPath: section.text.includes('/file/list'),
  hasField: section.text.includes('file_id'),
  est: section.estHeight > 0,
}));
"""
    assert json.loads(_run_node(script)) == {"hasPath": True, "hasField": True, "est": True}


def test_split_sections_handles_crlf_document():
    """真实手册是 CRLF 文件：\\r 不能残留进标题，也不能切错段。"""
    script = _BOOT + r"""
const markdown = ['# 标题一', '正文 A', '## 标题二', '正文 B'].join('\r\n');
const sections = ApiDocs.splitSections(markdown);
const rebuilt = sections.map((s) => s.lines.join('\n')).join('\n');
process.stdout.write(JSON.stringify({
  titles: sections.map((s) => s.title),
  ids: sections.map((s) => s.id),
  lossless: rebuilt === markdown.replace(/\r\n/g, '\n'),
}));
"""
    assert json.loads(_run_node(script)) == {
        "titles": ["标题一", "标题二"],
        "ids": ["标题一", "标题二"],
        "lossless": True,
    }


def test_per_section_render_matches_full_render():
    """逐段渲染拼起来必须等价于整篇渲染：分段不能切坏表格 / 代码块 / 列表。"""
    script = _BOOT + r"""
const markdown = fs.readFileSync('docs/API_FULL_REFERENCE.md', 'utf8');

// 懒渲染路径：标题在骨架里，正文逐段生成
const sections = ApiDocs.splitSections(markdown);
const perSection = sections.map((s) => {
  const head = s.level ? `<h${s.level}>${ApiDocs.renderInline(s.rawTitle)}</h${s.level}>` : '';
  const body = ApiDocs.markdownToHtml((s.level ? s.lines.slice(1) : s.lines).join('\n'));
  return head + body;
}).join('\n');

// 原路径：整篇一次渲染
const full = ApiDocs.markdownToHtml(markdown);

// 只比结构性标签的出现次数，标题的 id/属性差异忽略
const tally = (html) => {
  const out = {};
  ['table', 'tr', 'td', 'th', 'pre', 'code', 'ul', 'li', 'blockquote', 'p', 'hr'].forEach((tag) => {
    out[tag] = (html.match(new RegExp('<' + tag + '[ >]', 'g')) || []).length;
  });
  return out;
};

process.stdout.write(JSON.stringify({ perSection: tally(perSection), full: tally(full) }));
"""
    payload = json.loads(_run_node(script))
    assert payload["perSection"] == payload["full"]


def test_match_sections_empty_query_matches_all():
    """空查询直接全命中，不做任何逐字符匹配。"""
    script = _BOOT + r"""
const sections = ApiDocs.splitSections(['# A', 'x', '## B', 'y'].join('\n'));
process.stdout.write(JSON.stringify(ApiDocs.matchSections(sections, '   ')));
"""
    assert json.loads(_run_node(script)) == [True, True]


def test_match_sections_is_case_insensitive_on_section_text():
    """按段全文匹配，且大小写不敏感。"""
    script = _BOOT + r"""
const markdown = [
  '## GET /file/list',
  '分页查询文件列表',
  '## POST /analysis/run',
  '独立分析接口',
].join('\n');
const sections = ApiDocs.splitSections(markdown);
process.stdout.write(JSON.stringify({
  byPath: ApiDocs.matchSections(sections, '/ANALYSIS/RUN'),
  byBody: ApiDocs.matchSections(sections, '分页查询'),
  miss: ApiDocs.matchSections(sections, '不存在的词'),
}));
"""
    assert json.loads(_run_node(script)) == {
        "byPath": [False, True],
        "byBody": [True, False],
        "miss": [False, False],
    }
