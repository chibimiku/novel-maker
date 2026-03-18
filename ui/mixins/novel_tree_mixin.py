"""
NovelTreeMixin —— 小说大纲树的渲染、节点交互、拖拽同步、大纲生成。
"""
from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QInputDialog,
    QMenu,
    QMessageBox,
    QTreeWidgetItem,
)

from ui.theme import NODE_ADD_BTN, NODE_ERROR, NODE_MISSING, NODE_NORMAL
from ui.dialogs import IdeaInputDialog, RenameNodeDialog
from ui.utils import find_duplicate_paths, get_item_level
from ui.workers import OutlineBuildingThread
from core.context_builder import ContextBuilder

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class NovelTreeMixin:
    """小说大纲树的渲染、节点交互与大纲自动生成。"""

    # ================= 小说树渲染 ================= #

    def _refresh_novel_tree(self: "NovelCreatorWindow"):
        """渲染右侧的小说大纲目录树。"""
        self.novel_tree.clear()
        self.node_map.clear()
        self.novel_tree.setHeaderLabel("小说大纲结构")
        self.outline_tree_data = self.workspace.load_outline_tree()

        if "nodes" not in self.outline_tree_data:
            self.outline_tree_data["nodes"] = []
        nodes_ref = self.outline_tree_data["nodes"]

        self._duplicate_paths = find_duplicate_paths(nodes_ref)
        if self._duplicate_paths:
            dup_list = ", ".join(self._duplicate_paths)
            self.log_console.append(
                "<font color='red'><b>"
                "\u26a0\ufe0f 严重警告：检测到多个场景节点指向相同的物理文件！"
                "可能导致剧情覆盖或丢失。<br>"
                f"冲突的底层文件列表：{dup_list}<br>"
                "请手动在冲突的节点中点击\u201c保存当前节点\u201d"
                "以重新生成独立的 MD 文件绑定。"
                "</b></font>"
            )
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "节点冲突警告",
                "检测到多个大纲节点共用了同一个本地 Markdown 文件"
                "（树状图中已标红）。\n请留意控制台警告，并手动编辑处理冲突！",
            )

        self._build_novel_tree_ui(nodes_ref, self.novel_tree, level=1)
        self.novel_tree.expandAll()

    def _build_novel_tree_ui(
        self: "NovelCreatorWindow", nodes: list, parent_widget, level: int = 1
    ):
        # 场景的内部（第4级），严格禁止渲染任何子节点和按钮
        if level > 3:
            return

        for node in nodes:
            title = node.get("title", "未命名节点")
            item = QTreeWidgetItem(parent_widget, [title])

            if level == 3:
                item.setFlags(
                    (item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
                    & ~Qt.ItemFlag.ItemIsDropEnabled
                )
            else:
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsDropEnabled
                )

            node_id = str(uuid.uuid4())
            self.node_map[node_id] = node
            item.setData(0, Qt.ItemDataRole.UserRole, node_id)

            status = node.get("_status", "ok")
            file_path = node.get("file_path")

            if level == 3:
                if (
                    file_path
                    and getattr(self, "_duplicate_paths", None)
                    and file_path in self._duplicate_paths
                ):
                    item.setForeground(0, QColor(NODE_ERROR))
                    node["_status"] = "duplicate_conflict"
                elif status == "missing":
                    item.setForeground(0, QColor(NODE_MISSING))
                elif status == "modified_externally":
                    item.setForeground(0, QColor(NODE_ERROR))
                else:
                    item.setForeground(0, QColor(NODE_NORMAL))
            else:
                item.setForeground(0, QColor(NODE_NORMAL))

            if "children" not in node:
                node["children"] = []

            self._build_novel_tree_ui(node["children"], item, level + 1)

        titles = {1: "+ 新增章...", 2: "+ 新增节...", 3: "+ 新增场景..."}
        btn_text = titles.get(level, "+ 新增节点...")

        add_btn = QTreeWidgetItem(parent_widget, [btn_text])
        add_btn.setForeground(0, QColor(NODE_ADD_BTN))
        add_btn.setFlags(
            add_btn.flags()
            & ~Qt.ItemFlag.ItemIsDragEnabled
            & ~Qt.ItemFlag.ItemIsDropEnabled
        )

    # ================= 树结构同步辅助 ================= #

    def _cleanup_tree_add_buttons(self: "NovelCreatorWindow", parent_item=None):
        target = (
            parent_item if parent_item else self.novel_tree.invisibleRootItem()
        )

        add_btn_index = -1
        for i in range(target.childCount()):
            child = target.child(i)
            if child.text(0).startswith("+"):
                add_btn_index = i
                break

        if add_btn_index != -1 and add_btn_index < target.childCount() - 1:
            add_btn = target.takeChild(add_btn_index)
            target.addChild(add_btn)

        for i in range(target.childCount()):
            child = target.child(i)
            if not child.text(0).startswith("+"):
                self._cleanup_tree_add_buttons(child)

    def sync_tree_data_from_ui(self: "NovelCreatorWindow"):
        if not self.workspace or self.outline_tree_data is None:
            return

        new_nodes: list = []
        root = self.novel_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.text(0).startswith("+"):
                continue
            node_data = self._build_node_data_from_item(item)
            if node_data:
                new_nodes.append(node_data)

        self.outline_tree_data["nodes"] = new_nodes
        self.workspace.save_outline_tree(self.outline_tree_data)
        self.log_console.append("系统通知：节点位置结构已自动保存。")

    def _build_node_data_from_item(self: "NovelCreatorWindow", item):
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        node_data = self.node_map.get(node_id)
        if not node_data:
            return None

        new_children: list = []
        for i in range(item.childCount()):
            child_item = item.child(i)
            if child_item.text(0).startswith("+"):
                continue
            child_data = self._build_node_data_from_item(child_item)
            if child_data:
                new_children.append(child_data)

        node_data["children"] = new_children
        return node_data

    # ================= 小说大纲树交互 ================= #

    def on_novel_node_clicked(self: "NovelCreatorWindow", item, column):
        if not self.workspace:
            return

        if self.is_batch_generating:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "提示",
                "批量生成中，请先停止任务后再手动操作节点。",
            )
            return

        if item.text(0).startswith("+"):
            parent_item = item.parent()
            if parent_item:
                parent_node_id = parent_item.data(0, Qt.ItemDataRole.UserRole)
                real_parent_node = self.node_map.get(parent_node_id)
                if real_parent_node is not None:
                    target_list = real_parent_node.setdefault("children", [])
                    parent_level = get_item_level(parent_item)
                    self.add_new_novel_node(target_list, parent_level + 1)
            else:
                if self.outline_tree_data is None:
                    self.outline_tree_data = {"nodes": []}
                target_list = self.outline_tree_data.setdefault("nodes", [])
                self.add_new_novel_node(target_list, 1)
            return

        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        real_node = self.node_map.get(node_id)
        if not real_node:
            return

        self.current_editing_node = real_node
        self.current_editing_item = item
        self.current_setting_path = None
        node_level = get_item_level(item)

        self.btn_delete.setEnabled(not bool(real_node.get("children")))

        # 1. 加载概要
        self.summary_editor.setText(real_node.get("summary", ""))
        self.summary_editor.setEnabled(True)
        self.btn_save.setEnabled(True)

        # 2. 加载正文 (仅对第3级开放)
        if node_level == 3:
            rel_path = real_node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                if os.path.exists(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        self.content_editor.setText(f.read())
                else:
                    self.content_editor.setText(
                        f"# {real_node.get('title')}\n\n(文件尚未生成)"
                    )
            else:
                self.content_editor.setText(
                    f"# {real_node.get('title')}\n\n"
                    "(尚未配置正文路径，在此输入内容并保存后将自动生成)"
                )

            self.content_editor.setEnabled(True)
            self.btn_generate.setEnabled(True)
            self.btn_rewrite.setEnabled(True)
        else:
            self.content_editor.setText(
                "（当前层级仅支持填写概要，正文请在底层的"
                "\u201c场景\u201d节点中生成/编写）"
            )
            self.content_editor.setEnabled(False)
            self.btn_generate.setEnabled(False)
            self.btn_rewrite.setEnabled(False)

        self.update_word_count()

    def add_new_novel_node(self: "NovelCreatorWindow", target_list: list, level: int):
        titles = {1: "章", 2: "节", 3: "场景"}
        node_type = titles.get(level, "节点")

        title, ok = QInputDialog.getText(
            self, f"新增{node_type}", f"请输入新{node_type}名称:"  # type: ignore[arg-type]
        )
        if ok and title.strip():
            title = title.strip()

            new_node = {
                "title": title,
                "summary": "",
                "children": [],
                "_status": "ok",
            }

            if level == 3:
                file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
                initial_content = f"# {title}\n\n请在此输入正文...\n"
                try:
                    initial_md5 = self.workspace.save_markdown_file(
                        file_name, initial_content
                    )
                    new_node["file_path"] = file_name
                    new_node["md5"] = initial_md5
                except Exception as e:
                    QMessageBox.critical(
                        self,  # type: ignore[arg-type]
                        "错误",
                        f"创建本地物理文件失败: {e}",
                    )
                    return

            target_list.append(new_node)

            if (
                "nodes" not in self.outline_tree_data
                or not self.outline_tree_data["nodes"]
            ):
                self.outline_tree_data["nodes"] = target_list

            try:
                with open(
                    self.workspace.tree_json_file, "w", encoding="utf-8"
                ) as f:
                    json.dump(self.outline_tree_data, f, ensure_ascii=False, indent=4)
                self.log_console.append(f"成功添加{node_type}: {title}")
                self.refresh_ui_from_workspace()
            except Exception as e:
                QMessageBox.critical(
                    self, "错误", f"保存大纲 JSON 失败:\n{e}"  # type: ignore[arg-type]
                )

    def rename_current_node(self: "NovelCreatorWindow"):
        item = self.novel_tree.currentItem()
        if not item or item.text(0).startswith("+"):
            return

        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        real_node = self.node_map.get(node_id)
        if not real_node:
            return

        old_title = real_node.get("title", "")
        dialog = RenameNodeDialog(
            self, "重命名节点", "请输入新的节点名称:", default_text=old_title  # type: ignore[arg-type]
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_title = dialog.get_text()
            if new_title.strip() and new_title.strip() != old_title:
                clean_title = new_title.strip()
                real_node["title"] = clean_title
                item.setText(0, clean_title)

                if self.workspace and self.outline_tree_data:
                    self.workspace.save_outline_tree(self.outline_tree_data)
                    self.log_console.append(
                        f"\U0001f504 节点已重命名: 【{old_title}】 -> 【{clean_title}】"
                        " (已自动保存大纲)"
                    )

    # ================= 小说大纲右键菜单 ================= #

    def show_novel_context_menu(self: "NovelCreatorWindow", position):
        if not self.workspace:
            return

        menu = QMenu()
        gen_outline_action = menu.addAction(
            "\U0001f4a1 结合当前点子与左侧勾选设定，自动生成大纲"
        )
        gen_outline_action.triggered.connect(self.open_outline_building_dialog)

        menu.exec(self.novel_tree.viewport().mapToGlobal(position))

    def open_outline_building_dialog(self: "NovelCreatorWindow"):
        if not self.llm_client:
            QMessageBox.warning(
                self, "未配置", "请先在设置中配置大模型 API。"  # type: ignore[arg-type]
            )
            return

        dialog = IdeaInputDialog(
            self,  # type: ignore[arg-type]
            "自动生成小说大纲",
            "请输入关于大纲的剧情发展点子、主线走向或期待的章节结构：\n"
            "（左侧打钩的世界观设定也会作为参考上下文发送给AI）",
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            idea = dialog.get_text()
            if idea.strip():
                self.log_console.append("启动大纲生成引擎。后台处理中，请稍候...")
                self.btn_save.setEnabled(False)

                checked_paths = self.get_checked_settings()
                builder = ContextBuilder(self.workspace)
                settings_text = builder._build_settings_text(checked_paths)

                default_prompt = ""
                prompt_tpl = self._get_or_create_prompt_template(
                    "outline_build.txt", default_prompt, "根据点子和设定生成大纲"
                )

                self.ob_thread = OutlineBuildingThread(
                    llm_client=self.llm_client,
                    idea=idea.strip(),
                    settings_text=settings_text,
                    prompt_tpl=prompt_tpl,
                )
                self.ob_thread.progress_signal.connect(
                    lambda msg: self.log_console.append(
                        f"<font color='cyan'>{msg}</font>"
                    )
                )
                self.ob_thread.success_signal.connect(
                    self.on_outline_building_success
                )
                self.ob_thread.error_signal.connect(self.on_outline_building_error)
                self.ob_thread.start()

    def on_outline_building_success(self: "NovelCreatorWindow", outline_data):
        def process_nodes(nodes, level):
            for node in nodes:
                node["_status"] = "ok"
                if level == 3:
                    node["file_path"] = f"场景_{uuid.uuid4().hex[:8]}.md"
                    node["children"] = []
                else:
                    process_nodes(node.get("children", []), level + 1)

        new_nodes = outline_data.get("nodes", [])
        process_nodes(new_nodes, 1)

        if "nodes" not in self.outline_tree_data:
            self.outline_tree_data["nodes"] = []

        self.outline_tree_data["nodes"].extend(new_nodes)

        try:
            with open(
                self.workspace.tree_json_file, "w", encoding="utf-8"
            ) as f:
                json.dump(self.outline_tree_data, f, ensure_ascii=False, indent=4)

            self.log_console.append(
                "<b><font color='green'>"
                "\U0001f389 大纲生成完毕！已追加到目录树末尾。"
                "</font></b>"
            )
            self.refresh_ui_from_workspace()
        except Exception as e:
            QMessageBox.critical(
                self, "错误", f"保存大纲 JSON 失败:\n{e}"  # type: ignore[arg-type]
            )
        finally:
            self.btn_save.setEnabled(True)

    def on_outline_building_error(self: "NovelCreatorWindow", err_msg: str):
        self.log_console.append(
            f"<font color='red'>大纲生成失败: {err_msg}</font>"
        )
        QMessageBox.warning(
            self, "生成失败", f"大纲生成流程中断:\n{err_msg}"  # type: ignore[arg-type]
        )
        self.btn_save.setEnabled(True)
