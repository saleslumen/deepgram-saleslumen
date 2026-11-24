"""Pipecat adapter. FlowManager owns the conversation."""

import uuid

from loguru import logger
from pipecat.flows.manager import FlowManager
from pipecat.flows.types import NodeEntered
from pipecat.observers.function_call_observer import FunctionCallObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContext,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.runner.types import RunnerArguments
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from call.flow import Call, workflow_client
from catalog.load import Catalog
from config import Config
from execution.steps import Step
from live.events import CallEnded, CallStarted, FlowNodeEvent, TranscriptEvent
from live.feed import feed
from live.pipecat_graph import flow_highlight_id
from live.publish import workflow_step_event
from voice.connectors import connector_tools
from voice.providers import build_llm, build_stt, build_tts

IDLE_SECONDS = 8


def call_observers() -> list:
    function_observer = FunctionCallObserver(include_arguments=False, include_results=False)
    @function_observer.event_handler("on_function_call_event")
    async def on_function_call_event(_observer, event) -> None:
        logger.info("Function call {} {}", event.function_name, event.kind)
    latency_observer = UserBotLatencyObserver()
    @latency_observer.event_handler("on_latency_measured")
    async def on_latency_measured(_observer, latency_seconds: float) -> None:
        logger.info("User-to-bot latency {:.0f} ms", latency_seconds * 1000)
    return [function_observer, latency_observer]


def call_key(runner_args: RunnerArguments) -> str:
    data = runner_args.call_data
    if data is not None and hasattr(data, "call_id") and isinstance(data.call_id, str) and data.call_id != "":
        return data.call_id
    if isinstance(runner_args.session_id, str) and runner_args.session_id != "":
        return runner_args.session_id
    return str(uuid.uuid4())


async def run_call(
    transport: BaseTransport,
    runner_args: RunnerArguments,
    catalog: Catalog,
    settings: Config,
) -> None:
    def client_factory():
        return workflow_client(
            origin=settings.workflows_origin,
            saleslumen_api_key=settings.saleslumen_api_key,
            organization_id=settings.saleslumen_organization_id,
            namespace_id=settings.saleslumen_namespace_id,
        )
    key = call_key(runner_args)
    clients: dict[tuple[str, str], object] = {}
    connector_by_agent: dict[str, tuple] = {}
    for agent in catalog.agents.values():
        if agent.connectors:
            connector_by_agent[agent.name] = tuple(await connector_tools(agent, clients, settings))
    def on_step(execution_id: str, version: int, step: Step) -> None:
        feed.publish(workflow_step_event(key, execution_id, version, step, call.workflow))
    call = Call(catalog, client_factory, key, connector_tools=connector_by_agent, on_step=on_step)
    stt = build_stt(settings)
    tts = build_tts(settings)
    llm = build_llm(settings)
    context = LLMContext()
    user_params = LLMUserAggregatorParams(user_idle_timeout=IDLE_SECONDS)
    aggregators = LLMContextAggregatorPair(context, user_params=user_params)
    user_aggregator, assistant_aggregator = aggregators
    pipe = Pipeline([transport.input(), stt, user_aggregator, llm, tts, transport.output(), assistant_aggregator])
    worker = PipelineWorker(
        pipe,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
        observers=call_observers(),
    )
    flow_manager = FlowManager(
        llm=llm,
        context_aggregator=aggregators,
        worker=worker,
        transport=transport,
        global_functions=[call.end_call],
    )
    @flow_manager.event_handler("on_node_entered")
    async def on_node_entered(_manager, entered: NodeEntered) -> None:
        feed.publish(FlowNodeEvent(
            call_id=key,
            node=entered.node,
            graph_node=flow_highlight_id(entered.node),
            function=entered.function,
        ))
    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(
        _aggregator,
        _strategy,
        message: UserTurnStoppedMessage,
    ) -> None:
        if message.content is not None:
            text = message.content.strip()
            if text != "":
                call.on_heard()
                feed.publish(TranscriptEvent(call_id=key, role="user", text=text))
    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_aggregator, message: AssistantTurnStoppedMessage) -> None:
        text = message.content.strip()
        if text != "":
            feed.publish(TranscriptEvent(call_id=key, role="assistant", text=text))
    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(_aggregator) -> None:
        await call.on_idle(flow_manager)
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    call_started_published = False
    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client) -> None:
        nonlocal call_started_published
        logger.info("Client connected")
        call_started_published = True
        feed.publish(CallStarted(call_id=key))
        await flow_manager.initialize(call.start_node())
        await call.say(flow_manager, call.catalog.pipeline.lines.greeting)
    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client) -> None:
        logger.info("Client disconnected")
        await disconnect_call(clients, runner, call)
    try:
        await runner.run()
    finally:
        if call_started_published:
            feed.publish(CallEnded(call_id=key))
        await teardown_call(clients, call)


async def teardown_call(
    clients: dict[tuple[str, str], object],
    call: Call,
) -> None:
    """Idempotent call and MCP cleanup for hang-up and runner exit."""
    await call.close()
    keys = list(clients)
    taken: list[object] = []
    for key in keys:
        client = clients.pop(key, None)
        if client is not None:
            taken.append(client)
    for client in taken:
        await client.close()


async def disconnect_call(
    clients: dict[tuple[str, str], object],
    runner: WorkerRunner,
    call: Call,
) -> None:
    """Hang-up teardown: close the call and MCP clients, then cancel the runner."""
    await teardown_call(clients, call)
    await runner.cancel()