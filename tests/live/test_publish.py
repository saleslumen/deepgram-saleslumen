"""Workflow step event mapping."""

import unittest

from execution.steps import Awaiting, Failed, Running, Succeeded

from live.publish import workflow_step_event


class PublishTest(unittest.TestCase):
    def test_running_maps_to_running_state(self):
        event = workflow_step_event("call-1", "exec-1", 1, Running(), "refund")
        self.assertEqual(event.state, "running")
        self.assertEqual(event.workflow, "refund")
        self.assertEqual(event.step_path, "")
        self.assertIsNone(event.sequence)

    def test_awaiting_copies_path_and_sequence(self):
        step = Awaiting(label="Wait", signal="continue", sequence=4, step_path="confirm", arguments=())
        event = workflow_step_event("call-1", "exec-1", 2, step, "refund")
        self.assertEqual(event.state, "awaiting")
        self.assertEqual(event.workflow, "refund")
        self.assertEqual(event.step_path, "confirm")
        self.assertEqual(event.sequence, 4)
        dumped = event.model_dump()
        self.assertIn("step_path", dumped)
        self.assertIn("sequence", dumped)
        self.assertNotIn("label", dumped)

    def test_succeeded_and_failed(self):
        ok = workflow_step_event("c", "e", 3, Succeeded(result={"done": True}), "refund")
        bad = workflow_step_event("c", "e", 3, Failed(message="nope"), "refund")
        self.assertEqual(ok.state, "succeeded")
        self.assertEqual(bad.state, "failed")


if __name__ == "__main__":
    unittest.main()