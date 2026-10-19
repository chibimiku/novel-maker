import json
import os
import tempfile
import unittest

from core.config_store import (
    build_runtime_config,
    deep_merge,
    load_raw_config,
    save_raw_config,
)


FIXTURE = {
    "proxy": {"enabled": False, "url": "http://127.0.0.1:7890"},
    "providers": [
        {
            "id": "pv_a",
            "name": "提供商 A",
            "type": "openai",
            "base_url": "https://api.a.com/v1",
            "api_key": "sk-a",
            "timeout": 60,
            "models": [
                {
                    "id": "md_a1",
                    "model": "gpt-4o",
                    "display_name": "GPT-4o",
                    "context_size": 128000,
                    "max_output_tokens": 16384,
                    "capabilities": ["chat", "vision"],
                    "supported_params": ["temperature", "top_p", "max_tokens"],
                    "params": {"temperature": 0.5, "top_p": 0.9},
                }
            ],
        },
        {
            "id": "pv_b",
            "name": "提供商 B",
            "type": "openai",
            "base_url": "https://api.b.com/v1",
            "api_key": "sk-b",
            "timeout": 120,
            "models": [
                {
                    "id": "md_b1",
                    "model": "deepseek-chat",
                    "display_name": "DeepSeek",
                    "context_size": 160000,
                    "max_output_tokens": 8192,
                    "capabilities": ["chat"],
                    "supported_params": ["temperature", "max_tokens"],
                    "params": {"temperature": 0.7},
                }
            ],
        },
    ],
    "text_api_profiles": {
        "normal": {
            "provider_id": "pv_a",
            "model_id": "md_a1",
            "instructions": "写作指令",
            "summary_instructions": "概要指令",
            "modify_instructions": "修改指令",
            "world_context_compression_profile": "balanced",
        },
        "nsfw": {
            "provider_id": "pv_b",
            "model_id": "md_b1",
            "instructions": "NSFW 指令",
        },
    },
    "text_api_active_profile": "normal",
    "task_completion_notification_enabled": True,
    "recent_api_configs": [],
}


class BuildRuntimeConfigTests(unittest.TestCase):
    def test_derived_text_api_comes_from_active_model(self):
        cfg = build_runtime_config(FIXTURE)
        api = cfg["text_api"]
        self.assertEqual(api["model"], "gpt-4o")
        self.assertEqual(api["model_context_size"], 128000)
        self.assertEqual(api["model_max_output_tokens"], 16384)
        self.assertEqual(api["type"], "openai")
        self.assertEqual(api["base_url"], "https://api.a.com/v1")
        self.assertEqual(api["model_params"]["temperature"], 0.5)
        self.assertEqual(api["provider_name"], "提供商 A")

    def test_nsfw_profile_switch(self):
        cfg = build_runtime_config(FIXTURE, prefer_nsfw=True)
        self.assertEqual(cfg["text_api_active_profile"], "nsfw")
        self.assertEqual(cfg["text_api"]["model"], "deepseek-chat")
        self.assertEqual(cfg["text_api"]["model_context_size"], 160000)

    def test_legacy_config_is_migrated(self):
        legacy = {
            "text_api_profiles": {
                "normal": {
                    "type": "openai",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-x",
                    "model": "deepseek-chat",
                    "model_context_size": 163840,
                }
            }
        }
        cfg = build_runtime_config(legacy)
        self.assertEqual(len(cfg["providers"]), 1)
        self.assertEqual(cfg["text_api"]["model"], "deepseek-chat")
        self.assertEqual(cfg["text_api"]["model_context_size"], 163840)

    def test_empty_config_has_no_runtime(self):
        cfg = build_runtime_config({})
        self.assertEqual(cfg["providers"], [])
        self.assertEqual(cfg["text_api"]["model_context_size"], 8192)
        self.assertNotIn("model", cfg["text_api"])

    def test_input_not_mutated(self):
        import copy

        original = copy.deepcopy(FIXTURE)
        build_runtime_config(FIXTURE, prefer_nsfw=True)
        self.assertEqual(FIXTURE, original)

    def test_profile_without_binding_falls_back_to_first_model(self):
        cfg = {
            "providers": FIXTURE["providers"],
            "text_api_profiles": {"normal": {"instructions": "x"}},
        }
        built = build_runtime_config(cfg)
        self.assertEqual(built["text_api"]["model"], "gpt-4o")


class DeepMergeTests(unittest.TestCase):
    def test_merge_keeps_unknown_keys(self):
        base = {"a": 1, "nested": {"x": 1, "y": 2}}
        merged = deep_merge(base, {"nested": {"y": 3}, "b": 2})
        self.assertEqual(merged["a"], 1)
        self.assertEqual(merged["nested"], {"x": 1, "y": 3})
        self.assertEqual(merged["b"], 2)

    def test_merge_does_not_mutate_base(self):
        base = {"nested": {"x": 1}}
        deep_merge(base, {"nested": {"x": 2}})
        self.assertEqual(base["nested"]["x"], 1)


class SaveLoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "conf", "setting.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        self.assertTrue(save_raw_config(FIXTURE, self.path))
        loaded = load_raw_config(self.path)
        self.assertEqual(
            loaded["text_api_profiles"]["normal"]["model_id"], "md_a1"
        )
        self.assertEqual(len(loaded["providers"]), 2)

    def test_save_creates_directories(self):
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))
        self.assertTrue(save_raw_config({"a": 1}, self.path))
        self.assertTrue(os.path.exists(self.path))

    def test_load_missing_returns_default(self):
        loaded = load_raw_config(self.path)
        self.assertIn("providers", loaded)
        self.assertEqual(loaded["providers"], [])

    def test_load_corrupt_returns_default(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        loaded = load_raw_config(self.path)
        self.assertIsInstance(loaded, dict)
        self.assertIn("providers", loaded)

    def test_saved_json_keeps_chinese(self):
        save_raw_config(FIXTURE, self.path)
        with open(self.path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        self.assertIn("提供商 A", raw)
        self.assertTrue(json.loads(raw))


if __name__ == "__main__":
    unittest.main()
