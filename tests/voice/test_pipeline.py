"""Call key, teardown, and disconnect."""

import inspect
import uuid
import unittest
from unittest.mock import AsyncMock, MagicMock

import bot
from pipecat.evals.transport import EvalTransportParams
from pipecat.observers.function_call_observer import FunctionCallObserver
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.runner.types import RunnerArguments

from voice.pipeline import call_key, call_observers, disconnect_call, run_call, teardown_call
from voice.providers import build_llm


class EvalTransportTest(unittest.TestCase):
    def test_eval_transport_params_are_registered(self):
        params = bot.transport_params["eval"]()
        self.assertIsInstance(params, EvalTransportParams)
        self.assertTrue(params.audio_in_enabled)


class ObserverTest(unittest.TestCase):
    def test_function_observer_omits_arguments(self):
        observers = call_observers()
        kinds = {type(observer) for observer in observers}
        self.assertIn(FunctionCallObserver, kinds)
        self.assertIn(UserBotLatencyObserver, kinds)
        self.assertNotIn(TurnTrackingObserver, kinds)
        function_observer = next(observer for observer in observers if isinstance(observer, FunctionCallObserver))
        self.assertFalse(function_observer._include_arguments)
        self.assertFalse(function_observer._include_results)


class TeardownSignatureTest(unittest.TestCase):
    def test_teardown_and_disconnect_require_a_call(self):
        teardown = inspect.signature(teardown_call)
        disconnect = inspect.signature(disconnect_call)
        self.assertEqual(teardown.parameters["call"].default, inspect.Parameter.empty)
        self.assertEqual(disconnect.parameters["call"].default, inspect.Parameter.empty)


class SettingsSignatureTest(unittest.TestCase):
    def test_build_llm_and_run_call_take_settings_only(self):
        llm_params = list(inspect.signature(build_llm).parameters)
        self.assertEqual(llm_params, ["settings"])
        run_params = list(inspect.signature(run_call).parameters)
        self.assertEqual(run_params, ["transport", "runner_args", "catalog", "settings"])


class CallKeyTest(unittest.TestCase):
    def test_missing_call_id_is_a_uuid(self):
        args_a = RunnerArguments()
        args_b = RunnerArguments()
        key_a = call_key(args_a)
        key_b = call_key(args_b)
        uuid.UUID(key_a)
        uuid.UUID(key_b)
        self.assertNotEqual(key_a, key_b)

    def test_present_call_id_is_used(self):
        data = MagicMock()
        data.call_id = "webrtc-abc"
        args = RunnerArguments()
        args.call_data = data
        self.assertEqual(call_key(args), "webrtc-abc")


class GreetOrderTest(unittest.TestCase):
    def test_on_client_connected_says_greeting_after_initialize(self):
        source = inspect.getsource(run_call)
        self.assertNotIn("on_entered", source)
        self.assertNotIn("EndCallSilence", source)
        connect = source.split("async def on_client_connected", 1)[1]
        init_idx = connect.find("await flow_manager.initialize(call.start_node())")
        say_idx = connect.find(
            "await call.say(flow_manager, call.catalog.pipeline.lines.greeting)"
        )
        self.assertGreater(init_idx, -1)
        self.assertGreater(say_idx, -1)
        self.assertLess(init_idx, say_idx)


class TeardownTest(unittest.IsolatedAsyncioTestCase):
    async def test_teardown_closes_the_call_once(self):
        call = AsyncMock()
        clients = {("mcp", "https://example.com"): AsyncMock()}
        client = clients[("mcp", "https://example.com")]
        await teardown_call(clients, call)
        call.close.assert_awaited_once()
        client.close.assert_awaited_once()
        self.assertEqual(clients, {})
        await teardown_call(clients, call)
        self.assertEqual(call.close.await_count, 2)

    async def test_disconnect_closes_then_cancels_runner(self):
        runner = AsyncMock()
        call = AsyncMock()
        clients: dict = {}
        await disconnect_call(clients, runner, call)
        call.close.assert_awaited_once()
        runner.cancel.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
