from __future__ import annotations

from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta


class CharacterKnowledgePass(BasePass):
    pass_name = "l10_character_knowledge"
    categories = ["L10"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        llm_gateway = shared_data.get("llm_gateway")
        if llm_gateway is None:
            return [], {}

        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}
        memory: list[dict[str, Any]] = []

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            payload = {
                "task": "l10_character_knowledge_check",
                "scene": {"title": scene.scene_title, "summary": scene.summary_raw, "content": scene.content_raw[:1400]},
                "knowledge_memory": memory[-20:],
                "instruction": (
                    "检查角色是否知道不该知道的信息，或遗忘已知关键信息。"
                    '仅返回 JSON: {"ok":true,"issues":[...],"intermediate_data":{"knowledge_events":[...],"memory_delta":[...]}}'
                ),
            }
            result = llm_gateway.call_json(payload=payload, task_id=f"{self.pass_name}/{scene.node_id}")
            parsed = result.get("result") or {}
            inter = parsed.get("intermediate_data") or {}
            if not isinstance(inter, dict):
                inter = {}
            intermediates[scene.node_id] = inter
            memory.extend(inter.get("memory_delta", []))
            issues.extend(self._build_issues(scene, parsed.get("issues", [])))
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        return issues, intermediates

    def _build_issues(self, scene: SceneMeta, items: list[dict[str, Any]]) -> list[Issue]:
        out: list[Issue] = []
        for idx, item in enumerate(items, start=1):
            out.append(
                Issue(
                    issue_id=str(item.get("issue_id", f"L10-{scene.node_id[:6]}-{idx}")),
                    category="L10",
                    pass_name=self.pass_name,
                    severity=str(item.get("severity", "warning")),
                    title=str(item.get("title", "角色知识矛盾")),
                    description=str(item.get("description", "")),
                    location=IssueLocation(
                        node_id=scene.node_id,
                        chapter_title=scene.chapter_title,
                        section_title=scene.section_title,
                        scene_title=scene.scene_title,
                        file_path=scene.file_path,
                        line_numbers=[int(x) for x in item.get("line_numbers", []) if str(x).isdigit()],
                    ),
                    suggestion=str(item.get("suggestion", "补充信息来源，或修正文中角色认知")),
                    evidence=[str(x) for x in item.get("evidence", [])],
                )
            )
        return out
