"""Live view HTTP routes."""

import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from call.flow import END_CALL, Call
from catalog import load as load_catalog
from live.feed import Feed
from live.pipecat_graph import GLOBAL_NODE_ID
from live.routes import register

ROOT = Path(__file__).resolve().parents[2]


def _grant_names(catalog) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for agent in catalog.agents.values():
        for grant in agent.grants:
            if grant.name in seen:
                continue
            seen.add(grant.name)
            names.append(grant.name)
    return names


class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.source = Feed()
        register(self.app, global_names=(END_CALL,), source=self.source, root=ROOT)
        self.client = TestClient(self.app)

    def test_live_page_and_static(self):
        page = self.client.get("/live")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers.get("content-type", ""))
        self.assertIn("/live/static/page.js", page.text)
        script = self.client.get("/live/static/page.js")
        self.assertEqual(script.status_code, 200)

    def test_graphs_json(self):
        catalog = load_catalog(ROOT)
        response = self.client.get("/live/graphs")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("steps", body)
        self.assertEqual(list(body["workflows"].keys()), _grant_names(catalog))
        confirm = next((step for step in body["workflows"]["refund"] if step.get("id") == "confirm"), None)
        self.assertIsNotNone(confirm)
        pipecat = body["pipecat"]
        ids = {node["id"] for node in pipecat["nodes"]}
        self.assertIn("verification", ids)
        self.assertIn("workflow", ids)
        self.assertIn("workflow-step", ids)
        self.assertIn("end", ids)
        self.assertIn(GLOBAL_NODE_ID, ids)
        self.assertEqual(Call(catalog, lambda: object(), "graph").end_call.name, END_CALL)
        global_node = next(node for node in pipecat["nodes"] if node["id"] == GLOBAL_NODE_ID)
        self.assertEqual(global_node["caption"], END_CALL)
        self.assertTrue(any("from" in edge for edge in pipecat["edges"]))
        self.assertFalse(any(edge.get("label") == END_CALL for edge in pipecat["edges"]))
        for edge in pipecat["edges"]:
            self.assertNotIn(GLOBAL_NODE_ID, (edge.get("from"), edge.get("to")))


if __name__ == "__main__":
    unittest.main()