"""
WorkspaceMixin —— 工作区的新建、加载、重载逻辑。
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from core.workspace_manager import WorkspaceManager

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class WorkspaceMixin:
    """处理工作区的新建 / 加载 / 重载操作。"""

    def new_workspace(self: "NovelCreatorWindow"):
        folder_path = QFileDialog.getExistingDirectory(
            self, "选择空文件夹创建新工作区"  # type: ignore[arg-type]
        )
        if folder_path:
            if os.listdir(folder_path):
                QMessageBox.warning(
                    self,  # type: ignore[arg-type]
                    "操作取消",
                    "为了防止意外覆盖文件，请选择一个【空文件夹】来初始化新工作区！",
                )
                return

            try:
                temp_workspace = WorkspaceManager(folder_path)
                temp_workspace.init_workspace()
                self._load_workspace_by_path(folder_path)
            except Exception as e:
                QMessageBox.critical(
                    self, "错误", f"初始化新工作区失败:\n{str(e)}"  # type: ignore[arg-type]
                )

    def load_workspace(self: "NovelCreatorWindow"):
        folder_path = QFileDialog.getExistingDirectory(
            self, "选择已有的工作区目录"  # type: ignore[arg-type]
        )
        if folder_path:
            self._load_workspace_by_path(folder_path)

    def _load_workspace_by_path(self: "NovelCreatorWindow", folder_path: str):
        try:
            self.workspace = WorkspaceManager(folder_path)
            self._save_sys_state(folder_path)

            self.log_console.append(f"成功加载工作区: {folder_path}")
            self.setWindowTitle(f"AI小说创作器 - {os.path.basename(folder_path)}")

            self.refresh_ui_from_workspace()
        except Exception as e:
            QMessageBox.critical(
                self, "错误", f"无法加载工作区:\n{str(e)}"  # type: ignore[arg-type]
            )
            self.log_console.append(f"<font color='red'>工作区加载失败: {e}</font>")

    def reload_workspace(self: "NovelCreatorWindow"):
        if not self.workspace:
            QMessageBox.information(
                self, "提示", "当前未打开任何工作区，无法重载。"  # type: ignore[arg-type]
            )
            return

        reply = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "重载工作区",
            "确定要重新加载当前工作区吗？\n警告：由于是强制读取本地硬盘配置，"
            "您当前未保存的所有修改（包含正在编辑的正文和概要）都将丢失！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.log_console.append("开始重载工作区...")
            self._load_workspace_by_path(self.workspace.workspace_path)
