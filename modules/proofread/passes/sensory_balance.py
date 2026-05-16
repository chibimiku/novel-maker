from __future__ import annotations

import re
from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta


class SensoryBalancePass(BasePass):
    pass_name = "w3_sensory_balance"
    categories = ["W3"]

    VISION = ("看", "望", "光", "影", "颜色", "明亮", "黑暗")
    HEARING = ("听", "声", "响", "低语", "喧哗", "寂静")
    SMELL = ("闻", "气味", "香", "臭", "腥", "烟味")
    TOUCH = ("触", "冷", "热", "疼", "麻", "湿", "干")
    TASTE = ("尝", "甜", "苦", "咸", "辣", "酸")

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
            dist = self._count(scene.content_raw or "")
            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "distribution": dist,
            }
            total = sum(dist.values())
            if total > 0:
                vision_ratio = dist["vision"] / total
                if vision_ratio >= 0.9 and total >= 8:
                    issues.append(
                        Issue(
                            issue_id=f"W3-{scene.node_id[:6]}-1",
                            category="W3",
                            pass_name=self.pass_name,
                            severity="info",
                            title="感官描写偏单一",
                            description=f"视觉相关描写占比 {vision_ratio:.0%}",
                            location=IssueLocation(
                                node_id=scene.node_id,
                                chapter_title=scene.chapter_title,
                                section_title=scene.section_title,
                                scene_title=scene.scene_title,
                                file_path=scene.file_path,
                                line_numbers=[1],
                            ),
                            suggestion="可适量补充听觉/触觉/嗅觉信息",
                            evidence=[],
                        )
                    )
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        return issues, intermediates

    def _count(self, text: str) -> dict[str, int]:
        text = re.sub(r"\s+", "", text)
        return {
            "vision": self._hits(text, self.VISION),
            "hearing": self._hits(text, self.HEARING),
            "smell": self._hits(text, self.SMELL),
            "touch": self._hits(text, self.TOUCH),
            "taste": self._hits(text, self.TASTE),
        }

    def _hits(self, text: str, words: tuple[str, ...]) -> int:
        return sum(text.count(w) for w in words)

