"""
SettingTreeMixin —— 世界观设定树的渲染、交互、右键菜单以及世界观 / 索引生成。
"""
from __future__ import annotations

import json
import os
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

from core.context_builder import ContextBuilder
from ui.theme import NODE_ADD_BTN
from ui.dialogs import IdeaInputDialog
from ui.workers import IndexGenerateThread, WorldBuildingThread

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class SettingTreeMixin:
    """世界观设定树的全部交互与 AI 生成逻辑。"""

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
                files = os.listdir(cat_path)
                if "index.json" in files:
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

                for file in files:
                    if file.endswith(".json") and file not in [
                        "template.json",
                        "index.json",
                    ]:
                        file_item = QTreeWidgetItem(
                            cat_item, [file.replace(".json", "")]
                        )
                        file_item.setData(
                            0,
                            Qt.ItemDataRole.UserRole,
                            os.path.join(cat_path, file),
                        )
                        file_item.setFlags(
                            file_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                        )
                        file_item.setCheckState(0, Qt.CheckState.Checked)

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

        if item.parent() is None:
            for i in range(item.childCount()):
                child = item.child(i)
                if not child.text(0).startswith("+"):
                    child.setCheckState(0, state)
        else:
            parent = item.parent()
            all_checked = True
            all_unchecked = True
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.text(0).startswith("+"):
                    continue
                if child.checkState(0) == Qt.CheckState.Checked:
                    all_unchecked = False
                elif child.checkState(0) == Qt.CheckState.Unchecked:
                    all_checked = False
                else:
                    all_checked = False
                    all_unchecked = False

            if all_checked:
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif all_unchecked:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)

        self._updating_settings = False

    def get_checked_settings(self: "NovelCreatorWindow") -> list:
        checked_paths: list[str] = []
        root = self.setting_tree.invisibleRootItem()
        if not root:
            return checked_paths

        for i in range(root.childCount()):
            cat_item = root.child(i)
            for j in range(cat_item.childCount()):
                file_item = cat_item.child(j)
                if file_item.checkState(0) == Qt.CheckState.Checked:
                    path = file_item.data(0, Qt.ItemDataRole.UserRole)
                    if path and os.path.exists(path):
                        checked_paths.append(path)
        return list(set(checked_paths))

    # ================= 设定树点击处理 ================= #

    def on_setting_node_clicked(self: "NovelCreatorWindow", item, column):
        if not self.workspace:
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
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
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

            self.current_editing_node = None
            self.current_setting_path = file_path

            self.summary_editor.setEnabled(True)
            self.content_editor.setEnabled(False)
            self.btn_generate.setEnabled(False)

        except Exception as e:
            self.log_console.append(f"<font color='red'>读取设定失败: {e}</font>")

    # ================= 世界观设定右键菜单 ================= #

    def show_setting_context_menu(self: "NovelCreatorWindow", position):
        if not self.workspace:
            return

        menu = QMenu()
        item = self.setting_tree.itemAt(position)

        gen_init_action = menu.addAction("\U0001f4a1 输入点子自动产生基础设定")
        gen_init_action.triggered.connect(
            lambda: self.open_world_building_dialog("init")
        )

        gen_sup_action = menu.addAction("\U0001f4a1 针对现有内容补充新设定")
        gen_sup_action.triggered.connect(
            lambda: self.open_world_building_dialog("supplement")
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

        menu.exec(self.setting_tree.viewport().mapToGlobal(position))

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
        except Exception as e:
            self.log_console.append(
                f"<font color='red'>保存 index.json 时出错: {e}</font>"
            )

    def on_index_generation_error(self: "NovelCreatorWindow", err_msg: str):
        self.log_console.append(f"<font color='red'>索引生成失败: {err_msg}</font>")
        QMessageBox.warning(
            self, "生成失败", f"生成索引流程中断:\n{err_msg}"  # type: ignore[arg-type]
        )
