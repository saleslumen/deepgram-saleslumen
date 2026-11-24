"""MCP servers named by an agent. One entry is one HTTP endpoint."""

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from mcp.client.session_group import StreamableHttpParameters
from pipecat.flows.manager import FlowManager
from pipecat.flows.types import FlowsFunctionSchema
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.mcp_service import MCPClient

from catalog.agents import Agent, Connector
from config import Config

FlowHandler = Callable[[dict[str, Any], FlowManager], Awaitable[tuple[dict[str, Any], None]]]


def authorization(url: str, saleslumen_api_key: str, domain: str) -> dict[str, str] | None:
    """The Saleslumen API key for an https host on our domain. Plain HTTP and other hosts get none."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or saleslumen_api_key == "":
        return None
    if host == domain or host.endswith("." + domain):
        return {"sl-api-key": saleslumen_api_key}
    return None


def client_for(connector: Connector, settings: Config) -> MCPClient:
    """A streamable-HTTP client. Our own https hosts receive the Saleslumen API key."""
    headers = authorization(connector.url, settings.saleslumen_api_key, settings.saleslumen_api_domain)
    return MCPClient(server_params=StreamableHttpParameters(url=connector.url, headers=headers))


def mcp_flows_handler(name: str, original: Callable[[FunctionCallParams], Awaitable[None]]) -> FlowHandler:
    """Adapt an MCP ``FunctionCallParams`` handler to Flows' two-argument form."""
    async def handler(args: dict[str, Any], flow_manager: FlowManager) -> tuple[dict[str, Any], None]:
        captured: dict[str, str] = {}
        async def result_callback(text, properties=None):
            captured["text"] = text
        context = flow_manager._context_aggregator.user()._context
        params = FunctionCallParams(
            function_name=name,
            tool_call_id=name,
            arguments=args,
            llm=flow_manager._llm,
            pipeline_worker=flow_manager.worker,
            context=context,
            result_callback=result_callback,
        )
        await original(params)
        return ({"text": captured.get("text", "")}, None)
    return handler


async def connector_tools(
    agent: Agent,
    clients: dict[tuple[str, str], MCPClient],
    settings: Config,
) -> list[FlowsFunctionSchema]:
    """Each MCP tool on a connector the agent names becomes a Flows function."""
    listed: list[FlowsFunctionSchema] = []
    owned: set[str] = set()
    for connector in agent.connectors:
        key = (connector.name, connector.url)
        client = clients.get(key)
        if client is None:
            client = client_for(connector, settings)
            clients[key] = client
        schema = await client.tools()
        for function in schema.standard_tools:
            if function.name in owned:
                continue
            handler = function.handler
            if handler is None:
                continue
            listed.append(
                FlowsFunctionSchema(
                    name=function.name,
                    description=function.description,
                    properties=function.properties,
                    required=function.required,
                    handler=mcp_flows_handler(function.name, handler),
                )
            )
            owned.add(function.name)
    return listed
