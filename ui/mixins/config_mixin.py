"""
ConfigMixin —— 配置文件读写与系统状态管理。
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QDialog, QInputDialog
from PyQt6.QtCore import QByteArray, QRect

from core.llm_client import LLMClient
from ui.settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class ConfigMixin:
    """管理 setting.json / sys_state.json / prompt 模板等配置读写。"""

    # ---- 以下方法中 self 实际为 NovelCreatorWindow 实例 ----

    def _get_default_writing_instruction(self: "NovelCreatorWindow") -> str:
        return (
            "你是一个专业的AI小说家。你的输出必须纯粹是小说情节文本，"
            "严禁包含任何前言、后语、剧情解释或'已为您生成'之类的助手客套话。"
        )

    def _get_default_summary_instruction(self: "NovelCreatorWindow") -> str:
        return "你是一个专业的小说编辑，擅长为小说节点生成精炼、准确的概要。"

    def _get_default_modify_instruction(self: "NovelCreatorWindow") -> str:
        return (
            "你是一个专业的小说改写编辑。你必须严格遵循用户的修改要求，"
            "对原文进行高质量改写。只输出修改后的完整正文，不要附加解释。"
        )

    def _get_instruction_history(
        self: "NovelCreatorWindow", history_key: str
    ) -> list[dict]:
        text_cfg = (self.config or {}).get("text_api", {})
        raw_history = text_cfg.get(history_key, [])
        history: list[dict] = []
        for item in raw_history:
            if isinstance(item, str):
                history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                history.append(
                    {
                        "name": str(item.get("name", "")),
                        "content": str(item.get("content", "")),
                    }
                )
        return history

    def _find_instruction_content_by_name(
        self: "NovelCreatorWindow", history_key: str, template_name: str
    ) -> str | None:
        if not template_name:
            return None
        for inst in self._get_instruction_history(history_key):
            if (inst.get("name") or "").strip() == template_name:
                content = (inst.get("content") or "").strip()
                if content:
                    return content
        return None

    def _resolve_instruction_name_from_content(
        self: "NovelCreatorWindow",
        history_key: str,
        content: str,
        fallback_name: str,
    ) -> str:
        normalized = (content or "").strip()
        if not normalized:
            return fallback_name
        for inst in self._get_instruction_history(history_key):
            if (inst.get("content") or "").strip() == normalized:
                name = (inst.get("name") or "").strip()
                if name:
                    return name
        return fallback_name

    def _ensure_named_instruction_in_config(
        self: "NovelCreatorWindow",
        history_key: str,
        content_key: str,
        fallback_name: str,
        default_content: str,
    ):
        text_cfg = (self.config or {}).setdefault("text_api", {})
        target_content = str(text_cfg.get(content_key, default_content))
        raw_history = text_cfg.get(history_key, [])
        normalized_history = []
        for item in raw_history:
            if isinstance(item, str):
                normalized_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                normalized_history.append(
                    {
                        "name": str(item.get("name", "")),
                        "content": str(item.get("content", "")),
                    }
                )

        has_named_match = any(
            (it.get("name") or "").strip() == fallback_name
            and (it.get("content") or "").strip() == target_content.strip()
            for it in normalized_history
        )
        if not has_named_match:
            normalized_history.insert(
                0,
                {"name": fallback_name, "content": target_content},
            )
        text_cfg[history_key] = normalized_history

    def _get_current_instruction_profile(self: "NovelCreatorWindow") -> dict:
        text_cfg = (self.config or {}).get("text_api", {})
        writing_content = text_cfg.get(
            "instructions", self._get_default_writing_instruction()
        )
        modify_content = text_cfg.get(
            "modify_instructions", self._get_default_modify_instruction()
        )
        return {
            "writing_instruction_name": self._resolve_instruction_name_from_content(
                "instructions_history",
                writing_content,
                "默认写作Instruction",
            ),
            "modify_instruction_name": self._resolve_instruction_name_from_content(
                "modify_instructions_history",
                modify_content,
                "默认编辑Instruction",
            ),
            "append_writing_style_on_modify": bool(
                getattr(self, "_append_writing_style_on_modify", False)
            ),
        }

    def _save_workspace_instruction_profile(self: "NovelCreatorWindow"):
        if not getattr(self, "workspace", None):
            return
        try:
            self._ensure_named_instruction_in_config(
                "instructions_history",
                "instructions",
                "默认写作Instruction",
                self._get_default_writing_instruction(),
            )
            self._ensure_named_instruction_in_config(
                "modify_instructions_history",
                "modify_instructions",
                "默认编辑Instruction",
                self._get_default_modify_instruction(),
            )
            self.workspace.save_instruction_profile(
                self._get_current_instruction_profile()
            )
        except Exception as e:
            self.log_console.append(
                f"<font color='orange'>保存工作区 Instruction 绑定失败: {e}</font>"
            )

    def _apply_workspace_instruction_profile(self: "NovelCreatorWindow"):
        if not getattr(self, "workspace", None):
            return

        profile = self.workspace.load_instruction_profile()
        if not profile:
            self._save_workspace_instruction_profile()
            return

        text_cfg = (self.config or {}).setdefault("text_api", {})
        fallback_writing = text_cfg.get(
            "instructions", self._get_default_writing_instruction()
        )
        fallback_modify = text_cfg.get(
            "modify_instructions", self._get_default_modify_instruction()
        )

        warnings: list[str] = []

        writing_name = str(profile.get("writing_instruction_name", "")).strip()
        writing_content = self._find_instruction_content_by_name(
            "instructions_history", writing_name
        )
        if writing_name and writing_content:
            text_cfg["instructions"] = writing_content
        else:
            text_cfg["instructions"] = fallback_writing
            if writing_name:
                warnings.append(
                    f"未找到写作 Instruction 名称：{writing_name}，已回退为默认写作 Instruction。"
                )

        modify_name = str(profile.get("modify_instruction_name", "")).strip()
        modify_content = self._find_instruction_content_by_name(
            "modify_instructions_history", modify_name
        )
        if modify_name and modify_content:
            text_cfg["modify_instructions"] = modify_content
        else:
            text_cfg["modify_instructions"] = fallback_modify
            if modify_name:
                warnings.append(
                    f"未找到编辑 Instruction 名称：{modify_name}，已回退为默认编辑 Instruction。"
                )

        self._append_writing_style_on_modify = bool(
            profile.get(
                "append_writing_style_on_modify",
                getattr(self, "_append_writing_style_on_modify", False),
            )
        )
        self._save_modify_style_option(self._append_writing_style_on_modify)

        self.llm_client = LLMClient(self.config) if self.config else None

        if warnings:
            from PyQt6.QtWidgets import QMessageBox

            msg = "\n".join(warnings)
            self.log_console.append(f"<font color='orange'>{msg}</font>")
            QMessageBox.warning(self, "Instruction 绑定告警", msg)  # type: ignore[arg-type]

    def _load_config(self: "NovelCreatorWindow") -> dict:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(base_dir, "conf", "setting.json")

        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                text_cfg = cfg.setdefault("text_api", {})
                default_writing = self._get_default_writing_instruction()
                if not text_cfg.get("instructions"):
                    text_cfg["instructions"] = default_writing
                raw_writing_history = text_cfg.get("instructions_history", [])
                normalized_writing_history = []
                for item in raw_writing_history:
                    if isinstance(item, str):
                        normalized_writing_history.append({"name": "", "content": item})
                    elif isinstance(item, dict):
                        normalized_writing_history.append(
                            {
                                "name": str(item.get("name", "")),
                                "content": str(item.get("content", "")),
                            }
                        )
                if not any(
                    (it.get("name") or "").strip() == "默认写作Instruction"
                    and (it.get("content") or "").strip()
                    == (text_cfg["instructions"] or "").strip()
                    for it in normalized_writing_history
                ):
                    normalized_writing_history.insert(
                        0,
                        {
                            "name": "默认写作Instruction",
                            "content": text_cfg["instructions"],
                        },
                    )
                text_cfg["instructions_history"] = normalized_writing_history

                default_modify = self._get_default_modify_instruction()
                if not text_cfg.get("modify_instructions"):
                    text_cfg["modify_instructions"] = default_modify

                raw_modify_history = text_cfg.get("modify_instructions_history", [])
                normalized_history = []
                for item in raw_modify_history:
                    if isinstance(item, str):
                        normalized_history.append({"name": "", "content": item})
                    elif isinstance(item, dict):
                        normalized_history.append(
                            {
                                "name": str(item.get("name", "")),
                                "content": str(item.get("content", "")),
                            }
                        )
                if not any(
                    (it.get("name") or "").strip() == "默认编辑Instruction"
                    and (it.get("content") or "").strip()
                    == (text_cfg["modify_instructions"] or "").strip()
                    for it in normalized_history
                ):
                    normalized_history.insert(
                        0,
                        {
                            "name": "默认编辑Instruction",
                            "content": text_cfg["modify_instructions"],
                        },
                    )
                text_cfg["modify_instructions_history"] = normalized_history

                raw_caps = text_cfg.get("model_capabilities", [])
                if isinstance(raw_caps, str):
                    caps = [
                        item.strip()
                        for item in raw_caps.replace(";", ",").split(",")
                        if item.strip()
                    ]
                elif isinstance(raw_caps, list):
                    caps = [str(item).strip() for item in raw_caps if str(item).strip()]
                else:
                    caps = []
                text_cfg["model_capabilities"] = caps

                raw_context_size = text_cfg.get("model_context_size", 8192)
                try:
                    context_size = int(raw_context_size)
                except Exception:
                    context_size = 8192
                if context_size <= 0:
                    context_size = 8192
                text_cfg["model_context_size"] = context_size

                raw_catalog = text_cfg.get("model_meta_catalog", {})
                normalized_catalog = {}
                if isinstance(raw_catalog, dict):
                    for model_name, meta in raw_catalog.items():
                        model_key = str(model_name).strip()
                        if not model_key:
                            continue
                        meta_dict = meta if isinstance(meta, dict) else {}
                        model_caps = meta_dict.get("capabilities", [])
                        if isinstance(model_caps, str):
                            model_caps = [
                                item.strip()
                                for item in model_caps.replace(";", ",").split(",")
                                if item.strip()
                            ]
                        elif isinstance(model_caps, list):
                            model_caps = [
                                str(item).strip()
                                for item in model_caps
                                if str(item).strip()
                            ]
                        else:
                            model_caps = []
                        model_ctx = meta_dict.get("context_size", context_size)
                        try:
                            model_ctx = int(model_ctx)
                        except Exception:
                            model_ctx = context_size
                        if model_ctx <= 0:
                            model_ctx = context_size
                        normalized_catalog[model_key] = {
                            "capabilities": model_caps,
                            "context_size": model_ctx,
                        }
                text_cfg["model_meta_catalog"] = normalized_catalog
                current_model = str(text_cfg.get("model", "")).strip()
                if current_model and current_model not in normalized_catalog:
                    text_cfg["model_meta_catalog"][current_model] = {
                        "capabilities": caps,
                        "context_size": context_size,
                    }
                raw_profile = str(
                    text_cfg.get("world_context_compression_profile", "balanced")
                ).strip().lower()
                if raw_profile not in {"conservative", "balanced", "aggressive"}:
                    raw_profile = "balanced"
                text_cfg["world_context_compression_profile"] = raw_profile
                return cfg
            except Exception as e:
                print(f"读取配置文件失败: {e}")
        return {
            "text_api": {
                "instructions": self._get_default_writing_instruction(),
                "instructions_history": [
                    {
                        "name": "默认写作Instruction",
                        "content": self._get_default_writing_instruction(),
                    }
                ],
                "modify_instructions": self._get_default_modify_instruction(),
                "modify_instructions_history": [
                    {
                        "name": "默认编辑Instruction",
                        "content": self._get_default_modify_instruction(),
                    }
                ],
                "model_capabilities": [],
                "model_context_size": 8192,
                "world_context_compression_profile": "balanced",
                "model_meta_catalog": {},
            }
        }

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

    def _write_sys_state(self: "NovelCreatorWindow", state: dict):
        path = self._get_sys_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存系统状态失败: {e}")

    def _save_sys_state(self: "NovelCreatorWindow", workspace_path: str):
        state = self._load_sys_state()
        recents = state.get("recent_workspaces", [])
        if workspace_path in recents:
            recents.remove(workspace_path)
        recents.insert(0, workspace_path)
        state["recent_workspaces"] = recents[:10]
        self._write_sys_state(state)

    def _save_window_ui_state(self: "NovelCreatorWindow"):
        """保存主窗口尺寸/状态与分栏位置。"""
        state = self._load_sys_state()
        ui_state = state.get("ui_state", {})

        if self.isMaximized():
            geom = self.normalGeometry()
        else:
            geom = self.geometry()
        ui_state["window_geometry"] = [geom.x(), geom.y(), geom.width(), geom.height()]
        ui_state["is_maximized"] = bool(self.isMaximized())

        if hasattr(self, "main_splitter"):
            ui_state["main_splitter_sizes"] = self.main_splitter.sizes()
            ui_state["main_splitter_state"] = bytes(
                self.main_splitter.saveState().toBase64()
            ).decode("ascii")
        if hasattr(self, "editor_splitter"):
            ui_state["editor_splitter_sizes"] = self.editor_splitter.sizes()
            ui_state["editor_splitter_state"] = bytes(
                self.editor_splitter.saveState().toBase64()
            ).decode("ascii")

        state["ui_state"] = ui_state
        self._write_sys_state(state)

    def _restore_window_ui_state(self: "NovelCreatorWindow"):
        """恢复主窗口尺寸/状态与分栏位置。"""
        state = self._load_sys_state()
        ui_state = state.get("ui_state", {})
        if not isinstance(ui_state, dict):
            return

        geom_vals = ui_state.get("window_geometry")
        if (
            isinstance(geom_vals, list)
            and len(geom_vals) == 4
            and all(isinstance(v, int) for v in geom_vals)
        ):
            self.setGeometry(QRect(*geom_vals))

        main_sizes = ui_state.get("main_splitter_sizes")
        if (
            hasattr(self, "main_splitter")
            and isinstance(main_sizes, list)
            and len(main_sizes) == 3
            and all(isinstance(v, int) for v in main_sizes)
        ):
            self.main_splitter.setSizes(main_sizes)

        editor_sizes = ui_state.get("editor_splitter_sizes")
        if (
            hasattr(self, "editor_splitter")
            and isinstance(editor_sizes, list)
            and len(editor_sizes) == 2
            and all(isinstance(v, int) for v in editor_sizes)
        ):
            self.editor_splitter.setSizes(editor_sizes)

        main_state_b64 = ui_state.get("main_splitter_state")
        if hasattr(self, "main_splitter") and isinstance(main_state_b64, str):
            self.main_splitter.restoreState(
                QByteArray.fromBase64(main_state_b64.encode("ascii"))
            )

        editor_state_b64 = ui_state.get("editor_splitter_state")
        if hasattr(self, "editor_splitter") and isinstance(editor_state_b64, str):
            self.editor_splitter.restoreState(
                QByteArray.fromBase64(editor_state_b64.encode("ascii"))
            )

        if bool(ui_state.get("is_maximized", False)):
            self.showMaximized()

    def _get_debug_add_button_enabled(self: "NovelCreatorWindow") -> bool:
        state = self._load_sys_state()
        return bool(state.get("debug_add_button_enabled", False))

    def _save_debug_add_button_enabled(self: "NovelCreatorWindow", enabled: bool):
        state = self._load_sys_state()
        state["debug_add_button_enabled"] = bool(enabled)
        self._write_sys_state(state)

    def _get_modify_style_option(self: "NovelCreatorWindow") -> bool:
        state = self._load_sys_state()
        return bool(state.get("append_writing_style_on_modify", False))

    def _save_modify_style_option(self: "NovelCreatorWindow", enabled: bool):
        state = self._load_sys_state()
        state["append_writing_style_on_modify"] = bool(enabled)
        self._write_sys_state(state)

    def open_settings_dialog(self: "NovelCreatorWindow"):
        dialog = SettingsDialog(self)  # type: ignore[arg-type]
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.log_console.append("系统配置已更新，正在重新初始化模型客户端...")
            self.config = self._load_config()
            self.llm_client = LLMClient(self.config) if self.config else None
            self._save_workspace_instruction_profile()
            self.log_console.append("模型客户端初始化完成！")
