from __future__ import annotations

from typing import Any

from .base import BasePass
from ..pre_analyzer import PreAnalyzer
from ..types import Issue, IssueLocation, SceneMeta


class CharacterCountPass(BasePass):
    pass_name = "l20_character_count"
    categories = ["L20"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        analyzer = PreAnalyzer()
        alias_map: dict[str, str] = shared_data.get("character_alias_map", {})
        llm_gateway = shared_data.get("llm_gateway")

        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            ctx = analyzer.build_scene_context(scene)
            declared = {alias_map.get(n, n) for n in ctx.characters_declared}
            detected = self._detect_names(scene.content_raw or "", alias_map)
            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "declared": sorted(declared),
                "detected": sorted(detected),
            }

            # 检测大幅度突变：检测到人数比声明人数明显更多
            if declared and len(detected) >= len(declared) + 2:
                issue = Issue(
                    issue_id=f"L20-{scene.node_id[:6]}-1",
                    category="L20",
                    pass_name=self.pass_name,
                    severity="warning",
                    title="角色数量突变",
                    description=f"摘要声明角色 {len(declared)} 人，正文识别到约 {len(detected)} 人",
                    location=IssueLocation(
                        node_id=scene.node_id,
                        chapter_title=scene.chapter_title,
                        section_title=scene.section_title,
                        scene_title=scene.scene_title,
                        file_path=scene.file_path,
                        line_numbers=[1],
                    ),
                    suggestion="补充角色进入/离场描写，或修正摘要人物列表",
                    evidence=[f"declared={sorted(declared)}", f"detected={sorted(detected)}"],
                )
                if self._llm_recheck(issue, scene, llm_gateway):
                    issues.append(issue)

            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")

        return issues, intermediates

    def _detect_names(self, text: str, alias_map: dict[str, str]) -> set[str]:
        found: set[str] = set()
        for alias, name in alias_map.items():
            if alias and alias in text:
                found.add(name)
        return found

    def _llm_recheck(self, issue: Issue, scene: SceneMeta, llm_gateway) -> bool:
        if llm_gateway is None:
            return True
        payload = {
            "task": "character_count_recheck",
            "issue": issue.to_dict(),
            "scene": {"title": scene.scene_title, "summary": scene.summary_raw, "content": scene.content_raw[:1200]},
            "instruction": '判断是否确实存在角色数量突变，只返回 JSON: {"ok":true,"confirm":true|false}',
        }
        result = llm_gateway.call_json(payload=payload, task_id=f"{self.pass_name}/{scene.node_id}/recheck")
        if not result.get("ok") or not result.get("result"):
            return True
        return bool(result["result"].get("confirm", True))

