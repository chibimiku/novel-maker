from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
from typing import Any, Callable

from .debug_log import dlog
from .types import Issue, SceneMeta

logger = logging.getLogger(__name__)


class SolveEngine:
    def __init__(self, workspace, llm_gateway):
        self.workspace = workspace
        self.llm_gateway = llm_gateway

    def group_issues_by_scene(
        self,
        issues: list[Issue],
        include_categories: set[str] | None = None,
        include_chapters: set[str] | None = None,
    ) -> dict[str, list[Issue]]:
        grouped: dict[str, list[Issue]] = defaultdict(list)
        for issue in issues:
            if issue.ignored or issue.severity == "info":
                continue
            if include_categories and issue.category not in include_categories:
                continue
            if include_chapters and issue.location.chapter_title not in include_chapters:
                continue
            grouped[issue.location.node_id].append(issue)
        dlog(
            logger,
            f"group_issues_by_scene: input={len(issues)}, grouped_scenes={len(grouped)}, categories_filter={include_categories}, chapters_filter={include_chapters}",
        )
        return grouped

    def build_fix_payload(
        self,
        scene: SceneMeta,
        scene_issues: list[Issue],
        characters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        issue_items = []
        for issue in scene_issues:
            issue_items.append(
                {
                    "issue_id": issue.issue_id,
                    "category": issue.category,
                    "severity": issue.severity,
                    "title": issue.title,
                    "description": issue.description,
                    "suggestion": issue.suggestion,
                    "evidence": issue.evidence,
                }
            )
        return {
            "scene_id": scene.node_id,
            "scene_title": scene.scene_title,
            "content": scene.content_raw,
            "summary": scene.summary_raw,
            "characters": characters,
            "issues": issue_items,
            "instruction": (
                "你是专业小说编辑。请修复所有问题并保持剧情主线不变。"
                '仅返回 JSON: {"ok":true,"fixed_text":"..."}'
            ),
        }

    def run_solve(
        self,
        scenes: dict[str, SceneMeta],
        grouped_issues: dict[str, list[Issue]],
        characters: list[dict[str, Any]],
        stop_event=None,
        progress_callback=None,
        skip_existing_pending: bool = True,
        max_workers: int = 1,
        savepoint: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        max_workers = max(1, int(max_workers or 1))
        items = list(grouped_issues.items())

        def _solve_one(node_id: str, scene_issues: list[Issue]) -> tuple[str, str]:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户手动暂停")
            if skip_existing_pending and self.workspace.has_pending_modify(node_id):
                dlog(logger, f"solve skip existing pending: node_id={node_id}")
                if savepoint:
                    try:
                        savepoint(node_id, "skipped")
                    except Exception:
                        pass
                return node_id, "skipped"
            scene = scenes.get(node_id)
            if not scene:
                return node_id, "failed"
            payload = self.build_fix_payload(scene, scene_issues, characters)
            result = self.llm_gateway.call_json(
                payload=payload,
                task_id=f"solve/{node_id}",
                progress_callback=progress_callback,
            )
            parsed = result.get("result") or {}
            if not result.get("ok") or not parsed:
                dlog(logger, f"solve llm failed: node_id={node_id}, ok={result.get('ok')}")
                return node_id, "failed"
            fixed_text = str(parsed.get("fixed_text", "") or "")
            if not fixed_text.strip():
                dlog(logger, f"solve empty fixed_text: node_id={node_id}")
                return node_id, "failed"
            self.workspace.save_pending_modify(
                node_id=node_id,
                original_text=scene.content_raw,
                modified_text=fixed_text,
                request_prompt=str(payload.get("instruction", "")),
            )
            if savepoint:
                try:
                    savepoint(node_id, "ok")
                except Exception:
                    pass
            dlog(logger, f"solve success: node_id={node_id}, issues={len(scene_issues)}, fixed_len={len(fixed_text)}")
            if progress_callback:
                progress_callback(f"[solve] {scene.scene_title} 生成完成")
            return node_id, "ok"

        ok_count = 0
        fail_count = 0
        skipped_count = 0

        if max_workers <= 1 or len(items) <= 1:
            for node_id, scene_issues in items:
                _, status = _solve_one(node_id, scene_issues)
                if status == "ok":
                    ok_count += 1
                elif status == "skipped":
                    skipped_count += 1
                else:
                    fail_count += 1
        else:
            if progress_callback:
                progress_callback(f"[solve] 并发修复已启用: workers={max_workers}, tasks={len(items)}")
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(_solve_one, node_id, scene_issues): node_id for node_id, scene_issues in items}
                for fut in as_completed(futures):
                    try:
                        _node_id, status = fut.result()
                    except InterruptedError:
                        if stop_event:
                            stop_event.set()
                        for f in futures:
                            f.cancel()
                        raise
                    except Exception as e:
                        fail_count += 1
                        if progress_callback:
                            progress_callback(f"[solve] 任务异常: node_id={futures.get(fut)}, err={e}")
                        continue
                    if status == "ok":
                        ok_count += 1
                    elif status == "skipped":
                        skipped_count += 1
                    else:
                        fail_count += 1

        dlog(
            logger,
            f"solve stats: total={len(grouped_issues)}, ok={ok_count}, fail={fail_count}, skipped={skipped_count}",
        )
        return {
            "ok_count": ok_count,
            "fail_count": fail_count,
            "skipped_count": skipped_count,
            "total": len(grouped_issues),
        }
