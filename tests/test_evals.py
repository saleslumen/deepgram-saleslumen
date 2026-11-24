"""Eval scenario files load and match the suite manifest."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipecat.evals.scenario import EvalScenarioFile
from pipecat.evals.suite import EvalManifest
from pipecat.services.openrouter.llm import OpenRouterLLMService

from evals import llm as llm_module
from evals.llm import openrouter

REPO = Path(__file__).resolve().parent.parent
PROJECT_DOTENV = Path(llm_module.__file__).resolve().parent.parent / ".env"
EVALS = REPO / "evals"
OPENROUTER_FACTORY = "evals.llm.openrouter"


class EvalsLoadTest(unittest.TestCase):
    def test_scenarios_load(self):
        loaded_names: set[str] = set()
        scenario_files = 0
        for path in sorted(EVALS.rglob("*.yaml")):
            if path.name == "suite.yaml":
                continue
            scenario_files += 1
            file = EvalScenarioFile.load(path)
            for scenario in file.scenarios:
                loaded_names.add(scenario.name)
                if path.parent.name == "scripted":
                    self.assertEqual(scenario.judge.get("factory"), OPENROUTER_FACTORY, scenario.name)
                if path.parent.name == "simulations":
                    self.assertEqual(scenario.judge.get("factory"), OPENROUTER_FACTORY, scenario.name)
                    self.assertEqual(scenario.simulator.get("factory"), OPENROUTER_FACTORY, scenario.name)
        self.assertGreater(scenario_files, 0)
        manifest_path = EVALS / "suite.yaml"
        manifest = EvalManifest.load(manifest_path)
        run_names = {run.scenario for run in manifest.runs}
        self.assertGreater(len(manifest.runs), 0)
        self.assertEqual(loaded_names, run_names)
        for run in manifest.runs:
            self.assertTrue(run.bot.endswith("bot.py"), run.bot)
            self.assertIsNotNone(run.bot_path)
            self.assertTrue(run.bot_path.is_file(), run.bot_path)


class OpenRouterFactoryTest(unittest.TestCase):
    @patch("evals.llm.load")
    @patch("evals.llm.load_dotenv")
    def test_openrouter_uses_config_settings(self, mock_load_dotenv, mock_load):
        settings = MagicMock()
        settings.openrouter_api_key = "test-key"
        settings.openrouter_model = "openai/gpt-4.1-mini"
        call_order: list[str] = []
        def record_dotenv(path):
            call_order.append("load_dotenv")
        def record_load():
            call_order.append("load")
            return settings
        mock_load_dotenv.side_effect = record_dotenv
        mock_load.side_effect = record_load
        service = openrouter({})
        mock_load_dotenv.assert_called_once_with(PROJECT_DOTENV)
        mock_load.assert_called_once_with()
        self.assertEqual(call_order, ["load_dotenv", "load"])
        self.assertIsInstance(service, OpenRouterLLMService)
        self.assertEqual(service._client.api_key, "test-key")
        self.assertEqual(service._settings.model, "openai/gpt-4.1-mini")

    @patch("evals.llm.load")
    @patch("evals.llm.load_dotenv")
    def test_openrouter_requires_api_key(self, mock_load_dotenv, mock_load):
        settings = MagicMock()
        settings.openrouter_api_key = ""
        mock_load.return_value = settings
        with self.assertRaises(RuntimeError) as ctx:
            openrouter({})
        self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))

    def test_openrouter_source_has_no_os_environ(self):
        source = (REPO / "evals" / "llm.py").read_text()
        self.assertNotIn("os.environ", source)
        self.assertNotIn("os.getenv", source)


if __name__ == "__main__":
    unittest.main()