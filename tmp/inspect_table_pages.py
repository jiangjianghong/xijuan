import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from model.database import get_session_factory, get_engine
from model.tables import FileContent, FileTable
from service.table_name_utils import _build_llm_context_text

async def main():
    async with get_session_factory()() as s:
        fid = '4730012dc27e63e604c4a87ea313f14b'
        fc = (await s.execute(select(FileContent).where(FileContent.file_id == fid))).scalar_one()
        ts = (await s.execute(select(FileTable).where(FileTable.file_id == fid).order_by(FileTable.table_index))).scalars().all()
        rows = []
        for t in ts:
            if any(str(p) in str(t.page_num).split('-') for p in range(74, 78)):
                rows.append(dict(index=t.table_index, page=t.page_num, name=t.table_name,
                    preceding=fc.file_content[max(0, t.start_pos-2000):t.start_pos],
                    context=_build_llm_context_text(fc.file_content[:t.start_pos]), html=t.table_content))
        Path('tmp/table_75_76.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    await get_engine().dispose()

asyncio.run(main())
