import unittest

from core.model_config import (
    RuntimeModel,
    catalog_choices,
    ensure_providers,
    infer_supported_params,
    is_reasoning_model,
    lookup_catalog_entry,
    load_catalog,
    normalize_model,
    normalize_params,
    normalize_provider,
    resolve_runtime,
    validate_config,
)

CATALOG = {
    "version": 1,
    "models": {
        "openai/gpt-4o": {
            "context": 128000,
            "max_out": 16384,
            "caps": ["chat", "vision"],
            "params": ["temperature", "top_p", "max_tokens", "stop"],
        },
        "openai/o3": {
            "context": 200000,
            "max_out": 32768,
            "caps": ["chat", "reasoning"],
            "params": ["max_tokens", "stop"],
        },
        "google/gemini-2.5-pro": {
            "context": 1048576,
            "max_out": 32768,
            "caps": ["chat", "vision"],
            "params": ["temperature", "top_p", "top_k", "max_tokens", "stop"],
        },
        "deepseek/deepseek-chat": {
            "context": 163840,
            "max_out": 8192,
            "caps": ["chat"],
            "params": ["temperature", "top_p", "max_tokens"],
        },
    },
}


def _model(**overrides):
    data = {
        "profile_key": "normal",
        "provider_id": "pv_1",
        "provider_name": "P",
        "api_type": "openai",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-x",
        "timeout": 120,
        "model_id": "md_1",
        "model_key": "gpt-4o",
        "display_name": "GPT-4o",
        "context_size": 128000,
        "max_output_tokens": 16384,
        "capabilities": ["chat"],
        "supported_params": ["temperature", "top_p", "max_tokens"],
        "params": {"temperature": 0.7},
    }
    data.update(overrides)
    return RuntimeModel(**data)


class CatalogTests(unittest.TestCase):
    def test_lookup_exact(self):
        self.assertIsNotNone(lookup_catalog_entry(CATALOG, "openai/gpt-4o"))

    def test_lookup_case_insensitive(self):
        entry = lookup_catalog_entry(CATALOG, "OpenAI/GPT-4O")
        self.assertEqual(entry["context"], 128000)

    def test_lookup_bare_model_matches_prefixed_entry(self):
        entry = lookup_catalog_entry(CATALOG, "deepseek-chat")
        self.assertEqual(entry["context"], 163840)

    def test_lookup_unknown_returns_none(self):
        self.assertIsNone(lookup_catalog_entry(CATALOG, "vendor/unknown"))

    def test_catalog_choices_sorted_and_filtered(self):
        choices = catalog_choices(CATALOG)
        names = [item["model"] for item in choices]
        self.assertEqual(names, sorted(names, key=str.lower))
        filtered = catalog_choices(CATALOG, "gemini")
        self.assertEqual([item["model"] for item in filtered], ["google/gemini-2.5-pro"])

    def test_load_catalog_missing_file(self):
        self.assertEqual(load_catalog("does/not/exist.json")["models"], {})


class SupportedParamsTests(unittest.TestCase):
    def test_reasoning_models_exclude_temperature(self):
        params = infer_supported_params("o3-mini", "openai")
        self.assertNotIn("temperature", params)
        self.assertTrue(is_reasoning_model("o3-mini"))

    def test_gemini_supports_top_k(self):
        self.assertIn("top_k", infer_supported_params("gemini-2.5-pro", "gemini"))

    def test_claude_supports_top_k_via_openai_compatible(self):
        self.assertIn("top_k", infer_supported_params("anthropic/claude-sonnet-4", "openai"))

    def test_local_gateways_add_repetition_penalty(self):
        params = infer_supported_params("qwen2.5-14b-instruct-vllm", "openai")
        self.assertIn("repetition_penalty", params)

    def test_plain_openai_model_has_no_top_k(self):
        self.assertNotIn("top_k", infer_supported_params("gpt-4o", "openai"))


class ParamsNormalizationTests(unittest.TestCase):
    def test_none_values_are_dropped(self):
        params = normalize_params({"temperature": None, "top_p": 0.9}, ["temperature", "top_p"])
        self.assertEqual(params, {"top_p": 0.9})

    def test_values_are_coerced_and_clamped(self):
        params = normalize_params({"temperature": "3.5", "top_k": "12"}, ["temperature", "top_k"])
        self.assertEqual(params["temperature"], 2.0)
        self.assertEqual(params["top_k"], 12)

    def test_unknown_params_dropped(self):
        self.assertEqual(normalize_params({"foo": 1}, ["foo"]), {})

    def test_stop_string_is_split(self):
        params = normalize_params({"stop": "END\n###"}, ["stop"])
        self.assertEqual(params["stop"], ["END", "###"])

    def test_defaults_filled_for_supported(self):
        params = normalize_params({}, ["temperature", "top_p"])
        self.assertEqual(params["temperature"], 0.7)
        self.assertEqual(params["top_p"], 1.0)

    def test_empty_list_default_not_added(self):
        params = normalize_params({}, ["stop"])
        self.assertNotIn("stop", params)


class ModelNormalizationTests(unittest.TestCase):
    def test_known_model_uses_catalog_limits(self):
        model = normalize_model({"model": "openai/gpt-4o"}, CATALOG, api_type="openai")
        self.assertEqual(model["context_size"], 128000)
        self.assertEqual(model["max_output_tokens"], 16384)
        self.assertIn("vision", model["capabilities"])
        self.assertTrue(model["id"].startswith("md_"))

    def test_unknown_model_gets_defaults(self):
        model = normalize_model({"model": "vendor/unknown"}, CATALOG)
        self.assertEqual(model["context_size"], 8192)
        self.assertGreater(model["max_output_tokens"], 0)
        self.assertEqual(model["display_name"], "vendor/unknown")

    def test_user_override_wins(self):
        model = normalize_model(
            {"model": "openai/gpt-4o", "context_size": 32000, "max_output_tokens": 4096},
            CATALOG,
        )
        self.assertEqual(model["context_size"], 32000)
        self.assertEqual(model["max_output_tokens"], 4096)

    def test_params_are_normalized_and_limited_to_supported(self):
        model = normalize_model(
            {
                "model": "openai/o3",
                "params": {"temperature": 0.5, "top_p": 0.9, "max_tokens": 500},
            },
            CATALOG,
        )
        self.assertNotIn("temperature", model["params"])
        self.assertNotIn("top_p", model["params"])
        self.assertEqual(model["params"]["max_tokens"], 500)

    def test_max_output_cannot_exceed_half_window(self):
        model = normalize_model(
            {"model": "x", "context_size": 4096, "max_output_tokens": 100000}, CATALOG
        )
        self.assertLessEqual(model["max_output_tokens"], 2048)


class ProviderTests(unittest.TestCase):
    def test_provider_defaults(self):
        provider = normalize_provider({"name": "X"}, CATALOG)
        self.assertEqual(provider["type"], "openai")
        self.assertEqual(provider["models"], [])
        self.assertTrue(provider["id"].startswith("pv_"))

    def test_duplicate_models_are_removed(self):
        provider = normalize_provider(
            {
                "name": "X",
                "models": [{"model": "gpt-4o"}, {"model": "gpt-4o"}],
            },
            CATALOG,
        )
        self.assertEqual(len(provider["models"]), 1)

    def test_invalid_type_falls_back_to_openai(self):
        provider = normalize_provider({"name": "X", "type": "weird"}, CATALOG)
        self.assertEqual(provider["type"], "openai")

    def test_gemini_default_base_url(self):
        provider = normalize_provider({"name": "G", "type": "gemini"}, CATALOG)
        self.assertIn("generativelanguage", provider["base_url"])


class MigrationTests(unittest.TestCase):
    def _legacy_config(self):
        return {
            "proxy": {"enabled": False},
            "text_api_profiles": {
                "normal": {
                    "type": "openai",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-a",
                    "model": "deepseek-chat",
                    "model_context_size": 163840,
                    "model_capabilities": ["chat"],
                    "timeout": 120,
                },
                "nsfw": {
                    "type": "openai",
                    "base_url": "https://openrouter.ai/api/v1/",
                    "api_key": "sk-b",
                    "model": "anthropic/claude-sonnet-4",
                    "model_context_size": 1000000,
                    "timeout": 60,
                },
            },
        }

    def test_migration_creates_providers_and_bindings(self):
        cfg = ensure_providers(self._legacy_config(), CATALOG)
        self.assertEqual(len(cfg["providers"]), 2)
        normal = cfg["text_api_profiles"]["normal"]
        self.assertTrue(normal["provider_id"])
        self.assertTrue(normal["model_id"])
        runtime = resolve_runtime(cfg, "normal")
        self.assertEqual(runtime.model_key, "deepseek-chat")
        self.assertEqual(runtime.context_size, 163840)

    def test_migration_is_idempotent(self):
        cfg = ensure_providers(self._legacy_config(), CATALOG)
        again = ensure_providers(cfg, CATALOG)
        self.assertEqual(
            [p["id"] for p in cfg["providers"]], [p["id"] for p in again["providers"]]
        )
        self.assertEqual(
            cfg["text_api_profiles"]["normal"]["model_id"],
            again["text_api_profiles"]["normal"]["model_id"],
        )

    def test_shared_provider_is_reused(self):
        legacy = self._legacy_config()
        legacy["text_api_profiles"]["nsfw"] = dict(legacy["text_api_profiles"]["normal"])
        cfg = ensure_providers(legacy, CATALOG)
        self.assertEqual(len(cfg["providers"]), 1)
        self.assertEqual(len(cfg["providers"][0]["models"]), 1)

    def test_missing_model_binding_falls_back(self):
        cfg = ensure_providers(
            {
                "providers": [
                    {"id": "pv_1", "name": "P", "models": [{"id": "md_1", "model": "gpt-4o"}]}
                ],
                "text_api_profiles": {"normal": {"provider_id": "missing", "model_id": "missing"}},
            },
            CATALOG,
        )
        self.assertEqual(cfg["text_api_profiles"]["normal"]["provider_id"], "pv_1")
        self.assertEqual(cfg["text_api_profiles"]["normal"]["model_id"], "md_1")

    def test_empty_config_returns_no_runtime(self):
        cfg = ensure_providers({}, CATALOG)
        self.assertEqual(cfg["providers"], [])
        self.assertIsNone(resolve_runtime(cfg, "normal"))


class RuntimeTests(unittest.TestCase):
    def test_request_params_filters_unsupported(self):
        runtime = _model(
            supported_params=["top_p"],
            params={"temperature": 0.9, "top_p": 0.5, "top_k": 40},
        )
        params = runtime.request_params()
        self.assertEqual(params, {"top_p": 0.5})

    def test_request_params_includes_configured(self):
        runtime = _model(
            supported_params=["temperature", "top_p"],
            params={"temperature": 0.3, "top_p": 0.8},
        )
        self.assertEqual(runtime.request_params(), {"temperature": 0.3, "top_p": 0.8})

    def test_max_tokens_field_for_reasoning_model(self):
        self.assertEqual(_model(model_key="openai/o3").max_tokens_field(), "max_completion_tokens")
        self.assertEqual(_model(model_key="gpt-4o").max_tokens_field(), "max_tokens")

    def test_gemini_generation_config(self):
        runtime = _model(
            api_type="gemini",
            supported_params=["temperature", "top_k", "max_tokens", "stop"],
            params={"temperature": 0.4, "top_k": 32, "max_tokens": 2048, "stop": ["END"]},
        )
        config = runtime.gemini_generation_config()
        self.assertEqual(config["temperature"], 0.4)
        self.assertEqual(config["top_k"], 32)
        self.assertEqual(config["max_output_tokens"], 2048)
        self.assertEqual(config["stop_sequences"], ["END"])

    def test_token_budget_uses_model_window(self):
        runtime = _model(context_size=100000, max_output_tokens=8000)
        budget = runtime.token_budget()
        self.assertEqual(budget.context_size, 100000)
        self.assertEqual(budget.max_output_tokens, 8000)

    def test_token_budget_respects_param_override(self):
        runtime = _model(
            context_size=100000,
            max_output_tokens=8000,
            supported_params=["max_tokens"],
            params={"max_tokens": 512},
        )
        self.assertEqual(runtime.token_budget().max_output_tokens, 512)

    def test_endpoint_description(self):
        self.assertIn("api.example.com", _model().endpoint_desc())
        self.assertIn("google-genai", _model(api_type="gemini").endpoint_desc())


class ValidationTests(unittest.TestCase):
    def test_empty_config_reports_missing_provider(self):
        self.assertTrue(validate_config({"providers": []}))

    def test_missing_api_key_reported(self):
        cfg = {
            "providers": [
                {"id": "pv", "name": "P", "api_key": "", "models": [{"model": "gpt-4o"}]}
            ]
        }
        self.assertTrue(any("API Key" in issue for issue in validate_config(cfg)))

    def test_valid_config_has_no_issues(self):
        cfg = {
            "providers": [
                {
                    "id": "pv",
                    "name": "P",
                    "api_key": "sk",
                    "models": [
                        {
                            "model": "gpt-4o",
                            "context_size": 128000,
                            "max_output_tokens": 4096,
                        }
                    ],
                }
            ]
        }
        self.assertEqual(validate_config(cfg), [])


if __name__ == "__main__":
    unittest.main()
