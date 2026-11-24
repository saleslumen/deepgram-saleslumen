import unittest
from pathlib import Path

from call.flow import END_CALL, Call
from catalog.load import load
from execution.steps import Argument, Awaiting
from live.pipecat_graph import WORKFLOW_STEP_ID, flow_highlight_id, pipecat_graph


class PipecatGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load(Path(__file__).resolve().parents[2])

    def test_graph_nodes_and_captions(self):
        call = Call(self.catalog, lambda: object(), "call-key")
        self.assertEqual(call.end_call.name, END_CALL)
        graph = pipecat_graph(self.catalog, (END_CALL,))
        ids = tuple(node.id for node in graph.nodes)
        self.assertEqual(ids, ("verification", "workflow", "workflow-step", "end", "global"))
        by_id = {node.id: node for node in graph.nodes}
        self.assertEqual(by_id["verification"].caption, "find_account, check_passcode, saleslumen")
        self.assertEqual(by_id["workflow"].caption, "saleslumen, refund")
        self.assertEqual(by_id["workflow-step"].kind, "workflow_step")
        self.assertEqual(by_id["global"].caption, END_CALL)

    def test_flow_highlight_and_step_nodes(self):
        call = Call(self.catalog, lambda: object(), "call-key")
        call._workflow_agent = "workflow"
        question = Awaiting(
            label="Confirm?",
            signal="confirm",
            sequence=7,
            step_path="confirm",
            arguments=(Argument("accepted", "boolean"),),
        )
        notice = Awaiting(
            label="Notice.",
            signal="speech_complete",
            sequence=9,
            step_path="review_notice",
            arguments=(),
        )
        start_name = call.start_node()["name"]
        end_name = call.end_node(call.catalog.pipeline.lines.goodbye)["name"]
        workflow_name = call.agent_node("workflow")["name"]
        qname = call.question_node(question)["name"]
        nname = call.notice_node(notice)["name"]
        self.assertRegex(qname, r"^step-\d+$")
        self.assertRegex(nname, r"^notice-\d+$")
        self.assertEqual(flow_highlight_id(start_name), start_name)
        self.assertEqual(flow_highlight_id(end_name), end_name)
        self.assertEqual(flow_highlight_id(workflow_name), workflow_name)
        self.assertEqual(flow_highlight_id(qname), WORKFLOW_STEP_ID)
        self.assertEqual(flow_highlight_id(nname), WORKFLOW_STEP_ID)

    def test_graph_edges(self):
        graph = pipecat_graph(self.catalog, (END_CALL,))
        labels = {(edge.source, edge.to, edge.label) for edge in graph.edges}
        self.assertIn(("verification", "workflow", "verified"), labels)
        self.assertIn(("verification", "end", "refused"), labels)
        self.assertIn(("workflow", "workflow-step", "start_workflow / answer_step"), labels)
        self.assertFalse(any(edge.source == WORKFLOW_STEP_ID for edge in graph.edges))
        end_call_label = END_CALL
        for edge in graph.edges:
            self.assertNotEqual(edge.label, end_call_label)
            self.assertNotIn(edge.label, ("ack", "idle", "skill"))
            self.assertNotIn("global", (edge.source, edge.to))

    def test_edge_dump_uses_from_alias(self):
        graph = pipecat_graph(self.catalog, (END_CALL,))
        payload = graph.edges[0].model_dump(mode="json", by_alias=True)
        self.assertIn("from", payload)
        self.assertNotIn("from_", payload)
        self.assertNotIn("source", payload)


if __name__ == "__main__":
    unittest.main()