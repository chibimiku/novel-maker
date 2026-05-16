from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Iterable

from .types import Issue

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


class IssueAggregator:
    def aggregate(self, pass_issue_map: dict[str, list[Issue]]) -> list[Issue]:
        merged: list[Issue] = []
        seen: set[tuple] = set()
        for _pass_name, issues in pass_issue_map.items():
            for issue in issues:
                key = (
                    issue.category,
                    issue.location.node_id,
                    tuple(issue.location.line_numbers),
                    issue.title.strip(),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(issue)
        merged.sort(key=self._sort_key)
        return merged

    def save_issues(self, issues: Iterable[Issue], output_file: str) -> None:
        issue_list = list(issues)
        payload = {
            "version": "v2",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": self._build_summary(issue_list),
            "issues": [i.to_dict() for i in issue_list],
        }
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _build_summary(self, issues: list[Issue]) -> dict:
        by_severity = {"error": 0, "warning": 0, "info": 0}
        by_category: dict[str, int] = {}
        for i in issues:
            by_severity[i.severity] = by_severity.get(i.severity, 0) + 1
            by_category[i.category] = by_category.get(i.category, 0) + 1
        return {
            "total": len(issues),
            "by_severity": by_severity,
            "by_category": by_category,
        }

    @staticmethod
    def _sort_key(issue: Issue):
        return (
            SEVERITY_ORDER.get(issue.severity, 9),
            issue.location.chapter_title,
            issue.location.section_title,
            issue.location.scene_title,
            issue.location.line_numbers[:1] or [0],
            issue.issue_id,
        )

