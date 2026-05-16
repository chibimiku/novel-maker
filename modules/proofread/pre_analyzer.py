from __future__ import annotations

import re
from typing import Any

from .types import SceneContext, SceneMeta


class PreAnalyzer:
    def build_scene_context(self, scene: SceneMeta) -> SceneContext:
        summary = scene.summary_raw or ""
        fields = self._extract_summary_fields(summary)
        return SceneContext(
            time_desc=fields.get("时间", ""),
            locations=self._split_items(fields.get("地点", "")),
            characters_declared=self._split_items(fields.get("人物", "")),
            events=self._split_events(fields.get("事件", "")),
            summary_narrative=fields.get("剧情总结", ""),
        )

    def build_text_chunks(self, scene: SceneMeta) -> dict[str, Any]:
        lines = (scene.content_raw or "").split("\n")
        dialogue_ranges = []
        in_dialogue = False
        start = 0
        for idx, line in enumerate(lines, start=1):
            has_quote = ("“" in line and "”" in line) or ("\"" in line)
            if has_quote and not in_dialogue:
                in_dialogue = True
                start = idx
            if in_dialogue and not line.strip():
                dialogue_ranges.append({"line_start": start, "line_end": idx - 1})
                in_dialogue = False
        if in_dialogue:
            dialogue_ranges.append({"line_start": start, "line_end": len(lines)})
        return {
            "line_count": len(lines),
            "dialogue_ranges": dialogue_ranges,
        }

    def _extract_summary_fields(self, summary: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("时间", "地点", "人物", "事件", "剧情总结"):
            match = re.search(rf"【{key}】[:：]\s*([\s\S]*?)(?=\n【|\Z)", summary)
            out[key] = match.group(1).strip() if match else ""
        return out

    def _split_items(self, text: str) -> list[str]:
        if not text:
            return []
        normalized = text.replace("→", "、").replace(",", "、").replace("，", "、")
        return [x.strip() for x in normalized.split("、") if x.strip()]

    def _split_events(self, text: str) -> list[str]:
        if not text:
            return []
        numbered = re.findall(r"(?:^|\n)\s*\d+\.\s*(.+)", text)
        if numbered:
            return [x.strip() for x in numbered if x.strip()]
        return [x.strip() for x in text.split("\n") if x.strip()]

