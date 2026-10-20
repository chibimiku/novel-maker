"""Agent 控制台：输入一句命令 → 先规划步骤 → 确认后逐步执行。

界面分三块：
1. 命令与模型选择；
2. 计划表格（每步可勾选启用/停用，可编辑 JSON）；
3. 执行日志与状态。

规划与执行都在后台线程进行，执行过程中的每次模型请求都会落到任务队列。
"""
from __future__ import annotations

import copy
import json
import time
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.agent_executor import AgentRunner
from core.agent_planner import AgentPlanner, describe_plan, normalize_plan, parse_plan_response
from core.task_queue import TaskStatus

STATUS_LABELS = {
    "pending": "待执行",
    "running": "执行中",
    "success": "成功",
    "failed": "失败",
    "skipped": "已跳过",
}
STATUS_COLORS = {
    "running": "#ffb74d",
    "success": "#7bd88f",
    "failed": "#ff7b72",
    "skipped": "#9aa0a6",
}


class AgentPlanThread(QThread):
    success_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(str)

    def __init__(
        self,
        planner: AgentPlanner,
        command: str,
        tree_data: dict,
        checked_paths: list[str],
        checked_ids: list[str],
        action_id: str,
        parent=None,
    ):
        super().__init__(parent)
        self.planner = planner
        self.command = command
        self.tree_data = tree_data
        self.checked_paths = checked_paths
        self.checked_ids = checked_ids
        self.action_id = action_id

    def run(self):
        try:
            result = self.planner.plan(
                self.command,
                self.tree_data,
                checked_paths=self.checked_paths,
                checked_ids=self.checked_ids,
                progress_callback=self.progress_signal.emit,
                action_id=self.action_id,
            )
            self.success_signal.emit(result)
        except Exception as exc:  # noqa: BLE001 - 线程内异常转信号
            self.error_signal.emit(str(exc))


class AgentRunThread(QThread):
    log_signal = pyqtSignal(str)
    step_started = pyqtSignal(int, str)
    step_finished = pyqtSignal(int, str, str)
    node_update = pyqtSignal(dict)
    finished_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)

    def __init__(self, runner: AgentRunner, steps: list[dict], action_id: str, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.steps = steps
        self.action_id = action_id
        runner.progress = self.log_signal.emit
        runner.on_step_start = lambda step: self.step_started.emit(step.index, step.title)
        runner.on_step_finish = lambda step: self.step_finished.emit(
            step.index, step.status, step.message
        )
        runner.on_update = self.node_update.emit

    def request_stop(self):
        self.runner.cancel_event.set()

    def run(self):
        try:
            result = self.runner.run(self.steps, self.action_id)
            self.finished_signal.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error_signal.emit(str(exc))


class PlanJsonDialog(QDialog):
    """直接编辑计划 JSON 的对话框。"""

    def __init__(self, plan: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑计划 JSON")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("修改后点击确定，会重新校验步骤与节点范围。"))
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(json.dumps(plan, ensure_ascii=False, indent=2))
        self.editor.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.editor, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AgentConsoleDialog(QDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent if parent is not None else window)
        self.window = window
        self.setWindowTitle("🤖 Agent 控制台：命令 → 计划 → 执行")
        self.resize(1080, 760)
        self._plan: dict = {"goal": "", "steps": []}
        self._plan_thread: AgentPlanThread | None = None
        self._run_thread: AgentRunThread | None = None
        self._planner: AgentPlanner | None = None
        self._running = False
        self._planning = False
        self._run_action_id = ""
        self._step_rows: dict[int, int] = {}

        self._build_ui()
        self._refresh_models()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        command_group = QWidget()
        command_layout = QVBoxLayout(command_group)
        command_layout.setContentsMargins(0, 0, 0, 0)
        command_layout.addWidget(
            QLabel("用一句话描述你想做的事，例如：把第一章下面缺失正文的场景补完，再生成概要。")
        )
        self.command_input = QPlainTextEdit()
        self.command_input.setPlaceholderText(
            "例如：检查第一章所有场景，把与设定冲突的地方按人物设定改写；"
            "缺失正文的场景先生成 3000 字。"
        )
        self.command_input.setFixedHeight(84)
        command_layout.addWidget(self.command_input)

        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("模型:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(300)
        control_row.addWidget(self.model_combo, stretch=1)

        self.btn_plan = QPushButton("① 规划")
        self.btn_plan.setToolTip("调用模型把命令拆解成可执行步骤（Ctrl+Enter）")
        self.btn_plan.clicked.connect(self._start_plan)
        control_row.addWidget(self.btn_plan)

        self.btn_run = QPushButton("② 执行")
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self._start_run)
        control_row.addWidget(self.btn_run)

        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_run)
        control_row.addWidget(self.btn_stop)

        self.btn_edit_plan = QPushButton("✏️ 编辑计划")
        self.btn_edit_plan.setEnabled(False)
        self.btn_edit_plan.clicked.connect(self._edit_plan)
        control_row.addWidget(self.btn_edit_plan)
        command_layout.addLayout(control_row)

        status_row = QHBoxLayout()
        self.goal_label = QLabel("尚未规划。")
        self.goal_label.setWordWrap(True)
        self.goal_label.setStyleSheet("color: #8ab4f8;")
        status_row.addWidget(self.goal_label, stretch=1)
        self.continue_on_error_cb = QCheckBox("某步失败后继续执行后续步骤")
        status_row.addWidget(self.continue_on_error_cb)
        command_layout.addLayout(status_row)
        layout.addWidget(command_group)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.plan_table = QTableWidget(0, 7)
        self.plan_table.setHorizontalHeaderLabels(
            ["启用", "#", "动作", "范围 / 场景", "参数", "说明", "状态"]
        )
        self.plan_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.plan_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.plan_table.verticalHeader().setVisible(False)
        self.plan_table.setAlternatingRowColors(False)
        header = self.plan_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.plan_table)

        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("执行日志:"))
        log_header.addStretch()
        self.btn_clear_log = QPushButton("清空")
        self.btn_clear_log.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(self.btn_clear_log)
        log_layout.addLayout(log_header)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;"
        )
        log_layout.addWidget(self.log_view)
        splitter.addWidget(log_panel)
        splitter.setSizes([380, 260])
        layout.addWidget(splitter, stretch=1)

        bottom = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")
        bottom.addWidget(self.status_label, stretch=1)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.close)
        bottom.addWidget(self.btn_close)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    # 模型选择
    # ------------------------------------------------------------------
    def _refresh_models(self):
        self.model_combo.clear()
        current_label = ""
        runtime = getattr(getattr(self.window, "llm_client", None), "runtime", None)
        if runtime is not None:
            current_label = f"跟随当前模型：{runtime.label()}"
        self.model_combo.addItem(current_label or "跟随当前模型", None)
        providers = (getattr(self.window, "config", {}) or {}).get("providers", []) or []
        for provider in providers:
            provider_name = provider.get("name") or provider.get("id", "")
            for model in provider.get("models", []) or []:
                label = (
                    f"{provider_name} / "
                    f"{model.get('display_name') or model.get('model', '')}"
                )
                self.model_combo.addItem(
                    label, (provider.get("id", ""), model.get("id", ""))
                )

    def _client(self):
        selection = self.model_combo.currentData()
        if selection:
            provider_id, model_id = selection
            return self.window._create_llm_client_for_model(provider_id, model_id)
        return getattr(self.window, "llm_client", None)

    # ------------------------------------------------------------------
    # 规划
    # ------------------------------------------------------------------
    def _busy(self) -> bool:
        return self._planning or self._running

    def _set_planning_ui(self, planning: bool):
        self._planning = planning
        self.btn_plan.setEnabled(not planning)
        self.btn_plan.setText("规划中…" if planning else "① 规划")
        self.command_input.setEnabled(not planning)
        self.model_combo.setEnabled(not planning)

    def _start_plan(self):
        if self._busy():
            return
        if not self.window.workspace:
            QMessageBox.information(self, "提示", "请先加载工作区。")
            return
        command = self.command_input.toPlainText().strip()
        if not command:
            QMessageBox.information(self, "提示", "请先输入命令。")
            return
        client = self._client()
        if client is None or getattr(client, "runtime", None) is None:
            QMessageBox.warning(
                self,
                "未配置模型",
                "请先在「设置 → 模型服务」中配置可用的文本模型。",
            )
            return
        if self._window_busy():
            QMessageBox.warning(
                self, "正在执行其它任务", "主窗口还有生成/批量任务在运行，请等待完成后再规划。"
            )
            return

        self._plan = {"goal": "", "steps": []}
        self.plan_table.setRowCount(0)
        self._step_rows.clear()
        self.btn_run.setEnabled(False)
        self.btn_edit_plan.setEnabled(False)
        self.goal_label.setText("正在规划…")
        self._append_log(f"命令：{command}")

        checked_nodes = self.window._collect_checked_novel_nodes()
        checked_ids = []
        for item, node in checked_nodes or []:
            if getattr(self.window, "novel_tree", None) is not None:
                try:
                    from ui.utils import get_item_level

                    if get_item_level(item) != 3:
                        continue
                except Exception:
                    pass
            node_id = node.get("id")
            if node_id:
                checked_ids.append(str(node_id))
        checked_paths = list(self.window.get_checked_settings() or [])
        action_id = self.window._task_action_begin(
            "agent_plan",
            f"Agent 规划：{command[:36]}",
            description=command,
            meta={"command": command, "phase": "plan"},
            reuse=False,
        )
        self._plan_action_id = action_id

        planner = AgentPlanner(client, self.window.workspace)
        self._planner = planner
        thread = AgentPlanThread(
            planner,
            command,
            copy.deepcopy(self.window.outline_tree_data or {"nodes": []}),
            checked_paths,
            checked_ids,
            action_id,
            self,
        )
        thread.success_signal.connect(self._on_plan_success)
        thread.error_signal.connect(self._on_plan_error)
        thread.progress_signal.connect(self._append_log)
        self._plan_thread = thread
        self._set_planning_ui(True)
        self.status_label.setText("规划中…")
        thread.start()

    def _finish_plan_action(self, status: str | None = None):
        try:
            self.window._task_action_finish(status)
        except Exception:
            pass

    def _on_plan_success(self, result):
        self._set_planning_ui(False)
        self._finish_plan_action()
        if not result.ok:
            self.goal_label.setText("规划失败，请根据日志调整命令。")
            for error in result.errors:
                self._append_log(f"✗ {error}")
            QMessageBox.warning(
                self,
                "规划失败",
                "\n".join(result.errors) or "模型没有返回可执行的计划。",
            )
            return
        self._plan = result.plan
        self._populate_plan_table(self._plan)
        self.goal_label.setText(f"目标：{self._plan.get('goal', '')}")
        self._append_log("规划完成：")
        for line in describe_plan(self._plan).splitlines():
            self._append_log(f"  {line}")
        self.btn_run.setEnabled(bool(self._plan.get("steps")))
        self.btn_edit_plan.setEnabled(bool(self._plan.get("steps")))
        self.status_label.setText(
            f"计划 {len(self._plan.get('steps', []))} 步，可勾选后执行。"
        )

    def _on_plan_error(self, message: str):
        self._set_planning_ui(False)
        self._finish_plan_action(TaskStatus.FAILED)
        self.goal_label.setText("规划失败。")
        self._append_log(f"✗ 规划异常：{message}")
        QMessageBox.warning(self, "规划失败", message)

    # ------------------------------------------------------------------
    # 计划表格
    # ------------------------------------------------------------------
    def _populate_plan_table(self, plan: dict):
        steps = plan.get("steps", []) or []
        self.plan_table.setRowCount(len(steps))
        self._step_rows.clear()
        for row, step in enumerate(steps):
            index = int(step.get("index", row + 1))
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            enabled_item.setCheckState(Qt.CheckState.Checked)
            enabled_item.setData(Qt.ItemDataRole.UserRole, step)
            self.plan_table.setItem(row, 0, enabled_item)

            self.plan_table.setItem(row, 1, QTableWidgetItem(str(index)))
            self.plan_table.setItem(
                row, 2, QTableWidgetItem(str(step.get("action_label", step.get("action", ""))))
            )
            titles = step.get("node_titles", []) or []
            scope_text = f"{len(step.get('node_ids', []))} 个场景"
            if titles:
                preview = "、".join(titles[:3])
                if len(titles) > 3:
                    preview += f" 等 {len(titles)} 个"
                scope_text += f"：{preview}"
            self.plan_table.setItem(row, 3, QTableWidgetItem(scope_text))
            self.plan_table.setItem(row, 4, QTableWidgetItem(self._params_text(step)))
            desc = str(step.get("title", ""))
            if step.get("reason"):
                desc += f"（{step['reason']}）"
            self.plan_table.setItem(row, 5, QTableWidgetItem(desc))
            status_item = QTableWidgetItem(STATUS_LABELS["pending"])
            self.plan_table.setItem(row, 6, status_item)
            self._step_rows[index] = row

    @staticmethod
    def _params_text(step: dict) -> str:
        params = step.get("params") or {}
        parts = []
        if "word_count" in params:
            parts.append(f"{params['word_count']} 字")
        if params.get("include_next") is False:
            parts.append("不含后续节点")
        if params.get("requirement"):
            parts.append(f"要求：{params['requirement'][:24]}…")
        return "，".join(parts) or "-"

    def _steps_from_table(self) -> list[dict]:
        steps = []
        for row in range(self.plan_table.rowCount()):
            item = self.plan_table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            step = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(step, dict):
                steps.append(step)
        return steps

    def _set_step_status(self, index: int, status: str, message: str = ""):
        row = self._step_rows.get(index)
        if row is None or row >= self.plan_table.rowCount():
            return
        item = self.plan_table.item(row, 6)
        if item is None:
            return
        item.setText(STATUS_LABELS.get(status, status))
        color = STATUS_COLORS.get(status)
        if color:
            item.setForeground(QColor(color))
        if message:
            item.setToolTip(message)

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    def _window_busy(self) -> bool:
        return any(
            bool(getattr(self.window, attr, False))
            for attr in (
                "is_batch_generating",
                "is_batch_summary_regenerating",
                "is_batch_modifying",
                "is_batch_modifying_settings",
            )
        )

    def _start_run(self):
        if self._busy():
            return
        steps = self._steps_from_table()
        if not steps:
            QMessageBox.information(self, "提示", "请至少勾选一个步骤。")
            return
        if self._window_busy():
            QMessageBox.warning(
                self, "正在执行其它任务", "主窗口还有生成/批量任务在运行，请等待完成。"
            )
            return
        client = self._client()
        if client is None or getattr(client, "runtime", None) is None:
            QMessageBox.warning(self, "未配置模型", "请先配置可用的文本模型。")
            return

        total_nodes = sum(len(step.get("node_ids", []) or []) for step in steps)
        reply = QMessageBox.question(
            self,
            "确认执行",
            f"将执行 {len(steps)} 个步骤，共 {total_nodes} 个场景。\n\n"
            "执行期间会连续调用模型，请在控制台等待；\n"
            "“按要求改写”只会生成待合并修改，不会自动覆盖原文。\n\n"
            "确认开始吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_action_id = self.window._task_action_begin(
            "agent_run",
            f"Agent 执行：{(self._plan.get('goal') or self.command_input.toPlainText().strip())[:36]}",
            description=self.command_input.toPlainText().strip(),
            node_ids=[
                node_id
                for step in steps
                for node_id in step.get("node_ids", []) or []
            ],
            meta={
                "phase": "run",
                "command": self.command_input.toPlainText().strip(),
                "steps": [
                    {
                        "index": step.get("index"),
                        "action": step.get("action"),
                        "title": step.get("title"),
                        "node_ids": step.get("node_ids", []),
                    }
                    for step in steps
                ],
            },
            reuse=False,
        )
        runner = AgentRunner(
            workspace=self.window.workspace,
            tree_data=self.window.outline_tree_data or {"nodes": []},
            llm_client=client,
            setting_paths=list(self.window.get_checked_settings() or []),
            stop_on_error=not self.continue_on_error_cb.isChecked(),
        )
        thread = AgentRunThread(runner, steps, self._run_action_id, self)
        thread.log_signal.connect(self._append_log)
        thread.step_started.connect(self._on_step_started)
        thread.step_finished.connect(self._on_step_finished)
        thread.node_update.connect(self._on_node_update)
        thread.finished_signal.connect(self._on_run_finished)
        thread.error_signal.connect(self._on_run_error)
        self._run_thread = thread

        for index in self._step_rows:
            self._set_step_status(index, "pending", "")
        self._running = True
        self.btn_run.setEnabled(False)
        self.btn_plan.setEnabled(False)
        self.btn_edit_plan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.command_input.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.status_label.setText("执行中…（可点击停止，当前请求结束后停下）")
        self._append_log("=" * 40)
        self._append_log("开始执行计划…")
        thread.start()

    def _on_step_started(self, index: int, title: str):
        self._set_step_status(index, "running")
        self._append_log(f"▶ 步骤 {index}：{title}")

    def _on_step_finished(self, index: int, status: str, message: str):
        self._set_step_status(index, status, message)
        self._append_log(f"■ 步骤 {index}：{STATUS_LABELS.get(status, status)} · {message}")

    def _on_node_update(self, update: dict):
        try:
            self.window.apply_agent_node_update(update)
        except Exception as exc:  # pragma: no cover - UI 异常不影响执行
            self._append_log(f"⚠ 写回节点更新失败：{exc}")
        kind = str(update.get("kind", ""))
        label = {"content": "正文", "summary": "概要", "pending": "待合并修改"}.get(kind, kind)
        self._append_log(f"  ✔ 已保存{label}：{update.get('node_id', '')}")

    def _on_run_finished(self, result):
        self._running = False
        self.btn_run.setEnabled(True)
        self.btn_plan.setEnabled(True)
        self.btn_edit_plan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.command_input.setEnabled(True)
        self.model_combo.setEnabled(True)
        try:
            self.window.finish_agent_run(self._run_action_id, result)
        except Exception as exc:  # pragma: no cover
            self._append_log(f"⚠ 收尾失败：{exc}")
        summary = (
            f"完成 {sum(step.ok_nodes for step in result.steps)} 个场景，"
            f"失败 {sum(step.failed_nodes for step in result.steps)} 个"
        )
        if result.cancelled:
            self.status_label.setText("已停止。")
            self._append_log("⏹ 执行已停止。")
            QMessageBox.information(self, "已停止", summary)
        elif result.success:
            self.status_label.setText("执行完成。")
            self._append_log(f"✅ 执行完成：{summary}")
            QMessageBox.information(self, "执行完成", summary)
        else:
            self.status_label.setText("执行结束，但有失败步骤。")
            self._append_log(f"⚠ 执行结束：{summary}；{result.error}")
            QMessageBox.warning(self, "执行结束", f"{summary}\n{result.error}")

    def _on_run_error(self, message: str):
        self._running = False
        self.btn_run.setEnabled(True)
        self.btn_plan.setEnabled(True)
        self.btn_edit_plan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.command_input.setEnabled(True)
        self.model_combo.setEnabled(True)
        try:
            self.window._task_action_finish(TaskStatus.FAILED)
        except Exception:
            pass
        self._append_log(f"✗ 执行线程异常：{message}")
        QMessageBox.critical(self, "执行失败", message)

    def _stop_run(self):
        if self._run_thread is not None and self._running:
            self._run_thread.request_stop()
            self.btn_stop.setEnabled(False)
            self.status_label.setText("正在停止…（当前请求结束后停下）")
            self._append_log("请求停止：将在当前模型请求结束后停下。")

    # ------------------------------------------------------------------
    # 编辑计划 / 关闭
    # ------------------------------------------------------------------
    def _edit_plan(self):
        if not self._plan.get("steps"):
            return
        dialog = PlanJsonDialog(self._plan, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            raw = parse_plan_response(dialog.editor.toPlainText())
            checked_ids = []
            for item, node in self.window._collect_checked_novel_nodes() or []:
                node_id = node.get("id")
                if node_id:
                    checked_ids.append(str(node_id))
            plan, errors = normalize_plan(
                raw, self.window.outline_tree_data or {"nodes": []}, checked_ids=checked_ids
            )
        except ValueError as exc:
            QMessageBox.warning(self, "计划格式错误", str(exc))
            return
        if errors:
            QMessageBox.warning(self, "计划有问题", "\n".join(errors))
        self._plan = plan
        self._populate_plan_table(plan)
        self.goal_label.setText(f"目标：{plan.get('goal', '')}")
        self.btn_run.setEnabled(bool(plan.get("steps")))
        self.btn_edit_plan.setEnabled(bool(plan.get("steps")))
        self._append_log("已更新计划。")
        for line in describe_plan(plan).splitlines():
            self._append_log(f"  {line}")

    def _append_log(self, message: str):
        if not message:
            return
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")

    def closeEvent(self, event):
        if self._planning:
            QMessageBox.information(
                self, "正在规划", "规划请求还在进行，请等待完成后再关闭。"
            )
            event.ignore()
            return
        if self._running:
            reply = QMessageBox.question(
                self,
                "正在执行",
                "执行尚未结束，确定要停止并关闭吗？\n（当前模型请求结束后才会真正停下）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._stop_run()
            event.ignore()
            return
        event.accept()


__all__ = ["AgentConsoleDialog", "AgentPlanThread", "AgentRunThread", "PlanJsonDialog"]
