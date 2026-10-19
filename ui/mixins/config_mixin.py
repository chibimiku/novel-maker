"""
ConfigMixin —— 配置文件读写与系统状态管理。
"""
from __future__ import annotations

import json
import os
import copy
from typing import Any

from PyQt6.QtWidgets import QDialog, QInputDialog
from PyQt6.QtCore import QByteArray, QRect

from core.config_store import build_runtime_config, load_raw_config, save_raw_config
from core.llm_client import LLMClient
from core.model_config import resolve_runtime, validate_config
from ui.settings_dialog import SettingsDialog

class ConfigMixin:
    """管理 setting.json / sys_state.json / prompt 模板等配置读写。"""

    # ---- 以下方法中 self 实际为 NovelCreatorWindow 实例 ----

    def _get_default_writing_instruction(self: Any) -> str:
        return (
            "你是一个专业的AI小说家。你的输出必须纯粹是小说情节文本，"
            "严禁包含任何前言、后语、剧情解释或'已为您生成'之类的助手客套话。"
        )

    def _get_default_summary_instruction(self: Any) -> str:
        return "你是一个专业的小说编辑，擅长为小说节点生成精炼、准确的概要。"

    def _get_default_modify_instruction(self: Any) -> str:
        return (
            "你是一个专业的小说改写编辑。你必须严格遵循用户的修改要求，"
            "对原文进行高质量改写。只输出修改后的完整正文，不要附加解释。"
        )

    def _get_default_text_api_profile(self: Any) -> dict:
        return {
            "type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o",
            "timeout": 120,
            "instructions": self._get_default_writing_instruction(),
            "instructions_history": [
                {
                    "name": "默认写作Instruction",
                    "content": self._get_default_writing_instruction(),
                }
            ],
            "summary_instructions": self._get_default_summary_instruction(),
            "summary_instructions_history": [
                {
                    "name": "默认概要Instruction",
                    "content": self._get_default_summary_instruction(),
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
            "children_summary_compress_trigger_ratio": 0.27,
            "children_summary_group_budget_ratio": 0.24,
            "auto_continue_on_incomplete": True,
            "auto_continue_max_rounds": 3,
            "model_meta_catalog": {},
        }

    def _normalize_text_api_profile(
        self: Any, raw_profile: dict | None
    ) -> dict:
        default_profile = self._get_default_text_api_profile()
        text_cfg = copy.deepcopy(default_profile)
        if isinstance(raw_profile, dict):
            text_cfg.update(raw_profile)

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

        default_summary = self._get_default_summary_instruction()
        if not text_cfg.get("summary_instructions"):
            text_cfg["summary_instructions"] = default_summary
        raw_summary_history = text_cfg.get("summary_instructions_history", [])
        normalized_summary_history = []
        for item in raw_summary_history:
            if isinstance(item, str):
                normalized_summary_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                normalized_summary_history.append(
                    {
                        "name": str(item.get("name", "")),
                        "content": str(item.get("content", "")),
                    }
                )
        if not any(
            (it.get("name") or "").strip() == "默认概要Instruction"
            and (it.get("content") or "").strip()
            == (text_cfg["summary_instructions"] or "").strip()
            for it in normalized_summary_history
        ):
            normalized_summary_history.insert(
                0,
                {
                    "name": "默认概要Instruction",
                    "content": text_cfg["summary_instructions"],
                },
            )
        text_cfg["summary_instructions_history"] = normalized_summary_history

        default_modify = self._get_default_modify_instruction()
        if not text_cfg.get("modify_instructions"):
            text_cfg["modify_instructions"] = default_modify

        raw_modify_history = text_cfg.get("modify_instructions_history", [])
        normalized_modify_history = []
        for item in raw_modify_history:
            if isinstance(item, str):
                normalized_modify_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                normalized_modify_history.append(
                    {
                        "name": str(item.get("name", "")),
                        "content": str(item.get("content", "")),
                    }
                )
        if not any(
            (it.get("name") or "").strip() == "默认编辑Instruction"
            and (it.get("content") or "").strip()
            == (text_cfg["modify_instructions"] or "").strip()
            for it in normalized_modify_history
        ):
            normalized_modify_history.insert(
                0,
                {
                    "name": "默认编辑Instruction",
                    "content": text_cfg["modify_instructions"],
                },
            )
        text_cfg["modify_instructions_history"] = normalized_modify_history

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
        compression_profile = str(
            text_cfg.get("world_context_compression_profile", "balanced")
        ).strip().lower()
        if compression_profile not in {"conservative", "balanced", "aggressive"}:
            compression_profile = "balanced"
        text_cfg["world_context_compression_profile"] = compression_profile

        def _normalize_ratio(raw_value, default_value, min_value, max_value):
            try:
                value = float(raw_value)
            except Exception:
                value = default_value
            if value > 1:
                value = value / 100.0
            if value <= 0:
                value = default_value
            return max(min_value, min(max_value, value))

        text_cfg["children_summary_compress_trigger_ratio"] = _normalize_ratio(
            text_cfg.get("children_summary_compress_trigger_ratio", 0.27),
            default_value=0.27,
            min_value=0.05,
            max_value=0.8,
        )
        text_cfg["children_summary_group_budget_ratio"] = _normalize_ratio(
            text_cfg.get("children_summary_group_budget_ratio", 0.24),
            default_value=0.24,
            min_value=0.04,
            max_value=0.6,
        )

        text_cfg["auto_continue_on_incomplete"] = bool(
            text_cfg.get("auto_continue_on_incomplete", True)
        )
        try:
            rounds = int(text_cfg.get("auto_continue_max_rounds", 3))
        except Exception:
            rounds = 3
        text_cfg["auto_continue_max_rounds"] = max(0, min(10, rounds))
        return text_cfg

    def _build_text_api_profiles(self: Any, cfg: dict) -> dict:
        raw_profiles = cfg.get("text_api_profiles", {})
        if isinstance(raw_profiles, dict):
            raw_normal = raw_profiles.get("normal")
            raw_nsfw = raw_profiles.get("nsfw")
        else:
            raw_normal = None
            raw_nsfw = None

        # 兼容旧配置：只有 text_api 时，默认复制为 normal / nsfw 两套。
        legacy_text_api = cfg.get("text_api", {}) if isinstance(cfg.get("text_api"), dict) else {}
        if raw_normal is None:
            raw_normal = legacy_text_api
        if raw_nsfw is None:
            raw_nsfw = copy.deepcopy(raw_normal if isinstance(raw_normal, dict) else legacy_text_api)

        return {
            "normal": self._normalize_text_api_profile(
                raw_normal if isinstance(raw_normal, dict) else {}
            ),
            "nsfw": self._normalize_text_api_profile(
                raw_nsfw if isinstance(raw_nsfw, dict) else {}
            ),
        }

    def _get_instruction_history(
        self: Any, history_key: str
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
        self: Any, history_key: str, template_name: str
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
        self: Any,
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
        self: Any,
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

    def _get_current_instruction_profile(self: Any) -> dict:
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

    def _save_workspace_instruction_profile(self: Any):
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

    def _apply_workspace_instruction_profile(self: Any):
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

        self.llm_client = self._create_llm_client()

        if warnings:
            from PyQt6.QtWidgets import QMessageBox

            msg = "\n".join(warnings)
            self.log_console.append(f"<font color='orange'>{msg}</font>")
            QMessageBox.warning(self, "Instruction 绑定告警", msg)  # type: ignore[arg-type]

    def _load_config(self: Any) -> dict:
        """读取 + 归一化配置（自动迁移旧版 text_api 到 providers 二级结构）。"""
        try:
            raw = load_raw_config()
        except Exception as e:
            print(f"读取配置文件失败: {e}")
            raw = {}

        try:
            prefer_nsfw = bool(getattr(self, "_workspace_is_nsfw", False))
            cfg = build_runtime_config(raw, prefer_nsfw=prefer_nsfw)
        except Exception as e:
            print(f"配置迁移失败，使用默认配置: {e}")
            cfg = build_runtime_config({}, prefer_nsfw=getattr(self, "_workspace_is_nsfw", False))

        # 归一化各画像的 instruction / 压缩策略等行为配置
        profiles = self._build_text_api_profiles(cfg)
        active_key = str(cfg.get("text_api_active_profile", "normal"))
        if active_key not in profiles:
            active_key = "normal"
        cfg["text_api_profiles"] = profiles
        cfg["text_api_active_profile"] = active_key
        cfg["text_generation_mode"] = active_key

        # 重新生成向后兼容的 text_api 视图（保留运行期模型字段）
        runtime_fields = {
            key: cfg.get("text_api", {}).get(key)
            for key in (
                "type",
                "base_url",
                "api_key",
                "model",
                "model_context_size",
                "model_max_output_tokens",
                "model_capabilities",
                "model_params",
                "provider_id",
                "model_id",
                "provider_name",
            )
        }
        derived = copy.deepcopy(profiles.get(active_key, {}))
        for key, value in runtime_fields.items():
            if value is not None:
                derived[key] = value
        derived["text_generation_mode"] = active_key
        cfg["text_api"] = derived
        cfg["task_completion_notification_enabled"] = bool(
            cfg.get("task_completion_notification_enabled", True)
        )

        issues = validate_config(cfg)
        if issues:
            print("模型配置告警:\n- " + "\n- ".join(issues))
        return cfg

    def _runtime_model(self: Any):
        """返回当前生效的运行期模型（可能为 None）。"""
        if not self.config:
            return None
        active = str(self.config.get("text_api_active_profile", "normal")).lower()
        if active not in ("normal", "nsfw"):
            active = "normal"
        return resolve_runtime(self.config, active)

    def _task_queue(self: Any):
        """返回全局任务队列（已绑定当前工作区目录）。"""
        from core.task_queue import get_task_queue

        return get_task_queue()

    def _configure_task_queue(self: Any):
        """把任务队列的存储根目录切到当前工作区。"""
        from core.task_queue import configure_task_queue, workspace_task_root

        workspace = getattr(self, "workspace", None)
        workspace_path = getattr(workspace, "workspace_path", None) if workspace else None
        return configure_task_queue(workspace_task_root(workspace_path))

    def _create_llm_client(self: Any):
        """按当前配置与工作区创建 LLM 客户端（每次重建都会读取最新模型配置）。"""
        if not self.config:
            return None
        workspace = getattr(self, "workspace", None)
        workspace_path = getattr(workspace, "workspace_path", None) if workspace else None
        return LLMClient(
            self.config,
            task_queue=self._task_queue(),
            workspace_path=workspace_path,
        )

    def _persist_config(self: Any, updates: dict):
        """合并写入配置文件，保留未知字段（providers / recent_api_configs 等）。"""
        raw = load_raw_config()
        merged = copy.deepcopy(raw)
        merged.update(copy.deepcopy(updates))
        return save_raw_config(merged)

    def _get_or_create_prompt_template(
        self: Any,
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

    def _get_sys_state_path(self: Any) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base_dir, "conf", "sys_state.json")

    def _load_sys_state(self: Any) -> dict:
        path = self._get_sys_state_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"recent_workspaces": []}

    def _write_sys_state(self: Any, state: dict):
        path = self._get_sys_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存系统状态失败: {e}")

    def _save_sys_state(self: Any, workspace_path: str):
        state = self._load_sys_state()
        recents = state.get("recent_workspaces", [])
        if workspace_path in recents:
            recents.remove(workspace_path)
        recents.insert(0, workspace_path)
        state["recent_workspaces"] = recents[:10]
        self._write_sys_state(state)

    def _save_window_ui_state(self: Any):
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

    def _restore_window_ui_state(self: Any):
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

    def _get_debug_log_enabled(self: Any) -> bool:
        state = self._load_sys_state()
        # 兼容旧字段 debug_add_button_enabled
        return bool(
            state.get(
                "debug_log_enabled",
                state.get("debug_add_button_enabled", False),
            )
        )

    def _save_debug_log_enabled(self: Any, enabled: bool):
        state = self._load_sys_state()
        state["debug_log_enabled"] = bool(enabled)
        # 兼容旧字段，避免历史版本读不到
        state["debug_add_button_enabled"] = bool(enabled)
        self._write_sys_state(state)

    # 兼容旧调用名
    def _get_debug_add_button_enabled(self: Any) -> bool:
        return self._get_debug_log_enabled()

    # 兼容旧调用名
    def _save_debug_add_button_enabled(self: Any, enabled: bool):
        self._save_debug_log_enabled(enabled)

    def _get_modify_style_option(self: Any) -> bool:
        state = self._load_sys_state()
        return bool(state.get("append_writing_style_on_modify", False))

    def _save_modify_style_option(self: Any, enabled: bool):
        state = self._load_sys_state()
        state["append_writing_style_on_modify"] = bool(enabled)
        self._write_sys_state(state)

    def _get_batch_modify_thread_count(self: Any) -> int:
        state = self._load_sys_state()
        value = state.get("batch_modify_thread_count", 3)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 3
        return max(1, min(20, value))

    def _save_batch_modify_thread_count(self: Any, count: int):
        state = self._load_sys_state()
        try:
            value = int(count)
        except (TypeError, ValueError):
            value = 3
        state["batch_modify_thread_count"] = max(1, min(20, value))
        self._write_sys_state(state)

    def _get_auto_export_www_enabled(self: Any) -> bool:
        state = self._load_sys_state()
        return bool(state.get("auto_export_www_enabled", False))

    def _save_auto_export_www_enabled(self: Any, enabled: bool):
        state = self._load_sys_state()
        state["auto_export_www_enabled"] = bool(enabled)
        self._write_sys_state(state)

    def _get_smart_setting_selection_enabled(self: Any) -> bool:
        state = self._load_sys_state()
        return bool(state.get("smart_setting_selection_enabled", False))

    def _save_smart_setting_selection_enabled(self: Any, enabled: bool):
        state = self._load_sys_state()
        state["smart_setting_selection_enabled"] = bool(enabled)
        self._write_sys_state(state)

    def open_settings_dialog(self: Any):
        dialog = SettingsDialog(self)  # type: ignore[arg-type]
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.log_console.append("系统配置已更新，正在重新初始化模型客户端...")
            self.config = self._load_config()
            self.llm_client = self._create_llm_client()
            self._save_workspace_instruction_profile()
            runtime = self._runtime_model()
            if runtime is not None:
                self.log_console.append(
                    f"模型客户端初始化完成：{runtime.label()} "
                    f"(context={runtime.context_size}, max_out={runtime.max_output_tokens}, "
                    f"params={runtime.params_desc()})"
                )
            else:
                self.log_console.append(
                    "<font color='orange'>尚未配置可用的文本模型，请在「设置 → 模型服务」中添加。</font>"
                )

    def open_model_manager_dialog(self: Any):
        """打开“提供商 → 模型”管理窗口。"""
        from ui.model_manager_dialog import ModelManagerDialog

        dialog = ModelManagerDialog(self)  # type: ignore[arg-type]
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config = self._load_config()
            self.llm_client = self._create_llm_client()
            runtime = self._runtime_model()
            if runtime is not None:
                self.log_console.append(
                    f"<b><font color='green'>模型配置已更新：{runtime.label()} "
                    f"(context={runtime.context_size}, max_out={runtime.max_output_tokens})</font></b>"
                )
            self.statusBar().showMessage("模型配置已更新", 3000)

    def switch_active_model(self: Any, provider_id: str, model_id: str) -> bool:
        """快速切换当前画像使用的模型（写入配置并重建客户端）。"""
        raw = load_raw_config()
        if not isinstance(raw, dict):
            return False
        active = "nsfw" if bool(getattr(self, "_workspace_is_nsfw", False)) else str(
            raw.get("text_api_active_profile", "normal")
        )
        if active not in ("normal", "nsfw"):
            active = "normal"
        profiles = raw.setdefault("text_api_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            raw["text_api_profiles"] = profiles
        profile = profiles.setdefault(active, {})
        if not isinstance(profile, dict):
            profile = {}
            profiles[active] = profile
        profile["provider_id"] = provider_id
        profile["model_id"] = model_id
        if not save_raw_config(raw):
            self.log_console.append(
                "<font color='red'>切换模型失败：无法写入 conf/setting.json。</font>"
            )
            return False
        self.config = self._load_config()
        self.llm_client = self._create_llm_client()
        runtime = self._runtime_model()
        if runtime is not None:
            self.log_console.append(
                f"<b>已切换模型：{runtime.label()} "
                f"(context={runtime.context_size}, max_out={runtime.max_output_tokens}, "
                f"params={runtime.params_desc()})</b>"
            )
        else:
            self.log_console.append(
                "<font color='orange'>切换后没有找到可用模型，请检查模型服务配置。</font>"
            )
        return True
