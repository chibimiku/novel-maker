import unittest

from core.token_budget import (
    DEFAULT_CONTEXT_SIZE,
    TokenBudget,
    TokenEstimator,
    estimate_messages_tokens,
    estimate_tokens,
    truncate_messages_to_budget,
    truncate_text_to_budget,
)


class EstimateTokensTests(unittest.TestCase):
    def test_empty_text_is_zero(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None), 0)

    def test_chinese_is_roughly_one_token_per_char(self):
        text = "这是一段中文小说正文。" * 10
        tokens = estimate_tokens(text)
        self.assertGreaterEqual(tokens, len(text) * 0.8)
        self.assertLessEqual(tokens, len(text) * 1.2)

    def test_english_is_more_compact_than_chars(self):
        text = "The quick brown fox jumps over the lazy dog. " * 20
        self.assertLess(estimate_tokens(text), len(text))

    def test_mixed_text_is_positive(self):
        self.assertGreater(estimate_tokens("Hello 世界 123"), 0)

    def test_messages_include_protocol_overhead(self):
        messages = [{"role": "user", "content": "hello"}]
        self.assertGreater(estimate_messages_tokens(messages), estimate_tokens("hello"))

    def test_multimodal_content_parts_are_counted(self):
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "请描述这个场景" * 10}],
            }
        ]
        self.assertGreater(estimate_messages_tokens(messages), 50)


class TokenBudgetTests(unittest.TestCase):
    def test_defaults(self):
        budget = TokenBudget()
        self.assertEqual(budget.context_size, DEFAULT_CONTEXT_SIZE)
        self.assertGreater(budget.input_budget(), 0)

    def test_input_budget_scales_with_context_window(self):
        small = TokenBudget(context_size=8192, max_output_tokens=1024)
        large = TokenBudget(context_size=1_000_000, max_output_tokens=8192)
        self.assertLess(small.input_budget(), large.input_budget())
        self.assertEqual(small.input_budget(), 8192 - 1024 - 512)

    def test_output_is_clamped_to_window(self):
        # max_output 超过窗口一半时被压回一半，避免输入预算为负
        budget = TokenBudget(context_size=4096, max_output_tokens=8192)
        self.assertEqual(budget.max_output_tokens, 2048)
        self.assertGreater(budget.input_budget(), 0)

    def test_prompt_budget_subtracts_system_and_history(self):
        budget = TokenBudget(context_size=10000, max_output_tokens=2000)
        full = budget.prompt_budget(0, 0)
        reduced = budget.prompt_budget(500, 1000)
        self.assertLess(reduced, full)
        self.assertEqual(reduced, max(256, full - 1500))

    def test_history_budget_ratio(self):
        budget = TokenBudget(context_size=10000, max_output_tokens=2000, history_ratio=0.5)
        self.assertEqual(
            budget.history_budget(0), int(budget.input_budget() * 0.5)
        )

    def test_clamp_output(self):
        budget = TokenBudget(context_size=100000, max_output_tokens=8000)
        self.assertEqual(budget.clamp_output(None), 8000)
        self.assertEqual(budget.clamp_output(1024), 1024)
        self.assertEqual(budget.clamp_output(999999), 8000)
        self.assertEqual(budget.clamp_output(12), 128)


class TruncateTests(unittest.TestCase):
    def test_short_text_unchanged(self):
        text = "短文本"
        result, trimmed = truncate_text_to_budget(text, 100)
        self.assertEqual(result, text)
        self.assertFalse(trimmed)

    def test_long_text_trimmed_and_keeps_head_and_tail(self):
        head = "开头" * 100
        middle = "中间" * 5000
        tail = "结尾" * 100
        text = head + middle + tail
        result, trimmed = truncate_text_to_budget(text, 500)
        self.assertTrue(trimmed)
        self.assertTrue(result.startswith("开头"))
        self.assertTrue(result.endswith("结尾"))
        self.assertIn("已按预算压缩", result)

    def test_scale_matches_calibrated_tokens(self):
        text = "中文内容" * 1000
        budget = 500
        plain, _ = truncate_text_to_budget(text, budget)
        scaled, _ = truncate_text_to_budget(text, budget, scale=1.5)
        self.assertLessEqual(estimate_tokens(scaled), estimate_tokens(plain))
        self.assertLessEqual(estimate_tokens(scaled) * 1.5, budget * 1.08)

    def test_messages_dropped_from_front(self):
        messages = [
            {"role": "user", "content": "旧上下文" * 2000},
            {"role": "assistant", "content": "旧回复" * 2000},
            {"role": "user", "content": "最近上下文"},
        ]
        kept, dropped = truncate_messages_to_budget(messages, 200, keep_recent=1)
        self.assertTrue(dropped)
        self.assertEqual(len(kept), 1)
        self.assertIn("最近上下文", kept[0]["content"])

    def test_messages_within_budget_unchanged(self):
        messages = [{"role": "user", "content": "hi"}]
        kept, dropped = truncate_messages_to_budget(messages, 1000)
        self.assertFalse(dropped)
        self.assertEqual(kept, messages)


class TokenEstimatorTests(unittest.TestCase):
    def test_default_factor_is_one(self):
        estimator = TokenEstimator()
        self.assertEqual(estimator.factor_for("m"), 1.0)
        self.assertEqual(estimator.estimate("测试", "m"), estimate_tokens("测试"))

    def test_observe_calibrates_factor(self):
        estimator = TokenEstimator()
        text = "中文测试" * 100
        estimated = estimator.estimate(text, "m")
        for _ in range(30):
            estimator.observe("m", estimated, int(estimated * 2))
        self.assertGreater(estimator.factor_for("m"), 1.5)
        self.assertGreater(estimator.estimate(text, "m"), estimated)

    def test_factor_is_per_model(self):
        estimator = TokenEstimator()
        estimator.observe("a", 100, 200)
        self.assertNotEqual(estimator.factor_for("a"), estimator.factor_for("b"))

    def test_invalid_observations_are_ignored(self):
        estimator = TokenEstimator()
        self.assertEqual(estimator.observe("m", 0, 100), 1.0)
        self.assertEqual(estimator.observe("m", 100, 0), 1.0)
        self.assertEqual(estimator.observe("", 100, 200), 1.0)

    def test_snapshot_roundtrip(self):
        estimator = TokenEstimator({"m": 1.4})
        self.assertEqual(estimator.snapshot(), {"m": 1.4})
        restored = TokenEstimator(estimator.snapshot())
        self.assertAlmostEqual(restored.factor_for("m"), 1.4)


if __name__ == "__main__":
    unittest.main()
