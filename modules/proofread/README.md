# 小说校对模块（独立运行版）

`modules/proofread` 是一个可独立运行的小说校对子系统，支持：

- 三阶段流程：`Find -> Solve -> Confirm`
- 问题类型：`W1-W5`、`L1-L20`
- 检查点恢复：中断后 `--resume` 继续
- 缓存复用：减少重复分析与重复请求
- 传输转码：`base64+gzip+utf8`，降低请求失败概率

> 当前阶段为“独立模块运行”，尚未强耦合到现有 UI 主流程。

---

## 快速开始

## 1. 准备配置文件

创建一个 JSON 文件（示例：`proofread_config.json`）：

```json
{
  "text_api": {
    "type": "openai",
    "model": "gpt-4o",
    "api_key": "YOUR_API_KEY",
    "base_url": "https://api.openai.com/v1",
    "timeout": 120
  }
}
```

## 2. 查找问题（Find）

```bash
python -m modules.proofread --workspace <小说工程目录> --config <proofread_config.json> --mode find
```

运行后会生成：

- `系统数据/proofread_issues.json`
- `系统数据/proofread_cache/`
- `系统数据/proofread_checkpoint/`

## 3. 自动修复（Solve）

```bash
python -m modules.proofread --workspace <小说工程目录> --config <proofread_config.json> --mode solve
```

运行后会生成：

- `系统数据/pending_modifies/{node_id}.json`

这些文件可直接复用现有 Diff 合并流程。

---

## 恢复执行

如果执行中断，可使用：

```bash
python -m modules.proofread --workspace <小说工程目录> --config <proofread_config.json> --mode find --resume
python -m modules.proofread --workspace <小说工程目录> --config <proofread_config.json> --mode solve --resume
```

---

## 筛选修复范围

按问题类别筛选：

```bash
python -m modules.proofread --workspace <工程目录> --config <配置json> --mode solve --categories L1,L2,W5
```

按章节筛选：

```bash
python -m modules.proofread --workspace <工程目录> --config <配置json> --mode solve --chapters 第一章,第二章
```

组合筛选：

```bash
python -m modules.proofread --workspace <工程目录> --config <配置json> --mode solve --categories L1,L2 --chapters 第一章
```

---

## 目录说明

```text
modules/proofread/
├── __init__.py
├── __main__.py
├── cli.py
├── engine.py
├── loader.py
├── llm_gateway.py
├── transport_codec.py
├── cache_manager.py
├── checkpoint_manager.py
├── solve_engine.py
├── issue_aggregator.py
├── reporters/
│   └── issue_reporter.py
└── passes/
    ├── W1-W5
    └── L1-L20
```

---

## 常见问题

## 1) 提示 `issues 文件不存在`

先执行 `--mode find`，确保生成 `系统数据/proofread_issues.json`。

## 2) LLM 请求失败

- 检查 `api_key/base_url/model` 是否可用；
- 降低并发（后续可通过配置调小）；
- 使用 `--resume` 继续未完成任务。

## 3) 修复结果没有写回正文

本模块在 `solve` 阶段只写 `pending_modifies`，不直接覆盖正文。请走现有 Diff 合并流程确认后写回。

---

## 相关文档

- 详细设计：`modules/proofread/DETAILED_DESIGN_V2.md`
- 调用指引：`modules/proofread/CALLING_GUIDE.md`
- 旧设计：`modules/proofread/DESIGN.md`

