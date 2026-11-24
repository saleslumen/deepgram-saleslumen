"""Agent Skills discovery."""

import tempfile
import unittest
from pathlib import Path

from catalog.skills import SkillError, load_skills, skill_prompt

ROOT = Path(__file__).resolve().parents[2]


class SkillTest(unittest.TestCase):
    def test_installed_skill_explains_saleslumen(self):
        skills = load_skills(ROOT / "skills")
        self.assertEqual([skill.name for skill in skills], ["saleslumen"])
        self.assertIn("what Saleslumen is", skills[0].description)
        self.assertNotIn("email outreach", skills[0].description)
        body = skills[0].body
        self.assertIn("helps teams run sales outreach and automate the work around it", body)
        self.assertIn("one short sentence", body)
        self.assertIn("then stop", body)
        self.assertNotIn("email outreach", skill_prompt(skills))
        self.assertIn("Do not speak the description.", skill_prompt(skills))

    def test_name_must_match_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "other"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: saleslumen\ndescription: Use when asked.\n---\nBody\n")
            with self.assertRaises(SkillError):
                load_skills(Path(tmp))

    def test_metadata_is_accepted_and_unknown_names_are_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "notes"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: notes\ndescription: Use when the caller asks for the demo note.\nmetadata:\n  author: saleslumen\n---\nSay the note.\n"
            )
            skills = load_skills(Path(tmp))
            self.assertEqual(skills[0].body, "Say the note.")

    def test_body_may_contain_a_frontmatter_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "notes"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: notes\ndescription: Use when the caller asks for the demo note.\n---\nKeep the --- marker.\n"
            )
            skills = load_skills(Path(tmp))
            self.assertEqual(skills[0].body, "Keep the --- marker.")

    def test_name_pattern_rejects_uppercase(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "Notes"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: Notes\ndescription: Use when asked.\n---\nBody\n")
            with self.assertRaises(SkillError) as caught:
                load_skills(Path(tmp))
        self.assertIn("SKILL.md: name:", str(caught.exception))

    def test_description_length_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "notes"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: notes\ndescription: '   '\n---\nBody\n")
            with self.assertRaises(SkillError) as caught:
                load_skills(Path(tmp))
        self.assertIn("SKILL.md: description:", str(caught.exception))

    def test_compatibility_length_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "notes"
            skill.mkdir()
            long = "x" * 501
            (skill / "SKILL.md").write_text(f"---\nname: notes\ndescription: Use when asked.\ncompatibility: {long}\n---\nBody\n")
            with self.assertRaises(SkillError) as caught:
                load_skills(Path(tmp))
        self.assertIn("SKILL.md: compatibility:", str(caught.exception))

    def test_metadata_values_must_be_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "notes"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: notes\ndescription: Use when asked.\nmetadata:\n  count: 1\n---\nBody\n")
            with self.assertRaises(SkillError) as caught:
                load_skills(Path(tmp))
        self.assertIn("SKILL.md: metadata.count:", str(caught.exception))

    def test_unknown_skill_field_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "notes"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: notes\ndescription: Use when asked.\nextra: nope\n---\nBody\n")
            with self.assertRaises(SkillError) as caught:
                load_skills(Path(tmp))
        self.assertIn("SKILL.md: extra:", str(caught.exception))

    def test_invalid_skill_stops_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "empty").mkdir()
            with self.assertRaises(SkillError):
                load_skills(root)
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "notes"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: [\n---\nBody\n")
            with self.assertRaises(SkillError):
                load_skills(Path(tmp))


if __name__ == "__main__":
    unittest.main()
