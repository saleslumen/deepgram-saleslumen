"""Tests for ``Call``."""

import asyncio
import inspect
import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from catalog.agents import Agent, Lines, Pipeline
from catalog.load import Catalog
from catalog.skills import Skill
from catalog.workflows import Grant
from pipecat.flows.types import FlowsFunctionSchema, NO_RESPONSE
from pipecat.flows.manager import FlowManager
from pipecat.flows.actions import ActionFinishedFrame, FunctionActionFrame
from pipecat.frames.frames import (
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMRunFrame,
    LLMSetToolsFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frameworks.rtvi.frames import RTVIFlowNodeEnteredFrame
from call.flow import Call
from execution.run import Accepted, Failed, Invalid, Moved
from execution.steps import Argument, Awaiting, Succeeded

_PIPELINE_LINES = Lines(
    greeting="Hi.",
    check_in="Are you still there?",
    goodbye="Thanks for calling. Goodbye.",
)


@dataclass
class FakeFlow:
    state: dict[str, Any]
    nodes: list[dict]
    messages: list[dict]
    worker: Any = field(default_factory=lambda: MagicMock(queue_frames=AsyncMock(), queue_frame=AsyncMock()))
    def get_current_context(self) -> list[dict]:
        return self.messages
    async def set_node_from_config(self, node: dict) -> None:
        self.nodes.append(node)


class FakeRun:
    def __init__(
        self,
        *,
        active: bool = True,
        step=None,
        start_result=None,
        signal_result=None,
    ) -> None:
        self.active = active
        self.step = step
        self.start_result = start_result
        self.signal_result = signal_result
        self.scheduled: list = []
        self.closed = False
        self.execution_id = "exec-test"
    def schedule(self, coro) -> None:
        self.scheduled.append(coro)
    async def close(self) -> None:
        self.closed = True
        self.active = False
    async def signal(self, sequence: int, payload: dict):
        return self.signal_result
    async def start(self, workflow_id: str, request: str, call_key: str, attempt: int):
        return self.start_result


def _catalog(
    *,
    tools: tuple = (),
    grants: tuple[Grant, ...] = (),
    skills: tuple[Skill, ...] = (),
    routes: dict[str, dict[str, str]] | None = None,
) -> Catalog:
    agent = Agent(
        name="verification",
        prompt="Verify the caller.",
        tools=tools,
        grants=grants,
        skills=skills,
        connectors=(),
    )
    skill = Skill(name="saleslumen", description="Product facts.", body="Saleslumen body.")
    grant = Grant(name="refund", description="Duplicate charge.", workflow_id="hello-voice")
    workflow = Agent(
        name="workflow",
        prompt="Help with workflows.",
        tools=(),
        grants=(grant,),
        skills=(skill,),
        connectors=(),
    )
    pipeline = Pipeline(start="verification", routes=routes or {}, lines=_PIPELINE_LINES)
    return Catalog(
        agents={"verification": agent, "workflow": workflow},
        pipeline=pipeline,
        skills=(skill,),
        shared="Shared.",
    )


def _call(catalog: Catalog | None = None) -> Call:
    catalog = catalog if catalog is not None else _catalog()
    return Call(catalog, lambda: object(), "call-key")


def _schema(call: Call, agent_name: str, name: str) -> Any:
    node = call.agent_node(agent_name)
    return next(item for item in node["functions"] if item.name == name)


async def _invoke(schema, args: dict, flow: FakeFlow):
    result = await schema.handler(args, flow)
    if isinstance(result, tuple):
        return result
    return result, None


class CallTest(unittest.IsolatedAsyncioTestCase):
    async def test_call_owns_the_run_and_idle(self):
        call = _call()
        flow = FakeFlow(state={}, nodes=[], messages=[])
        self.assertIsNone(call.run)
        self.assertNotIn("run", flow.state)
        await call.on_idle(flow)
        await call.on_idle(flow)
        self.assertEqual(flow.nodes[-1]["name"], "end")
        tts_actions = [item for item in flow.nodes[-1]["pre_actions"] if item.get("type") == "tts_say"]
        self.assertEqual(tts_actions[0]["text"], _PIPELINE_LINES.goodbye)
        flow.worker.queue_frame.assert_awaited()
        self.assertIsInstance(flow.worker.queue_frame.await_args[0][0], EndFrame)
        call.idle = 2
        call.on_heard()
        self.assertEqual(call.idle, 0)

    def test_start_node_waits_without_greeting_pre_action(self):
        call = _call()
        node = call.start_node()
        self.assertFalse(node["respond_immediately"])
        pre_actions = node.get("pre_actions", [])
        self.assertFalse(any(action.get("type") == "tts_say" for action in pre_actions))

    async def test_say_queues_three_llm_frames_without_tts_or_run(self):
        call = _call()
        queued: list = []
        async def capture(frames) -> None:
            queued.extend(frames)
        flow = FakeFlow(state={}, nodes=[], messages=[])
        flow.worker.queue_frames = AsyncMock(side_effect=capture)
        await call.say(flow, _PIPELINE_LINES.greeting)
        self.assertEqual(len(queued), 3)
        self.assertIsInstance(queued[0], LLMFullResponseStartFrame)
        self.assertIsInstance(queued[1], LLMTextFrame)
        self.assertEqual(queued[1].text, _PIPELINE_LINES.greeting)
        self.assertFalse(getattr(queued[1], "skip_tts", False))
        self.assertIsInstance(queued[2], LLMFullResponseEndFrame)
        self.assertFalse(any(isinstance(item, TTSSpeakFrame) for item in queued))
        self.assertFalse(any(isinstance(item, LLMRunFrame) for item in queued))

    async def test_initialize_sets_context_before_greeting_can_interrupt(self):
        call = _call()
        queued: list = []
        class RecordingWorker:
            def set_reached_downstream_filter(self, _filter) -> None:
                return None
            def event_handler(self, _name):
                def decorator(handler):
                    return handler
                return decorator
            async def queue_frame(self, frame) -> None:
                queued.append(frame)
            async def queue_frames(self, frames) -> None:
                queued.extend(frames)
        worker = RecordingWorker()
        llm = MagicMock()
        context_aggregator = MagicMock()
        context_aggregator.user.return_value._context = None
        flow_manager = FlowManager(
            llm=llm,
            context_aggregator=context_aggregator,
            worker=worker,
            global_functions=[call.end_call],
        )
        await flow_manager.initialize(call.start_node())
        init_frames = list(queued)
        self.assertFalse(any(isinstance(item, TTSSpeakFrame) for item in init_frames))
        self.assertFalse(any(isinstance(item, LLMRunFrame) for item in init_frames))
        context_frames = [
            item
            for item in init_frames
            if isinstance(item, (LLMUpdateSettingsFrame, LLMMessagesAppendFrame))
        ]
        tools_frames = [item for item in init_frames if isinstance(item, LLMSetToolsFrame)]
        self.assertTrue(context_frames)
        self.assertTrue(tools_frames)
        for frame in context_frames + tools_frames:
            self.assertFalse(frame.interruptible)
        await call.say(flow_manager, _PIPELINE_LINES.greeting)
        llm_frames = [
            item
            for item in queued
            if isinstance(
                item,
                (LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame),
            )
        ]
        self.assertEqual(len(llm_frames), 3)
        text_frame = next(item for item in llm_frames if isinstance(item, LLMTextFrame))
        self.assertEqual(text_frame.text, _PIPELINE_LINES.greeting)
        self.assertFalse(any(isinstance(item, TTSSpeakFrame) for item in queued))
        self.assertFalse(any(isinstance(item, LLMRunFrame) for item in queued))

    async def test_first_idle_speaks_check_in_without_an_llm_run(self):
        call = _call()
        queued: list = []
        async def capture(frames) -> None:
            queued.extend(frames)
        flow = FakeFlow(state={}, nodes=[], messages=[])
        flow.worker.queue_frames = AsyncMock(side_effect=capture)
        await call.on_idle(flow)
        self.assertEqual(len(queued), 3)
        self.assertIsInstance(queued[0], LLMFullResponseStartFrame)
        self.assertIsInstance(queued[1], LLMTextFrame)
        self.assertEqual(queued[1].text, _PIPELINE_LINES.check_in)
        self.assertFalse(getattr(queued[1], "skip_tts", False))
        self.assertIsInstance(queued[2], LLMFullResponseEndFrame)
        self.assertFalse(any(isinstance(item, TTSSpeakFrame) for item in queued))
        self.assertFalse(any(isinstance(item, LLMRunFrame) for item in queued))

    def test_end_node_with_goodbye_speaks_and_does_not_run_the_model(self):
        call = _call()
        node = call.end_node(_PIPELINE_LINES.goodbye)
        self.assertFalse(node["respond_immediately"])
        self.assertNotIn("post_actions", node)
        self.assertEqual(len(node["pre_actions"]), 2)
        self.assertEqual(node["pre_actions"][0]["type"], "function")
        self.assertEqual(node["pre_actions"][1]["type"], "tts_say")
        self.assertEqual(node["pre_actions"][1]["text"], _PIPELINE_LINES.goodbye)
        joined = " ".join(message.get("content", "") for message in node["task_messages"])
        self.assertNotIn("Say goodbye", joined)

    def test_end_node_without_goodbye_has_no_tts_say(self):
        call = _call()
        node = call.end_node(None)
        self.assertFalse(node["respond_immediately"])
        self.assertNotIn("post_actions", node)
        self.assertEqual(len(node["pre_actions"]), 1)
        self.assertEqual(node["pre_actions"][0]["type"], "function")

    async def test_finish_queues_frames_in_order(self):
        call = _call()
        queued: list = []
        class RecordingWorker:
            def __init__(self) -> None:
                self._handlers: dict[str, Any] = {}
            def set_reached_downstream_filter(self, _filter) -> None:
                return None
            def event_handler(self, name: str):
                def decorator(handler):
                    self._handlers[name] = handler
                    return handler
                return decorator
            async def _deliver(self, frame) -> None:
                handler = self._handlers.get("on_frame_reached_downstream")
                if handler is not None and isinstance(
                    frame, (FunctionActionFrame, ActionFinishedFrame)
                ):
                    await handler(self, frame)
            async def queue_frame(self, frame) -> None:
                queued.append(frame)
                await self._deliver(frame)
            async def queue_frames(self, frames) -> None:
                for frame in frames:
                    await self.queue_frame(frame)
        worker = RecordingWorker()
        llm = MagicMock()
        context_aggregator = MagicMock()
        context_aggregator.user.return_value._context = None
        flow_manager = FlowManager(
            llm=llm,
            context_aggregator=context_aggregator,
            worker=worker,
            global_functions=[call.end_call],
        )
        await flow_manager.initialize()
        await call.finish(flow_manager, _PIPELINE_LINES.goodbye)
        entered_idx = next(
            i for i, item in enumerate(queued) if isinstance(item, RTVIFlowNodeEnteredFrame)
        )
        goodbye_idx = next(
            i for i, item in enumerate(queued) if isinstance(item, TTSSpeakFrame)
        )
        end_idx = next(i for i, item in enumerate(queued) if isinstance(item, EndFrame))
        self.assertLess(goodbye_idx, entered_idx)
        self.assertLess(entered_idx, end_idx)
        queued_none: list = []
        class RecordingWorkerNone:
            def __init__(self) -> None:
                self._handlers: dict[str, Any] = {}
            def set_reached_downstream_filter(self, _filter) -> None:
                return None
            def event_handler(self, name: str):
                def decorator(handler):
                    self._handlers[name] = handler
                    return handler
                return decorator
            async def _deliver(self, frame) -> None:
                handler = self._handlers.get("on_frame_reached_downstream")
                if handler is not None and isinstance(
                    frame, (FunctionActionFrame, ActionFinishedFrame)
                ):
                    await handler(self, frame)
            async def queue_frame(self, frame) -> None:
                queued_none.append(frame)
                await self._deliver(frame)
            async def queue_frames(self, frames) -> None:
                for frame in frames:
                    await self.queue_frame(frame)
        worker_none = RecordingWorkerNone()
        flow_none = FlowManager(
            llm=MagicMock(),
            context_aggregator=MagicMock(user=MagicMock(return_value=MagicMock(_context=None))),
            worker=worker_none,
            global_functions=[call.end_call],
        )
        await flow_none.initialize(call.start_node())
        await call.finish(flow_none, None)
        entered_none_idx = max(
            i for i, item in enumerate(queued_none) if isinstance(item, RTVIFlowNodeEnteredFrame)
        )
        self.assertFalse(any(isinstance(item, TTSSpeakFrame) for item in queued_none))
        end_none_idx = next(i for i, item in enumerate(queued_none) if isinstance(item, EndFrame))
        self.assertLess(entered_none_idx, end_none_idx)

    async def test_one_log_line_per_tool_call(self):
        async def forward(flow_manager, value: str) -> dict:
            """Forward an outcome.
            Args:
                value: The outcome name.
            """
            return {"status": "ok", "outcome": value}
        catalog = _catalog(tools=(forward,), routes={"verification": {"accepted": "workflow"}})
        call = _call(catalog)
        schema = _schema(call, "verification", "forward")
        flow = FakeFlow(state={}, nodes=[], messages=[])
        with patch("call.flow.logger.info") as info:
            await _invoke(schema, {"value": "accepted"}, flow)
            self.assertEqual(info.call_count, 1)
            self.assertEqual(info.call_args[0][1], "forward")
            self.assertEqual(info.call_args[0][2], "accepted")

    async def test_replacing_a_terminal_run_closes_the_old_one(self):
        catalog = _catalog()
        call = _call(catalog)
        old = FakeRun(active=False)
        call.run = old
        call._workflow_agent = "workflow"
        step = Awaiting("Wait", "wait", 1, "wait", ())
        new_run = FakeRun(active=True, start_result=Accepted(step))
        flow = FakeFlow(state={}, nodes=[], messages=[])
        schema = _schema(call, "workflow", "start_workflow")
        with patch("call.flow.Run", return_value=new_run):
            await _invoke(schema, {"workflow": "refund", "request": "help"}, flow)
        self.assertTrue(old.closed)
        self.assertIs(call.run, new_run)

    async def test_end_node_closes_the_run(self):
        call = _call()
        run = FakeRun()
        call.run = run
        node = call.end_node(_PIPELINE_LINES.goodbye)
        handler = node["pre_actions"][0]["handler"]
        await handler({"type": "function"}, FakeFlow({}, [], []))
        self.assertTrue(run.closed)
        self.assertIsNone(call.run)

    async def test_unknown_workflow_stays(self):
        call = _call()
        before = call.attempt
        flow = FakeFlow(state={}, nodes=[], messages=[])
        schema = _schema(call, "workflow", "start_workflow")
        with patch("call.flow.Run") as run_type:
            result, nxt = await _invoke(schema, {"workflow": "missing", "request": "help"}, flow)
        self.assertEqual(result["status"], "unknown_workflow")
        self.assertIsNone(nxt)
        self.assertEqual(call.attempt, before)
        run_type.assert_not_called()

    async def test_outcome_routes_to_the_named_agent(self):
        async def forward(flow_manager, value: str) -> dict:
            """Forward an outcome.
            Args:
                value: The outcome name.
            """
            return {"status": "ok", "outcome": value}
        catalog = _catalog(tools=(forward,), routes={"verification": {"accepted": "workflow"}})
        call = _call(catalog)
        schema = _schema(call, "verification", "forward")
        flow = FakeFlow(state={}, nodes=[], messages=[])
        result, nxt = await _invoke(schema, {"value": "accepted"}, flow)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(nxt["name"], "workflow")

    async def test_unknown_outcome_stays(self):
        async def forward(flow_manager, value: str) -> dict:
            """Forward an outcome.
            Args:
                value: The outcome name.
            """
            return {"status": "ok", "outcome": value}
        catalog = _catalog(tools=(forward,), routes={"verification": {"accepted": "workflow"}})
        call = _call(catalog)
        schema = _schema(call, "verification", "forward")
        flow = FakeFlow(state={}, nodes=[], messages=[])
        result, nxt = await _invoke(schema, {"value": "missing"}, flow)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(nxt)

    async def test_start_workflow_busy_while_active(self):
        call = _call()
        call.run = FakeRun(active=True)
        flow = FakeFlow(state={}, nodes=[], messages=[])
        schema = _schema(call, "workflow", "start_workflow")
        result, nxt = await _invoke(schema, {"workflow": "refund", "request": "help"}, flow)
        self.assertEqual(result["status"], "busy")
        self.assertIsNone(nxt)

    async def test_step_nodes_use_the_agent_that_started_the_run(self):
        call = _call()
        flow = FakeFlow(state={}, nodes=[], messages=[])
        question = Awaiting("Confirm?", "confirm", 3, "confirm", (Argument("accepted", "boolean"),))
        notice = Awaiting("Please wait.", "wait", 2, "wait", ())
        new_run = FakeRun(start_result=Accepted(question))
        schema = _schema(call, "workflow", "start_workflow")
        with patch("call.flow.Run", return_value=new_run):
            await _invoke(schema, {"workflow": "refund", "request": "help"}, flow)
        self.assertEqual(call._workflow_agent, "workflow")
        self.assertEqual(list(inspect.signature(call.question_node).parameters), ["step"])
        self.assertEqual(list(inspect.signature(call.notice_node).parameters), ["step"])
        q_node = call.question_node(question)
        self.assertEqual(q_node["name"], "step-3")
        self.assertTrue(any(item.name == "skill" for item in q_node["functions"]))
        n_node = call.notice_node(notice)
        self.assertEqual(n_node["name"], "notice-2")

    async def test_question_node_speaks_the_label_and_waits(self):
        call = _call()
        call._workflow_agent = "workflow"
        step = Awaiting("Confirm?", "confirm", 3, "confirm", (Argument("accepted", "boolean"),))
        node = call.question_node(step)
        self.assertEqual(node["name"], "step-3")
        self.assertFalse(node["respond_immediately"])
        self.assertEqual(node["pre_actions"][0], {"type": "tts_say", "text": "Confirm?"})

    def test_question_task_states_the_step_and_the_arguments(self):
        call = _call()
        call._workflow_agent = "workflow"
        label = "I found a duplicate charge of twenty dollars. Please confirm the refund"
        step = Awaiting(label, "confirm", 3, "confirm", (Argument("accepted", "boolean"),))
        task = call.question_node(step)["task_messages"][0]["content"]
        self.assertEqual(
            task,
            'The workflow asked the caller: "I found a duplicate charge of twenty dollars. Please confirm the refund". When the caller answers, call answer_step. accepted: true if they agree, false if they decline. Until answer_step returns, nothing has been done; never say it is done, submitted, or processed. If the caller asks a question instead of answering, answer it briefly and ask the workflow question again in that same reply. If the caller\'s latest words answer the question, including yes, yeah, or thanks, call answer_step before any other words and do not ask the question again. Otherwise ask again and do not call answer_step without an answer. If the caller asks something a skill covers, call skill; do not answer that from memory.',
        )
        schema = call._answer_step_schema(step)
        self.assertEqual(schema.properties["accepted"]["type"], "boolean")
        self.assertEqual(schema.properties["accepted"]["description"], "true if they agree, false if they decline.")
        counted = Awaiting("How many?", "qty", 1, "qty", (Argument("count", "integer"),))
        counted_task = call.question_node(counted)["task_messages"][0]["content"]
        self.assertIn('count: the caller\'s count for: "How many?"', counted_task)
        counted_schema = call._answer_step_schema(counted)
        self.assertEqual(counted_schema.properties["count"]["description"], 'the caller\'s count for: "How many?"')

    def test_connector_tools_are_on_step_nodes(self):
        async def lookup(args: dict, flow_manager) -> tuple:
            return ({"status": "ok"}, None)
        lookup_schema = FlowsFunctionSchema(
            name="lookup",
            description="Lookup.",
            properties={},
            required=[],
            handler=lookup,
        )
        call = Call(_catalog(), lambda: object(), "key", connector_tools={"workflow": (lookup_schema,)})
        call._workflow_agent = "workflow"
        question = Awaiting("Q?", "q", 1, "q", (Argument("x", "string"),))
        notice = Awaiting("N", "n", 2, "n", ())
        q_names = [item.name for item in call.question_node(question)["functions"]]
        n_names = [item.name for item in call.notice_node(notice)["functions"]]
        self.assertIn("lookup", q_names)
        self.assertIn("lookup", n_names)
        self.assertEqual(call.end_node(_PIPELINE_LINES.goodbye)["functions"], [])

    async def test_notice_ack_schedules_and_does_not_await(self):
        call = _call()
        call._workflow_agent = "workflow"
        step = Awaiting("Please wait.", "wait", 2, "wait", ())
        call.run = FakeRun(signal_result=Accepted(step))
        flow = FakeFlow(state={}, nodes=[], messages=[])
        node = call.notice_node(step)
        ack = node["pre_actions"][1]["handler"]
        started = asyncio.get_event_loop().time()
        await ack({"type": "function"}, flow)
        elapsed = asyncio.get_event_loop().time() - started
        self.assertLess(elapsed, 0.05)
        self.assertEqual(len(call.run.scheduled), 1)
        await call.run.scheduled[0]
        self.assertEqual(flow.nodes[-1]["name"], "notice-2")

    async def test_invalid_notice_speaks_the_failed_line(self):
        call = _call()
        call._workflow_agent = "workflow"
        step = Awaiting("Please wait.", "wait", 2, "wait", ())
        call.run = FakeRun(signal_result=Invalid(()))
        flow = FakeFlow(state={}, nodes=[], messages=[])
        node = call.notice_node(step)
        await node["pre_actions"][1]["handler"]({"type": "function"}, flow)
        await call.run.scheduled[0]
        self.assertEqual(flow.nodes[-1]["pre_actions"][0]["text"], "The workflow failed.")

    async def test_notice_ack_exception_recovers_to_failed_agent_node(self):
        call = _call()
        call._workflow_agent = "workflow"
        step = Awaiting("Please wait.", "wait", 2, "wait", ())
        run = FakeRun()
        run.signal = AsyncMock(side_effect=RuntimeError("boom"))
        call.run = run
        flow = FakeFlow(state={}, nodes=[], messages=[])
        node = call.notice_node(step)
        await node["pre_actions"][1]["handler"]({"type": "function"}, flow)
        await call.run.scheduled[0]
        self.assertEqual(flow.nodes[-1]["name"], "workflow")
        self.assertEqual(flow.nodes[-1]["pre_actions"][0]["text"], "The workflow failed.")

    async def test_terminal_node_speaks_the_line_and_responds(self):
        call = _call()
        call._workflow_agent = "workflow"
        step = Succeeded({"message": "Your refund is on the way."})
        node = call.node_for_step(step)
        self.assertTrue(node["respond_immediately"])
        self.assertEqual(node["pre_actions"][0]["text"], "Your refund is on the way.")

    async def test_answer_step_accepted(self):
        call = _call()
        call._workflow_agent = "workflow"
        open_step = Awaiting("Confirm?", "confirm", 3, "confirm", (Argument("accepted", "boolean"),))
        next_step = Succeeded({"message": "Done."})
        call.run = FakeRun(step=open_step, signal_result=Accepted(next_step))
        flow = FakeFlow(state={}, nodes=[], messages=[])
        schema = call._answer_step_schema(open_step)
        result, nxt = await _invoke(schema, {"accepted": True}, flow)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(nxt["name"], "workflow")

    async def test_invalid_answer_stays(self):
        call = _call()
        call._workflow_agent = "workflow"
        open_step = Awaiting("Confirm?", "confirm", 3, "confirm", (Argument("accepted", "boolean"),))
        call.run = FakeRun(step=open_step, signal_result=Invalid(("accepted",)))
        flow = FakeFlow(state={}, nodes=[], messages=[])
        schema = call._answer_step_schema(open_step)
        result, nxt = await _invoke(schema, {}, flow)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["fields"], ["accepted"])
        self.assertIsNone(nxt)

    async def test_answer_step_failed_returns_to_the_agent(self):
        call = _call()
        call._workflow_agent = "workflow"
        open_step = Awaiting("Confirm?", "confirm", 3, "confirm", (Argument("accepted", "boolean"),))
        call.run = FakeRun(step=open_step, signal_result=Failed("REJECTED"))
        flow = FakeFlow(state={}, nodes=[], messages=[])
        schema = call._answer_step_schema(open_step)
        result, nxt = await _invoke(schema, {"accepted": False}, flow)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "REJECTED")
        self.assertEqual(nxt["name"], "workflow")
        self.assertEqual(nxt["pre_actions"][0]["text"], "The workflow failed.")

    async def test_skill_tool_returns_the_body(self):
        call = _call()
        schema = _schema(call, "workflow", "skill")
        self.assertIn("saleslumen: Product facts.", schema.description)
        self.assertIn("saleslumen: Product facts.", schema.properties["name"]["description"])
        flow = FakeFlow(state={}, nodes=[], messages=[])
        result, nxt = await _invoke(schema, {"name": "saleslumen"}, flow)
        self.assertEqual(result["body"], "Saleslumen body.")
        self.assertIsNone(nxt)

    async def test_end_call_finishes_without_closing_run(self):
        call = _call()
        run = FakeRun()
        call.run = run
        flow = FakeFlow(state={}, nodes=[], messages=[])
        result, nxt = await _invoke(call.end_call, {}, flow)
        self.assertEqual(result["status"], "ending")
        self.assertIs(nxt, NO_RESPONSE)
        node = flow.nodes[-1]
        self.assertEqual(node["name"], "end")
        self.assertFalse(any(action.get("type") == "tts_say" for action in node["pre_actions"]))
        self.assertFalse(run.closed)
        flow.worker.queue_frame.assert_awaited()
        self.assertIsInstance(flow.worker.queue_frame.await_args[0][0], EndFrame)

    def test_end_call_schema_has_no_arguments(self):
        call = _call()
        schema = call.end_call
        self.assertEqual(schema.required, [])
        self.assertEqual(schema.properties, {})


if __name__ == "__main__":
    unittest.main()
