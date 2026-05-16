# 小说校对系统详细设计 V2（基于 `modules/proofread/DESIGN.md`）

## 1. 目标与边界

本文档在现有 `DESIGN.md` 基础上补全可实现级别细节，重点回答四件事：

1. 每个核心函数的入参/出参（Python 类型级别）。
2. 复用现有函数与新建函数的边界。
3. 中间存储数据结构（内存 + 落盘 JSON）。
4. 传输转码策略（解决请求失败、乱码、超长和特殊字符导致的失败）。

不改变原有三阶段流程：`Find -> Solve -> Confirm(Diff)`。

---

## 2. 复用现有能力（已存在，不重写）

## 2.1 复用清单

| 能力 | 复用函数 | 入参 | 出参 | 用途 |
|---|---|---|---|---|
| 加载大纲树 | `WorkspaceManager.load_outline_tree` | `()` | `dict` | 获取章/节/场景树及 `md5` |
| 保存大纲树 | `WorkspaceManager.save_outline_tree` | `(tree_data: dict)` | `None` | 更新 `outline_tree.json` |
| 计算文件 MD5 | `WorkspaceManager.calculate_md5` | `(file_path: str)` | `str` | 缓存失效判断 |
| 保存正文文件 | `WorkspaceManager.save_markdown_file` | `(rel_path: str, content: str)` | `str` | 写正文并返回新 `md5` |
| 保存待合并修改 | `WorkspaceManager.save_pending_modify` | `(node_id: str, original_text: str, modified_text: str, request_prompt: str)` | `None` | 环节二产物 |
| 读取待合并修改 | `WorkspaceManager.get_pending_modify` | `(node_id: str)` | `dict \| None` | 环节三读数据 |
| 删除待合并修改 | `WorkspaceManager.delete_pending_modify` | `(node_id: str)` | `None` | Diff 合并后清理 |
| 文本生成 | `LLMClient.generate_text` | `(prompt: str, context_messages: list \| None, override_system_instruction: str \| None, progress_callback: callable \| None)` | `str` | 各 Pass 与 Solve 调用 LLM |
| 长上下文压缩 | `WorldContextCompressor.compress` | `(nodes: list[WorldSettingNode])` | `str` | 场景过长时压缩设定上下文 |
| 打开差异合并弹窗 | `NovelTreeMixin.open_merge_dialog` | `(node_id: str, real_node: dict)` | `None` | Confirm 阶段复用 |
| 应用合并结果 | `NovelTreeMixin.apply_merge_result` | `(node_id: str, real_node: dict, result_text: str)` | `None` | 写回正文并更新 `md5` |

## 2.2 复用原则

- 不重写 `pending_modifies` 格式，完全兼容现有 UI Diff 流程。
- 不改造 `LLMClient.generate_text` 的现有行为，通过包装层处理转码和重试。
- `WorkspaceManager` 继续作为工作区读写统一入口。

---

## 3. 新建模块与函数签名

> 新增代码目录：`modules/proofread/`

## 3.1 模块：`types.py`

```python
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
    time_desc: str
    locations: list[str]
    characters_declared: list[str]
    events: list[str]
    summary_narrative: str

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
```

## 3.2 模块：`loader.py`

```python
class ProofreadDataLoader:
    def __init__(self, workspace: WorkspaceManager): ...

    def load_workspace_data(self) -> dict:
        """
        入参: 无（使用初始化注入的 workspace）
        出参:
        {
          "project_name": str,
          "scene_list": list[SceneMeta],
          "characters": list[dict],
          "locations": list[dict],
          "character_alias_map": dict[str, str],
        }
        """

    def read_text_file_with_fallback(self, file_path: str) -> str:
        """
        入参: file_path
        出参: 标准化后的正文字符串（统一 '\n'）
        规则: utf-8 -> gbk 回退
        """
```

## 3.3 模块：`cache_manager.py`

```python
class ProofreadCacheManager:
    def __init__(self, workspace_path: str): ...

    def load_manifest(self) -> dict: ...
    def save_manifest(self, manifest: dict) -> None: ...
    def is_scene_pass_cache_valid(self, scene_id: str, scene_md5: str, pass_name: str) -> bool: ...
    def load_scene_pass_data(self, scene_id: str, pass_name: str) -> dict | None: ...
    def save_scene_pass_data(self, scene_id: str, pass_name: str, data: dict) -> None: ...
    def invalidate_scene(self, scene_id: str) -> None: ...
```

## 3.4 模块：`checkpoint_manager.py`

```python
class ProofreadCheckpointManager:
    def __init__(self, workspace_path: str): ...

    def init_run(self, phase: Phase, run_id: str, md5_snapshot: dict[str, str], pass_names: list[str]) -> dict: ...
    def load_run_state(self) -> dict | None: ...
    def save_run_state(self, run_state: dict) -> None: ...
    def save_task_state(self, pass_name: str, scene_id: str, task_state: dict) -> None: ...
    def save_pass_issues(self, pass_name: str, issues: list[dict]) -> None: ...
    def load_pass_tasks(self, pass_name: str) -> dict: ...
    def list_pending_scenes(self, pass_name: str) -> list[str]: ...
    def cleanup(self) -> None: ...
```

## 3.5 模块：`transport_codec.py`（新增，重点）

```python
class TransportCodec:
    @staticmethod
    def encode_payload(payload: dict, compress: bool = True) -> dict:
        """
        入参:
          payload: 原始请求体
          compress: 是否 gzip 压缩
        出参:
          {
            "encoding": "base64+gzip+utf8" | "base64+utf8",
            "payload_b64": str,
            "sha256": str,
            "raw_bytes": int,
            "encoded_bytes": int,
          }
        """

    @staticmethod
    def decode_payload(encoded: dict) -> dict:
        """
        入参: encode_payload 产物
        出参: 原始 payload(dict)
        异常: ValueError(校验失败/不可解码)
        """

    @staticmethod
    def to_llm_prompt(encoded: dict, prompt_header: str) -> str:
        """
        入参:
          encoded: 已编码载荷
          prompt_header: 指令头（告诉模型先解码再处理）
        出参: 最终 prompt 字符串
        """
```

## 3.6 模块：`llm_gateway.py`（新增，包装重试）

```python
class ProofreadLLMGateway:
    def __init__(self, llm_client: LLMClient, max_retry: int = 3, backoff_sec: float = 1.5): ...

    def call_json(
        self,
        payload: dict,
        task_id: str,
        system_instruction: str | None = None,
        progress_callback = None,
    ) -> dict:
        """
        入参:
          payload: 原始业务 JSON（场景、问题、设定）
          task_id: 如 "l1_continuity/scene_xxx"
          system_instruction: 可覆盖系统指令
        出参:
          {
            "ok": bool,
            "result": dict | None,
            "raw_text": str,
            "attempts": int,
            "error": str | None
          }
        逻辑:
          1) TransportCodec.encode_payload
          2) 调用 LLMClient.generate_text
          3) 解析 JSON；失败则重试（指数退避）
          4) 每次重试切换更严格模板（要求仅返回 JSON）
        """
```

## 3.7 模块：`engine.py`

```python
class ProofreadEngine:
    def __init__(
        self,
        workspace: WorkspaceManager,
        llm_gateway: ProofreadLLMGateway,
        cache_manager: ProofreadCacheManager,
        checkpoint_manager: ProofreadCheckpointManager,
        config: dict,
    ): ...

    def run_find(self, stop_event=None, progress_callback=None) -> list[Issue]: ...
    def resume_find(self, stop_event=None, progress_callback=None) -> list[Issue]: ...
    def run_solve(self, issues: list[Issue], stop_event=None, progress_callback=None) -> dict: ...
    def resume_solve(self, stop_event=None, progress_callback=None) -> dict: ...
```

## 3.8 模块：`passes/base.py`

```python
class BasePass:
    pass_name: str
    categories: list[str]

    def run(
        self,
        scene_list: list[SceneMeta],
        shared_data: dict,
        stop_event=None,
        progress_callback=None,
    ) -> tuple[list[Issue], dict]:
        """
        出参:
          issues: 本 Pass 发现的问题
          intermediates: {scene_id: {...}} 的中间产物
        """
```

---

## 4. 传输转码与失败恢复（必须实现）

## 4.1 为什么要加转码层

历史失败主要集中在：

- Prompt 中包含大量中文符号、换行、引号，导致 JSON 片段被模型破坏。
- 长文本传输中出现截断、丢括号、转义失配。
- 网络抖动时返回半段内容。

## 4.2 编码协议

1. 业务对象 `payload(dict)` 先 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`。
2. 编码为 `utf-8 bytes`。
3. 可选 `gzip` 压缩（推荐开启）。
4. `base64.urlsafe_b64encode` 得到 `payload_b64`。
5. 记录 `sha256` 与长度元数据。

编码包结构：

```json
{
  "encoding": "base64+gzip+utf8",
  "payload_b64": "<...>",
  "sha256": "7d5f...",
  "raw_bytes": 187230,
  "encoded_bytes": 64420
}
```

## 4.3 LLM 提示词协议（请求）

请求模板固定要求：

1. 先解码 `payload_b64`（按 `encoding`）。
2. 按字段处理。
3. 仅输出 JSON（不允许 markdown 包裹）。
4. 顶层必须含 `ok`, `issues`, `intermediate_data`, `error`。

## 4.4 LLM 返回解析协议（响应）

解析顺序：

1. 直接 `json.loads(raw_text)`。
2. 失败则提取首个完整 `{...}` 再解析。
3. 仍失败则进行一次“修复解析”重试请求（附上上次原文）。
4. 连续失败达到阈值，任务标记 `FAILED`，但流程继续。

## 4.5 重试策略

- 最大重试：`3`。
- 退避：`1.5s -> 3s -> 6s`。
- 可重试错误：超时、连接中断、JSON 解析失败、空返回。
- 不可重试错误：鉴权失败、配额拒绝（直接透出）。

---

## 5. 中间存储结构（落盘）

## 5.1 `系统数据/proofread_cache/manifest.json`

```json
{
  "version": "v2",
  "updated_at": "2026-05-09T10:00:00",
  "scenes": {
    "scene_001": {
      "md5": "9f3a9d...",
      "cached_passes": ["l1_continuity", "l2_appearance", "w5_punctuation"],
      "cached_at": "2026-05-09T10:00:00"
    }
  }
}
```

## 5.2 `系统数据/proofread_cache/scene_{scene_id}/{pass_name}.json`

```json
{
  "scene_id": "scene_001",
  "pass_name": "l1_continuity",
  "md5": "9f3a9d...",
  "intermediate_data": {
    "states": [
      {
        "character": "朱岚",
        "start_state": {"left_foot": "烫伤"},
        "end_state": {"left_foot": "无法着地"}
      }
    ]
  },
  "generated_at": "2026-05-09T10:00:00"
}
```

## 5.3 `系统数据/proofread_checkpoint/run_state.json`

```json
{
  "run_id": "20260509-100000-9a2f",
  "phase": "find",
  "status": "running",
  "md5_snapshot": {"scene_001": "9f3a9d..."},
  "passes": {
    "l1_continuity": {"status": "running", "scenes_total": 200, "scenes_completed": 51, "scenes_failed": 1}
  },
  "llm_calls_total": 88,
  "updated_at": "2026-05-09T10:15:30"
}
```

## 5.4 `系统数据/proofread_checkpoint/tasks/{pass_name}.json`

```json
{
  "pass_name": "l1_continuity",
  "scenes": {
    "scene_001": {
      "status": "completed",
      "attempts": 1,
      "tokens_used": 3500,
      "issues_found": 2,
      "llm_response_file": "llm_responses/l1_continuity/scene_001.json"
    },
    "scene_002": {
      "status": "failed",
      "attempts": 3,
      "error_message": "json parse failed"
    }
  },
  "issues": []
}
```

## 5.5 `系统数据/proofread_checkpoint/llm_responses/{pass}/{scene}.json`

```json
{
  "task_id": "l1_continuity/scene_001",
  "request_encoded": {
    "encoding": "base64+gzip+utf8",
    "sha256": "7d5f..."
  },
  "raw_response_text": "{...}",
  "parsed_result": {
    "ok": true,
    "issues": [],
    "intermediate_data": {}
  },
  "attempt": 1,
  "timestamp": "2026-05-09T10:10:00"
}
```

## 5.6 `系统数据/proofread_issues.json`

```json
{
  "version": "v2",
  "generated_at": "2026-05-09T10:30:00",
  "summary": {
    "total": 123,
    "by_severity": {"error": 28, "warning": 71, "info": 24},
    "by_category": {"L1": 12, "L2": 8, "W5": 20}
  },
  "issues": [
    {
      "issue_id": "L1-0012",
      "category": "L1",
      "pass_name": "l1_continuity",
      "severity": "error",
      "title": "角色伤势前后矛盾",
      "description": "上场景无法着地，下场景健步行走且无恢复描写",
      "location": {
        "node_id": "scene_035",
        "chapter_title": "第3章",
        "section_title": "第2节",
        "scene_title": "雨夜追逐",
        "file_path": "第3章/场景3-2-4.md",
        "line_numbers": [48, 126]
      },
      "suggestion": "补充治疗或移动方式过渡",
      "evidence": ["“左脚疼得根本踩不下去”", "“她快步冲下楼梯”"]
    }
  ]
}
```

---

## 6. Find/Solve 关键函数 I/O 定义

## 6.1 Find 阶段

| 函数 | 入参 | 出参 |
|---|---|---|
| `ProofreadEngine.run_find` | `stop_event`, `progress_callback` | `list[Issue]` |
| `ProofreadDataLoader.load_workspace_data` | 无 | `dict(project_name, scene_list, characters, locations, character_alias_map)` |
| `PreAnalyzer.build_scene_context` | `(scene: SceneMeta)` | `SceneContext` |
| `BasePass.run` | `(scene_list, shared_data, stop_event, progress_callback)` | `(issues: list[Issue], intermediates: dict)` |
| `IssueAggregator.aggregate` | `(pass_issue_map: dict[str, list[Issue]])` | `list[Issue]` |
| `IssueAggregator.save_issues` | `(issues: list[Issue], output_file: str)` | `None` |

## 6.2 Solve 阶段

| 函数 | 入参 | 出参 |
|---|---|---|
| `ProofreadEngine.run_solve` | `(issues: list[Issue], stop_event, progress_callback)` | `dict`（统计信息） |
| `SolveEngine.group_issues_by_scene` | `(issues: list[Issue], include_levels: set[str] \| None)` | `dict[str, list[Issue]]` |
| `SolveEngine.build_fix_payload` | `(scene: SceneMeta, issues: list[Issue], characters: list[dict])` | `dict` |
| `SolveEngine.generate_fixed_text` | `(payload: dict, task_id: str)` | `dict(ok, fixed_text, error)` |
| `SolveEngine.save_pending_modify` | `(node_id: str, original_text: str, modified_text: str, request_prompt: str)` | `None`（内部复用 `workspace.save_pending_modify`） |

---

## 7. 复用/新建映射（逐函数）

| 目标函数 | 是否新建 | 说明 |
|---|---|---|
| `ProofreadDataLoader.load_workspace_data` | 新建 | 聚合读取大纲、设定、场景正文 |
| `ProofreadDataLoader.read_text_file_with_fallback` | 新建 | 复用现有 utf-8/gbk 回退经验 |
| `TransportCodec.encode_payload` | 新建 | 解决传输失败核心能力 |
| `TransportCodec.decode_payload` | 新建 | 对称解码 + 校验 |
| `ProofreadLLMGateway.call_json` | 新建 | 封装转码、请求、解析、重试 |
| `WorkspaceManager.load_outline_tree` | 复用 | 不改签名 |
| `WorkspaceManager.save_pending_modify` | 复用 | 不改格式 |
| `LLMClient.generate_text` | 复用 | 网关层调用，不侵入原逻辑 |
| `NovelTreeMixin.open_merge_dialog` | 复用 | Confirm UI 入口 |
| `NovelTreeMixin.apply_merge_result` | 复用 | 落盘 + md5 + 清理 pending |

---

## 8. 实施顺序（建议）

1. 先实现 `types.py`, `transport_codec.py`, `llm_gateway.py`（先把失败率降下来）。
2. 再实现 `loader/cache/checkpoint`（保证可恢复）。
3. 再接入 25 个 Pass（优先规则型 Pass）。
4. 最后接 `solve_engine` 并直接复用现有 Diff UI。

---

## 9. 验收标准（含“转码”要求）

1. 任意含中文、换行、引号、emoji、长段落的 payload，经 `encode_payload -> decode_payload` 后完全一致（sha256 校验通过）。
2. LLM 返回非标准 JSON 时，最多 3 次可恢复重试；失败任务可记录并继续总流程。
3. 重跑时未变更场景命中 cache，不重复调用 LLM。
4. `pending_modifies/{node_id}.json` 与现有系统完全兼容，可直接打开 Diff 合并。
5. 人工中断后 `resume_find/resume_solve` 可从断点继续，且不重复已完成任务。

