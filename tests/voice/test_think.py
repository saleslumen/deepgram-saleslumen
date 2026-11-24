"""Think request filtering and Deepgram Think settings."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from pipecat.frames.frames import LLMUpdateSettingsFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import LLMSettings

from voice.providers import DeepgramThinkLLMService, _instruction
from voice.think import ALLOWED_FIELDS, ThinkRequestError, collect_think_chunks, sanitize_message, think_request


class ThinkRequestTest(unittest.TestCase):
    def test_request_keeps_only_accepted_fields(self):
        body = think_request(
            model="think-fast",
            messages=[
                {"role": "system", "content": "Be brief.", "name": "ignored"},
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "end_call", "arguments": {"unused": True}}, "extra": 1}], "refusal": None},
                {"role": "tool", "tool_call_id": "call-1", "content": {"observation": "ok"}, "extra": True},
            ],
            tools=[{"type": "function", "function": {"name": "end_call", "description": "End the call.", "parameters": {"type": "object", "properties": {}}, "strict": True}}],
            temperature=None,
        )
        self.assertTrue(set(body) <= ALLOWED_FIELDS)
        self.assertNotIn("tool_choice", body)
        self.assertNotIn("temperature", body)
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Be brief."})
        self.assertEqual(body["messages"][1]["content"], "Hello")
        self.assertEqual(body["messages"][2]["tool_calls"][0]["function"]["arguments"], '{"unused":true}')
        self.assertNotIn("content", body["messages"][2])
        self.assertEqual(body["messages"][3]["content"], '{"observation":"ok"}')
        self.assertNotIn("strict", body["tools"][0]["function"])

    def test_developer_message_is_sent_as_system(self):
        mapped = sanitize_message({"role": "developer", "content": "Ask for the code."})
        self.assertEqual(mapped, {"role": "system", "content": "Ask for the code."})
        body = think_request(
            model="think-fast",
            messages=[
                {"role": "developer", "content": "Task step."},
                {"role": "user", "content": "Go."},
            ],
            tools=None,
            temperature=None,
        )
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Task step."})

    def test_think_request_rejects_a_body_with_no_user_message(self):
        for messages in (
            [{"role": "system", "content": "a"}, {"role": "system", "content": "b"}],
            [{"role": "system", "content": "a"}, {"role": "system", "content": "b"}, {"role": "assistant", "content": "c"}],
        ):
            with self.assertRaises(ThinkRequestError) as raised:
                think_request(model="think-fast", messages=messages, tools=None, temperature=None)
            self.assertIn("no user message", str(raised.exception).lower())
        body = think_request(
            model="think-fast",
            messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            tools=None,
            temperature=None,
        )
        self.assertTrue(any(message.get("role") == "user" for message in body["messages"]))

    def test_stream_requires_done(self):
        chunks = collect_think_chunks(["data: {\"id\":\"1\",\"choices\":[]}", "", "data: [DONE]"])
        self.assertEqual(chunks, [{"id": "1", "choices": []}])
        with self.assertRaises(RuntimeError):
            collect_think_chunks(["data: {\"id\":\"1\",\"choices\":[]}"])


class ThinkServiceTest(unittest.TestCase):
    def test_think_does_not_declare_supports_developer_role(self):
        self.assertNotIn("supports_developer_role", DeepgramThinkLLMService.__dict__)


class ThinkSettingsTest(unittest.IsolatedAsyncioTestCase):
    async def test_system_instruction_from_settings_frame_reaches_think_request(self):
        service = DeepgramThinkLLMService(
            deepgram_api_key="unused",
            settings=DeepgramThinkLLMService.Settings(model="think-fast", system_instruction="Initial."),
        )
        frame = LLMUpdateSettingsFrame(delta=LLMSettings(system_instruction="From the flow node."))
        await service.process_frame(frame, FrameDirection.DOWNSTREAM)
        context = LLMContext(messages=[{"role": "user", "content": "Hi"}])
        params = await service.get_llm_adapter().get_llm_invocation_params(
            context,
            system_instruction=_instruction(service._settings.system_instruction),
            convert_developer_to_user=False,
        )
        body = think_request(model="think-fast", messages=list(params["messages"]), tools=None, temperature=None)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "From the flow node."})

    async def test_think_service_reuses_and_closes_http_client(self):
        service = DeepgramThinkLLMService(
            deepgram_api_key="unused",
            settings=DeepgramThinkLLMService.Settings(model="think-fast"),
        )
        first = service._http()
        second = service._http()
        self.assertIs(first, second)
        self.assertFalse(first.is_closed)
        await service.cleanup()
        self.assertTrue(first.is_closed)
        self.assertIsNone(service._http_client)


if __name__ == "__main__":
    unittest.main()
