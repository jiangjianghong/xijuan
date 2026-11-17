"""智能体对话上下文的估算、压缩与滑动窗口处理。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable


MEMORY_MARKER = "[xijuan_agent_memory]"


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """用序列化后的字符长度估算上下文 token 数。

    字符数除以 2 只用于窗口调度，并非模型 tokenizer 的精确计数或硬保证。
    图片按每页 1536 tokens 估算，实际消耗取决于模型及分辨率。
    """
    total = 0
    for message in messages:
        image_count = 0
        normalized = dict(message)
        content = normalized.get("content")
        if isinstance(content, list):
            safe_content = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    image_count += 1
                    safe_content.append({"type": "image_url", "image_url": {"url": "[PDF page image]"}})
                else:
                    safe_content.append(item)
            normalized["content"] = safe_content
        try:
            encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            encoded = str(normalized)
        total += len(encoded) + image_count * 1536 * 2
    return max(1, (total + 1) // 2)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif item.get("type") == "image_url":
                parts.append("[PDF 页图已提供给视觉模型]")
        return "\n".join(parts)
    return str(content or "")


def serialize_for_summary(messages: list[dict[str, Any]], max_chars: int = 50000) -> str:
    """把待压缩消息变成不包含图片 data URL 的可读文本。"""
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "unknown")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                calls.append(
                    f"{function.get('name') or 'unknown'}({function.get('arguments') or '{}'})"
                )
            text = "调用工具：" + "; ".join(calls)
        else:
            text = _content_text(message.get("content"))
        if role == "tool":
            call_id = message.get("tool_call_id") or ""
            text = f"工具结果 {call_id}：{text}"
        lines.append(f"{role}: {text}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        marker = "\n[中间部分因摘要输入预算省略]\n"
        half = max(0, (max_chars - len(marker)) // 2)
        text = text[:half] + marker + (text[-half:] if half else "")
    return text


def remove_image_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """移除视觉输入中的 data URL，供浏览器内存状态保存。"""
    result: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "image_url" for item in content
        ):
            text = _content_text(content)
            result.append({"role": message.get("role", "user"), "content": text})
        else:
            result.append(message)
    return result


def _is_image_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    )


def _is_memory(message: dict[str, Any]) -> bool:
    return message.get("role") == "system" and str(message.get("content") or "").startswith(MEMORY_MARKER)


def _is_question(message: dict[str, Any]) -> bool:
    return (
        message.get("role") == "user"
        and not _is_image_message(message)
        and not _content_text(message.get("content")).startswith("以下是 pdf_reader 返回的原始 PDF 页图")
    )


def _tool_groups(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """只在完整工具调用组边界切分，同时带走其后的 PDF 视觉输入。"""
    groups = []
    for start, message in enumerate(messages):
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            continue
        end = start + 1
        replies = set()
        while end < len(messages) and messages[end].get("role") == "tool":
            replies.add(messages[end].get("tool_call_id"))
            end += 1
        if not all(call.get("id") in replies for call in calls):
            continue
        while end < len(messages) and (
            _is_image_message(messages[end])
            or (
                messages[end].get("role") == "user"
                and _content_text(messages[end].get("content")).startswith("以下是 pdf_reader 返回的原始 PDF 页图")
            )
        ):
            end += 1
        groups.append((start, end))
    return groups


async def _safe_summary(
    summarize: Callable[[str], Awaitable[str]], transcript: str
) -> str:
    try:
        summary = (await summarize(transcript)).strip()
    except Exception:
        summary = ""
    if summary:
        return summary
    # 摘要服务不可用时仍能继续研究；保留较新的证据片段并明确其为回退摘要。
    excerpt = transcript[-5000:]
    return "[模型摘要不可用，保留较近的旧证据片段]\n" + excerpt


@dataclass(frozen=True)
class CompactionPlan:
    prefix: list[dict[str, Any]]
    retained: list[dict[str, Any]]
    transcript: str
    fingerprint: str
    summary_chars: int

    def apply(self, messages: list[dict[str, Any]], summary: str) -> bool:
        """仅替换创建任务时的前缀，保留后台任务运行期间新增的所有消息。"""
        if messages[:len(self.prefix)] != self.prefix:
            return False
        memory = {
            "role": "system",
            "content": f"{MEMORY_MARKER}\n较早对话与工具证据摘要（需以当前证据核对）：\n{summary[:self.summary_chars]}",
        }
        retained = list(self.retained)
        insert_at = 1 if retained and retained[0].get("role") == "system" else 0
        retained.insert(insert_at, memory)
        result = retained + messages[len(self.prefix):]
        if estimate_tokens(result) >= estimate_tokens(messages):
            return False
        messages[:] = result
        return True


def plan_compaction(
    messages: list[dict[str, Any]], *, recent_tool_rounds: int, force: bool = False,
) -> CompactionPlan | None:
    groups = _tool_groups(messages)
    questions = [index for index, message in enumerate(messages) if _is_question(message)]
    latest_question = questions[-1] if questions else -1
    if force and groups:
        # 从最早完整组开始兜底，避免无谓丢掉其他仍可原样保留的结果。
        start, cutoff = groups[0]
        if len(groups) == 1 and any(_is_image_message(item) for item in messages[start:cutoff]):
            # 末尾页图尚未被下一次模型请求消费，不能只摘要成文字占位符。
            return None
    elif groups:
        cutoff = groups[max(0, len(groups) - max(1, recent_tool_rounds))][0]
    else:
        cutoff = max(0, latest_question)
    prefix = messages[:cutoff]
    retained = [
        message for index, message in enumerate(prefix)
        if index == latest_question or (message.get("role") == "system" and not _is_memory(message))
    ]
    removed = [
        message for index, message in enumerate(prefix)
        if index != latest_question and (message.get("role") != "system" or _is_memory(message))
    ]
    # 只有记忆或当前问题时不能继续摘要自己，否则会每轮重复整理。
    if not any(not _is_memory(message) for message in removed):
        return None
    transcript = serialize_for_summary([
        message for message in prefix if message.get("role") != "system" or _is_memory(message)
    ])
    fingerprint = hashlib.sha256(json.dumps(prefix, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return CompactionPlan(
        prefix=copy.deepcopy(prefix), retained=copy.deepcopy(retained), transcript=transcript,
        fingerprint=fingerprint, summary_chars=min(6000, len(serialize_for_summary(removed)) // 2),
    )


class ContextWindow:
    """每个请求最多一个后台摘要任务，在模型请求边界安全合并结果。"""

    def __init__(self, *, threshold_tokens: int, max_tokens: int, recent_tool_rounds: int,
                 summarize: Callable[[str], Awaitable[str]]) -> None:
        self.threshold_tokens = threshold_tokens
        self.max_tokens = max_tokens
        self.recent_tool_rounds = recent_tool_rounds
        self.summarize = summarize
        self.task: asyncio.Task[str] | None = None
        self.plan: CompactionPlan | None = None
        self.attempted: set[str] = set()
        self.sequence = 0

    def _apply_ready(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        if self.task is None or not self.task.done():
            return None
        summary = self.task.result()
        changed = self.plan.apply(messages, summary) if self.plan else False
        self.task = None
        self.plan = None
        return {"type": "context_compact", "state": "completed" if changed else "skipped",
                "task_id": self.sequence, "estimated_tokens": estimate_tokens(messages)}

    async def prepare(self, messages: list[dict[str, Any]], *, overhead_tokens: int = 0) -> AsyncIterator[dict[str, Any]]:
        """超过阈值即启动后台任务；只有预计请求越过上限才等待摘要。"""
        while True:
            completed = self._apply_ready(messages)
            if completed:
                yield completed
            tokens = estimate_tokens(messages) + overhead_tokens
            over_budget = tokens > self.max_tokens
            if self.task is not None:
                if not over_budget:
                    return
                yield {"type": "context_compact", "state": "waiting", "task_id": self.sequence}
                await self.task
                continue
            if tokens <= self.threshold_tokens and not over_budget:
                return
            plan = plan_compaction(messages, recent_tool_rounds=self.recent_tool_rounds)
            if over_budget and (plan is None or plan.fingerprint in self.attempted):
                plan = plan_compaction(messages, recent_tool_rounds=self.recent_tool_rounds, force=True)
            if plan is None or plan.fingerprint in self.attempted:
                if over_budget:
                    raise ValueError(
                        "整理后预计请求仍超过 Agent 上下文上限，已停止发送；"
                        "请调高上下文上限，或减少单次工具结果、PDF 页数及输出 Token。"
                    )
                return
            self.attempted.add(plan.fingerprint)
            self.plan = plan
            self.sequence += 1
            self.task = asyncio.create_task(_safe_summary(self.summarize, plan.transcript))
            yield {"type": "context_compact", "state": "started", "task_id": self.sequence}
            if not over_budget:
                return

    async def finish(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        completed = self._apply_ready(messages)
        if self.task is not None:
            await self.close()
            return {"type": "context_compact", "state": "cancelled", "task_id": self.sequence}
        return completed

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
            self.plan = None


async def compact_messages(
    messages: list[dict[str, Any]],
    *,
    threshold_tokens: int,
    max_tokens: int,
    recent_tool_rounds: int,
    summarize: Callable[[str], Awaitable[str]],
) -> tuple[list[dict[str, Any]], bool, int]:
    """压缩旧工具结果并返回 `(messages, changed, estimated_tokens)`。

    最近若干个工具调用组完整保留；更早的消息整体交给同一 Agent 模型摘要，
    摘要以 system memory 消息注入，浏览器后续请求只需携带这份小状态。
    """
    # 同步兼容入口；实际 Agent 使用 ContextWindow 的后台调度。
    result = copy.deepcopy(messages)
    window = ContextWindow(threshold_tokens=threshold_tokens, max_tokens=max_tokens,
                           recent_tool_rounds=recent_tool_rounds, summarize=summarize)
    try:
        async for _event in window.prepare(result):
            pass
        if window.task is not None:
            await window.task
            window._apply_ready(result)
        return result, result != messages, estimate_tokens(result)
    finally:
        await window.close()


def sanitize_history(history: Any, *, max_messages: int = 240) -> list[dict[str, Any]]:
    """校验浏览器传回的模型消息，避免把任意 role 或超大结构直接转发。"""
    if not isinstance(history, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in history:
        if not isinstance(raw, dict):
            continue
        role = raw.get("role")
        if role not in {"user", "assistant", "tool", "system"}:
            continue
        item: dict[str, Any] = {"role": role}
        # 工具结果须覆盖设置允许的最大值，避免下次提问又被旧的 60000 字符限制静默截断。
        content_limit = 200000 if role == "tool" else 60000
        if role == "assistant" and raw.get("tool_calls"):
            calls = raw.get("tool_calls")
            if isinstance(calls, list):
                item["tool_calls"] = calls[:20]
            item["content"] = raw.get("content") if isinstance(raw.get("content"), str) else None
        else:
            content = raw.get("content")
            if isinstance(content, str):
                item["content"] = content[:content_limit]
            elif isinstance(content, list):
                text = _content_text(content)
                item["content"] = text[:content_limit]
            else:
                item["content"] = str(content or "")[:content_limit]
        if role == "tool" and raw.get("tool_call_id"):
            item["tool_call_id"] = str(raw["tool_call_id"])[:160]
        result.append(item)
    while len(result) > max_messages:
        users = [index for index, item in enumerate(result) if item.get("role") == "user"]
        if len(users) < 2:
            break
        result = [item for item in result if item.get("role") == "system"] + result[users[1]:]
    return result
