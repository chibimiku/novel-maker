import unittest
from unittest.mock import patch

from ui.workers import SettingSelectionThread


def _collect_signals(thread):
    progress_msgs = []
    success_args = []
    error_args = []
    thread.progress_signal.connect(lambda msg: progress_msgs.append(msg))
    thread.success_signal.connect(lambda paths: success_args.append(paths))
    thread.error_signal.connect(lambda obj: error_args.append(obj))
    return progress_msgs, success_args, error_args


# ============================================================
# _parse_smart_setting_selection_ids 静态方法测试
# ============================================================

class ParseSmartSelectionIdsTests(unittest.TestCase):
    def test_valid_json_with_selected_ids(self):
        result = SettingSelectionThread._parse_smart_setting_selection_ids(
            '{"selected_ids":[3,1,5]}'
        )
        self.assertEqual(result, [3, 1, 5])

    def test_valid_json_with_ids_key(self):
        result = SettingSelectionThread._parse_smart_setting_selection_ids(
            '{"ids":[2,4]}'
        )
        self.assertEqual(result, [2, 4])

    def test_valid_json_with_indexes_key(self):
        result = SettingSelectionThread._parse_smart_setting_selection_ids(
            '{"indexes":[7,8]}'
        )
        self.assertEqual(result, [7, 8])

    def test_plain_array(self):
        result = SettingSelectionThread._parse_smart_setting_selection_ids(
            "[1,2,3]"
        )
        self.assertEqual(result, [1, 2, 3])

    def test_duplicates_removed(self):
        result = SettingSelectionThread._parse_smart_setting_selection_ids(
            '{"selected_ids":[1,2,2,1,3]}'
        )
        self.assertEqual(result, [1, 2, 3])

    def test_zero_or_negative_filtered_out(self):
        result = SettingSelectionThread._parse_smart_setting_selection_ids(
            '{"selected_ids":[0, -1, 2, 3, 0]}'
        )
        self.assertEqual(result, [2, 3])

    def test_empty_string_or_none_returns_empty(self):
        self.assertEqual(
            SettingSelectionThread._parse_smart_setting_selection_ids(""),
            [],
        )
        self.assertEqual(
            SettingSelectionThread._parse_smart_setting_selection_ids(None),
            [],
        )

    def test_invalid_json_returns_empty_list(self):
        self.assertEqual(
            SettingSelectionThread._parse_smart_setting_selection_ids("not json"),
            [],
        )

    def test_markdown_wrapped_json_still_parsed(self):
        result = SettingSelectionThread._parse_smart_setting_selection_ids(
            '```json\n{"selected_ids":[10,20]}\n```'
        )
        self.assertEqual(result, [10, 20])


# ============================================================
# _run_impl 主线测试
# 注意: compute_local_relevance / SettingRelevanceCache 在 _run_impl
# 内部通过 from ... import 懒加载，因此补丁目标为其所属模块
# ============================================================

_CORE_SETTING_MODULE = "core.setting_relevance"
_CACHE_MODULE = "core.setting_relevance_cache"

_CANDIDATE_3 = [
    "d:/ws/settings/人物设定/主角.json",
    "d:/ws/settings/地点设定/小镇.json",
    "d:/ws/settings/公共设定/世界观.json",
]

_FAKE_RELEVANCE_3 = [
    ("d:/ws/settings/人物设定/主角.json", 0.85),
    ("d:/ws/settings/公共设定/世界观.json", 0.60),
    ("d:/ws/settings/地点设定/小镇.json", 0.40),
]


class _FakeCache:
    def __init__(self, workspace_path=None):
        self.workspace_path = workspace_path
        self.saved_scores = None
        self.saved_selection = None
        self._cached_scores = None
        self._cached_selection = None

    def load(self, node_id, node_summary, candidate_paths):
        return self._cached_scores

    def save(self, node_id, node_summary, candidate_paths, scores):
        self.saved_scores = (node_id, candidate_paths, scores)

    def load_selection(self, node_id, node_summary, candidate_paths):
        return self._cached_selection

    def save_selection(self, node_id, node_summary, candidate_paths,
                       selected_paths, relevance_scores):
        self.saved_selection = (node_id, candidate_paths, selected_paths)


class _FakeLLMClient:
    def __init__(self, raw_response='{"selected_ids":[1,2]}'):
        self.raw_response = raw_response
        self.generate_calls = []

    def generate_text(self, prompt, override_system_instruction=None,
                      progress_callback=None):
        self.generate_calls.append({
            "prompt": prompt,
            "system": override_system_instruction,
            "progress_callback": progress_callback,
        })
        return self.raw_response


def _make_thread(**overrides):
    defaults = {
        "llm_client": _FakeLLMClient(),
        "workspace_path": "d:/ws",
        "task_name": "生成正文",
        "target_node": {"id": "n1", "title": "主角登场", "summary": "主角来到小镇"},
        "task_context_prompt": "生成第一章内容",
        "use_cache": True,
        "checked_paths": ["d:/ws/settings/主角.json"],
        "candidate_paths": _CANDIDATE_3,
    }
    defaults.update(overrides)
    return SettingSelectionThread(**defaults)


class RunImplTests(unittest.TestCase):
    @patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache)
    @patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
           lambda *a, **kw: _FAKE_RELEVANCE_3)
    def test_full_flow_llm_returns_valid_selection(self):
        thread = _make_thread()
        progress, success, error = _collect_signals(thread)

        thread._run_impl()

        self.assertEqual(len(success), 1)
        selected = success[0]
        self.assertIn("主角.json", selected[0])
        self.assertIn("世界观.json", selected[1])
        self.assertEqual(len(error), 0)
        self.assertTrue(any("智能勾选完成" in msg for msg in progress))

    @patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache)
    @patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
           lambda *a, **kw: _FAKE_RELEVANCE_3)
    def test_full_flow_llm_returns_empty_selection_fallback_to_checked(self):
        thread = _make_thread(
            llm_client=_FakeLLMClient('{"selected_ids":[]}'),
        )
        progress, success, error = _collect_signals(thread)

        thread._run_impl()

        self.assertEqual(len(success), 1)
        self.assertEqual(success[0], thread.checked_paths)
        self.assertEqual(len(error), 0)
        self.assertTrue(any("结果为空" in msg for msg in progress))

    @patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache)
    @patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
           lambda *a, **kw: _FAKE_RELEVANCE_3)
    def test_full_flow_llm_throws_emits_fallback(self):
        bad_client = _FakeLLMClient()
        def raise_err(*a, **kw):
            raise ConnectionError("network down")
        bad_client.generate_text = raise_err
        thread = _make_thread(llm_client=bad_client)
        progress, success, error = _collect_signals(thread)

        thread._run_impl()

        self.assertEqual(len(success), 1)
        self.assertEqual(success[0], thread.checked_paths)
        self.assertTrue(any("智能勾选失败" in msg for msg in progress))

    @patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache)
    @patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
           lambda *a, **kw: _FAKE_RELEVANCE_3)
    def test_llm_response_ids_not_found_in_map_falls_back(self):
        thread = _make_thread(
            llm_client=_FakeLLMClient('{"selected_ids":[99,100]}'),
        )
        progress, success, error = _collect_signals(thread)

        thread._run_impl()

        self.assertEqual(len(success), 1)
        self.assertEqual(success[0], thread.checked_paths)
        self.assertTrue(any("结果为空" in msg for msg in progress))

    @patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache)
    @patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
           lambda *a, **kw: _FAKE_RELEVANCE_3)
    def test_llm_response_invalid_json_still_emits_fallback(self):
        thread = _make_thread(
            llm_client=_FakeLLMClient("not a json at all"),
        )
        progress, success, error = _collect_signals(thread)

        thread._run_impl()

        self.assertEqual(len(success), 1)
        self.assertEqual(success[0], thread.checked_paths)
        self.assertTrue(any("结果为空" in msg for msg in progress))

    def test_empty_candidate_paths_emits_checked_paths_immediately(self):
        thread = _make_thread(candidate_paths=[])
        progress, success, error = _collect_signals(thread)

        thread._run_impl()

        self.assertEqual(len(success), 1)
        self.assertEqual(success[0], thread.checked_paths)
        self.assertEqual(len(progress), 0)

    def test_cache_hit_scores_skip_local_computation(self):
        fake_cache = _FakeCache()
        fake_cache._cached_scores = _FAKE_RELEVANCE_3
        with patch(_CACHE_MODULE + ".SettingRelevanceCache",
                   return_value=fake_cache):
            thread = _make_thread(use_cache=True)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        self.assertTrue(any("从缓存加载设定相关性评分" in msg for msg in progress))
        self.assertEqual(len(success), 1)

    def test_cache_hit_selection_skips_llm_call(self):
        fake_cache = _FakeCache()
        fake_cache._cached_scores = _FAKE_RELEVANCE_3
        fake_cache._cached_selection = [
            "d:/ws/settings/人物设定/主角.json",
            "d:/ws/settings/公共设定/世界观.json",
        ]
        llm = _FakeLLMClient()
        with patch(_CACHE_MODULE + ".SettingRelevanceCache",
                   return_value=fake_cache):
            thread = _make_thread(use_cache=True, llm_client=llm)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        self.assertEqual(len(llm.generate_calls), 0)
        self.assertTrue(any("从缓存加载LLM勾选结果" in msg for msg in progress))
        self.assertEqual(success[0], fake_cache._cached_selection)

    def test_cache_selection_stale_but_scores_hit_still_calls_llm(self):
        fake_cache = _FakeCache()
        fake_cache._cached_scores = _FAKE_RELEVANCE_3
        fake_cache._cached_selection = ["d:/ws/settings/不存在的设定.json"]
        llm = _FakeLLMClient('{"selected_ids":[1,3]}')
        with patch(_CACHE_MODULE + ".SettingRelevanceCache",
                   return_value=fake_cache):
            thread = _make_thread(use_cache=True, llm_client=llm)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        self.assertEqual(len(llm.generate_calls), 1)
        self.assertTrue(any("缓存勾选结果已失效" in msg for msg in progress))

    def test_use_cache_false_bypasses_all_cache(self):
        fake_cache = _FakeCache()
        fake_cache._cached_scores = _FAKE_RELEVANCE_3
        fake_cache._cached_selection = _FAKE_RELEVANCE_3
        llm = _FakeLLMClient('{"selected_ids":[1]}')
        with patch(_CACHE_MODULE + ".SettingRelevanceCache",
                   return_value=fake_cache):
            thread = _make_thread(use_cache=False, llm_client=llm)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        self.assertEqual(len(llm.generate_calls), 1)
        self.assertFalse(any("从缓存加载" in msg for msg in progress))

    def test_compute_local_relevance_saves_to_cache(self):
        fake_cache = _FakeCache()
        with patch(_CACHE_MODULE + ".SettingRelevanceCache",
                   return_value=fake_cache), \
             patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
                   lambda *a, **kw: _FAKE_RELEVANCE_3):
            thread = _make_thread(use_cache=True)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        self.assertIsNotNone(fake_cache.saved_scores)
        saved_scores = fake_cache.saved_scores[2]
        self.assertEqual(len(saved_scores), 3)

    def test_llm_selection_saves_to_cache(self):
        fake_cache = _FakeCache()
        with patch(_CACHE_MODULE + ".SettingRelevanceCache",
                   return_value=fake_cache), \
             patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
                   lambda *a, **kw: _FAKE_RELEVANCE_3):
            thread = _make_thread(use_cache=True)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        self.assertIsNotNone(fake_cache.saved_selection)
        saved = fake_cache.saved_selection[2]
        self.assertEqual(len(saved), 2)

    def test_progress_provides_step_updates_during_relevance_calc(self):
        many_candidates = [f"d:/ws/settings/人物设定/c{i}.json"
                           for i in range(35)]
        fake_scores = [(p, 0.5) for p in many_candidates]

        def fake_compute(summary, title, paths, progress_callback=None):
            total = len(paths)
            for i in range(total):
                if progress_callback:
                    progress_callback(i, total)
            if progress_callback:
                progress_callback(total, total)
            return fake_scores

        with patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache), \
             patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
                   fake_compute):
            thread = _make_thread(candidate_paths=many_candidates)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        self.assertTrue(any("相关性计算完成" in msg for msg in progress))
        self.assertTrue(any("相关性计算进度" in msg for msg in progress))

    def test_target_node_minimal_still_produces_selection(self):
        node = {"id": "", "title": "", "summary": ""}
        with patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache), \
             patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
                   lambda *a, **kw: _FAKE_RELEVANCE_3):
            thread = _make_thread(target_node=node)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        self.assertEqual(len(success), 1)
        self.assertIn("主角.json", success[0][0])

    def test_long_context_is_truncated_in_llm_prompt(self):
        task_context = "x" * 4000
        llm = _FakeLLMClient('{"selected_ids":[1]}')
        with patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache), \
             patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
                   lambda *a, **kw: _FAKE_RELEVANCE_3):
            thread = _make_thread(
                task_context_prompt=task_context,
                llm_client=llm,
            )
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        prompt = llm.generate_calls[0]["prompt"]
        self.assertIn("已截断", prompt)
        self.assertNotIn(task_context, prompt)

    def test_llm_system_instruction_is_passed(self):
        llm = _FakeLLMClient('{"selected_ids":[1]}')
        with patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache), \
             patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
                   lambda *a, **kw: _FAKE_RELEVANCE_3):
            thread = _make_thread(llm_client=llm)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        sys = llm.generate_calls[0]["system"]
        self.assertIn("JSON 输出助手", sys)

    def test_progress_callback_passed_to_llm_client(self):
        llm = _FakeLLMClient('{"selected_ids":[1]}')
        with patch(_CACHE_MODULE + ".SettingRelevanceCache", _FakeCache), \
             patch(_CORE_SETTING_MODULE + ".compute_local_relevance",
                   lambda *a, **kw: _FAKE_RELEVANCE_3):
            thread = _make_thread(llm_client=llm)
            progress, success, error = _collect_signals(thread)
            thread._run_impl()

        kwargs = llm.generate_calls[0]
        self.assertIn("progress_callback", kwargs)
        self.assertIsNotNone(kwargs.get("progress_callback"))


# ============================================================
# run() 顶层异常兜底测试
# ============================================================

class RunTopLevelErrorTests(unittest.TestCase):
    def test_run_impl_raises_non_llm_error_emits_fallback(self):
        thread = _make_thread()
        progress, success, error = _collect_signals(thread)

        def boom():
            raise RuntimeError("磁盘写入失败")
        thread._run_impl = boom

        thread.run()

        self.assertEqual(len(success), 1)
        self.assertEqual(success[0], thread.checked_paths)
        self.assertTrue(any("磁盘写入失败" in msg for msg in progress))
        self.assertTrue(any("智能设定筛选异常" in msg for msg in progress))
        self.assertTrue(any("回退手动勾选设定" in msg for msg in progress))

    def test_run_impl_raises_emits_error_signal(self):
        thread = _make_thread()
        progress, success, error = _collect_signals(thread)

        def boom():
            raise ValueError("bad path")
        thread._run_impl = boom

        thread.run()

        self.assertEqual(len(success), 1)
        self.assertEqual(success[0], thread.checked_paths)
        self.assertTrue(any("<font color='red'>" in msg for msg in progress))


if __name__ == "__main__":
    unittest.main()
