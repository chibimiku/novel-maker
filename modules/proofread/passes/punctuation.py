from __future__ import annotations

import re
from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta


class PunctuationPass(BasePass):
    pass_name = "w5_punctuation"
    categories = ["W5"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            scene_issues = self._check_scene(scene)
            issues.extend(scene_issues)
            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "hits": [i.title for i in scene_issues],
            }
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        return issues, intermediates

    def _check_scene(self, scene: SceneMeta) -> list[Issue]:
        text = scene.content_raw or ""
        lines = text.split("\n")
        found: list[Issue] = []

        for ln, line in enumerate(lines, start=1):
            if "..." in line or "。。。" in line:
                found.append(self._mk_issue(scene, ln, "省略号格式不规范", "建议统一使用中文省略号“……”"))
            if "--" in line:
                found.append(self._mk_issue(scene, ln, "破折号格式不规范", "建议使用中文破折号“——”"))
            if re.search(r"[\u4e00-\u9fff],[\u4e00-\u9fff]", line):
                found.append(self._mk_issue(scene, ln, "中英标点混用", "中文语句中建议使用全角逗号“，”"))
        return found

    def _mk_issue(self, scene: SceneMeta, line_no: int, title: str, suggestion: str) -> Issue:
        return Issue(
            issue_id=f"W5-{scene.node_id[:6]}-{line_no}",
            category="W5",
            pass_name=self.pass_name,
            severity="warning",
            title=title,
            description=f"在第 {line_no} 行发现：{title}",
            location=IssueLocation(
                node_id=scene.node_id,
                chapter_title=scene.chapter_title,
                section_title=scene.section_title,
                scene_title=scene.scene_title,
                file_path=scene.file_path,
                line_numbers=[line_no],
            ),
            suggestion=suggestion,
            evidence=[],
        )
