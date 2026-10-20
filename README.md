# ✍️ AI 小说创作器 (AI Novel Creator)

一款基于 PyQt6 构建的本地化 AI 辅助小说创作客户端。本工具通过结构化的提示词工程，将大语言模型（LLM）的生成能力与传统小说的大纲/世界观管理深度结合，实现从“灵感点子”到“几万字初稿”的自动化、全闭环创作流。

## ✨ 核心特性

* **🌍 自动化世界观构建**：输入一句话核心点子，AI 自动为你规划并生成包含“人物、地点、名词、公共”等多维度的详细世界观设定（JSON 格式驱动）。
* **📑 智能三级大纲生成**：结合当前点子与已勾选的世界观设定，一键生成“章 -> 节 -> 场景”的树状大纲结构。
* **🚀 批量正文生成引擎**：自动扫描大纲中缺失正文的“场景”节点，结合上下文（上一章结尾、下一章开头、当前概要）进行全自动批量长文本生成。
* **✂️ 智能重写与扩写**：支持对已有正文进行指定字数的精准重写、细节扩写或精简缩写。
* **🌐 沉浸式网页导出**：一键将整个工程打包导出为排版精美的可阅读 HTML 网页，并支持 EPUB 电子书。
* **🧩 提供商 → 模型二级配置**：一个提供商下可挂多个模型，每个模型单独配置上下文窗口、最大输出与采样参数（temperature / top_p / top_k / 惩罚项 / seed / stop …），内置 360+ 模型的“模型全集”作为默认值。
* **📏 按模型计算 Token 预算**：不同模型上下文窗口不同（8K～2M），每次请求都按当前模型窗口计算输入/输出预算，并用服务端返回的 usage 自动校准估算系数。
* **🗂️ 动作 / 任务队列**：每个原子任务 = 一次真实的大模型请求，自动落盘完整输入输出，可在独立界面按“动作 → 任务”追溯（例如“重写第一章 7 个小节”= 1 个动作 + 7 个任务）。
* **🤖 命令式 Agent 控制台**：输入一句自然语言命令，模型先规划出可勾选/可编辑的执行步骤（生成、重写、概要、按要求改写），确认后逐步执行；每次请求同样进入任务队列。支持 DeepSeek、千问等任意 OpenAI 兼容模型。

## 🛠️ 技术栈

* **UI 框架**: PyQt6 (搭配暗色主题)
* **AI 接口**: `openai` (官方 SDK / OpenAI 兼容接口), `google-genai` (Gemini SDK), `httpx`
* **数据存储**: 本地文件系统 (Markdown 用于正文，JSON 用于大纲树、设定、任务记录)

## 📦 安装与运行

1. 克隆本项目到本地。
2. 确保已安装 Python 3.10+ 环境。
3. 安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

4. 运行主程序：

   ```bash
   python main.py
   ```

5. 初次配置：启动后打开 `设置 → 模型服务（提供商 / 模型 / 参数）`，依次：
   1. 新增一个提供商（OpenAI 兼容接口或 Gemini），填入 Base URL 与 API Key；也可以点「⚡ 预设」一键添加 **DeepSeek 官方 / 阿里云百炼（千问） / OpenRouter / OpenAI / Moonshot / 本地 Ollama**；
   2. 在提供商下「从全集添加」/「拉取提供商模型」/「自定义模型」，并设置上下文窗口 / 最大输出 / 采样参数；
   3. 在顶部“画像绑定”里指定「普通小说」与「NSFW 小说」各自使用哪个模型。

   之后可以在 `设置 → 快速切换模型` 里一键切换当前画像使用的模型，无需重新打开管理窗口。

   旧版 `conf/setting.json`（只有 `text_api`）会在启动时自动迁移为 `providers` 二级结构，无需手工改文件。

## 🧭 菜单结构

| 菜单 | 主要功能 |
| --- | --- |
| 文件 | 新建 / 打开 / 重载工作区，保存全部，导出 HTML / EPUB，退出 |
| 编辑 | 保存当前节点、删除当前节点、后退一步、全选 / 全不选大纲节点 |
| 视图 | 设定面板 / 大纲面板 / 编辑区 / 日志 / Issue 面板显隐，Debug Log，重置布局 |
| 生成 | 生成正文、重写正文、重新生成概要、批量生成缺失场景、批量生成 / 校验 / 修改勾选节点 |
| 工具 | **🤖 Agent 控制台**、**任务队列**、小说校对、时间线校对、修复章节结构、一键合并待修改、从文本建工作区、世界观 / 大纲生成 |
| 设置 | 系统配置（网络 / 通知 / 指令）、**模型服务（提供商 / 模型 / 参数）**、快速切换模型、NSFW 工作区开关 |
| 帮助 | 使用说明、关于 |

## 🧠 模型配置与 Token 预算

配置文件 `conf/setting.json` 的关键结构：

```json
{
  "providers": [
    {
      "id": "pv_xxx",
      "name": "DeepSeek 官方",
      "type": "openai",
      "base_url": "https://api.deepseek.com",
      "api_key": "sk-...",
      "timeout": 120,
      "models": [
        {
          "id": "md_xxx",
          "model": "deepseek-chat",
          "display_name": "DeepSeek Chat",
          "context_size": 131072,
          "max_output_tokens": 8192,
          "capabilities": ["chat"],
          "supported_params": ["temperature", "top_p", "max_tokens"],
          "params": {"temperature": 0.7}
        }
      ]
    }
  ],
  "text_api_profiles": {
    "normal": {"provider_id": "pv_xxx", "model_id": "md_xxx", "instructions": "..."},
    "nsfw": {"provider_id": "pv_yyy", "model_id": "md_yyy", "instructions": "..."}
  }
}
```

* `supported_params` 决定“这个模型支持哪些参数”，请求时只发送受支持的参数。
  推理类模型（o1/o3/o4/gpt-5 等）默认不发送 `temperature`；Gemini / Claude 才勾选 `top_k`。
* 上下文窗口与最大输出共同决定输入预算：
  `输入预算 = context_size - max_output_tokens - 协议预留`，
  超出时优先裁剪较早的上下文消息，再按头尾保留裁剪用户提示词。
* 估算系数会随真实 `usage.prompt_tokens` 自动校准，并按模型分别记录在
  `conf/token_calibration.json`（不入库）。
* 模型全集位于 `data/model_catalog.json`，可用
  `python tools/build_model_catalog.py` 从本地历史配置重新生成。

## 🗂️ 任务队列

* 目录：`<工作区>/系统数据/任务队列/`
  * `index.json`：动作摘要索引
  * `actions/<action_id>.json`：动作详情 + 全部原子任务（含输入/输出/参数/耗时/usage）
* 每个原子任务记录：system prompt、用户提示词、上下文消息、裁剪后的实际发送内容、
  请求参数、上下文窗口/输出上限/输入预算、耗时、usage、自动续请求轮次、错误信息。
* 批量动作开始时会把整批任务预先登记为“排队中”（例如 7 个小节立即出现 7 条任务），
  每次真实请求执行时再“认领”对应任务并补全输入输出；中途停止时未执行的任务标记为已取消。
* 打开方式：`工具 → 📋 任务队列（动作 / 任务追踪）`，或点击状态栏右下角的「📋 任务队列」。
* 未显式归类的零散请求会挂在“临时模型请求”动作下，闲置 30 秒后自动收尾。
* 退出应用时仍处于运行中的任务，下次启动会标记为“已中断”。

## 🤖 Agent 控制台（命令 → 计划 → 执行）

入口：`工具 → 🤖 Agent 控制台（命令 → 计划 → 执行）`，或状态栏的「🤖 Agent」（`Ctrl+Shift+A`）。

使用方式：

1. 用一句自然语言描述任务，例如：
   * “把第一章下面缺失正文的场景补完，每个 3000 字”
   * “检查第一章所有场景，按人物设定改写与设定冲突的地方”
   * “给第二章全部场景重新生成概要”
2. 选择模型（默认跟随当前模型，也可临时指定某个提供商/模型，例如 DeepSeek 或千问）。
3. 点「① 规划」：模型根据当前大纲、已勾选设定和命令，输出结构化步骤计划。
4. 在计划表里勾选要执行的步骤，或点「✏️ 编辑计划」直接改 JSON（会重新校验节点范围）。
5. 点「② 执行」，按步骤顺序执行；可随时「停止」（当前请求结束后停下）。

计划支持的动作：

| 动作 | 说明 | 主要参数 |
| --- | --- | --- |
| generate | 生成缺失正文 / 续写 | word_count、include_next |
| rewrite | 重写已有正文（扩写/缩写/润色） | word_count |
| summary | 生成 / 修正概要 | - |
| modify | 按修改要求改写，结果进入待合并修改 | requirement |

作用范围支持单个场景、整章、整节、全部缺失场景、当前勾选场景、全书所有场景，避免让模型枚举几十个节点 ID。

执行细节：

* 规划请求与执行请求分别记录为 `agent_plan` / `agent_run` 动作，可在任务队列里逐条回看；
* 正文生成/重写直接写文件并增量更新大纲树的 md5；
* “按要求改写”只写入 `系统数据/pending_modifies/`，需要在主界面右键「进行合并」查看 diff 后手动确认，不会自动覆盖原文；
* 某个节点失败时默认中止（可勾选“某步失败后继续执行后续步骤”）。

## 📖 核心工作流 (Workflow)

1. **新建/加载工作区**：通过顶部菜单创建空文件夹初始化工程。
2. **构建世界观**：在左侧“世界观设定”树空白处右键，选择“输入点子自动产生基础设定”。
3. **生成大纲**：在左侧“小说大纲结构”树空白处右键，结合打钩的设定生成目录树。
4. **批量填坑**：点击右侧底部的 🚀 批量生成缺失场景，去喝杯咖啡，等待 AI 将正文填满。
5. **导出阅读**：点击 文件 → 导出 → 🌐 导出为可阅读网页 进行全书预览。

## 📁 工程目录说明

用户创建的工作区（Workspace）会生成如下结构：

```
├── 设定/                     # 存放所有 JSON 格式的世界观设定文件
│   ├── 人物设定/
│   ├── 地点设定/
│   └── ...
├── 正文/                     # 存放由 AI 生成或手动编写的 Markdown 场景正文
│   └── images/               # 小说插图目录
├── 系统数据/
│   ├── outline_tree.json     # 核心大纲树配置文件
│   ├── pending_modifies/     # LLM 生成的待合并修改结果
│   ├── 任务队列/              # 动作 / 原子任务记录
│   └── mobile_issues/        # 手机端提交的修改建议（Issue）
├── www/                      # 导出的可阅读网页（静态，IIS 映射）
│   ├── index.html            # 小说网页版
│   └── images/               # 插图同步目录
├── www-php/                  # PHP 后端接口（IIS FastCGI 映射）
│   └── api/
│       └── issues.php        # 接收手机端提交的 Issue 建议
└── ...
```

## 🧪 自动化测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

测试覆盖：

* `tests/test_token_budget.py`：token 估算、预算计算、裁剪、校准；
* `tests/test_model_config.py`：目录查找、参数推断、归一化、迁移、运行期模型；
* `tests/test_config_store.py`：配置读写、深合并、运行期派生；
* `tests/test_task_queue.py`：动作/任务生命周期、持久化、中断恢复、统计；
* `tests/test_llm_client.py`：参数下发、按模型预算、任务记录、续写、错误处理；
* `tests/test_workspace_manager.py`：工作区初始化、MD5 校验、待合并修改；
* `tests/test_agent_planner.py`：规划提示词、JSON 解析、scope 解析、计划校验；
* `tests/test_agent_executor.py`：生成/重写/概要/改写的执行与写回、失败与取消、任务记录；
* `tests/test_ui_window.py` / `test_ui_task_queue_dialog.py` / `test_ui_model_manager.py` /
  `test_ui_agent_console.py`：offscreen 模式下的菜单结构、视图切换、任务队列界面、
  模型管理界面、Agent 控制台（含“规划 → 执行”端到端流程）。

> Qt 界面测试通过 `QT_QPA_PLATFORM=offscreen` 运行，无需真实显示器。

## 📱 手机端提交修改建议（Mobile Issue）

### 使用方式

1. **导出网页**：在编辑器中点击 `文件 → 导出 → 🌐 导出为可阅读网页`，这会在工作区 `www/` 目录生成网页版小说。
2. **IIS 部署**：将工作区目录映射为 IIS 网站（物理路径 = workspace 根目录），配置 PHP FastCGI 处理 `www-php/` 下的 `.php` 文件。
3. **手机浏览**：手机连接同一局域网，浏览器访问 `http://电脑IP:端口/www/index.html`。
4. **提交 Issue**：阅读时点击右下角 💡 浮动按钮，填写修改需求并提交。
5. **编辑器处理**：打开工作区后，在底部 "📱 待处理 Issue" 面板中逐条管理。

### 数据格式

**`系统数据/mobile_issues/{issue_id}.json`**

```json
{
  "issue_id": "a1b2c3d4e5f6...",
  "node_id": "场景节点的UUID（对应 outline_tree.json 中的节点 id）",
  "requirement": "用户填写的修改需求文本",
  "quoted_text": "用户在手机端选中的原文引用片段（可为空）",
  "submitted_at": "2026-05-17T14:30:00+08:00",
  "status": "pending"
}
```

### 编辑器处理流程

```
待处理 Issue 列表 (按时间倒序)
  │
  ├─ [✏️ 编辑] → 修改需求文本后保存
  ├─ [🚀 提交修改] → 将需求文本送入 LLM 修改流程
  │       └─ LLM 生成修改版 → 保存到 pending_modifies/ → 节点进入"待合并"状态
  │       └─ 自动删除该 Issue
  └─ [✔ 关闭] → 直接删除 Issue（放弃处理）
```
