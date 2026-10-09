"""临时实测：analysis（deepseek）+ GLM 对照。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.config import get_config
from utils.llm_client import chat_completion


async def main() -> None:
    cfg = get_config()
    print("analysis.model =", cfg.analysis.model)
    print("analysis.enable_thinking =", cfg.analysis.enable_thinking)
    text = await chat_completion("reply with: ok", config_group="analysis")
    print("ANALYSIS RESULT:", text[:200])

    text2 = await chat_completion("reply with: ok", config_group="extraction")
    print("EXTRACTION RESULT:", text2[:200])


if __name__ == "__main__":
    asyncio.run(main())
