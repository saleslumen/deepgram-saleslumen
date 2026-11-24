import asyncio
import hashlib
import json
import random
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger
from pydantic import BaseModel, ValidationError

from execution.models import ExecutionResource, Observation, RevisionDefinition, RevisionEnvelope, StatusEnvelope, TERMINAL_STATES, WorkflowDisplay

WORKFLOW_ID = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
SnapshotHandler = Callable[[Observation], Awaitable[None]]
_EXECUTION_KEY = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_REPLAY = {"ALREADY_EXISTS", "ABORTED"}


class WorkflowError(Exception):
    def __init__(self, status: int, code: str, message: str, reason: str = "", metadata: dict[str, str] | None = None) -> None:
        self.status = status
        self.code = code
        self.reason = reason
        self.metadata = {} if metadata is None else dict(metadata)
        super().__init__(message if code == "" else f"{code}: {message}")
        self.message = message


def execution_key(call_key: str, attempt: int) -> str:
    if attempt < 1:
        raise WorkflowError(0, "INVALID_CALL", "the call is not a valid execution id")
    cleaned = "".join(ch for ch in call_key.lower() if ch in "abcdefghijklmnopqrstuvwxyz0123456789-")
    suffix = f"-{attempt}"
    budget = 63 - len(suffix)
    if budget < 1:
        raise WorkflowError(0, "INVALID_CALL", "the call is not a valid execution id")
    call_part = cleaned[:budget] if len(cleaned) > budget else cleaned
    while call_part and not call_part[0].isalnum():
        call_part = call_part[1:]
    if len(call_part) > budget:
        call_part = call_part[:budget]
    while call_part:
        candidate = f"{call_part}{suffix}"
        if _EXECUTION_KEY.fullmatch(candidate) is not None:
            return candidate
        call_part = call_part[:-1]
        while call_part and call_part[-1] == "-":
            call_part = call_part[:-1]
    raise WorkflowError(0, "INVALID_CALL", "the call is not a valid execution id")


def _request_id(execution_id: str) -> str:
    raw = bytearray(hashlib.sha256(execution_id.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


class SSEParser:
    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []

    def feed(self, line: str) -> tuple[str, str] | None:
        line = line.rstrip("\r")
        if line == "":
            if not self._data:
                self._event = ""
                return None
            event = self._event or "message"
            data = "\n".join(self._data)
            self._event = ""
            self._data = []
            return event, data
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        return None


class WorkflowClient:
    def __init__(self, *, origin: str, saleslumen_api_key: str, organization_id: str, namespace_id: str, client: httpx.AsyncClient | None = None) -> None:
        self._origin = origin.rstrip("/")
        self._saleslumen_api_key = saleslumen_api_key
        self._organization_id = organization_id
        self._namespace_id = namespace_id
        self._workflow_id = ""
        self._client = client

    def use(self, workflow_id: str) -> None:
        if not isinstance(workflow_id, str) or WORKFLOW_ID.fullmatch(workflow_id) is None:
            raise WorkflowError(0, "INVALID_WORKFLOW", "the selected workflow id is not allowed")
        self._workflow_id = workflow_id

    async def display_name(self) -> str:
        selected = self._selected()
        payload = await self._send("GET", f"/v1/workflows/{quote(selected, safe='')}", None, None)
        resource = _read(WorkflowDisplay, payload, "INVALID_RESPONSE")
        name = resource.display_name
        if not isinstance(name, str) or name.strip() == "":
            return selected
        return name.strip()

    async def revision(self, name: str) -> RevisionDefinition:
        selected = self._selected()
        revision_id = self._revision_id(name)
        payload = await self._send("GET", f"/v1/workflows/{quote(selected, safe='')}/revisions/{quote(revision_id, safe='')}", None, None)
        return _read(RevisionEnvelope, payload, "INVALID_REVISION").definition

    async def create(self, execution_id: str, request: str) -> ExecutionResource:
        self._selected()
        query = {"executionId": execution_id, "requestId": _request_id(execution_id)}
        try:
            payload = await self._send("POST", self._execution_collection(), query, {"input": {"request": request}})
        except WorkflowError as exc:
            if exc.code not in _REPLAY and exc.reason not in _REPLAY:
                raise
            try:
                payload = await self._send("GET", self._execution(execution_id), None, None)
            except WorkflowError:
                raise exc
            try:
                return self._read_created(payload)
            except WorkflowError:
                raise exc
        return self._read_created(payload)

    async def cancel(self, execution_id: str) -> None:
        self._selected()
        await self._send("POST", f"{self._execution(execution_id)}:cancel", None, {})

    async def signal(self, execution_id: str, request_id: str, signal: str, payload: dict[str, Any], target_step_run: str, execution_uid: str) -> None:
        self._selected()
        body = {"signal": signal, "payload": payload, "targetStepRun": target_step_run, "executionUid": execution_uid, "requestId": request_id}
        await self._send("POST", f"{self._execution(execution_id)}:signal", None, body)

    async def watch(self, execution_id: str, on_snapshot: SnapshotHandler) -> None:
        self._selected()
        last_version = 0
        uid = ""
        delay = 0.5
        for attempt in range(3):
            try:
                terminal, last_version, uid = await self._watch_once(execution_id, on_snapshot, last_version, uid)
            except WorkflowError as exc:
                if exc.status < 500 and exc.status != 429:
                    raise
                if attempt == 2:
                    raise
            except (httpx.HTTPError, asyncio.TimeoutError):
                if attempt == 2:
                    raise
            else:
                if terminal:
                    return
                if attempt == 2:
                    raise WorkflowError(0, "WATCH_INTERRUPTED", "watch ended before a terminal snapshot")
            await asyncio.sleep(delay + random.uniform(0, delay))
            delay = min(delay * 2, 4)

    async def _watch_once(self, execution_id: str, on_snapshot: SnapshotHandler, last_version: int, uid: str) -> tuple[bool, int, str]:
        path = f"{self._execution(execution_id)}:watch"
        headers = self._headers("text/event-stream")
        timeout = httpx.Timeout(10, read=None, write=10, pool=10)
        parser = SSEParser()
        async with self._http() as http:
            async with http.stream("GET", self._origin + path, headers=headers, timeout=timeout) as response:
                if response.status_code < 200 or response.status_code > 299:
                    raise _status_error(response.status_code, await response.aread())
                async for line in response.aiter_lines():
                    terminal, last_version, uid = await _apply_watch_event(parser.feed(line), on_snapshot, last_version, uid)
                    if terminal:
                        return True, last_version, uid
                terminal, last_version, uid = await _apply_watch_event(parser.feed(""), on_snapshot, last_version, uid)
                if terminal:
                    return True, last_version, uid
        return False, last_version, uid

    async def _send(self, method: str, path: str, query: dict[str, str] | None, body: dict[str, Any] | None) -> dict[str, Any]:
        timeout = httpx.Timeout(30)
        request: dict[str, Any] = {"headers": self._headers("application/json"), "params": query, "timeout": timeout}
        if body is not None:
            request["json"] = body
        try:
            async with self._http() as http:
                response = await http.request(method, self._origin + path, **request)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            raise WorkflowError(0, "WORKFLOWS_UNAVAILABLE", "Workflows request failed") from exc
        if response.status_code < 200 or response.status_code > 299:
            raise _status_error(response.status_code, response.content)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise WorkflowError(response.status_code, "INVALID_RESPONSE", "Workflows response must be an object") from exc
        if not isinstance(payload, dict):
            raise WorkflowError(response.status_code, "INVALID_RESPONSE", "Workflows response must be an object")
        return payload

    def _read_created(self, payload: dict[str, Any]) -> ExecutionResource:
        return _read(ExecutionResource, payload, "INVALID_EXECUTION", context={"workflow_id": self._workflow_id})

    def _selected(self) -> str:
        if self._workflow_id == "":
            raise WorkflowError(0, "WORKFLOW_UNSELECTED", "no workflow is selected")
        return self._workflow_id

    def _revision_id(self, name: str) -> str:
        prefix = f"workflows/{self._workflow_id}/revisions/"
        revision_id = name.removeprefix(prefix)
        if name == "" or not name.startswith(prefix) or revision_id == "" or "/" in revision_id:
            raise WorkflowError(200, "INVALID_REVISION", "execution revision is not for this workflow")
        return revision_id

    def _execution_collection(self) -> str:
        return f"/v1/workflows/{quote(self._workflow_id, safe='')}/executions"

    def _execution(self, execution_id: str) -> str:
        return f"{self._execution_collection()}/{quote(execution_id, safe='')}"

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"sl-api-key": self._saleslumen_api_key, "sl-organization-id": self._organization_id, "Accept": accept}
        if self._namespace_id:
            headers["sl-namespace-id"] = self._namespace_id
        return headers

    def _http(self) -> httpx.AsyncClient:
        if self._client is not None:
            return _BorrowedClient(self._client)
        return httpx.AsyncClient()


class _BorrowedClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        return None


async def _apply_watch_event(event: tuple[str, str] | None, on_snapshot: SnapshotHandler, last_version: int, uid: str) -> tuple[bool, int, str]:
    if event is None:
        return False, last_version, uid
    name, data = event
    if name == "error":
        raise _status_error(200, data.encode())
    if name != "execution":
        raise WorkflowError(200, "UNEXPECTED_EVENT", f"unexpected watch event {name}")
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise WorkflowError(200, "INVALID_RESPONSE", "execution snapshot must be an object") from exc
    observation = _read(Observation, raw, "INVALID_RESPONSE")
    if uid and observation.uid != uid:
        raise WorkflowError(200, "EXECUTION_UID_CHANGED", "execution identity changed during watch")
    uid = observation.uid
    if observation.version <= last_version:
        return False, last_version, uid
    last_version = observation.version
    await on_snapshot(observation)
    return observation.state in TERMINAL_STATES, last_version, uid


def _read(model: type[BaseModel], payload: Any, code: str, **kwargs: Any) -> Any:
    try:
        return model.model_validate(payload, **kwargs)
    except ValidationError as exc:
        for error in exc.errors():
            if error.get("type") == "value_error":
                raise WorkflowError(200, code, str(error.get("msg", "")).removeprefix("Value error, ")) from None
        fallback = "execution snapshot is invalid" if code == "INVALID_RESPONSE" else "Workflows response is invalid"
        raise WorkflowError(200, code, fallback) from None


def _status_error(status: int, body: bytes) -> WorkflowError:
    text = body.decode(errors="replace")[:300]
    try:
        parsed = StatusEnvelope.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValidationError):
        error = WorkflowError(status, "", text)
    else:
        code, message, reason, metadata = parsed.fields(text)
        error = WorkflowError(status, code, message, reason, metadata)
    detail = str(error) if str(error) else "empty body"
    logger.warning("Workflows request failed: status={} {}", error.status, detail)
    return error
