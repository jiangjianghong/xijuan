"""智能体对话上下文的估算、压缩与滑动窗口处理。"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


MEMORY_MARKER = "[xijuan_agent_memory]"


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """用 UTF-8 文本长度估算上下文 token 数。

    Agent 需要兼容中文和英文模型，字符数除以 2 是偏保守的上限估算，
    用来触发窗口压缩而不是计费，因此不依赖具体 tokenizer。
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
        text = text[:max_chars] + "\n[更早内容已截断]"
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


def _tool_group_starts(messages: list[dict[str, Any]]) -> list[int]:
    return [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]


def _previous_user_start(messages: list[dict[str, Any]], index: int) -> int:
    for cursor in range(index - 1, -1, -1):
        if messages[cursor].get("role") == "user":
            return cursor
    return index


def _is_image_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    )


def _memory_index(messages: list[dict[str, Any]]) -> int | None:
    for index, message in enumerate(messages):
        if message.get("role") == "system" and str(message.get("content") or "").startswith(MEMORY_MARKER):
            return index
    return None


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


async def _fit_budget(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    summarize: Callable[[str], Awaitable[str]],
) -> list[dict[str, Any]]:
    """超出硬上限時，整組摘要最早工具證據或最早完整對话轮。"""
    result = list(messages)
    while estimate_tokens(result) > max_tokens:
        starts = _tool_group_starts(result)
        start = starts[0] if starts else None
        if start is not None:
            end = start + 1
            while end < len(result) and result[end].get("role") == "tool":
                end += 1
            while end < len(result) and _is_image_message(result[end]):
                end += 1
            removed = result[start:end]
            result = result[:start] + result[end:]
        else:
            users = [index for index, item in enumerate(result) if item.get("role") == "user"]
            if len(users) < 2:
                break
            start, end = users[0], users[1]
            removed = result[start:end]
            result = result[:start] + result[end:]

        memory_index = _memory_index(result)
        prior_memory = result[memory_index].get("content", "") if memory_index is not None else ""
        transcript = "\n\n".join(part for part in (str(prior_memory), serialize_for_summary(removed)) if part)
        summary = await _safe_summary(summarize, transcript or "较早工具证据已收纳。")
        memory = {
            "role": "system",
            "content": f"{MEMORY_MARKER}\n较早对话与工具结果摘要：\n{summary}",
        }
        if memory_index is None:
            insert_at = 1 if result and result[0].get("role") == "system" else 0
            result.insert(insert_at, memory)
        else:
            result[memory_index] = memory

    return result


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
    if estimate_tokens(messages) <= threshold_tokens:
        return messages, False, estimate_tokens(messages)

    if not messages:
        return messages, False, 0

    system = messages[0] if messages[0].get("role") == "system" else None
    rest = messages[1:] if system else list(messages)
    starts = _tool_group_starts(rest)
    # 单条新问题本身不应被压缩成记忆，否则模型会失去当前用户目标。
    if not starts and len(rest) <= 1:
        return messages, False, estimate_tokens(messages)
    if starts:
        keep_start = starts[max(0, len(starts) - max(1, recent_tool_rounds))]
        keep_start = _previous_user_start(rest, keep_start)
    else:
        keep_start = max(1, len(rest) - 4)
        keep_start = _previous_user_start(rest, keep_start)

    prefix = rest[:keep_start]
    tail = rest[keep_start:]
    compacted = list(messages)
    if prefix:
        summary_input = serialize_for_summary(prefix)
        summary = await _safe_summary(summarize, summary_input)
        memory = {
            "role": "system",
            "content": f"{MEMORY_MARKER}\n以下是较早对话与工具证据的摘要，请把它当作背景记忆，结论仍需以当前工具结果核对：\n{summary}",
        }
        compacted = ([system] if system else []) + [memory] + tail
    compacted = await _fit_budget(compacted, max_tokens=max_tokens, summarize=summarize)
    return compacted, True, estimate_tokens(compacted)


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
        if role == "assistant" and raw.get("tool_calls"):
            calls = raw.get("tool_calls")
            if isinstance(calls, list):
                item["tool_calls"] = calls[:20]
            item["content"] = raw.get("content") if isinstance(raw.get("content"), str) else None
        else:
            content = raw.get("content")
            if isinstance(content, str):
                item["content"] = content[:60000]
            elif isinstance(content, list):
                text = _content_text(content)
                item["content"] = text[:60000]
            else:
                item["content"] = str(content or "")[:60000]
        if role == "tool" and raw.get("tool_call_id"):
            item["tool_call_id"] = str(raw["tool_call_id"])[:160]
        result.append(item)
    while len(result) > max_messages:
        users = [index for index, item in enumerate(result) if item.get("role") == "user"]
        if len(users) < 2:
            break
        result = [item for item in result if item.get("role") == "system"] + result[users[1]:]
    return result
