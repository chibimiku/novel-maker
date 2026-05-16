# Proofread 模块调用指引

## 1. 文档目标

本文档面向开发者，说明如何在**不改现有业务流程**的前提下，调用 `modules/proofread` 独立校对模块。

---

## 2. 模块结构速览

核心入口：

- `modules/proofread/__init__.py`
- `modules/proofread/engine.py`
- `modules/proofread/cli.py`

核心能力层：

- `loader.py`：读取工作区、设定、场景内容
- `transport_codec.py`：请求体转码（`base64+gzip+utf8`）
- `llm_gateway.py`：LLM 调用与重试
- `cache_manager.py`：中间缓存
- `checkpoint_manager.py`：运行检查点与恢复
- `solve_engine.py`：修复执行引擎
- `issue_aggregator.py`：问题聚合与导出
- `reporters/issue_reporter.py`：JSON/Markdown 报告输出

Pass 层：

- `passes/` 下已实现 `W1-W5` 与 `L1-L20`。

---

## 3. 代码调用（推荐）

## 3.1 最简方式（默认全量 Pass）

```python
from core.workspace_manager import WorkspaceManager
from core.llm_client import LLMClient
from modules.proofread import create_default_engine

workspace = WorkspaceManager(r"D:\path\to\workspace")
config = {
    "text_api": {
        "type": "openai",
        "model": "gpt-4o",
        "api_key": "YOUR_KEY",
        "base_url": "https://api.openai.com/v1"
    }
}
llm_client = LLMClient(config)
engine = create_default_engine(workspace=workspace, llm_client=llm_client, config=config)

# 环节一：查找问题
issues = engine.run_find(progress_callback=print)

# 环节二：自动修复（写入 pending_modifies）
stats = engine.run_solve(issues=issues, progress_callback=print)
print(stats)
```

## 3.2 指定 Pass 运行

```python
from modules.proofread.engine import ProofreadEngine
from modules.proofread.passes import PunctuationPass, TimelinePass

engine = ProofreadEngine.from_llm_client(
    workspace=workspace,
    llm_client=llm_client,
    config=config,
    pass_instances=[PunctuationPass(), TimelinePass()],
)
issues = engine.run_find()
```

## 3.3 断点恢复

```python
# find 恢复
issues = engine.resume_find(progress_callback=print)

# solve 恢复（可叠加筛选）
stats = engine.resume_solve(
    issues=issues,
    include_categories={"L1", "L2", "W5"},
    include_chapters={"第一章"},
    progress_callback=print,
)
```

---

## 4. CLI 调用（独立运行）

## 4.1 查找问题

```bash
python -m modules.proofread --workspace <工程目录> --config <配置json> --mode find
```

## 4.2 恢复查找

```bash
python -m modules.proofread --workspace <工程目录> --config <配置json> --mode find --resume
```

## 4.3 自动修复

```bash
python -m modules.proofread --workspace <工程目录> --config <配置json> --mode solve
```

## 4.4 按类别/章节筛选修复

```bash
python -m modules.proofread \
  --workspace <工程目录> \
  --config <配置json> \
  --mode solve \
  --categories L1,L2,W5 \
  --chapters 第一章,第二章
```

---

## 5. 产物与路径

- 问题清单：`系统数据/proofread_issues.json`
- 中间缓存：`系统数据/proofread_cache/`
- 运行检查点：`系统数据/proofread_checkpoint/`
- 修复结果：`系统数据/pending_modifies/{node_id}.json`

---

## 6. 回调约定

- `progress_callback(message: str)`：接收文本进度信息。
- `run_find` 返回：`list[Issue]`
- `run_solve` 返回：`dict`，包含 `ok_count/fail_count/skipped_count/total`

---

## 7. 与现有系统合并建议（后续）

当前阶段建议继续通过 CLI 或独立入口验证；待你确认后再执行：

1. UI 菜单增加“启动校对（独立模块）”入口；
2. 将 `progress_callback` 映射到现有日志面板；
3. 复用已有 Diff 合并入口读取 `pending_modifies`。

