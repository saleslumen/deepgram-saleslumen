"""The call flow: one ``Call`` owns agents, workflows, and the active run."""

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.flows.types import (
    ConsolidatedFunctionResult,
    FlowsDirectFunctionWrapper,
    FlowsFunctionSchema,
    NO_RESPONSE,
    NodeConfig,
)

from catalog.agents import END, Agent
from catalog.workflows import Grant
from catalog.load import Catalog
from execution.client import WorkflowClient
from execution.run import Accepted, Failed, Invalid, Moved, Run, StepListener
from execution.steps import (
    FAILED_LINE,
    AnswerKind,
    Argument,
    Awaiting,
    Failed as FailedStep,
    Step,
    Succeeded,
    answer_kind,
    spoken_line,
)

START_TIMEOUT_SECS = 30
END_CALL = "end_call"


def argument_description(argument: Argument, label: str) -> str:
    """What one answer_step argument means for this question."""
    if argument.json_type == "boolean":
        return "true if they agree, false if they decline."
    return f'the caller\'s {argument.name} for: "{label}"'


def question_task(step: Awaiting) -> str:
    """The question-node task. The wording comes from the step, not from a workflow name."""
    details = " ".join(f"{item.name}: {argument_description(item, step.label)}" for item in step.arguments)
    return (
        f'The workflow asked the caller: "{step.label}". When the caller answers, call answer_step. '
        f"{details} "
        "Until answer_step returns, nothing has been done; never say it is done, submitted, or processed. "
        "If the caller asks a question instead of answering, answer it briefly and ask the workflow question again in that same reply. "
        "If the caller's latest words answer the question, including yes, yeah, or thanks, call answer_step before any other words and do not ask the question again. "
        "Otherwise ask again and do not call answer_step without an answer."
    )


def workflow_client(*, origin: str, saleslumen_api_key: str, organization_id: str, namespace_id: str) -> WorkflowClient:
    """The Workflows client for one call."""
    return WorkflowClient(
        origin=origin,
        saleslumen_api_key=saleslumen_api_key,
        organization_id=organization_id,
        namespace_id=namespace_id,
    )


class Call:
    """Builds Pipecat nodes and owns the workflow run for one phone call."""

    def __init__(
        self,
        catalog: Catalog,
        client_factory: Callable[[], WorkflowClient],
        call_key: str,
        connector_tools: dict[str, tuple[FlowsFunctionSchema, ...]] | None = None,
        on_step: StepListener | None = None,
    ) -> None:
        self.catalog = catalog
        self.client_factory = client_factory
        self.call_key = call_key
        self.connector_tools = connector_tools if connector_tools is not None else {}
        self.run: Run | None = None
        self.attempt = 0
        self.idle = 0
        self.workflow: str = ""
        self._workflow_agent: str | None = None
        self._on_step = on_step

    async def close(self) -> None:
        run = self.run
        if run is None:
            return
        self.run = None
        await run.close()

    def on_heard(self) -> None:
        self.idle = 0

    async def on_idle(self, flow_manager) -> None:
        self.idle += 1
        if self.idle == 1:
            await self.say(flow_manager, self.catalog.pipeline.lines.check_in)
            return
        await self.finish(flow_manager, self.catalog.pipeline.lines.goodbye)

    async def finish(self, flow_manager, goodbye: str | None) -> None:
        await flow_manager.set_node_from_config(self.end_node(goodbye))
        await flow_manager.worker.queue_frame(EndFrame())

    @property
    def end_call(self) -> FlowsFunctionSchema:
        call = self
        async def handler(args: dict[str, Any], flow_manager) -> ConsolidatedFunctionResult:
            await call.finish(flow_manager, None)
            return ({"status": "ending"}, NO_RESPONSE)
        return FlowsFunctionSchema(
            name=END_CALL,
            description="Say your goodbye to the caller in the same reply, then call this.",
            properties={},
            required=[],
            handler=handler,
        )

    async def say(self, flow_manager, text: str) -> None:
        await flow_manager.worker.queue_frames([
            LLMFullResponseStartFrame(),
            LLMTextFrame(text=text),
            LLMFullResponseEndFrame(),
        ])

    def start_node(self) -> NodeConfig:
        node = self.agent_node(self.catalog.pipeline.start)
        node["respond_immediately"] = False
        return node

    def agent_node(self, name: str) -> NodeConfig:
        agent = self.catalog.agents[name]
        functions: list[FlowsFunctionSchema] = [self._wrap_tool(tool, name) for tool in agent.tools]
        functions.extend(self.connector_tools.get(name, ()))
        if agent.grants:
            functions.append(self._start_workflow_schema(name))
        if agent.skills:
            functions.append(self._skill_schema(agent))
        return {
            "name": name,
            "role_message": self.catalog.shared,
            "task_messages": [{"role": "developer", "content": agent.prompt}],
            "functions": functions,
            "respond_immediately": True,
        }

    def end_node(self, goodbye: str | None) -> NodeConfig:
        call = self
        async def close_enter(action: dict[str, Any], flow_manager) -> None:
            await call.close()
        pre_actions: list[dict[str, Any]] = [{"type": "function", "handler": close_enter}]
        if goodbye is not None:
            pre_actions.append({"type": "tts_say", "text": goodbye})
        return {
            "name": "end",
            "role_message": self.catalog.shared,
            "task_messages": [],
            "functions": [],
            "respond_immediately": False,
            "pre_actions": pre_actions,
        }

    def node_for_step(self, step: Step) -> NodeConfig:
        agent_name = self._workflow_agent
        if agent_name is None:
            raise ValueError("no workflow agent")
        if isinstance(step, Awaiting):
            if answer_kind(step.arguments) == AnswerKind.NOTICE:
                return self.notice_node(step)
            return self.question_node(step)
        if isinstance(step, (Succeeded, FailedStep)):
            line = spoken_line(step)
            node = self.agent_node(agent_name)
            if line is not None:
                node["pre_actions"] = [{"type": "tts_say", "text": line}]
            node["respond_immediately"] = True
            return node
        raise ValueError("running steps are not nodes")

    def question_node(self, step: Awaiting) -> NodeConfig:
        agent_name = self._workflow_agent
        if agent_name is None:
            raise ValueError("no workflow agent")
        agent = self.catalog.agents[agent_name]
        functions: list[FlowsFunctionSchema] = [self._answer_step_schema(step)]
        if agent.skills:
            functions.append(self._skill_schema(agent))
        functions.extend(self.connector_tools.get(agent_name, ()))
        task_content = question_task(step)
        if agent.skills:
            task_content += " If the caller asks something a skill covers, call skill; do not answer that from memory."
        return {
            "name": f"step-{step.sequence}",
            "role_message": self.catalog.shared,
            "task_messages": [{"role": "developer", "content": task_content}],
            "pre_actions": [{"type": "tts_say", "text": step.label}],
            "functions": functions,
            "respond_immediately": False,
        }

    def notice_node(self, step: Awaiting) -> NodeConfig:
        agent_name = self._workflow_agent
        if agent_name is None:
            raise ValueError("no workflow agent")
        call = self
        sequence = step.sequence
        async def ack(action: dict[str, Any], flow_manager) -> None:
            logger.info("Notice ack: {}", sequence)
            run = call.run
            if run is None:
                return
            async def finish() -> None:
                try:
                    result = await run.signal(sequence, {})
                    target = call._node_after_signal(result)
                    await flow_manager.set_node_from_config(target)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Notice ack failed: {} {}", sequence, type(exc).__name__)
                    try:
                        await flow_manager.set_node_from_config(call._failed_agent_node())
                    except Exception as recovery_exc:
                        logger.warning("Notice recovery failed: {} {}", sequence, type(recovery_exc).__name__)
            run.schedule(finish())
        functions = list(self.connector_tools.get(agent_name, ()))
        return {
            "name": f"notice-{step.sequence}",
            "role_message": self.catalog.shared,
            "task_messages": [{"role": "developer", "content": "Say nothing."}],
            "pre_actions": [
                {"type": "tts_say", "text": step.label},
                {"type": "function", "handler": ack},
            ],
            "functions": functions,
            "respond_immediately": False,
        }

    def _failed_agent_node(self) -> NodeConfig:
        agent_name = self._workflow_agent
        if agent_name is None:
            raise ValueError("no workflow agent")
        node = self.agent_node(agent_name)
        node["pre_actions"] = [{"type": "tts_say", "text": FAILED_LINE}]
        node["respond_immediately"] = True
        return node

    def _node_after_signal(self, result: Accepted | Moved | Invalid | Failed) -> NodeConfig:
        if isinstance(result, (Invalid, Failed)):
            return self._failed_agent_node()
        if isinstance(result, (Accepted, Moved)):
            return self.node_for_step(result.step)
        raise ValueError("unexpected signal result")

    def _grant_descriptions(self, agent: Agent) -> str:
        return " ".join(f"{grant.name}: {grant.description}" for grant in agent.grants)

    def _grant_by_name(self, agent: Agent, name: str) -> Grant | None:
        for grant in agent.grants:
            if grant.name == name:
                return grant
        return None

    def _start_workflow_schema(self, agent_name: str) -> FlowsFunctionSchema:
        agent = self.catalog.agents[agent_name]
        names = [grant.name for grant in agent.grants]
        descriptions = self._grant_descriptions(agent)
        call = self
        async def handler(args: dict[str, Any], flow_manager) -> ConsolidatedFunctionResult:
            if call.run is not None and call.run.active:
                return ({"status": "busy"}, None)
            if call.run is not None and not call.run.active:
                await call.run.close()
                call.run = None
            workflow = args.get("workflow")
            request = args.get("request")
            if not isinstance(workflow, str) or not isinstance(request, str):
                return ({"status": "failed", "code": "INVALID"}, call.agent_node(agent_name))
            grant = call._grant_by_name(agent, workflow)
            if grant is None:
                return ({"status": "unknown_workflow"}, None)
            call.workflow = grant.name
            call.attempt += 1
            run = Run(call.client_factory(), on_step=call._on_step)
            outcome = await run.start(grant.workflow_id, request, call.call_key, call.attempt)
            if isinstance(outcome, Failed):
                return ({"status": "failed", "code": outcome.code}, call.agent_node(agent_name))
            call.run = run
            call._workflow_agent = agent_name
            return ({"status": "started"}, call.node_for_step(outcome.step))
        return FlowsFunctionSchema(
            name="start_workflow",
            description=f"Start a granted workflow. {descriptions}",
            properties={
                "workflow": {"type": "string", "enum": names, "description": descriptions},
                "request": {"type": "string", "description": "What the caller needs, in their words."},
            },
            required=["workflow", "request"],
            handler=handler,
            cancel_on_interruption=False,
            timeout_secs=START_TIMEOUT_SECS,
        )

    def _skill_descriptions(self, agent: Agent) -> str:
        return " ".join(f"{skill.name}: {' '.join(skill.description.split())}" for skill in agent.skills)

    def _skill_schema(self, agent: Agent) -> FlowsFunctionSchema:
        names = tuple(skill.name for skill in agent.skills)
        descriptions = self._skill_descriptions(agent)
        bodies = {skill.name: skill.body for skill in agent.skills}
        async def handler(args: dict[str, Any], flow_manager) -> ConsolidatedFunctionResult:
            name = args.get("name")
            if not isinstance(name, str) or name not in bodies:
                return ({"status": "unknown"}, None)
            return ({"body": bodies[name]}, None)
        return FlowsFunctionSchema(
            name="skill",
            description=f"Return the markdown body for a granted skill. Do not speak the description. {descriptions}",
            properties={"name": {"type": "string", "enum": names, "description": descriptions}},
            required=["name"],
            handler=handler,
        )

    def _wrap_tool(self, tool: Callable[..., Any], agent_name: str) -> FlowsFunctionSchema:
        wrapper = FlowsDirectFunctionWrapper(tool)
        base = wrapper.to_function_schema()
        name = tool.__name__
        call = self
        async def handler(args: dict[str, Any], flow_manager) -> ConsolidatedFunctionResult:
            result = await tool(flow_manager, **args)
            if not isinstance(result, dict):
                logger.info("Tool {} stay", name)
                return (result, None)
            outcome = result.get("outcome")
            if not isinstance(outcome, str):
                logger.info("Tool {} stay", name)
                return (result, None)
            routes = call.catalog.pipeline.routes.get(agent_name, {})
            if outcome not in routes:
                logger.info("Tool {} stay", name)
                return (result, None)
            logger.info("Tool {} {}", name, outcome)
            target = routes[outcome]
            if target == END:
                await call.finish(flow_manager, call.catalog.pipeline.lines.goodbye)
                return (result, NO_RESPONSE)
            return (result, call.agent_node(target))
        return FlowsFunctionSchema(
            name=base.name,
            description=base.description,
            properties=base.properties,
            required=base.required,
            handler=handler,
            cancel_on_interruption=wrapper.cancel_on_interruption,
            timeout_secs=wrapper.timeout_secs,
        )

    def _schema_type(self, argument: Argument) -> dict[str, str]:
        return {"type": argument.json_type}

    def _answer_step_schema(self, step: Awaiting) -> FlowsFunctionSchema:
        properties = {
            item.name: {**self._schema_type(item), "description": argument_description(item, step.label)}
            for item in step.arguments
        }
        required = [item.name for item in step.arguments]
        sequence = step.sequence
        call = self
        async def handler(args: dict[str, Any], flow_manager) -> ConsolidatedFunctionResult:
            run = call.run
            agent_name = call._workflow_agent
            if run is None:
                if agent_name is None:
                    raise ValueError("no workflow agent")
                return ({"status": "moved"}, call.agent_node(agent_name))
            result = await run.signal(sequence, dict(args))
            if isinstance(result, Invalid):
                return ({"status": "invalid", "fields": list(result.fields)}, None)
            if isinstance(result, Moved):
                return ({"status": "moved"}, call.node_for_step(result.step))
            if isinstance(result, Failed):
                return ({"status": "failed", "code": result.code}, call._failed_agent_node())
            return ({"status": "accepted"}, call.node_for_step(result.step))
        return FlowsFunctionSchema(
            name="answer_step",
            description="Answer the open workflow step once.",
            properties=properties,
            required=required,
            handler=handler,
        )
