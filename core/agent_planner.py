"""Agent 规划器：把自然语言命令转换成可执行的结构化步骤计划。

设计目标与 DSH 类似：先“规划”，让用户看到要做什么，再逐步执行。规划只负责
产出步骤（动作 + 作用范围 + 参数），不直接改文件；真正的执行交给
:mod:`core.agent_executor`，每次模型请求都会落到任务队列里。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: 支持的动作及其中文名
ACTION_LABELS = {
    "generate": "生成正文",
    "rewrite": "重写正文",
    "summary": "生成概要",
    "modify": "按要求改写",
}

SCOPE_LABELS = {
    "node": "指定场景",
    "chapter": "整章",
    "section": "整节",
    "missing": "缺失正文的场景",
    "checked": "当前勾选场景",
    "all": "全书所有场景",
}

MAX_STEPS = 30
MAX_OUTLINE_CHARS = 24000


# ---------------------------------------------------------------------------
# 大纲工具
# ---------------------------------------------------------------------------

def iter_nodes(nodes: list, level: int = 1):
    """深度优先遍历节点，产出 ``(node, level)``。"""
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        yield node, level
        yield from iter_nodes(node.get("children", []) or [], level + 1)


def find_node_with_level(tree_data: dict, node_id: str) -> tuple[dict | None, int]:
    for node, level in iter_nodes((tree_data or {}).get("nodes", [])):
        if str(node.get("id", "")) == str(node_id):
            return node, level
    return None, 0


def level3_descendants(node: dict, level: int) -> list[dict]:
    result: list[dict] = []
    for child, child_level in iter_nodes(node.get("children", []) or [], level + 1):
        if child_level == 3:
            result.append(child)
    return result


def all_level3_nodes(tree_data: dict) -> list[dict]:
    return [node for node, level in iter_nodes((tree_data or {}).get("nodes", [])) if level == 3]


def is_missing(node: dict) -> bool:
    status = str(node.get("_status", "") or "")
    if status == "missing":
        return True
    # 没有实体文件路径也视为缺失
    return not str(node.get("file_path", "") or "").strip()


def missing_level3_nodes(tree_data: dict, scope_node: dict | None = None) -> list[dict]:
    if scope_node is None:
        candidates = all_level3_nodes(tree_data)
    else:
        scope_id = str(scope_node.get("id", "") or "")
        candidates = []
        for node, node_level in iter_nodes((tree_data or {}).get("nodes", [])):
            matched = (
                node is scope_node
                or (scope_id and str(node.get("id", "")) == scope_id)
            )
            if matched:
                candidates = level3_descendants(node, node_level)
                break
    return [node for node in candidates if is_missing(node)]


def _node_status_label(node: dict) -> str:
    status = str(node.get("_status", "") or "")
    if status == "missing" or is_missing(node):
        return "缺失正文"
    if status == "modified_externally":
        return "正文被外部修改"
    summary = str(node.get("summary", "") or "")
    if summary:
        return f"有正文 / 概要 {len(summary)} 字"
    return "有正文"


def build_outline_text(tree_data: dict, max_chars: int = MAX_OUTLINE_CHARS) -> str:
    """把大纲压成便于规划器阅读的缩进清单。"""
    lines: list[str] = []
    total = 0
    truncated = False

    for node, level in iter_nodes((tree_data or {}).get("nodes", [])):
        node_id = str(node.get("id", "") or "")
        title = str(node.get("title", "") or "未命名")
        if level == 3:
            line = f"{'  ' * (level - 1)}- [L3] {title} ({node_id}) [{_node_status_label(node)}]"
        else:
            line = f"{'  ' * (level - 1)}- [L{level}] {title} ({node_id})"
        if total + len(line) + 1 > max_chars:
            truncated = True
            break
        lines.append(line)
        total += len(line) + 1

    if truncated:
        chapter_count = sum(1 for _n, lv in iter_nodes((tree_data or {}).get("nodes", [])) if lv == 1)
        scene_count = len(all_level3_nodes(tree_data))
        lines.append(
            f"……（大纲过长已截断，共 {chapter_count} 章 / {scene_count} 个场景。"
            "请优先使用 chapter / section / missing 这类 scope，避免逐条枚举节点。）"
        )
    if not lines:
        return "（当前工作区还没有大纲节点）"
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 规划提示词
# ---------------------------------------------------------------------------

def build_plan_prompt(
    command: str,
    tree_data: dict,
    *,
    checked_titles: list[str] | None = None,
    model_label: str = "",
    extra_context: str = "",
) -> str:
    checked_text = "、".join(checked_titles or []) or "（无）"
    outline_text = build_outline_text(tree_data)
    schema_example = (
        '{"goal":"...","steps":[{"action":"modify",'
        '"scope":{"type":"chapter","node_id":"..."},"nodes":[],'
        '"params":{"requirement":"..."},"title":"...","reason":"..."}]}'
    )
    return f"""你是「AI 小说创作器」的任务规划器。用户会用自然语言下达创作指令，你要先把指令拆解成可执行步骤，交给执行器逐步完成。

## 可用动作
1. generate —— 生成正文。
   params: word_count（500-20000，默认 5000）、include_next（true/false，默认 true）
2. rewrite —— 重写已有正文（扩写 / 缩写 / 润色）。
   params: word_count（500-20000，默认 3000）
3. summary —— 生成或修正概要。
   params: 无
4. modify —— 按修改要求改写正文。结果会进入“待合并修改”，不会自动覆盖原文。
   params: requirement（必填，具体、可直接交给写作者的修改要求）

## 作用范围 scope（四选一，或用 nodes 显式指定）
- {{"type":"node","node_id":"..."}}：单个 3 级场景
- {{"type":"chapter","node_id":"..."}}：某章下的全部 3 级场景
- {{"type":"section","node_id":"..."}}：某节下的全部 3 级场景
- {{"type":"missing"}}：全书所有缺失正文的场景；可加 "node_id" 限定在某章/节
- {{"type":"checked"}}：当前勾选的场景
- {{"type":"all"}}：全书所有场景
- 也可以写 "nodes":["id1","id2"] 显式指定多个场景，nodes 优先于 scope。

## 规划规则
- 只输出一个 JSON 对象，不要 markdown 代码块、不要解释文字。
- 步骤按执行顺序排列，最多 {MAX_STEPS} 步。
- 只能使用下方大纲中真实存在的 node_id，禁止编造。
- 用户说“改写 / 修改 / 调整 / 按照要求改” → modify；说“重写 / 扩写 / 缩写 / 润色” → rewrite；说“生成 / 续写 / 补全” → generate；说“概要 / 摘要” → summary。
- 批量操作优先用 scope（chapter / section / missing / checked），不要逐条列出几十个 id。
- modify 的 requirement 必须完整表达用户意图，并补充“只修改必要部分，保持剧情、人物与时间线一致”这类约束。
- 用户指令含糊时，按最合理的方式规划，并在 goal 中写明你的理解。
- 不要规划与指令无关的步骤；不要自动加入合并、导出等操作。

## 输出格式（严格 JSON）
{schema_example}

## 当前模型
{model_label or "（跟随当前模型）"}

## 当前已勾选的世界观设定
{checked_text}

{extra_context}

## 当前大纲（缩进层级 + 节点 ID + 场景状态）
{outline_text}

## 用户命令
{command}
""".strip()


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------

def parse_plan_response(text: str) -> dict:
    """从模型输出中提取 JSON 计划，失败时抛出 ValueError。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("模型返回为空")
    if raw.startswith("> **生成失败:**"):
        raise ValueError(raw.replace("> **生成失败:**", "").strip())

    # 去掉 ```json 代码块
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        data = json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型输出里没有找到 JSON 计划")
        try:
            data = json.loads(raw[start : end + 1])
        except Exception as exc:
            raise ValueError(f"计划 JSON 解析失败：{exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("计划根节点必须是 JSON 对象")
    if not isinstance(data.get("steps"), list):
        raise ValueError("计划缺少 steps 数组")
    return data


def _coerce_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _resolve_nodes(
    tree_data: dict,
    scope: dict | None,
    explicit_nodes: list | None,
    checked_ids: list[str],
) -> tuple[list[dict], str]:
    """把 scope / nodes 解析成 3 级节点列表。"""
    if explicit_nodes:
        nodes: list[dict] = []
        seen: set[str] = set()
        for raw_id in explicit_nodes:
            node_id = str(raw_id or "").strip()
            if not node_id or node_id in seen:
                continue
            node, level = find_node_with_level(tree_data, node_id)
            if node is None:
                return [], f"找不到节点 {node_id}"
            if level != 3:
                return [], f"节点 {node_id} 不是 3 级场景（{node.get('title', '')}）"
            seen.add(node_id)
            nodes.append(node)
        return nodes, "" if nodes else "nodes 为空"

    scope = scope if isinstance(scope, dict) else {}
    scope_type = str(scope.get("type") or "node").strip().lower()
    node_id = str(scope.get("node_id") or "").strip()

    if scope_type == "node":
        node, level = find_node_with_level(tree_data, node_id)
        if node is None:
            return [], f"scope.node 找不到节点 {node_id}"
        if level != 3:
            return [], "scope.type=node 只能指定 3 级场景；章/节请用 chapter 或 section"
        return [node], ""

    if scope_type in ("chapter", "section"):
        node, level = find_node_with_level(tree_data, node_id)
        if node is None:
            return [], f"scope.{scope_type} 找不到节点 {node_id}"
        expected = 1 if scope_type == "chapter" else 2
        if level != expected:
            return [], (
                f"scope.type={scope_type} 需要 {expected} 级节点，"
                f"但 {node.get('title', '')} 是 {level} 级"
            )
        nodes = level3_descendants(node, level)
        if not nodes:
            return [], f"【{node.get('title', '')}】下没有 3 级场景"
        return nodes, ""

    if scope_type == "missing":
        if node_id:
            node, level = find_node_with_level(tree_data, node_id)
            if node is None:
                return [], f"scope.missing 找不到节点 {node_id}"
            nodes = [
                item
                for item in level3_descendants(node, level)
                if is_missing(item)
            ]
        else:
            nodes = missing_level3_nodes(tree_data)
        return nodes, "" if nodes else "没有缺失正文的场景"

    if scope_type == "checked":
        nodes = []
        seen = set()
        for item in checked_ids or []:
            node, level = find_node_with_level(tree_data, str(item))
            if node is None or level != 3 or str(item) in seen:
                continue
            seen.add(str(item))
            nodes.append(node)
        return nodes, "" if nodes else "当前没有勾选任何场景"

    if scope_type == "all":
        nodes = all_level3_nodes(tree_data)
        return nodes, "" if nodes else "大纲里没有 3 级场景"

    return [], f"未知的 scope 类型：{scope_type}"


def normalize_plan(
    raw_plan: dict,
    tree_data: dict,
    *,
    checked_ids: list[str] | None = None,
) -> tuple[dict, list[str]]:
    """校验并补全计划，返回 ``(规范化计划, 错误列表)``。

    有错误时仍返回已解析出的合法步骤，方便界面展示问题。
    """
    errors: list[str] = []
    goal = str((raw_plan or {}).get("goal", "") or "").strip()
    raw_steps = (raw_plan or {}).get("steps") or []
    if not isinstance(raw_steps, list):
        return {"goal": goal, "steps": []}, ["steps 必须是数组"]
    if len(raw_steps) > MAX_STEPS:
        errors.append(f"步骤数超过上限 {MAX_STEPS}，只保留前 {MAX_STEPS} 步")
        raw_steps = raw_steps[:MAX_STEPS]

    steps: list[dict] = []
    for index, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            errors.append(f"第 {index} 步不是对象，已跳过")
            continue
        action = str(raw_step.get("action", "") or "").strip().lower()
        if action not in ACTION_LABELS:
            errors.append(f"第 {index} 步使用了未知动作：{action or '(空)'}")
            continue

        params = raw_step.get("params") if isinstance(raw_step.get("params"), dict) else {}
        checked_ids = checked_ids or []
        nodes, resolve_error = _resolve_nodes(
            tree_data,
            raw_step.get("scope"),
            raw_step.get("nodes"),
            checked_ids,
        )
        if resolve_error or not nodes:
            errors.append(f"第 {index} 步（{ACTION_LABELS[action]}）范围无效：{resolve_error}")
            continue

        step_params: dict[str, Any] = {}
        if action == "generate":
            step_params["word_count"] = _coerce_int(
                params.get("word_count"), 5000, 200, 20000
            )
            step_params["include_next"] = bool(params.get("include_next", True))
        elif action == "rewrite":
            step_params["word_count"] = _coerce_int(
                params.get("word_count"), 3000, 200, 20000
            )
        elif action == "modify":
            requirement = str(params.get("requirement", "") or "").strip()
            if not requirement:
                errors.append(f"第 {index} 步（按要求改写）缺少 requirement")
                continue
            step_params["requirement"] = requirement

        titles = [str(node.get("title", "") or "未命名") for node in nodes]
        title = str(raw_step.get("title", "") or "").strip()
        if not title:
            suffix = f"等 {len(nodes)} 个场景" if len(nodes) > 1 else titles[0]
            title = f"{ACTION_LABELS[action]}：{suffix}"
        steps.append(
            {
                "index": len(steps) + 1,
                "action": action,
                "action_label": ACTION_LABELS[action],
                "title": title,
                "reason": str(raw_step.get("reason", "") or "").strip(),
                "scope": raw_step.get("scope") if isinstance(raw_step.get("scope"), dict) else {},
                "params": step_params,
                "node_ids": [str(node.get("id", "")) for node in nodes],
                "node_titles": titles,
            }
        )

    return {"goal": goal, "steps": steps}, errors


def describe_plan(plan: dict) -> str:
    """生成便于日志展示的计划摘要。"""
    lines = []
    goal = str((plan or {}).get("goal", "") or "").strip()
    if goal:
        lines.append(f"目标：{goal}")
    for step in (plan or {}).get("steps", []):
        scope_desc = ""
        scope = step.get("scope") or {}
        if scope.get("type"):
            scope_desc = SCOPE_LABELS.get(str(scope.get("type")), str(scope.get("type")))
        params = step.get("params") or {}
        param_desc = ""
        if "word_count" in params:
            param_desc += f" {params['word_count']}字"
        if params.get("requirement"):
            param_desc += f" 要求：{params['requirement'][:40]}…"
        lines.append(
            f"{step.get('index')}. {step.get('action_label')}"
            f"（{len(step.get('node_ids', []))} 个场景{scope_desc and ' / ' + scope_desc}{param_desc}）"
            f" — {step.get('title', '')}"
        )
    return "\n".join(lines)


@dataclass
class PlanResult:
    ok: bool
    plan: dict = field(default_factory=lambda: {"goal": "", "steps": []})
    errors: list[str] = field(default_factory=list)
    raw_text: str = ""
    model_label: str = ""


class AgentPlanner:
    """调用大模型生成计划。"""

    def __init__(self, llm_client, workspace=None):
        self.llm_client = llm_client
        self.workspace = workspace

    def plan(
        self,
        command: str,
        tree_data: dict,
        *,
        checked_paths: list[str] | None = None,
        checked_ids: list[str] | None = None,
        progress_callback=None,
        action_id: str | None = None,
    ) -> PlanResult:
        if not self.llm_client:
            return PlanResult(ok=False, errors=["尚未配置文本模型"])
        checked_titles = []
        for path in checked_paths or []:
            name = str(path).replace("\\", "/").rstrip("/").split("/")[-1]
            if name:
                checked_titles.append(name)
        model_label = ""
        runtime = getattr(self.llm_client, "runtime", None)
        if runtime is not None:
            model_label = runtime.label()
        prompt = build_plan_prompt(
            command,
            tree_data,
            checked_titles=checked_titles,
            model_label=model_label,
        )
        system_prompt = (
            "你是严谨的任务规划助手，只能输出一个合法的 JSON 对象。"
            "不要输出 markdown 代码块、解释、前后缀。"
        )
        raw_text = self.llm_client.generate_text(
            prompt,
            override_system_instruction=system_prompt,
            progress_callback=progress_callback,
            task_meta={
                "action_id": action_id,
                "label": "Agent 规划",
            },
        )
        try:
            raw_plan = parse_plan_response(raw_text)
        except ValueError as exc:
            return PlanResult(
                ok=False,
                errors=[str(exc)],
                raw_text=raw_text,
                model_label=model_label,
            )
        plan, errors = normalize_plan(
            raw_plan, tree_data, checked_ids=checked_ids
        )
        return PlanResult(
            ok=not errors and bool(plan["steps"]),
            plan=plan,
            errors=errors,
            raw_text=raw_text,
            model_label=model_label,
        )
