"""本次全库重算临时调度器：复用既有脚本，四个互斥批次并发提交。"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import select
from model.database import get_engine, get_session_factory
from model.tables import FileContent
from scripts.recompute_page_mapping_batch import _process_batch


async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    factory = get_session_factory()
    async with factory() as session:
        ids = list((await session.execute(select(FileContent.file_id).order_by(FileContent.file_id))).scalars())
    queue = asyncio.Queue()
    for start in range(0, len(ids), 5):
        queue.put_nowait(ids[start:start + 5])
    summary = dict(total=len(ids), processed=0, changed=0, tables=0, chunks=0, skipped=0, failed=[])
    args = SimpleNamespace(dry_run=False)
    print(json.dumps({'started': summary['total'], 'workers': 4}), flush=True)

    async def worker():
        while not queue.empty():
            batch = queue.get_nowait()
            for attempt in range(1, 6):
                counters = dict(done=0, skipped=0, changed=[], failed=[])
                try:
                    await _process_batch(factory, batch, args, counters, len(ids))
                    summary['processed'] += counters['done']
                    summary['skipped'] += counters['skipped']
                    summary['failed'].extend(counters['failed'])
                    summary['changed'] += len(counters['changed'])
                    summary['tables'] += sum(c['tables_repaged'] for c in counters['changed'])
                    summary['chunks'] += sum(c['chunks_repaged'] for c in counters['changed'])
                    break
                except Exception as exc:
                    print(json.dumps({'retry': attempt, 'first_id': batch[0], 'error_type': type(exc).__name__}), flush=True)
                    if attempt == 5:
                        summary['failed'].extend(batch)
                        summary['processed'] += len(batch)
                    else:
                        await asyncio.sleep(min(2 ** attempt, 16))
            print('PROGRESS ' + json.dumps(summary), flush=True)
            queue.task_done()

    try:
        await asyncio.gather(*(worker() for _ in range(4)))
    finally:
        await get_engine().dispose()
    (ROOT / 'tmp/page_mapping_recompute_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print('FINAL ' + json.dumps(summary), flush=True)
    return bool(summary['failed'])


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
