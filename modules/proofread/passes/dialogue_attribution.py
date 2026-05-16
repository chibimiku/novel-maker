from __future__ import annotations

import re
from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta


class DialogueAttributionPass(BasePass):
    pass_name = "l15_dialogue_attribution"
    categories = ["L15"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}
        threshold = 4

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            lines = (scene.content_raw or "").split("\n")
            no_attr_run = 0
            max_run = 0
            for ln, line in enumerate(lines, start=1):
                if self._has_dialogue(line):
                    if self._has_speaker_tag(line):
                        no_attr_run = 0
                    else:
                        no_attr_run += 1
                        max_run = max(max_run, no_attr_run)
                        if no_attr_run == threshold:
                            issues.append(
                                Issue(
                                    issue_id=f"L15-{scene.node_id[:6]}-{ln}",
                                    category="L15",
                                    pass_name=self.pass_name,
                                    severity="warning",
                                    title="对话归属不明",
                                    description=f"连续 {threshold} 段对话缺少说话人归属",
                                    location=IssueLocation(
                                        node_id=scene.node_id,
                                        chapter_title=scene.chapter_title,
                                        section_title=scene.section_title,
                                        scene_title=scene.scene_title,
                                        file_path=scene.file_path,
                                        line_numbers=[ln],
                                    ),
                                    suggestion="在关键对话段添加“XX说/问/答”等归属标记",
                                    evidence=[line.strip()],
                                )
                            )
                elif line.strip():
                    no_attr_run = 0
            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "max_no_attr_run": max_run,
            }
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")

        return issues, intermediates

    def _has_dialogue(self, line: str) -> bool:
        return ("“" in line and "”" in line) or ("\"" in line and len(line.strip()) > 2)

    def _has_speaker_tag(self, line: str) -> bool:
        return bool(re.search(r"(说|问|答|道|喊|低声|轻声)", line))

