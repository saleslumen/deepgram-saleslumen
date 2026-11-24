"""Live feed ordering, replay, bounds, and call isolation."""

import unittest
from unittest import mock

from pydantic import ValidationError

from live.events import (
    CallStarted,
    FlowNodeEvent,
    TranscriptEvent,
    WorkflowStepEvent,
)
from live import feed as feed_module
from live.feed import Feed, REPLAY_LIMIT


class FeedOrderingTest(unittest.TestCase):
    def test_publish_order_seen_by_early_subscriber(self):
        feed = Feed()
        queue = feed.subscribe()
        cid = "call-1"
        events = [
            CallStarted(call_id=cid),
            TranscriptEvent(call_id=cid, role="user", text="hello"),
            TranscriptEvent(call_id=cid, role="assistant", text="hi"),
            FlowNodeEvent(call_id=cid, node="verify", graph_node="start"),
        ]
        for event in events:
            feed.publish(event)
        received = [queue.get_nowait() for _ in range(len(events))]
        self.assertEqual(received, events)


class FeedReplayTest(unittest.TestCase):
    def test_late_subscriber_gets_call_started_then_others(self):
        feed = Feed()
        cid = "call-replay"
        feed.publish(CallStarted(call_id=cid))
        feed.publish(TranscriptEvent(call_id=cid, role="user", text="one"))
        feed.publish(FlowNodeEvent(call_id=cid, node="agent", graph_node="g1"))
        queue = feed.subscribe()
        self.assertIsInstance(queue.get_nowait(), CallStarted)
        self.assertEqual(queue.get_nowait().kind, "transcript")
        self.assertEqual(queue.get_nowait().kind, "flow_node")
        feed.publish(TranscriptEvent(call_id=cid, role="assistant", text="two"))
        self.assertEqual(queue.get_nowait().kind, "transcript")


class FeedBoundedTest(unittest.TestCase):
    def test_replay_keeps_call_started_and_newest_others(self):
        feed = Feed()
        cid = "bounded"
        feed.publish(CallStarted(call_id=cid))
        for index in range(REPLAY_LIMIT + 2):
            feed.publish(TranscriptEvent(call_id=cid, role="user", text=f"t{index}"))
        queue = feed.subscribe()
        self.assertEqual(queue.get_nowait().kind, "call_started")
        replayed = [queue.get_nowait() for _ in range(REPLAY_LIMIT)]
        self.assertEqual(len(replayed), REPLAY_LIMIT)
        self.assertEqual(getattr(replayed[0], "text", None), "t2")
        self.assertEqual(getattr(replayed[-1], "text", None), f"t{REPLAY_LIMIT + 1}")


class FeedBoundedQueueTest(unittest.TestCase):
    def test_full_queue_ends_stream(self):
        with mock.patch.object(feed_module, "REPLAY_LIMIT", 2):
            feed = Feed()
            cid = "queue-end"
            feed.publish(CallStarted(call_id=cid))
            queue = feed.subscribe()
            feed.publish(TranscriptEvent(call_id=cid, role="user", text="a"))
            feed.publish(TranscriptEvent(call_id=cid, role="user", text="b"))
            feed.publish(TranscriptEvent(call_id=cid, role="user", text="c"))
            self.assertIs(queue.get_nowait(), feed_module.STREAM_END)
            fresh = feed.subscribe()
            self.assertEqual(fresh.get_nowait().kind, "call_started")
            replayed = [fresh.get_nowait(), fresh.get_nowait()]
            self.assertEqual(getattr(replayed[0], "text", None), "b")
            self.assertEqual(getattr(replayed[1], "text", None), "c")
            self.assertTrue(fresh.empty())


class FeedCallIsolationTest(unittest.TestCase):
    def test_new_call_ends_subscribers_and_isolates_replay(self):
        feed = Feed()
        queue = feed.subscribe()
        feed.publish(CallStarted(call_id="A"))
        feed.publish(TranscriptEvent(call_id="A", role="user", text="from-a"))
        self.assertEqual(queue.get_nowait().call_id, "A")
        self.assertEqual(getattr(queue.get_nowait(), "text", None), "from-a")
        feed.publish(CallStarted(call_id="B"))
        ended = queue.get_nowait()
        self.assertIs(ended, feed_module.STREAM_END)
        self.assertTrue(queue.empty())
        feed.publish(TranscriptEvent(call_id="B", role="user", text="from-b"))
        feed.publish(TranscriptEvent(call_id="A", role="user", text="late-a"))
        late_queue = feed.subscribe()
        self.assertEqual(late_queue.get_nowait().call_id, "B")
        self.assertEqual(getattr(late_queue.get_nowait(), "text", None), "from-b")
        self.assertTrue(late_queue.empty())


class FeedDropBeforeStartTest(unittest.TestCase):
    def test_events_before_call_started_are_dropped(self):
        feed = Feed()
        feed.publish(TranscriptEvent(call_id="ghost", role="user", text="x"))
        feed.publish(FlowNodeEvent(call_id="ghost", node="n", graph_node="g"))
        queue = feed.subscribe()
        self.assertTrue(queue.empty())
        feed.publish(CallStarted(call_id="real"))
        feed.publish(TranscriptEvent(call_id="real", role="user", text="ok"))
        self.assertEqual(queue.get_nowait().kind, "call_started")


class FeedUnsubscribeTest(unittest.TestCase):
    def test_unsubscribe_stops_delivery(self):
        feed = Feed()
        cid = "unsub"
        feed.publish(CallStarted(call_id=cid))
        queue = feed.subscribe()
        queue.get_nowait()
        feed.unsubscribe(queue)
        feed.publish(TranscriptEvent(call_id=cid, role="user", text="after"))
        self.assertTrue(queue.empty())


class FeedDuplicateStartTest(unittest.TestCase):
    def test_second_call_started_same_id_keeps_transcript(self):
        feed = Feed()
        cid = "same"
        queue = feed.subscribe()
        feed.publish(CallStarted(call_id=cid))
        feed.publish(TranscriptEvent(call_id=cid, role="user", text="keep"))
        feed.publish(CallStarted(call_id=cid))
        items = [queue.get_nowait() for _ in range(2)]
        self.assertEqual(items[0].kind, "call_started")
        self.assertEqual(getattr(items[1], "text", None), "keep")
        self.assertTrue(queue.empty())


class WorkflowStepEventTest(unittest.TestCase):
    def test_awaiting_and_workflow_field(self):
        cid = "wf"
        with self.assertRaises(ValidationError):
            WorkflowStepEvent(call_id=cid, execution_id="ex-1", version=1, state="awaiting", workflow="refund", step_path="", sequence=1)
        with self.assertRaises(ValidationError):
            WorkflowStepEvent(call_id=cid, execution_id="ex-1", version=1, state="awaiting", workflow="refund", step_path="step", sequence=None)
        ok = WorkflowStepEvent(call_id=cid, execution_id="ex-1", version=2, state="awaiting", workflow="refund", step_path="confirm", sequence=3)
        self.assertEqual(ok.workflow, "refund")
        self.assertEqual(ok.sequence, 3)


if __name__ == "__main__":
    unittest.main()