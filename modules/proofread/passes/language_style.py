from __future__ import annotations

from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta

TRADITIONAL_MAP = {
    "透過": "通过",
    "螢幕": "屏幕",
    "爲": "为",
    "後來": "后来",
    "裏": "里",
    "這裡": "这里",
    "那裡": "那里",
    "學校": "学校",
    "說": "说",
    "嗎": "吗",
    "妳": "你",
}


class LanguageStylePass(BasePass):
    pass_name = "w1_language_style"
    categories = ["W1"]

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
            scene_hits = []
            lines = (scene.content_raw or "").split("\n")
            for ln, line in enumerate(lines, start=1):
                for trad, simp in TRADITIONAL_MAP.items():
                    if trad in line:
                        scene_hits.append({"line": ln, "term": trad, "suggest": simp})
                        issues.append(
                            Issue(
                                issue_id=f"W1-{scene.node_id[:6]}-{ln}-{trad}",
                                category="W1",
                                pass_name=self.pass_name,
                                severity="warning",
                                title="非简体中文习惯",
                                description=f"检测到“{trad}”，建议使用“{simp}”",
                                location=IssueLocation(
                                    node_id=scene.node_id,
                                    chapter_title=scene.chapter_title,
                                    section_title=scene.section_title,
                                    scene_title=scene.scene_title,
                                    file_path=scene.file_path,
                                    line_numbers=[ln],
                                ),
                                suggestion=f"将“{trad}”替换为“{simp}”",
                                evidence=[line.strip()],
                            )
                        )
            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "hits": scene_hits,
            }
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        return issues, intermediates

