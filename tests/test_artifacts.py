"""Local script and workflow files stay aligned with the sl request bodies."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ArtifactTest(unittest.TestCase):
    def test_content_body_matches_the_source_files(self):
        body = json.loads((ROOT / "workflows" / "refund" / "script" / "content.json").read_text())
        files = {item["name"]: item for item in body["content"]["files"]}
        self.assertEqual(set(files), {"appsscript.json", "Code.js"})
        self.assertEqual(files["appsscript.json"]["type"], "JSON")
        self.assertEqual(files["Code.js"]["type"], "SERVER_JS")
        self.assertEqual(files["appsscript.json"]["source"], (ROOT / "workflows" / "refund" / "script" / "appsscript.json").read_text())
        self.assertEqual(files["Code.js"]["source"], (ROOT / "workflows" / "refund" / "script" / "Code.js").read_text())
        self.assertNotIn("etag", body["content"])

    def test_revision_classifies_the_charge_before_it_refunds(self):
        body = json.loads((ROOT / "workflows" / "refund" / "revision.json").read_text())
        definition = body["definition"]
        self.assertEqual(definition["input"], "object{request: string}")
        self.assertEqual(definition["vars"]["assessment"], "object{outcome: string}")
        self.assertEqual(definition["signals"]["confirm"], "object{accepted: bool}")
        steps = {step["id"]: step for step in definition["steps"]}
        self.assertEqual([step["id"] for step in definition["steps"]], ["assess", "outcome", "confirm", "authorized", "declined", "refund", "done", "single", "refunded", "review_notice", "review_choice", "review_decision", "email_notice", "send_follow_up", "email_done", "transfer_notice", "transfer_to_teammate", "transfer_decision", "transfer_started", "transfer_failed_notice", "send_follow_up_after", "transfer_email_done"])
        for name in ("assess", "refund"):
            self.assertEqual(steps[name]["call"]["customScript"]["script"], "scripts/REPLACE_WITH_DEPLOYMENT_ID")
            self.assertEqual(steps[name]["call"]["customScript"]["function"], name)
        self.assertEqual(steps["send_follow_up"]["call"]["customScript"]["script"], "scripts/REPLACE_WITH_DEPLOYMENT_ID")
        self.assertEqual(steps["transfer_to_teammate"]["call"]["customScript"]["script"], "scripts/REPLACE_WITH_DEPLOYMENT_ID")
        self.assertEqual(steps["send_follow_up_after"]["call"]["customScript"]["script"], "scripts/REPLACE_WITH_DEPLOYMENT_ID")
        self.assertNotIn("REPLACE_WITH_SCRIPT_ID", json.dumps(definition))
        self.assertEqual(steps["transfer_to_teammate"]["call"]["customScript"]["function"], "transfer_to_teammate")
        self.assertEqual(steps["assess"]["call"]["args"]["request"]["expr"], "input.request")
        cases = {item["next"]: item["when"] for item in steps["outcome"]["switch"]["cases"]}
        self.assertEqual(cases["confirm"], 'vars.assessment.outcome == "duplicate"')
        self.assertEqual(cases["single"], 'vars.assessment.outcome == "single_charge"')
        self.assertEqual(cases["refunded"], 'vars.assessment.outcome == "already_refunded"')
        self.assertEqual(steps["outcome"]["switch"]["default"], "review_notice")
        self.assertNotIn("eligibility", definition["vars"])
        self.assertEqual(steps["confirm"]["awaitSignal"]["signal"], "confirm")
        self.assertIn("twenty dollars", steps["confirm"]["label"])
        self.assertEqual(steps["authorized"]["switch"]["cases"][0]["next"], "refund")
        self.assertEqual(steps["authorized"]["switch"]["default"], "declined")
        refund_callers = [step["id"] for step in definition["steps"] if step.get("call", {}).get("customScript", {}).get("function") == "refund"]
        self.assertEqual(refund_callers, ["refund"])
        refund_next = [step["id"] for step in definition["steps"] for case in step.get("switch", {}).get("cases", []) if case.get("next") == "refund"]
        self.assertEqual(refund_next, ["authorized"])
        self.assertEqual(steps["single"]["return"]["value"]["literal"]["message"], "There is only one charge of twenty dollars on that order, so nothing was refunded.")
        self.assertEqual(steps["refunded"]["return"]["value"]["literal"]["message"], "That charge has already been refunded, and there is nothing more to do.")
        self.assertEqual(steps["review_notice"]["label"], "Nothing was refunded, and a person needs to review this.")
        self.assertEqual(steps["review_choice"]["label"], "Would you like me to transfer you to a teammate now? If not, we will follow up by email.")
        self.assertEqual(steps["email_notice"]["label"], "A person will review it and we will follow up by email.")
        self.assertEqual(steps["transfer_notice"]["label"], "Transferring you to a teammate now.")
        self.assertEqual(steps["transfer_failed_notice"]["label"], "I couldn't transfer you just now. A person will review it and we will follow up by email.")
        self.assertEqual(steps["review_notice"]["awaitSignal"]["signal"], "speech_complete")
        self.assertEqual(steps["email_notice"]["awaitSignal"]["signal"], "speech_complete")
        self.assertEqual(steps["transfer_notice"]["awaitSignal"]["signal"], "speech_complete")
        self.assertEqual(steps["transfer_failed_notice"]["awaitSignal"]["signal"], "speech_complete")
        self.assertEqual(steps["review_choice"]["awaitSignal"]["signal"], "review_choice")
        self.assertEqual(steps["review_decision"]["switch"]["cases"][0]["next"], "transfer_notice")
        self.assertEqual(steps["review_decision"]["switch"]["default"], "email_notice")
        self.assertEqual(steps["transfer_decision"]["switch"]["default"], "transfer_failed_notice")
        self.assertEqual(steps["email_done"]["return"]["value"]["expr"], "vars.follow_up_result")
        self.assertEqual(steps["transfer_started"]["return"]["value"]["literal"], {"transfer_started": True})
        self.assertEqual(steps["transfer_email_done"]["return"]["value"]["expr"], "vars.follow_up_result")
        self.assertNotIn("collectEmail", json.dumps(definition))
        follow_ups = [step["id"] for step in definition["steps"] if step.get("call", {}).get("customScript", {}).get("function") == "send_follow_up"]
        self.assertEqual(follow_ups, ["send_follow_up", "send_follow_up_after"])
        self.assertLess(definition["steps"].index(steps["email_notice"]), definition["steps"].index(steps["send_follow_up"]))
        self.assertLess(definition["steps"].index(steps["transfer_notice"]), definition["steps"].index(steps["transfer_to_teammate"]))
        self.assertLess(definition["steps"].index(steps["transfer_failed_notice"]), definition["steps"].index(steps["send_follow_up_after"]))
        field = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
        for schema in list(definition["vars"].values()) + list(definition["signals"].values()):
            body = schema[len("object{"):-1]
            for part in body.split(","):
                name = part.split(":")[0].strip()
                if name == "":
                    continue
                self.assertRegex(name, field)
        self.assertEqual(steps["done"]["return"]["value"]["expr"], "vars.refund")


if __name__ == "__main__":
    unittest.main()
