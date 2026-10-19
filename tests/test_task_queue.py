import json
import os
import tempfile
import unittest

from core.task_queue import (
    TaskQueue,
    TaskStatus,
    workspace_task_root,
)


class TaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "queue")
        self.queue = TaskQueue(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _create_action(self, title="重写第一章全部小节", action_type="batch_rewrite"):
        return self.queue.begin_action(
            action_type,
            title,
            node_ids=["n1", "n2"],
            meta={"requirement": "更紧凑"},
        )

    def _create_task(self, action_id, label="改写小节", node_id="n1", **kwargs):
        return self.queue.create_task(
            label,
            action_id=action_id,
            node_id=node_id,
            node_title=f"节点 {node_id}",
            model="gpt-4o",
            provider_name="OpenAI",
            context_size=128000,
            max_output_tokens=4096,
            user_prompt="原始提示词",
            system_prompt="系统提示",
            context_messages=[{"role": "user", "content": "上下文"}],
            **kwargs,
        )

    # -- 动作 ---------------------------------------------------------
    def test_begin_action_sets_active_and_persists(self):
        action_id = self._create_action()
        self.assertEqual(self.queue.active_action_id, action_id)
        self.assertTrue(os.path.exists(os.path.join(self.root, "actions", f"{action_id}.json")))
        actions = self.queue.list_actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["title"], "重写第一章全部小节")
        self.assertEqual(actions[0]["status"], TaskStatus.RUNNING)

    def test_finish_action_derives_status_from_tasks(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.complete_task(task_id, "结果")
        self.queue.finish_action()
        action = self.queue.load_action(action_id)
        self.assertEqual(action["status"], TaskStatus.SUCCESS)
        self.assertTrue(action["finished_at"])
        self.assertIsNone(self.queue.active_action_id)

    def test_finish_action_marks_partial_failure(self):
        action_id = self._create_action()
        ok_task = self._create_task(action_id, node_id="n1")
        bad_task = self._create_task(action_id, node_id="n2")
        self.queue.complete_task(ok_task, "ok")
        self.queue.fail_task(bad_task, "boom")
        self.queue.finish_action(action_id)
        self.assertEqual(self.queue.load_action(action_id)["status"], TaskStatus.FAILED)

    # -- 任务 ---------------------------------------------------------
    def test_plan_and_claim_task(self):
        action_id = self._create_action()
        planned = self.queue.plan_task(
            "生成正文", action_id=action_id, node_id="n1", node_title="第1节"
        )
        action = self.queue.load_action(action_id)
        self.assertEqual(len(action["tasks"]), 1)
        self.assertEqual(action["tasks"][0]["task_id"], planned)
        self.assertEqual(action["tasks"][0]["status"], TaskStatus.PENDING)
        self.assertIn("排队", action["tasks"][0]["user_prompt"])

        claimed = self.queue.create_task(
            "生成正文",
            action_id=action_id,
            node_id="n1",
            node_title="第1节",
            user_prompt="真正发出去的提示词",
            task_id=planned,
        )
        self.assertEqual(claimed, planned)
        action = self.queue.load_action(action_id)
        self.assertEqual(len(action["tasks"]), 1)
        task = action["tasks"][0]
        self.assertEqual(task["seq"], 1)
        self.assertEqual(task["user_prompt"], "真正发出去的提示词")
        self.assertEqual(task["status"], TaskStatus.PENDING)

    def test_finish_action_cancels_pending_tasks(self):
        action_id = self._create_action()
        done = self._create_task(action_id, node_id="n1")
        self.queue.complete_task(done, "ok")
        pending = self.queue.plan_task("生成正文", action_id=action_id, node_id="n2")
        self.queue.finish_action(action_id)
        action = self.queue.load_action(action_id)
        by_id = {task["task_id"]: task for task in action["tasks"]}
        self.assertEqual(by_id[done]["status"], TaskStatus.SUCCESS)
        self.assertEqual(by_id[pending]["status"], TaskStatus.CANCELLED)
        self.assertEqual(action["status"], TaskStatus.CANCELLED)

    def test_claim_unknown_task_creates_new(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id, task_id="tk_does_not_exist")
        self.assertNotEqual(task_id, "tk_does_not_exist")
        self.assertEqual(len(self.queue.load_action(action_id)["tasks"]), 1)

    def test_task_lifecycle_and_fields(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.start_task(task_id)
        self.queue.complete_task(
            task_id,
            "改写后的正文",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            estimated_output_tokens=48,
        )
        task = self.queue.get_task(action_id, task_id)
        self.assertEqual(task["status"], TaskStatus.SUCCESS)
        self.assertEqual(task["output_text"], "改写后的正文")
        self.assertEqual(task["user_prompt"], "原始提示词")
        self.assertEqual(task["system_prompt"], "系统提示")
        self.assertEqual(task["context_messages"][0]["content"], "上下文")
        self.assertEqual(task["usage"]["total_tokens"], 150)
        self.assertIsNotNone(task["duration_ms"])
        self.assertEqual(task["context_size"], 128000)

    def test_task_failure_records_error(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.start_task(task_id)
        self.queue.fail_task(task_id, "HTTP 500")
        task = self.queue.get_task(action_id, task_id)
        self.assertEqual(task["status"], TaskStatus.FAILED)
        self.assertEqual(task["error"], "HTTP 500")

    def test_cancel_task(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.cancel_task(task_id, "用户停止")
        self.assertEqual(self.queue.get_task(action_id, task_id)["status"], TaskStatus.CANCELLED)

    def test_attempts_are_appended(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.add_attempt(task_id, "finish_reason=length")
        self.queue.add_attempt(task_id, "finish_reason=length")
        task = self.queue.get_task(action_id, task_id)
        self.assertEqual(len(task["attempts"]), 2)
        self.assertEqual(task["attempts"][0]["round"], 1)

    def test_create_task_without_action_creates_adhoc(self):
        task_id = self.queue.create_task("临时请求", user_prompt="hi")
        self.assertTrue(task_id)
        actions = self.queue.list_actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], "adhoc")
        self.assertTrue(actions[0]["implicit"])

    def test_implicit_action_is_auto_closed(self):
        task_id = self.queue.create_task("临时请求", user_prompt="hi")
        self.queue.start_task(task_id)
        self.queue.complete_task(task_id, "ok")
        self.assertEqual(self.queue.list_actions()[0]["status"], TaskStatus.RUNNING)
        self.queue._auto_close_implicit_actions(grace_seconds=0)
        self.assertEqual(self.queue.list_actions()[0]["status"], TaskStatus.SUCCESS)

    def test_explicit_action_is_not_auto_closed(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.complete_task(task_id, "ok")
        self.queue._auto_close_implicit_actions(grace_seconds=0)
        self.assertEqual(
            self.queue.load_action(action_id)["status"], TaskStatus.RUNNING
        )

    def test_task_ids_are_sequential(self):
        action_id = self._create_action()
        first = self._create_task(action_id, node_id="n1")
        second = self._create_task(action_id, node_id="n2")
        self.assertEqual(self.queue.get_task(action_id, first)["seq"], 1)
        self.assertEqual(self.queue.get_task(action_id, second)["seq"], 2)

    def test_action_aggregates_models_and_nodes(self):
        action_id = self._create_action()
        self._create_task(action_id, node_id="n1")
        self._create_task(action_id, node_id="n2")
        action = self.queue.load_action(action_id)
        self.assertEqual(action["node_ids"], ["n1", "n2"])
        self.assertIn("gpt-4o", action["models"])

    # -- 持久化 -------------------------------------------------------
    def test_reload_from_disk(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.complete_task(task_id, "落盘结果")
        self.queue.finish_action()

        reopened = TaskQueue(self.root)
        actions = reopened.list_actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_id"], action_id)
        task = reopened.get_task(action_id, task_id)
        self.assertEqual(task["output_text"], "落盘结果")

    def test_index_is_written(self):
        self._create_action()
        with open(os.path.join(self.root, "index.json"), "r", encoding="utf-8") as handle:
            index = json.load(handle)
        self.assertEqual(len(index["actions"]), 1)

    def test_interrupted_tasks_are_recovered(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.start_task(task_id)
        # 模拟进程退出：不调用完成，直接重新打开
        reopened = TaskQueue(self.root)
        task = reopened.get_task(action_id, task_id)
        self.assertEqual(task["status"], TaskStatus.INTERRUPTED)
        self.assertIn("中断", task["error"])
        self.assertEqual(reopened.load_action(action_id)["status"], TaskStatus.INTERRUPTED)

    # -- 清理 / 统计 ---------------------------------------------------
    def test_delete_action(self):
        action_id = self._create_action()
        self.assertTrue(self.queue.delete_action(action_id))
        self.assertEqual(self.queue.list_actions(), [])
        self.assertFalse(self.queue.delete_action("missing"))

    def test_clear_actions_keeps_running(self):
        running = self._create_action("运行中的动作")
        finished = self._create_action("已完成动作")
        task_id = self._create_task(finished)
        self.queue.complete_task(task_id, "ok")
        self.queue.finish_action(finished)
        removed = self.queue.clear_actions(keep_running=True)
        self.assertEqual(removed, 1)
        remaining = [item["action_id"] for item in self.queue.list_actions()]
        self.assertEqual(remaining, [running])

    def test_stats(self):
        action_id = self._create_action()
        task_id = self._create_task(action_id)
        self.queue.complete_task(task_id, "ok")
        self.queue.finish_action()
        stats = self.queue.stats()
        self.assertEqual(stats["actions"], 1)
        self.assertEqual(stats["tasks"], 1)
        self.assertEqual(stats["success"], 1)
        self.assertEqual(stats["running"], 0)
        self.assertEqual(stats["root"], os.path.abspath(self.root))

    def test_export_action(self):
        action_id = self._create_action()
        self._create_task(action_id)
        target = os.path.join(self.tmp.name, "export.json")
        self.assertTrue(self.queue.export_action_json(action_id, target))
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["action_id"], action_id)
        self.assertEqual(len(data["tasks"]), 1)

    def test_workspace_task_root(self):
        self.assertEqual(
            workspace_task_root("D:/book"),
            os.path.join("D:/book", "系统数据", "任务队列"),
        )
        self.assertIsNone(workspace_task_root(None))


if __name__ == "__main__":
    unittest.main()
