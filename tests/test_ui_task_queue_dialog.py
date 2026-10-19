"""任务队列窗口测试：动作树、任务详情、输入输出展示。"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from core.task_queue import TaskQueue
from ui.task_queue_dialog import TaskQueueDialog


@pytest.fixture
def queue(tmp_path):
    q = TaskQueue(str(tmp_path / "queue"))
    action_id = q.begin_action(
        "batch_rewrite",
        "重写第一章全部小节",
        node_ids=["n1", "n2"],
        description="更紧凑",
    )
    for index, node_id in enumerate(("n1", "n2"), start=1):
        task_id = q.create_task(
            "重写正文",
            action_id=action_id,
            node_id=node_id,
            node_title=f"小节{index}",
            provider_name="测试提供商",
            model="test-model",
            context_size=100000,
            max_output_tokens=4000,
            input_budget_tokens=95000,
            estimated_input_tokens=1200,
            system_prompt="系统指令内容",
            user_prompt=f"请重写第{index}节",
            context_messages=[{"role": "user", "content": f"上下文{index}"}],
            params={"temperature": 0.7},
        )
        q.start_task(task_id)
        if index == 1:
            q.complete_task(
                task_id,
                "重写后的正文",
                usage={"prompt_tokens": 1100, "completion_tokens": 900, "total_tokens": 2000},
            )
        else:
            q.fail_task(task_id, "网络错误")
    q.finish_action(action_id)
    return q, action_id


@pytest.fixture
def dialog(qapp, queue):
    q, _action_id = queue
    dlg = TaskQueueDialog(None, queue=q)
    dlg.show()
    qapp.processEvents()
    yield dlg
    dlg._timer.stop()
    dlg.close()


def test_action_tree_loads(dialog, queue):
    _q, action_id = queue
    assert dialog.tree.topLevelItemCount() == 1
    item = dialog.tree.topLevelItem(0)
    assert "重写第一章全部小节" in item.text(0)
    assert item.childCount() == 1  # 懒加载占位

    dialog._expanded_actions.add(action_id)
    dialog.refresh()
    item = dialog.tree.topLevelItem(0)
    assert item.childCount() == 2


def test_task_detail_shows_input_output(dialog, queue):
    q, action_id = queue
    dialog._expanded_actions.add(action_id)
    dialog.refresh()
    item = dialog.tree.topLevelItem(0)
    first_task = item.child(0)
    dialog.tree.setCurrentItem(first_task)
    dialog._on_selection_changed()

    assert "重写正文" in dialog.title_label.text()
    assert dialog.system_view.toPlainText() == "系统指令内容"
    assert dialog.prompt_view.toPlainText() == "请重写第1节"
    assert "上下文1" in dialog.context_view.toPlainText()
    assert dialog.output_view.toPlainText() == "重写后的正文"
    assert "network" not in dialog.error_view.toPlainText()
    assert "temperature" in dialog.params_view.toPlainText()


def test_failed_task_shows_error(dialog, queue):
    q, action_id = queue
    dialog._expanded_actions.add(action_id)
    dialog.refresh()
    item = dialog.tree.topLevelItem(0)
    second = item.child(1)
    dialog.tree.setCurrentItem(second)
    dialog._on_selection_changed()
    assert "网络错误" in dialog.error_view.toPlainText()
    assert "失败" in dialog.meta_label.text()


def test_action_detail(dialog, queue):
    _q, _action_id = queue
    action_item = dialog.tree.topLevelItem(0)
    dialog.tree.setCurrentItem(action_item)
    dialog._on_selection_changed()
    assert "动作：" in dialog.title_label.text()
    assert "更紧凑" in dialog.prompt_view.toPlainText()
    assert "n1" in dialog.context_view.toPlainText()


def test_filter_by_failed(dialog, queue):
    dialog.filter_combo.setCurrentIndex(2)  # 失败
    assert dialog.tree.topLevelItemCount() == 1
    dialog.filter_combo.setCurrentIndex(1)  # 进行中 -> 没有
    assert dialog.tree.topLevelItemCount() == 0


def test_search_filters_actions(dialog, queue):
    dialog.search_input.setText("不存在的关键字")
    assert dialog.tree.topLevelItemCount() == 0
    dialog.search_input.setText("第一章")
    assert dialog.tree.topLevelItemCount() == 1


def test_stats_label(dialog, queue):
    assert "动作 1" in dialog.stats_label.text()
    assert "任务 2" in dialog.stats_label.text()


def test_refresh_keeps_task_selection(dialog, queue):
    q, action_id = queue
    dialog._expanded_actions.add(action_id)
    dialog.refresh()
    item = dialog.tree.topLevelItem(0)
    task_item = item.child(0)
    dialog.tree.setCurrentItem(task_item)
    dialog._on_selection_changed()
    task_id = task_item.data(0, Qt.ItemDataRole.UserRole)[2]

    dialog.refresh()
    selected = dialog.tree.selectedItems()
    assert selected, "刷新后应恢复选中项"
    payload = selected[0].data(0, Qt.ItemDataRole.UserRole)
    assert payload[0] == "task"
    assert payload[2] == task_id


def test_timer_skips_rebuild_when_unchanged(dialog, queue):
    dialog._expanded_actions.add(queue[1])
    dialog.refresh()
    calls = {"n": 0}
    original_refresh = dialog.refresh

    def counting_refresh(*args, **kwargs):
        calls["n"] += 1
        return original_refresh(*args, **kwargs)

    dialog.refresh = counting_refresh
    dialog._on_timer()
    assert calls["n"] == 0  # 数据没变，只更新统计
    q, _action_id = queue
    action = q.load_action(queue[1])
    q.begin_action("generate", "新动作")
    dialog._on_timer()
    assert calls["n"] == 1
