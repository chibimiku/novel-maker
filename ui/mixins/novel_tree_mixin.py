"""
NovelTreeMixin —— 小说大纲树的渲染、节点交互、拖拽同步、大纲生成。
"""
from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING
import os
from ui.summary_sync_worker import SummarySyncWorker

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
from ui.utils import find_duplicate_paths, get_item_level, find_item_by_data
from ui.workers import OutlineBuildingThread, GenerateTaskThread
from core.context_builder import ContextBuilder

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class NovelTreeMixin:
    """小说大纲树的渲染、节点交互与大纲自动生成。"""

    # ================= 小说树渲染 ================= #

    def _refresh_novel_tree(self: "NovelCreatorWindow"):
        """渲染右侧的小说大纲目录树（带折叠状态记忆）。"""
        
        # 1. 在清空前，记录当前展开的节点 ID
        is_first_load = self.novel_tree.topLevelItemCount() == 0
        expanded_ids = set()
        
        if not is_first_load:
            def collect_expanded(parent_item):
                for i in range(parent_item.childCount()):
                    child = parent_item.child(i)
                    if child.isExpanded():
                        n_id = child.data(0, Qt.ItemDataRole.UserRole)
                        if n_id:
                            expanded_ids.add(n_id)
                    # 递归检查子节点
                    collect_expanded(child)
            
            collect_expanded(self.novel_tree.invisibleRootItem())

        # 2. 原有的清理和加载逻辑
        self.novel_tree.clear()
        self.node_map.clear()
        self.current_editing_item = None  # 清除已删除的item引用
        self.novel_tree.setHeaderLabel("小说大纲结构")
        self.outline_tree_data = self.workspace.load_outline_tree()

        if "nodes" not in self.outline_tree_data:
            self.outline_tree_data["nodes"] = []
        nodes_ref = self.outline_tree_data["nodes"]

        # 冲突警告逻辑保持不变
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

        # 构建 UI 树
        self._build_novel_tree_ui(nodes_ref, self.novel_tree, level=1)
        
        # 3. 恢复折叠状态
        if is_first_load:
            # 如果是初次加载，默认全部展开
            self.novel_tree.expandAll()
        else:
            # 否则，仅恢复之前展开的节点
            def restore_expanded(parent_item):
                for i in range(parent_item.childCount()):
                    child = parent_item.child(i)
                    n_id = child.data(0, Qt.ItemDataRole.UserRole)
                    if n_id and n_id in expanded_ids:
                        child.setExpanded(True)
                    # 递归恢复子节点
                    restore_expanded(child)
                    
            restore_expanded(self.novel_tree.invisibleRootItem())

    def _build_novel_tree_ui(
        self: "NovelCreatorWindow", nodes: list, parent_widget, level: int = 1
    ):
        # 场景的内部（第4级），严格禁止渲染任何子节点和按钮
        if level > 3:
            return

        for node in nodes:
            title = node.get("title", "未命名节点")
            # 对于3级节点，添加文件修改时间
            if level == 3:
                file_path = node.get("file_path")
                if file_path:
                    full_path = os.path.join(self.workspace.text_path, file_path)
                    if os.path.exists(full_path):
                        import time
                        mtime = os.path.getmtime(full_path)
                        # 格式化时间为 YYYY-MM-DD HH:MM
                        formatted_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                        title = f"{title} ({formatted_time})"
            item = QTreeWidgetItem(parent_widget, [title])

            # 添加勾选框
            item.setCheckState(0, Qt.CheckState.Unchecked)
            
            if level == 3:
                item.setFlags(
                    (item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                    & ~Qt.ItemFlag.ItemIsDropEnabled
                )
            else:
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsDropEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                )

            # 使用节点的id属性作为node_id
            node_id = node.get("id")
            self.node_map[node_id] = node
            item.setData(0, Qt.ItemDataRole.UserRole, node_id)

            status = node.get("_status", "ok")
            file_path = node.get("file_path")

            # 检查是否有待合并的修改
            has_pending = False
            if self.workspace and node_id:
                has_pending = self.workspace.has_pending_modify(node_id)

            if level == 3:
                if has_pending:
                    # 有待合并修改，显示红色
                    item.setForeground(0, QColor("#FF4444"))
                elif (
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
                if has_pending:
                    item.setForeground(0, QColor("#FF4444"))
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
        
        all_nodes_dict = {}
        
        def collect_all_nodes(nodes):
            for node in nodes:
                if "id" in node:
                    all_nodes_dict[node["id"]] = node
                if "children" in node:
                    collect_all_nodes(node["children"])
        
        if hasattr(self, '_pre_drag_tree_snapshot') and self._pre_drag_tree_snapshot:
            collect_all_nodes(self._pre_drag_tree_snapshot.get("nodes", []))
        else:
            collect_all_nodes(self.outline_tree_data.get("nodes", []))
        
        for i in range(root.childCount()):
            item = root.child(i)
            if item.text(0).startswith("+"):
                continue
            node_data = self._build_node_data_from_item(item, all_nodes_dict)
            if node_data:
                new_nodes.append(node_data)

        self.outline_tree_data["nodes"] = new_nodes
        self.workspace.save_outline_tree(self.outline_tree_data)
        
        if hasattr(self, '_pre_drag_tree_snapshot'):
            self._pre_drag_tree_snapshot = None
        
        self.log_console.append("系统通知：节点位置结构已自动保存。")

    def _build_node_data_from_item(self: "NovelCreatorWindow", item, all_nodes_dict=None, original_parent_data=None):
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        
        # 【修复核心】：Qt内部拖拽父节点会导致子节点UserRole丢失。在此通过标题匹配进行回退恢复。
        if not node_id and original_parent_data and "children" in original_parent_data:
            item_text = item.text(0)
            for orig_child in original_parent_data["children"]:
                # 3级节点标题在UI中附加了修改时间，因此使用 startswith 或 in 进行匹配
                if item_text.startswith(orig_child.get("title", "")):
                    node_id = orig_child.get("id")
                    if node_id:
                        # 恢复ID并重新写回UI，防止后续流程再次丢失
                        item.setData(0, Qt.ItemDataRole.UserRole, node_id)
                        break
        
        node_data = None
        if all_nodes_dict and node_id in all_nodes_dict:
            node_data = all_nodes_dict[node_id]
        if not node_data:
            node_data = self.node_map.get(node_id)
        
        if not node_data:
            return None

        new_children: list = []
        for i in range(item.childCount()):
            child_item = item.child(i)
            if child_item.text(0).startswith("+"):
                continue
            # 递归调用时，将当前的 node_data 作为 original_parent_data 传给子节点
            child_data = self._build_node_data_from_item(child_item, all_nodes_dict, node_data)
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

        # === 【修复核心 1】：将未保存更改的检查逻辑移动到最前面 ===
        if self.current_editing_node:
            current_summary = self.summary_editor.toPlainText()
            current_content = self.content_editor.toPlainText()
            original_summary = getattr(self, "current_node_original_summary", "")
            original_content = getattr(self, "current_node_original_content", "")
            
            # 【修复核心 2】：统一换行符标准，防止 Windows/Mac 换行符差异导致的“假变动”误报
            is_summary_changed = current_summary.replace('\r\n', '\n') != original_summary.replace('\r\n', '\n')
            is_content_changed = current_content.replace('\r\n', '\n') != original_content.replace('\r\n', '\n')
            
            if is_summary_changed or is_content_changed:
                reply = QMessageBox.question(
                    self,  # type: ignore[arg-type]
                    "保存更改",
                    "当前节点有未保存的更改，是否在切换前保存？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                
                if reply == QMessageBox.StandardButton.Cancel:
                    # 【修复核心 3】：如果用户取消切换，强行把大纲树的高亮选择框拉回正在编辑的节点
                    if getattr(self, "current_editing_item", None):
                        self.novel_tree.setCurrentItem(self.current_editing_item)
                    return
                elif reply == QMessageBox.StandardButton.Yes:
                    self.save_current_node()

        # === 拦截并处理 “+新增” 按钮 ===
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

        # === 以下为原有的常规节点点击处理逻辑 ===
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        real_node = self.node_map.get(node_id)
        if not real_node:
            return

        # (保留你原来的后续代码，比如记录 current_editing_node、重置 Undo 等...)
        self.current_editing_node = real_node
        self.current_editing_item = item
        self.current_setting_path = None
        node_level = get_item_level(item)
        
        # 重置回退缓冲区
        self.undo_stack = []
        self.btn_undo.setEnabled(False)
        
        # 保存当前节点的原始状态
        self.current_node_original_summary = real_node.get("summary", "")
        if node_level == 3:
            rel_path = real_node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                if os.path.exists(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        self.current_node_original_content = f.read()
                else:
                    self.current_node_original_content = f"# {real_node.get('title')}\n\n(文件尚未生成)"
            else:
                self.current_node_original_content = f"# {real_node.get('title')}\n\n"
        else:
            self.current_node_original_content = "（当前层级仅支持填写概要，正文请在底层的\u201c场景\u201d节点中生成/编写）"

        self.btn_delete.setEnabled(not bool(real_node.get("children")))

        # 1. 加载概要
        self.summary_editor.setText(real_node.get("summary", ""))
        self.summary_editor.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_regenerate_summary.setEnabled(True)
        self.btn_select_all.setEnabled(True)
        self.btn_select_none.setEnabled(True)

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
                "id": str(uuid.uuid4()),
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
        
        # 获取当前点击的节点
        item = self.novel_tree.itemAt(position)
        real_node = None
        level = 0
        node_id = None
        has_pending = False
        
        # 检查是否有被勾选的节点
        checked_nodes = []
        root = self.novel_tree.invisibleRootItem()
        
        def collect_checked_items(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if not child.text(0).startswith("+"):
                    if child.checkState(0) == Qt.CheckState.Checked:
                        n_id = child.data(0, Qt.ItemDataRole.UserRole)
                        if n_id and n_id in self.node_map:
                            checked_nodes.append((child, self.node_map[n_id]))
                    collect_checked_items(child)
        
        collect_checked_items(root)
        
        # 如果有被勾选的节点，添加批量修改选项
        if checked_nodes:
            batch_modify_action = menu.addAction("📝 批量修改勾选节点")
            batch_modify_action.triggered.connect(lambda: self.open_batch_modify_request_dialog(checked_nodes))
            menu.addSeparator()
        
        if item and not item.text(0).startswith("+"):
            node_id = item.data(0, Qt.ItemDataRole.UserRole)
            real_node = self.node_map.get(node_id)
            if real_node:
                level = get_item_level(item)
                has_pending = self.workspace.has_pending_modify(node_id) if node_id else False
                
                # 只对1级和2级节点添加增加子节点的选项
                if level in [1, 2]:
                    add_children_action = menu.addAction(
                        "\U0001f4a1 根据要求增加子节点"
                    )
                    add_children_action.triggered.connect(lambda: self.open_add_children_dialog(real_node, level))
                    menu.addSeparator()
                
                # 只对3级节点添加修改内容的选项
                if level == 3:
                    if has_pending:
                        # 有待合并修改时，"修改内容"变灰，"进行合并"可用
                        modify_action = menu.addAction("✏️ 修改内容")
                        modify_action.setEnabled(False)
                        
                        merge_action = menu.addAction("🔄 进行合并")
                        merge_action.triggered.connect(lambda: self.open_merge_dialog(node_id, real_node))
                        
                        discard_action = menu.addAction("❌ 丢弃修改")
                        discard_action.triggered.connect(lambda: self.discard_pending_modify(node_id))
                    else:
                        # 无待合并修改时，"修改内容"可用
                        modify_action = menu.addAction("✏️ 修改内容")
                        modify_action.triggered.connect(lambda: self.open_modify_request_dialog(real_node, node_id))
                    
                    menu.addSeparator()

        gen_outline_action = menu.addAction(
            "\U0001f4a1 结合当前点子与左侧勾选设定，自动生成大纲"
        )
        gen_outline_action.triggered.connect(self.open_outline_building_dialog)

        if item and not item.text(0).startswith("+") and real_node:
            menu.addSeparator()
            sync_summary_action = menu.addAction("\U0001f504 校验并同步该节点及子节点概要 (生成Diff)")
            sync_summary_action.triggered.connect(lambda: self.start_summary_sync(real_node, level))

        menu.exec(self.novel_tree.viewport().mapToGlobal(position))

    def start_summary_sync(self: "NovelCreatorWindow", target_node: dict, level: int):
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。") # type: ignore[arg-type]
            return
            
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QPushButton, QHBoxLayout
        
        # 构建一个自定义的确认对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("确认执行校验同步")
        dialog.setMinimumWidth(450)
        
        layout = QVBoxLayout()
        
        msg_label = QLabel(
            f"即将遍历校验节点【{target_node.get('title')}】及其所有子节点。\n\n"
            "默认情况下，系统只会生成两份临时 Markdown 文件供您进行 Diff 比较，不会修改原数据。"
        )
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)
        
        # 新增强制覆盖模式复选框
        force_cb = QCheckBox("开启【强制覆盖模式】（仅对底层场景生效）")
        force_cb.setToolTip("勾选后，所有3级场景节点的概要将直接被新版本覆盖。1级(章)、2级(节)节点过于复杂，依然只生成 Diff 供人工确认。")
        # 默认不选中
        force_cb.setChecked(False)
        layout.addWidget(force_cb)
        
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定开始")
        cancel_btn = QPushButton("取消")
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        
        dialog.setLayout(layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            force_mode = force_cb.isChecked()
            mode_str = "【自动覆盖3级场景】" if force_mode else "【仅生成Diff】"
            
            self.log_console.append(f"<font color='cyan'>启动概要同步校验引擎 {mode_str}，根节点：{target_node.get('title')}...</font>")
            self.btn_save.setEnabled(False)
            
            self.sync_worker = SummarySyncWorker(
                target_node=target_node,
                level=level,
                llm_client=self.llm_client,
                workspace_manager=self.workspace,
                force_mode=force_mode  # 传入勾选状态
            )
            self.sync_worker.progress_signal.connect(lambda msg: self.log_console.append(f"<font color='gray'>{msg}</font>"))
            self.sync_worker.success_signal.connect(self.on_summary_sync_success)
            self.sync_worker.error_signal.connect(self.on_summary_sync_error)
            self.sync_worker.start()

    def on_summary_sync_success(self: "NovelCreatorWindow", before_path: str, after_path: str, level3_updates: dict):
        updated_count = 0
        
        # 如果有需要强制更新的3级节点，直接覆盖内存中的大纲树并保存
        if level3_updates:
            for node_id, new_summary in level3_updates.items():
                if node_id in self.node_map:
                    self.node_map[node_id]["summary"] = new_summary
                    updated_count += 1
            
            if updated_count > 0 and self.workspace and self.outline_tree_data:
                self.workspace.save_outline_tree(self.outline_tree_data)
                
                # 如果当前UI右侧正文编辑器正在编辑刚才被覆盖的节点，自动刷新其内容
                if self.current_editing_node and self.current_editing_node.get("id") in level3_updates:
                    self.summary_editor.setText(self.current_editing_node["summary"])

        self.btn_save.setEnabled(True)
        self.log_console.append("<font color='green'><b>✅ 概要校验与同步完成！</b></font>")
        
        if updated_count > 0:
            self.log_console.append(f"<font color='yellow'>已自动覆盖 {updated_count} 个3级场景的概要。</font>")
            
        self.log_console.append(f"旧版概要文件: <a href='file:///{before_path}'>{before_path}</a>")
        self.log_console.append(f"新版概要文件: <a href='file:///{after_path}'>{after_path}</a>")
        
        # 弹窗提示
        msg = "大纲概要校验已完成！\n\n"
        if updated_count > 0:
            msg += f"已为您自动覆盖 {updated_count} 个3级场景节点的概要，大纲已自动保存。\n\n"
            
        msg += (
            "系统已将所有节点（含1、2、3级）修改前后的汇总输出为以下文件：\n"
            f"1. {os.path.basename(before_path)}\n2. {os.path.basename(after_path)}\n\n"
            "（请前往工作区的 temp_diff 文件夹查阅章/节的 Diff 并手动合并）"
        )
        
        QMessageBox.information(self, "处理完成", msg) # type: ignore[arg-type]

    def on_summary_sync_error(self: "NovelCreatorWindow", error_msg: str):
        self.btn_save.setEnabled(True)
        self.log_console.append(f"<font color='red'>❌ 概要校验失败: {error_msg}</font>")
        QMessageBox.critical(self, "错误", f"概要校验过程中发生异常:\n{error_msg}") # type: ignore[arg-type]ype: ignore[arg-type]

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
                if "id" not in node:
                    node["id"] = str(uuid.uuid4())
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

    def open_add_children_dialog(self: "NovelCreatorWindow", target_node: dict, level: int):
        """打开增加子节点的对话框"""
        if not self.llm_client:
            QMessageBox.warning(
                self, "未配置", "请先在设置中配置大模型 API。"  # type: ignore[arg-type]
            )
            return

        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QLineEdit, QPushButton, QHBoxLayout

        class AddChildrenDialog(QDialog):
            def __init__(self, parent):
                super().__init__(parent)
                self.setWindowTitle("根据要求增加子节点")
                self.setMinimumWidth(500)
                
                layout = QVBoxLayout()
                
                # 输入要求
                layout.addWidget(QLabel("请输入子节点的生成要求："))
                self.requirement_edit = QTextEdit()
                self.requirement_edit.setPlaceholderText("例如：生成3个关于主角在森林中冒险的场景")
                layout.addWidget(self.requirement_edit)
                
                # 输入节点个数
                layout.addWidget(QLabel("请输入要生成的子节点个数："))
                self.count_edit = QLineEdit()
                self.count_edit.setPlaceholderText("输入数字，如：3")
                layout.addWidget(self.count_edit)
                
                # 按钮
                btn_layout = QHBoxLayout()
                self.ok_btn = QPushButton("确定")
                self.cancel_btn = QPushButton("取消")
                btn_layout.addStretch()
                btn_layout.addWidget(self.ok_btn)
                btn_layout.addWidget(self.cancel_btn)
                layout.addLayout(btn_layout)
                
                self.ok_btn.clicked.connect(self.accept)
                self.cancel_btn.clicked.connect(self.reject)
                
                self.setLayout(layout)
            
            def get_requirement(self):
                return self.requirement_edit.toPlainText().strip()
            
            def get_count(self):
                try:
                    return int(self.count_edit.text().strip())
                except:
                    return 0

        dialog = AddChildrenDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            requirement = dialog.get_requirement()
            count = dialog.get_count()
            
            if not requirement:
                QMessageBox.warning(
                    self, "输入错误", "请输入生成要求。"  # type: ignore[arg-type]
                )
                return
            
            if count <= 0:
                QMessageBox.warning(
                    self, "输入错误", "请输入有效的节点个数。"  # type: ignore[arg-type]
                )
                return
            
            self.generate_children_nodes(target_node, level, requirement, count)

    def generate_children_nodes(self: "NovelCreatorWindow", target_node: dict, level: int, requirement: str, count: int):
        """根据要求生成子节点"""
        node_title = target_node.get("title", "未知节点")
        self.log_console.append(f"开始生成【{node_title}】的子节点...")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("正在构建上下文并发送请求到LLM生成子节点...")
        
        # 禁用相关按钮
        self.btn_save.setEnabled(False)
        
        try:
            # 构建上下文
            builder = ContextBuilder(self.workspace)
            checked_paths = self.get_checked_settings()
            
            # 获取父节点和同级节点
            parents = []
            siblings = []
            
            def find_node_info(current_nodes, current_path):
                nonlocal parents, siblings
                for node in current_nodes:
                    path = current_path + [node]
                    if node is target_node:
                        parents = current_path
                        # 收集同级节点
                        for sibling in current_nodes:
                            if sibling is not target_node:
                                siblings.append(sibling)
                        return True
                    if node.get("children"):
                        if find_node_info(node.get("children", []), path):
                            return True
                return False
            
            find_node_info(self.outline_tree_data.get("nodes", []), [])
            
            # 构建上下文文本
            context_blocks = []
            
            # 父节点信息
            if parents:
                context_blocks.append("【父节点信息】")
                for p in parents:
                    title = p.get("title", "未命名")
                    summary = p.get("summary", "").strip() or "(该层级无概要)"
                    context_blocks.append(f"<{title}> 概要:\n{summary}\n")
            
            # 同级节点信息
            if siblings:
                context_blocks.append("【同级节点信息】")
                for sibling in siblings:
                    title = sibling.get("title", "未命名")
                    summary = sibling.get("summary", "").strip() or "(暂无概要)"
                    context_blocks.append(f"<{title}> 概要:\n{summary}\n")
            
            # 当前节点信息
            context_blocks.append("【当前节点信息】")
            current_summary = target_node.get("summary", "").strip() or "(暂无概要)"
            context_blocks.append(f"<{node_title}> 概要:\n{current_summary}\n")
            
            context_text = "\n".join(context_blocks) if context_blocks else "（无相关上下文）"
            
            # 构建提示词
            child_type = "节" if level == 1 else "场景"
            prompt = f"""
你是一个专业的小说创作者。请根据提供的上下文信息，为指定的节点生成子节点。

### 一、 上下文信息
{context_text}

### 二、 生成要求与核心要素规范（绝对红线）
1. 请为节点【{node_title}】生成 {count} 个 {child_type} 子节点，需围绕此要求展开：{requirement}
2. 每个子节点需要包含标题和概要内容。如果当前节点【{node_title}】没有概要，请一并生成其概要内容。
3. **【核心概要提取强制规范】**：你生成的**所有概要**（包括补充的父节点概要和新建的子节点概要），都必须结构严谨，明确写出以下信息：
   - **时间与事件交互**：明确交代该剧情发生时的【时间】、【具体地点】、【出场人物】，以及明确的【事件交互】（谁和谁具体完成了什么事）。
   - **状态与变化**：必须体现核心人物的【心境/情绪转变】、【衣着/装备/外貌的改变】，或【队伍成员的增减变化】。
   - **场景与轨迹**：若发生位置转移，需明确指出【从哪里移动到了哪里】。
4. 生成的子节点应该与上下文信息逻辑连贯。只需要生成概要内容，不需要生成正文。

### 三、 输出格式
请严格按照以下 JSON 格式输出：
{{
  "current_node_summary": "当前节点的概要内容（如果需要生成，也必须符合上述核心要素规范）",
  "children": [
    {{
      "title": "子节点1标题",
      "summary": "子节点1概要（必须符合上述核心要素规范）"
    }},
    {{
      "title": "子节点2标题",
      "summary": "子节点2概要（必须符合上述核心要素规范）"
    }}
  ]
}}

请确保输出是有效的 JSON 格式，不要包含任何其他内容。
"""
            
            self.log_console.append("提示词构建完成，准备发送请求...")
            
            # 发送请求
            self.generate_thread = GenerateTaskThread(self.llm_client, prompt)
            self.generate_thread.success_signal.connect(lambda result: self.on_children_generate_success(result, target_node))
            self.generate_thread.error_signal.connect(self.on_children_generate_error)
            self.generate_thread.start()
            
            self.log_console.append("生成线程已启动，等待LLM响应...")
        except Exception as e:
            self.log_console.append(f"<font color='red'>生成子节点时发生错误: {e}</font>")
            # 在状态栏显示错误信息
            statusbar = self.statusBar()
            if statusbar:
                statusbar.showMessage(f"生成子节点时发生错误: {e[:50]}...", 3000)
            # 恢复按钮状态
            self.btn_save.setEnabled(True)

    def on_children_generate_success(self: "NovelCreatorWindow", result: str, target_node: dict):
        """处理子节点生成成功的回调"""
        try:
            import json
            data = json.loads(result)
            
            # 更新当前节点的概要（如果有）
            if "current_node_summary" in data and data["current_node_summary"]:
                target_node["summary"] = data["current_node_summary"]
            
            # 添加子节点
            if "children" in data and isinstance(data["children"], list):
                children = target_node.setdefault("children", [])
                for child in data["children"]:
                    if "title" in child:
                        new_child = {
                                "id": str(uuid.uuid4()),
                                "title": child["title"],
                                "summary": child.get("summary", ""),
                                "children": [],
                                "_status": "ok"
                            }
                        # 如果是生成场景节点，添加文件路径
                        if len(target_node.get("children", [])) + len(data["children"]) <= 3:
                            if target_node.get("children", []) and len(target_node["children"]) == 2:
                                # 第三个子节点，应该是场景
                                import uuid
                                file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
                                new_child["file_path"] = file_name
                        children.append(new_child)
            
            # 保存大纲
            self.workspace.save_outline_tree(self.outline_tree_data)
            self.log_console.append("子节点生成成功！")
            
            # 在状态栏显示信息
            statusbar = self.statusBar()
            if statusbar:
                statusbar.showMessage("子节点生成成功，已更新大纲树", 3000)
            
            # 刷新 UI
            self.refresh_ui_from_workspace()
        except Exception as e:
            self.log_console.append(f"<font color='red'>处理生成结果失败: {e}</font>")
            # 在状态栏显示错误信息
            statusbar = self.statusBar()
            if statusbar:
                statusbar.showMessage(f"处理生成结果失败: {e[:50]}...", 3000)
        finally:
            self.btn_save.setEnabled(True)

    def on_children_generate_error(self: "NovelCreatorWindow", error_msg: str):
        """处理子节点生成错误的回调"""
        self.log_console.append(
            f"<font color='red'>子节点生成失败: {error_msg}</font>"
        )
        # 在状态栏显示错误信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage(f"子节点生成失败: {error_msg[:50]}...", 3000)
        # 恢复按钮状态
        self.btn_save.setEnabled(True)

    def on_outline_building_error(self: "NovelCreatorWindow", err_msg: str):
        self.log_console.append(
            f"<font color='red'>大纲生成失败: {err_msg}</font>"
        )
        QMessageBox.warning(
            self, "生成失败", f"大纲生成流程中断:\n{err_msg}"  # type: ignore[arg-type]
        )
        self.btn_save.setEnabled(True)

    # ================= 修改内容暂存与合并功能 ================= #

    def open_modify_request_dialog(self: "NovelCreatorWindow", real_node: dict, node_id: str):
        """打开修改请求对话框"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton, QHBoxLayout

        class ModifyRequestDialog(QDialog):
            def __init__(self, parent, node_title):
                super().__init__(parent)
                self.setWindowTitle(f"修改内容 - {node_title}")
                self.setMinimumWidth(600)
                self.setMinimumHeight(400)
                self.result_text = ""

                layout = QVBoxLayout()
                layout.addWidget(QLabel("请输入修改要求："))

                self.requirement_edit = QTextEdit()
                self.requirement_edit.setPlaceholderText("例如：将这段内容的语气改得更加轻松幽默，或者增加一些环境描写...")
                layout.addWidget(self.requirement_edit)

                btn_layout = QHBoxLayout()
                btn_layout.addStretch()

                self.cancel_btn = QPushButton("取消")
                self.cancel_btn.clicked.connect(self.reject)
                btn_layout.addWidget(self.cancel_btn)

                self.ok_btn = QPushButton("生成修改")
                self.ok_btn.clicked.connect(self.accept)
                self.ok_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
                btn_layout.addWidget(self.ok_btn)

                layout.addLayout(btn_layout)
                self.setLayout(layout)

            def get_requirement(self):
                return self.requirement_edit.toPlainText().strip()

        dialog = ModifyRequestDialog(self, real_node.get("title", "未知节点"))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            requirement = dialog.get_requirement()
            if requirement:
                self.start_modify_content(real_node, node_id, requirement)

    def start_modify_content(self: "NovelCreatorWindow", real_node: dict, node_id: str, requirement: str):
        """开始修改内容的LLM请求"""
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return

        # 读取原文
        original_text = ""
        rel_path = real_node.get("file_path")
        if rel_path:
            full_path = os.path.join(self.workspace.text_path, rel_path)
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    original_text = f.read()

        if not original_text:
            QMessageBox.warning(self, "提示", "该节点暂无正文内容可修改。")
            return

        self.log_console.append(f"<font color='cyan'>开始修改节点【{real_node.get('title')}】的内容...</font>")
        self.btn_save.setEnabled(False)

        # 构建提示词
        prompt = f"""你是一个专业的小说编辑助手。请根据用户的修改要求，对提供的小说正文进行修改。

### 修改要求：
{requirement}

### 原文内容：
{original_text}

### 输出要求：
1. 只输出修改后的完整文本
2. 保持原文的基本结构和格式
3. 不要添加任何额外的解释性文字
4. 如果原文有标题（# 开头），请保留
"""

        # 发送请求
        from ui.workers import GenerateTaskThread
        self.modify_thread = GenerateTaskThread(self.llm_client, prompt)
        self.modify_thread.success_signal.connect(lambda result: self.on_modify_success(result, real_node, node_id, original_text, requirement))
        self.modify_thread.error_signal.connect(self.on_modify_error)
        self.modify_thread.start()

    def on_modify_success(self: "NovelCreatorWindow", result: str, real_node: dict, node_id: str, original_text: str, requirement: str):
        """修改成功回调"""
        try:
            # 保存待合并修改
            self.workspace.save_pending_modify(node_id, original_text, result, requirement)
            
            self.log_console.append(f"<font color='green'>✅ 修改内容生成完成！节点标题已变红，请右键选择【进行合并】来查看差异并合并。</font>")
            
            # 刷新树UI，显示红色标题
            self._refresh_novel_tree()
            
        except Exception as e:
            self.log_console.append(f"<font color='red'>保存待合并修改失败: {e}</font>")
            QMessageBox.critical(self, "错误", f"保存待合并修改失败:\n{e}")
        finally:
            self.btn_save.setEnabled(True)

    def on_modify_error(self: "NovelCreatorWindow", error_msg: str):
        """修改失败回调"""
        self.log_console.append(f"<font color='red'>修改内容失败: {error_msg}</font>")
        QMessageBox.critical(self, "错误", f"修改内容过程中发生异常:\n{error_msg}")
        self.btn_save.setEnabled(True)

    def open_merge_dialog(self: "NovelCreatorWindow", node_id: str, real_node: dict):
        """打开合并对话框"""
        from ui.diff_merge_dialog import DiffMergeDialog

        pending_data = self.workspace.get_pending_modify(node_id)
        if not pending_data:
            QMessageBox.warning(self, "提示", "没有找到待合并的修改。")
            return

        dialog = DiffMergeDialog(
            self,
            node_id,
            pending_data["original_text"],
            pending_data["modified_text"],
            real_node.get("title", "未知节点")
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            result_text = dialog.get_result()
            self.apply_merge_result(node_id, real_node, result_text)

    def apply_merge_result(self: "NovelCreatorWindow", node_id: str, real_node: dict, result_text: str):
        """应用合并结果"""
        try:
            # 保存到文件
            rel_path = real_node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(result_text)
                
                # 更新md5
                new_md5 = self.workspace.calculate_md5(full_path)
                real_node["md5"] = new_md5

            # 删除待合并修改
            self.workspace.delete_pending_modify(node_id)

            # 保存大纲树
            self.workspace.save_outline_tree(self.outline_tree_data)

            # 刷新UI
            self._refresh_novel_tree()
            
            # 重新查找并设置当前编辑的节点（刷新树后旧item已无效）
            if self.current_editing_node and self.current_editing_node.get("id") == node_id:
                # 从新构建的树中重新找到对应的item和节点对象
                item = find_item_by_data(self.novel_tree.invisibleRootItem(), node_id)
                if item:
                    self.current_editing_item = item
                # 更新当前编辑的节点为新的节点对象
                if node_id in self.node_map:
                    self.current_editing_node = self.node_map[node_id]
                # 刷新编辑器内容
                if rel_path:
                    full_path = os.path.join(self.workspace.text_path, rel_path)
                    if os.path.exists(full_path):
                        with open(full_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            self.content_editor.setText(content)
                            # 更新原始内容比较基准
                            self.current_node_original_content = content
                            self.current_node_original_summary = self.summary_editor.toPlainText()
            
            self.log_console.append(f"<font color='green'>✅ 合并成功！节点内容已更新。</font>")

        except Exception as e:
            self.log_console.append(f"<font color='red'>合并失败: {e}</font>")
            QMessageBox.critical(self, "错误", f"合并失败:\n{e}")

    def discard_pending_modify(self: "NovelCreatorWindow", node_id: str):
        """丢弃待合并修改"""
        reply = QMessageBox.question(
            self,
            "确认丢弃",
            "确定要丢弃这次修改吗？LLM生成的结果将被永久删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.workspace.delete_pending_modify(node_id)
            self._refresh_novel_tree()
            self.log_console.append(f"<font color='yellow'>已丢弃待合并的修改。</font>")
    
    def open_batch_modify_request_dialog(self: "NovelCreatorWindow", checked_nodes):
        """打开批量修改请求对话框"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton, QHBoxLayout, QListWidget, QListWidgetItem
        
        # 过滤节点：只保留3级且没有待合并修改的节点
        valid_nodes = []
        skipped_info = []
        
        for item, node in checked_nodes:
            level = get_item_level(item)
            node_id = node.get("id")
            
            if level in [1, 2]:
                skipped_info.append(f"跳过【{node.get('title', '未知')}】：1级/2级节点无正文")
                continue
            
            if self.workspace.has_pending_modify(node_id):
                skipped_info.append(f"跳过【{node.get('title', '未知')}】：已处于待合并状态")
                continue
            
            # 读取原文，检查是否有正文
            original_text = ""
            rel_path = node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                if os.path.exists(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        original_text = f.read()
            
            if not original_text:
                skipped_info.append(f"跳过【{node.get('title', '未知')}】：无正文内容")
                continue
            
            valid_nodes.append((item, node))
        
        if not valid_nodes:
            info_text = "没有符合条件的节点可以处理。\n\n" + "\n".join(skipped_info)
            QMessageBox.information(self, "提示", info_text)
            return
        
        class BatchModifyRequestDialog(QDialog):
            def __init__(self, parent, valid_nodes_info, skipped_info_list):
                super().__init__(parent)
                self.setWindowTitle("批量修改内容")
                self.setMinimumWidth(600)
                self.setMinimumHeight(500)
                self.result_text = ""
                
                layout = QVBoxLayout()
                
                # 显示待处理的节点列表
                layout.addWidget(QLabel(f"准备处理 {len(valid_nodes_info)} 个节点："))
                node_list = QListWidget()
                for _, node in valid_nodes_info:
                    node_list.addItem(QListWidgetItem(f"✅ {node.get('title', '未知')}"))
                layout.addWidget(node_list)
                
                # 显示跳过的节点信息（如果有）
                if skipped_info_list:
                    layout.addWidget(QLabel("\n以下节点将被跳过："))
                    skipped_list = QListWidget()
                    for info in skipped_info_list:
                        skipped_list.addItem(QListWidgetItem(f"⚠️ {info}"))
                    layout.addWidget(skipped_list)
                
                # 输入修改要求
                layout.addWidget(QLabel("\n请输入修改要求（将应用于所有选中节点）："))
                self.requirement_edit = QTextEdit()
                self.requirement_edit.setPlaceholderText("例如：将这段内容的语气改得更加轻松幽默，或者增加一些环境描写...")
                self.requirement_edit.setMinimumHeight(120)
                layout.addWidget(self.requirement_edit)
                
                # 按钮
                btn_layout = QHBoxLayout()
                btn_layout.addStretch()
                
                self.cancel_btn = QPushButton("取消")
                self.cancel_btn.clicked.connect(self.reject)
                btn_layout.addWidget(self.cancel_btn)
                
                self.ok_btn = QPushButton("开始批量修改")
                self.ok_btn.clicked.connect(self.accept)
                self.ok_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
                btn_layout.addWidget(self.ok_btn)
                
                layout.addLayout(btn_layout)
                self.setLayout(layout)
            
            def get_requirement(self):
                return self.requirement_edit.toPlainText().strip()
        
        dialog = BatchModifyRequestDialog(self, valid_nodes, skipped_info)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            requirement = dialog.get_requirement()
            if requirement:
                self.start_batch_modify_content(valid_nodes, requirement)
    
    def start_batch_modify_content(self: "NovelCreatorWindow", valid_nodes, requirement):
        """开始批量修改内容"""
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return
        
        # 初始化批量处理状态
        if not hasattr(self, 'batch_modify_queue'):
            self.batch_modify_queue = []
        if not hasattr(self, 'is_batch_modifying'):
            self.is_batch_modifying = False
        
        if self.is_batch_modifying:
            QMessageBox.warning(self, "提示", "正在进行批量修改，请等待完成或停止后再进行新的批量操作。")
            return
        
        self.batch_modify_queue = valid_nodes.copy()
        self.is_batch_modifying = True
        self.batch_modify_requirement = requirement
        self.batch_modify_success_count = 0
        self.batch_modify_fail_count = 0
        
        self.log_console.append(f"<font color='cyan'>🚀 开始批量修改，共 {len(self.batch_modify_queue)} 个节点...</font>")
        self.btn_save.setEnabled(False)
        
        self._process_next_batch_modify_node()
    
    def _process_next_batch_modify_node(self: "NovelCreatorWindow"):
        """处理下一个批量修改节点"""
        if not self.is_batch_modifying:
            return
        
        if not self.batch_modify_queue:
            self.is_batch_modifying = False
            self.btn_save.setEnabled(True)
            self.log_console.append(f"<b><font color='green'>🎉 批量修改完成！成功：{self.batch_modify_success_count} 个，失败：{self.batch_modify_fail_count} 个</font></b>")
            QMessageBox.information(
                self, 
                "批量修改完成", 
                f"批量修改已结束！\n成功：{self.batch_modify_success_count} 个\n失败：{self.batch_modify_fail_count} 个\n\n节点已变红，请右键选择【进行合并】查看差异并合并。"
            )
            self._refresh_novel_tree()
            return
        
        next_item, next_node = self.batch_modify_queue.pop(0)
        node_title = next_node.get('title', '未知节点')
        node_id = next_node.get('id')
        
        self.log_console.append(f"<hr><b>⏳ 正在处理节点: {node_title} (队列剩余 {len(self.batch_modify_queue)} 个)</b>")
        
        # 读取原文
        original_text = ""
        rel_path = next_node.get("file_path")
        if rel_path:
            full_path = os.path.join(self.workspace.text_path, rel_path)
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    original_text = f.read()
        
        if not original_text:
            self.log_console.append(f"<font color='orange'>跳过【{node_title}】：无正文内容</font>")
            self.batch_modify_fail_count += 1
            self._process_next_batch_modify_node()
            return
        
        # 构建提示词
        prompt = f"""你是一个专业的小说编辑助手。请根据用户的修改要求，对提供的小说正文进行修改。

### 修改要求：
{self.batch_modify_requirement}

### 原文内容：
{original_text}

### 输出要求：
1. 只输出修改后的完整文本
2. 保持原文的基本结构和格式
3. 不要添加任何额外的解释性文字
4. 如果原文有标题（# 开头），请保留
"""
        
        # 发送请求
        from ui.workers import GenerateTaskThread
        self.batch_modify_thread = GenerateTaskThread(self.llm_client, prompt)
        self.batch_modify_thread.success_signal.connect(
            lambda result: self.on_batch_modify_success(result, next_node, node_id, original_text)
        )
        self.batch_modify_thread.error_signal.connect(
            lambda error: self.on_batch_modify_error(error, node_title)
        )
        self.batch_modify_thread.start()
    
    def on_batch_modify_success(self: "NovelCreatorWindow", result: str, real_node: dict, node_id: str, original_text: str):
        """批量修改单个节点成功回调"""
        try:
            # 保存待合并修改
            self.workspace.save_pending_modify(node_id, original_text, result, self.batch_modify_requirement)
            
            self.batch_modify_success_count += 1
            node_title = real_node.get('title', '未知节点')
            self.log_console.append(f"<font color='green'>✅ 节点【{node_title}】修改成功！</font>")
            
        except Exception as e:
            self.batch_modify_fail_count += 1
            self.log_console.append(f"<font color='red'>保存待合并修改失败: {e}</font>")
        finally:
            self._process_next_batch_modify_node()
    
    def on_batch_modify_error(self: "NovelCreatorWindow", error_msg: str, node_title: str):
        """批量修改单个节点失败回调"""
        self.batch_modify_fail_count += 1
        self.log_console.append(f"<font color='red'>❌ 节点【{node_title}】修改失败: {error_msg}</font>")
        self._process_next_batch_modify_node()