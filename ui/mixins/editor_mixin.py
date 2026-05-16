"""
EditorMixin —— 保存 / 删除 / 全局保存 / 导出 / 字数统计。
"""
from __future__ import annotations

import json
import os
import uuid
import webbrowser
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QMessageBox

from ui.theme import NODE_NORMAL
from ui.workers import HtmlExportThread

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class EditorMixin:
    """编辑器区域的保存、删除、导出以及字数统计。"""

    def on_auto_export_www_toggled(self: "NovelCreatorWindow", checked: bool):
        self._auto_export_www_enabled = bool(checked)
        self._save_auto_export_www_enabled(self._auto_export_www_enabled)
        status = "开启" if self._auto_export_www_enabled else "关闭"
        self.log_console.append(f"自动更新 www 网页输出已{status}。")

    def on_smart_setting_selection_toggled(self: "NovelCreatorWindow", checked: bool):
        self._smart_setting_selection_enabled = bool(checked)
        self._save_smart_setting_selection_enabled(
            self._smart_setting_selection_enabled
        )
        status = "开启" if self._smart_setting_selection_enabled else "关闭"
        self.log_console.append(f"设定集智能勾选已{status}。")

    def _auto_export_html_if_enabled(self: "NovelCreatorWindow", reason: str = ""):
        if not getattr(self, "_auto_export_www_enabled", False):
            return
        if not self.workspace or not self.outline_tree_data:
            return
        if not hasattr(self, "_auto_export_timer"):
            self._auto_export_timer = QTimer(self)
            self._auto_export_timer.setSingleShot(True)
            self._auto_export_timer.timeout.connect(self._run_scheduled_auto_export_html)
            self._auto_export_reasons = []

        if reason:
            self._auto_export_reasons.append(reason)
        self._auto_export_timer.start(1200)

    def _run_scheduled_auto_export_html(self: "NovelCreatorWindow"):
        if not getattr(self, "_auto_export_www_enabled", False):
            return
        if not self.workspace or not self.outline_tree_data:
            return
        reasons = getattr(self, "_auto_export_reasons", [])
        reason = "、".join(sorted(set(reasons))) if reasons else "自动导出"
        self._auto_export_reasons = []
        self._start_html_export_async(
            reason=reason,
            prompt_open=False,
            notify_success=False,
            auto_mode=True,
        )

    def _start_html_export_async(
        self: "NovelCreatorWindow",
        reason: str = "",
        prompt_open: bool = False,
        notify_success: bool = False,
        auto_mode: bool = False,
    ) -> bool:
        if not self.workspace or not self.outline_tree_data:
            return False

        current_thread = getattr(self, "_html_export_thread", None)
        if current_thread and current_thread.isRunning():
            if not hasattr(self, "_pending_html_export_reasons"):
                self._pending_html_export_reasons = []
                self._pending_html_export_prompt_open = False
                self._pending_html_export_notify_success = False
                self._pending_html_export_has_manual = False

            if reason:
                self._pending_html_export_reasons.append(reason)
            self._pending_html_export_prompt_open = (
                self._pending_html_export_prompt_open or prompt_open
            )
            self._pending_html_export_notify_success = (
                self._pending_html_export_notify_success or notify_success
            )
            self._pending_html_export_has_manual = (
                self._pending_html_export_has_manual or (not auto_mode)
            )

            if not auto_mode:
                self.log_console.append(
                    "<font color='gray'>网页正在后台导出，已加入下一轮导出队列。</font>"
                )
            return False

        thread = HtmlExportThread(self.workspace, self)
        self._html_export_thread = thread
        self._html_export_context = {
            "reason": reason,
            "prompt_open": bool(prompt_open),
            "notify_success": bool(notify_success),
            "auto_mode": bool(auto_mode),
        }
        thread.success_signal.connect(self._on_html_export_success)
        thread.error_signal.connect(self._on_html_export_error)

        statusbar = self.statusBar()
        if statusbar is not None:
            statusbar.showMessage("正在后台导出网页...", 0)
        if not auto_mode:
            self.log_console.append("<font color='gray'>🌐 已开始后台导出网页...</font>")

        thread.start()
        return True

    def _on_html_export_success(self: "NovelCreatorWindow", output_file: str):
        ctx = getattr(self, "_html_export_context", {}) or {}
        auto_mode = bool(ctx.get("auto_mode", False))
        reason = str(ctx.get("reason", "")).strip()
        tip = f"（触发：{reason}）" if reason else ""

        if auto_mode:
            self.log_console.append(
                f"<font color='gray'>🔁 已自动更新网页输出: {output_file}{tip}</font>"
            )
        else:
            self.log_console.append(
                "<b><font color='green'>"
                f"\U0001f389 网页导出成功！文件已保存至: {output_file}{tip}"
                "</font></b>"
            )

        statusbar = self.statusBar()
        if statusbar is not None:
            statusbar.showMessage("网页导出完成", 2000)

        notify_success = bool(ctx.get("notify_success", False))
        prompt_open = bool(ctx.get("prompt_open", False))
        if notify_success:
            reply = QMessageBox.question(
                self,  # type: ignore[arg-type]
                "导出成功",
                "已成功在工程目录的 www 文件夹下生成网页版小说。\n"
                "是否立即在浏览器中打开预览？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                webbrowser.open(f"file://{os.path.abspath(output_file)}")
        elif prompt_open:
            webbrowser.open(f"file://{os.path.abspath(output_file)}")

        self._html_export_thread = None
        self._html_export_context = {}
        self._drain_pending_html_export()

    def _on_html_export_error(self: "NovelCreatorWindow", error_msg: str):
        ctx = getattr(self, "_html_export_context", {}) or {}
        auto_mode = bool(ctx.get("auto_mode", False))

        if auto_mode:
            self.log_console.append(
                f"<font color='orange'>自动更新网页输出失败: {error_msg}</font>"
            )
        else:
            QMessageBox.critical(
                self,  # type: ignore[arg-type]
                "导出错误",
                f"导出网页失败:\n{error_msg}",
            )
            self.log_console.append(
                f"<font color='red'>网页导出异常: {error_msg}</font>"
            )

        statusbar = self.statusBar()
        if statusbar is not None:
            statusbar.showMessage("网页导出失败", 3000)

        self._html_export_thread = None
        self._html_export_context = {}
        self._drain_pending_html_export()

    def _drain_pending_html_export(self: "NovelCreatorWindow"):
        reasons = getattr(self, "_pending_html_export_reasons", [])
        if not reasons and not getattr(self, "_pending_html_export_notify_success", False):
            return

        reason = "、".join(sorted(set(reasons))) if reasons else "队列导出"
        prompt_open = bool(getattr(self, "_pending_html_export_prompt_open", False))
        notify_success = bool(getattr(self, "_pending_html_export_notify_success", False))
        has_manual = bool(getattr(self, "_pending_html_export_has_manual", False))

        self._pending_html_export_reasons = []
        self._pending_html_export_prompt_open = False
        self._pending_html_export_notify_success = False
        self._pending_html_export_has_manual = False

        self._start_html_export_async(
            reason=reason,
            prompt_open=prompt_open,
            notify_success=notify_success,
            auto_mode=not has_manual,
        )

    def _calculate_node_word_count(self: "NovelCreatorWindow", node: dict) -> int:
        """递归计算节点及其所有子节点的字数总和"""
        total_count = 0
        
        # 如果是3级节点，计算自身字数
        file_path = node.get("file_path")
        if file_path:
            full_path = os.path.join(self.workspace.text_path, file_path)
            if os.path.exists(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        text = f.read()
                        clean_text = text.replace(" ", "").replace("\n", "").replace("\t", "")
                        total_count += len(clean_text)
                except Exception:
                    pass
        
        # 递归计算子节点
        for child in node.get("children", []):
            total_count += self._calculate_node_word_count(child)
        
        return total_count

    def update_word_count(self: "NovelCreatorWindow"):
        if not self.current_editing_node or not self.current_editing_item:
            self.word_count_label.setText("当前字数: 0")
            return
        
        from ui.utils import get_item_level
        node_level = get_item_level(self.current_editing_item)
        
        # 如果是1级或2级节点，计算所有子节点字数之和
        if node_level in [1, 2]:
            total_count = self._calculate_node_word_count(self.current_editing_node)
            self.word_count_label.setText(f"当前字数: {total_count}")
        else:
            # 3级节点保持原行为
            text = self.content_editor.toPlainText()
            clean_text = text.replace(" ", "").replace("\n", "").replace("\t", "")
            self.word_count_label.setText(f"当前字数: {len(clean_text)}")

    def save_current_node(self: "NovelCreatorWindow"):
        if not self.workspace:
            return

        if self.current_editing_node:
            with self.loading_ui("正在保存当前节点..."):
                self.current_editing_node["summary"] = self.summary_editor.toPlainText()

                if self.content_editor.isEnabled():
                    content = self.content_editor.toPlainText()
                    rel_path = self.current_editing_node.get("file_path")

                    if not rel_path:
                        rel_path = f"场景_{uuid.uuid4().hex[:8]}.md"
                        self.current_editing_node["file_path"] = rel_path

                    try:
                        new_md5 = self.workspace.save_markdown_file(rel_path, content)
                        self.current_editing_node["md5"] = new_md5
                        self.current_editing_node["_status"] = "ok"
                        # 安全地检查current_editing_item是否仍然有效并且是正确的类型
                        if self.current_editing_item:
                            try:
                                # 检查是否有setForeground方法，避免在错误类型上调用
                                if hasattr(self.current_editing_item, 'setForeground'):
                                    self.current_editing_item.setForeground(
                                        0, QColor(NODE_NORMAL)
                                    )
                            except (RuntimeError, AttributeError):
                                # 如果item已被删除或类型错误，忽略这个错误
                                pass
                    except Exception as e:
                        QMessageBox.critical(
                            self,  # type: ignore[arg-type]
                            "错误",
                            f"保存正文文件失败:\n{e}",
                        )
                        return

                try:
                    self.workspace.save_outline_tree(self.outline_tree_data)
                    self.log_console.append(
                        f"\U0001f4be 节点保存成功: "
                        f"{self.current_editing_node.get('title')}"
                    )
                    self._auto_export_html_if_enabled("保存当前节点")
                    # 更新原始状态，避免切换节点时重复提示保存
                    self.current_node_original_summary = self.summary_editor.toPlainText()
                    if self.content_editor.isEnabled():
                        self.current_node_original_content = self.content_editor.toPlainText()
                except Exception as e:
                    QMessageBox.critical(
                        self, "错误", f"保存大纲树失败:\n{e}"  # type: ignore[arg-type]
                    )
            return

        if self.current_setting_path and os.path.exists(self.current_setting_path):
            try:
                parsed_json = json.loads(self.summary_editor.toPlainText())
                with open(self.current_setting_path, "w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, ensure_ascii=False, indent=4)
                self.log_console.append(
                    f"\U0001f4be 设定保存成功: "
                    f"{os.path.basename(self.current_setting_path)}"
                )
            except json.JSONDecodeError as e:
                QMessageBox.warning(
                    self,  # type: ignore[arg-type]
                    "JSON 格式错误",
                    f"保存失败！请检查 JSON 格式:\n{e}",
                )
            return

    def delete_current_node(self: "NovelCreatorWindow"):
        if not self.current_editing_node or not self.outline_tree_data:
            return

        if self.current_editing_node.get("children"):
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "不可删除",
                "当前节点包含子章节或场景，请先删除底层的子节点！",
            )
            return

        node_title = self.current_editing_node.get("title", "未知节点")
        reply = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "确认删除",
            f"确定要永久删除节点【{node_title}】吗？\n"
            "警告：对应的 Markdown 文件也将被彻底删除！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            target_node = self.current_editing_node

            def remove_from_list(nodes_list):
                for i, node in enumerate(nodes_list):
                    if node is target_node:
                        del nodes_list[i]
                        return True
                    if remove_from_list(node.get("children", [])):
                        return True
                return False

            is_removed = remove_from_list(
                self.outline_tree_data.get("nodes", [])
            )

            if is_removed:
                rel_path = target_node.get("file_path")
                if rel_path:
                    full_path = os.path.join(self.workspace.text_path, rel_path)
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                        except Exception as e:
                            self.log_console.append(
                                f"<font color='orange'>警告：无法删除本地文件 "
                                f"{rel_path} ({e})</font>"
                            )

                try:
                    with open(
                        self.workspace.tree_json_file, "w", encoding="utf-8"
                    ) as f:
                        json.dump(
                            self.outline_tree_data, f, ensure_ascii=False, indent=4
                        )

                    self.log_console.append(
                        f"\U0001f5d1\ufe0f 成功删除节点: {node_title}"
                    )
                    self._auto_export_html_if_enabled("删除节点")

                    self.current_editing_node = None

                    self.summary_editor.clear()
                    self.summary_editor.setEnabled(False)
                    self.content_editor.clear()
                    self.content_editor.setEnabled(False)
                    self.btn_save.setEnabled(False)
                    self.btn_generate.setEnabled(False)
                    self.btn_delete.setEnabled(False)

                    self.refresh_ui_from_workspace()
                except Exception as e:
                    QMessageBox.critical(
                        self,  # type: ignore[arg-type]
                        "错误",
                        f"保存系统大纲 JSON 失败:\n{e}",
                    )
            else:
                QMessageBox.warning(
                    self,  # type: ignore[arg-type]
                    "错误",
                    "在大纲树中未找到该节点，删除操作中断。",
                )

    def save_all(self: "NovelCreatorWindow"):
        if self.workspace and self.outline_tree_data:
            if self.current_editing_node and self.content_editor.isEnabled():
                self.save_current_node()
            else:
                with self.loading_ui("正在保存大纲..."):
                    self.workspace.save_outline_tree(self.outline_tree_data)

            self.log_console.append(
                "<b><font color='green'>"
                "[系统通知] 执行全局保存成功 (Ctrl+S)。"
                "</font></b>"
            )
            statusbar = self.statusBar()
            if statusbar is not None:
                statusbar.showMessage("全局保存成功", 1000)
        else:
            self.log_console.append(
                "<font color='orange'>全局保存跳过：当前没有打开工作区。</font>"
            )

    def export_to_html(self: "NovelCreatorWindow"):
        """处理导出 HTML 网页的逻辑"""
        if not self.workspace or not self.outline_tree_data:
            QMessageBox.warning(
                self, "操作失败", "请先加载或新建一个工作区！"  # type: ignore[arg-type]
            )
            return

        self.save_all()
        self._start_html_export_async(
            reason="手动导出",
            prompt_open=False,
            notify_success=True,
            auto_mode=False,
        )
