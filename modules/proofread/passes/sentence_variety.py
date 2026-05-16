from __future__ import annotations

import re
from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta


class SentenceVarietyPass(BasePass):
    pass_name = "w4_sentence_variety"
    categories = ["W4"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}
        threshold = int(shared_data.get("sentence_repeat_threshold", 5))

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            scene_issues, stats = self._check_scene(scene, threshold)
            issues.extend(scene_issues)
            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "stats": stats,
            }
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        return issues, intermediates

    def _check_scene(self, scene: SceneMeta, threshold: int) -> tuple[list[Issue], dict[str, Any]]:
        text = scene.content_raw or ""
        sentences = [s.strip() for s in re.split(r"[。！？!?]", text) if s.strip()]
        starts = [self._sentence_start(s) for s in sentences]
        issues: list[Issue] = []
        max_run = 0
        run = 1
        for i in range(1, len(starts)):
            if starts[i] and starts[i] == starts[i - 1]:
                run += 1
            else:
                max_run = max(max_run, run)
                run = 1
        max_run = max(max_run, run)

        if max_run >= threshold:
            issues.append(
                Issue(
                    issue_id=f"W4-{scene.node_id[:6]}-1",
                    category="W4",
                    pass_name=self.pass_name,
                    severity="warning",
                    title="句式结构单一",
                    description=f"检测到连续句首重复，最大连续次数 {max_run}",
                    location=IssueLocation(
                        node_id=scene.node_id,
                        chapter_title=scene.chapter_title,
                        section_title=scene.section_title,
                        scene_title=scene.scene_title,
                        file_path=scene.file_path,
                        line_numbers=[1],
                    ),
                    suggestion="尝试调整句式和句首结构，增加长短句变化",
                    evidence=[sentences[0] if sentences else ""],
                )
            )

        stats = {"sentence_count": len(sentences), "max_prefix_run": max_run}
        return issues, stats

    def _sentence_start(self, sentence: str) -> str:
        sentence = sentence.strip()
        if not sentence:
            return ""
        return sentence[:2]

