"""TaskMixin —— 把用户操作包装成“动作（action）”，并维护任务队列状态。

约定：

* 单个操作（生成正文 / 重写 / 生成概要）在开始时 ``_task_action_begin``，
  成功或失败时 ``_task_action_finish``；
* 批量操作（批量生成 / 批量修改 / 批量概要）在批量开始时开启动作，
  循环内部复用同一个动作，批量结束时统一收尾；
* 每次真实 LLM 请求由 ``LLMClient`` 自动写入任务队列，界面层只需要提供
  ``task_meta``（标签、节点信息）。
"""
from __future__ import annotations

from typing import Any


class TaskMixin:
    def _task_action_begin(
        self: Any,
        action_type: str,
        title: str,
        *,
        description: str = "",
        node_ids: list[str] | None = None,
        meta: dict | None = None,
        reuse: bool = True,
    ) -> str:
        """开启一个动作；``reuse=True`` 时若已有活动动作则直接复用（批量子任务）。"""
        active = getattr(self, "_active_task_action_id", None)
        if reuse and active:
            return active
        queue = self._task_queue()
        action_id = queue.begin_action(
            action_type,
            title,
            description=description,
            node_ids=node_ids,
            meta=meta,
        )
        self._active_task_action_id = action_id
        self._update_task_status_label()
        return action_id

    def _task_action_finish(self, status: str | None = None, note: str = ""):
        """结束当前动作（任务结果自动推导状态）。"""
        action_id = getattr(self, "_active_task_action_id", None)
        if not action_id:
            return
        try:
            self._task_queue().finish_action(action_id, status, note)
        except Exception:
            pass
        finally:
            self._active_task_action_id = None
            self._update_task_status_label()

    def _task_action_note(self, note: str):
        action_id = getattr(self, "_active_task_action_id", None)
        if not action_id:
            return
        try:
            self._task_queue().update_action(action_id, description=note)
        except Exception:
            pass

    def _task_meta(
        self: Any,
        label: str,
        node: dict | None = None,
        **extra,
    ) -> dict:
        """构造传给 ``LLMClient.generate_text`` 的任务归属信息。"""
        meta: dict[str, Any] = {
            "label": label,
            "action_id": getattr(self, "_active_task_action_id", None),
        }
        if isinstance(node, dict):
            meta["node_id"] = node.get("id", "")
            meta["node_title"] = node.get("title", "")
        meta.update(extra)
        return meta

    def _task_status_summary(self: Any) -> str:
        try:
            stats = self._task_queue().stats()
        except Exception:
            return ""
        return (
            f"任务：运行 {stats.get('running', 0)} / 成功 {stats.get('success', 0)} "
            f"/ 失败 {stats.get('failed', 0)}"
        )

    def _update_task_status_label(self: Any):
        label = getattr(self, "task_status_label", None)
        if label is None:
            return
        try:
            label.setText(self._task_status_summary())
        except Exception:
            pass

    def open_task_queue_dialog(self: Any):
        """打开任务队列窗口。"""
        from ui.task_queue_dialog import TaskQueueDialog

        dialog = TaskQueueDialog(self, queue=self._task_queue())  # type: ignore[arg-type]
        dialog.exec()
        self._update_task_status_label()

    def _reset_task_action(self: Any):
        """切换工作区等场景下丢弃当前动作引用（不结束已落盘动作）。"""
        self._active_task_action_id = None
        self._update_task_status_label()
