from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..types import Issue, SceneMeta


class BasePass(ABC):
    pass_name: str = "base"
    categories: list[str] = []

    @abstractmethod
    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        raise NotImplementedError
