import json
import unittest

from core.agent_planner import (
    AgentPlanner,
    all_level3_nodes,
    build_outline_text,
    build_plan_prompt,
    describe_plan,
    find_node_with_level,
    is_missing,
    iter_nodes,
    missing_level3_nodes,
    normalize_plan,
    parse_plan_response,
)


def make_tree():
    return {
        "nodes": [
            {
                "id": "c1",
                "title": "第一章 初入异世",
                "children": [
                    {
                        "id": "s1",
                        "title": "第1节 迷雾森林",
                        "children": [
                            {
                                "id": "p1",
                                "title": "场景1 苏醒",
                                "file_path": "p1.md",
                                "md5": "x",
                                "_status": "ok",
                                "summary": "主角醒来",
                            },
                            {
                                "id": "p2",
                                "title": "场景2 遭遇魔兽",
                                "file_path": "p2.md",
                                "_status": "missing",
                                "summary": "",
                            },
                            {
                                "id": "p3",
                                "title": "场景3 逃离",
                                "file_path": "p3.md",
                                "_status": "ok",
                                "summary": "逃出森林",
                            },
                        ],
                    },
                    {
                        "id": "s2",
                        "title": "第2节 城镇",
                        "children": [
                            {
                                "id": "p4",
                                "title": "场景4 入城",
                                "file_path": "p4.md",
                                "_status": "ok",
                                "summary": "",
                            }
                        ],
                    },
                ],
            },
            {
                "id": "c2",
                "title": "第二章 王都",
                "children": [
                    {
                        "id": "s3",
                        "title": "第1节 城门",
                        "children": [
                            {
                                "id": "p5",
                                "title": "场景5 盘查",
                                "file_path": "",
                                "_status": "missing",
                            }
                        ],
                    }
                ],
            },
        ]
    }


class TreeUtilsTests(unittest.TestCase):
    def test_iter_nodes_levels(self):
        pairs = [(node["id"], level) for node, level in iter_nodes(make_tree()["nodes"])]
        self.assertIn(("c1", 1), pairs)
        self.assertIn(("s1", 2), pairs)
        self.assertIn(("p1", 3), pairs)

    def test_find_node(self):
        node, level = find_node_with_level(make_tree(), "p2")
        self.assertEqual(level, 3)
        self.assertEqual(node["title"], "场景2 遭遇魔兽")
        self.assertIsNone(find_node_with_level(make_tree(), "nope")[0])

    def test_all_level3(self):
        self.assertEqual(
            [node["id"] for node in all_level3_nodes(make_tree())],
            ["p1", "p2", "p3", "p4", "p5"],
        )

    def test_missing_detection(self):
        self.assertTrue(is_missing({"file_path": ""}))
        self.assertTrue(is_missing({"file_path": "x", "_status": "missing"}))
        self.assertFalse(is_missing({"file_path": "x", "_status": "ok"}))

    def test_missing_level3_all_and_scoped(self):
        self.assertEqual(
            [node["id"] for node in missing_level3_nodes(make_tree())], ["p2", "p5"]
        )
        chapter_one, _ = find_node_with_level(make_tree(), "c1")
        self.assertEqual(
            [node["id"] for node in missing_level3_nodes(make_tree(), chapter_one)],
            ["p2"],
        )

    def test_outline_text_contains_ids_and_status(self):
        text = build_outline_text(make_tree())
        self.assertIn("[L1] 第一章 初入异世 (c1)", text)
        self.assertIn("[L3] 场景2 遭遇魔兽 (p2)", text)
        self.assertIn("缺失正文", text)

    def test_outline_truncation(self):
        tree = {"nodes": [{"id": f"c{i}", "title": "章" * 50, "children": [], "level": 1} for i in range(200)]}
        text = build_outline_text(tree, max_chars=500)
        self.assertIn("已截断", text)


class PromptTests(unittest.TestCase):
    def test_prompt_contains_tools_and_command(self):
        prompt = build_plan_prompt(
            "把第一章下面所有缺失的场景补完",
            make_tree(),
            checked_titles=["主角.md", "王都.md"],
            model_label="DeepSeek Chat",
        )
        self.assertIn("generate", prompt)
        self.assertIn("rewrite", prompt)
        self.assertIn("summary", prompt)
        self.assertIn("modify", prompt)
        self.assertIn("缺失正文的场景", prompt)
        self.assertIn("把第一章下面所有缺失的场景补完", prompt)
        self.assertIn("主角.md、王都.md", prompt)
        self.assertIn("DeepSeek Chat", prompt)
        self.assertIn("只输出一个 JSON 对象", prompt)


class ParsePlanTests(unittest.TestCase):
    def test_parse_plain_json(self):
        data = parse_plan_response('{"goal":"x","steps":[]}')
        self.assertEqual(data["goal"], "x")

    def test_parse_fenced_json(self):
        text = '好的，计划如下：\n```json\n{"goal":"g","steps":[{"action":"summary"}]}\n```\n完毕'
        data = parse_plan_response(text)
        self.assertEqual(len(data["steps"]), 1)

    def test_parse_prose_wrapped(self):
        text = '解释一些文字\n{"goal":"g","steps":[]}\n后面还有字'
        self.assertEqual(parse_plan_response(text)["goal"], "g")

    def test_parse_failure_message(self):
        with self.assertRaises(ValueError):
            parse_plan_response("> **生成失败:** 网络错误")

    def test_parse_empty(self):
        with self.assertRaises(ValueError):
            parse_plan_response("")

    def test_parse_missing_steps(self):
        with self.assertRaises(ValueError):
            parse_plan_response('{"goal":"x"}')

    def test_parse_not_object(self):
        with self.assertRaises(ValueError):
            parse_plan_response("[1,2,3]")


class NormalizePlanTests(unittest.TestCase):
    def _normalize(self, steps, checked=None):
        plan, errors = normalize_plan(
            {"goal": "测试", "steps": steps}, make_tree(), checked_ids=checked or []
        )
        return plan, errors

    def test_node_scope(self):
        plan, errors = self._normalize(
            [{"action": "summary", "scope": {"type": "node", "node_id": "p1"}}]
        )
        self.assertEqual(errors, [])
        self.assertEqual(plan["steps"][0]["node_ids"], ["p1"])

    def test_chapter_scope(self):
        plan, errors = self._normalize(
            [{"action": "generate", "scope": {"type": "chapter", "node_id": "c1"}}]
        )
        self.assertEqual(errors, [])
        self.assertEqual(plan["steps"][0]["node_ids"], ["p1", "p2", "p3", "p4"])

    def test_section_scope(self):
        plan, errors = self._normalize(
            [{"action": "rewrite", "scope": {"type": "section", "node_id": "s1"}}]
        )
        self.assertEqual(errors, [])
        self.assertEqual(plan["steps"][0]["node_ids"], ["p1", "p2", "p3"])

    def test_missing_scope(self):
        plan, errors = self._normalize(
            [{"action": "generate", "scope": {"type": "missing"}}]
        )
        self.assertEqual(errors, [])
        self.assertEqual(plan["steps"][0]["node_ids"], ["p2", "p5"])

    def test_missing_scope_limited_to_chapter(self):
        plan, errors = self._normalize(
            [
                {
                    "action": "generate",
                    "scope": {"type": "missing", "node_id": "c1"},
                }
            ]
        )
        self.assertEqual(errors, [])
        self.assertEqual(plan["steps"][0]["node_ids"], ["p2"])

    def test_checked_scope(self):
        plan, errors = self._normalize(
            [{"action": "generate", "scope": {"type": "checked"}}],
            checked=["p1", "p2"],
        )
        self.assertEqual(errors, [])
        self.assertEqual(plan["steps"][0]["node_ids"], ["p1", "p2"])

    def test_all_scope(self):
        plan, errors = self._normalize(
            [{"action": "summary", "scope": {"type": "all"}}]
        )
        self.assertEqual(len(plan["steps"][0]["node_ids"]), 5)

    def test_explicit_nodes(self):
        plan, errors = self._normalize(
            [{"action": "summary", "nodes": ["p3", "p1", "p3"]}]
        )
        self.assertEqual(errors, [])
        self.assertEqual(plan["steps"][0]["node_ids"], ["p3", "p1"])

    def test_unknown_action(self):
        plan, errors = self._normalize(
            [{"action": "delete_everything", "scope": {"type": "all"}}]
        )
        self.assertEqual(plan["steps"], [])
        self.assertTrue(any("未知动作" in error for error in errors))

    def test_node_scope_requires_level3(self):
        plan, errors = self._normalize(
            [{"action": "generate", "scope": {"type": "node", "node_id": "c1"}}]
        )
        self.assertEqual(plan["steps"], [])
        self.assertTrue(any("3 级场景" in error for error in errors))

    def test_chapter_scope_requires_level1(self):
        _plan, errors = self._normalize(
            [{"action": "generate", "scope": {"type": "chapter", "node_id": "s1"}}]
        )
        self.assertTrue(any("需要 1 级节点" in error for error in errors))

    def test_missing_requirement_rejected(self):
        plan, errors = self._normalize(
            [{"action": "modify", "scope": {"type": "node", "node_id": "p1"}, "params": {}}]
        )
        self.assertEqual(plan["steps"], [])
        self.assertTrue(any("requirement" in error for error in errors))

    def test_word_count_clamped_and_defaults(self):
        plan, _errors = self._normalize(
            [
                {
                    "action": "generate",
                    "scope": {"type": "node", "node_id": "p1"},
                    "params": {"word_count": 999999, "include_next": False},
                }
            ]
        )
        step = plan["steps"][0]
        self.assertEqual(step["params"]["word_count"], 20000)
        self.assertFalse(step["params"]["include_next"])

        plan, _ = self._normalize(
            [{"action": "rewrite", "nodes": ["p1"], "params": {}}]
        )
        self.assertEqual(plan["steps"][0]["params"]["word_count"], 3000)

    def test_default_title(self):
        plan, _ = self._normalize(
            [{"action": "summary", "scope": {"type": "node", "node_id": "p1"}}]
        )
        self.assertIn("生成概要", plan["steps"][0]["title"])
        self.assertIn("场景1 苏醒", plan["steps"][0]["title"])

    def test_multiple_scene_title(self):
        plan, _ = self._normalize(
            [{"action": "generate", "scope": {"type": "chapter", "node_id": "c1"}}]
        )
        self.assertIn("等 4 个场景", plan["steps"][0]["title"])

    def test_step_limit(self):
        steps = [
            {"action": "summary", "nodes": ["p1"], "title": f"步骤{i}"}
            for i in range(40)
        ]
        plan, errors = self._normalize(steps)
        self.assertEqual(len(plan["steps"]), 30)
        self.assertTrue(any("上限" in error for error in errors))


class DescribePlanTests(unittest.TestCase):
    def test_describe(self):
        plan, _ = normalize_plan(
            {
                "goal": "补完第一章",
                "steps": [
                    {
                        "action": "generate",
                        "scope": {"type": "chapter", "node_id": "c1"},
                        "params": {"word_count": 3000},
                    }
                ],
            },
            make_tree(),
        )
        text = describe_plan(plan)
        self.assertIn("目标：补完第一章", text)
        self.assertIn("生成正文", text)
        self.assertIn("3000字", text)


class _FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.runtime = type(
            "RT", (), {"label": lambda self: "Fake/Model"}
        )()

    def generate_text(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.response


class AgentPlannerTests(unittest.TestCase):
    def test_plan_success(self):
        response = json.dumps(
            {
                "goal": "补完第一章缺失场景",
                "steps": [
                    {
                        "action": "generate",
                        "scope": {"type": "missing", "node_id": "c1"},
                        "params": {"word_count": 3000},
                    }
                ],
            }
        )
        llm = _FakeLLM(response)
        planner = AgentPlanner(llm)
        result = planner.plan(
            "补完第一章所有缺失场景",
            make_tree(),
            checked_paths=["D:/ws/设定/人物/主角.md"],
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.plan["goal"], "补完第一章缺失场景")
        self.assertEqual(result.plan["steps"][0]["node_ids"], ["p2"])
        prompt, kwargs = llm.calls[0]
        self.assertIn("补完第一章所有缺失场景", prompt)
        self.assertIn("主角.md", prompt)
        self.assertIn("任务规划助手", kwargs["override_system_instruction"])

    def test_plan_parse_error(self):
        llm = _FakeLLM("我不知道怎么规划")
        result = AgentPlanner(llm).plan("随便", make_tree())
        self.assertFalse(result.ok)
        self.assertTrue(result.errors)
        self.assertEqual(result.plan["steps"], [])

    def test_plan_invalid_scope(self):
        llm = _FakeLLM(
            json.dumps(
                {
                    "goal": "x",
                    "steps": [
                        {"action": "generate", "nodes": ["not_exist"]}
                    ],
                }
            )
        )
        result = AgentPlanner(llm).plan("x", make_tree())
        self.assertFalse(result.ok)
        self.assertTrue(any("范围无效" in error for error in result.errors))

    def test_no_llm(self):
        result = AgentPlanner(None).plan("x", make_tree())
        self.assertFalse(result.ok)
        self.assertIn("尚未配置", result.errors[0])


if __name__ == "__main__":
    unittest.main()
