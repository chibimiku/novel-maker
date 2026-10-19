"""主窗口界面级测试（offscreen Qt）：菜单结构、视图切换、动作/任务联动。"""
from __future__ import annotations

import copy

import pytest

from core.task_queue import TaskQueue, TaskStatus

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
                }
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
            "auto_continue_on_incomplete": True,
            "auto_continue_max_rounds": 3,
        },
        "nsfw": {"provider_id": "pv_test", "model_id": "md_test"},
    },
    "text_api_active_profile": "normal",
    "task_completion_notification_enabled": False,
    "recent_api_configs": [],
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
    monkeypatch.setattr(
        config_mixin_module, "save_raw_config", lambda cfg, path=None: True
    )
    monkeypatch.setattr(
        ConfigMixin, "_load_sys_state", lambda self: {"recent_workspaces": []}
    )
    monkeypatch.setattr(ConfigMixin, "_write_sys_state", lambda self, state: None)
    monkeypatch.setattr(task_queue_module, "get_task_queue", lambda: queue)
    monkeypatch.setattr(
        task_queue_module, "configure_task_queue", lambda root=None: queue
    )

    win = NovelCreatorWindow()
    win.show()
    qapp.processEvents()
    yield win, queue
    # 显式销毁 Qt 对象，避免下一个测试构造窗口时 GC 回收已析构的 C++ 对象
    win.close()
    win.deleteLater()
    qapp.processEvents()
    import gc

    gc.collect()


def test_top_level_menus(window):
    win, _queue = window
    menubar = win.menuBar()
    titles = [action.text() for action in menubar.actions()]
    assert titles == ["文件", "编辑", "视图", "生成", "工具", "设置", "帮助"]


def test_file_menu_actions(window):
    win, _queue = window
    texts = [a.text() for a in win.menus["文件"].actions() if a.text()]
    assert "新建工作区" in texts
    assert "保存全部" in texts
    assert "导出" in texts
    assert "退出" in texts


def test_generate_and_tool_menus(window):
    win, _queue = window
    generate_texts = [a.text() for a in win.menus["生成"].actions() if a.text()]
    assert "结合上下文生成正文" in generate_texts
    assert "批量生成缺失场景" in generate_texts
    assert "批量修改勾选节点正文" in generate_texts

    tool_texts = [a.text() for a in win.menus["工具"].actions() if a.text()]
    assert any("任务队列" in text for text in tool_texts)
    assert "小说校对（独立模块）" in tool_texts
    assert "时间线校对" in tool_texts


def test_settings_menu_has_model_service(window):
    win, _queue = window
    texts = [a.text() for a in win.menus["设置"].actions() if a.text()]
    assert any("系统配置" in text for text in texts)
    assert any("模型服务" in text for text in texts)


def test_shortcuts_are_unique(window):
    win, _queue = window
    shortcuts = []
    for menu in win.menus.values():
        for action in menu.actions():
            shortcut = action.shortcut().toString()
            if shortcut:
                shortcuts.append(shortcut)
    duplicates = {item for item in shortcuts if shortcuts.count(item) > 1}
    assert duplicates == set(), f"重复快捷键: {duplicates}"


def test_view_toggle_hides_panels(window):
    win, _queue = window
    assert not win.setting_tree.isHidden()
    win.action_view_settings.setChecked(False)
    assert win.setting_tree.isHidden()
    win.action_view_settings.setChecked(True)
    assert not win.setting_tree.isHidden()

    win.action_view_log.setChecked(False)
    assert win.log_console.isHidden()


def test_reset_layout_restores_panels(window):
    win, _queue = window
    win.setting_tree.setVisible(False)
    win.novel_tree.setVisible(False)
    win.log_console.setVisible(False)
    win.reset_window_layout()
    assert not win.setting_tree.isHidden()
    assert not win.novel_tree.isHidden()
    assert not win.log_console.isHidden()
    sizes = win.main_splitter.sizes()
    # Qt 会按最小宽度重新分配，这里只校验“三栏都还在且右侧最宽”的布局意图
    self_sum = sum(sizes)
    assert self_sum > 0
    assert sizes[-1] == max(sizes)


def test_task_action_lifecycle_updates_status_bar(window):
    win, queue = window

    action_id = win._task_action_begin(
        "generate", "生成正文：测试节点", node_ids=["n1"]
    )
    assert win._active_task_action_id == action_id
    assert queue.list_actions()[0]["status"] == TaskStatus.RUNNING

    task_id = win.llm_client._task_queue_or_none().create_task(
        "生成正文",
        action_id=action_id,
        node_id="n1",
        user_prompt="写一段",
    )
    queue.start_task(task_id)
    queue.complete_task(task_id, "写完了")
    win._task_action_finish()

    assert win._active_task_action_id is None
    action = queue.list_actions()[0]
    assert action["status"] == TaskStatus.SUCCESS
    assert "成功 1" in win.task_status_label.text()


def test_task_meta_contains_active_action(window):
    win, _queue = window
    action_id = win._task_action_begin("rewrite", "重写正文：节点")
    meta = win._task_meta("重写正文", {"id": "n9", "title": "节点九"})
    assert meta["action_id"] == action_id
    assert meta["node_id"] == "n9"
    assert meta["node_title"] == "节点九"
    win._task_action_finish()


def test_llm_client_uses_model_context_window(window):
    win, _queue = window
    assert win.llm_client is not None
    assert win.llm_client.text_context_size == 100000
    assert win.llm_client.token_budget().max_output_tokens == 4000
    assert win.llm_client.runtime.model_key == "test-model"


def test_quick_model_menu_lists_provider_models(window):
    win, _queue = window
    win._populate_quick_model_menu()
    actions = win.menu_quick_model.actions()
    assert any("普通" in action.text() for action in actions)
    submenus = [action.menu() for action in actions if action.menu() is not None]
    assert submenus, "应至少有一个提供商子菜单"
    model_texts = [item.text() for item in submenus[0].actions()]
    assert any("Test Model" in text for text in model_texts)
    assert any("测试提供商" in action.text() for action in actions if action.menu())


def test_switch_active_model_reloads_client(window, monkeypatch):
    win, _queue = window
    saved = {}

    def fake_save(config, path=None):
        saved["config"] = copy.deepcopy(config)
        return True

    monkeypatch.setattr("ui.mixins.config_mixin.save_raw_config", fake_save)
    assert win.switch_active_model("pv_test", "md_test") is True
    profile = saved["config"]["text_api_profiles"]["normal"]
    assert profile["provider_id"] == "pv_test"
    assert profile["model_id"] == "md_test"
    assert win.llm_client is not None
    assert "已切换模型" in "\n".join(win.log_console.toPlainText().splitlines())
