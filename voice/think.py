"""Deepgram Think request body.

Think accepts an OpenAI-shaped chat completion and a fixed set of top-level
fields. https://developers.deepgram.com/docs/llm/request-response-format
"""

import json
from collections.abc import Iterable
from typing import Any

THINK_URL = "https://agent.deepgram.com/v1/think"
ALLOWED_FIELDS = {"model", "messages", "temperature", "reasoning_effort", "tools", "stream", "stream_options"}


class ThinkRequestError(Exception):
    """The sanitized Think body is missing a user message."""


class ThinkAssembler:
    """Turn `/v1/think` server-sent lines into completion chunks."""

    def __init__(self) -> None:
        self.saw_done = False

    def feed(self, line: str) -> dict[str, Any] | str:
        """Return a chunk, `ignore`, or `done`."""
        if self.saw_done:
            return "ignore"
        data = think_data(line.rstrip("\r"))
        if data is None:
            return "ignore"
        if data == "[DONE]":
            self.saw_done = True
            return "done"
        chunk = json.loads(data)
        if not isinstance(chunk, dict):
            raise TypeError("Think chunk must be an object")
        return chunk

    def close(self) -> None:
        if not self.saw_done:
            raise RuntimeError("Think stream ended without [DONE]")


def think_request(*, model: str, messages: list[Any], tools: list[Any] | None, temperature: float | None) -> dict[str, Any]:
    """Build the only JSON object this demo sends to Think."""
    sanitized = [sanitize_message(message) for message in messages]
    if not any(message.get("role") == "user" for message in sanitized):
        raise ThinkRequestError("Think request rejected: no user message")
    body: dict[str, Any] = {
        "model": model,
        "messages": sanitized,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = [sanitize_tool(tool) for tool in tools]
    extra = set(body) - ALLOWED_FIELDS
    if extra:
        raise RuntimeError("Think request contains rejected fields")
    return body


def sanitize_message(message: Any) -> dict[str, Any]:
    """Keep the message fields Think documents for each role."""
    raw = _as_dict(message)
    role = raw.get("role")
    if role == "assistant":
        cleaned: dict[str, Any] = {"role": "assistant"}
        content = _content(raw.get("content")) if "content" in raw else None
        if content is not None:
            cleaned["content"] = content
        if raw.get("tool_calls"):
            cleaned["tool_calls"] = [_tool_call(call) for call in raw["tool_calls"]]
        return cleaned
    if role == "tool":
        cleaned = {"role": "tool", "content": _content(raw.get("content")) or "", "tool_call_id": raw["tool_call_id"]}
        if raw.get("name"):
            cleaned["name"] = raw["name"]
        return cleaned
    if role == "developer":
        return {"role": "system", "content": _content(raw.get("content")) or ""}
    if role in ("system", "user"):
        return {"role": role, "content": _content(raw.get("content")) or ""}
    raise ValueError(f"unsupported message role {role}")


def sanitize_tool(tool: Any) -> dict[str, Any]:
    """Keep one function tool in the OpenAI shape Think accepts."""
    raw = _as_dict(tool)
    function = _as_dict(raw.get("function") or {})
    cleaned: dict[str, Any] = {
        "type": "function",
        "function": {"name": function["name"], "parameters": function.get("parameters") or {"type": "object", "properties": {}}},
    }
    if function.get("description"):
        cleaned["function"]["description"] = function["description"]
    return cleaned


def think_data(line: str) -> str | None:
    """Return the data payload of one SSE line, or None when the line is not data."""
    if not line.startswith("data:"):
        return None
    value = line[5:]
    if value.startswith(" "):
        value = value[1:]
    return value


def collect_think_chunks(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Read a complete Think stream. A missing `[DONE]` is a failed response."""
    assembler = ThinkAssembler()
    chunks = []
    for line in lines:
        item = assembler.feed(line)
        if item == "ignore":
            continue
        if item == "done":
            break
        chunks.append(item)
    assembler.close()
    return chunks


def _tool_call(call: Any) -> dict[str, Any]:
    raw = _as_dict(call)
    function = _as_dict(raw.get("function") or {})
    arguments = function.get("arguments", "")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, separators=(",", ":"))
    return {"id": raw["id"], "type": "function", "function": {"name": function["name"], "arguments": arguments}}


def _content(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                raise TypeError("unsupported message content")
        return "".join(parts)
    raise TypeError("unsupported message content")


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    if isinstance(value, dict):
        return value
    raise TypeError("expected an object")
