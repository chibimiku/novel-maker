"""
EditorMixin —— 保存 / 删除 / 全局保存 / 导出 / 字数统计。
"""
from __future__ import annotations

import json
import os
import uuid
import webbrowser
from typing import TYPE_CHECKING

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QMessageBox

from core.html_exporter import HtmlExporter
from ui.theme import NODE_NORMAL

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class EditorMixin:
    """编辑器区域的保存、删除、导出以及字数统计。"""

    def update_word_count(self: "NovelCreatorWindow"):
        text = self.content_editor.toPlainText()
        clean_text = text.replace(" ", "").replace("\n", "").replace("\t", "")
        self.word_count_label.setText(f"当前字数: {len(clean_text)}")

    def save_current_node(self: "NovelCreatorWindow"):
        if not self.workspace:
            return

        if self.current_editing_node:
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
                    if self.current_editing_item:
                        self.current_editing_item.setForeground(
                            0, QColor(NODE_NORMAL)
                        )
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
            self.workspace.save_outline_tree(self.outline_tree_data)

            if self.current_editing_node and self.content_editor.isEnabled():
                self.save_current_node()

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

        try:
            exporter = HtmlExporter(self.workspace)
            output_file = exporter.export()

            self.log_console.append(
                "<b><font color='green'>"
                f"\U0001f389 网页导出成功！文件已保存至: {output_file}"
                "</font></b>"
            )

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

        except Exception as e:
            QMessageBox.critical(
                self,  # type: ignore[arg-type]
                "导出错误",
                f"导出网页失败:\n{str(e)}",
            )
            self.log_console.append(
                f"<font color='red'>网页导出异常: {e}</font>"
            )
