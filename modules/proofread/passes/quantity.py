from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta

ZH_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


class QuantityPass(BasePass):
    pass_name = "l11_quantity"
    categories = ["L11"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}
        llm_gateway = shared_data.get("llm_gateway")

        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            local_entities = self._extract_local_counts(scene.content_raw or "")
            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "counts": local_entities,
            }
            for noun, items in local_entities.items():
                values = {x["count"] for x in items}
                if len(values) >= 2:
                    issue = Issue(
                        issue_id=f"L11-{scene.node_id[:6]}-{items[0]['line']}",
                        category="L11",
                        pass_name=self.pass_name,
                        severity="warning",
                        title="数字/数量矛盾",
                        description=f"同一场景中“{noun}”数量出现不一致: {sorted(values)}",
                        location=IssueLocation(
                            node_id=scene.node_id,
                            chapter_title=scene.chapter_title,
                            section_title=scene.section_title,
                            scene_title=scene.scene_title,
                            file_path=scene.file_path,
                            line_numbers=sorted({x["line"] for x in items}),
                        ),
                        suggestion="补充人数/数量变化的原因或统一描述",
                        evidence=[x["text"] for x in items[:3]],
                    )
                    if self._llm_recheck(issue, scene, llm_gateway):
                        issues.append(issue)
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")
        return issues, intermediates

    def _extract_local_counts(self, text: str) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        lines = text.split("\n")
        for ln, line in enumerate(lines, start=1):
            for m in re.finditer(r"([一二两三四五六七八九十\d]{1,3})(个|名|位|人|把|只|条)([\u4e00-\u9fff]{1,8})", line):
                raw = m.group(1)
                noun = m.group(3)
                count = self._to_int(raw)
                if count is None:
                    continue
                result[noun].append({"count": count, "line": ln, "text": line.strip()})
        return result

    def _to_int(self, raw: str) -> int | None:
        if raw.isdigit():
            return int(raw)
        if raw in ZH_NUM:
            return ZH_NUM[raw]
        return None

    def _llm_recheck(self, issue: Issue, scene: SceneMeta, llm_gateway) -> bool:
        if llm_gateway is None:
            return True
        payload = {
            "task": "quantity_recheck",
            "issue": issue.to_dict(),
            "scene": {"title": scene.scene_title, "content": scene.content_raw[:1200]},
            "instruction": '判断数量变化是否合理，只返回 JSON: {"ok":true,"confirm":true|false}',
        }
        result = llm_gateway.call_json(payload=payload, task_id=f"{self.pass_name}/{scene.node_id}/recheck")
        if not result.get("ok") or not result.get("result"):
            return True
        return bool(result["result"].get("confirm", True))

