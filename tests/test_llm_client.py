import os
import tempfile
import types
import unittest
from unittest.mock import patch

from core.llm_client import LLMClient
from core.model_config import RuntimeModel
from core.task_queue import TaskQueue, TaskStatus


# ---------------------------------------------------------------------------
# 假的 OpenAI SDK
# ---------------------------------------------------------------------------

class FakeUsage:
    def __init__(self, prompt_tokens=10, completion_tokens=5):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = FakeMessage(content)
        self.finish_reason = finish_reason


class FakeResponse:
    def __init__(self, content, finish_reason="stop", usage=None):
        self.choices = [FakeChoice(content, finish_reason)]
        self.usage = usage if usage is not None else FakeUsage()


class FakeCompletions:
    def __init__(self, responses, calls):
        self._responses = list(responses)
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        if not self._responses:
            raise AssertionError("请求次数超过预期")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeChat:
    def __init__(self, responses, calls):
        self.completions = FakeCompletions(responses, calls)


class FakeOpenAI:
    def __init__(self, responses, calls):
        self.chat = FakeChat(responses, calls)


# ---------------------------------------------------------------------------
# 配置 fixture
# ---------------------------------------------------------------------------

def make_config(
    *,
    context_size=100000,
    max_output_tokens=4000,
    supported_params=None,
    params=None,
    api_type="openai",
    auto_continue=True,
    max_rounds=3,
):
    supported_params = supported_params or ["temperature", "top_p", "max_tokens"]
    params = params if params is not None else {"temperature": 0.4, "top_p": 0.9}
    return {
        "proxy": {"enabled": False},
        "providers": [
            {
                "id": "pv_1",
                "name": "测试提供商",
                "type": api_type,
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "timeout": 30,
                "models": [
                    {
                        "id": "md_1",
                        "model": "test-model",
                        "display_name": "Test Model",
                        "context_size": context_size,
                        "max_output_tokens": max_output_tokens,
                        "capabilities": ["chat"],
                        "supported_params": supported_params,
                        "params": params,
                    }
                ],
            }
        ],
        "text_api_profiles": {
            "normal": {
                "provider_id": "pv_1",
                "model_id": "md_1",
                "instructions": "你是系统提示",
                "summary_instructions": "概要提示",
                "modify_instructions": "修改提示",
                "auto_continue_on_incomplete": auto_continue,
                "auto_continue_max_rounds": max_rounds,
            },
            "nsfw": {"provider_id": "pv_1", "model_id": "md_1"},
        },
        "text_api_active_profile": "normal",
    }


class LLMClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queue = TaskQueue(os.path.join(self.tmp.name, "queue"))
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _make_client(self, responses, **config_kwargs):
        fake = FakeOpenAI(responses, self.calls)
        patcher = patch("core.llm_client.OpenAI", return_value=fake)
        self.addCleanup(patcher.stop)
        patcher.start()
        client = LLMClient(make_config(**config_kwargs), task_queue=self.queue)
        # 避免写入真实的 conf/token_calibration.json
        client._save_calibration = lambda: None
        return client

    # -- 参数 ---------------------------------------------------------
    def test_params_come_from_model_config(self):
        client = self._make_client([FakeResponse("结果")])
        client.generate_text("写点东西")
        kwargs = self.calls[0]
        self.assertEqual(kwargs["temperature"], 0.4)
        self.assertEqual(kwargs["top_p"], 0.9)
        self.assertEqual(kwargs["max_tokens"], 4000)
        self.assertEqual(kwargs["model"], "test-model")

    def test_unsupported_params_are_not_sent(self):
        client = self._make_client(
            [FakeResponse("结果")],
            supported_params=["max_tokens"],
            params={"temperature": 0.9, "max_tokens": 1000},
        )
        client.generate_text("hi")
        kwargs = self.calls[0]
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["max_tokens"], 1000)

    def test_reasoning_model_uses_max_completion_tokens_on_openai(self):
        client = self._make_client(
            [FakeResponse("结果")],
            supported_params=["max_tokens"],
            params={"max_tokens": 1000},
        )
        client.runtime.model_key = "o3-mini"
        client.runtime.base_url = "https://api.openai.com/v1"
        client.generate_text("hi")
        kwargs = self.calls[0]
        self.assertIn("max_completion_tokens", kwargs)
        self.assertNotIn("max_tokens", kwargs)

    # -- 预算 ---------------------------------------------------------
    def test_message_payload_has_system_and_user(self):
        client = self._make_client([FakeResponse("结果")])
        client.generate_text("用户提示", context_messages=[{"role": "user", "content": "历史"}])
        messages = self.calls[0]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "你是系统提示")
        self.assertEqual(messages[-1]["content"], "用户提示")
        self.assertEqual(messages[1]["content"], "历史")

    def test_override_system_instruction(self):
        client = self._make_client([FakeResponse("结果")])
        client.generate_text("hi", override_system_instruction="覆盖提示")
        self.assertEqual(self.calls[0]["messages"][0]["content"], "覆盖提示")

    def test_long_prompt_is_trimmed_to_model_budget(self):
        client = self._make_client(
            [FakeResponse("结果")], context_size=4096, max_output_tokens=1024
        )
        huge_prompt = "这是很长的提示词。" * 5000
        client.generate_text(huge_prompt)
        sent = self.calls[0]["messages"][-1]["content"]
        self.assertLess(len(sent), len(huge_prompt))
        self.assertIn("已按预算压缩", sent)

        actions = self.queue.list_actions()
        task = self.queue.get_task(actions[0]["action_id"], self.queue.load_action(actions[0]["action_id"])["tasks"][0]["task_id"])
        self.assertTrue(task["extra"]["prompt_trimmed"])
        self.assertEqual(task["user_prompt"], huge_prompt)
        self.assertEqual(task["context_size"], 4096)

    def test_budget_scales_with_model_context(self):
        small = self._make_client(
            [FakeResponse("结果")], context_size=8192, max_output_tokens=2048
        )
        self.assertEqual(small.text_context_size, 8192)
        self.assertEqual(small.token_budget().input_budget(), 8192 - 2048 - 512)
        self.calls.clear()
        large = self._make_client(
            [FakeResponse("结果")], context_size=1_000_000, max_output_tokens=8192
        )
        self.assertGreater(
            large.token_budget().input_budget(), small.token_budget().input_budget()
        )

    # -- 任务记录 -----------------------------------------------------
    def test_request_is_recorded_as_task(self):
        client = self._make_client([FakeResponse("正文结果")])
        action_id = self.queue.begin_action("generate", "生成正文", node_ids=["n1"])
        client.generate_text(
            "写第一章",
            context_messages=[{"role": "user", "content": "背景"}],
            task_meta={"label": "生成正文", "node_id": "n1", "node_title": "第一章"},
        )
        action = self.queue.load_action(action_id)
        self.assertEqual(len(action["tasks"]), 1)
        task = action["tasks"][0]
        self.assertEqual(task["label"], "生成正文")
        self.assertEqual(task["node_title"], "第一章")
        self.assertEqual(task["status"], TaskStatus.SUCCESS)
        self.assertEqual(task["output_text"], "正文结果")
        self.assertEqual(task["user_prompt"], "写第一章")
        self.assertEqual(task["system_prompt"], "你是系统提示")
        self.assertEqual(task["model"], "test-model")
        self.assertEqual(task["provider_name"], "测试提供商")
        self.assertEqual(task["usage"]["total_tokens"], 15)
        self.assertIsNotNone(task["duration_ms"])

    def test_request_without_active_action_creates_adhoc(self):
        client = self._make_client([FakeResponse("结果")])
        client.generate_text("hi")
        actions = self.queue.list_actions()
        self.assertEqual(actions[0]["action_type"], "adhoc")
        action = self.queue.load_action(actions[0]["action_id"])
        self.assertEqual(action["tasks"][0]["status"], TaskStatus.SUCCESS)

    def test_batch_action_groups_seven_requests(self):
        """“重写第一章下 7 个小节” = 1 个动作 + 7 个原子任务。"""
        responses = [FakeResponse(f"第{i}节结果") for i in range(1, 8)]
        client = self._make_client(responses)
        action_id = self.queue.begin_action(
            "batch_rewrite",
            "重写第一章全部小节",
            node_ids=[f"n{i}" for i in range(1, 8)],
        )
        for i in range(1, 8):
            client.generate_text(
                f"重写第{i}节",
                task_meta={
                    "label": "重写正文",
                    "node_id": f"n{i}",
                    "node_title": f"第{i}节",
                },
            )
        self.queue.finish_action(action_id)

        action = self.queue.load_action(action_id)
        self.assertEqual(len(action["tasks"]), 7)
        self.assertEqual(action["status"], TaskStatus.SUCCESS)
        self.assertEqual(action["node_ids"], [f"n{i}" for i in range(1, 8)])
        # 每个任务都能追溯自己的输入与输出
        for index, task in enumerate(action["tasks"], start=1):
            self.assertEqual(task["user_prompt"], f"重写第{index}节")
            self.assertEqual(task["output_text"], f"第{index}节结果")
            self.assertEqual(task["node_title"], f"第{index}节")

        # 重新打开队列（模拟重启）仍能读到全部任务
        reopened = TaskQueue(self.queue.root)
        reloaded = reopened.load_action(action_id)
        self.assertEqual(len(reloaded["tasks"]), 7)
        self.assertEqual(reloaded["tasks"][-1]["output_text"], "第7节结果")

    def test_failed_request_records_failure(self):
        client = self._make_client([RuntimeError("网络炸了")])
        output = client.generate_text("hi")
        self.assertIn("生成失败", output)
        actions = self.queue.list_actions()
        action = self.queue.load_action(actions[0]["action_id"])
        task = action["tasks"][0]
        self.assertEqual(task["status"], TaskStatus.FAILED)
        self.assertIn("网络炸了", task["error"])

    # -- 续写 ---------------------------------------------------------
    def test_auto_continuation_merges_parts(self):
        client = self._make_client(
            [
                FakeResponse("前半段", finish_reason="length"),
                FakeResponse("后半段", finish_reason="stop"),
            ]
        )
        result = client.generate_text("写长文")
        self.assertEqual(result, "前半段后半段")
        self.assertEqual(len(self.calls), 2)
        actions = self.queue.list_actions()
        task = self.queue.load_action(actions[0]["action_id"])["tasks"][0]
        self.assertEqual(len(task["attempts"]), 1)
        self.assertEqual(task["attempts"][0]["reason"], "finish_reason=length")

    def test_auto_continuation_disabled(self):
        client = self._make_client(
            [FakeResponse("前半段", finish_reason="length")], auto_continue=False
        )
        self.assertEqual(client.generate_text("写长文"), "前半段")
        self.assertEqual(len(self.calls), 1)

    def test_max_rounds_limits_continuation(self):
        responses = [FakeResponse(f"第{i}段", finish_reason="length") for i in range(5)]
        client = self._make_client(responses, max_rounds=2)
        client.generate_text("写长文")
        self.assertEqual(len(self.calls), 3)  # 首次 + 2 次续写

    # -- 校准 ---------------------------------------------------------
    def test_usage_calibrates_estimator(self):
        body = "中文内容" * 500
        client = self._make_client(
            [FakeResponse("ok", usage=FakeUsage(prompt_tokens=20000, completion_tokens=10))]
        )
        before = client._estimator.factor_for("test-model")
        client.generate_text(body)
        after = client._estimator.factor_for("test-model")
        self.assertGreater(after, before)

    # -- 未配置 -------------------------------------------------------
    def test_no_runtime_returns_error_message(self):
        client = LLMClient({"providers": []}, task_queue=self.queue)
        output = client.generate_text("hi")
        self.assertIn("生成失败", output)
        self.assertIn("模型服务", output)
        self.assertEqual(self.queue.list_actions(), [])

    def test_runtime_model_values(self):
        runtime = RuntimeModel(
            profile_key="normal",
            provider_id="p",
            provider_name="P",
            api_type="openai",
            base_url="https://x",
            api_key="k",
            timeout=10,
            model_id="m",
            model_key="gpt-4o",
            display_name="GPT-4o",
            context_size=128000,
            max_output_tokens=4096,
            supported_params=["temperature"],
            params={"temperature": 0.2},
        )
        self.assertIn("GPT-4o", runtime.label())
        self.assertEqual(runtime.token_budget().max_output_tokens, 4096)


if __name__ == "__main__":
    unittest.main()
