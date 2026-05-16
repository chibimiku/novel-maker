from __future__ import annotations

from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta


class CausalityPass(BasePass):
    pass_name = "l19_causality"
    categories = ["L19"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        llm_gateway = shared_data.get("llm_gateway")
        cfg = shared_data.get("proofread_config")
        batch_enabled = bool(getattr(cfg, "batch_enabled", False))
        batch_max_items = int(getattr(cfg, "batch_find_max_items", 1) or 1)
        batch_max_raw_bytes = int(getattr(cfg, "batch_find_max_raw_bytes", 30000) or 30000)
        if llm_gateway is None:
            return [], {}
        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}
        prev_scene: SceneMeta | None = None
        if batch_enabled and batch_max_items > 1:
            items: list[dict[str, Any]] = []
            scene_by_task: dict[str, SceneMeta] = {}
            prev = None
            for scene in scene_list:
                if stop_event and stop_event.is_set():
                    raise InterruptedError("用户手动暂停")
                payload = {
                    "task": "l19_causality_check",
                    "previous_scene": None
                    if prev is None
                    else {"title": prev.scene_title, "summary": prev.summary_raw, "content": prev.content_raw[:1000]},
                    "current_scene": {"title": scene.scene_title, "summary": scene.summary_raw, "content": scene.content_raw[:1000]},
                    "instruction": (
                        "检查角色行为/剧情推进是否存在因果链断裂。"
                        '仅返回 JSON: {"ok":true,"issues":[...],"intermediate_data":{"causality":"..."}}'
                    ),
                }
                task_id = f"{self.pass_name}/{scene.node_id}"
                scene_by_task[task_id] = scene
                items.append({"task_id": task_id, "payload": payload})
                prev = scene
            batches = llm_gateway.split_batch_items(items, batch_max_items, batch_max_raw_bytes)
            for bi, batch in enumerate(batches, start=1):
                if stop_event and stop_event.is_set():
                    raise InterruptedError("用户手动暂停")
                results = llm_gateway.call_json_batch(batch=batch, batch_id=f"{self.pass_name}/batch_{bi}")
                for it in batch:
                    task_id = str(it.get("task_id", "") or "")
                    scene = scene_by_task.get(task_id)
                    if scene is None:
                        continue
                    parsed = (results.get(task_id, {}) or {}).get("result") or {}
                    intermediates[scene.node_id] = parsed.get("intermediate_data", {})
                    issues.extend(self._build_issues(scene, parsed.get("issues", [])))
                    if progress_callback:
                        progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        else:
            for scene in scene_list:
                if stop_event and stop_event.is_set():
                    raise InterruptedError("用户手动暂停")
                payload = {
                    "task": "l19_causality_check",
                    "previous_scene": None if prev_scene is None else {"title": prev_scene.scene_title, "summary": prev_scene.summary_raw, "content": prev_scene.content_raw[:1000]},
                    "current_scene": {"title": scene.scene_title, "summary": scene.summary_raw, "content": scene.content_raw[:1000]},
                    "instruction": (
                        "检查角色行为/剧情推进是否存在因果链断裂。"
                        '仅返回 JSON: {"ok":true,"issues":[...],"intermediate_data":{"causality":"..."}}'
                    ),
                }
                result = llm_gateway.call_json(payload=payload, task_id=f"{self.pass_name}/{scene.node_id}")
                parsed = result.get("result") or {}
                intermediates[scene.node_id] = parsed.get("intermediate_data", {})
                issues.extend(self._build_issues(scene, parsed.get("issues", [])))
                prev_scene = scene
                if progress_callback:
                    progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        return issues, intermediates

    def _build_issues(self, scene: SceneMeta, items: list[dict[str, Any]]) -> list[Issue]:
        out: list[Issue] = []
        for idx, item in enumerate(items, start=1):
            out.append(
                Issue(
                    issue_id=str(item.get("issue_id", f"L19-{scene.node_id[:6]}-{idx}")),
                    category="L19",
                    pass_name=self.pass_name,
                    severity=str(item.get("severity", "warning")),
                    title=str(item.get("title", "因果链断裂")),
                    description=str(item.get("description", "")),
                    location=IssueLocation(
                        node_id=scene.node_id,
                        chapter_title=scene.chapter_title,
                        section_title=scene.section_title,
                        scene_title=scene.scene_title,
                        file_path=scene.file_path,
                        line_numbers=[int(x) for x in item.get("line_numbers", []) if str(x).isdigit()],
                    ),
                    suggestion=str(item.get("suggestion", "补充行为动机或前因触发")),
                    evidence=[str(x) for x in item.get("evidence", [])],
                )
            )
        return out
