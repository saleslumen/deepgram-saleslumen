"""Workflow grants load from workflows/<name>/WORKFLOW.md and do not replace the client."""

import os
import tempfile
import unittest
from pathlib import Path

from execution import client as workflows
from catalog.workflows import ManifestError, granted, load_manifests, revision_steps

ROOT = Path(__file__).resolve().parents[2]


class ManifestTest(unittest.TestCase):
    def test_import_workflows_is_the_client_module(self):
        self.assertTrue(workflows.__file__.endswith("client.py"))
        self.assertTrue(hasattr(workflows, "WorkflowClient"))

    def test_the_refund_grant_resolves_through_its_environment_key(self):
        previous = os.environ.get("SALESLUMEN_WORKFLOW_ID")
        os.environ["SALESLUMEN_WORKFLOW_ID"] = "other-flow"
        try:
            loaded = load_manifests(ROOT / "workflows")
            found = granted(loaded, ("refund",))
        finally:
            if previous is None:
                os.environ.pop("SALESLUMEN_WORKFLOW_ID", None)
            else:
                os.environ["SALESLUMEN_WORKFLOW_ID"] = previous
        self.assertEqual(loaded[0].workflow_id, "")
        self.assertEqual(loaded[0].id_env, "SALESLUMEN_WORKFLOW_ID")
        self.assertEqual([item.name for item in found], ["refund"])
        self.assertEqual(found[0].workflow_id, "other-flow")
        self.assertIn("charged twice", found[0].description)
        self.assertIn("same payment intent", found[0].description)
        self.assertIn("already refunded", found[0].description)
        self.assertIn("reviewed", found[0].description)

    def test_an_invalid_workflow_id_is_rejected_without_echoing_it(self):
        previous = os.environ.get("SALESLUMEN_WORKFLOW_ID")
        os.environ["SALESLUMEN_WORKFLOW_ID"] = "Not Valid"
        try:
            with self.assertRaises(ManifestError) as caught:
                granted(load_manifests(ROOT / "workflows"), ("refund",))
        finally:
            if previous is None:
                os.environ.pop("SALESLUMEN_WORKFLOW_ID", None)
            else:
                os.environ["SALESLUMEN_WORKFLOW_ID"] = previous
        self.assertNotIn("Not Valid", str(caught.exception))
        self.assertIn("SALESLUMEN_WORKFLOW_ID: workflow_id:", str(caught.exception))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "refund"
            folder.mkdir()
            (folder / "WORKFLOW.md").write_text(
                "---\nname: refund\ndescription: A charge.\nworkflow-id-env: OTHER_WORKFLOW_ID\n---\n",
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError) as blank:
                granted(load_manifests(root), ("refund",))
        self.assertIn("OTHER_WORKFLOW_ID: workflow_id:", str(blank.exception))
        self.assertNotIn("hello-voice", str(blank.exception))

    def test_revision_and_script_subdirectories_do_not_break_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "refund"
            folder.mkdir()
            (folder / "WORKFLOW.md").write_text("---\nname: refund\ndescription: A charge.\nworkflow-id-env: SALESLUMEN_WORKFLOW_ID\n---\n", encoding="utf-8")
            (folder / "revision.json").write_text("{}", encoding="utf-8")
            script = folder / "script"
            script.mkdir()
            (script / "Code.js").write_text("// stub", encoding="utf-8")
            found = load_manifests(root)
            self.assertEqual([item.name for item in found], ["refund"])
            self.assertEqual(found[0].id_env, "SALESLUMEN_WORKFLOW_ID")

    def test_workflow_name_pattern_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "Bad_Name"
            folder.mkdir()
            (folder / "WORKFLOW.md").write_text("---\nname: Bad_Name\ndescription: A charge.\nworkflow-id-env: SALESLUMEN_WORKFLOW_ID\n---\n", encoding="utf-8")
            with self.assertRaises(ManifestError) as caught:
                load_manifests(root)
        self.assertIn("Bad_Name: name:", str(caught.exception))

    def test_workflow_directory_name_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "refund"
            folder.mkdir()
            (folder / "WORKFLOW.md").write_text("---\nname: other\ndescription: A charge.\nworkflow-id-env: SALESLUMEN_WORKFLOW_ID\n---\n", encoding="utf-8")
            with self.assertRaises(ManifestError) as caught:
                load_manifests(root)
        self.assertIn("refund: : name does not match the directory", str(caught.exception))

    def test_workflow_description_length_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "refund"
            folder.mkdir()
            (folder / "WORKFLOW.md").write_text("---\nname: refund\ndescription: '   '\nworkflow-id-env: SALESLUMEN_WORKFLOW_ID\n---\n", encoding="utf-8")
            with self.assertRaises(ManifestError) as caught:
                load_manifests(root)
        self.assertIn("refund: description:", str(caught.exception))

    def test_workflow_id_env_must_be_an_environment_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "refund"
            folder.mkdir()
            (folder / "WORKFLOW.md").write_text("---\nname: refund\ndescription: A charge.\nworkflow-id-env: bad_key\n---\n", encoding="utf-8")
            with self.assertRaises(ManifestError) as caught:
                load_manifests(root)
        self.assertIn("refund: workflow-id-env:", str(caught.exception))

    def test_unknown_frontmatter_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "refund"
            folder.mkdir()
            (folder / "WORKFLOW.md").write_text("---\nname: refund\ndescription: A charge.\nworkflow-id-env: SALESLUMEN_WORKFLOW_ID\nextra: nope\n---\n", encoding="utf-8")
            with self.assertRaises(ManifestError) as caught:
                load_manifests(root)
            self.assertIn("refund: extra:", str(caught.exception))

    def test_match_words_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "refund"
            folder.mkdir()
            (folder / "WORKFLOW.md").write_text("---\nname: refund\ndescription: A charge.\nworkflow-id-env: SALESLUMEN_WORKFLOW_ID\nmatch-words:\n  - charge\n---\n", encoding="utf-8")
            with self.assertRaises(ManifestError) as caught:
                load_manifests(root)
            self.assertIn("refund: match-words:", str(caught.exception))


class RevisionStepsTest(unittest.TestCase):
    def test_revision_steps_from_workflow_directory(self):
        steps = revision_steps(ROOT, "refund")
        self.assertIsInstance(steps, list)
        confirm = next((item for item in steps if item.get("id") == "confirm"), None)
        self.assertIsNotNone(confirm)

    def test_revision_steps_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            with self.assertRaises(ManifestError) as caught:
                revision_steps(temp_root, "refund")
            self.assertEqual(str(caught.exception), "workflows/refund/revision.json: file is missing")

    def test_revision_steps_non_list_steps_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            folder = temp_root / "workflows" / "refund"
            folder.mkdir(parents=True)
            (folder / "revision.json").write_text(
                '{"definition": {"steps": {}}}',
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError) as caught:
                revision_steps(temp_root, "refund")
            self.assertEqual(
                str(caught.exception),
                "workflows/refund/revision.json: definition.steps must be a list",
            )


if __name__ == "__main__":
    unittest.main()
