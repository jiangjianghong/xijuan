"""日志查看路由：/log/*"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from model.schemas import ResponseWrapper


router = APIRouter(prefix="/log", tags=["log"])

_LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"
_MAX_TAIL_LINES = 1000
_POLL_INTERVAL_SECONDS = 0.8
_HEARTBEAT_SECONDS = 15
_LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")


def _sse_event(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _log_files() -> list[Path]:
    if not _LOGS_DIR.exists():
        return []
    return sorted(
        (p for p in _LOGS_DIR.glob("app_*.log") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _latest_log_file() -> Path | None:
    files = _log_files()
    return files[0] if files else None


def _resolve_log_file(file_name: str | None) -> Path | None:
    if not file_name:
        return _latest_log_file()

    safe_name = Path(file_name).name
    candidate = (_LOGS_DIR / safe_name).resolve()
    logs_dir = _LOGS_DIR.resolve()

    if candidate.parent != logs_dir or not safe_name.startswith("app_") or candidate.suffix != ".log":
        raise HTTPException(status_code=400, detail="日志文件名不合法")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    return candidate


def _clamp_tail_lines(lines: int) -> int:
    return max(0, min(lines, _MAX_TAIL_LINES))


def _normalize_level(level: str | None) -> str:
    value = (level or "").strip().upper()
    if value and value not in _LOG_LEVELS:
        raise HTTPException(status_code=400, detail="日志等级不合法")
    return value


def _detect_level(line: str) -> str:
    parts = line.split(" | ", 3)
    if len(parts) >= 3:
        maybe_level = parts[1].strip().upper()
        if maybe_level in _LOG_LEVELS:
            return maybe_level
    return "INFO"


def _line_payload(path: Path, line: str, offset: int | None = None) -> dict:
    return {
        "file": path.name,
        "level": _detect_level(line),
        "line": line.rstrip("\r\n"),
        "offset": offset,
    }


def _match_level(line: str, level: str) -> bool:
    return not level or _detect_level(line) == level


def _tail_lines(path: Path, lines: int) -> Iterable[str]:
    if lines <= 0:
        return []

    buffer: deque[str] = deque(maxlen=lines)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            buffer.append(line)
    return buffer


@router.get("/files", response_model=ResponseWrapper)
async def list_log_files():
    """列出可查看的应用日志文件。"""
    files = _log_files()
    return ResponseWrapper(
        data={
            "current": files[0].name if files else None,
            "items": [
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "modified_at": path.stat().st_mtime,
                }
                for path in files
            ],
        }
    )


@router.get("/recent", response_model=ResponseWrapper)
async def get_recent_logs(
    file: str | None = None,
    lines: int = Query(default=200, ge=0, le=_MAX_TAIL_LINES),
    level: str | None = None,
):
    """读取最近若干行应用日志。"""
    level_filter = _normalize_level(level)
    path = _resolve_log_file(file)

    if path is None:
        return ResponseWrapper(data={"file": None, "lines": []})

    recent_lines = [
        _line_payload(path, line)
        for line in _tail_lines(path, _clamp_tail_lines(lines))
        if _match_level(line, level_filter)
    ]
    return ResponseWrapper(data={"file": path.name, "lines": recent_lines})


@router.get("/stream")
async def stream_logs(
    request: Request,
    file: str | None = None,
    tail: int = Query(default=200, ge=0, le=_MAX_TAIL_LINES),
    level: str | None = None,
):
    """实时推送应用日志。未指定 file 时自动跟随最新 app_*.log。"""
    level_filter = _normalize_level(level)
    initial_path = _resolve_log_file(file)
    follow_latest = not file

    async def event_generator():
        path = initial_path
        position = 0
        last_heartbeat = time.monotonic()

        if path is None:
            yield _sse_event("ready", {"file": None, "message": "暂无日志文件"})
        else:
            yield _sse_event("ready", {"file": path.name, "message": "日志连接已建立"})
            for line in _tail_lines(path, _clamp_tail_lines(tail)):
                if _match_level(line, level_filter):
                    yield _sse_event("line", _line_payload(path, line))
            position = path.stat().st_size

        while not await request.is_disconnected():
            if follow_latest:
                latest = _latest_log_file()
                if latest != path:
                    path = latest
                    position = 0
                    if path is not None:
                        yield _sse_event("rotated", {"file": path.name, "message": "已切换到最新日志"})

            if path is not None and path.exists():
                current_size = path.stat().st_size
                if current_size < position:
                    position = 0

                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(position)
                    while True:
                        line = fh.readline()
                        if not line:
                            break
                        position = fh.tell()
                        if _match_level(line, level_filter):
                            yield _sse_event("line", _line_payload(path, line, position))

            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                yield _sse_event("heartbeat", {"file": path.name if path else None, "ts": now})
                last_heartbeat = now

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
