"""任务队列：把“用户动作（action）”拆成“原子任务（task）”并可追溯。

概念模型
--------

* **动作（Action）** —— 用户发起的一次业务操作，例如「重写第一章下的 7 个场景」
  或「批量生成缺失场景（12 个节点）」；
* **原子任务（Task）** —— 一次真实的大模型后端请求。动作与任务是一对多：
  7 个小节的改写 = 1 个动作 + 7 个任务；开启智能设定筛选时，每个任务前还会
  多出一次“筛选设定”的请求，同样作为独立任务记录在同一个动作下。

每个任务都会落盘完整的输入（system prompt / 用户提示词 / 上下文消息）、输出、
请求参数、token 预算与耗时，因此可以在界面里逐条回看、复现与排查。

存储布局::

    <workspace>/系统数据/任务队列/
        index.json                  # 动作摘要索引（列表页快速加载）
        actions/<action_id>.json    # 动作详情 + 全部任务（含输入输出）

没有打开工作区时回落到 ``<project>/data/task_queue/``，保证任何请求都不会丢。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
TASK_DIR_NAME = "任务队列"

#: 单字段落盘上限，防止异常输出把 json 撑爆
MAX_TEXT_CHARS = 4_000_000


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    ALL = (PENDING, RUNNING, SUCCESS, FAILED, CANCELLED, INTERRUPTED)
    FINISHED = (SUCCESS, FAILED, CANCELLED, INTERRUPTED)


STATUS_LABELS = {
    TaskStatus.PENDING: "排队中",
    TaskStatus.RUNNING: "进行中",
    TaskStatus.SUCCESS: "成功",
    TaskStatus.FAILED: "失败",
    TaskStatus.CANCELLED: "已取消",
    TaskStatus.INTERRUPTED: "已中断",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clip(text: Any) -> Any:
    if isinstance(text, str) and len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS] + "\n...(内容超长已截断)"
    return text


def _clip_messages(messages: Any) -> Any:
    if not isinstance(messages, list):
        return []
    return [
        {
            "role": str(item.get("role", "")) if isinstance(item, dict) else "",
            "content": _clip(item.get("content", "")) if isinstance(item, dict) else str(item),
        }
        for item in messages
    ]


def _ms_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        return None
    return int((end_dt - start_dt).total_seconds() * 1000)


def _atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp_path, path)


class TaskQueue:
    """线程安全的动作/任务记录器。"""

    def __init__(self, root: str | None = None) -> None:
        self._lock = threading.RLock()
        self._root = os.path.abspath(root) if root else _default_root()
        self._actions: dict[str, dict] = {}
        self._index: dict[str, dict] = {}
        self._active_action_id: str | None = None
        self._loaded = False

    # -- 存储 ---------------------------------------------------------
    @property
    def root(self) -> str:
        return self._root

    def set_root(self, root: str | None) -> None:
        """切换存储根目录（打开/切换工作区时调用）。"""
        target = os.path.abspath(root) if root else _default_root()
        with self._lock:
            if target == self._root:
                return
            self._root = target
            self._actions.clear()
            self._index.clear()
            self._loaded = False
            self._active_action_id = None
            self._load_index()

    def actions_dir(self) -> str:
        return os.path.join(self._root, "actions")

    def index_path(self) -> str:
        return os.path.join(self._root, "index.json")

    def _action_path(self, action_id: str) -> str:
        return os.path.join(self.actions_dir(), f"{action_id}.json")

    # -- 索引 ---------------------------------------------------------
    def _load_index(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            path = self.index_path()
            entries: dict[str, dict] = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        raw = json.load(handle)
                    for item in raw.get("actions", []) if isinstance(raw, dict) else []:
                        if isinstance(item, dict) and item.get("action_id"):
                            entries[str(item["action_id"])] = item
                except Exception as exc:  # pragma: no cover - 损坏索引
                    logger.warning("任务队列索引损坏，将重新扫描: %s", exc)
            if not entries:
                entries = self._scan_actions()
            self._index = entries
            self._recover_interrupted()

    def _scan_actions(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        directory = self.actions_dir()
        if not os.path.isdir(directory):
            return result
        for name in os.listdir(directory):
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                continue
            if not isinstance(data, dict) or not data.get("action_id"):
                continue
            result[str(data["action_id"])] = self._summarize(data)
        return result

    def _recover_interrupted(self) -> None:
        """上次退出时仍在 running 的动作/任务标记为 interrupted。"""
        dirty = False
        for action_id, summary in list(self._index.items()):
            if summary.get("status") != TaskStatus.RUNNING:
                continue
            action = self._load_action_file(action_id)
            if not action:
                continue
            for task in action.get("tasks", []):
                if task.get("status") == TaskStatus.RUNNING:
                    task["status"] = TaskStatus.INTERRUPTED
                    task["error"] = task.get("error") or "应用退出导致任务中断"
                    task["finished_at"] = task.get("finished_at") or now_iso()
                    if task.get("started_at"):
                        task["duration_ms"] = _ms_between(
                            task.get("started_at"), task["finished_at"]
                        )
            action["status"] = TaskStatus.INTERRUPTED
            action["finished_at"] = action.get("finished_at") or now_iso()
            self._actions[action_id] = action
            self._index[action_id] = self._summarize(action)
            self._write_action(action)
            dirty = True
        if dirty:
            self._write_index()

    def _load_action_file(self, action_id: str) -> dict | None:
        path = self._action_path(action_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            logger.warning("读取动作文件失败 %s: %s", path, exc)
            return None
        return data if isinstance(data, dict) else None

    def _write_action(self, action: dict) -> None:
        try:
            _atomic_write_json(self._action_path(action["action_id"]), action)
        except Exception as exc:  # pragma: no cover - 磁盘异常
            logger.warning("写入动作文件失败: %s", exc)

    def _write_index(self) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": now_iso(),
            "actions": sorted(
                self._index.values(),
                key=lambda item: str(item.get("created_at", "")),
                reverse=True,
            ),
        }
        try:
            _atomic_write_json(self.index_path(), payload)
        except Exception as exc:  # pragma: no cover - 磁盘异常
            logger.warning("写入任务队列索引失败: %s", exc)

    @staticmethod
    def _summarize(action: dict) -> dict:
        tasks = action.get("tasks", []) or []
        counts = {status: 0 for status in TaskStatus.ALL}
        for task in tasks:
            status = task.get("status", TaskStatus.PENDING)
            counts[status] = counts.get(status, 0) + 1
        return {
            "action_id": action.get("action_id", ""),
            "action_type": action.get("action_type", ""),
            "title": action.get("title", ""),
            "description": action.get("description", ""),
            "status": action.get("status", TaskStatus.PENDING),
            "created_at": action.get("created_at", ""),
            "updated_at": action.get("updated_at", ""),
            "finished_at": action.get("finished_at", ""),
            "task_count": len(tasks),
            "counts": counts,
            "models": action.get("models", []),
            "node_ids": action.get("node_ids", []),
            "implicit": bool(action.get("implicit", False)),
        }

    def _auto_close_implicit_actions(self, grace_seconds: float = 30.0) -> None:
        """自动收尾“隐式动作”。

        未显式开启动作的零散请求会挂在临时动作下。若该动作下已没有进行中的
        任务，且闲置超过 ``grace_seconds``，就把它标记为成功，避免界面里永远
        显示“进行中”。
        """
        now = datetime.now().astimezone()
        changed = False
        for action_id, summary in list(self._index.items()):
            if not summary.get("implicit"):
                continue
            if summary.get("status") != TaskStatus.RUNNING:
                continue
            counts = summary.get("counts") or {}
            if counts.get(TaskStatus.RUNNING) or counts.get(TaskStatus.PENDING):
                continue
            reference = str(summary.get("updated_at") or summary.get("created_at") or "")
            try:
                stamp = datetime.fromisoformat(reference)
            except ValueError:
                stamp = None
            if stamp is not None and (now - stamp).total_seconds() < grace_seconds:
                continue
            action = self._get_action(action_id)
            if action is None:
                continue
            action["status"] = TaskStatus.SUCCESS
            action["finished_at"] = now_iso()
            self._persist_action(action)
            if self._active_action_id == action_id:
                self._active_action_id = None
            changed = True
        if changed:
            self._write_index()

    def _get_action(self, action_id: str) -> dict | None:
        if not action_id:
            return None
        self._load_index()
        action = self._actions.get(action_id)
        if action is not None:
            return action
        action = self._load_action_file(action_id)
        if action is not None:
            self._actions[action_id] = action
        return action

    def _persist_action(self, action: dict) -> None:
        action["updated_at"] = now_iso()
        self._actions[action["action_id"]] = action
        self._index[action["action_id"]] = self._summarize(action)
        self._write_action(action)
        self._write_index()

    # -- 动作 ---------------------------------------------------------
    @property
    def active_action_id(self) -> str | None:
        with self._lock:
            return self._active_action_id

    def set_active_action(self, action_id: str | None) -> None:
        with self._lock:
            self._active_action_id = action_id

    def begin_action(
        self,
        action_type: str,
        title: str,
        *,
        description: str = "",
        node_ids: list[str] | None = None,
        meta: dict | None = None,
        implicit: bool = False,
    ) -> str:
        """开始一个新动作，并将其设为当前活动动作。"""
        with self._lock:
            self._load_index()
            action_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            action = {
                "schema_version": SCHEMA_VERSION,
                "action_id": action_id,
                "action_type": str(action_type or "unknown"),
                "title": str(title or "未命名动作"),
                "description": str(description or ""),
                "status": TaskStatus.RUNNING,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "finished_at": "",
                "node_ids": [str(item) for item in (node_ids or [])],
                "models": [],
                "implicit": bool(implicit),
                "meta": copy.deepcopy(meta) if isinstance(meta, dict) else {},
                "tasks": [],
            }
            self._active_action_id = action_id
            self._persist_action(action)
            return action_id

    def update_action(self, action_id: str | None = None, **fields) -> None:
        """更新动作字段（title/description/status/meta/node_ids...）。"""
        with self._lock:
            action = self._get_action(action_id or self._active_action_id or "")
            if action is None:
                return
            for key, value in fields.items():
                if key in ("tasks", "action_id", "schema_version"):
                    continue
                action[key] = value
            self._persist_action(action)

    def finish_action(
        self,
        action_id: str | None = None,
        status: str | None = None,
        note: str = "",
    ) -> str:
        """结束动作；status 未指定时按任务结果自动推导。"""
        with self._lock:
            target_id = action_id or self._active_action_id
            action = self._get_action(target_id or "")
            if action is None:
                self._active_action_id = None
                return ""
            if status is None:
                statuses = [task.get("status") for task in action.get("tasks", [])]
                if not statuses:
                    status = TaskStatus.SUCCESS
                elif any(item == TaskStatus.RUNNING for item in statuses):
                    status = TaskStatus.RUNNING
                elif all(item == TaskStatus.SUCCESS for item in statuses):
                    status = TaskStatus.SUCCESS
                elif any(item == TaskStatus.SUCCESS for item in statuses):
                    status = TaskStatus.FAILED
                else:
                    status = statuses[-1] if statuses else TaskStatus.FAILED
            action["status"] = status
            action["finished_at"] = now_iso() if status != TaskStatus.RUNNING else ""
            if note:
                action["note"] = note
            self._persist_action(action)
            if self._active_action_id == action["action_id"]:
                self._active_action_id = None
            return action["action_id"]

    # -- 任务 ---------------------------------------------------------
    def create_task(
        self,
        label: str,
        *,
        action_id: str | None = None,
        node_id: str | None = None,
        node_title: str | None = None,
        runtime: Any = None,
        provider_id: str | None = None,
        provider_name: str | None = None,
        model_id: str | None = None,
        model: str | None = None,
        api_type: str | None = None,
        params: dict | None = None,
        context_size: int | None = None,
        max_output_tokens: int | None = None,
        input_budget_tokens: int | None = None,
        estimated_input_tokens: int | None = None,
        system_prompt: str = "",
        user_prompt: str = "",
        context_messages: list | None = None,
        extra: dict | None = None,
    ) -> str:
        """创建一个排队中的原子任务，返回 task_id。"""
        with self._lock:
            self._load_index()
            target_action_id = action_id or self._active_action_id
            action = self._get_action(target_action_id or "")
            if action is None:
                target_action_id = self.begin_action(
                    "adhoc",
                    "临时模型请求",
                    description="未归类到具体动作的请求",
                    implicit=True,
                )
                action = self._get_action(target_action_id)
            assert action is not None

            task_id = f"tk_{uuid.uuid4().hex[:12]}"
            seq = len(action.get("tasks", [])) + 1
            model_info = {
                "provider_id": provider_id or getattr(runtime, "provider_id", ""),
                "provider_name": provider_name or getattr(runtime, "provider_name", ""),
                "model_id": model_id or getattr(runtime, "model_id", ""),
                "model": model or getattr(runtime, "model_key", ""),
                "display_name": getattr(runtime, "display_name", "") or model or "",
                "api_type": api_type or getattr(runtime, "api_type", ""),
            }
            runtime_params = (
                dict(params)
                if isinstance(params, dict)
                else dict(getattr(runtime, "params", {}) or {})
            )
            task = {
                "task_id": task_id,
                "action_id": target_action_id,
                "seq": seq,
                "label": str(label or "模型请求"),
                "node_id": str(node_id) if node_id else "",
                "node_title": str(node_title) if node_title else "",
                **model_info,
                "params": runtime_params,
                "context_size": int(context_size or getattr(runtime, "context_size", 0) or 0),
                "max_output_tokens": int(
                    max_output_tokens or getattr(runtime, "max_output_tokens", 0) or 0
                ),
                "input_budget_tokens": int(input_budget_tokens or 0),
                "estimated_input_tokens": int(estimated_input_tokens or 0),
                "estimated_output_tokens": 0,
                "usage": {},
                "status": TaskStatus.PENDING,
                "created_at": now_iso(),
                "started_at": "",
                "finished_at": "",
                "duration_ms": None,
                "system_prompt": _clip(system_prompt or ""),
                "user_prompt": _clip(user_prompt or ""),
                "context_messages": _clip_messages(context_messages),
                "output_text": "",
                "error": "",
                "attempts": [],
                "extra": copy.deepcopy(extra) if isinstance(extra, dict) else {},
            }
            action.setdefault("tasks", []).append(task)
            models = action.setdefault("models", [])
            label_text = model_info["model"] or ""
            if label_text and label_text not in models:
                models.append(label_text)
            if model_info["provider_name"]:
                provider_label = f"{model_info['provider_name']}/{label_text}"
                if provider_label not in models:
                    models.append(provider_label)
            if node_id:
                node_ids = action.setdefault("node_ids", [])
                if str(node_id) not in node_ids:
                    node_ids.append(str(node_id))
            self._persist_action(action)
            return task_id

    def _find_task(self, action_id: str, task_id: str) -> tuple[dict | None, dict | None]:
        action = self._get_action(action_id)
        if action is None:
            return None, None
        for task in action.get("tasks", []):
            if task.get("task_id") == task_id:
                return action, task
        return action, None

    def start_task(self, task_id: str, action_id: str | None = None) -> None:
        with self._lock:
            action, task = self._resolve_task(task_id, action_id)
            if task is None or action is None:
                return
            task["status"] = TaskStatus.RUNNING
            task["started_at"] = now_iso()
            action["status"] = TaskStatus.RUNNING
            self._persist_action(action)

    def complete_task(
        self,
        task_id: str,
        output_text: str,
        *,
        action_id: str | None = None,
        usage: dict | None = None,
        estimated_output_tokens: int | None = None,
        extra: dict | None = None,
    ) -> None:
        with self._lock:
            action, task = self._resolve_task(task_id, action_id)
            if task is None or action is None:
                return
            task["status"] = TaskStatus.SUCCESS
            task["output_text"] = _clip(output_text or "")
            task["finished_at"] = now_iso()
            task["duration_ms"] = _ms_between(task.get("started_at"), task["finished_at"])
            if isinstance(usage, dict):
                task["usage"] = {
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                }
            if estimated_output_tokens is not None:
                task["estimated_output_tokens"] = int(estimated_output_tokens)
            if isinstance(extra, dict):
                task.setdefault("extra", {}).update(extra)
            self._persist_action(action)

    def fail_task(
        self,
        task_id: str,
        error: str,
        *,
        action_id: str | None = None,
        extra: dict | None = None,
    ) -> None:
        with self._lock:
            action, task = self._resolve_task(task_id, action_id)
            if task is None or action is None:
                return
            task["status"] = TaskStatus.FAILED
            task["error"] = _clip(str(error or "未知错误"))
            task["finished_at"] = now_iso()
            task["duration_ms"] = _ms_between(task.get("started_at"), task["finished_at"])
            if isinstance(extra, dict):
                task.setdefault("extra", {}).update(extra)
            self._persist_action(action)

    def cancel_task(self, task_id: str, reason: str = "", action_id: str | None = None) -> None:
        with self._lock:
            action, task = self._resolve_task(task_id, action_id)
            if task is None or action is None:
                return
            task["status"] = TaskStatus.CANCELLED
            task["error"] = _clip(reason or "已取消")
            task["finished_at"] = now_iso()
            task["duration_ms"] = _ms_between(task.get("started_at"), task["finished_at"])
            self._persist_action(action)

    def add_attempt(self, task_id: str, reason: str, action_id: str | None = None) -> None:
        """记录一次自动续请求（截断重试）。"""
        with self._lock:
            action, task = self._resolve_task(task_id, action_id)
            if task is None or action is None:
                return
            attempts = task.setdefault("attempts", [])
            attempts.append(
                {
                    "round": len(attempts) + 1,
                    "reason": str(reason or ""),
                    "at": now_iso(),
                }
            )
            self._persist_action(action)

    def _resolve_task(
        self, task_id: str, action_id: str | None
    ) -> tuple[dict | None, dict | None]:
        if action_id:
            return self._find_task(action_id, task_id)
        active = self._active_action_id
        if active:
            action, task = self._find_task(active, task_id)
            if task is not None:
                return action, task
        # 活动动作被清理后仍能找到任务：扫描内存中的动作
        for candidate in list(self._actions.values()):
            for task in candidate.get("tasks", []):
                if task.get("task_id") == task_id:
                    return candidate, task
        # 最后回落到索引扫描（重启后直接操作旧动作的场景）
        for candidate_id in list(self._index.keys()):
            action, task = self._find_task(candidate_id, task_id)
            if task is not None:
                return action, task
        return None, None

    # -- 查询 ---------------------------------------------------------
    def list_actions(self, limit: int = 300) -> list[dict]:
        """返回动作摘要列表（按创建时间倒序）。"""
        with self._lock:
            self._load_index()
            self._auto_close_implicit_actions()
            actions = sorted(
                self._index.values(),
                key=lambda item: str(item.get("created_at", "")),
                reverse=True,
            )
            return copy.deepcopy(actions[:limit] if limit else actions)

    def load_action(self, action_id: str) -> dict | None:
        with self._lock:
            action = self._get_action(action_id)
            return copy.deepcopy(action) if action else None

    def get_task(self, action_id: str, task_id: str) -> dict | None:
        with self._lock:
            action = self._get_action(action_id)
            if not action:
                return None
            for task in action.get("tasks", []):
                if task.get("task_id") == task_id:
                    return copy.deepcopy(task)
            return None

    def stats(self) -> dict:
        """返回用于状态栏展示的统计信息。"""
        with self._lock:
            self._load_index()
            self._auto_close_implicit_actions()
            task_counts = {status: 0 for status in TaskStatus.ALL}
            action_count = 0
            today = datetime.now().strftime("%Y-%m-%d")
            today_tasks = 0
            for summary in self._index.values():
                action_count += 1
                for status, value in (summary.get("counts") or {}).items():
                    if status in task_counts:
                        task_counts[status] += int(value or 0)
                if str(summary.get("created_at", "")).startswith(today):
                    today_tasks += int(summary.get("task_count") or 0)
            return {
                "actions": action_count,
                "tasks": sum(task_counts.values()),
                "running": task_counts.get(TaskStatus.RUNNING, 0),
                "failed": task_counts.get(TaskStatus.FAILED, 0),
                "success": task_counts.get(TaskStatus.SUCCESS, 0),
                "today_tasks": today_tasks,
                "root": self._root,
            }

    # -- 清理 ---------------------------------------------------------
    def delete_action(self, action_id: str) -> bool:
        with self._lock:
            path = self._action_path(action_id)
            existed = os.path.exists(path)
            if existed:
                try:
                    os.remove(path)
                except OSError as exc:  # pragma: no cover
                    logger.warning("删除动作文件失败: %s", exc)
                    return False
            self._actions.pop(action_id, None)
            self._index.pop(action_id, None)
            if self._active_action_id == action_id:
                self._active_action_id = None
            self._write_index()
            return existed

    def clear_actions(self, keep_running: bool = True) -> int:
        """删除已结束的动作，返回删除数量。"""
        with self._lock:
            removed = 0
            for action_id, summary in list(self._index.items()):
                if keep_running and summary.get("status") == TaskStatus.RUNNING:
                    continue
                if self.delete_action(action_id):
                    removed += 1
            return removed

    def export_action_json(self, action_id: str, path: str) -> bool:
        """把动作（含全部任务）导出为单个 JSON 文件。"""
        action = self.load_action(action_id)
        if action is None:
            return False
        try:
            _atomic_write_json(path, action)
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning("导出动作失败: %s", exc)
            return False


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_default_queue: TaskQueue | None = None
_queue_lock = threading.Lock()
_root_provider: Callable[[], str] | None = None


def _default_root() -> str:
    if _root_provider is not None:
        try:
            current = _root_provider()
            if current:
                return current
        except Exception:  # pragma: no cover - 回调异常
            pass
    return os.path.join(_PROJECT_ROOT, "data", "task_queue")


def get_task_queue() -> TaskQueue:
    """获取全局任务队列实例。"""
    global _default_queue
    with _queue_lock:
        if _default_queue is None:
            _default_queue = TaskQueue(_default_root())
        return _default_queue


def configure_task_queue(root: str | None) -> TaskQueue:
    """切换任务队列存储根目录（通常是当前工作区）。"""
    global _default_queue
    with _queue_lock:
        if _default_queue is None:
            _default_queue = TaskQueue(root or _default_root())
        else:
            _default_queue.set_root(root or _default_root())
        return _default_queue


def set_default_root_provider(provider: Callable[[], str] | None) -> None:
    """注册默认根目录提供者（切换工作区时无需显式调用 configure）。"""
    global _root_provider
    _root_provider = provider


def workspace_task_root(workspace_path: str | None) -> str | None:
    """由工作区路径推导任务队列目录。"""
    if not workspace_path:
        return None
    return os.path.join(workspace_path, "系统数据", TASK_DIR_NAME)
