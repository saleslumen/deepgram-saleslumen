"""The voice record joined from a pinned revision and a snapshot."""

import json
import unittest

from execution.models import Observation, RevisionDefinition
from execution.steps import UNSUPPORTED, Argument, Succeeded, answer_kind, AnswerKind, compile_index, parse_fields, present, project, spoken_line

REVISION = "workflows/hello-voice/revisions/b"


def snapshot(state, step_path, signal, sequence=4, revision=REVISION):
    return Observation.model_validate({
        "name": "workflows/hello-voice/executions/run-1",
        "uid": "11111111-1111-4111-8111-111111111111",
        "revision": revision,
        "state": state,
        "observationVersion": "2",
        "vars": {"greeting": {"message": "Hello, world."}},
        "waits": [{"kind": "KIND_SIGNAL", "stepPath": step_path, "sequence": str(sequence), "signal": signal}],
    })


def definition():
    return {
        "signals": {"approve_order": "object{decision: string, quantity: integer}"},
        "steps": [{
            "id": "fan",
            "parallel": {"branches": [{
                "id": "right",
                "steps": [{
                    "id": "each",
                    "for_each": {"steps": [{
                        "id": "w",
                        "label": "Approve the order",
                        "awaitSignal": {"signal": "approve_order"},
                    }]},
                }],
            }]},
        }],
    }


def revision(payload):
    return RevisionDefinition.model_validate(payload)


class StepsTest(unittest.TestCase):
    def test_refund_revision_ignores_unsupported_step_keys(self):
        index = compile_index(revision({
            "signals": {"confirm": "object{accepted: bool}"},
            "steps": [{
                "id": "confirm",
                "label": "Confirm the refund",
                "input": {"request": "vars.request"},
                "vars": {"note": "internal"},
                "switch": {"on": "vars.kind", "cases": []},
                "awaitSignal": {"signal": "confirm"},
            }],
        }))
        item = project(index, snapshot("STATE_WAITING", "confirm", "confirm"))
        self.assertEqual(item.label, "Confirm the refund")

    def test_nested_loop_path_resolves_without_leaking_the_definition(self):
        index = compile_index(revision(definition()))
        item = project(index, snapshot("STATE_WAITING", "fan.right/each[17]/w", "approve_order"))
        text = present(item, "Hello voice")
        self.assertEqual(json.loads(text), {"kind": "awaiting_input", "label": "Approve the order", "workflow": "Hello voice"})
        self.assertNotIn("approve_order", text)
        self.assertEqual([field.name for field in item.arguments], ["decision", "quantity"])
        self.assertEqual(answer_kind(item.arguments), AnswerKind.QUESTION)

    def test_notice_and_question_kinds(self):
        self.assertEqual(answer_kind(()), AnswerKind.NOTICE)
        self.assertEqual(answer_kind((Argument("transfer", "boolean"),)), AnswerKind.QUESTION)
        self.assertEqual(answer_kind((Argument("name", "string"),)), AnswerKind.QUESTION)

    def test_terminal_records_hide_the_api_error(self):
        index = compile_index(revision(definition()))
        failed = Observation.model_validate({
            "name": "workflows/hello-voice/executions/run-1",
            "uid": "11111111-1111-4111-8111-111111111111",
            "revision": REVISION,
            "state": "STATE_FAILED",
            "observationVersion": "4",
            "error": {"code": "WORKFLOW_FAILED", "reason": "SECRET", "message": "internal"},
        })
        text = present(project(index, failed), "Hello voice")
        self.assertEqual(json.loads(text)["message"], "The workflow failed.")
        self.assertNotIn("SECRET", text)

    def test_unsupported_schema_and_mismatches_fail_closed(self):
        for schema in ("object{item: list<string>}", "object{name: string?}"):
            with self.assertRaises(Exception):
                parse_fields(schema)
        index = compile_index(revision({"signals": {"continue": "object{accepted: bool}"}, "steps": [{"id": "confirm", "label": "Wait", "awaitSignal": {"signal": "continue"}}]}))
        self.assertEqual(project(index, snapshot("STATE_WAITING", "missing", "continue")).message, UNSUPPORTED)

    def test_the_session_says_the_step_and_not_the_workflow_name(self):
        waiting = project(compile_index(revision({"signals": {"confirm": "object{accepted: bool}"}, "steps": [{"id": "confirm", "label": "I found a duplicate charge of twenty dollars. Please confirm the refund", "awaitSignal": {"signal": "confirm"}}]})), snapshot("STATE_WAITING", "confirm", "confirm"))
        line = spoken_line(waiting)
        self.assertEqual(line, "I found a duplicate charge of twenty dollars. Please confirm the refund.")
        self.assertIsNone(spoken_line(Succeeded({"sent": True})))


if __name__ == "__main__":
    unittest.main()
