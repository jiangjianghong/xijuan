"""进程内并发运行时只读监控接口。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from model.schemas import ResponseWrapper
from utils.concurrency import get_limiter, runtime_snapshot
from utils.config import get_config


router = APIRouter(prefix="/runtime", tags=["runtime"])

_GLOBAL_POOLS = (
    ("global_llm", "文本 LLM", "模型通道", "模型通道"),
    ("global_embedding", "Embedding", "模型通道", "模型通道"),
    ("global_vl", "VL 视觉", "模型通道", "模型通道"),
    ("global_table_validation", "表名校验", "业务阶段", "表名校验"),
    ("global_extraction", "字段抽取", "业务阶段", "字段抽取"),
    ("global_analysis", "逻辑分析总池", "业务阶段", "逻辑分析"),
    ("independent_analysis", "独立分析", "独立接口", "独立分析"),
)
_TASK_POOLS = (
    ("task_table_validation", "文件内表名校验", "文件内任务", "单文件表名"),
    ("task_extraction", "文件内字段抽取", "文件内任务", "单文件抽取"),
    ("task_file_analysis", "文件内逻辑分析", "文件内任务", "单文件分析"),
)
_POOL_CONSTRAINTS = {
    "global_table_validation": ["global_llm"],
    "global_extraction": ["global_llm", "global_embedding", "global_vl"],
    "global_analysis": ["global_llm"],
    "task_table_validation": ["global_table_validation", "global_llm"],
    "task_extraction": ["global_extraction"],
    "task_file_analysis": ["global_analysis"],
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
                "wait_p95_ms": float(item.get("wait_p95_ms", 0)),
                "status": _status(active, limit, queued),
                "constraints": _POOL_CONSTRAINTS.get(pool_id, []),
                "tasks": list(item.get("holders", [])),
            }
        )
    return records


def _task_pool_records(raw: dict, limits: dict[str, int]) -> list[dict]:
    records = []
    for pool_id, label, group, _ in _TASK_POOLS:
        item = dict(raw.get(pool_id) or {})
        limit = int(item.get("per_instance_limit", limits[pool_id]))
        busiest = int(item.get("busiest_active", 0))
        aggregate_queued = int(item.get("aggregate_queued", 0))
        instances = []
        for instance in item.get("instances", []):
            instance = dict(instance)
            instance_active = int(instance.get("active", 0))
            instance_queued = int(instance.get("queued", 0))
            instance["status"] = _status(instance_active, limit, instance_queued)
            instances.append(instance)
        records.append(
            {
                "id": pool_id,
                "label": label,
                "group": group,
                "scope": "task",
                "per_instance_limit": limit,
                "instance_count": int(item.get("instance_count", 0)),
                "busiest_active": busiest,
                "aggregate_active": int(item.get("aggregate_active", 0)),
                "aggregate_queued": aggregate_queued,
                "status": _status(busiest, limit, aggregate_queued),
                "constraints": _POOL_CONSTRAINTS.get(pool_id, []),
                "instances": instances,
            }
        )
    return records


@router.get("/concurrency", response_model=ResponseWrapper)
async def get_concurrency_snapshot():
    """返回当前 worker 的并发池和局部实例快照。"""
    limits = get_config().concurrency.model_dump(mode="python")
    for pool_id, _, _, _ in _GLOBAL_POOLS:
        get_limiter(pool_id, int(limits[pool_id]))

    raw = runtime_snapshot()
    global_records = _global_pool_records(raw.get("pools", {}), limits)
    task_records = _task_pool_records(raw.get("task_pools", {}), limits)
    pipeline_record = {
        "id": "global_pipeline",
        "label": "文件管线",
        "group": "管线调度",
        "scope": "global",
        "limit": int(limits["global_pipeline"]),
        "active": 0,
        "queued": 0,
        "completed": 0,
        "wait_p95_ms": 0,
        "status": "offline",
        "connected": False,
        "constraints": [],
        "tasks": [],
        "note": "当前配置尚未接入文件管线调度。",
    }
    global_for_summary = [item for item in global_records]
    summary = {
        "active": sum(item["active"] for item in global_for_summary),
        "capacity": sum(item["limit"] for item in global_for_summary),
        "queued": sum(item["queued"] for item in global_for_summary),
        "hot_pools": sum(item["status"] in {"pressure", "saturated"} for item in global_for_summary),
        "wait_p95_ms": max((item["wait_p95_ms"] for item in global_for_summary), default=0),
    }
    return ResponseWrapper(
        data={
            "updated_at": datetime.now().astimezone().isoformat(),
            "scope": "single-process",
            "summary": summary,
            "pools": [*global_records, *task_records, pipeline_record],
            "events": list(reversed(raw.get("events", [])))[:20],
        }
    )
