"""Run lifecycle with a fake Workflows client."""

import asyncio
import unittest

from loguru import logger

from execution.client import WorkflowError, execution_key
from execution.models import ExecutionResource, Observation, RevisionDefinition
from execution.run import Accepted, Failed, Invalid, Moved, Run
from execution.steps import AnswerKind, Awaiting, Running, Succeeded, answer_kind


def execution(state, version, waits=None, result=None):
    return {
        "name": "workflows/hello-voice/executions/run-1",
        "uid": "11111111-1111-4111-8111-111111111111",
        "revision": "workflows/hello-voice/revisions/greeting",
        "state": state,
        "observationVersion": str(version),
        "waits": waits or [],
        "result": result,
    }


class _FakeClient:
    def __init__(self, *, revision=None):
        self.queue = asyncio.Queue()
        self.signaled = []
        self.cancelled = []
        self.closed_count = 0
        self.create_error = None
        self.signal_error = None
        self.revision_error = None
        self.cancel_error = None
        self.workflow_id = "hello-voice"
        self._revision = revision
        self._on_snapshot = None
        self.inject_during_signal = None

    def use(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id

    async def create(self, execution_id, request):
        self.execution_id = execution_id
        self.request = request
        if self.create_error is not None:
            error = self.create_error
            self.create_error = None
            raise error
        return ExecutionResource(name="workflows/hello-voice/executions/run-1", uid="11111111-1111-4111-8111-111111111111", revision="workflows/hello-voice/revisions/greeting", execution_id=execution_id)

    async def watch(self, execution_id, on_snapshot):
        self._on_snapshot = on_snapshot
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                await on_snapshot(item)
            finally:
                self.queue.task_done()

    async def aclose(self):
        self.closed_count += 1

    async def revision(self, name):
        if self.revision_error is not None:
            error = self.revision_error
            self.revision_error = None
            raise error
        if self._revision is not None:
            if isinstance(self._revision, RevisionDefinition):
                return self._revision
            return RevisionDefinition.model_validate(self._revision)
        if name != "workflows/hello-voice/revisions/greeting":
            raise AssertionError(name)
        return RevisionDefinition.model_validate({
            "signals": {"continue": "object{accepted: bool}"},
            "steps": [{"id": "confirm", "label": "Wait for the caller", "awaitSignal": {"signal": "continue", "result": "vars.confirmation"}}],
        })

    async def signal(self, execution_id, request_id, signal, payload, target_step_run, execution_uid):
        if self.signal_error is not None:
            error = self.signal_error
            self.signal_error = None
            raise error
        self.signaled.append({"payload": payload, "target_step_run": target_step_run, "execution_uid": execution_uid, "signal": signal})
        if self.inject_during_signal is not None and self._on_snapshot is not None:
            for item in self.inject_during_signal:
                await self._on_snapshot(item)
            self.inject_during_signal = None

    async def cancel(self, execution_id):
        if self.cancel_error is not None:
            error = self.cancel_error
            self.cancel_error = None
            raise error
        self.cancelled.append(execution_id)


async def _start_waiting(run, client, revision=None, call_key="run-1", attempt=1):
    client._revision = revision
    ready = asyncio.create_task(run.start("hello-voice", "caller request", call_key, attempt))
    await asyncio.sleep(0)
    await client.queue.put(Observation.model_validate(execution("STATE_WAITING", 2, [{"kind": "KIND_SIGNAL", "stepPath": "confirm", "sequence": "4", "signal": "continue"}])))
    result = await asyncio.wait_for(ready, timeout=2.0)
    if isinstance(result, Failed):
        raise AssertionError(result.code)
    return result.step


class RunTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_log_names_the_execution_and_not_the_request(self):
        lines = []
        secret = "DISTINCTIVE_REQUEST_MUST_NOT_LEAK_B11"
        expected_id = execution_key("run-1", 1)
        sink = logger.add(lambda message: lines.append(str(message)), format="{message}", level="INFO")
        try:
            client = _FakeClient()
            run = Run(client)
            ready = asyncio.create_task(run.start("hello-voice", secret, "run-1", 1))
            await asyncio.sleep(0)
            await client.queue.put(Observation.model_validate(execution("STATE_WAITING", 2, [{"kind": "KIND_SIGNAL", "stepPath": "confirm", "sequence": "4", "signal": "continue"}])))
            result = await asyncio.wait_for(ready, timeout=2.0)
            self.assertIsInstance(result, Accepted)
            joined = "\n".join(lines)
            self.assertIn(expected_id, joined)
            self.assertNotIn(secret, joined)
            await client.queue.put(None)
            await run.close()
        finally:
            logger.remove(sink)

    async def test_snapshot_log_names_the_step_and_not_the_label(self):
        lines = []
        label = "Wait for the caller"
        secret = "DISTINCTIVE_REQUEST_MUST_NOT_LEAK_Q3"
        outcome = "DISTINCTIVE_OUTCOME_MUST_NOT_LEAK_Q3"
        sink = logger.add(lambda message: lines.append(str(message)), format="{message}", level="INFO")
        try:
            client = _FakeClient()
            run = Run(client)
            ready = asyncio.create_task(run.start("hello-voice", secret, "run-1", 1))
            await asyncio.sleep(0)
            await client.queue.put(Observation.model_validate(execution("STATE_WAITING", 2, [{"kind": "KIND_SIGNAL", "stepPath": "confirm", "sequence": "4", "signal": "continue"}])))
            result = await asyncio.wait_for(ready, timeout=2.0)
            self.assertIsInstance(result, Accepted)
            awaiting = [line.strip() for line in lines if "awaiting" in line]
            self.assertEqual(awaiting, [f"Execution {run.execution_id} version 2 awaiting sequence 4 step confirm"])
            pending = asyncio.create_task(run.signal(4, {"accepted": True}))
            await asyncio.sleep(0)
            await client.queue.put(Observation.model_validate(execution("STATE_SUCCEEDED", 5, result={"message": outcome})))
            done = await asyncio.wait_for(pending, timeout=2.0)
            self.assertIsInstance(done, Accepted)
            self.assertIsInstance(done.step, Succeeded)
            self.assertIn(f"Execution {run.execution_id} version 5 succeeded", [line.strip() for line in lines])
            joined = "\n".join(lines)
            self.assertNotIn(label, joined)
            self.assertNotIn(secret, joined)
            self.assertNotIn(outcome, joined)
            await client.queue.put(None)
            await run.close()
        finally:
            logger.remove(sink)

    async def test_on_step_runs_for_accepted_snapshots_only(self):
        calls: list[tuple[str, int, object]] = []
        def recorder(execution_id: str, version: int, step) -> None:
            calls.append((execution_id, version, step))
        client = _FakeClient()
        run = Run(client, on_step=recorder)
        await _start_waiting(run, client)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], run.execution_id)
        self.assertEqual(calls[0][1], 2)
        self.assertIsInstance(calls[0][2], Awaiting)
        self.assertEqual(calls[0][2].step_path, "confirm")
        self.assertEqual(calls[0][2].sequence, 4)
        await client.queue.put(Observation.model_validate(execution("STATE_WAITING", 2, [{"kind": "KIND_SIGNAL", "stepPath": "confirm", "sequence": "4", "signal": "continue"}])))
        await asyncio.sleep(0)
        self.assertEqual(len(calls), 1)
        await client.queue.put(None)
        await run.close()

    async def test_start_returns_the_first_awaiting_step(self):
        client = _FakeClient()
        run = Run(client)
        step = await _start_waiting(run, client)
        self.assertIsInstance(step, Awaiting)
        self.assertEqual(step.sequence, 4)
        self.assertTrue(run.active)
        self.assertEqual(run.execution_id, execution_key("run-1", 1))
        await client.queue.put(None)
        await run.close()

    async def test_notice_vs_question_projection(self):
        notice_revision = {
            "signals": {"ack": "object{}"},
            "steps": [{"id": "n", "label": "One moment", "awaitSignal": {"signal": "ack"}}],
        }
        notice_observation = Observation.model_validate(execution("STATE_WAITING", 2, [{"kind": "KIND_SIGNAL", "stepPath": "n", "sequence": "4", "signal": "ack"}]))
        client = _FakeClient(revision=notice_revision)
        run = Run(client)
        ready = asyncio.create_task(run.start("hello-voice", "", "run-1", 1))
        await asyncio.sleep(0)
        await client.queue.put(notice_observation)
        result = await asyncio.wait_for(ready, timeout=2.0)
        self.assertIsInstance(result, Accepted)
        self.assertIsInstance(result.step, Awaiting)
        self.assertEqual(answer_kind(result.step.arguments), AnswerKind.NOTICE)
        await client.queue.put(None)
        await run.close()
        client = _FakeClient()
        run = Run(client)
        step = await _start_waiting(run, client)
        self.assertEqual(answer_kind(step.arguments), AnswerKind.QUESTION)
        await client.queue.put(None)
        await run.close()

    async def test_signal_accepted_advances_the_version(self):
        client = _FakeClient()
        run = Run(client)
        await _start_waiting(run, client)
        before = run.version
        pending = asyncio.create_task(run.signal(4, {"accepted": True}))
        await asyncio.sleep(0)
        await client.queue.put(Observation.model_validate(execution("STATE_SUCCEEDED", 4, result={"message": "Done"})))
        result = await asyncio.wait_for(pending, timeout=2.0)
        self.assertIsInstance(result, Accepted)
        self.assertIsInstance(result.step, Succeeded)
        self.assertGreater(run.version, before)
        await client.queue.put(None)
        await run.close()

    async def test_signal_does_not_skip_the_snapshot_that_arrives_during_the_request(self):
        client = _FakeClient()
        run = Run(client)
        await _start_waiting(run, client)
        client.inject_during_signal = [Observation.model_validate(execution("STATE_SUCCEEDED", 3, result={"done": True}))]
        result = await asyncio.wait_for(run.signal(4, {"accepted": True}), timeout=2.0)
        self.assertIsInstance(result, Accepted)
        self.assertIsInstance(result.step, Succeeded)
        self.assertEqual(run.version, 3)
        await client.queue.put(None)
        await run.close()
        client = _FakeClient()
        run = Run(client)
        await _start_waiting(run, client)
        client.inject_during_signal = [Observation.model_validate(execution("STATE_WAITING", 3, [{"kind": "KIND_SIGNAL", "stepPath": "confirm", "sequence": "4", "signal": "continue"}]))]
        pending = asyncio.create_task(run.signal(4, {"accepted": True}))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        await client.queue.put(Observation.model_validate(execution("STATE_SUCCEEDED", 4, result={"later": True})))
        result = await asyncio.wait_for(pending, timeout=2.0)
        self.assertIsInstance(result, Accepted)
        self.assertIsInstance(result.step, Succeeded)
        self.assertEqual(run.version, 4)
        await client.queue.put(None)
        await run.close()

    async def test_signal_moved_when_sequence_differs(self):
        client = _FakeClient()
        run = Run(client)
        await _start_waiting(run, client)
        result = await run.signal(9, {"accepted": True})
        self.assertIsInstance(result, Moved)
        await client.queue.put(None)
        await run.close()

    async def test_signal_moved_before_start(self):
        client = _FakeClient()
        run = Run(client)
        result = await run.signal(4, {"accepted": True})
        self.assertIsInstance(result, Moved)
        self.assertIsInstance(result.step, Running)

    async def test_bad_keys_are_invalid_and_closed_does_not_raise(self):
        client = _FakeClient()
        run = Run(client)
        await _start_waiting(run, client)
        result = await run.signal(4, {"wrong": True})
        self.assertIsInstance(result, Invalid)
        self.assertEqual(result.fields, ("accepted", "wrong"))
        self.assertEqual(len(client.signaled), 0)
        result = await run.signal(4, "not-a-dict")
        self.assertIsInstance(result, Invalid)
        await run.close()
        await run.close()
        await client.queue.put(None)

    async def test_workflow_errors_on_signal_return_failed(self):
        client = _FakeClient()
        run = Run(client)
        await _start_waiting(run, client)
        client.signal_error = WorkflowError(400, "FAILED_PRECONDITION", "The visit is already being booked.", reason="SIGNAL_REJECTED", metadata={})
        result = await run.signal(4, {"accepted": True})
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, "FAILED_PRECONDITION")
        client.signal_error = WorkflowError(503, "UNAVAILABLE", "busy")
        result = await run.signal(4, {"accepted": True})
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, "UNAVAILABLE")
        await client.queue.put(None)
        await run.close()

    async def test_close_is_idempotent_and_cancels(self):
        client = _FakeClient()
        run = Run(client)
        await _start_waiting(run, client)
        await run.close()
        self.assertEqual(client.cancelled, [execution_key("run-1", 1)])
        self.assertEqual(client.closed_count, 1)
        await run.close()
        self.assertEqual(client.cancelled, [execution_key("run-1", 1)])
        self.assertEqual(client.closed_count, 1)
        self.assertFalse(run.active)

    async def test_scheduled_task_is_cancelled_by_close(self):
        client = _FakeClient()
        run = Run(client)
        await _start_waiting(run, client)
        cancelled = asyncio.Event()
        async def sleeper():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        run.schedule(sleeper())
        await asyncio.sleep(0)
        await run.close()
        await asyncio.wait_for(cancelled.wait(), timeout=2.0)
        await client.queue.put(None)

    async def test_invalid_call_key_returns_failed(self):
        client = _FakeClient()
        run = Run(client)
        result = await asyncio.wait_for(run.start("hello-voice", "", "###", 1), timeout=2.0)
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, "INVALID_CALL")
        self.assertEqual(client.cancelled, [])

    async def test_start_failure_after_create_closes_the_execution(self):
        client = _FakeClient()
        client.revision_error = WorkflowError(404, "NOT_FOUND", "missing revision")
        run = Run(client)
        ready = asyncio.create_task(run.start("hello-voice", "", "run-1", 1))
        await asyncio.sleep(0)
        result = await asyncio.wait_for(ready, timeout=2.0)
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, "NOT_FOUND")
        self.assertEqual(client.cancelled, [execution_key("run-1", 1)])
        await run.close()
        self.assertEqual(client.cancelled, [execution_key("run-1", 1)])

    async def test_wait_failure_after_create_closes_the_run(self):
        client = _FakeClient()
        run = Run(client)
        ready = asyncio.create_task(run.start("hello-voice", "", "run-1", 1))
        while client._on_snapshot is None:
            await asyncio.sleep(0)
        await client.queue.put(None)
        result = await asyncio.wait_for(ready, timeout=2.0)
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, "WATCH_STOPPED")
        self.assertFalse(run.active)
        await run.close()
        self.assertEqual(client.cancelled, [execution_key("run-1", 1)])

    async def test_close_during_create_still_cancels_the_execution(self):
        client = _FakeClient()
        blocked = asyncio.Event()
        release = asyncio.Event()
        original = client.create
        async def slow_create(execution_id, request):
            blocked.set()
            await asyncio.wait_for(release.wait(), timeout=2.0)
            return await original(execution_id, request)
        client.create = slow_create
        run = Run(client)
        expected = execution_key("run-1", 1)
        starting = asyncio.create_task(run.start("hello-voice", "", "run-1", 1))
        await asyncio.wait_for(blocked.wait(), timeout=2.0)
        await run.close()
        release.set()
        result = await asyncio.wait_for(starting, timeout=2.0)
        self.assertIsInstance(result, Failed)
        self.assertEqual(result.code, "CLOSED")
        self.assertEqual(client.cancelled, [expected])
        await run.close()
        self.assertEqual(client.cancelled, [expected])


if __name__ == "__main__":
    unittest.main()
