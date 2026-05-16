"""
SettingTreeMixin —— 世界观设定树的渲染、交互、右键菜单以及世界观 / 索引生成。
"""
from __future__ import annotations

import json
import os
import hashlib
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QTreeWidgetItem,
    QVBoxLayout,
)

from core.context_builder import ContextBuilder
from ui.diff_merge_dialog import DiffMergeDialog
from ui.theme import NODE_ADD_BTN
from ui.dialogs import IdeaInputDialog
from ui.workers import GenerateTaskThread, IndexGenerateThread, WorldBuildingThread

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class SettingTreeMixin:
    """世界观设定树的全部交互与 AI 生成逻辑。"""

    def _iter_setting_json_files(self: "NovelCreatorWindow", cat_path: str) -> list[str]:
        """递归遍历分类目录下所有设定文件（排除 template/index）。"""
        result: list[str] = []
        if not os.path.exists(cat_path):
            return result

        for root, _, files in os.walk(cat_path):
            files = sorted(files)
            for file_name in files:
                if not file_name.endswith(".json"):
                    continue
                if file_name in {"template.json", "index.json"}:
                    continue
                result.append(os.path.join(root, file_name))
        return result

    def _build_setting_display_name(
        self: "NovelCreatorWindow", cat_path: str, file_path: str
    ) -> str:
        """
        为设定文件构建展示名：
        - 根目录文件: 名称
        - 子目录文件: 子目录/名称
        """
        rel_path = os.path.relpath(file_path, cat_path)
        rel_no_ext = rel_path.replace(".json", "")
        return rel_no_ext.replace("\\", "/")

    def _setting_pending_id(self: "NovelCreatorWindow", file_path: str) -> str:
        """把设定文件路径映射为稳定的 pending key。"""
        normalized = os.path.normcase(os.path.abspath(file_path))
        digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()
        return f"setting::{digest}"

    def _collect_checked_setting_items(self: "NovelCreatorWindow") -> list[tuple]:
        """收集设定树中所有打钩的设定文件项。"""
        checked_items: list[tuple] = []
        root = self.setting_tree.invisibleRootItem()
        if not root:
            return checked_items

        def collect_recursive(item):
            if not item:
                return
            if item.text(0).startswith("+"):
                return
            file_path = item.data(0, Qt.ItemDataRole.UserRole)
            if (
                isinstance(file_path, str)
                and file_path.endswith(".json")
                and os.path.exists(file_path)
                and item.checkState(0) == Qt.CheckState.Checked
            ):
                checked_items.append((item, file_path))
            for idx in range(item.childCount()):
                collect_recursive(item.child(idx))

        for i in range(root.childCount()):
            collect_recursive(root.child(i))
        return checked_items

    # ================= 设定树渲染 ================= #

    def _refresh_setting_tree(self: "NovelCreatorWindow"):
        """渲染左侧的世界观设定树。"""
        self.setting_tree.clear()
        self.setting_tree.setHeaderLabel("世界观设定 (勾选参与上下文)")
        self._updating_settings = True

        setting_dirs = getattr(
            self.workspace,
            "setting_dirs",
            ["公共设定", "人物设定", "名词设定", "地点设定", "其他设定"],
        )

        for cat in setting_dirs:
            cat_item = QTreeWidgetItem(self.setting_tree, [cat])
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            cat_item.setCheckState(0, Qt.CheckState.Checked)

            cat_path = os.path.join(self.workspace.settings_path, cat)
            if os.path.exists(cat_path):
                root_files = set(os.listdir(cat_path))
                if "index.json" in root_files:
                    index_item = QTreeWidgetItem(cat_item, ["\U0001f31f 总体概括 (index)"])
                    index_item.setData(
                        0,
                        Qt.ItemDataRole.UserRole,
                        os.path.join(cat_path, "index.json"),
                    )
                    index_item.setFlags(
                        index_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    index_item.setCheckState(0, Qt.CheckState.Unchecked)
                    index_item.setForeground(0, QColor("#E0B0FF"))

                folder_items: dict[str, QTreeWidgetItem] = {}
                for file_path in self._iter_setting_json_files(cat_path):
                    display_name = self._build_setting_display_name(cat_path, file_path)
                    parts = [p for p in display_name.split("/") if p]
                    parent_item = cat_item
                    if len(parts) >= 2:
                        folder_name = parts[0]
                        if folder_name not in folder_items:
                            folder_item = QTreeWidgetItem(cat_item, [folder_name])
                            folder_item.setData(
                                0,
                                Qt.ItemDataRole.UserRole,
                                "__folder__",
                            )
                            folder_item.setFlags(
                                folder_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                            )
                            folder_item.setCheckState(0, Qt.CheckState.Checked)
                            folder_items[folder_name] = folder_item
                        parent_item = folder_items[folder_name]
                        file_label = "/".join(parts[1:])
                    else:
                        file_label = display_name

                    file_item = QTreeWidgetItem(parent_item, [file_label])
                    file_item.setData(
                        0,
                        Qt.ItemDataRole.UserRole,
                        file_path,
                    )
                    file_item.setFlags(
                        file_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    file_item.setCheckState(0, Qt.CheckState.Checked)
                    if self.workspace.has_pending_modify(
                        self._setting_pending_id(file_path)
                    ):
                        file_item.setForeground(0, QColor("#FF4444"))

            add_btn = QTreeWidgetItem(cat_item, ["+ 新增设定..."])
            add_btn.setForeground(0, QColor(NODE_ADD_BTN))
            add_btn.setFlags(add_btn.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)

        self._updating_settings = False
        self.setting_tree.expandAll()

    # ================= 设定树勾选联动 ================= #

    def on_setting_item_changed(self: "NovelCreatorWindow", item, column):
        if self._updating_settings:
            return
        self._updating_settings = True

        state = item.checkState(column)
        if item.text(0).startswith("+"):
            self._updating_settings = False
            return

        def update_children(parent_item, target_state):
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if child.text(0).startswith("+"):
                    continue
                child.setCheckState(0, target_state)
                update_children(child, target_state)

        def update_parent(parent_item):
            if not parent_item:
                return
            if parent_item == self.setting_tree.invisibleRootItem():
                return
            all_checked = True
            all_unchecked = True
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if child.text(0).startswith("+"):
                    continue
                child_state = child.checkState(0)
                if child_state == Qt.CheckState.Checked:
                    all_unchecked = False
                elif child_state == Qt.CheckState.Unchecked:
                    all_checked = False
                else:
                    all_checked = False
                    all_unchecked = False
            if all_checked:
                parent_item.setCheckState(0, Qt.CheckState.Checked)
            elif all_unchecked:
                parent_item.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent_item.setCheckState(0, Qt.CheckState.PartiallyChecked)
            update_parent(parent_item.parent())

        update_children(item, state)
        update_parent(item.parent())

        self._updating_settings = False

    def get_checked_settings(self: "NovelCreatorWindow") -> list:
        checked_paths = [
            path for _, path in self._collect_checked_setting_items()
        ]
        return list(set(checked_paths))

    # ================= 设定树点击处理 ================= #

    def on_setting_node_clicked(self: "NovelCreatorWindow", item, column):
        if not self.workspace:
            return
        if hasattr(self, "summary_title_label"):
            base = "节点概要 (Summary - 保存至系统数据):"
            self.summary_title_label.setText(base)
            self.summary_title_label.setToolTip(base)

        # 点击目录节点（分类或一级目录）时，右侧应清空并禁用编辑区，避免显示残留内容
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        is_setting_file = (
            isinstance(file_path, str)
            and file_path.endswith(".json")
            and os.path.exists(file_path)
        )
        if (
            (item.parent() is None and not item.text(0).startswith("+"))
            or (not is_setting_file and not item.text(0).startswith("+"))
        ):
            self.current_editing_node = None
            self.current_editing_item = None
            self.current_setting_path = None

            self.summary_editor.clear()
            self.summary_editor.setEnabled(False)
            self.summary_editor.setReadOnly(False)

            self.content_editor.clear()
            self.content_editor.setEnabled(False)
            if hasattr(self, "word_count_label"):
                self.word_count_label.setText("当前字数: 0")

            self.btn_save.setEnabled(False)
            self.btn_generate.setEnabled(False)
            self.btn_rewrite.setEnabled(False)
            self.btn_regenerate_summary.setEnabled(False)
            return

        # 1. 如果点击的是新增按钮
        if item.text(0).startswith("+"):
            parent_item = item.parent()
            if not parent_item:
                return
            category = parent_item.text(0)

            new_name, ok = QInputDialog.getText(
                self, "新增设定", f"请输入【{category}】的设定名称:"  # type: ignore[arg-type]
            )
            if ok and new_name.strip():
                new_name = new_name.strip()
                cat_path = os.path.join(self.workspace.settings_path, category)
                template_path = os.path.join(cat_path, "template.json")
                new_file_path = os.path.join(cat_path, f"{new_name}.json")

                if os.path.exists(new_file_path):
                    QMessageBox.warning(
                        self, "警告", "该设定文件已存在，请换一个名称！"  # type: ignore[arg-type]
                    )
                    return

                try:
                    template_data = {}
                    if os.path.exists(template_path):
                        with open(template_path, "r", encoding="utf-8") as f:
                            template_data = json.load(f)

                    with open(new_file_path, "w", encoding="utf-8") as f:
                        json.dump(template_data, f, ensure_ascii=False, indent=4)

                    self.log_console.append(f"成功创建设定文件: {new_file_path}")
                    self.refresh_ui_from_workspace()
                except Exception as e:
                    QMessageBox.critical(
                        self, "错误", f"创建设定失败: {e}"  # type: ignore[arg-type]
                    )
            return

        # 2. 如果点击的是普通的设定文件或索引文件
        if not file_path or not os.path.exists(file_path):
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if os.path.basename(file_path) == "index.json":
                try:
                    data = json.loads(content)
                    overview = data.get("category_overview", "暂无概述")
                    items = data.get("items", [])

                    html_content = "<h2 style='color: #E0B0FF;'>\U0001f31f 分类总体概括</h2>"
                    html_content += (
                        f"<p><b>体系概述：</b><br>{overview}</p><hr>"
                    )
                    html_content += "<h3>包含的设定列表：</h3><ul>"
                    for it in items:
                        file_name = it.get("file_name", "未知").replace(".json", "")
                        brief = it.get("brief", "")
                        html_content += (
                            f"<li style='margin-bottom: 8px;'>"
                            f"<b>【{file_name}】</b>: {brief}</li>"
                        )
                    html_content += "</ul>"
                    html_content += (
                        "<br><p style='color: gray; font-size: 12px;'>"
                        "<i>提示：此目录为自动生成，如需更新，"
                        "请在左侧树状图右键点击分类名称重新生成。</i></p>"
                    )

                    self.summary_editor.setHtml(html_content)
                    self.summary_editor.setReadOnly(True)
                    self.btn_save.setEnabled(False)
                    self.log_console.append(
                        f"打开总体概括: "
                        f"{os.path.basename(os.path.dirname(file_path))}/index.json"
                    )

                except json.JSONDecodeError:
                    self.summary_editor.setPlainText(content)
                    self.summary_editor.setReadOnly(False)
                    self.btn_save.setEnabled(True)
                    self.log_console.append(
                        "<font color='orange'>警告：index.json 格式损坏，"
                        "已降级为纯文本显示。</font>"
                    )
            else:
                self.summary_editor.setPlainText(content)
                self.summary_editor.setReadOnly(False)
                self.btn_save.setEnabled(True)
                self.log_console.append(
                    f"打开设定文件: {os.path.basename(file_path)}"
                )

            # --- 修改部分开始 ---
            self.current_editing_node = None
            self.current_setting_path = file_path

            self.summary_editor.setEnabled(True)
            
            # 禁用正文区，清空遗留的文本，并重置字数显示
            self.content_editor.setEnabled(False)
            self.content_editor.clear()
            if hasattr(self, 'word_count_label'):
                self.word_count_label.setText("当前字数: 0")
                
            self.btn_generate.setEnabled(False)
            # --- 修改部分结束 ---
        except Exception as e:
            self.log_console.append(f"<font color='red'>读取设定失败: {e}</font>")

    # ================= 世界观设定右键菜单 ================= #

    def show_setting_context_menu(self: "NovelCreatorWindow", position):
        if not self.workspace:
            return

        menu = QMenu()
        item = self.setting_tree.itemAt(position)
        checked_setting_items = self._collect_checked_setting_items()

        gen_init_action = menu.addAction("\U0001f4a1 输入点子自动产生基础设定")
        gen_init_action.triggered.connect(
            lambda: self.open_world_building_dialog("init")
        )

        gen_sup_action = menu.addAction("\U0001f4a1 针对现有内容补充新设定")
        gen_sup_action.triggered.connect(
            lambda: self.open_world_building_dialog("supplement")
        )

        menu.addSeparator()

        if checked_setting_items:
            batch_modify_action = menu.addAction("📝 批量修改勾选设定")
            batch_modify_action.triggered.connect(
                lambda: self.open_batch_modify_setting_request_dialog(
                    checked_setting_items
                )
            )
            menu.addSeparator()

        if item and not item.parent() and not item.text(0).startswith("+"):
            category_name = item.text(0)
            gen_index_action = menu.addAction(
                f"\U0001f4dd 生成/更新【{category_name}】的总体概括 (index.json)"
            )
            gen_index_action.triggered.connect(
                lambda: self.start_index_generation(category_name)
            )
        elif item and item.parent() and not item.text(0).startswith("+"):
            file_path = item.data(0, Qt.ItemDataRole.UserRole)
            if file_path and os.path.exists(file_path):
                pending_id = self._setting_pending_id(file_path)
                if self.workspace.has_pending_modify(pending_id):
                    modify_action = menu.addAction("✏️ 修改内容")
                    modify_action.setEnabled(False)

                    merge_action = menu.addAction("🔄 进行合并")
                    merge_action.triggered.connect(
                        lambda: self.open_setting_merge_dialog(file_path)
                    )

                    discard_action = menu.addAction("❌ 丢弃修改")
                    discard_action.triggered.connect(
                        lambda: self.discard_pending_setting_modify(file_path)
                    )
                else:
                    modify_action = menu.addAction("✏️ 修改内容")
                    modify_action.triggered.connect(
                        lambda: self.open_modify_setting_request_dialog(file_path)
                    )

        menu.exec(self.setting_tree.viewport().mapToGlobal(position))

    def open_modify_setting_request_dialog(
        self: "NovelCreatorWindow", file_path: str
    ):
        """打开单个设定的修改要求输入框。"""
        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(self, "提示", "设定文件不存在。")  # type: ignore[arg-type]
            return

        requirement, ok = QInputDialog.getMultiLineText(
            self,
            "修改设定内容",
            f"请输入对设定【{os.path.basename(file_path)}】的修改要求：",
        )
        if ok and requirement.strip():
            append_writing_style = bool(getattr(self, "_append_writing_style_on_modify", False))
            self.start_modify_setting_content(
                file_path,
                requirement.strip(),
                append_writing_style=append_writing_style,
            )

    def start_modify_setting_content(
        self: "NovelCreatorWindow",
        file_path: str,
        requirement: str,
        append_writing_style: bool = False,
    ):
        """开始对单个设定文件发起修改请求。"""
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return
        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(self, "提示", "设定文件不存在。")
            return

        pending_id = self._setting_pending_id(file_path)
        if self.workspace.has_pending_modify(pending_id):
            QMessageBox.warning(self, "提示", "该设定已有待合并修改，请先处理。")
            return

        if not hasattr(self, "modifying_setting_nodes"):
            self.modifying_setting_nodes = set()
        if pending_id in self.modifying_setting_nodes:
            QMessageBox.warning(self, "提示", "该设定正在生成修改中，请稍候。")
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                original_text = f.read()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取设定失败: {e}")
            return

        if not original_text.strip():
            QMessageBox.warning(self, "提示", "该设定文件为空，暂无可修改内容。")
            return

        self.modifying_setting_nodes.add(pending_id)
        self.btn_save.setEnabled(False)
        display_name = os.path.basename(file_path)
        self.log_console.append(
            f"<font color='cyan'>开始修改设定【{display_name}】...</font>"
        )

        prompt = f"""你是一个专业的小说设定编辑助手。请根据用户要求，修改设定文件内容。

### 修改要求：
{requirement}

### 原设定内容：
{original_text}

### 输出要求：
1. 只输出修改后的完整文本
2. 保持 JSON 基本结构和可读性（若原文是 JSON）
3. 不要添加任何解释性文字
"""
        modify_system_instruction = self._build_modify_system_instruction(
            append_writing_style
        )
        self.setting_modify_thread = GenerateTaskThread(
            self.llm_client, prompt, modify_system_instruction
        )
        self.setting_modify_thread.progress_signal.connect(
            lambda msg: self._on_llm_progress(msg)
        )
        self.setting_modify_thread.success_signal.connect(
            lambda result: self.on_modify_setting_success(
                result, file_path, pending_id, original_text, requirement
            )
        )
        self.setting_modify_thread.error_signal.connect(
            lambda error: self.on_modify_setting_error(error, pending_id)
        )
        self.setting_modify_thread.start()

    def on_modify_setting_success(
        self: "NovelCreatorWindow",
        result: str,
        file_path: str,
        pending_id: str,
        original_text: str,
        requirement: str,
    ):
        if hasattr(self, "modifying_setting_nodes") and pending_id in self.modifying_setting_nodes:
            self.modifying_setting_nodes.remove(pending_id)
        try:
            is_refusal, reason = self._is_llm_refusal_or_no_change(result, original_text)
            if is_refusal:
                self.log_console.append(
                    f"<font color='orange'>⚠️ 设定【{os.path.basename(file_path)}】修改被跳过：{reason}</font>"
                )
                self.log_console.append(f"<font color='gray'>LLM 原始返回（前200字）：{result[:200]}...</font>")
                return

            self.workspace.save_pending_modify(
                pending_id, original_text, result, requirement
            )
            self.log_console.append(
                "<font color='green'>✅ 设定修改生成完成！请右键选择【进行合并】。</font>"
            )
            self._refresh_setting_tree()
        except Exception as e:
            self.log_console.append(f"<font color='red'>保存设定待合并修改失败: {e}</font>")
            QMessageBox.critical(self, "错误", f"保存设定待合并修改失败:\n{e}")
        finally:
            self.btn_save.setEnabled(True)

    def on_modify_setting_error(
        self: "NovelCreatorWindow", error_msg: str, pending_id: str
    ):
        if hasattr(self, "modifying_setting_nodes") and pending_id in self.modifying_setting_nodes:
            self.modifying_setting_nodes.remove(pending_id)
        self.log_console.append(f"<font color='red'>设定修改失败: {error_msg}</font>")
        QMessageBox.critical(self, "错误", f"设定修改过程中发生异常:\n{error_msg}")
        self.btn_save.setEnabled(True)

    def open_setting_merge_dialog(self: "NovelCreatorWindow", file_path: str):
        """打开设定文件修改的合并对话框。"""
        pending_id = self._setting_pending_id(file_path)
        pending_data = self.workspace.get_pending_modify(pending_id)
        if not pending_data:
            QMessageBox.warning(self, "提示", "没有找到待合并的设定修改。")
            return

        dialog = DiffMergeDialog(
            self,
            pending_id,
            pending_data["original_text"],
            pending_data["modified_text"],
            os.path.basename(file_path),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result_text = dialog.get_result()
            self.apply_setting_merge_result(file_path, pending_id, result_text)

    def apply_setting_merge_result(
        self: "NovelCreatorWindow", file_path: str, pending_id: str, result_text: str
    ):
        """应用设定合并结果到原文件。"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(result_text)
            self.workspace.delete_pending_modify(pending_id)

            if self.current_setting_path == file_path:
                self.summary_editor.setPlainText(result_text)

            self.log_console.append("<font color='green'>✅ 设定合并成功，内容已更新。</font>")
            self._refresh_setting_tree()
        except Exception as e:
            self.log_console.append(f"<font color='red'>设定合并失败: {e}</font>")
            QMessageBox.critical(self, "错误", f"设定合并失败:\n{e}")

    def discard_pending_setting_modify(
        self: "NovelCreatorWindow", file_path: str
    ):
        """丢弃设定文件的待合并修改。"""
        pending_id = self._setting_pending_id(file_path)
        reply = QMessageBox.question(
            self,
            "确认丢弃",
            "确定要丢弃该设定的修改吗？LLM 生成结果将被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.workspace.delete_pending_modify(pending_id)
            self._refresh_setting_tree()
            self.log_console.append("<font color='yellow'>已丢弃设定待合并修改。</font>")

    def open_batch_modify_setting_request_dialog(
        self: "NovelCreatorWindow", checked_setting_items: list[tuple]
    ):
        """打开批量修改设定的请求对话框（作用于勾选设定文件）。"""
        valid_items: list[tuple] = []
        skipped_info: list[str] = []
        if not hasattr(self, "modifying_setting_nodes"):
            self.modifying_setting_nodes = set()

        for _, file_path in checked_setting_items:
            if not file_path or not os.path.exists(file_path):
                skipped_info.append(f"跳过不存在文件：{file_path}")
                continue
            pending_id = self._setting_pending_id(file_path)
            base_name = os.path.basename(file_path)
            if self.workspace.has_pending_modify(pending_id):
                skipped_info.append(f"跳过【{base_name}】：已处于待合并状态")
                continue
            if pending_id in self.modifying_setting_nodes:
                skipped_info.append(f"跳过【{base_name}】：正在生成修改中")
                continue
            valid_items.append((file_path, pending_id))

        if not valid_items:
            detail = "\n".join(skipped_info) if skipped_info else "没有可处理的设定文件。"
            QMessageBox.information(self, "提示", detail)
            return

        class BatchSettingModifyDialog(QDialog):
            def __init__(self, parent, target_items, skipped, default_thread_count: int):
                super().__init__(parent)
                self.setWindowTitle("批量修改勾选设定")
                self.setMinimumWidth(600)
                self.setMinimumHeight(500)

                layout = QVBoxLayout()
                layout.addWidget(QLabel(f"准备处理 {len(target_items)} 个设定文件："))
                file_list = QListWidget()
                for path, _ in target_items:
                    file_list.addItem(QListWidgetItem(f"✅ {os.path.basename(path)}"))
                layout.addWidget(file_list)

                if skipped:
                    layout.addWidget(QLabel("\n以下设定将被跳过："))
                    skipped_list = QListWidget()
                    for info in skipped:
                        skipped_list.addItem(QListWidgetItem(f"⚠️ {info}"))
                    layout.addWidget(skipped_list)

                layout.addWidget(QLabel("\n请输入修改要求（将应用于全部设定）："))
                self.requirement_edit = QTextEdit()
                self.requirement_edit.setMinimumHeight(120)
                self.requirement_edit.setPlaceholderText("例如：统一补全字段并规范描述风格...")
                layout.addWidget(self.requirement_edit)

                self.append_writing_style_cb = QCheckBox("附加当前写作风格（写作Instruction）")
                self.append_writing_style_cb.setChecked(
                    bool(getattr(parent, "_append_writing_style_on_modify", False))
                )
                layout.addWidget(self.append_writing_style_cb)

                thread_layout = QHBoxLayout()
                thread_layout.addWidget(QLabel("请求线程数："))
                self.thread_count_spin = QSpinBox()
                self.thread_count_spin.setRange(1, 20)
                self.thread_count_spin.setValue(max(1, min(20, int(default_thread_count))))
                self.thread_count_spin.setSuffix(" 线程")
                thread_layout.addWidget(self.thread_count_spin)
                thread_layout.addStretch()
                layout.addLayout(thread_layout)

                btn_layout = QHBoxLayout()
                btn_layout.addStretch()
                cancel_btn = QPushButton("取消")
                ok_btn = QPushButton("开始批量修改")
                ok_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
                cancel_btn.clicked.connect(self.reject)
                ok_btn.clicked.connect(self.accept)
                btn_layout.addWidget(cancel_btn)
                btn_layout.addWidget(ok_btn)
                layout.addLayout(btn_layout)
                self.setLayout(layout)

            def get_requirement(self):
                return self.requirement_edit.toPlainText().strip()

            def get_thread_count(self):
                return self.thread_count_spin.value()

            def should_append_writing_style(self):
                return self.append_writing_style_cb.isChecked()

        dialog = BatchSettingModifyDialog(
            self,
            valid_items,
            skipped_info,
            getattr(self, "_batch_modify_thread_count", 3),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            requirement = dialog.get_requirement()
            if requirement:
                thread_count = dialog.get_thread_count()
                append_writing_style = dialog.should_append_writing_style()
                self._batch_modify_thread_count = thread_count
                self._append_writing_style_on_modify = append_writing_style
                self._save_batch_modify_thread_count(thread_count)
                self._save_modify_style_option(append_writing_style)
                self._save_workspace_instruction_profile()
                self.start_batch_modify_setting_content(
                    valid_items,
                    requirement,
                    thread_count=thread_count,
                    append_writing_style=append_writing_style,
                )

    def start_batch_modify_setting_content(
        self: "NovelCreatorWindow",
        valid_items: list[tuple],
        requirement: str,
        thread_count: int = 3,
        append_writing_style: bool = False,
    ):
        """开始批量修改设定文件。"""
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return
        if not hasattr(self, "is_batch_modifying_settings"):
            self.is_batch_modifying_settings = False
        if self.is_batch_modifying_settings:
            QMessageBox.warning(self, "提示", "正在进行批量设定修改，请稍候。")
            return

        self.is_batch_modifying_settings = True
        self.batch_setting_modify_queue = valid_items.copy()
        self.batch_setting_modify_requirement = requirement
        self.batch_setting_modify_append_writing_style = append_writing_style
        self.batch_setting_modify_success_count = 0
        self.batch_setting_modify_fail_count = 0
        self.batch_setting_modify_max_workers = max(1, min(20, int(thread_count)))
        self.batch_setting_modify_inflight = 0
        self.batch_setting_modify_threads = {}
        self.btn_save.setEnabled(False)

        self.log_console.append(
            f"<font color='cyan'>🚀 开始批量修改设定，共 {len(self.batch_setting_modify_queue)} 个，"
            f"并发 {self.batch_setting_modify_max_workers} 线程...</font>"
        )
        self._process_next_batch_setting_modify()

    def _process_next_batch_setting_modify(self: "NovelCreatorWindow"):
        if not self.is_batch_modifying_settings:
            return
        if not hasattr(self, "modifying_setting_nodes"):
            self.modifying_setting_nodes = set()

        while (
            self.batch_setting_modify_queue
            and self.batch_setting_modify_inflight < self.batch_setting_modify_max_workers
        ):
            file_path, pending_id = self.batch_setting_modify_queue.pop(0)
            display_name = os.path.basename(file_path)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    original_text = f.read()
            except Exception as e:
                self.log_console.append(
                    f"<font color='red'>❌ 读取设定失败【{display_name}】: {e}</font>"
                )
                self.batch_setting_modify_fail_count += 1
                continue

            if not original_text.strip():
                self.log_console.append(
                    f"<font color='orange'>跳过【{display_name}】：文件内容为空</font>"
                )
                self.batch_setting_modify_fail_count += 1
                continue

            self.modifying_setting_nodes.add(pending_id)
            prompt = f"""你是一个专业的小说设定编辑助手。请根据用户要求，修改设定文件内容。

### 修改要求：
{self.batch_setting_modify_requirement}

### 原设定内容：
{original_text}

### 输出要求：
1. 只输出修改后的完整文本
2. 保持 JSON 基本结构和可读性（若原文是 JSON）
3. 不要添加任何解释性文字
"""
            modify_system_instruction = self._build_modify_system_instruction(
                self.batch_setting_modify_append_writing_style
            )
            thread = GenerateTaskThread(
                self.llm_client, prompt, modify_system_instruction
            )
            thread.progress_signal.connect(lambda msg: self._on_llm_progress(msg))
            thread.success_signal.connect(
                lambda result, p=file_path, pid=pending_id, o=original_text: self.on_batch_modify_setting_success(
                    result, p, pid, o
                )
            )
            thread.error_signal.connect(
                lambda error, p=file_path, pid=pending_id: self.on_batch_modify_setting_error(
                    error, p, pid
                )
            )
            self.batch_setting_modify_threads[pending_id] = thread
            self.batch_setting_modify_inflight += 1
            thread.start()

        self._finalize_batch_setting_modify_if_done()

    def on_batch_modify_setting_success(
        self: "NovelCreatorWindow",
        result: str,
        file_path: str,
        pending_id: str,
        original_text: str,
    ):
        if hasattr(self, "modifying_setting_nodes") and pending_id in self.modifying_setting_nodes:
            self.modifying_setting_nodes.remove(pending_id)
        self.batch_setting_modify_threads.pop(pending_id, None)
        self.batch_setting_modify_inflight = max(0, self.batch_setting_modify_inflight - 1)
        try:
            is_refusal, reason = self._is_llm_refusal_or_no_change(result, original_text)
            if is_refusal:
                self.batch_setting_modify_fail_count += 1
                self.log_console.append(
                    f"<font color='orange'>⚠️ 设定【{os.path.basename(file_path)}】修改被跳过：{reason}</font>"
                )
                self.log_console.append(f"<font color='gray'>LLM 原始返回（前200字）：{result[:200]}...</font>")
                return

            self.workspace.save_pending_modify(
                pending_id,
                original_text,
                result,
                self.batch_setting_modify_requirement,
            )
            self.batch_setting_modify_success_count += 1
            self.log_console.append(
                f"<font color='green'>✅ 设定【{os.path.basename(file_path)}】修改成功</font>"
            )
        except Exception as e:
            self.batch_setting_modify_fail_count += 1
            self.log_console.append(
                f"<font color='red'>❌ 保存设定修改失败【{os.path.basename(file_path)}】: {e}</font>"
            )
        finally:
            self._process_next_batch_setting_modify()

    def on_batch_modify_setting_error(
        self: "NovelCreatorWindow", error_msg: str, file_path: str, pending_id: str
    ):
        if hasattr(self, "modifying_setting_nodes") and pending_id in self.modifying_setting_nodes:
            self.modifying_setting_nodes.remove(pending_id)
        self.batch_setting_modify_threads.pop(pending_id, None)
        self.batch_setting_modify_inflight = max(0, self.batch_setting_modify_inflight - 1)
        self.batch_setting_modify_fail_count += 1
        self.log_console.append(
            f"<font color='red'>❌ 设定修改失败【{os.path.basename(file_path)}】: {error_msg}</font>"
        )
        self._process_next_batch_setting_modify()

    def _finalize_batch_setting_modify_if_done(self: "NovelCreatorWindow"):
        if not getattr(self, "is_batch_modifying_settings", False):
            return
        if self.batch_setting_modify_queue or self.batch_setting_modify_inflight > 0:
            return

        self.is_batch_modifying_settings = False
        self.btn_save.setEnabled(True)
        self.log_console.append(
            f"<b><font color='green'>🎉 批量设定修改完成！成功：{self.batch_setting_modify_success_count}，"
            f"失败：{self.batch_setting_modify_fail_count}</font></b>"
        )
        QMessageBox.information(
            self,
            "批量设定修改完成",
            "批量设定修改已结束。\n"
            f"成功：{self.batch_setting_modify_success_count}\n"
            f"失败：{self.batch_setting_modify_fail_count}\n\n"
            "有待合并结果的设定将显示为红色，请右键执行【进行合并】。",
        )
        self._refresh_setting_tree()

    # ================= 世界观生成 ================= #

    def open_world_building_dialog(self: "NovelCreatorWindow", mode: str = "init"):
        if not self.llm_client:
            QMessageBox.warning(
                self, "未配置", "请先在设置中配置大模型 API。"  # type: ignore[arg-type]
            )
            return

        title = "全新生成背景资料" if mode == "init" else "针对性补充背景资料"
        hint = (
            "请输入您的核心点子或世界观框架概念"
            "（例如：一个由猫咪统治的赛博朋克城市）："
        )

        dialog = IdeaInputDialog(self, title, hint)  # type: ignore[arg-type]

        if dialog.exec() == QDialog.DialogCode.Accepted:
            idea = dialog.get_text()
            if idea.strip():
                self.log_console.append(
                    f"启动设定生成引擎，模式：{mode}。后台处理中，请稍候..."
                )
                self.btn_save.setEnabled(False)

                default_p1 = (
                    "你是一个资深的小说世界观设计师，擅长根据简短的点子构建丰富的背景设定。"
                    "请根据以下核心概念，帮我规划出一个适合小说创作的世界观分类体系"
                    "（例如：种族、政治、科技、宗教等），并列出每个分类下的关键要素名称。"
                )
                default_p2 = (
                    "请根据之前规划的世界观分类体系，针对每个要素进行详细的设定扩写，"
                    "内容可以包含但不限于：外貌特征、社会结构、历史背景、与其他要素的关系等。"
                    "请尽量丰富细节，帮助我构建一个生动立体的小说世界。"
                )

                p1_tpl = self._get_or_create_prompt_template(
                    "world_build_list.txt", default_p1, "世界观分类规划"
                )
                p2_tpl = self._get_or_create_prompt_template(
                    "world_build_detail.txt", default_p2, "世界观细节扩写"
                )

                self.wb_thread = WorldBuildingThread(
                    llm_client=self.llm_client,
                    workspace=self.workspace,
                    idea=idea.strip(),
                    prompt_1_tpl=p1_tpl,
                    prompt_2_tpl=p2_tpl,
                    mode=mode,
                )
                self.wb_thread.progress_signal.connect(
                    lambda msg: self.log_console.append(
                        f"<font color='cyan'>{msg}</font>"
                    )
                )
                self.wb_thread.success_signal.connect(self.on_world_building_success)
                self.wb_thread.error_signal.connect(self.on_world_building_error)
                self.wb_thread.start()

    def on_world_building_success(self: "NovelCreatorWindow"):
        self.log_console.append(
            "<b><font color='green'>"
            "\U0001f389 自动设定生成完毕！已将设定文件分配至对应目录。"
            "</font></b>"
        )
        self.refresh_ui_from_workspace()
        QMessageBox.information(
            self, "生成完成", "世界观设定生成完毕，请在左侧目录查看。"  # type: ignore[arg-type]
        )
        self._send_system_notification(
            "世界观生成完成",
            "世界观设定生成任务已完成。",
        )
        self.btn_save.setEnabled(True)

    def on_world_building_error(self: "NovelCreatorWindow", err_msg: str):
        self.log_console.append(
            f"<font color='red'>世界观生成中断或失败: {err_msg}</font>"
        )
        QMessageBox.warning(
            self, "生成失败", f"流程中断:\n{err_msg}"  # type: ignore[arg-type]
        )
        self.btn_save.setEnabled(True)

    # ================= 索引生成 ================= #

    def start_index_generation(self: "NovelCreatorWindow", category: str):
        if not self.llm_client:
            QMessageBox.warning(
                self, "未配置", "请先在设置中配置大模型 API。"  # type: ignore[arg-type]
            )
            return

        self.log_console.append(f"开始为【{category}】生成总体概括目录...")

        default_index_prompt = (
            "请根据以下内容，帮我提取出一份结构化的概括目录（JSON格式）：\n{all_content}"
        )
        prompt_tpl = self._get_or_create_prompt_template(
            "index_generate.txt",
            default_index_prompt,
            f"{category}的总体概括索引生成",
        )

        self.index_thread = IndexGenerateThread(
            self.llm_client, self.workspace, category, prompt_tpl
        )
        self.index_thread.progress_signal.connect(
            lambda msg: self.log_console.append(f"<font color='cyan'>{msg}</font>")
        )
        self.index_thread.success_signal.connect(self.on_index_generation_success)
        self.index_thread.error_signal.connect(self.on_index_generation_error)
        self.index_thread.start()

    def on_index_generation_success(
        self: "NovelCreatorWindow", category: str, index_content: str
    ):
        cat_path = os.path.join(self.workspace.settings_path, category)
        index_path = os.path.join(cat_path, "index.json")

        try:
            parsed_json = json.loads(index_content)
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(parsed_json, f, ensure_ascii=False, indent=4)

            self.log_console.append(
                f"<b><font color='green'>"
                f"\U0001f389 【{category}】总体概括目录生成完毕！"
                f"</font></b>"
            )
            self.refresh_ui_from_workspace()
            self._send_system_notification(
                "索引生成完成",
                f"【{category}】总体概括目录已生成完成。",
            )
        except Exception as e:
            self.log_console.append(
                f"<font color='red'>保存 index.json 时出错: {e}</font>"
            )

    def on_index_generation_error(self: "NovelCreatorWindow", err_msg: str):
        self.log_console.append(f"<font color='red'>索引生成失败: {err_msg}</font>")
        QMessageBox.warning(
            self, "生成失败", f"生成索引流程中断:\n{err_msg}"  # type: ignore[arg-type]
        )
