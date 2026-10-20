"""Agent 控制台界面测试：模型选择、计划表、节点写回、收尾。"""
from __future__ import annotations

import copy

import pytest
from PyQt6.QtCore import Qt

from core.agent_executor import RunResult, StepResult
from core.agent_planner import normalize_plan
from core.task_queue import TaskQueue, TaskStatus
from ui.agent_console_dialog import AgentConsoleDialog

FIXTURE_CONFIG = {
    "proxy": {"enabled": False, "url": "http://127.0.0.1:7897"},
    "providers": [
        {
            "id": "pv_test",
            "name": "测试提供商",
            "type": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test",
            "timeout": 30,
            "models": [
                {
                    "id": "md_test",
                    "model": "test-model",
                    "display_name": "Test Model",
                    "context_size": 100000,
                    "max_output_tokens": 4000,
                    "capabilities": ["chat"],
                    "supported_params": ["temperature", "top_p", "max_tokens"],
                    "params": {"temperature": 0.7},
                },
                {
                    "id": "md_other",
                    "model": "other-model",
                    "display_name": "Other Model",
                    "context_size": 32000,
                    "max_output_tokens": 2000,
                    "capabilities": ["chat"],
                    "supported_params": ["temperature"],
                    "params": {"temperature": 0.5},
                },
            ],
        }
    ],
    "text_api_profiles": {
        "normal": {
            "provider_id": "pv_test",
            "model_id": "md_test",
            "instructions": "写作",
            "summary_instructions": "概要",
            "modify_instructions": "修改",
        },
        "nsfw": {"provider_id": "pv_test", "model_id": "md_test"},
    },
    "text_api_active_profile": "normal",
    "task_completion_notification_enabled": False,
    "recent_api_configs": [],
}

TREE = {
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
                            "title": "场景1",
                            "file_path": "p1.md",
                            "md5": "m1",
                            "_status": "ok",
                            "summary": "旧概要",
                        },
                        {
                            "id": "p2",
                            "title": "场景2",
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


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    import core.task_queue as task_queue_module
    import ui.mixins.config_mixin as config_mixin_module
    from ui.mixins.config_mixin import ConfigMixin
    from ui.main_window import NovelCreatorWindow

    queue = TaskQueue(str(tmp_path / "queue"))
    monkeypatch.setattr(
        config_mixin_module, "load_raw_config", lambda path=None: copy.deepcopy(FIXTURE_CONFIG)
    )
    monkeypatch.setattr(config_mixin_module, "save_raw_config", lambda cfg, path=None: True)
    monkeypatch.setattr(
        ConfigMixin, "_load_sys_state", lambda self: {"recent_workspaces": []}
    )
    monkeypatch.setattr(ConfigMixin, "_write_sys_state", lambda self, state: None)
    monkeypatch.setattr(task_queue_module, "get_task_queue", lambda: queue)
    monkeypatch.setattr(task_queue_module, "configure_task_queue", lambda root=None: queue)

    win = NovelCreatorWindow()
    win.show()
    qapp.processEvents()
    # 伪造一个工作区，避免依赖真实目录
    from core.workspace_manager import WorkspaceManager

    workspace_path = tmp_path / "novel"
    workspace_path.mkdir()
    win.workspace = WorkspaceManager(str(workspace_path))
    win.workspace.init_workspace()
    from core.file_tracker import FileTracker

    win.file_tracker = FileTracker()
    win.outline_tree_data = copy.deepcopy(TREE)
    win.node_map = {}
    win.workspace.save_outline_tree(copy.deepcopy(TREE), win.file_tracker)
    win._task_queue().set_root(str(tmp_path / "queue"))
    yield win, queue
    win.close()
    win.deleteLater()
    qapp.processEvents()
    import gc

    gc.collect()


@pytest.fixture
def dialog(qapp, window):
    win, queue = window
    dlg = AgentConsoleDialog(win)
    dlg.show()
    qapp.processEvents()
    yield dlg
    if dlg._run_thread is not None:
        dlg._run_thread.request_stop()
    dlg.close()


def test_dialog_lists_follow_current_and_all_models(dialog, window):
    win, _queue = window
    labels = [dialog.model_combo.itemText(i) for i in range(dialog.model_combo.count())]
    assert any("跟随当前模型" in label for label in labels)
    assert any("测试提供商" in label and "Test Model" in label for label in labels)
    assert any("Other Model" in label for label in labels)


def test_client_for_selected_model(window):
    win, _queue = window
    client = win._create_llm_client_for_model("pv_test", "md_other")
    assert client is not None
    assert client.runtime.model_key == "other-model"
    # 不改变主客户端
    assert win.llm_client.runtime.model_key == "test-model"


def _sample_plan():
    plan, errors = normalize_plan(
        {
            "goal": "补完缺失场景",
            "steps": [
                {
                    "action": "generate",
                    "scope": {"type": "missing"},
                    "params": {"word_count": 1200},
                },
                {"action": "summary", "nodes": ["p1"]},
            ],
        },
        TREE,
    )
    assert errors == []
    return plan


def test_plan_table_and_checked_steps(dialog):
    plan = _sample_plan()
    dialog._populate_plan_table(plan)
    assert dialog.plan_table.rowCount() == 2
    assert dialog.plan_table.item(0, 2).text() == "生成正文"
    assert "1 个场景" in dialog.plan_table.item(0, 3).text()
    assert dialog.plan_table.item(0, 6).text() == "待执行"
    steps = dialog._steps_from_table()
    assert len(steps) == 2

    # 取消勾选第二步
    dialog.plan_table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    assert len(dialog._steps_from_table()) == 1


def test_apply_agent_node_update_content(dialog, window):
    win, _queue = window
    win.outline_tree_data = copy.deepcopy(TREE)
    update = {
        "node_id": "p2",
        "kind": "content",
        "content": "新的正文",
        "file_path": "p2.md",
        "md5": "newmd5",
    }
    win.apply_agent_node_update(update)
    node = None
    for chapter in win.outline_tree_data["nodes"]:
        for section in chapter["children"]:
            for scene in section["children"]:
                if scene["id"] == "p2":
                    node = scene
    assert node is not None
    assert node["md5"] == "newmd5"
    assert node["_status"] == "ok"


def test_apply_agent_node_update_summary(dialog, window):
    win, _queue = window
    win.outline_tree_data = copy.deepcopy(TREE)
    win.apply_agent_node_update(
        {"node_id": "p1", "kind": "summary", "summary": "新概要"}
    )
    scene = win.outline_tree_data["nodes"][0]["children"][0]["children"][0]
    assert scene["summary"] == "新概要"


def test_finish_agent_run_records_steps(dialog, window):
    win, queue = window
    win.outline_tree_data = copy.deepcopy(TREE)
    action_id = queue.begin_action("agent_run", "Agent 执行：测试")
    task_id = queue.create_task("生成正文", action_id=action_id, user_prompt="x")
    queue.start_task(task_id)
    queue.complete_task(task_id, "生成结果")
    win._active_task_action_id = action_id

    result = RunResult(
        success=True,
        steps=[
            StepResult(
                index=1,
                action="generate",
                title="生成正文",
                status="success",
                total_nodes=1,
                ok_nodes=1,
                message="1 个场景完成",
            )
        ],
        updates=[
            {"node_id": "p2", "kind": "content", "content": "x", "file_path": "p2.md", "md5": "m"}
        ],
    )
    win.finish_agent_run(action_id, result)
    action = queue.load_action(action_id)
    assert action["status"] == TaskStatus.SUCCESS
    assert action["meta"]["success"] is True
    assert len(action["meta"]["steps"]) == 1
    assert win._active_task_action_id is None


def test_finish_agent_run_cancelled(dialog, window):
    win, queue = window
    action_id = queue.begin_action("agent_run", "Agent 执行：取消")
    win._active_task_action_id = action_id
    result = RunResult(success=False, cancelled=True, error="用户已停止执行")
    win.finish_agent_run(action_id, result)
    assert queue.load_action(action_id)["status"] == TaskStatus.CANCELLED


def test_open_agent_console_reuses_dialog(window, monkeypatch):
    win, _queue = window
    win.open_agent_console()
    first = win._agent_dialog
    assert first is not None
    win.open_agent_console()
    assert win._agent_dialog is first
    first.close()


def test_agent_menu_action_exists(window):
    win, _queue = window
    texts = [action.text() for action in win.menus["工具"].actions() if action.text()]
    assert any("Agent 控制台" in text for text in texts)
    assert win.action_agent_console.shortcut().toString() == "Ctrl+Shift+A"


# ---------------------------------------------------------------------------
# 端到端（假模型）：规划 → 表格 → 执行 → 写回
# ---------------------------------------------------------------------------

class _FakeRuntime:
    context_size = 100000
    capabilities = ["chat"]
    model_key = "fake-model"
    display_name = "Fake Model"


class _FakeRunClient:
    def __init__(self, queue=None):
        self.queue = queue
        self.runtime = _FakeRuntime()
        self.profile = {
            "world_context_compression_profile": "balanced",
            "children_summary_compress_trigger_ratio": 0.27,
            "children_summary_group_budget_ratio": 0.24,
        }
        self.system_instruction = "写作"
        self.summary_system_instruction = "概要"
        self.modify_system_instruction = "修改"
        self.calls = []

    def generate_text(
        self,
        prompt,
        override_system_instruction=None,
        progress_callback=None,
        task_meta=None,
    ):
        meta = task_meta or {}
        self.calls.append(meta)
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
            )
            self.queue.start_task(task_id)
        text = "# 场景2\n魔兽冲了出来。"
        if self.queue is not None and task_id:
            self.queue.complete_task(task_id, text)
        return text


class _FakePlanner:
    def __init__(self, *args, **kwargs):
        pass

    def plan(self, command, tree_data, **kwargs):
        plan, errors = normalize_plan(
            {
                "goal": "补完缺失场景",
                "steps": [
                    {
                        "action": "generate",
                        "scope": {"type": "missing"},
                        "params": {"word_count": 800, "include_next": False},
                    }
                ],
            },
            tree_data,
            checked_ids=kwargs.get("checked_ids") or [],
        )
        assert errors == []
        from core.agent_planner import PlanResult

        return PlanResult(ok=True, plan=plan, errors=[], raw_text="{}")


def _wait_thread(qapp, thread, timeout=5000):
    thread.wait(timeout)
    for _ in range(100):
        qapp.processEvents()
        if thread.isFinished():
            break


def test_end_to_end_plan_and_run(qapp, monkeypatch, dialog, window):
    win, queue = window
    from PyQt6.QtWidgets import QMessageBox

    import ui.agent_console_dialog as dialog_module

    monkeypatch.setattr(dialog_module, "AgentPlanner", _FakePlanner)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    client = _FakeRunClient(queue)
    monkeypatch.setattr(dialog, "_client", lambda: client)

    dialog.command_input.setPlainText("把缺失的场景补完")
    dialog._start_plan()
    assert dialog._plan_thread is not None
    _wait_thread(qapp, dialog._plan_thread)

    assert dialog.plan_table.rowCount() == 1
    assert dialog.btn_run.isEnabled()
    assert "补完缺失场景" in dialog.goal_label.text()

    dialog._start_run()
    assert dialog._run_thread is not None
    _wait_thread(qapp, dialog._run_thread)
    qapp.processEvents()

    # 文件已写入
    import os

    full = os.path.join(win.workspace.text_path, "p2.md")
    assert os.path.exists(full)
    with open(full, "r", encoding="utf-8") as handle:
        assert "魔兽冲了出来" in handle.read()

    # 主窗口大纲已写回 md5
    scene = win.outline_tree_data["nodes"][0]["children"][0]["children"][1]
    assert scene["md5"]
    assert scene["_status"] == "ok"

    # 计划状态与任务队列
    assert dialog.plan_table.item(0, 6).text() == "成功"
    actions = queue.list_actions()
    run_actions = [a for a in actions if a["action_type"] == "agent_run"]
    assert run_actions
    finished = queue.load_action(run_actions[0]["action_id"])
    assert finished["status"] == TaskStatus.SUCCESS
    assert finished["tasks"][0]["node_title"] == "场景2"
    # 规划动作也应留下记录
    assert any(a["action_type"] == "agent_plan" for a in actions)
