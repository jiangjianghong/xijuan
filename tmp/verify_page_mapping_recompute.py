"""批量重算后的只读抽查，不修改数据库。"""
import json
import os
import sys
from pathlib import Path

import pymysql
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.page_mapping import build_page_mapping, lookup_page_num


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    cfg = yaml.safe_load(Path(os.environ.get('APP_CONFIG_PATH', ROOT / 'configs/config.yaml')).read_text(encoding='utf-8'))['mysql']
    conn = pymysql.connect(host=cfg['host'], port=cfg.get('port', 3306),
        user=cfg['username'], password=cfg['password'], database=cfg['database'],
        charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=12, read_timeout=90, autocommit=False)
    counts = {'files': 0, 'tables': 0, 'chunks': 0, 'mismatches': []}
    try:
        with conn.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION READ ONLY')
            cur.execute('START TRANSACTION READ ONLY')
            original = json.loads((ROOT / 'tmp/page_mapping_diagnosis/report.json').read_text(encoding='utf-8'))
            ids = {r['file_id'] for r in original}
            cur.execute('SELECT file_id FROM file_content ORDER BY file_id LIMIT 20')
            ids.update(r['file_id'] for r in cur.fetchall())
            for fid in sorted(ids):
                cur.execute('SELECT file_content, middle_json, page_mapping FROM file_content WHERE file_id=%s', (fid,))
                row = cur.fetchone()
                if not row:
                    counts['mismatches'].append({'file_id': fid, 'kind': 'missing_file'})
                    continue
                mapping = decode(row['page_mapping']) or []
                expected = build_page_mapping(row['file_content'], row['middle_json'] or '')
                counts['files'] += 1
                if mapping != expected:
                    counts['mismatches'].append({'file_id': fid, 'kind': 'mapping'})
                for table, counter in [('file_table', 'tables'), ('file_chunk', 'chunks')]:
                    cur.execute(f'SELECT start_pos, end_pos, page_num FROM {table} WHERE file_id=%s', (fid,))
                    for item in cur.fetchall():
                        counts[counter] += 1
                        if mapping and lookup_page_num(mapping, item['start_pos'], item['end_pos']) != (item['page_num'] or ''):
                            counts['mismatches'].append({'file_id': fid, 'kind': counter, 'start_pos': item['start_pos']})
            print(json.dumps(counts, ensure_ascii=False, indent=2))
    finally:
        conn.rollback()
        conn.close()
    if counts['mismatches']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
