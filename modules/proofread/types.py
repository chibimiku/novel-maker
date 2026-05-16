from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]
TaskStatus = Literal["pending", "running", "completed", "failed", "skipped"]
Phase = Literal["find", "solve"]


@dataclass
class SceneMeta:
    node_id: str
    chapter_title: str
    section_title: str
    scene_title: str
    file_path: str
    summary_raw: str
    content_raw: str
    content_md5: str


@dataclass
class SceneContext:
    time_desc: str = ""
    locations: list[str] = field(default_factory=list)
    characters_declared: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    summary_narrative: str = ""


@dataclass
class IssueLocation:
    node_id: str
    chapter_title: str
    section_title: str
    scene_title: str
    file_path: str
    line_numbers: list[int] = field(default_factory=list)


@dataclass
class Issue:
    issue_id: str
    category: str
    pass_name: str
    severity: Severity
    title: str
    description: str
    location: IssueLocation
    suggestion: str
    evidence: list[str] = field(default_factory=list)
    ignored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "category": self.category,
            "pass_name": self.pass_name,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "location": {
                "node_id": self.location.node_id,
                "chapter_title": self.location.chapter_title,
                "section_title": self.location.section_title,
                "scene_title": self.location.scene_title,
                "file_path": self.location.file_path,
                "line_numbers": self.location.line_numbers,
            },
            "suggestion": self.suggestion,
            "evidence": self.evidence,
            "ignored": self.ignored,
        }

