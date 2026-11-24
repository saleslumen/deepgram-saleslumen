"""The placeholder script classifies the caller's reason into one closed outcome."""

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(expression: str, request: str = "") -> dict:
    script = (ROOT / "workflows" / "refund" / "script" / "Code.js").read_text()
    completed = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script + f"\nprocess.stdout.write(JSON.stringify({expression}))"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "REQUEST": request},
        cwd=ROOT,
    )
    return json.loads(completed.stdout)


def assess(request: str) -> dict:
    return _run("assess({request: process.env.REQUEST})", request)


class PaymentTest(unittest.TestCase):
    def test_same_payment_intent_is_one_charge(self):
        found = assess("I was charged on the same payment intent")
        self.assertEqual(found, {"outcome": "single_charge"})

    def test_already_refunded_does_not_refund_again(self):
        found = assess("that charge was already refunded")
        self.assertEqual(found, {"outcome": "already_refunded"})
        self.assertNotIn("refund(", (ROOT / "workflows" / "refund" / "script" / "Code.js").read_text().split("function refund")[0])

    def test_twice_is_one_purchase_and_two_charges(self):
        self.assertEqual(assess("I was charged twice"), {"outcome": "duplicate"})

    def test_anything_else_is_a_review(self):
        found = assess("can you look at my bill")
        self.assertEqual(found, {"outcome": "manual_review"})
        self.assertNotIn("follow up by email", (ROOT / "workflows" / "refund" / "script" / "Code.js").read_text())

    def test_the_follow_up_sends_no_address(self):
        sent = _run("send_follow_up()")
        self.assertEqual(sent, {"sent": True})

    def test_the_transfer_placeholder_does_not_start(self):
        source = (ROOT / "workflows" / "refund" / "script" / "Code.js").read_text().split("function transfer_to_teammate")[1].split("function ")[0]
        self.assertNotIn("fetch", source)
        self.assertNotIn("http", source.lower())
        self.assertNotIn("twilio", source.lower())
        started = _run("transfer_to_teammate()")
        self.assertEqual(started, {"started": False})


if __name__ == "__main__":
    unittest.main()
