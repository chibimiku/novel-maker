from __future__ import annotations

import json
import os
from collections import Counter
from typing import Iterable

from ..types import Issue


class IssueReporter:
    def to_json(self, issues: Iterable[Issue]) -> dict:
        issue_list = list(issues)
        sev = Counter(i.severity for i in issue_list)
        cat = Counter(i.category for i in issue_list)
        return {
            "summary": {
                "total": len(issue_list),
                "by_severity": dict(sev),
                "by_category": dict(cat),
            },
            "issues": [i.to_dict() for i in issue_list],
        }

    def write_json(self, issues: Iterable[Issue], file_path: str) -> None:
        payload = self.to_json(issues)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def to_markdown(self, issues: Iterable[Issue]) -> str:
        issue_list = list(issues)
        sev = Counter(i.severity for i in issue_list)
        lines = [
            "# 校对问题报告",
            "",
            f"- 总问题数: {len(issue_list)}",
            f"- error: {sev.get('error', 0)}",
            f"- warning: {sev.get('warning', 0)}",
            f"- info: {sev.get('info', 0)}",
            "",
            "| ID | 类别 | 严重级别 | 场景 | 标题 |",
            "|---|---|---|---|---|",
        ]
        for i in issue_list:
            lines.append(
                f"| {i.issue_id} | {i.category} | {i.severity} | {i.location.scene_title} | {i.title} |"
            )
        return "\n".join(lines)

