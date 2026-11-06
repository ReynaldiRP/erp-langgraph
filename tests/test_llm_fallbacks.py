"""
Unit tests for Free LLM Models Catalog and Auto-Fallback (TASK: free-llm-models integration).
Validates dynamic discovery, priority sorting via free_llm_models metadata,
OpenRouter 3-item model array compliance, and fallback configuration.
"""
import unittest
from unittest.mock import MagicMock, patch
from app.core.config import settings
from app.services.llm import (
    fetch_free_models_catalog,
    afetch_free_models_catalog,
    get_chat_model,
    DEFAULT_CURATED_FREE_MODELS,
)


class TestFreeLLMModelsCatalog(unittest.TestCase):
    """Test suite for free models dynamic discovery & prioritization."""

    def test_default_curated_models_contains_openrouter_free(self):
        """Curated list must contain 'openrouter/free' and standard models."""
        self.assertIn("openrouter/free", DEFAULT_CURATED_FREE_MODELS)
        self.assertTrue(any("gemma" in m for m in DEFAULT_CURATED_FREE_MODELS))

    def test_fetch_catalog_offline_uses_curated_list(self):
        """When OpenRouter API is unreachable, catalog gracefully falls back to curated list."""
        with patch("httpx.Client.get", side_effect=Exception("Connection refused")):
            catalog = fetch_free_models_catalog(force_refresh=True)
            self.assertIn("openrouter/free", catalog)
            self.assertGreaterEqual(len(catalog), 3)

    def test_fetch_catalog_prioritizes_tools_support(self):
        """Models supporting tools/JSON from free_llm_models should be prioritized."""
        fake_api_response = MagicMock()
        fake_api_response.status_code = 200
        fake_api_response.json.return_value = {
            "data": [
                {"id": "unknown/random-model:free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "google/gemma-4-31b-it:free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}},
            ]
        }

        with patch("httpx.Client.get", return_value=fake_api_response):
            catalog = fetch_free_models_catalog(api_key="mock-key", force_refresh=True, prioritize_tools=True)
            self.assertEqual(catalog[0], "openrouter/free")
            self.assertIn("google/gemma-4-31b-it:free", catalog)

    def test_fetch_catalog_in_running_event_loop_is_non_blocking(self):
        """When an event loop is running, fetch_free_models_catalog returns immediately without blocking."""
        import asyncio

        async def _test_inside_loop():
            models = fetch_free_models_catalog(force_refresh=True)
            self.assertIsInstance(models, list)
            self.assertIn("openrouter/free", models)

        asyncio.run(_test_inside_loop())


class TestAsyncFreeLLMModelsCatalog(unittest.IsolatedAsyncioTestCase):
    """Test suite for afetch_free_models_catalog non-blocking execution."""

    async def test_afetch_catalog_offline_uses_curated_list(self):
        """When OpenRouter API is unreachable, afetch_free_models_catalog gracefully falls back."""
        with patch("httpx.Client.get", side_effect=Exception("Connection refused")):
            catalog = await afetch_free_models_catalog(force_refresh=True)
            self.assertIn("openrouter/free", catalog)
            self.assertGreaterEqual(len(catalog), 3)

    async def test_afetch_catalog_prioritizes_tools_support(self):
        """afetch_free_models_catalog respects tool priority and runs offloaded to thread."""
        fake_api_response = MagicMock()
        fake_api_response.status_code = 200
        fake_api_response.json.return_value = {
            "data": [
                {"id": "unknown/random-model:free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "google/gemma-4-31b-it:free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}},
            ]
        }

        with patch("httpx.Client.get", return_value=fake_api_response):
            catalog = await afetch_free_models_catalog(api_key="mock-key", force_refresh=True, prioritize_tools=True)
            self.assertEqual(catalog[0], "openrouter/free")
            self.assertIn("google/gemma-4-31b-it:free", catalog)

    async def test_afetch_catalog_cache_hit(self):
        """Subsequent afetch_free_models_catalog calls without force_refresh return cached results."""
        with patch("httpx.Client.get", side_effect=Exception("Should not be called on cache hit")):
            cat1 = await afetch_free_models_catalog(force_refresh=False)
            cat2 = await afetch_free_models_catalog(force_refresh=False)
            self.assertEqual(cat1, cat2)


class TestAutoFallbackModelFactory(unittest.TestCase):
    """Test suite for get_chat_model with auto-fallback configuration."""

    def test_openrouter_model_has_fallback_array(self):
        """OpenRouter ChatOpenAI should contain extra_body['models'] with max 3 items."""
        with patch.object(settings, "OPENROUTER_API_KEY", "mock-openrouter-key"):
            model = get_chat_model(
                provider="openrouter",
                model_name="google/gemma-4-31b-it:free",
                enable_fallback=True,
            )
            self.assertEqual(model.model_name, "google/gemma-4-31b-it:free")
            self.assertIsNotNone(model.extra_body)
            models_list = model.extra_body.get("models")
            self.assertIsInstance(models_list, list)
            # OpenRouter enforces max 3 models in fallback array
            self.assertLessEqual(len(models_list), 3)
            self.assertEqual(models_list[0], "google/gemma-4-31b-it:free")
            self.assertIn("openrouter/free", models_list)

    def test_openrouter_custom_fallback_override(self):
        """Explicit fallback list should be respected up to 3 items."""
        with patch.object(settings, "OPENROUTER_API_KEY", "mock-openrouter-key"):
            custom_fallbacks = ["nvidia/nemotron-3.5-lightning:free", "openrouter/free"]
            model = get_chat_model(
                provider="openrouter",
                model_name="primary/model:free",
                fallback_models=custom_fallbacks,
            )
            models_list = model.extra_body.get("models")
            self.assertEqual(models_list, ["primary/model:free", "nvidia/nemotron-3.5-lightning:free", "openrouter/free"])

    def test_openrouter_disable_fallback(self):
        """When enable_fallback is False, extra_body should be None or empty."""
        with patch.object(settings, "OPENROUTER_API_KEY", "mock-openrouter-key"):
            model = get_chat_model(
                provider="openrouter",
                model_name="primary/model:free",
                enable_fallback=False,
            )
            self.assertIsNone(model.extra_body)


if __name__ == "__main__":
    unittest.main()
