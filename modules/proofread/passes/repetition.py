from __future__ import annotations

from collections import defaultdict
from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta


class RepetitionPass(BasePass):
    pass_name = "w2_repetition"
    categories = ["W2"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        phrases = self._extract_character_phrases(shared_data.get("characters", []))
        freq: dict[str, int] = defaultdict(int)
        scene_hits: dict[str, set[str]] = defaultdict(set)
        first_loc: dict[str, tuple[SceneMeta, int, str]] = {}

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            lines = (scene.content_raw or "").split("\n")
            for ln, line in enumerate(lines, start=1):
                for phrase in phrases:
                    if phrase and phrase in line:
                        freq[phrase] += 1
                        scene_hits[phrase].add(scene.node_id)
                        first_loc.setdefault(phrase, (scene, ln, line.strip()))
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")

        issues: list[Issue] = []
        for phrase, count in freq.items():
            if count > 10 and len(scene_hits[phrase]) >= 5:
                scene, ln, evidence = first_loc[phrase]
                issues.append(
                    Issue(
                        issue_id=f"W2-{scene.node_id[:6]}-{ln}",
                        category="W2",
                        pass_name=self.pass_name,
                        severity="warning",
                        title="特征词过度重复",
                        description=f"短语“{phrase}”出现 {count} 次，覆盖 {len(scene_hits[phrase])} 个场景",
                        location=IssueLocation(
                            node_id=scene.node_id,
                            chapter_title=scene.chapter_title,
                            section_title=scene.section_title,
                            scene_title=scene.scene_title,
                            file_path=scene.file_path,
                            line_numbers=[ln],
                        ),
                        suggestion="尝试替换为同义表达或减少重复描写",
                        evidence=[evidence],
                    )
                )

        intermediates = {
            "global": {
                "pass_name": self.pass_name,
                "frequency": dict(sorted(freq.items(), key=lambda kv: kv[1], reverse=True)),
                "scene_coverage": {k: len(v) for k, v in scene_hits.items()},
            }
        }
        return issues, intermediates

    def _extract_character_phrases(self, characters: list[dict[str, Any]]) -> list[str]:
        phrases: set[str] = set()
        candidate_keys = ("性格", "背景描述", "特殊能力", "外貌", "描述", "标签")
        for c in characters:
            for k in candidate_keys:
                val = c.get(k)
                if isinstance(val, str):
                    items = [x.strip() for x in val.replace("，", ",").split(",") if x.strip()]
                    for it in items:
                        if 2 <= len(it) <= 12:
                            phrases.add(it)
        return sorted(phrases)

