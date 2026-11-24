"""Workflow client HTTP and parsing."""

import hashlib
import json
import unittest
import uuid

import httpx
from loguru import logger

from execution.client import SSEParser, WorkflowClient, WorkflowError, execution_key


def execution(state, version, waits=None, result=None):
    return {
        "name": "workflows/hello-voice/executions/run-1",
        "uid": "11111111-1111-4111-8111-111111111111",
        "revision": "workflows/hello-voice/revisions/greeting",
        "state": state,
        "observationVersion": str(version),
        "vars": {"greeting": {"message": "Hello, world."}} if version > 1 else {},
        "waits": waits or [],
        "result": result,
    }


def sse(payload):
    return "event: execution\ndata: " + json.dumps(payload) + "\n\n"


def derived_request_id(execution_id):
    raw = bytearray(hashlib.sha256(execution_id.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


class ParserTest(unittest.TestCase):
    def test_comments_are_heartbeats_and_events_dispatch_on_a_blank_line(self):
        parser = SSEParser()
        self.assertIsNone(parser.feed(": heartbeat"))
        self.assertIsNone(parser.feed("event: execution"))
        self.assertIsNone(parser.feed('data: {"state":"STATE_QUEUED"}'))
        event = parser.feed("")
        self.assertEqual(event, ("execution", '{"state":"STATE_QUEUED"}'))


class ClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_watch_and_signal(self):
        seen = []
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, dict(request.url.params), request.headers.get("sl-api-key"), request.headers.get("Authorization"), request.headers.get("sl-organization-id"), request.headers.get("sl-namespace-id"), json.loads(request.content) if request.content else None))
            if request.url.path.endswith(":watch"):
                body = sse(execution("STATE_WAITING", 2, [{"kind": "KIND_SIGNAL", "stepPath": "confirm", "sequence": "4", "signal": "continue"}]))
                body += ": keep-alive\n\n"
                body += sse(execution("STATE_SUCCEEDED", 3, result={"message": "Hello, world."}))
                return httpx.Response(200, text=body)
            if request.method == "POST" and request.url.path.endswith("/executions"):
                return httpx.Response(200, json=execution("STATE_QUEUED", 1))
            if request.url.path.endswith(":signal"):
                return httpx.Response(200, json=execution("STATE_ACTIVE", 3))
            return httpx.Response(404, json={"status": "NOT_FOUND", "message": "missing"})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="ns", client=http)
            client.use("hello-voice")
            created = await client.create("7c9e6679-7425-40de-944b-e07fc1f90ae7", "I was charged twice")
            self.assertEqual(created.execution_id, "run-1")
            snapshots = []
            async def collect(observation):
                snapshots.append(observation)
            await client.watch(created.execution_id, collect)
            self.assertEqual([item.version for item in snapshots], [2, 3])
            await client.signal(created.execution_id, "7c9e6679-7425-40de-944b-e07fc1f90ae8", "continue", {"accepted": True}, snapshots[0].waits[0].target_step_run, snapshots[0].uid)
        execution_id = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
        self.assertEqual(seen[0][0], "POST")
        self.assertEqual(seen[0][2]["executionId"], execution_id)
        self.assertEqual(seen[0][2]["requestId"], derived_request_id(execution_id))
        self.assertEqual(uuid.UUID(seen[0][2]["requestId"]).version, 4)
        self.assertEqual(seen[0][3], "token")
        self.assertIsNone(seen[0][4])
        self.assertEqual(seen[0][5], "org")
        self.assertEqual(seen[0][6], "ns")
        self.assertEqual(seen[0][7], {"input": {"request": "I was charged twice"}})
        self.assertNotIn("Authorization", seen[0][7])
        self.assertTrue(seen[1][1].endswith(":watch"))
        self.assertEqual(seen[2][7]["targetStepRun"], "workflows/hello-voice/executions/run-1/stepRuns/4")
        self.assertEqual(seen[2][7]["executionUid"], created.uid)
        self.assertEqual(seen[2][7]["payload"], {"accepted": True})
        for item in seen:
            self.assertEqual(item[3], "token")
            self.assertIsNone(item[4])

    async def test_create_fetches_the_pinned_revision_and_not_the_serving_one(self):
        definition = {"signals": {"continue": "object{accepted: bool}"}, "steps": []}
        seen = []
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.content))
            if request.method == "POST":
                return httpx.Response(200, json={"name": "workflows/hello-voice/executions/run-1", "uid": "11111111-1111-4111-8111-111111111111", "revision": "workflows/hello-voice/revisions/b"})
            if request.url.path == "/v1/workflows/hello-voice/revisions/b":
                return httpx.Response(200, json={"definition": definition})
            return httpx.Response(404, json={"status": "NOT_FOUND", "message": "serving revision was read"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            created = await client.create("7c9e6679-7425-40de-944b-e07fc1f90ae7", "")
            self.assertEqual(created.revision, "workflows/hello-voice/revisions/b")
            rev = await client.revision(created.revision)
            self.assertEqual(rev.signals, definition["signals"])
            self.assertEqual([step.id for step in rev.steps], [step["id"] for step in definition["steps"]])
        self.assertNotIn("/v1/workflows/hello-voice", [path for _method, path, _body in seen])
        self.assertEqual(seen[1][1], "/v1/workflows/hello-voice/revisions/b")
        self.assertEqual(seen[1][2], b"")

    async def test_use_selects_the_workflow_that_is_created(self):
        seen = []
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"name": "workflows/other-flow/executions/run-1", "uid": "11111111-1111-4111-8111-111111111111", "revision": "workflows/other-flow/revisions/b"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("other-flow")
            created = await client.create("7c9e6679-7425-40de-944b-e07fc1f90ae7", "")
        self.assertEqual(created.execution_id, "run-1")
        self.assertEqual(seen, ["/v1/workflows/other-flow/executions"])

    async def test_use_rejects_an_id_without_echoing_it(self):
        client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="")
        with self.assertRaises(WorkflowError) as caught:
            client.use("Not Valid")
        self.assertNotIn("Not Valid", str(caught.exception))

    async def test_an_unselected_client_does_not_call(self):
        client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="")
        with self.assertRaises(WorkflowError) as caught:
            await client.create("7c9e6679-7425-40de-944b-e07fc1f90ae7", "")
        self.assertIn("WORKFLOW_UNSELECTED", str(caught.exception))

    async def test_display_name_ignores_the_serving_revision(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/v1/workflows/hello-voice")
            return httpx.Response(200, json={"displayName": "Hello voice", "servingRevision": "workflows/hello-voice/revisions/other"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            self.assertEqual(await client.display_name(), "Hello voice")
        def blank(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"displayName": "  ", "servingRevision": "workflows/hello-voice/revisions/other"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(blank)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            self.assertEqual(await client.display_name(), "hello-voice")

    async def test_a_rejected_create_is_logged(self):
        lines = []
        sink = logger.add(lambda message: lines.append(str(message)), format="{message}", level="WARNING")
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401))) as http:
                client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
                client.use("hello-voice")
                with self.assertRaises(WorkflowError) as caught:
                    await client.create("7c9e6679-7425-40de-944b-e07fc1f90ae7", "")
        finally:
            logger.remove(sink)
        self.assertEqual(caught.exception.status, 401)
        self.assertIn("status=401", lines[0])
        self.assertIn("empty body", lines[0])

    async def test_revision_rejects_a_non_object_body(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/revisions/b"):
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"name": "workflows/hello-voice/executions/run-1", "uid": "11111111-1111-4111-8111-111111111111", "revision": "workflows/hello-voice/revisions/b"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            created = await client.create("run-1", "")
            with self.assertRaises(WorkflowError) as caught:
                await client.revision(created.revision)
        self.assertEqual(caught.exception.code, "INVALID_RESPONSE")

    async def test_watch_rejects_snapshot_payload_that_is_not_an_object(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(":watch"):
                return httpx.Response(200, text="event: execution\ndata: []\n\n")
            return httpx.Response(200, json=execution("STATE_QUEUED", 1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            with self.assertRaises(WorkflowError) as caught:
                await client.watch("run-1", _collect([]))
        self.assertEqual(caught.exception.code, "INVALID_RESPONSE")

    async def test_transport_and_malformed_json_stay_workflow_errors(self):
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("SECRET host")
        async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            with self.assertRaises(WorkflowError) as transport:
                await client.display_name()
        self.assertEqual(transport.exception.code, "WORKFLOWS_UNAVAILABLE")
        self.assertNotIn("SECRET", str(transport.exception))
        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"SECRET")
        async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            with self.assertRaises(WorkflowError) as payload:
                await client.display_name()
        self.assertEqual(payload.exception.code, "INVALID_RESPONSE")
        self.assertNotIn("SECRET", str(payload.exception))

    async def test_client_and_identity_errors_do_not_retry(self):
        calls = {"n": 0}
        def missing(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404, json={"status": "NOT_FOUND", "message": "missing"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(missing)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            with self.assertRaises(WorkflowError) as caught:
                await client.watch("run-1", _reject)
            self.assertEqual(caught.exception.code, "NOT_FOUND")
        self.assertEqual(calls["n"], 1)
        calls["n"] = 0
        def changed(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, text=sse(execution("STATE_ACTIVE", 2)))
        async with httpx.AsyncClient(transport=httpx.MockTransport(changed)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            with self.assertRaises(WorkflowError) as caught:
                await client.watch("run-1", _reject)
            self.assertEqual(caught.exception.code, "EXECUTION_UID_CHANGED")
        self.assertEqual(calls["n"], 1)

    async def test_unavailable_watch_is_retried(self):
        calls = {"n": 0}
        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, json={"status": "UNAVAILABLE", "message": "busy"})
            return httpx.Response(200, text=sse(execution("STATE_SUCCEEDED", 2, result={"message": "Hello, world."})))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            snapshots = []
            await client.watch("run-1", _collect(snapshots))
        self.assertEqual(calls["n"], 2)
        self.assertEqual(snapshots[0].state, "STATE_SUCCEEDED")

    async def test_create_reattaches_when_the_execution_already_exists(self):
        seen = []
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, dict(request.url.params)))
            if request.method == "POST":
                return httpx.Response(409, json={"status": "ALREADY_EXISTS", "message": "exists"})
            return httpx.Response(200, json=execution("STATE_WAITING", 2))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            created = await client.create("run-1", "I was charged twice")
        self.assertEqual(created.execution_id, "run-1")
        self.assertEqual(seen[0][0], "POST")
        self.assertEqual(seen[0][2]["executionId"], "run-1")
        self.assertEqual(seen[0][2]["requestId"], derived_request_id("run-1"))
        self.assertEqual(seen[1], ("GET", "/v1/workflows/hello-voice/executions/run-1", {}))

    async def test_create_reattaches_when_the_reason_is_aborted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(409, json={"code": 10, "message": "aborted", "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ABORTED"}]})
            return httpx.Response(200, json=execution("STATE_ACTIVE", 2))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            created = await client.create("run-1", "")
        self.assertEqual(created.uid, "11111111-1111-4111-8111-111111111111")

    async def test_create_keeps_the_original_error_when_reattach_fails(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(409, json={"status": "ALREADY_EXISTS", "message": "exists"})
            return httpx.Response(404, json={"status": "NOT_FOUND", "message": "missing"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            with self.assertRaises(WorkflowError) as caught:
                await client.create("run-1", "")
        self.assertEqual(caught.exception.code, "ALREADY_EXISTS")
        self.assertEqual(caught.exception.message, "exists")

    async def test_cancel_posts_an_empty_body(self):
        seen = []
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, json.loads(request.content), request.headers.get("sl-api-key"), request.headers.get("Authorization")))
            return httpx.Response(200, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            await client.cancel("run-1")
        self.assertEqual(seen, [("POST", "/v1/workflows/hello-voice/executions/run-1:cancel", {}, "token", None)])

    async def test_signal_rejected_errorinfo_keeps_the_step_open(self):
        body = {"code": 9, "message": "The visit is already being booked.", "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SIGNAL_REJECTED", "domain": "workflows.saleslumen.com", "metadata": {"error_code": "FAILED_PRECONDITION", "error_reason": "ADDRESS_LOCKED"}}]}
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            with self.assertRaises(WorkflowError) as caught:
                await client.signal("run-1", "7c9e6679-7425-40de-944b-e07fc1f90ae8", "confirm", {}, "workflows/hello-voice/executions/run-1/stepRuns/4", "11111111-1111-4111-8111-111111111111")
        exc = caught.exception
        self.assertEqual(exc.code, "FAILED_PRECONDITION")
        self.assertEqual(exc.reason, "SIGNAL_REJECTED")
        self.assertEqual(exc.metadata["error_reason"], "ADDRESS_LOCKED")
        self.assertEqual(exc.message, "The visit is already being booked.")
        def nested(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": body})
        async with httpx.AsyncClient(transport=httpx.MockTransport(nested)) as http:
            client = WorkflowClient(origin="https://workflows.saleslumenapis.com", saleslumen_api_key="token", organization_id="org", namespace_id="", client=http)
            client.use("hello-voice")
            with self.assertRaises(WorkflowError) as wrapped:
                await client.signal("run-1", "7c9e6679-7425-40de-944b-e07fc1f90ae8", "confirm", {}, "workflows/hello-voice/executions/run-1/stepRuns/4", "11111111-1111-4111-8111-111111111111")
        self.assertEqual(wrapped.exception.reason, "SIGNAL_REJECTED")


class ExecutionKeyTest(unittest.TestCase):
    def test_execution_key_includes_the_attempt(self):
        self.assertEqual(execution_key("run-1", 1), "run-1-1")
        self.assertEqual(execution_key("run-1", 1), execution_key("run-1", 1))
        self.assertNotEqual(execution_key("run-1", 1), execution_key("run-1", 2))
        self.assertTrue(execution_key("run-1", 2).endswith("-2"))
        long_key = "a" * 80
        shortened = execution_key(long_key, 7)
        self.assertLessEqual(len(shortened), 63)
        self.assertRegex(shortened, r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
        self.assertTrue(shortened.endswith("-7"))

    def test_lowercases_and_keeps_a_call_id(self):
        self.assertEqual(execution_key("7C9E6679-7425-40DE-944B-E07FC1F90AE7", 1), "7c9e6679-7425-40de-944b-e07fc1f90ae7-1")
        self.assertEqual(execution_key("Run-1", 3), "run-1-3")

    def test_rejects_without_echoing_the_raw_id(self):
        raw = "###"
        with self.assertRaises(WorkflowError) as caught:
            execution_key(raw, 1)
        self.assertEqual(caught.exception.code, "INVALID_CALL")
        self.assertNotIn(raw, str(caught.exception))
        with self.assertRaises(WorkflowError) as attempt:
            execution_key("run-1", 0)
        self.assertEqual(attempt.exception.code, "INVALID_CALL")

def _collect(snapshots):
    async def collect(observation):
        snapshots.append(observation)
    return collect


async def _reject(observation):
    raise WorkflowError(0, "EXECUTION_UID_CHANGED", "execution identity changed during watch")


if __name__ == "__main__":
    unittest.main()
