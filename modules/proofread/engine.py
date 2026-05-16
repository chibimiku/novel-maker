from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
import traceback
import uuid
from datetime import datetime
from typing import Any

from core.workspace_manager import WorkspaceManager

from .cache_manager import ProofreadCacheManager
from .checkpoint_manager import ProofreadCheckpointManager
from .debug_log import dlog, is_debug_enabled
from .issue_aggregator import IssueAggregator
from .llm_gateway import ProofreadLLMGateway
from .loader import ProofreadDataLoader
from .proofread_config import ProofreadConfig
from .solve_engine import SolveEngine
from .types import Issue, IssueLocation, SceneMeta

logger = logging.getLogger(__name__)


class ProofreadEngine:
    def __init__(
        self,
        workspace: WorkspaceManager,
        llm_gateway: ProofreadLLMGateway,
        cache_manager: ProofreadCacheManager | None = None,
        checkpoint_manager: ProofreadCheckpointManager | None = None,
        config: dict[str, Any] | None = None,
        pass_instances: list[Any] | None = None,
    ):
        self.workspace = workspace
        self.llm_gateway = llm_gateway
        self.cache_manager = cache_manager or ProofreadCacheManager(workspace.workspace_path)
        self.checkpoint_manager = checkpoint_manager or ProofreadCheckpointManager(workspace.workspace_path)
        self.config = config or {}
        self.pass_instances = pass_instances or []
        self.loader = ProofreadDataLoader(workspace)
        self.aggregator = IssueAggregator()
        self.solve_engine = SolveEngine(workspace=workspace, llm_gateway=llm_gateway)

    @classmethod
    def from_llm_client(
        cls,
        workspace: WorkspaceManager,
        llm_client: Any,
        config: dict[str, Any] | None = None,
        pass_instances: list[Any] | None = None,
    ) -> "ProofreadEngine":
        return cls(
            workspace=workspace,
            llm_gateway=ProofreadLLMGateway(llm_client=llm_client),
            config=config,
            pass_instances=pass_instances,
        )

    def run_find(self, stop_event=None, progress_callback=None) -> list[Issue]:
        return self._run_find_internal(
            resume=False,
            stop_event=stop_event,
            progress_callback=progress_callback,
            include_chapters=None,
            scene_title_keywords=None,
        )

    def resume_find(self, stop_event=None, progress_callback=None) -> list[Issue]:
        return self._run_find_internal(
            resume=True,
            stop_event=stop_event,
            progress_callback=progress_callback,
            include_chapters=None,
            scene_title_keywords=None,
        )

    def run_find_scoped(
        self,
        include_chapters: set[str] | None = None,
        scene_title_keywords: list[str] | None = None,
        stop_event=None,
        progress_callback=None,
    ) -> list[Issue]:
        return self._run_find_internal(
            resume=False,
            stop_event=stop_event,
            progress_callback=progress_callback,
            include_chapters=include_chapters,
            scene_title_keywords=scene_title_keywords,
        )

    def resume_find_scoped(
        self,
        include_chapters: set[str] | None = None,
        scene_title_keywords: list[str] | None = None,
        stop_event=None,
        progress_callback=None,
    ) -> list[Issue]:
        return self._run_find_internal(
            resume=True,
            stop_event=stop_event,
            progress_callback=progress_callback,
            include_chapters=include_chapters,
            scene_title_keywords=scene_title_keywords,
        )

    def _run_find_internal(
        self,
        resume: bool,
        stop_event=None,
        progress_callback=None,
        include_chapters: set[str] | None = None,
        scene_title_keywords: list[str] | None = None,
    ) -> list[Issue]:
        self.llm_gateway.default_progress_callback = progress_callback
        prev_on_success = getattr(self.llm_gateway, "on_call_success", None)
        try:
            cfg = ProofreadConfig.from_dict(self.config)
            self.llm_gateway.max_retry = cfg.max_retry
            self.llm_gateway.backoff_sec = cfg.backoff_sec

            scope = {
                "include_chapters": sorted(include_chapters) if include_chapters else [],
                "scene_title_keywords": [str(x) for x in (scene_title_keywords or []) if str(x).strip()],
            }

            if progress_callback:
                progress_callback("[io] 开始加载工作区数据...")
            data = self.loader.load_workspace_data(
                include_chapters=include_chapters,
                scene_title_keywords=scene_title_keywords,
            )
            scene_list: list[SceneMeta] = data["scene_list"]
            pass_names = [p.pass_name for p in self.pass_instances]
            md5_snapshot = {s.node_id: s.content_md5 for s in scene_list}

            if progress_callback:
                progress_callback(
                    f"[io] 工作区数据加载完成: scenes={len(scene_list)}, passes={len(self.pass_instances)}, sys_data={self.workspace.sys_data_path}"
                )
                if scope["include_chapters"] or scope["scene_title_keywords"]:
                    progress_callback(f"[find] 查找范围: chapters={scope['include_chapters']}, keywords={scope['scene_title_keywords']}")

            run_state = self.checkpoint_manager.load_run_state() if resume else None
            if run_state and run_state.get("phase") == "find":
                prev_scope = run_state.get("scope", {})
                if prev_scope != scope:
                    if progress_callback:
                        progress_callback("[find] 检测到查找范围变更，将重新初始化检查点。")
                    run_state = None
            if not run_state or run_state.get("phase") != "find":
                run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
                if progress_callback:
                    progress_callback(f"[io] 初始化检查点: phase=find, run_id={run_id}")
                run_state = self.checkpoint_manager.init_run(
                    phase="find",
                    run_id=run_id,
                    md5_snapshot=md5_snapshot,
                    pass_names=pass_names,
                )
                run_state["scope"] = scope
                self.checkpoint_manager.save_run_state(run_state)
            else:
                old_snapshot = run_state.get("md5_snapshot", {})
                changed_scene_ids = {sid for sid, md5 in md5_snapshot.items() if old_snapshot.get(sid) != md5}
                run_state["md5_snapshot"] = md5_snapshot
                run_state["scope"] = scope
                if changed_scene_ids:
                    if progress_callback:
                        progress_callback(f"[io] 检测到正文变更: scenes_changed={len(changed_scene_ids)}，将重跑所有 Pass")
                    for p in pass_names:
                        run_state["passes"][p]["status"] = "pending"
                    self.checkpoint_manager.save_run_state(run_state)

            pass_issue_map: dict[str, list[Issue]] = {}
            def _savepoint(pass_name: str, scene_id: str, is_complete: bool = True) -> None:
                """由 Pass 或 gateway 回调调用，保存单个场景完成状态"""
                run_state["passes"][pass_name]["scenes_completed"] = (
                    run_state["passes"].get(pass_name, {}).get("scenes_completed", 0)
                )
                if is_complete:
                    run_state["passes"][pass_name]["scenes_completed"] += 1
                    self.checkpoint_manager.save_task_state(
                        pass_name,
                        scene_id,
                        {
                            "status": "completed",
                            "attempts": 1,
                            "completed_at": datetime.now().isoformat(timespec="seconds"),
                        },
                    )
                    self.checkpoint_manager.save_run_state(run_state)

            shared_data = {
                "characters": data["characters"],
                "locations": data["locations"],
                "character_alias_map": data["character_alias_map"],
                "workspace_path": self.workspace.workspace_path,
                "llm_gateway": self.llm_gateway,
                "proofread_config": cfg,
                "debug_log_enabled": is_debug_enabled(),
                "savepoint": _savepoint,
            }

            def _build_scene_issues(scene: SceneMeta, issue_items: list[dict[str, Any]], pass_name: str) -> list[Issue]:
                out: list[Issue] = []
                for idx, item in enumerate(issue_items, start=1):
                    if not isinstance(item, dict):
                        continue
                    loc = item.get("location", {}) or {}
                    out.append(Issue(
                        issue_id=str(item.get("issue_id", f"{pass_name}-{scene.node_id[:6]}-{idx}")),
                        category=str(item.get("category", pass_name)),
                        pass_name=pass_name,
                        severity=str(item.get("severity", "warning")),
                        title=str(item.get("title", "")),
                        description=str(item.get("description", "")),
                        location=IssueLocation(
                            node_id=scene.node_id,
                            chapter_title=scene.chapter_title,
                            section_title=scene.section_title,
                            scene_title=scene.scene_title,
                            file_path=scene.file_path,
                            line_numbers=[int(x) for x in (loc.get("line_numbers", []) or []) if str(x).isdigit()],
                        ),
                        suggestion=str(item.get("suggestion", "")),
                        evidence=[str(x) for x in (item.get("evidence", []) or [])],
                    ))
                return out

            def _gateway_savepoint(task_id: str, parsed_result: dict[str, Any], raw_text: str) -> None:
                try:
                    parts = str(task_id).split("/")
                    if len(parts) < 2:
                        return
                    pname = parts[0]
                    sid = parts[1]
                    if sid.startswith("batch_"):
                        return
                    _savepoint(pname, sid, is_complete=True)
                    self.checkpoint_manager.save_llm_response(pname, sid, {
                        "raw_text": (raw_text or ""),
                        "parsed": parsed_result,
                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    issue_items = parsed_result.get("issues")
                    if isinstance(issue_items, list) and issue_items:
                        scene = {s.node_id: s for s in scene_list}.get(sid)
                        if scene:
                            built = _build_scene_issues(scene, issue_items, pname)
                            if built:
                                self.checkpoint_manager.append_pass_issues(pname, [i.to_dict() for i in built])
                                pass_issue_map.setdefault(pname, [])
                                for i in built:
                                    pass_issue_map[pname].append(i)
                    intermediate = parsed_result.get("intermediate_data")
                    if isinstance(intermediate, dict) and intermediate:
                        scene = {s.node_id: s for s in scene_list}.get(sid)
                        if scene:
                            self.cache_manager.save_scene_pass_data(sid, pname, intermediate)
                            self.cache_manager.mark_scene_pass_cached(sid, scene.content_md5, pname)
                except Exception:
                    pass

            self.llm_gateway.on_call_success = _gateway_savepoint
            dlog(
                logger,
                f"run_find start: resume={resume}, scenes={len(scene_list)}, passes={len(self.pass_instances)}",
                shared_data,
            )

            pending_passes: list[Any] = []
            pass_pending_scenes: dict[str, list[SceneMeta]] = {}
            pass_completed_count: dict[str, int] = {}
            for p in self.pass_instances:
                pass_state = run_state["passes"].setdefault(p.pass_name, {})
                if pass_state.get("status") == "completed":
                    task_data = self.checkpoint_manager.load_pass_tasks(p.pass_name)
                    loaded_issues = [
                        build_issue_from_dict(x) for x in task_data.get("issues", []) if isinstance(x, dict)
                    ]
                    pass_issue_map[p.pass_name] = loaded_issues
                    if progress_callback:
                        progress_callback(f"[find] 跳过已完成 Pass: {p.pass_name}（issues={len(loaded_issues)}）")
                    dlog(
                        logger,
                        f"run_find skip completed pass: {p.pass_name}, loaded_issues={len(loaded_issues)}",
                        shared_data,
                    )
                    continue
                pending_passes.append(p)
                completed_ids = self.checkpoint_manager.get_completed_scene_ids(p.pass_name)
                pass_completed_count[p.pass_name] = len(completed_ids)
                if completed_ids:
                    pending = [s for s in scene_list if s.node_id not in completed_ids]
                    pass_pending_scenes[p.pass_name] = pending
                    if not pending:
                        task_data = self.checkpoint_manager.load_pass_tasks(p.pass_name)
                        loaded_issues = [
                            build_issue_from_dict(x) for x in task_data.get("issues", []) if isinstance(x, dict)
                        ]
                        pass_issue_map[p.pass_name] = loaded_issues
                        run_state["passes"][p.pass_name]["status"] = "completed"
                        run_state["passes"][p.pass_name]["scenes_completed"] = len(scene_list)
                    if progress_callback:
                        progress_callback(
                            f"[find] Pass {p.pass_name} 从检查点恢复: 已完成={len(completed_ids)}, 待处理={len(pending)}"
                        )
                else:
                    pass_pending_scenes[p.pass_name] = list(scene_list)

            def _run_one_pass(pass_instance: Any, target_scenes: list[SceneMeta]) -> tuple[str, list[Issue]]:
                pass_name = str(getattr(pass_instance, "pass_name", "") or "")
                if stop_event and stop_event.is_set():
                    raise InterruptedError("用户手动暂停")
                issues, intermediates = pass_instance.run(
                    scene_list=target_scenes,
                    shared_data=shared_data,
                    stop_event=stop_event,
                    progress_callback=progress_callback,
                )
                if progress_callback:
                    progress_callback(f"[io] 保存 Pass issues: {pass_name}")
                self.checkpoint_manager.append_pass_issues(pass_name, [i.to_dict() for i in issues])
                if intermediates:
                    if progress_callback:
                        progress_callback(f"[io] 写入缓存与检查点: pass={pass_name}, scenes={len(intermediates)}")
                    scene_by_id = {s.node_id: s for s in scene_list}
                    for scene_id, payload in intermediates.items():
                        scene = scene_by_id.get(scene_id)
                        if scene is None:
                            continue
                        self.cache_manager.save_scene_pass_data(scene_id, pass_name, payload)
                        self.cache_manager.mark_scene_pass_cached(scene_id, scene.content_md5, pass_name)
                        self.checkpoint_manager.save_task_state(
                            pass_name,
                            scene_id,
                            {
                                "status": "completed",
                                "attempts": 1,
                                "completed_at": datetime.now().isoformat(timespec="seconds"),
                            },
                        )
                return pass_name, issues

            parallel_passes = max(1, int(cfg.parallel_passes or 1))
            if parallel_passes <= 1 or len(pending_passes) <= 1:
                for p in pending_passes:
                    target = pass_pending_scenes.get(p.pass_name, scene_list)
                    if not target:
                        continue
                    if stop_event and stop_event.is_set():
                        run_state["status"] = "paused"
                        self.checkpoint_manager.save_run_state(run_state)
                        raise InterruptedError("用户手动暂停")
                    if progress_callback:
                        total = len(target)
                        already = pass_completed_count.get(p.pass_name, 0)
                        if already:
                            progress_callback(f"[find] 开始 Pass(增量): {p.pass_name}（已完成={already}, 待处理={total}）")
                        else:
                            progress_callback(f"[find] 开始 Pass: {p.pass_name}（scenes={total}）")
                    run_state["passes"][p.pass_name]["status"] = "running"
                    run_state["passes"][p.pass_name]["scenes_total"] = len(scene_list)
                    run_state["passes"][p.pass_name]["scenes_completed"] = pass_completed_count.get(p.pass_name, 0)
                    self.checkpoint_manager.save_run_state(run_state)
                    pass_name, issues = _run_one_pass(p, target)
                    prev_issues = pass_issue_map.get(pass_name, [])
                    pass_issue_map[pass_name] = prev_issues + issues
                    if progress_callback:
                        progress_callback(
                            f"[find] Pass 完成: {pass_name}（本轮新增 issues={len(issues)}, 累计 issues={len(pass_issue_map[pass_name])}）"
                        )
                    run_state["passes"][pass_name]["status"] = "completed"
                    run_state["passes"][pass_name]["scenes_completed"] = len(scene_list)
                    self.checkpoint_manager.save_run_state(run_state)
            else:
                if progress_callback:
                    progress_callback(f"[find] 并发 Pass 已启用: workers={parallel_passes}, pending={len(pending_passes)}")
                futures = {}
                with ThreadPoolExecutor(max_workers=parallel_passes) as ex:
                    for p in pending_passes:
                        target = pass_pending_scenes.get(p.pass_name, scene_list)
                        if not target:
                            continue
                        if stop_event and stop_event.is_set():
                            run_state["status"] = "paused"
                            self.checkpoint_manager.save_run_state(run_state)
                            raise InterruptedError("用户手动暂停")
                        total = len(target)
                        already = pass_completed_count.get(p.pass_name, 0)
                        if progress_callback:
                            if already:
                                progress_callback(f"[find] 开始 Pass(增量): {p.pass_name}（已完成={already}, 待处理={total}）")
                            else:
                                progress_callback(f"[find] 开始 Pass: {p.pass_name}（scenes={total}）")
                        run_state["passes"][p.pass_name]["status"] = "running"
                        run_state["passes"][p.pass_name]["scenes_total"] = len(scene_list)
                        run_state["passes"][p.pass_name]["scenes_completed"] = pass_completed_count.get(p.pass_name, 0)
                        self.checkpoint_manager.save_run_state(run_state)
                        fut = ex.submit(_run_one_pass, p, target)
                        futures[fut] = p.pass_name

                    for fut in as_completed(futures):
                        pass_name = futures.get(fut, "")
                        try:
                            name, issues = fut.result()
                            prev_issues = pass_issue_map.get(name, [])
                            pass_issue_map[name] = prev_issues + issues
                            if progress_callback:
                                progress_callback(
                                    f"[find] Pass 完成: {name}（本轮新增 issues={len(issues)}, 累计 issues={len(pass_issue_map[name])}）"
                                )
                            run_state["passes"][name]["status"] = "completed"
                            run_state["passes"][name]["scenes_completed"] = len(scene_list)
                            self.checkpoint_manager.save_run_state(run_state)
                        except InterruptedError:
                            if stop_event:
                                stop_event.set()
                            run_state["status"] = "paused"
                            self.checkpoint_manager.save_run_state(run_state)
                            for f in futures:
                                f.cancel()
                            raise
                        except Exception:
                            if progress_callback:
                                progress_callback(f"[find] Pass 异常: {pass_name}\n{traceback.format_exc()}")
                            run_state["passes"][pass_name]["status"] = "failed"
                            self.checkpoint_manager.save_run_state(run_state)
                            pass_issue_map[pass_name] = pass_issue_map.get(pass_name, [])

            issues = self.aggregator.aggregate(pass_issue_map)
            out_file = os.path.join(self.workspace.sys_data_path, "proofread_issues.json")
            if progress_callback:
                progress_callback(f"[io] 写入问题清单: {out_file}（issues={len(issues)}）")
            self.aggregator.save_issues(issues, out_file)
            dlog(logger, f"run_find completed: total_issues={len(issues)}, out={out_file}", shared_data)
            run_state["status"] = "completed"
            self.checkpoint_manager.save_run_state(run_state)
            if progress_callback:
                progress_callback("[find] 全部完成")
            return issues
        finally:
            self.llm_gateway.on_call_success = prev_on_success
            self.llm_gateway.default_progress_callback = None

    def run_solve(
        self,
        issues: list[Issue],
        stop_event=None,
        progress_callback=None,
        include_categories: set[str] | None = None,
        include_chapters: set[str] | None = None,
        skip_existing_pending: bool = True,
    ) -> dict[str, Any]:
        return self._run_solve_internal(
            resume=False,
            issues=issues,
            stop_event=stop_event,
            progress_callback=progress_callback,
            include_categories=include_categories,
            include_chapters=include_chapters,
            skip_existing_pending=skip_existing_pending,
        )

    def resume_solve(
        self,
        issues: list[Issue],
        stop_event=None,
        progress_callback=None,
        include_categories: set[str] | None = None,
        include_chapters: set[str] | None = None,
        skip_existing_pending: bool = True,
    ) -> dict[str, Any]:
        return self._run_solve_internal(
            resume=True,
            issues=issues,
            stop_event=stop_event,
            progress_callback=progress_callback,
            include_categories=include_categories,
            include_chapters=include_chapters,
            skip_existing_pending=skip_existing_pending,
        )

    def _run_solve_internal(
        self,
        resume: bool,
        issues: list[Issue],
        stop_event=None,
        progress_callback=None,
        include_categories: set[str] | None = None,
        include_chapters: set[str] | None = None,
        skip_existing_pending: bool = True,
    ) -> dict[str, Any]:
        self.llm_gateway.default_progress_callback = progress_callback
        prev_on_success = getattr(self.llm_gateway, "on_call_success", None)
        try:
            cfg = ProofreadConfig.from_dict(self.config)
            self.llm_gateway.max_retry = cfg.max_retry
            self.llm_gateway.backoff_sec = cfg.backoff_sec

            if progress_callback:
                progress_callback("[io] 开始加载工作区数据...")
            data = self.loader.load_workspace_data()
            scenes: dict[str, SceneMeta] = {s.node_id: s for s in data["scene_list"]}
            grouped = self.solve_engine.group_issues_by_scene(
                issues=issues,
                include_categories=include_categories,
                include_chapters=include_chapters,
            )
            if progress_callback:
                progress_callback(
                    f"[io] 工作区数据加载完成: scenes={len(scenes)}, grouped_scenes={len(grouped)}, sys_data={self.workspace.sys_data_path}"
                )

            run_state = self.checkpoint_manager.load_run_state() if resume else None
            if not run_state or run_state.get("phase") != "solve":
                run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
                if progress_callback:
                    progress_callback(f"[io] 初始化检查点: phase=solve, run_id={run_id}")
                run_state = self.checkpoint_manager.init_run(
                    phase="solve",
                    run_id=run_id,
                    md5_snapshot={sid: s.content_md5 for sid, s in scenes.items()},
                    pass_names=["solve_scenes"],
                )
            run_state["passes"]["solve_scenes"]["status"] = "running"
            run_state["passes"]["solve_scenes"]["scenes_total"] = len(grouped)
            self.checkpoint_manager.save_run_state(run_state)
            dlog(
                logger,
                f"run_solve start: resume={resume}, grouped_scenes={len(grouped)}, categories={include_categories}, chapters={include_chapters}",
            )

            filtered_grouped: dict[str, list[Issue]] = {}
            pre_completed = 0
            for node_id, scene_issues in grouped.items():
                if stop_event and stop_event.is_set():
                    run_state["status"] = "paused"
                    self.checkpoint_manager.save_run_state(run_state)
                    raise InterruptedError("用户手动暂停")
                if resume:
                    task_file = self.checkpoint_manager.load_pass_tasks("solve_scenes")
                    state = task_file.get("scenes", {}).get(node_id, {})
                    if state.get("status") == "completed":
                        dlog(logger, f"run_solve resume skip completed scene: node_id={node_id}")
                        pre_completed += 1
                        continue
                filtered_grouped[node_id] = scene_issues

            run_state["passes"]["solve_scenes"]["scenes_completed"] = pre_completed
            run_state["passes"]["solve_scenes"]["scenes_total"] = len(grouped)
            self.checkpoint_manager.save_run_state(run_state)

            def _solve_savepoint(node_id: str, status: str) -> None:
                """每个场景修理完成后立即保存进度"""
                self.checkpoint_manager.save_task_state(
                    "solve_scenes",
                    node_id,
                    {
                        "status": "completed" if status in ("ok", "skipped") else status,
                        "attempts": 1,
                        "completed_at": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                current = run_state["passes"]["solve_scenes"]["scenes_completed"]
                run_state["passes"]["solve_scenes"]["scenes_completed"] = current + 1
                self.checkpoint_manager.save_run_state(run_state)

            def _solve_gateway_savepoint(task_id: str, parsed_result: dict[str, Any], raw_text: str) -> None:
                try:
                    fixed = parsed_result.get("fixed_text", "")
                    self.checkpoint_manager.save_llm_response("solve_scenes", task_id, {
                        "raw_text": (raw_text or ""),
                        "parsed": parsed_result,
                        "fixed_text_preview": (fixed or "")[:500] if fixed else "",
                        "fixed_text_len": len(str(fixed or "")),
                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                    })
                except Exception:
                    pass

            self.llm_gateway.on_call_success = _solve_gateway_savepoint

            if progress_callback:
                progress_callback(
                    f"[solve] 开始执行: scenes={len(filtered_grouped)}, pre_completed={pre_completed}, skip_existing_pending={bool(skip_existing_pending)}, categories={include_categories}, chapters={include_chapters}"
                )
            stats = self.solve_engine.run_solve(
                scenes=scenes,
                grouped_issues=filtered_grouped,
                characters=data["characters"],
                stop_event=stop_event,
                progress_callback=progress_callback,
                skip_existing_pending=skip_existing_pending,
                max_workers=cfg.parallel_solves,
                savepoint=_solve_savepoint,
            )

            if progress_callback:
                progress_callback("[io] 写入 solve 检查点...")
            run_state["passes"]["solve_scenes"]["status"] = "completed"
            run_state["passes"]["solve_scenes"]["scenes_completed"] = pre_completed + stats.get("ok_count", 0) + stats.get("skipped_count", 0)
            run_state["passes"]["solve_scenes"]["scenes_failed"] = stats.get("fail_count", 0)
            run_state["status"] = "completed"
            self.checkpoint_manager.save_run_state(run_state)
            dlog(logger, f"run_solve completed: stats={stats}, pre_completed={pre_completed}")
            if progress_callback:
                progress_callback("[solve] 全部完成")
            return stats
        finally:
            self.llm_gateway.on_call_success = prev_on_success
            self.llm_gateway.default_progress_callback = None


def build_issue_from_dict(data: dict[str, Any]) -> Issue:
    loc = data.get("location", {}) or {}
    return Issue(
        issue_id=str(data.get("issue_id", "")),
        category=str(data.get("category", "")),
        pass_name=str(data.get("pass_name", "")),
        severity=str(data.get("severity", "warning")),
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        location=IssueLocation(
            node_id=str(loc.get("node_id", "")),
            chapter_title=str(loc.get("chapter_title", "")),
            section_title=str(loc.get("section_title", "")),
            scene_title=str(loc.get("scene_title", "")),
            file_path=str(loc.get("file_path", "")),
            line_numbers=[int(x) for x in loc.get("line_numbers", []) if str(x).isdigit()],
        ),
        suggestion=str(data.get("suggestion", "")),
        evidence=[str(x) for x in data.get("evidence", [])],
        ignored=bool(data.get("ignored", False)),
    )
