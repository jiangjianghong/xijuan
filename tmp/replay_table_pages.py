import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import service.table_service as service
from utils.config import get_config

async def main():
    rows = json.loads(Path('tmp/table_75_76.json').read_text(encoding='utf-8'))[:2]
    real_chat = service.chat_completion
    async def capture(prompt, **kwargs):
        try:
            result = await real_chat(prompt, **kwargs)
        except Exception as exc:
            if getattr(exc, 'response', None) is not None:
                data = exc.response.json()
                print('API ERROR', data.get('error', {}).get('code'), data.get('error', {}).get('message'), flush=True)
            raise
        print('RAW', result, flush=True)
        return result
    service.chat_completion = capture
    cfg = get_config().table_name_validation
    print('context limits', cfg.max_context_lines, cfg.max_context_length, flush=True)
    for row in rows:
        result = await service._extract_table_name_with_llm(
            preceding_text=row['preceding'], table_index=row['index'],
            fallback_name=service._extract_table_name(row['preceding']), table_content=row['html'])
        print('FINAL', row['index'], result, flush=True)

asyncio.run(main())
