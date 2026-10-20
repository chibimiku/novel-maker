import os
import tempfile
import threading
import unittest

from core.agent_executor import (
    AgentRunner,
    build_context_builder,
    build_modify_prompt,
    find_node,
    _read_node_content,
)
from core.task_queue import TaskQueue, TaskStatus
from core.workspace_manager import WorkspaceManager


class FakeRuntime:
    context_size = 100000
    capabilities = ["chat"]
    model_key = "fake-model"
    display_name = "Fake Model"


class FakeLLM:
    def __init__(self, responses, queue=None):
        self.responses = list(responses)
        self.calls = []
        self.queue = queue
        self.runtime = FakeRuntime()
        self.profile = {
            "world_context_compression_profile": "balanced",
            "children_summary_compress_trigger_ratio": 0.27,
            "children_summary_group_budget_ratio": 0.24,
        }
        self.system_instruction = "你是写作系统"
        self.summary_system_instruction = "你是概要系统"
        self.modify_system_instruction = "你是修改系统"

    def generate_text(
        self,
        prompt,
        override_system_instruction=None,
        progress_callback=None,
        task_meta=None,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "system": override_system_instruction,
                "task_meta": task_meta,
            }
        )
        meta = task_meta or {}
        task_id = None
        if self.queue is not None:
            task_id = self.queue.create_task(
                str(meta.get("label") or "模型请求"),
                action_id=meta.get("action_id"),
                node_id=meta.get("node_id"),
                node_title=meta.get("node_title"),
                model="fake-model",
                provider_name="Fake",
                system_prompt=str(override_system_instruction or ""),
                user_prompt=prompt,
                task_id=meta.get("task_id"),
            )
            self.queue.start_task(task_id)
        if not self.responses:
            raise AssertionError("请求次数超过预期")
        response = self.responses.pop(0)
        if self.queue is not None and task_id:
            if str(response).startswith("> **生成失败:**"):
                self.queue.fail_task(task_id, str(response))
            else:
                self.queue.complete_task(
                    task_id,
                    response,
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
        return response


def make_tree():
    return {
        "nodes": [
            {
                "id": "c1",
                "title": "第一章",
                "children": [
                    {
                        "id": "s1",
                        "title": "第1节",
                        "children": [
                            {
                                "id": "p1",
                                "title": "场景1 苏醒",
                                "file_path": "p1.md",
                                "md5": "",
                                "_status": "ok",
                                "summary": "旧概要",
                            },
                            {
                                "id": "p2",
                                "title": "场景2 遭遇魔兽",
                                "file_path": "p2.md",
                                "_status": "missing",
                                "summary": "",
                            },
                        ],
                    }
                ],
            }
        ]
    }


def step(action, node_ids, params=None):
    return {
        "index": 1,
        "action": action,
        "action_label": {"generate": "生成正文", "rewrite": "重写正文", "summary": "生成概要", "modify": "按要求改写"}[action],
        "title": f"测试 {action}",
        "params": params or {},
        "node_ids": node_ids,
    }


class AgentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = WorkspaceManager(os.path.join(self.tmp.name, "novel"))
        self.ws.init_workspace()
        self.tree = make_tree()
        self.md5 = self.ws.save_markdown_file("p1.md", "# 场景1\n原文内容")
        scene1 = self.tree["nodes"][0]["children"][0]["children"][0]
        scene1["md5"] = self.md5
        self.queue = TaskQueue(os.path.join(self.tmp.name, "queue"))
        self.action_id = self.queue.begin_action(
            "agent", "Agent 命令：测试", node_ids=["p1", "p2"]
        )
        self.progress = []

    def tearDown(self):
        self.queue.finish_action(self.action_id)
        self.tmp.cleanup()

    def _runner(self, llm, *, stop_on_error=True, cancel_event=None):
        if getattr(llm, "queue", None) is None:
            llm.queue = self.queue
        return AgentRunner(
            workspace=self.ws,
            tree_data=self.tree,
            llm_client=llm,
            setting_paths=[],
            stop_on_error=stop_on_error,
            progress=self.progress.append,
            cancel_event=cancel_event,
        )

    # -- 生成 ---------------------------------------------------------
    def test_generate_missing_scene_writes_file_and_task(self):
        llm = FakeLLM(["# 场景2\n魔兽冲了出来。"])
        result = self._runner(llm).run(
            [step("generate", ["p2"], {"word_count": 1200, "include_next": False})],
            action_id=self.action_id,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.steps[0].status, "success")
        self.assertEqual(result.steps[0].ok_nodes, 1)

        full = os.path.join(self.ws.text_path, "p2.md")
        self.assertTrue(os.path.exists(full))
        with open(full, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "# 场景2\n魔兽冲了出来。")

        self.assertEqual(len(result.updates), 1)
        update = result.updates[0]
        self.assertEqual(update["kind"], "content")
        self.assertEqual(update["file_path"], "p2.md")
        self.assertTrue(update["md5"])

        action = self.queue.load_action(self.action_id)
        self.assertEqual(len(action["tasks"]), 1)
        task = action["tasks"][0]
        self.assertEqual(task["label"], "生成正文")
        self.assertEqual(task["node_title"], "场景2 遭遇魔兽")
        self.assertEqual(task["status"], TaskStatus.SUCCESS)
        self.assertEqual(task["output_text"], "# 场景2\n魔兽冲了出来。")
        self.assertIn("1200", llm.calls[0]["prompt"])
        self.assertEqual(llm.calls[0]["system"], None)

    def test_generate_uses_node_file_path_if_present(self):
        llm = FakeLLM(["新正文"])
        result = self._runner(llm).run([step("generate", ["p1"])], action_id=self.action_id)
        self.assertTrue(result.success)
        self.assertEqual(result.updates[0]["file_path"], "p1.md")
        with open(os.path.join(self.ws.text_path, "p1.md"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "新正文")

    def test_failure_marker_stops_step(self):
        llm = FakeLLM(["> **生成失败:** 网络错误"])
        result = self._runner(llm).run([step("generate", ["p2"])], action_id=self.action_id)
        self.assertFalse(result.success)
        self.assertEqual(result.steps[0].status, "failed")
        self.assertIn("网络错误", result.steps[0].message)
        self.assertFalse(os.path.exists(os.path.join(self.ws.text_path, "p2.md")))
        action = self.queue.load_action(self.action_id)
        self.assertEqual(action["tasks"][0]["status"], TaskStatus.FAILED)

    # -- 重写 ---------------------------------------------------------
    def test_rewrite_replaces_content(self):
        llm = FakeLLM(["重写后的正文"])
        result = self._runner(llm).run(
            [step("rewrite", ["p1"], {"word_count": 800})], action_id=self.action_id
        )
        self.assertTrue(result.success)
        with open(os.path.join(self.ws.text_path, "p1.md"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "重写后的正文")
        self.assertEqual(result.updates[0]["kind"], "content")

    def test_rewrite_empty_content_fails(self):
        llm = FakeLLM(["不应该被调用"])
        result = self._runner(llm).run([step("rewrite", ["p2"])], action_id=self.action_id)
        self.assertFalse(result.success)
        self.assertIn("没有正文", result.steps[0].message)
        self.assertEqual(llm.calls, [])

    # -- 概要 ---------------------------------------------------------
    def test_summary_updates_summary(self):
        llm = FakeLLM(["【时间】清晨\n【地点】森林\n【人物】主角\n【事件】苏醒"])
        result = self._runner(llm).run([step("summary", ["p1"])], action_id=self.action_id)
        self.assertTrue(result.success)
        self.assertEqual(result.updates[0]["kind"], "summary")
        self.assertIn("【时间】", result.updates[0]["summary"])
        self.assertEqual(llm.calls[0]["system"], "你是概要系统")
        action = self.queue.load_action(self.action_id)
        self.assertEqual(action["tasks"][0]["label"], "生成概要")

    # -- 修改 ---------------------------------------------------------
    def test_modify_saves_pending_modify(self):
        llm = FakeLLM(["修改后的正文"])
        result = self._runner(llm).run(
            [
                step(
                    "modify",
                    ["p1"],
                    {"requirement": "让开头更紧凑，保持剧情不变"},
                )
            ],
            action_id=self.action_id,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.updates[0]["kind"], "pending")
        self.assertTrue(self.ws.has_pending_modify("p1"))
        pending = self.ws.get_pending_modify("p1")
        self.assertEqual(pending["modified_text"], "修改后的正文")
        self.assertEqual(pending["original_text"], "# 场景1\n原文内容")
        self.assertIn("让开头更紧凑", llm.calls[0]["prompt"])
        self.assertEqual(llm.calls[0]["system"], "你是修改系统")

    def test_modify_without_requirement_fails(self):
        llm = FakeLLM(["x"])
        result = self._runner(llm).run([step("modify", ["p1"])], action_id=self.action_id)
        self.assertFalse(result.success)
        self.assertIn("缺少修改要求", result.steps[0].message)

    def test_pending_modify_blocks_node(self):
        self.ws.save_pending_modify("p1", "a", "b", "req")
        llm = FakeLLM(["x"])
        result = self._runner(llm).run([step("generate", ["p1"])], action_id=self.action_id)
        self.assertFalse(result.success)
        self.assertIn("待合并", result.steps[0].message)
        self.assertEqual(llm.calls, [])

    # -- 控制流 -------------------------------------------------------
    def test_stop_on_error(self):
        llm = FakeLLM(["> **生成失败:** boom"])
        result = self._runner(llm, stop_on_error=True).run(
            [step("generate", ["p1", "p2"])], action_id=self.action_id
        )
        self.assertFalse(result.success)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(result.steps[0].ok_nodes, 0)
        self.assertEqual(result.steps[0].failed_nodes, 1)

    def test_continue_on_error(self):
        llm = FakeLLM(["> **生成失败:** boom", "第二段成功"])
        result = self._runner(llm, stop_on_error=False).run(
            [step("generate", ["p1", "p2"])], action_id=self.action_id
        )
        self.assertFalse(result.success)
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(result.steps[0].ok_nodes, 1)
        self.assertEqual(result.steps[0].failed_nodes, 1)
        with open(os.path.join(self.ws.text_path, "p2.md"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "第二段成功")

    def test_cancel_before_run(self):
        event = threading.Event()
        event.set()
        llm = FakeLLM(["x"])
        result = self._runner(llm, cancel_event=event).run(
            [step("generate", ["p2"])], action_id=self.action_id
        )
        self.assertTrue(result.cancelled)
        self.assertEqual(llm.calls, [])
        self.assertEqual(result.steps[0].status, "skipped")

    def test_multi_step_order_and_updates(self):
        llm = FakeLLM(["正文内容", "概要内容"])
        result = self._runner(llm).run(
            [step("generate", ["p2"]), step("summary", ["p2"])],
            action_id=self.action_id,
        )
        self.assertTrue(result.success)
        self.assertEqual(len(result.updates), 2)
        self.assertEqual([u["kind"] for u in result.updates], ["content", "summary"])
        action = self.queue.load_action(self.action_id)
        self.assertEqual(len(action["tasks"]), 2)

    def test_progress_messages(self):
        llm = FakeLLM(["ok"])
        self._runner(llm).run([step("generate", ["p2"])], action_id=self.action_id)
        joined = "\n".join(self.progress)
        self.assertIn("开始", joined)
        self.assertIn("结束", joined)
        self.assertIn("场景2", joined)


class HelperTests(unittest.TestCase):
    def test_find_node(self):
        self.assertEqual(find_node(make_tree(), "p2")["title"], "场景2 遭遇魔兽")
        self.assertIsNone(find_node(make_tree(), "missing"))

    def test_read_node_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = WorkspaceManager(os.path.join(tmp, "novel"))
            ws.init_workspace()
            ws.save_markdown_file("a.md", "内容")
            self.assertEqual(_read_node_content(ws, {"file_path": "a.md"}), "内容")
            self.assertEqual(_read_node_content(ws, {"file_path": "none.md"}), "")
            self.assertEqual(_read_node_content(ws, {}), "")

    def test_build_modify_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = WorkspaceManager(os.path.join(tmp, "novel"))
            ws.init_workspace()
            llm = FakeLLM([])
            builder = build_context_builder(ws, llm)
            prompt = build_modify_prompt(builder, {}, [], "压缩开头", "原文")
            self.assertIn("压缩开头", prompt)
            self.assertIn("原文", prompt)
            self.assertIn("只输出修改后的完整文本", prompt)


if __name__ == "__main__":
    unittest.main()
