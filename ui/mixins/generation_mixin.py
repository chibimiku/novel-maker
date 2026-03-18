"""
GenerationMixin —— AI 单节点生成、重写与批量生成逻辑。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QMessageBox

from core.context_builder import ContextBuilder
from ui.utils import find_item_by_data, get_missing_level3_nodes
from ui.workers import GenerateTaskThread

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class GenerationMixin:
    """处理 AI 生成正文、重写正文以及批量生成缺失场景。"""

    # ================= AI 生成核心逻辑 ================= #

    def generate_current_node(self: "NovelCreatorWindow"):
        if not self.current_editing_node or not self.outline_tree_data:
            return

        if not self.llm_client:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "配置缺失",
                "尚未初始化大模型客户端，请检查 conf/setting.json 文件。",
            )
            return

        self.save_current_node()

        node_title = self.current_editing_node.get("title", "未知节点")
        self.log_console.append(f"开始构建【{node_title}】的上下文...")

        self.btn_generate.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)

        builder = ContextBuilder(self.workspace)
        checked_paths = self.get_checked_settings()

        messages = builder.build_generation_prompt(
            self.current_editing_node,
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
        if not self.current_editing_node:
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

        node_title = self.current_editing_node.get("title", "未知节点")
        self.log_console.append(f"开始构建【{node_title}】的重写请求...")

        builder = ContextBuilder(self.workspace)
        checked_paths = self.get_checked_settings()

        messages = builder.build_rewrite_prompt(
            self.current_editing_node,
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
        self.content_editor.setText(result)
        self.log_console.append("生成成功！已填入编辑器并自动保存。")

        self.save_current_node()

        self._restore_generate_ui_state()

        if self.is_batch_generating:
            self._process_next_batch_node()

    def on_generate_error(self: "NovelCreatorWindow", error_msg: str):
        self.log_console.append(
            f"<font color='red'>生成失败: {error_msg}</font>"
        )
        if not self.is_batch_generating:
            QMessageBox.critical(
                self,  # type: ignore[arg-type]
                "生成错误",
                f"大模型请求失败:\n{error_msg}",
            )
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
            self.btn_batch_generate.setText("\U0001f6d1 停止批量生成")
            self._process_next_batch_node()

    def _process_next_batch_node(self: "NovelCreatorWindow"):
        if not self.is_batch_generating:
            return

        if not self.batch_generate_queue:
            self.is_batch_generating = False
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

        self.generate_current_node()

        self.content_editor.setText("（自动批量生成中，请稍候...）")
