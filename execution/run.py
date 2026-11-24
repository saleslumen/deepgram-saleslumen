import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from execution.client import WorkflowClient, WorkflowError, execution_key
from execution.models import TERMINAL_STATES, ExecutionResource, Observation
from execution.steps import FAILED_LINE, Awaiting, Failed as FailedStep, Running, Step, StepError, Succeeded, compile_index, project


@dataclass(frozen=True)
class Accepted:
    step: Step
@dataclass(frozen=True)
class Moved:
    step: Step
@dataclass(frozen=True)
class Invalid:
    fields: tuple[str, ...]
@dataclass(frozen=True)
class Failed:
    code: str
SignalResult = Accepted | Moved | Invalid | Failed
StepListener = Callable[[str, int, Step], None]


def _snapshot_kind(step: Step) -> str:
    if isinstance(step, Running):
        return "running"
    if isinstance(step, Awaiting):
        return "awaiting"
    if isinstance(step, Succeeded):
        return "succeeded"
    return "failed"


def _failure_code(exc: WorkflowError) -> str:
    if exc.code:
        return exc.code
    if exc.reason:
        return exc.reason
    return "UNKNOWN"


def _invalid_fields(names: list[str], payload: Any) -> tuple[str, ...] | None:
    if not isinstance(payload, dict):
        return tuple(sorted(names))
    expected = set(names)
    provided = set(payload.keys())
    if provided == expected:
        return None
    return tuple(sorted(expected.symmetric_difference(provided)))


class Run:
    def __init__(self, client: WorkflowClient, on_step: StepListener | None = None) -> None:
        self._client = client
        self._on_step = on_step
        self._owned: list[asyncio.Task[Any]] = []
        self._closed = False
        self._cancel_sent = False
        self._active = False
        self._execution_id = ""
        self._created: ExecutionResource | None = None
        self._index = None
        self._observation: Observation | None = None
        self._step: Step | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()

    @property
    def active(self) -> bool:
        return self._active

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def version(self) -> int:
        if self._observation is None:
            return 0
        return self._observation.version

    @property
    def step(self) -> Step | None:
        return self._step

    async def start(self, workflow_id: str, request: str, call_key: str, attempt: int) -> Accepted | Failed:
        created: ExecutionResource | None = None
        try:
            self._execution_id = execution_key(call_key, attempt)
        except WorkflowError as exc:
            logger.warning("Workflow start failed: {}", exc.code)
            return Failed(exc.code)
        try:
            self._client.use(workflow_id)
            created = await self._client.create(self._execution_id, request)
            if await self._abort_if_closed():
                return await self._fail_after_create(created, "CLOSED")
            definition = await self._client.revision(created.revision)
            try:
                index = compile_index(definition)
            except StepError:
                index = None
        except WorkflowError as exc:
            code = _failure_code(exc)
            logger.warning("Workflow start failed: {}", code)
            if created is not None:
                return await self._fail_after_create(created, code)
            await self._close_client()
            return Failed(code)
        async with self._lock:
            closed = self._closed
            if not closed:
                self._created = created
                self._index = index
                self._active = True
        if closed:
            return await self._fail_after_create(created, "CLOSED")
        logger.info("Execution started: {}", self._execution_id)
        self._watch_task = asyncio.create_task(self._run_watch())
        self._owned.append(self._watch_task)
        step, code = await self._wait(after=0, allow_running=False)
        if code is not None:
            return await self._fail_after_create(created, code)
        return Accepted(step)

    async def signal(self, sequence: int, payload: dict) -> SignalResult:
        logger.info("Execution signal: {}", self._execution_id)
        async with self._lock:
            awaiting = self._step if isinstance(self._step, Awaiting) else None
            created = self._created
            observation = self._observation
            if awaiting is None or awaiting.sequence != sequence or created is None or observation is None:
                return Moved(self._current_step())
            wait = next((item for item in observation.waits if item.kind == "KIND_SIGNAL" and item.sequence == sequence and item.step_path == awaiting.step_path and item.signal == awaiting.signal), None)
            if wait is None:
                return Moved(self._current_step())
            names = [item.name for item in awaiting.arguments]
            invalid = _invalid_fields(names, payload)
            if invalid is not None:
                return Invalid(invalid)
            ordered = {name: payload[name] for name in names}
            execution_id = created.execution_id
            signal_name = awaiting.signal
            target = wait.target_step_run
            uid = observation.uid
            captured = observation.version
        try:
            await self._client.signal(execution_id, str(uuid.uuid4()), signal_name, ordered, target, uid)
        except WorkflowError as exc:
            code = exc.code if exc.code else "UNKNOWN"
            logger.warning("Execution signal failed: {}", code)
            return Failed(code)
        except Exception:
            logger.warning("Execution signal failed")
            return Failed("UNKNOWN")
        step, code = await self._wait(after=captured, allow_running=False, exclude_sequence=sequence)
        if code is not None:
            return Failed(code)
        return Accepted(step)

    def schedule(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._owned.append(task)

    async def close(self) -> None:
        pending: list[asyncio.Task[Any]] = []
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._active = False
            for task in list(self._owned):
                if not task.done():
                    task.cancel()
                    pending.append(task)
            if self._watch_task is not None and not self._watch_task.done():
                if self._watch_task not in pending:
                    self._watch_task.cancel()
                    pending.append(self._watch_task)
        if pending:
            await asyncio.wait(pending, timeout=2)
        try:
            await self._cancel_open_once()
        finally:
            await self._close_client()

    async def _close_client(self) -> None:
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()

    def _current_step(self) -> Step:
        if self._step is None:
            return Running()
        return self._step

    def _matches(self, step: Step, allow_running: bool, exclude_sequence: int | None) -> bool:
        if isinstance(step, Running):
            return allow_running
        if exclude_sequence is not None and isinstance(step, Awaiting) and step.sequence == exclude_sequence:
            return False
        return True

    async def _wait(self, after: int, allow_running: bool, exclude_sequence: int | None = None) -> tuple[Step, str | None]:
        while True:
            async with self._lock:
                observation = self._observation
                step = self._step
                if observation is not None and observation.version > after and step is not None and self._matches(step, allow_running, exclude_sequence):
                    return step, None
                closed = self._closed
                watch = self._watch_task
            if closed:
                return Running(), "CLOSED"
            if watch is not None and watch.done():
                async with self._lock:
                    step = self._step
                    if step is not None and self._matches(step, allow_running, exclude_sequence):
                        return step, None
                    if isinstance(step, FailedStep):
                        return step, None
                return Running(), "WATCH_STOPPED"
            self._changed.clear()
            await self._changed.wait()

    async def _run_watch(self) -> None:
        created = self._created
        if created is None:
            return
        try:
            try:
                await self._client.watch(created.execution_id, self._on_snapshot)
            except asyncio.CancelledError:
                raise
            except WorkflowError as exc:
                code = _failure_code(exc)
                logger.warning("Watch stopped: {}", code)
                await self._fail_watch()
            except Exception:
                logger.warning("Watch stopped")
                await self._fail_watch()
        finally:
            self._changed.set()

    async def _fail_watch(self) -> None:
        async with self._lock:
            if isinstance(self._step, (Succeeded, FailedStep)):
                return
            current = FailedStep(FAILED_LINE)
            self._step = current
        self._changed.set()

    def _log_snapshot(self, version: int, step: Step) -> None:
        if isinstance(step, Awaiting):
            logger.info("Execution {} version {} awaiting sequence {} step {}", self._execution_id, version, step.sequence, step.step_path)
            return
        logger.info("Execution {} version {} {}", self._execution_id, version, _snapshot_kind(step))

    async def _on_snapshot(self, observation: Observation) -> None:
        accepted = False
        current: Step | None = None
        async with self._lock:
            if isinstance(self._step, FailedStep) and self._step.message == FAILED_LINE:
                return
            created = self._created
            if created is not None and observation.revision != created.revision:
                raise WorkflowError(0, "EXECUTION_REVISION_CHANGED", "execution revision changed during watch")
            if created is not None and (observation.uid != created.uid or observation.name != created.name):
                raise WorkflowError(0, "EXECUTION_UID_CHANGED", "execution identity changed during watch")
            if self._observation is not None and observation.version <= self._observation.version:
                return
            self._observation = observation
            current = project(self._index, observation)
            self._step = current
            accepted = True
            if observation.state in TERMINAL_STATES:
                self._active = False
        if accepted and current is not None:
            self._log_snapshot(observation.version, current)
            if self._on_step is not None:
                self._on_step(self._execution_id, observation.version, current)
        self._changed.set()

    async def _cancel_open_once(self) -> None:
        async with self._lock:
            if self._cancel_sent:
                return
            created = self._created
            observation = self._observation
        if created is None:
            return
        if observation is not None and observation.state in TERMINAL_STATES:
            async with self._lock:
                self._cancel_sent = True
            return
        async with self._lock:
            self._cancel_sent = True
        await self._cancel_execution(created.execution_id)

    async def _fail_after_create(self, created: ExecutionResource, code: str) -> Failed:
        orphan_cancel = False
        async with self._lock:
            orphan_cancel = self._cancel_sent and self._created is None
            if self._created is None:
                self._created = created
            closed = self._closed
            cancel_sent = self._cancel_sent
            observation = self._observation
        if closed:
            try:
                if observation is not None and observation.state in TERMINAL_STATES:
                    return Failed(code)
                if orphan_cancel or not cancel_sent:
                    async with self._lock:
                        self._cancel_sent = True
                    await self._cancel_execution(created.execution_id)
                return Failed(code)
            finally:
                await self._close_client()
        await self.close()
        return Failed(code)

    async def _abort_if_closed(self) -> bool:
        async with self._lock:
            return self._closed

    async def _cancel_execution(self, execution_id: str) -> None:
        try:
            await self._client.cancel(execution_id)
        except WorkflowError as exc:
            if exc.code == "EXECUTION_TERMINAL" or exc.reason == "EXECUTION_TERMINAL":
                return
            logger.warning("Could not cancel the execution: {}", exc.code)
        except Exception:
            logger.warning("Could not cancel the execution")
