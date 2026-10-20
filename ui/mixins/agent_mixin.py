"""AgentMixin —— 命令式 Agent 控制台的入口、模型客户端构造与结果写回。"""
from __future__ import annotations

import os
from typing import Any

from PyQt6.QtWidgets import QMessageBox

from core.task_queue import TaskStatus


class AgentMixin:
    def open_agent_console(self: Any):
        """打开 Agent 控制台（非模态，可边执行边观察主窗口）。"""
        from ui.agent_console_dialog import AgentConsoleDialog

        if not self.workspace:
            QMessageBox.information(self, "提示", "请先加载工作区。")
            return
        if not self.llm_client:
            QMessageBox.warning(
                self,
                "未配置模型",
                "请先在「设置 → 模型服务」中配置可用的文本模型。",
            )
            return
        existing = getattr(self, "_agent_dialog", None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._agent_dialog = None
        dialog = AgentConsoleDialog(self)
        self._agent_dialog = dialog
        dialog.show()

    def _create_llm_client_for_model(self: Any, provider_id: str, model_id: str):
        """为 Agent 单独指定模型时，构造一个不改变全局配置的客户端副本。"""
        import copy

        from core.llm_client import LLMClient

        config = copy.deepcopy(self.config or {})
        active = str(config.get("text_api_active_profile", "normal"))
        if active not in ("normal", "nsfw"):
            active = "normal"
        profiles = config.setdefault("text_api_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            config["text_api_profiles"] = profiles
        profile = profiles.setdefault(active, {})
        if not isinstance(profile, dict):
            profile = {}
            profiles[active] = profile
        profile["provider_id"] = provider_id
        profile["model_id"] = model_id
        workspace_path = (
            getattr(self.workspace, "workspace_path", None) if self.workspace else None
        )
        return LLMClient(
            config,
            task_queue=self._task_queue(),
            workspace_path=workspace_path,
        )

    def _find_outline_node(self: Any, node_id: str) -> dict | None:
        if not self.outline_tree_data:
            return None
        stack = list(self.outline_tree_data.get("nodes", []) or [])
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if str(node.get("id", "")) == str(node_id):
                return node
            stack.extend(node.get("children", []) or [])
        return None

    def apply_agent_node_update(self: Any, update: dict):
        """把 Agent 执行过程中产生的节点更新写回大纲树并落盘。"""
        if not isinstance(update, dict) or not self.workspace:
            return
        node = self._find_outline_node(str(update.get("node_id", "")))
        if node is None:
            self.log_console.append(
                f"<font color='orange'>[Agent] 找不到节点 {update.get('node_id')}，更新已跳过。</font>"
            )
            return
        kind = str(update.get("kind", ""))
        if kind == "content":
            file_path = str(update.get("file_path", "") or node.get("file_path", "") or "")
            node["file_path"] = file_path
            node["md5"] = str(update.get("md5", "") or node.get("md5", "") or "")
            node["_status"] = "ok"
            if file_path:
                self.file_tracker.track_file(
                    os.path.join(self.workspace.text_path, file_path)
                )
            if self.current_editing_node is node:
                content = str(update.get("content", ""))
                if content:
                    self.content_editor.setText(content)
                self.current_node_original_content = self.content_editor.toPlainText()
        elif kind == "summary":
            node["summary"] = str(update.get("summary", ""))
            if self.current_editing_node is node:
                self.summary_editor.setText(node["summary"])
                self.current_node_original_summary = node["summary"]
        elif kind == "pending":
            self.log_console.append(
                f"<font color='#8ab4f8'>[Agent] 节点【{node.get('title', '')}】已生成待合并修改，"
                "请右键执行【进行合并】。</font>"
            )
        self.workspace.save_outline_tree(
            self.outline_tree_data, getattr(self, "file_tracker", None)
        )
        self._update_task_status_label()

    def finish_agent_run(self: Any, action_id: str, result):
        """Agent 执行结束后的收尾：记录步骤结果、结束动作、刷新界面。"""
        try:
            queue = self._task_queue()
            queue.update_action(
                action_id,
                meta={
                    "success": bool(getattr(result, "success", False)),
                    "cancelled": bool(getattr(result, "cancelled", False)),
                    "error": str(getattr(result, "error", "") or ""),
                    "steps": [
                        step.to_dict() for step in getattr(result, "steps", [])
                    ],
                },
            )
        except Exception:
            pass

        status = None
        note = ""
        if getattr(result, "cancelled", False):
            status = TaskStatus.CANCELLED
            note = "用户停止"
        elif not getattr(result, "success", False):
            status = TaskStatus.FAILED
            note = str(getattr(result, "error", "") or "部分步骤失败")
        self._task_action_finish(status, note)

        updated_nodes = sum(
            1 for update in getattr(result, "updates", []) if update.get("kind") == "content"
        )
        updated_summaries = sum(
            1 for update in getattr(result, "updates", []) if update.get("kind") == "summary"
        )
        pending = sum(
            1 for update in getattr(result, "updates", []) if update.get("kind") == "pending"
        )
        self.log_console.append(
            "<b><font color='green'>🤖 Agent 执行结束：</font></b>"
            f"正文 {updated_nodes} 个，概要 {updated_summaries} 个，"
            f"待合并 {pending} 个。"
        )
        self.refresh_ui_from_workspace()
        self._auto_export_html_if_enabled("Agent 执行")
        self._update_task_status_label()
