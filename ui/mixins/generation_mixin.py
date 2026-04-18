"""
GenerationMixin —— AI 单节点生成、重写与批量生成逻辑。
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QStyle, QSystemTrayIcon

from core.context_builder import ContextBuilder
from ui.utils import find_item_by_data, get_missing_level3_nodes
from ui.workers import GenerateTaskThread

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class GenerationMixin:
    """处理 AI 生成正文、重写正文以及批量生成缺失场景。"""

    def _send_system_notification(
        self: "NovelCreatorWindow",
        title: str,
        message: str,
    ):
        """发送系统托盘通知；托盘不可用时回退到日志提示。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.log_console.append(
                f"系统通知（托盘不可用）: {title} - {message}"
            )
            return

        tray_icon = getattr(self, "_system_tray_icon", None)
        if tray_icon is None:
            app = QApplication.instance()
            style = app.style() if app else self.style()
            icon = self.windowIcon()
            if icon.isNull():
                icon = style.standardIcon(
                    QStyle.StandardPixmap.SP_MessageBoxInformation
                )
            tray_icon = QSystemTrayIcon(icon, self)  # type: ignore[arg-type]
            tray_icon.setToolTip("AI小说创作器")
            tray_icon.show()
            self._system_tray_icon = tray_icon

        tray_icon.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    # ================= AI 生成核心逻辑 ================= #
    def _create_context_builder(self: "NovelCreatorWindow") -> ContextBuilder:
        text_cfg = (self.config or {}).get("text_api", {})
        return ContextBuilder(
            self.workspace,
            model_context_size=text_cfg.get("model_context_size"),
            model_capabilities=text_cfg.get("model_capabilities", []),
            compression_profile=text_cfg.get("world_context_compression_profile", "balanced"),
        )
    
    def _get_current_node_from_tree(self: "NovelCreatorWindow"):
        """从当前选中的树节点获取最新的节点对象"""
        if not self.current_editing_item:
            return None
        node_id = self.current_editing_item.data(0, Qt.ItemDataRole.UserRole)
        if node_id and node_id in self.node_map:
            return self.node_map[node_id]
        return None

    def generate_current_node(self: "NovelCreatorWindow"):
        current_node = self._get_current_node_from_tree() or self.current_editing_node
        if not current_node or not self.outline_tree_data:
            return

        if not self.llm_client:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "配置缺失",
                "尚未初始化大模型客户端，请检查 conf/setting.json 文件。",
            )
            return

        # 保存当前内容到回退缓冲区
        current_content = self.content_editor.toPlainText()
        self._save_to_undo_stack('content', current_content)

        self.save_current_node()

        node_title = current_node.get("title", "未知节点")
        self.log_console.append(f"开始构建【{node_title}】的上下文...")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("正在构建上下文并发送请求到LLM...")

        self.btn_generate.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)

        builder = self._create_context_builder()
        checked_paths = self.get_checked_settings()

        messages = builder.build_generation_prompt(
            current_node,
            self.outline_tree_data,
            checked_paths,
            generate_image=self.cb_gen_image.isChecked(),
            word_count=self.spin_word_count.value(),
            include_next=self.cb_include_next.isChecked(),
        )

        prompt_content = messages[-1]["content"]

        self.log_console.append(
            "========== [System] 模型系统指令 (Instructions) =========="
        )
        self.log_console.append(self.llm_client.system_instruction)
        self.log_console.append(
            "========== [User] 上下文与生成提示词 =========="
        )
        self.log_console.append(prompt_content)
        self.log_console.append("=================================================")
        self.log_console.append("发送请求至大语言模型，后台处理中，请稍候...")

        self.generate_thread = GenerateTaskThread(self.llm_client, prompt_content)
        self.generate_thread.success_signal.connect(self.on_generate_success)
        self.generate_thread.error_signal.connect(self.on_generate_error)
        self.generate_thread.start()

    def rewrite_current_node(self: "NovelCreatorWindow"):
        current_node = self._get_current_node_from_tree() or self.current_editing_node
        if not current_node:
            return

        target_content = self.content_editor.toPlainText().strip()
        if not target_content or (
            target_content.startswith("#") and len(target_content.split("\n")) <= 3
        ):
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "无法重写",
                "当前节点尚未生成有效正文内容。\n"
                "请先使用【结合上下文生成正文】或手动输入一段基础剧情。",
            )
            return

        target_word_count = self.spin_word_count.value()

        reply = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "确认重写",
            f"确定要将当前节点的正文重写/扩写为约 {target_word_count} 字吗？\n"
            "警告：生成成功后，现有的正文将被不可逆地完全覆盖！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        self.save_current_node()

        self.btn_generate.setEnabled(False)
        self.btn_rewrite.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)

        node_title = current_node.get("title", "未知节点")
        self.log_console.append(f"开始构建【{node_title}】的重写请求...")

        builder = self._create_context_builder()
        checked_paths = self.get_checked_settings()

        messages = builder.build_rewrite_prompt(
            current_node,
            self.outline_tree_data,
            checked_paths,
            target_word_count,
        )

        prompt_content = messages[-1]["content"]

        self.log_console.append("========== [User] 重写提示词 ==========")
        self.log_console.append(prompt_content)
        self.log_console.append(
            "发送重写请求至大模型，后台处理中，请耐心稍候..."
        )

        self.generate_thread = GenerateTaskThread(self.llm_client, prompt_content)
        self.generate_thread.success_signal.connect(self.on_generate_success)
        self.generate_thread.error_signal.connect(self.on_generate_error)
        self.generate_thread.start()

    def on_generate_success(self: "NovelCreatorWindow", result: str):
        node_title = (
            (self.current_editing_node or {}).get("title", "当前节点")
            if self.current_editing_node
            else "当前节点"
        )
        self.content_editor.setText(result)
        self.log_console.append("生成成功！已填入编辑器并自动保存。")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("LLM 生成成功，已更新编辑器内容", 3000)

        self.save_current_node()

        self._restore_generate_ui_state()

        if self.is_batch_generating:
            self._process_next_batch_node()
            return

        if self._is_batch_generation_context:
            self._is_batch_generation_context = False
            return

        self._send_system_notification(
            "生成完成",
            f"《{node_title}》正文已生成完成。",
        )

    def regenerate_summary(self: "NovelCreatorWindow"):
        """重新生成当前节点的概要(Summary)"""
        current_node = self._get_current_node_from_tree() or self.current_editing_node
        if not current_node or not self.outline_tree_data:
            return

        if not self.llm_client:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "配置缺失",
                "尚未初始化大模型客户端，请检查 conf/setting.json 文件。",
            )
            return

        # 防止重复点击触发并发请求，避免后返回结果覆盖先返回结果
        if self.generate_thread is not None:
            self.log_console.append("已有生成任务在进行中，请等待当前任务完成。")
            return

        # 保存当前选中节点的 ID，用于在生成后恢复选择
        target_node_id = current_node.get("id")
        self._current_regenerating_node_id = target_node_id
        request_id = uuid.uuid4().hex
        self._active_summary_regen_request_id = request_id

        # 保存当前概要到回退缓冲区
        current_summary = self.summary_editor.toPlainText()
        self._save_to_undo_stack('summary', current_summary)
        # 先落盘当前节点内容，确保概要生成读取到最新正文
        self.save_current_node()

        node_title = current_node.get("title", "未知节点")
        self.log_console.append(f"开始重新生成【{node_title}】的概要...")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("正在发送请求到LLM生成概要...")

        # 禁用相关按钮
        self.btn_generate.setEnabled(False)
        self.btn_rewrite.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_regenerate_summary.setEnabled(False)

        builder = self._create_context_builder()
        checked_paths = self.get_checked_settings()

        messages = builder.build_summary_prompt(
            current_node,
            self.outline_tree_data,
            checked_paths,
        )

        prompt_content = messages[-1]["content"]

        self.log_console.append(
            "========== [System] 概要生成系统指令 (Summary Instructions) =========="
        )
        self.log_console.append(self.llm_client.summary_system_instruction)
        self.log_console.append(
            "========== [User] 生成概要提示词 =========="
        )
        self.log_console.append(prompt_content)
        self.log_console.append("=================================================")
        self.log_console.append("发送请求至大语言模型，后台处理中，请稍候...")

        self.generate_thread = GenerateTaskThread(
            self.llm_client,
            prompt_content,
            self.llm_client.summary_system_instruction,
        )
        self.generate_thread._summary_request_id = request_id  # type: ignore[attr-defined]
        self.generate_thread._summary_target_node_id = target_node_id  # type: ignore[attr-defined]
        self.generate_thread.success_signal.connect(self.on_summary_generate_success)
        self.generate_thread.error_signal.connect(self.on_generate_error)
        self.generate_thread.start()

    def on_summary_generate_success(self: "NovelCreatorWindow", result: str):
        """处理概要生成成功的回调"""
        sender_thread = self.sender()
        callback_request_id = getattr(sender_thread, "_summary_request_id", None)
        active_request_id = getattr(self, "_active_summary_regen_request_id", None)
        # 忽略过期回调，防止并发请求导致旧结果覆盖新结果
        if callback_request_id and active_request_id and callback_request_id != active_request_id:
            self.log_console.append("检测到过期概要回调，已自动忽略。")
            return

        target_node_id = (
            getattr(sender_thread, "_summary_target_node_id", None)
            or getattr(self, "_current_regenerating_node_id", None)
        )
        target_node = self.node_map.get(target_node_id) if target_node_id else None
        if not target_node:
            self.log_console.append("<font color='orange'>概要生成完成，但目标节点不存在，未写回。</font>")
            self._active_summary_regen_request_id = None
            self._restore_generate_ui_state()
            return

        target_node["summary"] = result
        current_node = self._get_current_node_from_tree() or self.current_editing_node
        # 仅当当前编辑的就是目标节点时，才刷新编辑器内容，避免误改用户当前操作节点
        if current_node is target_node:
            self.summary_editor.setText(result)
            self.current_node_original_summary = result

        if self.workspace and self.outline_tree_data:
            self.workspace.save_outline_tree(self.outline_tree_data)
        self.log_console.append("概要生成成功！已写回目标节点并保存。")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("概要生成成功，已更新编辑器内容", 3000)

        self._active_summary_regen_request_id = None
        self._restore_generate_ui_state()

    def on_generate_error(self: "NovelCreatorWindow", error_msg: str):
        self.log_console.append(
            f"<font color='red'>生成失败: {error_msg}</font>"
        )
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage(f"LLM 请求失败: {error_msg[:50]}...", 3000)
        
        if not self.is_batch_generating:
            QMessageBox.critical(
                self,  # type: ignore[arg-type]
                "生成错误",
                f"大模型请求失败:\n{error_msg}",
            )
        self._active_summary_regen_request_id = None
        self._restore_generate_ui_state()

        if self.is_batch_generating:
            self.log_console.append(
                "<font color='orange'>"
                "\u26a0\ufe0f 当前节点生成失败，跳过并处理下一个..."
                "</font>"
            )
            self._process_next_batch_node()

    def _restore_generate_ui_state(self: "NovelCreatorWindow"):
        if not self.is_batch_generating:
            self.btn_generate.setEnabled(True)
            self.btn_save.setEnabled(True)
            self.btn_rewrite.setEnabled(True)
            self.btn_regenerate_summary.setEnabled(True)
            if self.current_editing_node:
                self.btn_delete.setEnabled(
                    not bool(self.current_editing_node.get("children"))
                )
        self.generate_thread = None

    # ================= 批量生成 ================= #

    def start_batch_generate(self: "NovelCreatorWindow"):
        if self.is_batch_generating:
            self.is_batch_generating = False
            self.batch_generate_queue.clear()
            self.btn_batch_generate.setText("\U0001f680 批量生成缺失场景")
            self.log_console.append(
                "<font color='orange'>"
                "\u26a0\ufe0f 已发送停止指令，将在当前节点完成后终止批量任务。"
                "</font>"
            )
            return

        if not self.workspace or not self.outline_tree_data:
            QMessageBox.warning(
                self, "提示", "请先打开并加载一个工作区！"  # type: ignore[arg-type]
            )
            return

        missing_nodes = get_missing_level3_nodes(
            self.outline_tree_data.get("nodes", [])
        )
        if not missing_nodes:
            QMessageBox.information(
                self,  # type: ignore[arg-type]
                "提示",
                "当前没有缺失正文的场景节点（没有灰色节点）。",
            )
            return

        reply = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "确认批量生成",
            f"大纲中找到了 {len(missing_nodes)} 个缺失文件（灰色）的场景。\n"
            "确认开始依次自动生成吗？这可能需要较长时间。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.batch_generate_queue = missing_nodes
            self.is_batch_generating = True
            self._is_batch_generation_context = True
            self.btn_batch_generate.setText("\U0001f6d1 停止批量生成")
            self._process_next_batch_node()

    def _process_next_batch_node(self: "NovelCreatorWindow"):
        if not self.is_batch_generating:
            self._is_batch_generation_context = False
            return

        if not self.batch_generate_queue:
            self.is_batch_generating = False
            self._is_batch_generation_context = False
            self.btn_batch_generate.setText("\U0001f680 批量生成缺失场景")
            self.btn_generate.setEnabled(True)
            self.btn_save.setEnabled(True)
            self.log_console.append(
                "<b><font color='green'>"
                "\U0001f389 批量生成任务全部完成！"
                "</font></b>"
            )
            QMessageBox.information(
                self, "完成", "批量生成已结束。"  # type: ignore[arg-type]
            )
            self._send_system_notification(
                "批量生成完成",
                "批量生成缺失场景任务已全部完成。",
            )
            return

        next_node = self.batch_generate_queue.pop(0)
        self.log_console.append(
            f"<hr><b>\u23f3 正在自动处理节点: {next_node.get('title')} "
            f"(队列剩余 {len(self.batch_generate_queue)} 个)</b>"
        )

        self.current_editing_node = next_node
        self.current_setting_path = None

        self.summary_editor.setText(next_node.get("summary", ""))
        self.summary_editor.setEnabled(True)

        self.content_editor.setText(f"# {next_node.get('title')}\n\n")
        self.content_editor.setEnabled(True)

        node_id = None
        for nid, n in self.node_map.items():
            if n is next_node:
                node_id = nid
                break

        if node_id:
            item = find_item_by_data(self.novel_tree.invisibleRootItem(), node_id)
            if item:
                self.current_editing_item = item
                self.novel_tree.setCurrentItem(item)

        self._is_batch_generation_context = True
        self.generate_current_node()

        self.content_editor.setText("（自动批量生成中，请稍候...）")
