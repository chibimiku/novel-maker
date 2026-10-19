"""任务队列窗口：按“动作 → 原子任务”查看每一次大模型请求的输入与输出。

左侧是动作树：一个动作（例如“重写第一章全部小节”）下挂着若干个原子任务
（每个任务 = 一次真实的后端请求）；右侧展示选中任务的完整输入、输出、参数、
token 预算与错误信息。任务数据已由 :mod:`core.task_queue` 落盘，这里只做展示。
"""
from __future__ import annotations

import json
import os
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QUrl

from core.task_queue import TaskQueue, TaskStatus, STATUS_LABELS, get_task_queue

STATUS_COLORS = {
    TaskStatus.SUCCESS: "#7bd88f",
    TaskStatus.FAILED: "#ff7b72",
    TaskStatus.RUNNING: "#ffb74d",
    TaskStatus.PENDING: "#9aa0a6",
    TaskStatus.CANCELLED: "#c792ea",
    TaskStatus.INTERRUPTED: "#ff9e64",
}

ACTION_TYPE_LABELS = {
    "generate": "生成正文",
    "rewrite": "重写正文",
    "summary": "生成概要",
    "batch_generate": "批量生成",
    "batch_rewrite": "批量重写",
    "batch_modify": "批量修改",
    "batch_summary": "批量概要",
    "setting_select": "设定筛选",
    "world_build": "世界观生成",
    "outline": "大纲生成",
    "index": "索引生成",
    "import": "文本导入",
    "proofread": "小说校对",
    "adhoc": "临时请求",
}


def _format_duration(ms: Any) -> str:
    if not isinstance(ms, (int, float)) or ms < 0:
        return "-"
    if ms < 1000:
        return f"{int(ms)} ms"
    return f"{ms / 1000:.1f} s"


def _format_ts(value: str) -> str:
    text = str(value or "")
    if "T" in text:
        date_part, _, time_part = text.partition("T")
        time_part = time_part[:8]
        return f"{date_part} {time_part}"
    return text or "-"


def _pretty_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


class TaskQueueDialog(QDialog):
    def __init__(self, parent=None, queue: TaskQueue | None = None):
        super().__init__(parent)
        self.setWindowTitle("任务队列：动作 / 原子任务")
        self.resize(1180, 780)
        self.queue = queue or get_task_queue()
        self._selected_task: dict | None = None
        self._selected_action_id: str = ""
        self._expanded_actions: set[str] = set()
        self._auto_refresh = True
        self._last_signature: tuple = ()

        self._build_ui()
        self.refresh()

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("筛选:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部", "all")
        self.filter_combo.addItem("进行中", TaskStatus.RUNNING)
        self.filter_combo.addItem("失败", TaskStatus.FAILED)
        self.filter_combo.addItem("成功", TaskStatus.SUCCESS)
        self.filter_combo.addItem("已中断", TaskStatus.INTERRUPTED)
        self.filter_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        toolbar.addWidget(self.filter_combo)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索动作标题 / 节点 / 模型 / 标签")
        self.search_input.textChanged.connect(lambda _t: self.refresh())
        toolbar.addWidget(self.search_input, stretch=1)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: gray;")
        toolbar.addWidget(self.stats_label)

        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.clicked.connect(lambda: self.refresh())
        toolbar.addWidget(self.btn_refresh)

        self.btn_open_dir = QPushButton("📁 打开目录")
        self.btn_open_dir.clicked.connect(self._open_directory)
        toolbar.addWidget(self.btn_open_dir)

        self.btn_clear = QPushButton("🧹 清理已结束")
        self.btn_clear.clicked.connect(self._clear_finished)
        toolbar.addWidget(self.btn_clear)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["动作 / 任务", "状态", "模型", "耗时"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        splitter.addWidget(self.tree)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel("请选择左侧的动作或任务")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        detail_layout.addWidget(self.title_label)

        self.meta_label = QLabel("")
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.meta_label.setStyleSheet("color: #9aa0a6;")
        detail_layout.addWidget(self.meta_label)

        self.tabs = QTabWidget()
        self.input_tab = QWidget()
        input_layout = QVBoxLayout(self.input_tab)
        input_layout.setContentsMargins(0, 4, 0, 0)
        input_layout.addWidget(QLabel("System 指令:"))
        self.system_view = self._make_text_view()
        input_layout.addWidget(self.system_view, stretch=1)
        input_layout.addWidget(QLabel("用户提示词（原始输入）:"))
        self.prompt_view = self._make_text_view()
        input_layout.addWidget(self.prompt_view, stretch=2)
        input_layout.addWidget(QLabel("上下文消息:"))
        self.context_view = self._make_text_view()
        input_layout.addWidget(self.context_view, stretch=1)
        self.tabs.addTab(self.input_tab, "输入")

        self.output_view = self._make_text_view()
        self.tabs.addTab(self.output_view, "输出")

        self.params_view = self._make_text_view()
        self.tabs.addTab(self.params_view, "参数 / 预算")

        self.error_view = self._make_text_view()
        self.tabs.addTab(self.error_view, "错误")

        self.raw_view = self._make_text_view()
        self.tabs.addTab(self.raw_view, "原始 JSON")

        detail_layout.addWidget(self.tabs, stretch=1)

        action_bar = QHBoxLayout()
        self.auto_refresh_check = QPushButton("⏱ 自动刷新：开")
        self.auto_refresh_check.setCheckable(True)
        self.auto_refresh_check.setChecked(True)
        self.auto_refresh_check.toggled.connect(self._on_auto_refresh_toggled)
        action_bar.addWidget(self.auto_refresh_check)
        action_bar.addStretch()
        self.btn_export = QPushButton("📤 导出所选动作")
        self.btn_export.clicked.connect(self._export_selected_action)
        action_bar.addWidget(self.btn_export)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        action_bar.addWidget(self.btn_close)
        detail_layout.addLayout(action_bar)

        splitter.addWidget(detail_panel)
        splitter.setSizes([520, 660])
        self.detail_panel = detail_panel
        self._update_stats()

    @staticmethod
    def _make_text_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        view.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;")
        view.setPlaceholderText("（空）")
        return view

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def refresh(self, preserve: bool = True):
        tree_state = self._capture_state() if preserve else {}
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            actions = self.queue.list_actions()
            filter_key = self.filter_combo.currentData()
            keyword = self.search_input.text().strip().lower()
            shown = 0
            for action in actions:
                if not self._action_matches(action, filter_key, keyword):
                    continue
                shown += 1
                self._add_action_item(action)
        finally:
            self.tree.blockSignals(False)
        if shown == 0:
            self._show_empty_detail()
        self._update_stats()
        self._last_signature = self._data_signature()

        if preserve and tree_state.get("action_id"):
            self._restore_selection(tree_state)

    def _action_matches(self, action: dict, filter_key: str, keyword: str) -> bool:
        counts = action.get("counts") or {}
        status = action.get("status")
        if filter_key == TaskStatus.RUNNING:
            if not (counts.get(TaskStatus.RUNNING) or status == TaskStatus.RUNNING):
                return False
        elif filter_key == TaskStatus.FAILED:
            if not counts.get(TaskStatus.FAILED):
                return False
        elif filter_key == TaskStatus.SUCCESS:
            if not counts.get(TaskStatus.SUCCESS):
                return False
        elif filter_key == TaskStatus.INTERRUPTED:
            if status != TaskStatus.INTERRUPTED and not counts.get(TaskStatus.INTERRUPTED):
                return False
        if not keyword:
            return True
        haystack = " ".join(
            [
                str(action.get("title", "")),
                str(action.get("description", "")),
                str(action.get("action_type", "")),
                " ".join(str(item) for item in action.get("models", [])),
                " ".join(str(item) for item in action.get("node_ids", [])),
            ]
        ).lower()
        return keyword in haystack

    def _add_action_item(self, action: dict):
        action_id = str(action.get("action_id", ""))
        counts = action.get("counts") or {}
        total = int(action.get("task_count") or 0)
        ok = int(counts.get(TaskStatus.SUCCESS, 0))
        failed = int(counts.get(TaskStatus.FAILED, 0))
        running = int(counts.get(TaskStatus.RUNNING, 0))
        if running:
            progress = f" ({ok}/{total} 完成)"
        elif total:
            progress = f" ({ok}/{total} 成功" + (f", {failed} 失败)" if failed else ")")
        else:
            progress = ""
        type_label = ACTION_TYPE_LABELS.get(
            str(action.get("action_type", "")), str(action.get("action_type", ""))
        )
        title = f"[{type_label}] {action.get('title', '')}{progress}"
        item = QTreeWidgetItem(
            [
                title,
                STATUS_LABELS.get(str(action.get("status")), str(action.get("status", ""))),
                ", ".join(action.get("models", [])[:2]),
                _format_ts(action.get("created_at", "")),
            ]
        )
        item.setData(0, Qt.ItemDataRole.UserRole, ("action", action_id, ""))
        item.setToolTip(0, action.get("description", "") or action.get("title", ""))
        color = STATUS_COLORS.get(str(action.get("status")))
        if color:
            item.setForeground(1, QColor(color))
        self.tree.addTopLevelItem(item)

        if total:
            placeholder = QTreeWidgetItem([f"加载 {total} 个任务…", "", "", ""])
            placeholder.setData(0, Qt.ItemDataRole.UserRole, ("placeholder", action_id, ""))
            item.addChild(placeholder)
        item.setExpanded(action_id in self._expanded_actions)
        if action_id in self._expanded_actions:
            self._load_tasks(item, action_id)

    def _on_item_expanded(self, item: QTreeWidgetItem):
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            return
        kind, action_id, _task_id = payload
        if kind != "action":
            return
        self._expanded_actions.add(action_id)
        self._load_tasks(item, action_id)

    def _load_tasks(self, action_item: QTreeWidgetItem, action_id: str):
        if action_item.childCount() != 1:
            return
        first = action_item.child(0)
        payload = first.data(0, Qt.ItemDataRole.UserRole)
        if not payload or payload[0] != "placeholder":
            return
        action_item.takeChild(0)
        action = self.queue.load_action(action_id)
        if not action:
            return
        for task in action.get("tasks", []):
            label = str(task.get("label", "任务"))
            node_title = str(task.get("node_title", ""))
            title = f"{task.get('seq', '')}. {label}"
            if node_title:
                title += f" · {node_title}"
            usage = task.get("usage") or {}
            token_text = ""
            if usage.get("total_tokens"):
                token_text = f"{usage['total_tokens']} tok"
            elif task.get("estimated_output_tokens"):
                token_text = f"≈{task['estimated_output_tokens']} tok"
            child = QTreeWidgetItem(
                [
                    title,
                    STATUS_LABELS.get(str(task.get("status")), str(task.get("status", ""))),
                    str(task.get("model", "")),
                    _format_duration(task.get("duration_ms")),
                ]
            )
            child.setData(
                0, Qt.ItemDataRole.UserRole, ("task", action_id, str(task.get("task_id", "")))
            )
            child.setToolTip(0, f"{title}\n{token_text}")
            color = STATUS_COLORS.get(str(task.get("status")))
            if color:
                child.setForeground(1, QColor(color))
            action_item.addChild(child)

    # ------------------------------------------------------------------
    # 选择 / 详情
    # ------------------------------------------------------------------
    def _on_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            self._show_empty_detail()
            return
        payload = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            return
        kind, action_id, task_id = payload
        if kind == "task":
            self._show_task(action_id, task_id)
        elif kind == "action":
            self._show_action(action_id)

    def _show_empty_detail(self):
        self._selected_task = None
        self.title_label.setText("请选择左侧的动作或任务")
        self.meta_label.setText("")
        for view in (
            self.system_view,
            self.prompt_view,
            self.context_view,
            self.output_view,
            self.params_view,
            self.error_view,
            self.raw_view,
        ):
            view.clear()

    def _show_action(self, action_id: str):
        action = self.queue.load_action(action_id)
        if not action:
            self._show_empty_detail()
            return
        self._selected_action_id = action_id
        self._selected_task = None
        counts = {}
        for task in action.get("tasks", []):
            status = str(task.get("status"))
            counts[status] = counts.get(status, 0) + 1
        count_text = "，".join(
            f"{STATUS_LABELS.get(status, status)} {value}"
            for status, value in counts.items()
        ) or "无任务"
        self.title_label.setText(
            f"动作：{action.get('title', '')}"
        )
        self.meta_label.setText(
            f"类型：{ACTION_TYPE_LABELS.get(str(action.get('action_type')), action.get('action_type'))} · "
            f"状态：{STATUS_LABELS.get(str(action.get('status')), action.get('status'))} · "
            f"任务：{len(action.get('tasks', []))} 个（{count_text}）<br>"
            f"创建：{_format_ts(action.get('created_at'))} · "
            f"结束：{_format_ts(action.get('finished_at'))} · "
            f"模型：{', '.join(action.get('models', [])) or '-'}<br>"
            f"动作 ID：{action_id}"
        )
        self.system_view.clear()
        self.prompt_view.setPlainText(str(action.get("description", "")))
        self.context_view.setPlainText(
            _pretty_json(
                {
                    "node_ids": action.get("node_ids", []),
                    "meta": action.get("meta", {}),
                }
            )
        )
        self.output_view.setPlainText(
            "\n".join(
                f"#{task.get('seq')} {task.get('label')} "
                f"[{STATUS_LABELS.get(str(task.get('status')), task.get('status'))}]"
                for task in action.get("tasks", [])
            )
        )
        self.params_view.clear()
        self.error_view.setPlainText(
            "\n\n".join(
                f"#{task.get('seq')} {task.get('label')}:\n{task.get('error')}"
                for task in action.get("tasks", [])
                if task.get("error")
            )
        )
        self.raw_view.setPlainText(_pretty_json(action))

    def _show_task(self, action_id: str, task_id: str):
        task = self.queue.get_task(action_id, task_id)
        if not task:
            self._show_empty_detail()
            return
        self._selected_action_id = action_id
        self._selected_task = task
        status = str(task.get("status"))
        usage = task.get("usage") or {}
        token_text = (
            f"输入 {usage.get('prompt_tokens', 0):,} / 输出 {usage.get('completion_tokens', 0):,} tok"
            if usage
            else f"估算输入 {int(task.get('estimated_input_tokens') or 0):,} tok，"
            f"估算输出 {int(task.get('estimated_output_tokens') or 0):,} tok"
        )
        self.title_label.setText(
            f"任务 #{task.get('seq')}：{task.get('label', '')}"
            + (f" · {task.get('node_title')}" if task.get("node_title") else "")
        )
        params_text = ", ".join(f"{k}={v}" for k, v in (task.get("params") or {}).items()) or "-"
        self.meta_label.setText(
            f"状态：{STATUS_LABELS.get(status, status)} · "
            f"模型：{task.get('model', '')} @ {task.get('provider_name', '')} · "
            f"参数：{params_text}<br>"
            f"耗时：{_format_duration(task.get('duration_ms'))} · {token_text} · "
            f"窗口 {int(task.get('context_size') or 0):,} / 输出上限 "
            f"{int(task.get('max_output_tokens') or 0):,} / 输入预算 "
            f"{int(task.get('input_budget_tokens') or 0):,}<br>"
            f"创建：{_format_ts(task.get('created_at'))} · "
            f"开始：{_format_ts(task.get('started_at'))} · "
            f"结束：{_format_ts(task.get('finished_at'))}<br>"
            f"任务 ID：{task_id}"
        )
        extra = task.get("extra") or {}
        self.system_view.setPlainText(str(task.get("system_prompt", "")))
        self.prompt_view.setPlainText(str(task.get("user_prompt", "")))
        context_text = _pretty_json(task.get("context_messages", []))
        if extra.get("sent_messages"):
            context_text += "\n\n===== 实际发送（裁剪后） =====\n" + _pretty_json(
                extra.get("sent_messages")
            )
        self.context_view.setPlainText(context_text)
        self.output_view.setPlainText(str(task.get("output_text", "")))
        self.params_view.setPlainText(
            _pretty_json(
                {
                    "params": task.get("params", {}),
                    "context_size": task.get("context_size"),
                    "max_output_tokens": task.get("max_output_tokens"),
                    "input_budget_tokens": task.get("input_budget_tokens"),
                    "estimated_input_tokens": task.get("estimated_input_tokens"),
                    "usage": usage,
                    "attempts": task.get("attempts", []),
                    "extra": extra,
                }
            )
        )
        self.error_view.setPlainText(str(task.get("error", "")))
        self.raw_view.setPlainText(_pretty_json(task))

    # ------------------------------------------------------------------
    # 状态保持
    # ------------------------------------------------------------------
    def _capture_state(self) -> dict:
        selected = self.tree.selectedItems()
        state = {"action_id": "", "kind": "", "task_id": ""}
        if selected:
            payload = selected[0].data(0, Qt.ItemDataRole.UserRole)
            if payload:
                state["kind"], state["action_id"], state["task_id"] = payload
        return state

    def _restore_selection(self, state: dict):
        action_id = state.get("action_id") or ""
        if not action_id:
            return
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            payload = item.data(0, Qt.ItemDataRole.UserRole)
            if not payload or payload[1] != action_id:
                continue
            if state.get("kind") == "task" and state.get("task_id"):
                if action_id not in self._expanded_actions:
                    self._expanded_actions.add(action_id)
                    item.setExpanded(True)
                self._load_tasks(item, action_id)
                for child_index in range(item.childCount()):
                    child = item.child(child_index)
                    child_payload = child.data(0, Qt.ItemDataRole.UserRole)
                    if child_payload and child_payload[2] == state["task_id"]:
                        self.tree.setCurrentItem(child)
                        self._show_task(action_id, state["task_id"])
                        return
            self.tree.setCurrentItem(item)
            self._show_action(action_id)
            return

    def _data_signature(self) -> tuple:
        """动作/任务状态快照，用于判断自动刷新是否需要重建界面。"""
        actions = self.queue.list_actions()
        return tuple(
            (
                action.get("action_id"),
                action.get("status"),
                action.get("task_count"),
                tuple(sorted((action.get("counts") or {}).items())),
            )
            for action in actions
        )

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _on_timer(self):
        if not self._auto_refresh or not self.isVisible():
            return
        try:
            # 数据没变化时只更新统计，避免打断用户正在查看的任务详情
            if self._data_signature() == self._last_signature:
                self._update_stats()
                return
            self.refresh()
        except Exception:
            pass

    def _on_auto_refresh_toggled(self, checked: bool):
        self._auto_refresh = checked
        self.auto_refresh_check.setText(
            f"⏱ 自动刷新：{'开' if checked else '关'}"
        )

    def _update_stats(self):
        try:
            stats = self.queue.stats()
        except Exception:
            self.stats_label.setText("")
            return
        self.stats_label.setText(
            f"动作 {stats.get('actions', 0)} · 任务 {stats.get('tasks', 0)} "
            f"（运行 {stats.get('running', 0)} / 失败 {stats.get('failed', 0)}）"
        )

    def _open_directory(self):
        root = self.queue.root
        os.makedirs(root, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(root))

    def _clear_finished(self):
        reply = QMessageBox.question(
            self,
            "清理任务记录",
            "确定删除所有已结束（成功/失败/取消/中断）的动作记录吗？\n运行中的动作会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = self.queue.clear_actions(keep_running=True)
        self.refresh(preserve=False)
        QMessageBox.information(self, "清理完成", f"已删除 {removed} 个动作记录。")

    def _export_selected_action(self):
        item = self.tree.currentItem()
        action_id = ""
        if item is not None:
            payload = item.data(0, Qt.ItemDataRole.UserRole)
            if payload:
                action_id = payload[1]
        if not action_id:
            QMessageBox.information(self, "提示", "请先选择一个动作。")
            return
        default_path = os.path.join(self.queue.root, f"action_{action_id}.json")
        target, _filter = QFileDialog.getSaveFileName(
            self, "导出动作记录", default_path, "JSON (*.json)"
        )
        if not target:
            return
        if self.queue.export_action_json(action_id, target):
            QMessageBox.information(self, "导出成功", f"已导出到：\n{target}")
        else:
            QMessageBox.warning(self, "导出失败", "导出动作记录失败。")


__all__ = ["TaskQueueDialog", "ACTION_TYPE_LABELS", "STATUS_COLORS"]
