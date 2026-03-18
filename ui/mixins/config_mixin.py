"""
ConfigMixin —— 配置文件读写与系统状态管理。
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QDialog, QInputDialog

from core.llm_client import LLMClient
from ui.settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class ConfigMixin:
    """管理 setting.json / sys_state.json / prompt 模板等配置读写。"""

    # ---- 以下方法中 self 实际为 NovelCreatorWindow 实例 ----

    def _load_config(self: "NovelCreatorWindow") -> dict:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(base_dir, "conf", "setting.json")

        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"读取配置文件失败: {e}")
        return {}

    def _get_or_create_prompt_template(
        self: "NovelCreatorWindow",
        template_name: str,
        default_text: str,
        desc: str,
    ) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        prompts_dir = os.path.join(base_dir, "data", "prompts")
        os.makedirs(prompts_dir, exist_ok=True)

        file_path = os.path.join(prompts_dir, template_name)

        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()

        text, ok = QInputDialog.getMultiLineText(
            self,  # type: ignore[arg-type]
            "需要初始化 Prompt 模板",
            f"未找到模板文件：{template_name}\n用途：{desc}\n请核对并确认模板内容：",
            default_text,
        )
        final_text = text if ok and text.strip() else default_text

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(final_text)
        except Exception as e:
            self.log_console.append(f"<font color='red'>保存模板文件失败: {e}</font>")

        return final_text

    def _get_sys_state_path(self: "NovelCreatorWindow") -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base_dir, "conf", "sys_state.json")

    def _load_sys_state(self: "NovelCreatorWindow") -> dict:
        path = self._get_sys_state_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"recent_workspaces": []}

    def _save_sys_state(self: "NovelCreatorWindow", workspace_path: str):
        state = self._load_sys_state()
        recents = state.get("recent_workspaces", [])
        if workspace_path in recents:
            recents.remove(workspace_path)
        recents.insert(0, workspace_path)
        state["recent_workspaces"] = recents[:10]

        path = self._get_sys_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存系统状态失败: {e}")

    def open_settings_dialog(self: "NovelCreatorWindow"):
        dialog = SettingsDialog(self)  # type: ignore[arg-type]
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.log_console.append("系统配置已更新，正在重新初始化模型客户端...")
            self.config = self._load_config()
            self.llm_client = LLMClient(self.config) if self.config else None
            self.log_console.append("模型客户端初始化完成！")
