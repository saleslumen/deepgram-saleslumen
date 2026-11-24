"""MCP connector authorization and Flows function schemas."""

import inspect
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.flows.manager import FlowManager
from pipecat.flows.types import FlowsFunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from catalog.agents import Agent, Connector
from voice.connectors import authorization, connector_tools, mcp_flows_handler

KEY = "fixture-key"
DOMAIN = "saleslumenapis.com"


class AuthorizationTest(unittest.TestCase):
    def test_https_on_our_domain_receives_the_saleslumen_api_key(self):
        headers = authorization("https://mcp.saleslumenapis.com/", KEY, DOMAIN)
        self.assertEqual(headers, {"sl-api-key": KEY})
        self.assertNotIn("Authorization", headers)

    def test_http_on_our_domain_receives_no_key(self):
        self.assertIsNone(authorization("http://mcp.saleslumenapis.com/", KEY, DOMAIN))

    def test_another_host_receives_no_key(self):
        self.assertIsNone(authorization("https://example.com/mcp", KEY, DOMAIN))


class ConnectorToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_connector_tool_schema(self):
        class FakeWrapper:
            async def __call__(self, _params) -> None:
                return None
        schema = FunctionSchema(
            name="lookup_account",
            description="Look up an account.",
            properties={"id": {"type": "string"}},
            required=["id"],
            handler=FakeWrapper(),
        )
        client = AsyncMock()
        client.tools = AsyncMock(return_value=ToolsSchema(standard_tools=[schema]))
        clients: dict = {}
        settings = MagicMock(saleslumen_api_key=KEY, saleslumen_api_domain=DOMAIN)
        agent = Agent(
            name="demo",
            prompt="prompt",
            tools=(),
            grants=(),
            skills=(),
            connectors=(Connector(name="crm", url="https://mcp.saleslumenapis.com/"),),
        )
        with patch("voice.connectors.client_for", return_value=client):
            tools = await connector_tools(agent, clients, settings)
        self.assertEqual(len(tools), 1)
        item = tools[0]
        self.assertIsInstance(item, FlowsFunctionSchema)
        self.assertEqual(item.name, "lookup_account")
        sig = inspect.signature(item.handler)
        self.assertEqual(len(sig.parameters), 2)

    async def test_mcp_handler_receives_arguments_and_a_flow_manager(self):
        recorded: list[tuple[str, dict]] = []
        class FakeWrapper:
            async def __call__(self, params) -> None:
                recorded.append((type(params).__name__, dict(params.arguments)))
                await params.result_callback("ok")
        flow_manager = MagicMock(spec=FlowManager)
        flow_manager._llm = MagicMock()
        flow_manager.worker = MagicMock()
        flow_manager._context_aggregator = MagicMock()
        flow_manager._context_aggregator.user.return_value._context = MagicMock()
        handler = mcp_flows_handler("lookup_account", FakeWrapper())
        result, nxt = await handler({"id": "42"}, flow_manager)
        self.assertIsNone(nxt)
        self.assertEqual(result, {"text": "ok"})
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0], "FunctionCallParams")
        self.assertEqual(recorded[0][1], {"id": "42"})

    async def test_connector_tools_are_built_per_agent(self):
        client = AsyncMock()
        client.tools = AsyncMock(return_value=ToolsSchema(standard_tools=[]))
        settings = MagicMock(saleslumen_api_key=KEY, saleslumen_api_domain=DOMAIN)
        with_connector = Agent(name="with", prompt="p", tools=(), grants=(), skills=(), connectors=(Connector(name="c", url="https://mcp.saleslumenapis.com/"),))
        without = Agent(name="without", prompt="p", tools=(), grants=(), skills=(), connectors=())
        clients: dict = {}
        with patch("voice.connectors.client_for", return_value=client):
            listed = await connector_tools(with_connector, clients, settings)
            empty = await connector_tools(without, clients, settings)
        self.assertEqual(listed, [])
        self.assertEqual(empty, [])
        self.assertNotIn("flow_manager", inspect.signature(connector_tools).parameters)


if __name__ == "__main__":
    unittest.main()
