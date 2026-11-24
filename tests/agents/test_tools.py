"""Verification tools find_account and check_passcode."""

import inspect
import unittest

from pipecat.flows.types import FlowsDirectFunctionWrapper

from agents.verification.tools import (
    ACCOUNT_MISSES,
    ACCOUNT_NAME,
    PASSCODE_ATTEMPTS,
    check_passcode,
    find_account,
)


class FakeFlow:
    def __init__(self, messages: list[dict] | None = None) -> None:
        self.state: dict = {}
        self._messages = messages or []

    def get_current_context(self) -> list[dict]:
        return self._messages


class VerificationToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_find_account_stores_name_without_leaking_number(self):
        speech = "five five five zero one zero two zero zero zero"
        flow = FakeFlow([{"role": "user", "content": speech}])
        result = await find_account(flow, speech)
        self.assertEqual(result, {"status": "found", "name": "Jordan"})
        self.assertEqual(flow.state[ACCOUNT_NAME], "Jordan")
        flow2 = FakeFlow([{"role": "user", "content": "5550199999"}])
        missing = await find_account(flow2, "5550199999")
        self.assertEqual(missing, {"status": "not_found"})

    async def test_find_account_requires_the_callers_digits(self):
        flow = FakeFlow([{"role": "user", "content": "hello"}])
        result = await find_account(flow, "five five five zero one zero two zero zero zero")
        self.assertEqual(result, {"status": "not_found"})
        self.assertNotIn(ACCOUNT_NAME, flow.state)

    async def test_third_account_miss_refuses(self):
        flow = FakeFlow([{"role": "user", "content": "wrong"}])
        for _ in range(2):
            result = await find_account(flow, "5550102000")
            self.assertEqual(result, {"status": "not_found"})
        refused = await find_account(flow, "5550102000")
        self.assertEqual(refused, {"status": "refused", "outcome": "refused"})
        self.assertEqual(flow.state[ACCOUNT_MISSES], 3)
        self.assertNotIn(PASSCODE_ATTEMPTS, flow.state)

    async def test_check_passcode_not_ready_without_account(self):
        flow = FakeFlow([{"role": "user", "content": "one two three"}])
        result = await check_passcode(flow, "one two three")
        self.assertEqual(result, {"status": "not_ready"})
        self.assertNotIn(PASSCODE_ATTEMPTS, flow.state)

    async def test_check_passcode_verifies_matching_spoken_code(self):
        flow = FakeFlow([{"role": "user", "content": "one two three"}])
        flow.state[ACCOUNT_NAME] = "Jordan"
        result = await check_passcode(flow, "one two three")
        self.assertEqual(result, {"status": "verified", "outcome": "verified"})

    async def test_model_code_without_caller_words_is_mismatch(self):
        flow = FakeFlow([{"role": "user", "content": "hello there"}])
        flow.state[ACCOUNT_NAME] = "Jordan"
        result = await check_passcode(flow, "one two three")
        self.assertEqual(result, {"status": "mismatch", "attempts": 1})

    async def test_third_mismatch_refuses(self):
        flow = FakeFlow([{"role": "user", "content": "nope"}])
        flow.state[ACCOUNT_NAME] = "Jordan"
        for expected, attempt in ((1, 1), (2, 2)):
            result = await check_passcode(flow, "one two four")
            self.assertEqual(result, {"status": "mismatch", "attempts": expected})
            self.assertEqual(flow.state[PASSCODE_ATTEMPTS], attempt)
        refused = await check_passcode(flow, "one two four")
        self.assertEqual(refused, {"status": "refused", "outcome": "refused"})

    def test_flows_direct_wrapper_accepts_docstrings(self):
        for tool in (find_account, check_passcode):
            wrapper = FlowsDirectFunctionWrapper(tool)
            self.assertEqual(wrapper.name, tool.__name__)
            schema = wrapper.to_function_schema()
            self.assertTrue(inspect.iscoroutinefunction(tool))
            if tool.__name__ == "find_account":
                self.assertIn("number", schema.required)
            else:
                self.assertIn("code", schema.required)


if __name__ == "__main__":
    unittest.main()
