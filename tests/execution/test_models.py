"""Observation records from a watch snapshot."""

import unittest

from pydantic import ValidationError

from execution.models import Observation


def snapshot(**overrides):
    payload = {
        "name": "workflows/hello-voice/executions/run-1",
        "uid": "11111111-1111-4111-8111-111111111111",
        "state": "STATE_WAITING",
        "observationVersion": "2",
        "vars": {"greeting": {"message": "Hello, world."}},
        "waits": [{"kind": "KIND_SIGNAL", "stepPath": "confirm", "sequence": "4", "signal": "continue"}],
    }
    payload.update(overrides)
    return payload


class ObservationTest(unittest.TestCase):
    def test_protojson_integers_and_signal_target(self):
        observation = Observation.model_validate(snapshot())
        self.assertEqual(observation.version, 2)
        self.assertEqual(observation.waits[0].sequence, 4)
        self.assertEqual(observation.waits[0].target_step_run, "workflows/hello-voice/executions/run-1/stepRuns/4")
        self.assertEqual(observation.waits[0].signal, "continue")

    def test_named_state_keeps_the_result(self):
        observation = Observation.model_validate(snapshot(state="STATE_SUCCEEDED", observationVersion="3", waits=[], result={"message": "Hello, world."}))
        self.assertEqual(observation.state, "STATE_SUCCEEDED")
        self.assertEqual(observation.result, {"message": "Hello, world."})
        self.assertFalse(hasattr(observation, "terminal"))
        self.assertFalse(hasattr(observation, "variables"))
        self.assertFalse(hasattr(observation, "error"))

    def test_unknown_state_and_kind_are_rejected(self):
        with self.assertRaises(ValidationError):
            Observation.model_validate(snapshot(state="STATE_EXPLODED"))
        with self.assertRaises(ValidationError):
            Observation.model_validate(snapshot(waits=[{"kind": "KIND_EXPLODED", "stepPath": "confirm", "sequence": "4", "signal": "continue"}]))
        with self.assertRaises(ValidationError):
            Observation.model_validate(snapshot(state=99))

    def test_waits_must_be_omitted_or_a_list(self):
        omitted = snapshot()
        del omitted["waits"]
        self.assertEqual(Observation.model_validate(omitted).waits, ())
        for value in (None, False, "", 0, {}):
            with self.assertRaises(ValidationError):
                Observation.model_validate(snapshot(waits=value))

    def test_rejects_incomplete_snapshot(self):
        with self.assertRaises(ValidationError):
            Observation.model_validate({"name": "workflows/hello-voice/executions/run-1"})


if __name__ == "__main__":
    unittest.main()
