"""并发运行台的快照构建与进程内历史采样。

快照口径（池清单、状态判定、汇总）集中在本模块，供 blue_print/runtime_router.py
与后台采样循环共用——两者必须同源，否则历史曲线与实时容量矩阵会对不上。
历史是纯内存环形缓冲（按事件循环隔离），进程重启即清零，不落库。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime
from typing import Any

from loguru import logger

from utils.concurrency import get_limiter, runtime_snapshot
from utils.config import get_config


SAMPLE_INTERVAL_S = 1.0
HISTORY_MAXLEN = 1800      # 1s 一点，保留 30 分钟
HISTORY_POINTS = 60        # 每个窗口恒返回 60 个桶，响应体积与窗口长度无关
WINDOWS: dict[str, int] = {"60s": 60, "5m": 300, "30m": 1800}
DEFAULT_WINDOW = "60s"

# 按事件循环隔离，与 utils/concurrency.py 的 limiter 注册表同构，避免测试串味
_history: dict[int, deque[dict[str, Any]]] = {}

_GLOBAL_POOLS = (
    ("global_llm", "文本 LLM", "模型通道", "模型通道"),
    ("global_embedding", "Embedding", "模型通道", "模型通道"),
    ("global_vl", "VL 视觉", "模型通道", "模型通道"),
    ("global_table_validation", "表名校验", "业务阶段", "表名校验"),
    ("global_extraction", "字段抽取", "业务阶段", "字段抽取"),
    ("global_analysis", "逻辑分析总池", "业务阶段", "逻辑分析"),
    ("independent_analysis", "独立分析", "独立接口", "独立分析"),
)
# 单文件池（task_table_validation / task_extraction / task_file_analysis）
# 仍在 utils/concurrency.py 真实限流，但不在运行台展示：它们的每实例上限
# 与对应全局池同量级时常年显示 100% 饱和，而全局池远未跑满，图与实情相反。
# 被它们吸收的排队改由各全局池的 total_wait_p95_ms 暴露。
_TASK_POOL_IDS = ("task_table_validation", "task_extraction", "task_file_analysis")
_POOL_CONSTRAINTS = {
    "global_table_validation": ["global_llm"],
    "global_extraction": ["global_llm", "global_embedding", "global_vl"],
    "global_analysis": ["global_llm"],
    "independent_analysis": ["global_analysis"],
}


def _status(active: int, limit: int, queued: int, connected: bool = True) -> str:
    if not connected:
        return "offline"
    if active >= limit and queued > 0:
        return "saturated"
    if active == 0:
        return "idle"
    if active / max(limit, 1) >= 0.75 or queued >= 2:
        return "pressure"
    return "normal"


def _global_pool_records(raw: dict, limits: dict[str, int]) -> list[dict]:
    records = []
    for pool_id, label, group, _ in _GLOBAL_POOLS:
        limit = int(limits[pool_id])
        item = dict(raw.get(pool_id) or {})
        active = int(item.get("active", 0))
        queued = int(item.get("queued", 0))
        records.append(
            {
                "id": pool_id,
                "label": label,
                "group": group,
                "scope": "global",
                "limit": limit,
                "active": active,
                "queued": queued,
                "completed": int(item.get("completed", 0)),
                "gate_wait_p95_ms": float(item.get("gate_wait_p95_ms", 0)),
                "total_wait_p95_ms": float(item.get("total_wait_p95_ms", 0)),
                "status": _status(active, limit, queued),
                "constraints": _POOL_CONSTRAINTS.get(pool_id, []),
                "tasks": list(item.get("holders", [])),
            }
        )
    return records


def _pipeline_record(raw: dict, limits: dict[str, int]) -> dict:
    """global_pipeline 已由 service/pipeline_gate.py 真实接入，读 limiter 实时水位。"""
    pipeline_raw = dict(raw.get("global_pipeline") or {})
    limit = int(limits["global_pipeline"])
    active = int(pipeline_raw.get("active", 0))
    queued = int(pipeline_raw.get("queued", 0))
    return {
        "id": "global_pipeline",
        "label": "文件管线",
        "group": "管线调度",
        "scope": "global",
        "limit": limit,
        "active": active,
        "queued": queued,
        "completed": int(pipeline_raw.get("completed", 0)),
        "gate_wait_p95_ms": float(pipeline_raw.get("gate_wait_p95_ms", 0)),
        "total_wait_p95_ms": float(pipeline_raw.get("total_wait_p95_ms", 0)),
        "status": _status(active, limit, queued),
        "connected": True,
        "constraints": [],
        "tasks": list(pipeline_raw.get("holders", [])),
        "note": "上传与重试的六个入口全程持有令牌，超限文件落 queued 排队。",
    }


def build_snapshot() -> dict[str, Any]:
    """构建当前 worker 的并发快照（不含 history）。"""
    limits = get_config().concurrency.model_dump(mode="python")
    for pool_id, _, _, _ in _GLOBAL_POOLS:
        get_limiter(pool_id, int(limits[pool_id]))
    # 闸门池不在 _GLOBAL_POOLS（它单独成组展示），但同样要预注册，
    # 否则首个文件进管线之前快照里读不到这个池
    get_limiter("global_pipeline", int(limits["global_pipeline"]))

    raw = runtime_snapshot()
    pools_raw = raw.get("pools", {})
    global_records = _global_pool_records(pools_raw, limits)
    summary = {
        "active": sum(item["active"] for item in global_records),
        "capacity": sum(item["limit"] for item in global_records),
        "queued": sum(item["queued"] for item in global_records),
        "hot_pools": sum(
            item["status"] in {"pressure", "saturated"} for item in global_records
        ),
        # 取端到端口径：本闸等待会被上游闸削平，不能代表最坏等待
        "total_wait_p95_ms": max(
            (item["total_wait_p95_ms"] for item in global_records), default=0
        ),
    }
    # 单文件池不展示，其事件也一并滤掉，避免事件流里冒出没有对应卡片的池
    events = [
        event
        for event in reversed(raw.get("events", []))
        if event.get("pool_id") not in _TASK_POOL_IDS
    ]
    return {
        "updated_at": datetime.now().astimezone().isoformat(),
        "scope": "single-process",
        "summary": summary,
        "pools": [*global_records, _pipeline_record(pools_raw, limits)],
        "events": events[:20],
    }


def _history_deque() -> deque[dict[str, Any]]:
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    return _history.setdefault(loop_id, deque(maxlen=HISTORY_MAXLEN))


def history_points() -> list[dict[str, Any]]:
    """当前事件循环内已采集的原始点（从旧到新），供测试与调试使用。"""
    return list(_history_deque())


def clear_history() -> None:
    """清空当前事件循环的历史，供测试使用。"""
    _history_deque().clear()


def pool_pressure(record: dict[str, Any]) -> int:
    """池利用率（0-100），与容量矩阵柱高同源。"""
    limit = int(record.get("limit", 0) or 0)
    active = int(record.get("active", 0) or 0)
    if limit <= 0:
        return 0
    return max(0, min(100, round(active / limit * 100)))


def record_sample(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """采集一个历史点并入队。传入 snapshot 可复用已构建的快照（测试也走这条路）。"""
    snapshot = snapshot if snapshot is not None else build_snapshot()
    summary = snapshot.get("summary") or {}
    capacity = int(summary.get("capacity", 0) or 0)
    active = int(summary.get("active", 0) or 0)
    point = {
        "at": time.time(),
        "overall": max(0, min(100, round(active / capacity * 100))) if capacity > 0 else 0,
        "pools": {
            record["id"]: pool_pressure(record)
            for record in snapshot.get("pools", [])
            if record.get("id")
        },
    }
    _history_deque().append(point)
    return point


def _merge_bucket(items: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """桶内取峰值：压力监控关心尖峰，均值会把短时饱和抹平。整桶无采样返回 None。"""
    real = [item for item in items if item]
    if not real:
        return None
    pools: dict[str, int] = {}
    for item in real:
        for pool_id, value in (item.get("pools") or {}).items():
            pools[pool_id] = max(pools.get(pool_id, 0), int(value))
    return {
        "at": real[-1].get("at"),
        "overall": max(int(item.get("overall", 0)) for item in real),
        "pools": pools,
    }


def history_payload(window: str | None = None) -> dict[str, Any]:
    """按窗口降采样成定长 60 桶。不足部分在左侧补 None，最新值恒在最右。"""
    key = window if window in WINDOWS else DEFAULT_WINDOW
    span = WINDOWS[key]
    bucket = max(1, span // HISTORY_POINTS)
    recent = list(_history_deque())[-span:]
    padded: list[dict[str, Any] | None] = [None] * (span - len(recent)) + list(recent)
    points = [
        _merge_bucket(padded[index * bucket : (index + 1) * bucket])
        for index in range(HISTORY_POINTS)
    ]
    return {
        "window": key,
        "window_seconds": span,
        "bucket_seconds": bucket,
        "interval_ms": int(SAMPLE_INTERVAL_S * 1000),
        "retention_seconds": HISTORY_MAXLEN,
        "windows": list(WINDOWS),
        "points": points,
    }


async def sampler_loop() -> None:
    """后台采样循环：每 SAMPLE_INTERVAL_S 秒记一个点。

    单轮失败只记日志不杀循环；收到取消向上抛出以便优雅退出。
    """
    while True:
        await asyncio.sleep(SAMPLE_INTERVAL_S)
        try:
            record_sample()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("并发历史采样失败: {}", e)
