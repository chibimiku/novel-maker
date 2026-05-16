from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime
from typing import Any

from .debug_log import dlog

logger = logging.getLogger(__name__)


class ProofreadCheckpointManager:
    def __init__(self, workspace_path: str):
        self.checkpoint_dir = os.path.join(workspace_path, "系统数据", "proofread_checkpoint")
        self.tasks_dir = os.path.join(self.checkpoint_dir, "tasks")
        self.responses_dir = os.path.join(self.checkpoint_dir, "llm_responses")
        self.run_state_path = os.path.join(self.checkpoint_dir, "run_state.json")
        self._tasks_lock = threading.RLock()
        os.makedirs(self.tasks_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)

    def init_run(
        self,
        phase: str,
        run_id: str,
        md5_snapshot: dict[str, str],
        pass_names: list[str],
    ) -> dict[str, Any]:
        run_state = {
            "run_id": run_id,
            "phase": phase,
            "status": "running",
            "md5_snapshot": md5_snapshot,
            "passes": {
                name: {
                    "status": "pending",
                    "scenes_total": 0,
                    "scenes_completed": 0,
                    "scenes_failed": 0,
                }
                for name in pass_names
            },
            "llm_calls_total": 0,
            "llm_tokens_total": 0,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.save_run_state(run_state)
        dlog(logger, f"checkpoint init_run: phase={phase}, run_id={run_id}, pass_names={len(pass_names)}")
        for pass_name in pass_names:
            self._atomic_write_json(self._task_file(pass_name), {"pass_name": pass_name, "scenes": {}, "issues": []})
        return run_state

    def load_run_state(self) -> dict[str, Any] | None:
        if not os.path.exists(self.run_state_path):
            return None
        with open(self.run_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None

    def save_run_state(self, run_state: dict[str, Any]) -> None:
        run_state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._atomic_write_json(self.run_state_path, run_state)
        dlog(logger, f"checkpoint save_run_state: phase={run_state.get('phase')}, status={run_state.get('status')}")

    def save_task_state(self, pass_name: str, scene_id: str, task_state: dict[str, Any]) -> None:
        with self._tasks_lock:
            data = self.load_pass_tasks(pass_name)
            data.setdefault("scenes", {})[scene_id] = task_state
            self._atomic_write_json(self._task_file(pass_name), data)
        dlog(logger, f"checkpoint save_task_state: pass={pass_name}, scene={scene_id}, status={task_state.get('status')}")

    def save_pass_issues(self, pass_name: str, issues: list[dict[str, Any]]) -> None:
        with self._tasks_lock:
            data = self.load_pass_tasks(pass_name)
            data["issues"] = issues
            self._atomic_write_json(self._task_file(pass_name), data)
        dlog(logger, f"checkpoint save_pass_issues: pass={pass_name}, issues={len(issues)}")

    def append_pass_issues(self, pass_name: str, new_issues: list[dict[str, Any]]) -> None:
        with self._tasks_lock:
            data = self.load_pass_tasks(pass_name)
            existing = data.get("issues", []) or []
            existing_ids = set()
            for item in existing:
                if isinstance(item, dict):
                    existing_ids.add(str(item.get("issue_id", "")))
            for ni in new_issues:
                if isinstance(ni, dict) and str(ni.get("issue_id", "")) not in existing_ids:
                    existing.append(ni)
            data["issues"] = existing
            self._atomic_write_json(self._task_file(pass_name), data)
        dlog(logger, f"checkpoint append_pass_issues: pass={pass_name}, added={len(new_issues)}, total={len(existing)}")

    def get_completed_scene_ids(self, pass_name: str) -> set[str]:
        data = self.load_pass_tasks(pass_name)
        completed: set[str] = set()
        for scene_id, state in data.get("scenes", {}).items():
            if state.get("status") == "completed":
                completed.add(scene_id)
        return completed

    def get_pass_issue_count(self, pass_name: str) -> int:
        data = self.load_pass_tasks(pass_name)
        return len(data.get("issues", []) or [])

    def load_pass_tasks(self, pass_name: str) -> dict[str, Any]:
        with self._tasks_lock:
            fp = self._task_file(pass_name)
            if not os.path.exists(fp):
                return {"pass_name": pass_name, "scenes": {}, "issues": []}
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"pass_name": pass_name, "scenes": {}, "issues": []}
            data.setdefault("scenes", {})
            data.setdefault("issues", [])
            return data

    def list_pending_scenes(self, pass_name: str) -> list[str]:
        data = self.load_pass_tasks(pass_name)
        out: list[str] = []
        for scene_id, state in data.get("scenes", {}).items():
            if state.get("status") not in ("completed", "skipped"):
                out.append(scene_id)
        return out

    def save_llm_response(self, pass_name: str, scene_id: str, payload: dict[str, Any]) -> str:
        out_dir = os.path.join(self.responses_dir, pass_name)
        os.makedirs(out_dir, exist_ok=True)
        fp = os.path.join(out_dir, f"{scene_id}.json")
        self._atomic_write_json(fp, payload)
        rel = os.path.relpath(fp, self.checkpoint_dir).replace("\\", "/")
        return rel

    def cleanup(self) -> None:
        if os.path.exists(self.checkpoint_dir):
            shutil.rmtree(self.checkpoint_dir, ignore_errors=True)

    def _task_file(self, pass_name: str) -> str:
        return os.path.join(self.tasks_dir, f"{pass_name}.json")

    @staticmethod
    def _atomic_write_json(path: str, data: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, dir=os.path.dirname(path), suffix=".tmp"
        ) as tf:
            json.dump(data, tf, ensure_ascii=False, indent=2)
            tf.flush()
            os.fsync(tf.fileno())
            temp_path = tf.name
        os.replace(temp_path, path)
