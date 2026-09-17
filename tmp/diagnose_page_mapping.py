"""只读诊断页码：数据库事务强制只读，结果保留在本地 tmp。"""
import argparse
import json
import os
import sys
from pathlib import Path

import pymysql
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.page_mapping import (
    _block_probes_and_bbox, _collect_table_groups, _TABLE_RE,
    _unique_find, build_page_mapping, lookup_page_num, build_page_projection,
)


def direct_check():
    """仅用已有页级投影验证直接取页码，不修改业务算法或原始样本。"""
    out = ROOT / 'tmp/page_mapping_diagnosis'
    reports = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    results = []
    for report in reports:
        sample = json.loads((out / (report['file_id'] + '.json')).read_text(encoding='utf-8'))
        row = sample['row']
        projection = build_page_projection(row['middle_json'])
        direct_tables = [dict(html=m.group(), pages=p['source_pages'])
            for p in projection for m in _TABLE_RE.finditer(p['content'])]
        raw_tables = [m.group() for m in _TABLE_RE.finditer(row['file_content'])]
        same = raw_tables == [t['html'] for t in direct_tables]
        examples = []
        if same:
            for err in report['table_errors'][:3]:
                examples.append(dict(table_index=err['index'], stored=err['stored'],
                    direct_pages=direct_tables[err['index'] - 1]['pages']))
        phrases = ['总投资650.0万元，其中工程费用613.7万元、工程建设其他费用10.0万元、预备费26.3万元。其中申请上级以工代赈专项资金592.0万元，本级配套资金58.0万元，计划发放劳务报酬304.6万元']
        hits = [{'phrase': phrase[:25], 'pages': [p['source_pages'] for p in projection if phrase in p['content']]} for phrase in phrases]
        result = dict(file_id=report['file_id'], name=report['name'], table_count=len(raw_tables),
            direct_table_count=len(direct_tables), all_table_html_equal_in_order=same,
            examples=examples, phrase_hits=hits if report['errors'] else [])
        results.append(result)
        if report['errors'] or not same:
            print(json.dumps(result, ensure_ascii=False))
    summary = dict(files=len(results), files_with_exact_table_sequence=sum(r['all_table_html_equal_in_order'] for r in results),
        total_tables=sum(r['table_count'] for r in results))
    print(json.dumps(summary))
    (out / 'direct_check.json').write_text(json.dumps(dict(summary=summary, results=results), ensure_ascii=False, indent=2), encoding='utf-8')


def replay():
    """离线比较原映射与当前算法，绝不覆盖原始诊断报告。"""
    import time
    out = ROOT / 'tmp/page_mapping_diagnosis'
    originals = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    results = []
    for old in originals:
        sample = json.loads((out / (old['file_id'] + '.json')).read_text(encoding='utf-8'))
        started = time.perf_counter()
        current = audit(sample['row'], sample['tables'])
        seconds = round(time.perf_counter() - started, 3)
        old_errors = [e for e in current['errors'] if e['stored'] != str(e['expected'])]
        new_errors = [e for e in current['errors'] if e['rebuilt'] != str(e['expected'])]
        regressions = [e for e in new_errors if e['stored'] == str(e['expected'])]
        new_tables = [e for e in current['table_errors'] if e['rebuilt'].split('-')[0] != str(e['expected_start'])]
        table_regressions = [e for e in new_tables if str(e['stored']).split('-')[0] == str(e['expected_start'])]
        result = dict(file_id=old['file_id'], name=old['name'], checks=current['checks'],
            anchors=[current['anchors'], current['rebuilt_anchors']], seconds=seconds,
            block_errors=[len(old_errors), len(new_errors)], regressions=regressions,
            table_errors=[len(old['table_errors']), len(new_tables)], table_regressions=table_regressions,
            remaining_blocks=new_errors, remaining_tables=new_tables)
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k not in ('remaining_blocks', 'remaining_tables')}, ensure_ascii=False), flush=True)
    summary = dict(files=len(results), checks=sum(r['checks'] for r in results),
        block_errors=[sum(r['block_errors'][i] for r in results) for i in (0, 1)],
        table_errors=[sum(r['table_errors'][i] for r in results) for i in (0, 1)],
        regressions=sum(len(r['regressions']) for r in results),
        table_regressions=sum(len(r['table_regressions']) for r in results))
    print(json.dumps(summary), flush=True)
    (out / 'replay.json').write_text(json.dumps(dict(summary=summary, results=results), ensure_ascii=False, indent=2), encoding='utf-8')


def benchmark():
    """同一份原始数据比较 Git 基线与当前构建耗时。"""
    import subprocess
    import time
    baseline = {}
    source = subprocess.check_output(['git', 'show', 'HEAD:utils/page_mapping.py'], cwd=ROOT).decode('utf-8')
    exec(compile(source, '<baseline_page_mapping>', 'exec'), baseline)
    out = ROOT / 'tmp/page_mapping_diagnosis'
    reports = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    results = []
    for report in reports:
        if report['pages'] < 100:
            continue
        sample = json.loads((out / (report['file_id'] + '.json')).read_text(encoding='utf-8'))['row']
        durations = {}
        for name, fn in [('before', baseline['build_page_mapping']), ('after', build_page_mapping)]:
            started = time.perf_counter()
            mapping = fn(sample['file_content'], sample['middle_json'])
            durations[name] = dict(seconds=round(time.perf_counter() - started, 3), anchors=len(mapping))
        result = dict(file_id=report['file_id'], pages=report['pages'], **durations)
        results.append(result)
        print(json.dumps(result), flush=True)
    (out / 'benchmark.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


def local_detail():
    from collections import Counter
    from bisect import bisect_right
    out = ROOT / 'tmp/page_mapping_diagnosis'
    reports = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    details = []
    for report in reports:
        if not report['errors'] and not report['table_errors']:
            continue
        sample = json.loads((out / (report['file_id'] + '.json')).read_text(encoding='utf-8'))
        row = sample['row']
        md = row['file_content']
        pages = decode(row['middle_json'])['pdf_info']
        mapping = decode(row['page_mapping'])
        positions = [m['start_pos'] for m in mapping]
        causes = Counter()
        examples = []
        for err in report['errors']:
            page = next(p for p in pages if p['page_idx'] + 1 == err['expected'])
            block = page['para_blocks'][err['block']]
            probes, _ = _block_probes_and_bbox(block)
            found, used = _unique_find(md, probes)
            cause = 'no_unique_prefix' if found < 0 else ('anchor_inside_block' if found > err['pos'] else 'anchor_removed_or_collision')
            causes[cause] += 1
            i = max(0, bisect_right(positions, err['pos']) - 1)
            example = dict(**err, cause=cause, found=found,
                counts=[{'len': n, 'count': md.count(probes[0][:n].strip()), 'prefix': probes[0][:n]} for n in (40, 25)],
                previous=mapping[i], following=mapping[i + 1] if i + 1 < len(mapping) else None,
                anchors_on_page=sum(m['page_num'] == err['expected'] for m in mapping))
            examples.append(example)
        detail = dict(file_id=report['file_id'], name=report['name'], causes=dict(causes), examples=examples)
        details.append(detail)
        print(json.dumps({**detail, 'examples': examples[:4]}, ensure_ascii=False))
    (out / 'detail.json').write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(sample_files=len(reports), checked_blocks=sum(r['checks'] for r in reports),
        files_with_errors=len(details), wrong_block_starts=sum(len(r['errors']) for r in reports),
        wrong_table_starts=sum(len(r['table_errors']) for r in reports),
        changed_mappings=sum(r['mapping_changed'] for r in reports)), ensure_ascii=False))
    # 最小复现：正文整体唯一，但前 40 字重复，第二页整段仍被查成第一页。
    common = '这是两页共享的重复前缀内容用于复现定位问题。' * 3
    texts = ['第一页独有开头用于建立可靠锚点', common + '第一页结尾', common + '第二页结尾']
    md = '\n\n'.join(texts)
    block = lambda value: {'type': 'text', 'lines': [{'spans': [{'content': value}]}]}
    middle = {'pdf_info': [
        {'page_idx': 0, 'para_blocks': [block(texts[0]), block(texts[1])]},
        {'page_idx': 1, 'para_blocks': [block(texts[2])]},
    ]}
    pos = md.index(texts[2])
    actual = lookup_page_num(build_page_mapping(md, middle), pos, pos + len(texts[2]))
    assert md.count(texts[2]) == 1
    assert actual == '1', '复现行为已经发生变化，请重新分析'
    print(json.dumps(dict(minimal_reproduction={'expected': 2, 'actual': actual, 'full_text_unique': True}), ensure_ascii=False))


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def audit(row, tables):
    md = row['file_content']
    middle = decode(row['middle_json'])
    old = decode(row['page_mapping']) or []
    fresh = build_page_mapping(md, middle)
    pages = middle.get('pdf_info', [])
    errors = []
    checks = 0
    # 独立采用完整片段唯一匹配，核对片段实际所属的 middle_json 页。
    for page in pages:
        expected = page.get('page_idx', 0) + 1
        for bi, block in enumerate(page.get('para_blocks', [])):
            probes, _ = _block_probes_and_bbox(block)
            for probe in probes:
                if len(probe) < 8 or md.count(probe) != 1:
                    continue
                pos = md.find(probe)
                checks += 1
                actual = lookup_page_num(old, pos, pos)
                current = lookup_page_num(fresh, pos, pos)
                if actual != str(expected) or current != str(expected):
                    errors.append(dict(expected=expected, stored=actual, rebuilt=current,
                        pos=pos, block=bi, kind=block.get('type'), text=probe[:100]))
                break
    groups = _collect_table_groups(pages)
    spans = list(_TABLE_RE.finditer(md))
    table_errors = []
    # 完整 HTML 唯一匹配确定归属，避免依赖表格序号配对推断。
    owners = {}
    for page in pages:
        for block in page.get('para_blocks', []):
            if block.get('type') != 'table':
                continue
            probes, _ = _block_probes_and_bbox(block)
            for probe in probes:
                for match in _TABLE_RE.finditer(probe):
                    html = match.group()
                    if md.count(html) == 1:
                        owners.setdefault(md.find(html), set()).add(page['page_idx'] + 1)
    for table in tables:
        owner = sorted(owners.get(table['start_pos'], []))
        if len(owner) != 1:
            continue
        actual = lookup_page_num(old, table['start_pos'], table['end_pos'])
        current = lookup_page_num(fresh, table['start_pos'], table['end_pos'])
        if str(owner[0]) != str(table['page_num']).split('-')[0] or str(owner[0]) != current.split('-')[0]:
            table_errors.append(dict(index=table['table_index'], name=table['table_name'],
                expected_start=owner[0], stored=table['page_num'], mapping=actual,
                rebuilt=current, start_pos=table['start_pos'], end_pos=table['end_pos']))
    return dict(file_id=row['file_id'], name=row['file_name'], pages=len(pages),
        page_idxs=[p.get('page_idx') for p in pages[:3]], anchors=len(old),
        rebuilt_anchors=len(fresh), mapping_changed=old != fresh,
        checks=checks, errors=errors, table_count=len(tables),
        table_groups=len(groups), markdown_tables=len(spans), table_errors=table_errors)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--limit', type=int, default=8)
    parser.add_argument('--file-id')
    parser.add_argument('--local-detail', action='store_true')
    parser.add_argument('--direct-check', action='store_true')
    parser.add_argument('--replay', action='store_true')
    parser.add_argument('--benchmark', action='store_true')
    args = parser.parse_args()
    if args.benchmark:
        benchmark()
        return
    if args.replay:
        replay()
        return
    if args.direct_check:
        direct_check()
        return
    if args.local_detail:
        local_detail()
        return
    cfg = yaml.safe_load(Path(os.environ.get('APP_CONFIG_PATH', ROOT / 'configs/config.yaml')).read_text(encoding='utf-8'))['mysql']
    conn = pymysql.connect(host=cfg['host'], port=cfg.get('port', 3306),
        user=cfg['username'], password=cfg['password'], database=cfg['database'],
        charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=12, read_timeout=90, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION READ ONLY')
            cur.execute('START TRANSACTION READ ONLY')
            cur.execute('SELECT COUNT(*) AS files FROM files')
            print(json.dumps(cur.fetchone()), flush=True)
            if not args.audit:
                cur.execute('SELECT f.file_id, f.file_name, f.create_time, f.progress, CHAR_LENGTH(c.middle_json) AS middle_chars, JSON_LENGTH(c.page_mapping) AS anchors FROM files f JOIN file_content c ON c.file_id=f.file_id ORDER BY f.create_time DESC LIMIT %s', (args.limit,))
                print(json.dumps(cur.fetchall(), ensure_ascii=False, default=str, indent=2))
                return
            if args.file_id:
                ids = [args.file_id]
            else:
                cur.execute('SELECT f.file_id FROM files f JOIN file_content c ON c.file_id=f.file_id WHERE c.middle_json IS NOT NULL ORDER BY f.create_time DESC LIMIT %s', (args.limit,))
                ids = [r['file_id'] for r in cur.fetchall()]
            out = ROOT / 'tmp/page_mapping_diagnosis'
            out.mkdir(exist_ok=True)
            reports = []
            for fid in ids:
                cur.execute('SELECT f.file_name, c.* FROM file_content c JOIN files f ON f.file_id=c.file_id WHERE c.file_id=%s', (fid,))
                row = cur.fetchone()
                cur.execute('SELECT table_index, table_name, page_num, start_pos, end_pos FROM file_table WHERE file_id=%s ORDER BY table_index', (fid,))
                tables = cur.fetchall()
                (out / f'{fid}.json').write_text(json.dumps(dict(row=row, tables=tables), ensure_ascii=False), encoding='utf-8')
                report = audit(row, tables)
                reports.append(report)
                print(json.dumps({**report, 'errors': report['errors'][:5], 'error_count': len(report['errors']), 'table_errors': report['table_errors'][:5], 'table_error_count': len(report['table_errors'])}, ensure_ascii=False), flush=True)
            (out / 'report.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        conn.rollback()
        conn.close()


if __name__ == '__main__':
    main()
