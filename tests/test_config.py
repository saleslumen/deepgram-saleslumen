"""Environment configuration."""

import os
import unittest

from config import ConfigError, load


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._previous = os.environ.copy()
        os.environ.clear()
        os.environ["DEEPGRAM_API_KEY"] = "dg"
        os.environ["SALESLUMEN_API_KEY"] = "token"
        os.environ["SALESLUMEN_ORGANIZATION_ID"] = "org"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._previous)

    def test_defaults_select_deepgram(self):
        settings = load()
        self.assertEqual(settings.stt_provider, "deepgram-flux")
        self.assertEqual(settings.tts_provider, "deepgram-flux")
        self.assertEqual(settings.llm_provider, "deepgram")
        self.assertEqual(settings.deepgram_voice, "flux-alexis-en")
        self.assertEqual(settings.deepgram_llm_model, "think-fast")
        self.assertEqual(settings.workflows_origin, "https://workflows.saleslumenapis.com")
        self.assertFalse(hasattr(settings, "workflow_id"))
        self.assertEqual(settings.saleslumen_api_key, "token")
        self.assertFalse(hasattr(settings, "access_token"))

    def test_openrouter_requires_its_key(self):
        os.environ["LLM_PROVIDER"] = "openrouter"
        with self.assertRaises(ConfigError):
            load()
        os.environ["OPENROUTER_API_KEY"] = "or"
        os.environ["OPENROUTER_MODEL"] = "openai/gpt-4.1-mini"
        settings = load()
        self.assertEqual(settings.llm_provider, "openrouter")
        self.assertEqual(settings.openrouter_model, "openai/gpt-4.1-mini")

    def test_blank_optional_values_use_defaults(self):
        os.environ["DEEPGRAM_VOICE"] = ""
        os.environ["SALESLUMEN_API_DOMAIN"] = ""
        settings = load()
        self.assertEqual(settings.deepgram_voice, "flux-alexis-en")
        self.assertEqual(settings.workflows_origin, "https://workflows.saleslumenapis.com")

    def test_unknown_provider_and_voice_fail(self):
        os.environ["STT_PROVIDER"] = "soniox"
        with self.assertRaises(ConfigError):
            load()
        os.environ["STT_PROVIDER"] = "deepgram-flux"
        os.environ["DEEPGRAM_VOICE"] = "aura-2-thalia-en"
        with self.assertRaises(ConfigError):
            load()

    def test_required_deepgram_api_key_missing(self):
        os.environ.pop("DEEPGRAM_API_KEY", None)
        with self.assertRaises(ConfigError) as caught:
            load()
        self.assertIn("DEEPGRAM_API_KEY:", str(caught.exception))

    def test_required_saleslumen_api_key_missing(self):
        os.environ.pop("SALESLUMEN_API_KEY", None)
        with self.assertRaises(ConfigError) as caught:
            load()
        self.assertIn("SALESLUMEN_API_KEY", str(caught.exception))

    def test_required_saleslumen_organization_id_missing(self):
        os.environ.pop("SALESLUMEN_ORGANIZATION_ID", None)
        with self.assertRaises(ConfigError) as caught:
            load()
        self.assertIn("SALESLUMEN_ORGANIZATION_ID", str(caught.exception))

    def test_whitespace_required_key_is_rejected(self):
        os.environ["SALESLUMEN_API_KEY"] = "   "
        with self.assertRaises(ConfigError) as caught:
            load()
        self.assertIn("SALESLUMEN_API_KEY:", str(caught.exception))

    def test_every_missing_required_key_is_reported(self):
        os.environ.pop("DEEPGRAM_API_KEY", None)
        os.environ.pop("SALESLUMEN_ORGANIZATION_ID", None)
        with self.assertRaises(ConfigError) as caught:
            load()
        self.assertIn("DEEPGRAM_API_KEY:", str(caught.exception))
        self.assertIn("SALESLUMEN_ORGANIZATION_ID:", str(caught.exception))

    def test_optional_namespace_id_falls_back_to_default(self):
        os.environ["SALESLUMEN_NAMESPACE_ID"] = "   "
        settings = load()
        self.assertEqual(settings.saleslumen_namespace_id, "")

    def test_api_domain_rejects_url_and_path(self):
        os.environ["SALESLUMEN_API_DOMAIN"] = "https://saleslumenapis.com"
        with self.assertRaises(ConfigError):
            load()
        os.environ["SALESLUMEN_API_DOMAIN"] = "saleslumenapis.com/extra"
        with self.assertRaises(ConfigError):
            load()

    def test_openrouter_key_required_only_when_provider_openrouter(self):
        os.environ["LLM_PROVIDER"] = "deepgram"
        os.environ["OPENROUTER_API_KEY"] = ""
        settings = load()
        self.assertEqual(settings.openrouter_api_key, "")
        os.environ["LLM_PROVIDER"] = "openrouter"
        with self.assertRaises(ConfigError) as caught:
            load()
        self.assertIn("OPENROUTER_API_KEY", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
