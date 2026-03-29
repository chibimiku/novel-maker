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
from ui.utils import find_duplicate_paths, get_item_level
from ui.workers import OutlineBuildingThread, GenerateTaskThread
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

        # 检查是否有未保存的更改
        if self.current_editing_node:
            current_summary = self.summary_editor.toPlainText()
            current_content = self.content_editor.toPlainText()
            original_summary = self.current_node_original_summary
            original_content = self.current_node_original_content
            
            if current_summary != original_summary or current_content != original_content:
                reply = QMessageBox.question(
                    self,  # type: ignore[arg-type]
                    "保存更改",
                    "当前节点有未保存的更改，是否在切换前保存？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                
                if reply == QMessageBox.StandardButton.Cancel:
                    return
                elif reply == QMessageBox.StandardButton.Yes:
                    self.save_current_node()

        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        real_node = self.node_map.get(node_id)
        if not real_node:
            return

        # 保存新节点的原始状态
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
        if item and not item.text(0).startswith("+"):
            node_id = item.data(0, Qt.ItemDataRole.UserRole)
            real_node = self.node_map.get(node_id)
            if real_node:
                level = get_item_level(item)
                # 只对1级和2级节点添加增加子节点的选项
                if level in [1, 2]:
                    add_children_action = menu.addAction(
                        "\U0001f4a1 根据要求增加子节点"
                    )
                    add_children_action.triggered.connect(lambda: self.open_add_children_dialog(real_node, level))
                    menu.addSeparator()

        gen_outline_action = menu.addAction(
            "\U0001f4a1 结合当前点子与左侧勾选设定，自动生成大纲"
        )
        gen_outline_action.triggered.connect(self.open_outline_building_dialog)

        if item and not item.text(0).startswith("+"):
            menu.addSeparator()
            sync_summary_action = menu.addAction("\U0001f504 校验并同步该节点及子节点概要 (生成Diff)")
            sync_summary_action.triggered.connect(lambda: self.start_summary_sync(real_node, get_item_level(item)))

        menu.exec(self.novel_tree.viewport().mapToGlobal(position))

    def start_summary_sync(self: "NovelCreatorWindow", target_node: dict, level: int):
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。") # type: ignore[arg-type]
            return
            
        reply = QMessageBox.question(
            self, # type: ignore[arg-type]
            "确认执行",
            f"即将遍历校验节点【{target_node.get('title')}】及其所有子节点。\n\n"
            "本操作不会直接覆盖您的原始数据，只会生成两份临时 Markdown 文件供您进行 Diff 比较和手动处理。\n"
            "请确认是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.log_console.append(f"<font color='cyan'>启动概要同步校验引擎，根节点：{target_node.get('title')}...</font>")
            self.btn_save.setEnabled(False)
            
            self.sync_worker = SummarySyncWorker(
                target_node=target_node,
                level=level,
                llm_client=self.llm_client,
                workspace_manager=self.workspace
            )
            self.sync_worker.progress_signal.connect(lambda msg: self.log_console.append(f"<font color='gray'>{msg}</font>"))
            self.sync_worker.success_signal.connect(self.on_summary_sync_success)
            self.sync_worker.error_signal.connect(self.on_summary_sync_error)
            self.sync_worker.start()

    def on_summary_sync_success(self: "NovelCreatorWindow", before_path: str, after_path: str):
        self.btn_save.setEnabled(True)
        self.log_console.append("<font color='green'><b>✅ 概要校验与同步完成！</b></font>")
        self.log_console.append(f"旧版概要文件: <a href='file:///{before_path}'>{before_path}</a>")
        self.log_console.append(f"新版概要文件: <a href='file:///{after_path}'>{after_path}</a>")
        
        QMessageBox.information(
            self, # type: ignore[arg-type]
            "处理完成",
            "大纲概要校验已完成！\n\n"
            "系统已将修改前后的汇总输出为以下文件：\n"
            f"1. {os.path.basename(before_path)}\n2. {os.path.basename(after_path)}\n\n"
            "请前往工作区的 temp_diff 文件夹下使用相关 Diff 工具（如 VS Code）进行查阅，按需复制所需的文本覆盖原节点。"
        )

    def on_summary_sync_error(self: "NovelCreatorWindow", error_msg: str):
        self.btn_save.setEnabled(True)
        self.log_console.append(f"<font color='red'>❌ 概要校验失败: {error_msg}</font>")
        QMessageBox.critical(self, "错误", f"概要校验过程中发生异常:\n{error_msg}") # type: ignore[arg-type]

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