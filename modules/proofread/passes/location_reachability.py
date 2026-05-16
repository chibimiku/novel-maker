from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .base import BasePass
from ..types import Issue, IssueLocation, SceneMeta
from ..pre_analyzer import PreAnalyzer


class LocationReachabilityPass(BasePass):
    pass_name = "l6_location_reachability"
    categories = ["L6"]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict[str, Any],
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict[str, Any]]:
        analyzer = PreAnalyzer()
        graph = self._build_graph(shared_data.get("locations", []))
        issues: list[Issue] = []
        intermediates: dict[str, Any] = {}

        prev_scene = None
        prev_last_location = ""
        for scene in scene_list:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")

            ctx = analyzer.build_scene_context(scene)
            curr_first = ctx.locations[0] if ctx.locations else ""
            curr_last = ctx.locations[-1] if ctx.locations else ""

            if prev_scene and prev_last_location and curr_first:
                reachable = self._is_reachable_within(graph, prev_last_location, curr_first, max_steps=2)
                if not reachable:
                    issues.append(
                        Issue(
                            issue_id=f"L6-{scene.node_id[:6]}-1",
                            category="L6",
                            pass_name=self.pass_name,
                            severity="warning",
                            title="地点可达性矛盾",
                            description=f"相邻场景地点转移可能不可达：{prev_last_location} -> {curr_first}",
                            location=IssueLocation(
                                node_id=scene.node_id,
                                chapter_title=scene.chapter_title,
                                section_title=scene.section_title,
                                scene_title=scene.scene_title,
                                file_path=scene.file_path,
                                line_numbers=[1],
                            ),
                            suggestion="补充中间地点或交通过渡描写",
                            evidence=[f"{prev_scene.scene_title} -> {scene.scene_title}"],
                        )
                    )

            intermediates[scene.node_id] = {
                "scene_id": scene.node_id,
                "pass_name": self.pass_name,
                "summary_locations": ctx.locations,
            }

            prev_scene = scene
            prev_last_location = curr_last or curr_first
            if progress_callback:
                progress_callback(f"[{self.pass_name}] {scene.scene_title} 完成")

        return issues, intermediates

    def _build_graph(self, locations: list[dict[str, Any]]) -> dict[str, set[str]]:
        g: dict[str, set[str]] = defaultdict(set)
        for item in locations:
            name = str(item.get("地点名称", "") or item.get("name", "")).strip()
            if not name:
                continue
            links = item.get("连接地点", []) or item.get("links", [])
            if isinstance(links, str):
                links = [x.strip() for x in links.replace("，", ",").split(",") if x.strip()]
            for nxt in links:
                nxt = str(nxt).strip()
                if not nxt:
                    continue
                g[name].add(nxt)
                g[nxt].add(name)
        return g

    def _is_reachable_within(self, g: dict[str, set[str]], src: str, dst: str, max_steps: int) -> bool:
        if src == dst:
            return True
        if src not in g or dst not in g:
            return False
        dq = deque([(src, 0)])
        visited = {src}
        while dq:
            cur, dep = dq.popleft()
            if dep >= max_steps:
                continue
            for nxt in g.get(cur, set()):
                if nxt == dst:
                    return True
                if nxt not in visited:
                    visited.add(nxt)
                    dq.append((nxt, dep + 1))
        return False

