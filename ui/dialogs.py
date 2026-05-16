"""
自定义对话框模块
包含项目中使用的各种自定义 QDialog 子类。
"""
import os
import re
import shutil
from threading import Event

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QTextEdit, QLineEdit, QPushButton, QMessageBox,
                             QProgressBar, QGroupBox, QPlainTextEdit, QCheckBox, QSpinBox,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)


class IdeaInputDialog(QDialog):
    """适配深色主题的自定义多行输入框"""
    def __init__(self, parent, title, hint):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 400)
        
        layout = QVBoxLayout(self)
        
        hint_label = QLabel(hint)
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("在这里输入您的设定概念或核心点子...")
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")
        
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def get_text(self):
        return self.text_edit.toPlainText()


class RenameNodeDialog(QDialog):
    """带清空按钮的节点重命名弹窗"""
    def __init__(self, parent, title, label_text, default_text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(350, 120)
        
        layout = QVBoxLayout(self)
        
        label = QLabel(label_text)
        layout.addWidget(label)
        
        # 使用 QLineEdit，完美支持 Ctrl+A 全选
        self.line_edit = QLineEdit(default_text)
        # 开启右侧自带的 'X' 清空按钮
        self.line_edit.setClearButtonEnabled(True) 
        layout.addWidget(self.line_edit)
        
        # 自动全选现有文本，方便直接输入覆盖
        self.line_edit.selectAll()
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")
        
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def get_text(self):
        return self.line_edit.text()


class ProofreadDialog(QDialog):
    def __init__(self, parent, workspace, llm_client, config: dict | None = None):
        super().__init__(parent)
        self.workspace = workspace
        self.llm_client = llm_client
        self.config = config or {}
        proofread_cfg = (self.config.get("proofread", {}) or {}) if isinstance(self.config, dict) else {}
        try:
            self._parallel_passes = int(proofread_cfg.get("parallel_passes", 4))
        except Exception:
            self._parallel_passes = 4
        try:
            self._parallel_solves = int(proofread_cfg.get("parallel_solves", 3))
        except Exception:
            self._parallel_solves = 3

        self._thread = None
        self._stop_event: Event | None = None
        self._active_phase: str | None = None

        self._find_total = 0
        self._find_done = 0
        self._solve_total = 0
        self._solve_done = 0

        self.setWindowTitle("小说校对（独立模块）")
        self.resize(860, 680)
        self._init_ui()
        self._refresh_state()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        workspace_path = getattr(self.workspace, "workspace_path", "") if self.workspace else ""
        header = QLabel(f"工作区：{workspace_path}")
        header.setWordWrap(True)
        layout.addWidget(header)

        self.find_group = QGroupBox("步骤 1：查找问题（Find）")
        find_layout = QVBoxLayout(self.find_group)
        self.find_status = QLabel("状态：未开始")
        find_layout.addWidget(self.find_status)
        self.find_progress = QProgressBar()
        self.find_progress.setRange(0, 100)
        self.find_progress.setValue(0)
        find_layout.addWidget(self.find_progress)
        find_option_layout = QHBoxLayout()
        find_option_layout.addWidget(QLabel("并发 Pass 数:"))
        self.spin_parallel_passes = QSpinBox()
        self.spin_parallel_passes.setRange(1, 16)
        self.spin_parallel_passes.setValue(max(1, min(16, self._parallel_passes)))
        find_option_layout.addWidget(self.spin_parallel_passes)
        find_option_layout.addStretch()
        find_layout.addLayout(find_option_layout)
        find_filter_layout = QHBoxLayout()
        find_filter_layout.addWidget(QLabel("章节:"))
        self.find_chapters = QLineEdit()
        self.find_chapters.setPlaceholderText("例如：第一章,第二章（留空=全部）")
        find_filter_layout.addWidget(self.find_chapters, stretch=2)
        find_filter_layout.addWidget(QLabel("场景关键词:"))
        self.find_scene_keywords = QLineEdit()
        self.find_scene_keywords.setPlaceholderText("例如：酒吧,枪战（留空=全部）")
        find_filter_layout.addWidget(self.find_scene_keywords, stretch=3)
        find_layout.addLayout(find_filter_layout)
        find_btn_layout = QHBoxLayout()
        self.btn_find_start = QPushButton("从头开始")
        self.btn_find_resume = QPushButton("从上次继续")
        self.btn_find_view_issues = QPushButton("查看问题列表")
        self.btn_find_view_issues.setEnabled(False)
        self.btn_find_view_issues.clicked.connect(self._open_issues_dialog)
        self.btn_find_start.clicked.connect(lambda: self._start_phase("find", resume=False))
        self.btn_find_resume.clicked.connect(lambda: self._start_phase("find", resume=True))
        find_btn_layout.addWidget(self.btn_find_start)
        find_btn_layout.addWidget(self.btn_find_resume)
        find_btn_layout.addWidget(self.btn_find_view_issues)
        find_btn_layout.addStretch()
        find_layout.addLayout(find_btn_layout)
        layout.addWidget(self.find_group)

        self.solve_group = QGroupBox("步骤 2：自动修复（Solve）")
        solve_layout = QVBoxLayout(self.solve_group)
        self.solve_status = QLabel("状态：未开始")
        solve_layout.addWidget(self.solve_status)
        self.solve_progress = QProgressBar()
        self.solve_progress.setRange(0, 100)
        self.solve_progress.setValue(0)
        solve_layout.addWidget(self.solve_progress)
        solve_option_layout = QHBoxLayout()
        solve_option_layout.addWidget(QLabel("并发 修复数:"))
        self.spin_parallel_solves = QSpinBox()
        self.spin_parallel_solves.setRange(1, 16)
        self.spin_parallel_solves.setValue(max(1, min(16, self._parallel_solves)))
        solve_option_layout.addWidget(self.spin_parallel_solves)
        solve_option_layout.addStretch()
        solve_layout.addLayout(solve_option_layout)
        solve_filter_layout = QHBoxLayout()
        solve_filter_layout.addWidget(QLabel("问题类别:"))
        self.solve_categories = QLineEdit()
        self.solve_categories.setPlaceholderText("例如：L1,L2,W5（留空=全部）")
        solve_filter_layout.addWidget(self.solve_categories, stretch=2)
        solve_filter_layout.addWidget(QLabel("章节:"))
        self.solve_chapters = QLineEdit()
        self.solve_chapters.setPlaceholderText("例如：第一章,第二章（留空=全部）")
        solve_filter_layout.addWidget(self.solve_chapters, stretch=3)
        solve_layout.addLayout(solve_filter_layout)
        self.cb_skip_existing_pending = QCheckBox("跳过已有待合并修改（推荐）")
        self.cb_skip_existing_pending.setChecked(True)
        solve_layout.addWidget(self.cb_skip_existing_pending)
        solve_btn_layout = QHBoxLayout()
        self.btn_solve_start = QPushButton("从头开始")
        self.btn_solve_resume = QPushButton("从上次继续")
        self.btn_solve_start.clicked.connect(lambda: self._start_phase("solve", resume=False))
        self.btn_solve_resume.clicked.connect(lambda: self._start_phase("solve", resume=True))
        solve_btn_layout.addWidget(self.btn_solve_start)
        solve_btn_layout.addWidget(self.btn_solve_resume)
        solve_btn_layout.addStretch()
        solve_layout.addLayout(solve_btn_layout)
        layout.addWidget(self.solve_group)

        confirm_group = QGroupBox("步骤 3：人工确认（Confirm）")
        confirm_layout = QVBoxLayout(confirm_group)
        confirm_label = QLabel(
            "修复完成后，待合并的场景会在大纲树中显示为红色。\n"
            "右键该场景节点 → “🔄 进行合并” 打开 Diff 合并界面；或选择“❌ 丢弃修改”。"
        )
        confirm_label.setWordWrap(True)
        confirm_layout.addWidget(confirm_label)
        layout.addWidget(confirm_group)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(220)
        layout.addWidget(self.log_view)

        bottom_layout = QHBoxLayout()
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_force_stop = QPushButton("强行停止")
        self.btn_force_stop.setEnabled(False)
        self.btn_force_stop.clicked.connect(self._force_stop)
        self.btn_clear = QPushButton("清理进度")
        self.btn_clear.clicked.connect(self._clear_progress)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.close)
        bottom_layout.addWidget(self.btn_pause)
        bottom_layout.addWidget(self.btn_force_stop)
        bottom_layout.addWidget(self.btn_clear)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_close)
        layout.addLayout(bottom_layout)

    def _append_log(self, msg: str):
        msg = str(msg or "").rstrip("\n")
        if msg:
            self.log_view.appendPlainText(msg)

    def _checkpoint_dir(self) -> str:
        workspace_path = getattr(self.workspace, "workspace_path", "") if self.workspace else ""
        return os.path.join(workspace_path, "系统数据", "proofread_checkpoint")

    def _issues_file(self) -> str:
        sys_data = getattr(self.workspace, "sys_data_path", "") if self.workspace else ""
        return os.path.join(sys_data, "proofread_issues.json")

    def _load_run_state(self) -> dict | None:
        run_state_path = os.path.join(self._checkpoint_dir(), "run_state.json")
        if not os.path.exists(run_state_path):
            return None
        try:
            import json

            with open(run_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _is_thread_running(self) -> bool:
        return bool(self._thread and getattr(self._thread, "isRunning", lambda: False)())

    def _set_running_ui(self, running: bool):
        self.btn_pause.setEnabled(bool(running))
        self.btn_force_stop.setEnabled(bool(running))
        self.btn_clear.setEnabled(not running)
        self.btn_close.setEnabled(not running)
        self.btn_find_start.setEnabled(not running)
        self.btn_find_resume.setEnabled(not running)
        self.btn_find_view_issues.setEnabled(not running and os.path.exists(self._issues_file()))
        self.btn_solve_start.setEnabled(not running)
        self.btn_solve_resume.setEnabled(not running)
        self.spin_parallel_passes.setEnabled(not running)
        self.spin_parallel_solves.setEnabled(not running)
        self.find_chapters.setEnabled(not running)
        self.find_scene_keywords.setEnabled(not running)
        self.solve_categories.setEnabled(not running)
        self.solve_chapters.setEnabled(not running)
        self.cb_skip_existing_pending.setEnabled(not running)

    def _refresh_state(self):
        run_state = self._load_run_state() or {}
        phase = str(run_state.get("phase", "") or "")
        status = str(run_state.get("status", "") or "")
        resumable = status in {"paused", "running"}

        issues_ok = os.path.exists(self._issues_file())
        self.btn_solve_start.setEnabled(bool(issues_ok) and not self._is_thread_running())
        self.btn_find_view_issues.setEnabled(bool(issues_ok) and not self._is_thread_running())

        self.btn_find_resume.setEnabled(bool(resumable and phase == "find") and not self._is_thread_running())
        self.btn_solve_resume.setEnabled(bool(resumable and phase == "solve" and issues_ok) and not self._is_thread_running())

        if phase == "find":
            self.find_status.setText(f"状态：{status or 'unknown'}")
        elif phase == "solve":
            self.solve_status.setText(f"状态：{status or 'unknown'}")

        if not issues_ok:
            self.solve_status.setText("状态：未开始（未找到问题清单）")

    def _clear_progress(self):
        if self._is_thread_running():
            return
        cp_dir = self._checkpoint_dir()
        if os.path.exists(cp_dir):
            shutil.rmtree(cp_dir, ignore_errors=True)
        issues_path = self._issues_file()
        if os.path.exists(issues_path):
            try:
                os.remove(issues_path)
            except Exception:
                pass

        self._find_total = 0
        self._find_done = 0
        self._solve_total = 0
        self._solve_done = 0
        self.find_progress.setValue(0)
        self.solve_progress.setValue(0)
        self.find_status.setText("状态：未开始")
        self.solve_status.setText("状态：未开始")
        self._append_log("已清理校对进度（checkpoint 与 issues 文件）。")
        self._refresh_state()

    def _pause(self):
        if self._stop_event:
            self._stop_event.set()
            self._append_log("已发送暂停信号，等待当前任务结束后保存进度...")
            self.btn_pause.setEnabled(False)

    def _force_stop(self):
        if not self._is_thread_running():
            return
        reply = QMessageBox.question(
            self,
            "确认强行停止",
            "强行停止会直接终止后台线程，可能导致本轮运行状态未能完整写入检查点。\n"
            "下次建议先点击“清理进度”再重新开始。\n\n"
            "确定要强行停止吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._append_log("强行停止：正在终止后台线程...")
        if self._stop_event:
            try:
                self._stop_event.set()
            except Exception:
                pass

        try:
            if self._thread:
                self._thread.terminate()
                self._thread.wait(1500)
        except Exception as e:
            self._append_log(f"强行停止异常：{e}")

        if self._is_thread_running():
            self._append_log("强行停止未能立即结束线程（仍在运行）。你仍可关闭窗口，但可能需要重启程序。")
        else:
            self._append_log("强行停止完成。")

        self._set_running_ui(False)
        self._active_phase = None
        self._stop_event = None
        self._refresh_state()

    def _start_phase(self, phase: str, resume: bool):
        if self._is_thread_running():
            return
        if not self.workspace:
            QMessageBox.information(self, "提示", "请先加载工作区，然后再使用小说校对。")
            return
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return

        if phase == "solve" and not os.path.exists(self._issues_file()):
            QMessageBox.information(self, "提示", "未找到问题清单，请先执行“查找问题（Find）”。")
            return

        if not resume:
            cp_dir = self._checkpoint_dir()
            if os.path.exists(cp_dir):
                shutil.rmtree(cp_dir, ignore_errors=True)

        self._active_phase = phase
        self._stop_event = Event()

        from ui.workers import ProofreadThread
        include_categories = None
        include_chapters = None
        find_include_chapters = None
        find_scene_title_keywords = None
        skip_existing_pending = True
        parallel_passes = int(self.spin_parallel_passes.value())
        parallel_solves = int(self.spin_parallel_solves.value())
        if phase == "solve":
            include_categories = self._parse_csv_set(self.solve_categories.text())
            include_chapters = self._parse_csv_set(self.solve_chapters.text())
            skip_existing_pending = bool(self.cb_skip_existing_pending.isChecked())
        if phase == "find":
            find_include_chapters = self._parse_csv_set(self.find_chapters.text())
            find_scene_title_keywords = self._parse_csv_list(self.find_scene_keywords.text())

        self._thread = ProofreadThread(
            llm_client=self.llm_client,
            workspace=self.workspace,
            config=self.config,
            mode=phase,
            resume=resume,
            stop_event=self._stop_event,
            skip_existing_pending=skip_existing_pending,
            include_categories=include_categories,
            include_chapters=include_chapters,
            find_include_chapters=find_include_chapters,
            find_scene_title_keywords=find_scene_title_keywords,
            parallel_passes=parallel_passes,
            parallel_solves=parallel_solves,
        )
        self._thread.progress_signal.connect(self._on_progress)
        self._thread.meta_signal.connect(self._on_meta)
        self._thread.success_signal.connect(self._on_success)
        self._thread.error_signal.connect(self._on_error)

        if phase == "find":
            self.find_status.setText("状态：运行中...")
            if not resume:
                self.find_progress.setValue(0)
                self._find_done = 0
        if phase == "solve":
            self.solve_status.setText("状态：运行中...")
            if not resume:
                self.solve_progress.setValue(0)
                self._solve_done = 0

        self._append_log(f"启动 {phase.upper()}（{'继续' if resume else '从头开始'}）...")
        self._set_running_ui(True)
        self._thread.start()

    def _on_meta(self, meta: dict):
        phase = str(meta.get("phase", "") or "")
        resume = bool(meta.get("resume", False))
        run_state = self._load_run_state() or {}

        if phase == "find":
            self._find_total = int(meta.get("tasks_total", 0) or 0)
            baseline = 0
            if resume and str(run_state.get("phase", "")) == "find":
                passes = run_state.get("passes", {}) or {}
                if isinstance(passes, dict):
                    baseline = sum(int(v.get("scenes_completed", 0) or 0) for v in passes.values() if isinstance(v, dict))
            self._find_done = max(0, min(baseline, self._find_total or baseline))
            self._update_find_progress()
            return

        if phase == "solve":
            self._solve_total = int(meta.get("scenes_total", 0) or 0)
            baseline = 0
            if resume and str(run_state.get("phase", "")) == "solve":
                passes = run_state.get("passes", {}) or {}
                solve_pass = passes.get("solve_scenes", {}) if isinstance(passes, dict) else {}
                if isinstance(solve_pass, dict):
                    baseline = int(solve_pass.get("scenes_completed", 0) or 0) + int(solve_pass.get("scenes_failed", 0) or 0)
            self._solve_done = max(0, min(baseline, self._solve_total or baseline))
            self._update_solve_progress()

    def _update_find_progress(self):
        if self._find_total <= 0:
            self.find_progress.setValue(0)
            self.find_status.setText("状态：运行中...")
            return
        pct = int(min(100, max(0, (self._find_done / self._find_total) * 100)))
        self.find_progress.setValue(pct)
        self.find_status.setText(f"状态：运行中...（{self._find_done}/{self._find_total}）")

    def _update_solve_progress(self):
        if self._solve_total <= 0:
            self.solve_progress.setValue(0)
            self.solve_status.setText("状态：运行中...")
            return
        pct = int(min(100, max(0, (self._solve_done / self._solve_total) * 100)))
        self.solve_progress.setValue(pct)
        self.solve_status.setText(f"状态：运行中...（{self._solve_done}/{self._solve_total}）")

    def _on_progress(self, msg: str):
        self._append_log(msg)
        text = str(msg or "").strip()
        if not text:
            return

        if self._active_phase == "find":
            if "请求第" in text:
                return
            m = re.match(r"^\[([^\]]+)\].*完成$", text)
            if m:
                tag = m.group(1)
                if tag != "solve":
                    self._find_done += 1
                    self._update_find_progress()
            return

        if self._active_phase == "solve":
            if text.startswith("[solve]") and ("生成完成" in text or text.endswith("完成")):
                self._solve_done += 1
                self._update_solve_progress()

    def _on_success(self, payload: dict):
        phase = str(payload.get("phase", "") or "")
        status = str(payload.get("status", "") or "")

        self._set_running_ui(False)
        self.btn_pause.setEnabled(False)

        if phase == "find":
            if status == "paused":
                self.find_status.setText("状态：已暂停（可继续）")
                self._append_log("Find 已暂停，进度已保存，可稍后继续。")
            else:
                issues_count = int(payload.get("issues_count", 0) or 0)
                self.find_progress.setValue(100)
                self.find_status.setText(f"状态：已完成（问题数：{issues_count}）")
                self._append_log(f"Find 完成：共输出 {issues_count} 条问题。")
                self.btn_find_view_issues.setEnabled(os.path.exists(self._issues_file()))
        elif phase == "solve":
            if status == "paused":
                self.solve_status.setText("状态：已暂停（可继续）")
                self._append_log("Solve 已暂停，进度已保存，可稍后继续。")
            else:
                stats = payload.get("stats", {}) or {}
                ok_count = int(stats.get("ok_count", 0) or 0)
                fail_count = int(stats.get("fail_count", 0) or 0)
                skipped_count = int(stats.get("skipped_count", 0) or 0)
                total = int(stats.get("total", 0) or 0)
                self.solve_progress.setValue(100 if self._solve_total or total else 0)
                self.solve_status.setText(f"状态：已完成（成功 {ok_count} / 失败 {fail_count} / 跳过 {skipped_count}）")
                self._append_log(f"Solve 完成：total={total}, ok={ok_count}, fail={fail_count}, skipped={skipped_count}")

                parent = self.parent()
                if parent and hasattr(parent, "_refresh_novel_tree"):
                    try:
                        parent.workspace._load_all_pending_modifies()  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    parent._refresh_novel_tree()  # type: ignore[attr-defined]
                QMessageBox.information(
                    self,
                    "校对修复完成",
                    f"修复已完成：成功 {ok_count}，失败 {fail_count}，跳过 {skipped_count}。\n"
                    "请在大纲树中右键红色场景节点，选择“进行合并”。",
                )

        self._active_phase = None
        self._stop_event = None
        self._refresh_state()

    def _on_error(self, err: str):
        self._set_running_ui(False)
        self.btn_pause.setEnabled(False)
        self._append_log(f"错误：{err}")
        self._active_phase = None
        self._stop_event = None
        self._refresh_state()
        QMessageBox.critical(self, "校对失败", str(err))

    def _open_issues_dialog(self):
        issues_path = self._issues_file()
        if not os.path.exists(issues_path):
            QMessageBox.information(self, "提示", "未找到问题清单文件。请先执行 Find。")
            return
        dialog = ProofreadIssuesDialog(self, issues_path)
        dialog.exec()

    @staticmethod
    def _parse_csv_set(raw: str) -> set[str] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        items = []
        for part in text.split(","):
            t = part.strip()
            if t:
                items.append(t)
        return set(items) if items else None

    @staticmethod
    def _parse_csv_list(raw: str) -> list[str] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        items = []
        for part in text.split(","):
            t = part.strip()
            if t:
                items.append(t)
        return items if items else None

    def closeEvent(self, event):
        if self._is_thread_running():
            box = QMessageBox(self)
            box.setWindowTitle("任务仍在运行")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("校对任务仍在运行中。")
            btn_pause = box.addButton("暂停", QMessageBox.ButtonRole.AcceptRole)
            btn_force = box.addButton("强行停止", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = box.addButton("取消关闭", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_cancel)
            box.exec()

            clicked = box.clickedButton()
            if clicked == btn_pause:
                self._pause()
            elif clicked == btn_force:
                self._force_stop()
                event.accept()
                return
            event.ignore()
            return
        super().closeEvent(event)


class ProofreadIssuesDialog(QDialog):
    def __init__(self, parent, issues_path: str):
        super().__init__(parent)
        self.issues_path = issues_path
        self.issues: list[dict] = []
        self.setWindowTitle("校对问题列表")
        self.resize(980, 720)
        self._init_ui()
        self._load_issues()
        self._render()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("搜索:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("输入关键词（标题/描述/场景/类别）")
        self.search.textChanged.connect(self._render)
        top.addWidget(self.search, stretch=1)
        self.btn_reload = QPushButton("刷新")
        self.btn_reload.clicked.connect(self._reload)
        top.addWidget(self.btn_reload)
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["严重性", "类别", "Pass", "章节", "场景", "标题"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.table, stretch=2)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(220)
        layout.addWidget(self.detail, stretch=1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

    def _reload(self):
        self._load_issues()
        self._render()

    def _load_issues(self):
        try:
            import json

            with open(self.issues_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.issues = data
            elif isinstance(data, dict) and isinstance(data.get("issues"), list):
                self.issues = data.get("issues") or []
            else:
                self.issues = []
        except FileNotFoundError:
            QMessageBox.warning(self, "加载失败", f"问题清单文件不存在：\n{self.issues_path}")
            self.issues = []
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "加载失败", f"问题清单 JSON 解析失败：\n{e}")
            self.issues = []
        except Exception as e:
            QMessageBox.warning(self, "加载失败", f"无法读取问题清单：\n{e}")
            self.issues = []

    def _match(self, issue: dict, keyword: str) -> bool:
        if not keyword:
            return True
        kw = keyword.lower()
        loc = issue.get("location", {}) or {}
        fields = [
            str(issue.get("severity", "")),
            str(issue.get("category", "")),
            str(issue.get("pass_name", "")),
            str(issue.get("title", "")),
            str(issue.get("description", "")),
            str(loc.get("chapter_title", "")),
            str(loc.get("scene_title", "")),
        ]
        text = " ".join(fields).lower()
        return kw in text

    def _render(self):
        keyword = self.search.text().strip()
        filtered = [x for x in self.issues if isinstance(x, dict) and self._match(x, keyword)]

        self.table.setRowCount(len(filtered))
        for row, issue in enumerate(filtered):
            loc = issue.get("location", {}) or {}
            values = [
                str(issue.get("severity", "")),
                str(issue.get("category", "")),
                str(issue.get("pass_name", "")),
                str(loc.get("chapter_title", "")),
                str(loc.get("scene_title", "")),
                str(issue.get("title", "")),
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, issue)
                self.table.setItem(row, col, item)
        if filtered:
            self.table.selectRow(0)
        else:
            self.detail.setPlainText("")

    def _on_row_selected(self):
        items = self.table.selectedItems()
        if not items:
            self.detail.setPlainText("")
            return
        issue = items[0].data(Qt.ItemDataRole.UserRole)
        if not isinstance(issue, dict):
            self.detail.setPlainText("")
            return
        loc = issue.get("location", {}) or {}
        parts = [
            f"严重性: {issue.get('severity', '')}",
            f"类别: {issue.get('category', '')}",
            f"Pass: {issue.get('pass_name', '')}",
            f"标题: {issue.get('title', '')}",
            f"章节: {loc.get('chapter_title', '')}",
            f"小节: {loc.get('section_title', '')}",
            f"场景: {loc.get('scene_title', '')}",
            f"文件: {loc.get('file_path', '')}",
            f"行号: {loc.get('line_numbers', '')}",
            "",
            "描述:",
            str(issue.get("description", "") or ""),
            "",
            "建议:",
            str(issue.get("suggestion", "") or ""),
        ]
        evidence = issue.get("evidence", [])
        if evidence:
            parts.extend(["", "证据:"])
            for e in evidence:
                parts.append(f"- {e}")
        self.detail.setPlainText("\n".join(parts))
