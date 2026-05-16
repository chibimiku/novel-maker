from __future__ import annotations

import re
from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta


class AttributeValuesPass(BasePass):
    pass_name = "l14_attribute_values"
    categories = ["L14"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        base_age = self._build_age_baseline(shared_data.get("characters", []))
        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            scene_hits = []
            lines = (scene.content_raw or "").split("\n")
            for ln, line in enumerate(lines, start=1):
                for name, age in base_age.items():
                    if name in line:
                        age_found = self._extract_age(line)
                        if age_found is not None and age_found != age:
                            scene_hits.append({"line": ln, "name": name, "expected": age, "found": age_found})
                            issues.append(
                                Issue(
                                    issue_id=f"L14-{scene.node_id[:6]}-{ln}",
                                    category="L14",
                                    pass_name=self.pass_name,
                                    severity="warning",
                                    title="角色属性数值矛盾",
                                    description=f"{name} 设定年龄 {age}，正文检测到 {age_found}",
                                    location=IssueLocation(
                                        node_id=scene.node_id,
                                        chapter_title=scene.chapter_title,
                                        section_title=scene.section_title,
                                        scene_title=scene.scene_title,
                                        file_path=scene.file_path,
                                        line_numbers=[ln],
                                    ),
                                    suggestion="统一角色年龄数值，或补充时间推进说明",
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

    def _build_age_baseline(self, characters: list[dict[str, Any]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in characters:
            name = str(c.get("姓名", "")).strip()
            age_text = str(c.get("年龄", "")).strip()
            if not name:
                continue
            age = self._extract_age(age_text)
            if age is not None:
                out[name] = age
        return out

    def _extract_age(self, text: str) -> int | None:
        m = re.search(r"(\d{1,3})\s*岁", text)
        if m:
            return int(m.group(1))
        zh = {
            "二十": 20,
            "二十一": 21,
            "二十二": 22,
            "二十三": 23,
            "十九": 19,
            "十八": 18,
        }
        for k, v in zh.items():
            if k in text:
                return v
        return None

