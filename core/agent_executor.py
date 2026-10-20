"""Agent 执行器：按计划逐步执行，每一次模型请求都会进入任务队列。

执行器运行在后台线程中，只读写工作区文件与内存中的大纲副本：
* 正文生成/重写：直接写 ``正文/*.md``，并返回节点更新（md5 / file_path）供主线程落盘；
* 概要：更新节点 summary（由主线程写回 outline_tree.json）；
* 按要求改写：写入 ``pending_modifies``，交给用户在主界面 diff 后合并，绝不自动覆盖。

这样即使执行中途关闭程序，已完成的正文文件也不会丢；大纲树由主线程按节点
更新增量保存。
"""
from __future__ import annotations

import copy
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable

from core.context_builder import ContextBuilder

logger = logging.getLogger(__name__)

FAILURE_MARKER = "> **生成失败:**"


@dataclass
class NodeUpdate:
    node_id: str
    kind: str  # content | summary | pending
    content: str = ""
    summary: str = ""
    file_path: str = ""
    md5: str = ""

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "content": self.content,
            "summary": self.summary,
            "file_path": self.file_path,
            "md5": self.md5,
        }


@dataclass
class StepResult:
    index: int
    action: str
    title: str
    status: str = "pending"  # pending | running | success | failed | skipped
    total_nodes: int = 0
    ok_nodes: int = 0
    failed_nodes: int = 0
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "action": self.action,
            "title": self.title,
            "status": self.status,
            "total_nodes": self.total_nodes,
            "ok_nodes": self.ok_nodes,
            "failed_nodes": self.failed_nodes,
            "message": self.message,
        }


@dataclass
class RunResult:
    success: bool = False
    cancelled: bool = False
    error: str = ""
    steps: list[StepResult] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "cancelled": self.cancelled,
            "error": self.error,
            "steps": [step.to_dict() for step in self.steps],
            "updates": [dict(update) for update in self.updates],
        }


def _is_failure(text: str) -> bool:
    return not text or text.strip().startswith(FAILURE_MARKER)


def _strip_failure(text: str) -> str:
    return text.replace(FAILURE_MARKER, "").strip()


def find_node(tree_data: dict, node_id: str) -> dict | None:
    stack = list((tree_data or {}).get("nodes", []))
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if str(node.get("id", "")) == str(node_id):
            return node
        stack.extend(node.get("children", []) or [])
    return None


def _read_node_content(workspace, node: dict) -> str:
    rel_path = str(node.get("file_path", "") or "")
    if not rel_path:
        return ""
    full_path = workspace.text_path
    import os

    full = os.path.join(full_path, rel_path)
    if not os.path.exists(full):
        return ""
    try:
        with open(full, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def build_context_builder(workspace, llm_client) -> ContextBuilder:
    runtime = getattr(llm_client, "runtime", None)
    profile = getattr(llm_client, "profile", {}) or {}
    return ContextBuilder(
        workspace,
        model_context_size=getattr(runtime, "context_size", None),
        model_capabilities=getattr(runtime, "capabilities", None) or [],
        compression_profile=profile.get("world_context_compression_profile", "balanced"),
        children_summary_compress_trigger_ratio=profile.get(
            "children_summary_compress_trigger_ratio"
        ),
        children_summary_group_budget_ratio=profile.get(
            "children_summary_group_budget_ratio"
        ),
    )


def build_modify_prompt(
    builder: ContextBuilder,
    target_node: dict,
    checked_setting_paths: list[str],
    requirement: str,
    original_text: str,
) -> str:
    settings_text = builder._build_settings_text(checked_setting_paths)  # noqa: SLF001
    return f"""你是一个专业的小说编辑助手。请根据用户的修改要求，对提供的小说正文进行修改。

### 修改要求：
{requirement}

### 世界观与设定参考：
{settings_text}

### 原文内容：
{original_text}

### 输出要求：
1. 只输出修改后的完整文本
2. 保持原文的基本结构和格式
3. 不要添加任何额外的解释性文字
4. 如果原文有标题（# 开头），请保留
""".strip()


class AgentRunner:
    """顺序执行计划中的步骤。"""

    def __init__(
        self,
        *,
        workspace,
        tree_data: dict,
        llm_client,
        setting_paths: list[str] | None = None,
        stop_on_error: bool = True,
        progress: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_step_start: Callable[[StepResult], None] | None = None,
        on_step_finish: Callable[[StepResult], None] | None = None,
        on_update: Callable[[dict], None] | None = None,
    ):
        self.workspace = workspace
        self.llm_client = llm_client
        self.setting_paths = list(setting_paths or [])
        self.stop_on_error = bool(stop_on_error)
        self.progress = progress
        self.cancel_event = cancel_event or threading.Event()
        self.on_step_start = on_step_start
        self.on_step_finish = on_step_finish
        self.on_update = on_update
        # 独立副本：避免与 UI 线程同时修改同一棵大纲树
        self.tree = copy.deepcopy(tree_data) if isinstance(tree_data, dict) else {"nodes": []}
        self.builder = build_context_builder(workspace, llm_client)
        self.updates: list[dict] = []

    # -- 工具 ---------------------------------------------------------
    def _emit(self, message: str) -> None:
        if not message:
            return
        logger.info("[Agent] %s", message)
        if callable(self.progress):
            try:
                self.progress(message)
            except Exception:  # pragma: no cover - 回调异常不影响执行
                logger.debug("agent progress callback failed", exc_info=True)

    def _cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def _record_update(self, update: NodeUpdate) -> None:
        data = update.to_dict()
        self.updates.append(data)
        if callable(self.on_update):
            try:
                self.on_update(data)
            except Exception:  # pragma: no cover
                logger.debug("on_update 回调失败", exc_info=True)

    # -- 主流程 -------------------------------------------------------
    def run(self, steps: list[dict], action_id: str = "") -> RunResult:
        result = RunResult()
        if not isinstance(steps, list) or not steps:
            result.error = "计划里没有可执行的步骤"
            return result

        total = len(steps)
        for position, raw_step in enumerate(steps, start=1):
            step = raw_step if isinstance(raw_step, dict) else {}
            step_result = StepResult(
                index=int(step.get("index") or position),
                action=str(step.get("action", "")),
                title=str(step.get("title", "")),
                total_nodes=len(step.get("node_ids", []) or []),
                status="running",
            )
            result.steps.append(step_result)
            if self._cancelled():
                step_result.status = "skipped"
                step_result.message = "已取消"
                result.cancelled = True
                break
            if callable(self.on_step_start):
                try:
                    self.on_step_start(step_result)
                except Exception:  # pragma: no cover
                    logger.debug("on_step_start 回调失败", exc_info=True)
            self._emit(
                f"[{position}/{total}] 开始：{step.get('action_label', step_result.action)}"
                f" · {step_result.title}（{step_result.total_nodes} 个场景）"
            )

            step_error = ""
            for node_position, node_id in enumerate(step.get("node_ids", []) or [], start=1):
                if self._cancelled():
                    result.cancelled = True
                    break
                node = find_node(self.tree, node_id)
                node_title = (
                    node.get("title", node_id) if isinstance(node, dict) else str(node_id)
                )
                if node is None:
                    step_result.failed_nodes += 1
                    step_error = f"找不到节点 {node_id}"
                    self._emit(f"  ✗ {node_title}：{step_error}")
                    if self.stop_on_error:
                        break
                    continue

                self._emit(
                    f"  → {node_title}（{node_position}/{step_result.total_nodes}）"
                    f" · {step.get('action_label', step_result.action)}"
                )
                ok, message = self._execute_node(
                    step, node, action_id=action_id
                )
                if ok:
                    step_result.ok_nodes += 1
                else:
                    step_result.failed_nodes += 1
                    step_error = message
                    self._emit(f"  ✗ {node_title}：{message}")
                    if self.stop_on_error:
                        break

            if result.cancelled:
                step_result.status = "skipped"
                step_result.message = "已取消"
                break

            if step_result.failed_nodes:
                step_result.status = "failed"
                step_result.message = step_error or f"{step_result.failed_nodes} 个场景失败"
            else:
                step_result.status = "success"
                step_result.message = f"{step_result.ok_nodes} 个场景完成"
            self._emit(
                f"[{position}/{total}] 结束：{step_result.status} · {step_result.message}"
            )
            if callable(self.on_step_finish):
                try:
                    self.on_step_finish(step_result)
                except Exception:  # pragma: no cover
                    logger.debug("on_step_finish 回调失败", exc_info=True)
            if step_result.status == "failed" and self.stop_on_error:
                result.error = step_result.message
                break

        if result.cancelled:
            result.error = "用户已停止执行"
        elif result.error:
            pass
        else:
            failed_steps = [step for step in result.steps if step.status == "failed"]
            result.success = not failed_steps
            if failed_steps:
                result.error = "部分步骤失败"
        result.updates = [update for update in self.updates]
        return result

    # -- 单节点执行 ---------------------------------------------------
    def _execute_node(
        self, step: dict, node: dict, *, action_id: str
    ) -> tuple[bool, str]:
        action = str(step.get("action", ""))
        params = step.get("params") or {}
        node_id = str(node.get("id", ""))
        node_title = str(node.get("title", "") or node_id)
        pending = self.workspace.has_pending_modify(node_id)
        if pending:
            return False, "存在待合并修改，请先合并"

        try:
            if action == "generate":
                return self._generate(step, node, params, action_id)
            if action == "rewrite":
                return self._rewrite(step, node, params, action_id)
            if action == "summary":
                return self._summary(step, node, action_id)
            if action == "modify":
                return self._modify(step, node, params, action_id)
        except Exception as exc:  # noqa: BLE001 - 单节点失败不应中断整个线程
            logger.exception("Agent 执行节点失败: %s", node_title)
            return False, f"执行异常：{exc}"
        return False, f"未知动作：{action}"

    def _call_llm(
        self,
        prompt: str,
        *,
        system_instruction: str | None,
        label: str,
        node: dict,
        action_id: str,
    ) -> str:
        return self.llm_client.generate_text(
            prompt,
            override_system_instruction=system_instruction,
            progress_callback=self._emit,
            task_meta={
                "action_id": action_id,
                "label": label,
                "node_id": str(node.get("id", "")),
                "node_title": str(node.get("title", "")),
            },
        )

    def _generate(
        self, step: dict, node: dict, params: dict, action_id: str
    ) -> tuple[bool, str]:
        word_count = int(params.get("word_count", 5000) or 5000)
        include_next = bool(params.get("include_next", True))
        messages = self.builder.build_generation_prompt(
            node,
            self.tree,
            self.setting_paths,
            generate_image=False,
            word_count=word_count,
            include_next=include_next,
        )
        raw = self._call_llm(
            messages[-1]["content"],
            system_instruction=None,
            label="生成正文",
            node=node,
            action_id=action_id,
        )
        if _is_failure(raw):
            return False, _strip_failure(raw) or "生成失败"
        content = raw.strip()
        if not content:
            return False, "模型返回为空"
        rel_path = str(node.get("file_path", "") or "").strip()
        if not rel_path:
            rel_path = f"场景_{uuid.uuid4().hex[:8]}.md"
        md5 = self.workspace.save_markdown_file(rel_path, content)
        node["file_path"] = rel_path
        node["md5"] = md5
        node["_status"] = "ok"
        self._record_update(
            NodeUpdate(
                node_id=str(node.get("id", "")),
                kind="content",
                content=content,
                file_path=rel_path,
                md5=md5,
            )
        )
        return True, f"已写入 {len(content)} 字"

    def _rewrite(
        self, step: dict, node: dict, params: dict, action_id: str
    ) -> tuple[bool, str]:
        original = _read_node_content(self.workspace, node)
        if not original.strip():
            return False, "该节点没有正文可重写"
        word_count = int(params.get("word_count", 3000) or 3000)
        messages = self.builder.build_rewrite_prompt(
            node, self.tree, self.setting_paths, word_count
        )
        raw = self._call_llm(
            messages[-1]["content"],
            system_instruction=None,
            label="重写正文",
            node=node,
            action_id=action_id,
        )
        if _is_failure(raw):
            return False, _strip_failure(raw) or "重写失败"
        content = raw.strip()
        if not content:
            return False, "模型返回为空"
        rel_path = str(node.get("file_path", "") or "").strip()
        if not rel_path:
            rel_path = f"场景_{uuid.uuid4().hex[:8]}.md"
        md5 = self.workspace.save_markdown_file(rel_path, content)
        node["file_path"] = rel_path
        node["md5"] = md5
        node["_status"] = "ok"
        self._record_update(
            NodeUpdate(
                node_id=str(node.get("id", "")),
                kind="content",
                content=content,
                file_path=rel_path,
                md5=md5,
            )
        )
        return True, f"重写为 {len(content)} 字"

    def _summary(
        self, step: dict, node: dict, action_id: str
    ) -> tuple[bool, str]:
        messages = self.builder.build_summary_prompt(
            node,
            self.tree,
            self.setting_paths,
            llm_client=self.llm_client,
            progress_callback=self._emit,
        )
        raw = self._call_llm(
            messages[-1]["content"],
            system_instruction=getattr(
                self.llm_client, "summary_system_instruction", None
            ),
            label="生成概要",
            node=node,
            action_id=action_id,
        )
        if _is_failure(raw):
            return False, _strip_failure(raw) or "概要生成失败"
        summary = raw.strip()
        if not summary:
            return False, "模型返回为空"
        node["summary"] = summary
        self._record_update(
            NodeUpdate(
                node_id=str(node.get("id", "")),
                kind="summary",
                summary=summary,
            )
        )
        return True, f"概要 {len(summary)} 字"

    def _modify(
        self, step: dict, node: dict, params: dict, action_id: str
    ) -> tuple[bool, str]:
        requirement = str(params.get("requirement", "") or "").strip()
        if not requirement:
            return False, "缺少修改要求"
        original = _read_node_content(self.workspace, node)
        if not original.strip():
            return False, "该节点没有正文可修改"
        prompt = build_modify_prompt(
            self.builder, node, self.setting_paths, requirement, original
        )
        raw = self._call_llm(
            prompt,
            system_instruction=getattr(
                self.llm_client, "modify_system_instruction", None
            ),
            label="按要求改写",
            node=node,
            action_id=action_id,
        )
        if _is_failure(raw):
            return False, _strip_failure(raw) or "改写失败"
        content = raw.strip()
        if not content:
            return False, "模型返回为空"
        node_id = str(node.get("id", ""))
        self.workspace.save_pending_modify(node_id, original, content, requirement)
        self._record_update(
            NodeUpdate(
                node_id=node_id,
                kind="pending",
                content=content,
            )
        )
        return True, "已生成待合并修改"
