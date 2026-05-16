from __future__ import annotations

import re
from typing import Any

from .base import BasePass
from ..pre_analyzer import PreAnalyzer
from ..types import Issue, IssueLocation, SceneMeta

TIME_KEYWORDS = {
    "清晨": 6,
    "早晨": 7,
    "上午": 10,
    "中午": 12,
    "下午": 15,
    "傍晚": 18,
    "黄昏": 18,
    "夜": 21,
    "深夜": 23,
    "凌晨": 2,
}


class TimelinePass(BasePass):
    pass_name = "l5_timeline"
    categories = ["L5"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        analyzer = PreAnalyzer()
        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}
        prev_scene: SceneMeta | None = None
        prev_hour: int | None = None
        prev_summary = ""
        llm_gateway = shared_data.get("llm_gateway")

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            ctx = analyzer.build_scene_context(scene)
            hour = self._extract_hour(ctx.time_desc, scene.content_raw)
            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "time_desc": ctx.time_desc,
                "normalized_hour": hour,
            }

            if prev_scene is not None and prev_hour is not None and hour is not None:
                jump = hour - prev_hour
                if jump < -6:
                    issue = self._build_issue(
                        scene=scene,
                        title="时间线可能反转",
                        description=f"相邻场景时间从 {prev_hour}:00 回退到 {hour}:00",
                        evidence=[f"{prev_scene.scene_title} -> {scene.scene_title}"],
                    )
                    if self._llm_recheck(issue, prev_scene, scene, llm_gateway):
                        issues.append(issue)

            if prev_scene is not None and "紧接" in (ctx.time_desc + scene.summary_raw) and prev_summary:
                # 若声明“紧接”，但时间段跨度很大，也发出告警。
                if prev_hour is not None and hour is not None and abs(hour - prev_hour) >= 5:
                    issue = self._build_issue(
                        scene=scene,
                        title="时间流速可能不合理",
                        description="标注为紧接上一场景，但时间跨度过大",
                        evidence=[f"{prev_hour}:00 -> {hour}:00"],
                    )
                    if self._llm_recheck(issue, prev_scene, scene, llm_gateway):
                        issues.append(issue)

            prev_scene = scene
            prev_hour = hour
            prev_summary = scene.summary_raw or ""
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        return issues, intermediates

    def _extract_hour(self, summary_time: str, content: str) -> int | None:
        text = f"{summary_time}\n{content[:300]}"
        m = re.search(r"(\d{1,2})\s*[:点时]", text)
        if m:
            h = int(m.group(1))
            if 0 <= h <= 23:
                return h
        for k, hour in TIME_KEYWORDS.items():
            if k in text:
                return hour
        return None

    def _build_issue(self, scene: SceneMeta, title: str, description: str, evidence: list[str]) -> Issue:
        return Issue(
            issue_id=f"L5-{scene.node_id[:6]}-1",
            category="L5",
            pass_name=self.pass_name,
            severity="warning",
            title=title,
            description=description,
            location=IssueLocation(
                node_id=scene.node_id,
                chapter_title=scene.chapter_title,
                section_title=scene.section_title,
                scene_title=scene.scene_title,
                file_path=scene.file_path,
                line_numbers=[1],
            ),
            suggestion="补充时间过渡描述或调整时间标记",
            evidence=evidence,
        )

    def _llm_recheck(self, issue: Issue, prev_scene: SceneMeta, scene: SceneMeta, llm_gateway) -> bool:
        if llm_gateway is None:
            return True
        payload = {
            "task": "timeline_recheck",
            "issue": issue.to_dict(),
            "prev_scene": {"title": prev_scene.scene_title, "summary": prev_scene.summary_raw, "content": prev_scene.content_raw[:800]},
            "curr_scene": {"title": scene.scene_title, "summary": scene.summary_raw, "content": scene.content_raw[:800]},
            "instruction": '判断是否确实存在时间线矛盾，只返回 JSON: {"ok":true,"confirm":true|false}',
        }
        result = llm_gateway.call_json(payload=payload, task_id=f"{self.pass_name}/{scene.node_id}/recheck")
        if not result.get("ok") or not result.get("result"):
            return True
        return bool(result["result"].get("confirm", True))

