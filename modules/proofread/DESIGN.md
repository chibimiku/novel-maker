# 小说校对系统 - 技术设计文档

## 一、项目背景与数据环境

### 1.1 数据环境

本系统校对的小说工程遵循三层树状结构化存储。项目根目录结构如下：

```
小说工程根目录/
├── 系统数据/
│   ├── outline_tree.json          # 故事大纲树（核心文件，三级树状结构）
│   ├── instruction_profile.json
│   ├── workspace_profile.json
│   ├── pending_modifies/          # 待合并修改（已有，Phase 3 复用）
│   ├── proofread_cache/           # 中间分析产物缓存（完成态，可跨运行复用）
│   └── proofread_checkpoint/      # 运行态检查点（中断恢复用，运行结束后可清理）
├── 设定/
│   ├── 人物设定/                   # 角色 JSON 文件
│   │   ├── template.json
│   │   └── *.json
│   ├── 地点设定/                   # 地点 JSON 文件（支持嵌套子目录）
│   ├── 名词设定/
│   ├── 公共设定/
│   └── 其他设定/
└── 正文/                           # 场景正文 .md 文件
    └── 场景_*.md
```

### 1.2 outline_tree.json 三级树结构

| 层级 | 名称 | 关键字段 | 叶子节点？ |
|------|------|---------|-----------|
| L1 | 章 | `id`, `title`, `summary`, `children` | 否 |
| L2 | 节 | `id`, `title`, `summary`, `children` | 否 |
| L3 | 场景 | `id`, `title`, `summary`, `children([])`, `_status`, `file_path`, `md5` | **是** |

所有节点的 `summary` 字段遵循统一的结构化格式：

```
【时间】：...
【地点】：...
【人物】：...
【事件】：
1. ...
2. ...
【剧情总结】：...
```

叶子节点（L3）额外通过 `file_path` 字段引用 `正文/` 目录下的实际长文内容，`md5` 字段记录文件内容的 hash（此字段将被缓存系统复用）。

### 1.3 设定文件格式

**人物设定** (`设定/人物设定/*.json`)：
```json
{
    "姓名": "汶晴",
    "年龄": "20岁",
    "性别": "女",
    "性格": "温柔、羞怯",
    "背景描述": "...",
    "特殊能力": "...",
    "人物关系": [
        { "人物": "朱岚", "关系": "室友与挚友" }
    ]
}
```

**地点设定** (`设定/地点设定/**/*.json`)：
```json
{
    "地点名称": "后山偏僻小径",
    "地理位置": "...",
    "环境特征": "...",
    "连接地点": ["音乐学院图书馆", "废弃仓库地下室"]
}
```

---

## 二、校对问题分类

### 2.1 逻辑问题：人物状态与互动

| 编号 | 问题类型 | 描述 | 示例 |
|------|---------|------|------|
| L1 | 穿戴/状态矛盾 | 角色衣着、装备、身体状态在时间线上前后不一致 | 场景3"穿上鞋子"，场景4"光脚走路"但中间无脱鞋描写 |
| L2 | 角色登场矛盾 | 角色在不该出现的场景中突兀出现/消失 | 某角色未在【人物】列出，却在正文中突然说话后又消失 |
| L3 | 关系/称呼错误 | 人物之间称呼或互动方式与设定关系不一致 | 设定中A是B的挚友，文中却称呼"这位同学" |
| L4 | 额外角色问题 | 出现未在设计文档中定义的角色，用完即消失 | "路过的学姐"出现一次后再无交代，且不在人物设定中 |

### 2.2 逻辑问题：时空一致性

| 编号 | 问题类型 | 描述 | 示例 |
|------|---------|------|------|
| L5 | 时间线矛盾 | 场景之间或场景内部的时间标记存在矛盾或流速不合理 | 场景A标记"深夜11点"，场景B标记"凌晨3点"，但场景B内容仅相当于15分钟的剧情 |
| L6 | 地点可达性矛盾 | 角色在相邻场景之间的地点转移不符合地点设定中的连接关系 | 设定中A和C互不连通，但角色未经过中间地点B直接从A到了C |
| L7 | 天气/环境矛盾 | 同一连续时段内，天气、光线等环境条件前后不一致 | 开头写"大雨滂沱"，同一场景末尾角色"抬头看星星"且中间无天气转晴交代 |
| L8 | 光照矛盾 | 场景的时间标记与文中的光线/能见度描述不匹配 | 写"深夜十二点"，但文中有"阳光透过窗帘照进来" |

### 2.3 逻辑问题：物品与信息连续性

| 编号 | 问题类型 | 描述 | 示例 |
|------|---------|------|------|
| L9 | 物品/道具矛盾 | 角色持有或使用的物品在时间线上凭空出现、消失或不合理转移 | 场景A中刀被踢飞到角落，场景B中角色凭空从身上掏出来用 |
| L10 | 角色知识矛盾 | 角色知道不该知道的信息，或遗忘了本应知道的关键信息 | 场景3中汶晴听到了歹徒的真名，场景5中她又对歹徒的名字表示"从未知道" |
| L11 | 数字/数量矛盾 | 同一实体（人数、物品数量等）在不同位置描述不一致 | 开场写"两个歹徒前后夹击"，后文同一场景中突然变成"三个人围住她们" |

### 2.4 逻辑问题：角色设定一致性

| 编号 | 问题类型 | 描述 | 示例 |
|------|---------|------|------|
| L12 | 角色能力矛盾 | 角色表现出的能力与设定或前文描述严重不符 | 设定中朱岚"指挥系高材生"，场景中她却连最基本的节拍都听不准 |
| L13 | 角色行为逻辑断裂 | 角色行为与其既有性格、动机、当前情绪状态产生断裂式跳跃 | 前一场景汶晴"瘫软在地、完全崩溃"，立即切换到下一场景"目光坚定地策划逃跑"且无恢复过程 |
| L14 | 角色属性数值矛盾 | 同一角色的固定属性（年龄、身高、年级等）在不同位置数值不一致 | 人物设定中"20岁"，正文某处写成"二十一岁"；设定中"170cm"，正文写成"一米六几" |

### 2.5 逻辑问题：叙事技法一致性

| 编号 | 问题类型 | 描述 | 示例 |
|------|---------|------|------|
| L15 | 对话归属不明 | 对话段中读者无法确认当前是谁在说话，或归属存在歧义 | 连续多段对话只写引号内容不写"XX说"，语境模糊无法判断说话人 |
| L16 | POV视角跳跃 | 第三人称有限视角中，叙事突然跳入另一个角色的内心活动 | 全程以汶晴视角叙事，突然冒出一句"朱岚心想他今天穿的衣服真难看" |
| L17 | 叙述语气跳变 | 同一场景内叙述人的语域（正式/口语/诗化）在无叙事理由时大幅波动 | 前半段冷静的白描风格，后半段突然变成煽情的排比句式 |

### 2.6 逻辑问题：跨场景情节漏洞

| 编号 | 问题类型 | 描述 | 示例 |
|------|---------|------|------|
| L18 | 关键事件缺失 | 两个场景之间存在剧情推进所必需的过渡事件，但未被任何场景覆盖 | 场景A结束时角色被锁在地下室，场景B开头角色已自由在校园行走，中间被解救/逃脱的过程完全缺失 |
| L19 | 因果链断裂 | 场景B的结果无法由场景A的事件或其他已知前因推导出来 | 角色上一场景还在极度恐惧中，下一场景主动去找曾经施暴者"谈合作"且无任何心理转变描写 |
| L20 | 角色数量突变 | 同一场景中参与互动的人数在不合理时刻发生无交代的变化 | 房间里有三个人在对话，其中一个突然消失此后再未提及，另外两个也毫无反应 |

### 2.7 书写习惯问题

| 编号 | 问题类型 | 描述 | 示例 |
|------|---------|------|------|
| W1 | 非简体中文习惯 | 对话或叙述不符合简体中文表达习惯 | 使用"透過"而非"通过"、"螢幕"而非"屏幕" |
| W2 | 特征词过度重复 | 角色特征的描写短语在全文高频复现 | 反复出现"170cm的身躯"达十数次 |
| W3 | 感官描写单一 | 描写长期偏向某一种感官（如只写视觉），忽视听觉/触觉/嗅觉的平衡 | 连续五个场景的环境描写全部只依赖视觉描写 |
| W4 | 句式结构单一 | 相邻段落大量使用同一句型结构，造成节奏单调 | 连续六句都是"她做了X，然后做了Y"的结构 |
| W5 | 标点使用不规范 | 引号未闭合、省略号使用不统一、中英标点混用等 | 对话左引号是中文"「"，右引号变成了英文"；"省略号有时用"……"有时用"..." |

### 2.8 问题类型总览

| 大类 | 编号范围 | 数量 | 主要检查方法 |
|------|---------|------|------------|
| 人物状态与互动 | L1-L4 | 4 | LLM 语义分析为主 |
| 时空一致性 | L5-L8 | 4 | 结构化解析 + LLM 交叉验证 |
| 物品与信息连续性 | L9-L11 | 3 | LLM 提取 + 规则比对 |
| 角色设定一致性 | L12-L14 | 3 | 设定文件 + LLM 分析 |
| 叙事技法一致性 | L15-L17 | 3 | 规则检测 + LLM 复核 |
| 跨场景情节漏洞 | L18-L20 | 3 | LLM 全局理解 |
| 书写习惯 | W1-W5 | 5 | 规则优先 + LLM 复核 |
| **合计** | | **25** | |

---

## 三、三大环节总览

校对系统将整个工作流拆分为三个顺序执行的环节，每个环节有明确的输入、输出和边界：

```
┌──────────────────────────────────────────────────────────────────┐
│                     环节一：查找问题 (Find)                        │
│  输入：工程目录路径                                                │
│  产出：proofread_issues.json（问题清单）                           │
│  产出：proofread_cache/*.json（中间分析产物，可复用）                │
│  特点：只读分析，不修改任何源文件，可多次重跑复用缓存                  │
└────────────────────────────┬─────────────────────────────────────┘
                             │ 用户审阅问题清单
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                     环节二：解决问题 (Solve)                        │
│  输入：proofread_issues.json + 原始场景正文                        │
│  产出：系统数据/pending_modifies/{node_id}.json（逐场景的修改文件）  │
│  特点：按场景维度生成修改后文本，一场景一 pending_modify               │
└────────────────────────────┬─────────────────────────────────────┘
                             │ 自动写入 pending_modifies
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                   环节三：人工确认 Diff (Confirm)                   │
│  输入：pending_modifies/ 目录下的修改文件                           │
│  交互：逐场景弹出 DiffMergeDialog（已有组件，直接复用）               │
│  产出：用户逐差异点勾选确认后的最终正文                               │
│  特点：完全复用现有 diff 流程，无需新增 UI                           │
└──────────────────────────────────────────────────────────────────┘
```

**各环节职责边界：**

| | 查找问题 | 解决问题 | 人工确认 |
|---|---|---|---|
| 修改源文件？ | 否 | 否 | **是** |
| 调用 LLM？ | 是（批量） | 是（逐场景） | 否 |
| 写入 pending_modifies？ | 否 | **是** | 读取并合并 |
| 人工交互？ | 否（可选审阅报告） | 否 | **是** |
| 产出物 | `proofread_issues.json` | `pending_modifies/*.json` | 修改后的场景正文 |

---

## 四、环节一：查找问题 (Find)

### 4.1 整体流程与节点产出物

```
┌─────────────────────────────────────────────────────────────────┐
│ Step 0: 数据加载 (DataLoader)                                    │
├─────────────────────────────────────────────────────────────────┤
│ 输入：workspace_path                                              │
│ 输出：                                                            │
│   ├─ TreeNode 树结构（含所有场景内容）                              │
│   ├─ Character[] 角色列表（含别名映射）                             │
│   ├─ Location[] 地点列表                                          │
│   └─ scene_list: list[SceneMeta]（场景扁平列表，按故事顺序排列）      │
│ 备注：全部为内存对象，不落盘                                         │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 1: 场景预分析 (PreAnalyzer)    ← 【可多线程并行】              │
├─────────────────────────────────────────────────────────────────┤
│ 输入：scene_list                                                   │
│ 对每个场景独立执行：                                                │
│   ├─ 解析 summary → SceneContext（时间/地点/人物/事件）              │
│   ├─ 提取 content 的文本特征（对话段、段落边界）                      │
│   └─ 计算内容 MD5（已有，直接复用 outline_tree 中的 md5 字段）        │
│ 输出（落盘到 proofread_cache/）：                                   │
│   ├─ proofread_cache/manifest.json           ← 缓存版本清单        │
│   ├─ proofread_cache/scene_{scene_id}/context.json                 │
│   └─ proofread_cache/scene_{scene_id}/text_chunks.json             │
│ 缓存策略：若 scene 的 MD5 未变且 context.json 已存在，跳过解析        │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 2: 多 Pass 并行分析    ← 【25 个 Pass 间无依赖，可并发执行】         │
├─────────────────────────────────────────────────────────────────┤
│                                                                     │
│    scene_list ──(扇出)──→ ├─ L1-L4: 人物状态与互动    (4 Pass)      │
│    character_list           │   continuity / appearance             │
│    location_list            │   relationships / extra_characters    │
│    (均为只读共享)             │                                      │
│                              ├─ L5-L8: 时空一致性          (4 Pass)  │
│                              │   timeline / location_reachability   │
│                              │   weather_env / lighting              │
│                              │                                      │
│                              ├─ L9-L11: 物品与信息连续性    (3 Pass)  │
│                              │   prop_continuity / knowledge        │
│                              │   quantity                           │
│                              │                                      │
│                              ├─ L12-L14: 角色设定一致性      (3 Pass) │
│                              │   ability_consistency                │
│                              │   behavior_logic / attribute_values  │
│                              │                                      │
│                              ├─ L15-L17: 叙事技法            (3 Pass) │
│                              │   dialogue_attribution / pov          │
│                              │   narrative_tone                     │
│                              │                                      │
│                              ├─ L18-L20: 跨场景情节漏洞      (3 Pass) │
│                              │   missing_events / causality         │
│                              │   character_count                    │
│                              │                                      │
│                              └─ W1-W5: 书写习惯              (5 Pass) │
│                                  language_style / repetition        │
│                                  sensory_balance / sentence_variety │
│                                  punctuation                         │
│                                                                     │
│ 关键属性：                                                          │
│  • 25 个 Pass 之间无任何数据依赖，可以安全地全并行执行                  │
│  • 各 Pass 只读共享数据，各自写各自的中间文件，无竞态条件               │
│  • 高 LLM 调用 Pass 建议共享速率限制器以控制 API 消耗                   │
│  • 每个场景的 LLM 调用完成后立即写 checkpoint，支持中断恢复              │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 3: 问题聚合 (IssueAggregator)                                │
├─────────────────────────────────────────────────────────────────┤
│ 输入：各 Pass 的 l*_issues.json + w*_issues.json                   │
│ 处理：                                                             │
│   ├─ 合并去重（同一位置同一问题类型只保留一条）                       │
│   ├─ 按严重程度排序：error > warning > info                        │
│   ├─ 按位置排序：章节 → 节 → 场景 → 行号                            │
│   └─ 生成统计摘要                                                  │
│ 输出：系统数据/proofread_issues.json  ← 【环节一的最终产出】         │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 中间产物缓存策略

**目的**：当用户修改部分场景后重新校对时，未变更的场景可以复用上次的分析结果，大幅减少 LLM 调用次数。**缓存与 checkpoint 的关系**：缓存保存的是 Pass 的"已完成终态产物"，而 checkpoint（见 4.3 节）保存的是"当前运行中的进度状态"。一个场景在某个 Pass 上完成后，结果先写入 checkpoint，待该 Pass 全部完成后统一从 checkpoint 提升到 cache。

**缓存目录结构**：

```
系统数据/proofread_cache/
├── manifest.json                    # 缓存清单：{scene_id: {"md5": "xxx", "cached_at": "..."}}
├── scene_{scene_id}/
│   ├── context.json                 # 解析后的 SceneContext
│   ├── text_chunks.json             # 文本分段信息（对话段、叙述段等）
│   ├── l1_states.json               # Pass L1 中间产物：该场景的角色状态快照
│   ├── l2_appearances.json          # Pass L2 中间产物：该场景的实际出场人物
│   ├── l3_dialogues.json            # Pass L3 中间产物：对话提取段
│   ├── l4_names.json                # Pass L4 中间产物：该场景中识别的人物名称
│   ├── l5_timeline.json             # Pass L5 中间产物
│   ├── l6_location.json             # Pass L6 中间产物
│   ├── l7_weather.json              # Pass L7 中间产物
│   ├── l8_lighting.json             # Pass L8 中间产物
│   ├── l9_props.json                # Pass L9 中间产物
│   ├── l10_knowledge.json           # Pass L10 中间产物
│   ├── l11_quantities.json          # Pass L11 中间产物
│   ├── l12_abilities.json           # Pass L12 中间产物
│   ├── l13_emotions.json            # Pass L13 中间产物
│   ├── l14_attributes.json          # Pass L14 中间产物
│   ├── l15_dialogues.json           # Pass L15 中间产物
│   ├── l16_pov.json                 # Pass L16 中间产物
│   ├── l17_tone.json                # Pass L17 中间产物
│   ├── l18_events.json              # Pass L18 中间产物
│   ├── l19_causality.json           # Pass L19 中间产物
│   ├── l20_characters.json          # Pass L20 中间产物
│   └── w1_flags.json                # Pass W1 中间产物：非简体中文标记
└── global/
    ├── w2_freq.json                 # Pass W2 全局中间产物：全文字频统计
    └── w3_sensory.json              # Pass W3 全局中间产物
```

**缓存生效逻辑**：

```
1. 加载 outline_tree.json，获取每个场景的当前 md5
2. 读取 manifest.json，获取上次缓存的 md5
3. 对每个场景：
   ├─ 若 md5 未变 且 某中间产物文件存在 → 跳过该 Pass 对该场景的分析，直接读缓存
   ├─ 若 md5 已变（场景被修改了） → 删除该场景的所有缓存文件，重新分析
   └─ 若缓存文件不存在 → 正常分析并写入缓存
4. Pass L1-L20 按场景粒度缓存：只改了一个场景，只需重新分析那个场景
5. Pass W2/W3 是全局统计：任意场景变更需要重跑全局统计（但无 LLM 调用，成本极低）
```

**关键设计决策**：

- Pass W2/W3 虽然也是全局统计，但产生的是频率数据而非 LLM 输出，因此重跑成本很低，不使用缓存也无妨
- 所有缓存文件的编码均为 UTF-8
- `manifest.json` 在每次完整执行环节一后更新
- **缓存与 checkpoint 生命周期不同**：缓存跨运行持久化，checkpoint 仅在一次运行中有意义

---

### 4.3 运行态检查点系统 (Checkpoint & Resume)

#### 4.3.1 设计目标

校对全流程涉及 200+ 场景 × 25 Pass 的大量 LLM 调用，单次全量运行可能耗时 30-60 分钟。必须支持：

| 能力 | 描述 |
|------|------|
| **自动保存** | 每个场景×Pass 的 LLM 调用完成后，立即将结果持久化到磁盘 |
| **手动中断** | 用户可随时通过 UI 按钮或信号终止运行，不丢失已完成的工作 |
| **断点续跑** | 重新启动后，自动识别已完成的 task 并跳过，从未完成处继续 |
| **失败重试** | 单个 LLM 调用失败不中断整体流程，记录失败后可稍后重试 |
| **增量恢复** | 用户修改了部分源文件后重跑，自动识别哪些 task 需要重新执行 |

#### 4.3.2 两层状态机

整个校对流程的状态管理分为两层：

**第一层：运行层 (Run State)**——描述一次校对会话的整体状态：

```
                  ┌──────────┐
       start_find  │  IDLE    │  start_solve
     ┌───────────→│          │←───────────┐
     │            └─────┬────┘            │
     ▼                  │                 │
┌──────────┐            │          ┌──────────┐
│ FINDING  │   pause    │          │ SOLVING  │
│  (环节一) │←──→│          │  (环节二) │
└────┬─────┘  resume    │          └────┬─────┘
     │                  │               │
     │  complete/error  │               │  complete/error
     ▼                  ▼               ▼
┌──────────┐     ┌──────────┐    ┌──────────┐
│   FIND   │     │  PAUSED  │    │  SOLVE   │
│ COMPLETE │     │          │    │ COMPLETE │
└──────────┘     └──────────┘    └──────────┘
```

**第二层：Task 层 (Task State)**——描述每个 Pass 的每个场景子任务的执行状态：

```
PENDING → RUNNING → COMPLETED
                  → FAILED (可被手动重置为 PENDING 后重试)
                  → SKIPPED (缓存命中或人为跳过)
```

每个 Task 的唯一标识为 `{pass_name}/{scene_id}`。

#### 4.3.3 检查点目录结构

```
系统数据/proofread_checkpoint/
├── run_state.json                  # 运行层总状态
├── tasks/
│   ├── l1_continuity.json          # Pass L1 的任务清单与进度
│   ├── l2_appearance.json          # Pass L2
│   ├── l3_relationships.json       # Pass L3
│   ├── l4_extra_characters.json    # Pass L4
│   ├── l5_timeline.json            # Pass L5
│   ├── l6_location.json            # Pass L6
│   ├── l7_weather.json             # Pass L7
│   ├── l8_lighting.json            # Pass L8
│   ├── l9_props.json               # Pass L9
│   ├── l10_knowledge.json          # Pass L10
│   ├── l11_quantities.json         # Pass L11
│   ├── l12_abilities.json          # Pass L12
│   ├── l13_behavior.json           # Pass L13
│   ├── l14_attributes.json         # Pass L14
│   ├── l15_dialogues.json          # Pass L15
│   ├── l16_pov.json                # Pass L16
│   ├── l17_tone.json               # Pass L17
│   ├── l18_missing_events.json     # Pass L18
│   ├── l19_causality.json          # Pass L19
│   ├── l20_character_count.json    # Pass L20
│   ├── w1_language.json            # Pass W1
│   ├── w2_repetition.json          # Pass W2
│   ├── w3_sensory.json             # Pass W3
│   ├── w4_sentence_variety.json    # Pass W4
│   ├── w5_punctuation.json         # Pass W5
│   └── solve_scenes.json           # 环节二的任务清单
└── llm_responses/
    └── {pass_name}/
        └── {scene_id}.json         # 单次 LLM 调用的完整响应（含原始 JSON）
```

#### 4.3.4 run_state.json 格式

```json
{
    "run_id": "20260508-143021-a3f2",
    "phase": "find",
    "step": "pass_analysis",
    "status": "running",
    "md5_snapshot": {
        "scene_xxx": "9f3a9dd9dd78d04340e2c7143609d905",
        "scene_yyy": "ab9d7bb0fa7eda784b63c4b7ba749dd9"
    },
    "passes": {
        "l1_continuity": {
            "status": "running",
            "scenes_completed": 45,
            "scenes_total": 200,
            "scenes_failed": 0,
            "started_at": "2026-05-08T14:30:22",
            "last_scene_id": "scene_xxx"
        },
        "l2_appearance": {
            "status": "completed",
            "scenes_completed": 200,
            "scenes_total": 200,
            "scenes_failed": 0,
            "started_at": "2026-05-08T14:30:22",
            "completed_at": "2026-05-08T14:35:10"
        },
        "w5_punctuation": {
            "status": "pending",
            "scenes_completed": 0,
            "scenes_total": 200,
            "scenes_failed": 0
        }
    },
    "started_at": "2026-05-08T14:30:21",
    "updated_at": "2026-05-08T14:38:45",
    "llm_calls_total": 127,
    "llm_tokens_total": 456000
}
```

#### 4.3.5 单 Pass 任务清单格式 (tasks/l1_continuity.json)

```json
{
    "pass_name": "l1_continuity",
    "status": "running",
    "scenes": {
        "scene_aef9ec80": {
            "status": "completed",
            "attempts": 1,
            "tokens_used": 3500,
            "issues_found": 0,
            "started_at": "2026-05-08T14:30:23",
            "completed_at": "2026-05-08T14:30:28",
            "llm_response_file": "llm_responses/l1_continuity/scene_aef9ec80.json"
        },
        "scene_1741ee27": {
            "status": "completed",
            "attempts": 1,
            "tokens_used": 4200,
            "issues_found": 2,
            "started_at": "2026-05-08T14:30:28",
            "completed_at": "2026-05-08T14:30:34",
            "llm_response_file": "llm_responses/l1_continuity/scene_1741ee27.json"
        },
        "scene_1977bcc7": {
            "status": "running",
            "attempts": 1,
            "started_at": "2026-05-08T14:30:34"
        },
        "scene_d4e74e2e": {
            "status": "pending",
            "attempts": 0
        }
    },
    "issues": [
        {
            "id": "L1-001",
            "scene_id": "scene_1741ee27",
            "severity": "warning",
            "title": "...",
            "description": "...",
            "location": {...},
            "suggestion": "...",
            "evidence": [...]
        }
    ]
}
```

#### 4.3.6 单次 LLM 响应存储 (llm_responses/l1_continuity/scene_aef9ec80.json)

```json
{
    "task_id": "l1_continuity/scene_aef9ec80",
    "status": "completed",
    "request": {
        "model": "gpt-4o",
        "prompt_hash": "sha256:abc123",
        "input_tokens": 2800
    },
    "response": {
        "output_tokens": 700,
        "raw_json": { "... 完整的 LLM 返回 JSON ..." },
        "finish_reason": "stop",
        "latency_ms": 4200
    },
    "parsed_result": {
        "issues": [...],
        "intermediate_data": {...}
    },
    "timestamp": "2026-05-08T14:30:28"
}
```

#### 4.3.7 自动保存机制

```
自动保存触发时机（按优先级）：
  1. 每个场景的 LLM 调用成功返回并解析完成后 → 立即写 task 状态 + LLM 响应
  2. 每个场景的 LLM 调用失败后 → 立即写 task 状态 (FAILED) + 错误信息
  3. 每个 Pass 全部场景执行完毕后 → 写 Pass summary + 所有 issues
  4. 每 30 秒定时器 → 刷新 run_state.json（作为心跳，防止进程突然崩溃时丢失进度）
  5. 用户手动暂停时 → 立即刷新所有文件

写入策略：
  - 先写 llm_responses/{pass}/{scene_id}.json（LLM 原始响应，最怕丢失）
  - 再写 tasks/{pass_name}.json（task 状态，原子覆盖写入）
  - 最后写 run_state.json（run 状态，原子覆盖写入）
  - 使用"写临时文件 → os.replace()"的方式保证原子性
```

#### 4.3.8 中断机制

```
中断来源：
  a. 用户主动暂停（UI 按钮）→ 设置 stop_event
  b. 连续失败超过阈值 → 自动暂停
  c. 进程被 kill → 依赖心跳恢复（见 4.3.9）

中断处理流程 (Pass 内部):
  def run(self, scene_list, stop_event):
      for scene in self._pending_scenes():
          if stop_event.is_set():
              self._save_checkpoint()
              raise InterruptedError("用户手动暂停")
          result = self._call_llm(scene)
          self._save_scene_result(scene, result)  # 立即写盘

中断处理流程 (Engine 层):
  try:
      engine.run_find(stop_event=stop_event)
  except InterruptedError:
      logger.info("校对已暂停，进度已保存，可稍后继续")
  except Exception:
      logger.error("校对异常中断，进度已保存")

  # 恢复时：
  engine.resume_find()  # 从 checkpoint 读取状态，跳过已完成 task
```

#### 4.3.9 断点续跑 (Resume)

```python
def resume_find(self) -> list[Issue]:
    """
    从 proofread_checkpoint/ 恢复环节一的执行。
    恢复逻辑：
    """
    # 1. 读取 run_state.json
    run_state = self._load_run_state()
    if run_state["phase"] != "find":
        raise ValueError("上次运行不在'查找问题'阶段")

    # 2. 校验 md5 快照
    current_md5s = self._get_current_scene_md5s()
    for scene_id, old_md5 in run_state["md5_snapshot"].items():
        if current_md5s.get(scene_id) != old_md5:
            # 场景被修改了，该场景在所有 Pass 中的结果都作废
            run_state["md5_snapshot"][scene_id] = current_md5s[scene_id]
            self._invalidate_scene_across_all_passes(scene_id)

    # 3. 读取每个 Pass 的 task 清单
    for pass_name, pass_state in run_state["passes"].items():
        if pass_state["status"] == "completed":
            continue  # 跳过已完成的 Pass
        # 加载 tasks/{pass_name}.json，跳过 status="completed" 的场景
        # 从第一个 status="pending" 或 "running" 的场景开始执行

    # 4. 恢复各 Pass 的执行（使用上次的 stop_event）
    self._resume_pass_analysis(run_state)
```

#### 4.3.10 检查点生命周期

```
创建: 环节一 Step 2 开始执行时
  │
更新: 每个场景的 LLM 调用完成/失败时
  │
暂停: 用户中断 → run_state.status = "paused"
  │
恢复: 用户继续 → run_state.status = "running" → 跳过已完成 task
  │
完成: 环节一全部 Pass 完成 → 将中间产物提升到 proofread_cache/
  │     检查点数据可选择性保留（供审计/调试）或清理
  │
清理: 用户确认"放弃本次校对" → 删除整个 proofread_checkpoint/ 目录
  │     或：新一轮全量校对开始时 → 自动清理旧 checkpoint
```

#### 4.3.11 环节二的检查点

环节二（解决问题）的检查点结构与环节一类似，差异在于：
- 每个 task 是 `{node_id}`（场景修复），而非 `{pass}/{scene_id}`
- 写入 `tasks/solve_scenes.json`
- LLM 响应保存了修复后的全文，体积较大，需注意磁盘空间
- 环节二的恢复会校验 `pending_modifies/{node_id}.json` 是否已存在（从步骤2写入的），避免重复生成

#### 4.3.12 与缓存系统的分工对比

| 维度 | proofread_cache/ | proofread_checkpoint/ |
|------|-----------------|----------------------|
| 存储内容 | Pass 完成的"终态产物"（解析后的结构化数据） | Task 状态 + LLM 原始响应 + 运行元信息 |
| 生命周期 | 跨运行持久化，直到场景源文件变更 | 单次运行，运行完成后可清理 |
| 用途 | 避免重复分析未变更的场景 | 支持中断恢复、失败重试、审计调试 |
| 粒度 | 每场景×Pass 的最终解析结果 | 每次都含 LLM 原始 JSON + 解析中间态 |
| 依赖 | 场景 MD5 | 场景 MD5 + run_id + task 状态机 |
| 读写频率 | 每个 Pass 完成后写入一次 | 每次 LLM 调用完成即写入 |
| 能否被用户手动清理 | 可以（下次自动重建） | 可以（会丢失当前进度） |

---

## 五、环节二：解决问题 (Solve)

### 5.1 整体流程与节点产出物

```
┌─────────────────────────────────────────────────────────────────┐
│ Step 1: 按场景归并问题                                            │
├─────────────────────────────────────────────────────────────────┤
│ 输入：proofread_issues.json                                       │
│ 处理：                                                             │
│   ├─ 按 scene_id 分组所有 issue                                    │
│   ├─ 排除 severity=info 的问题（仅提示，不自动修复）                  │
│   ├─ 同一场景的多个 issue 合并为一个修复请求上下文                    │
│   └─ 过滤掉用户标记为"忽略"的问题                                   │
│ 输出：{scene_id: [Issue]} 的映射                                   │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 2: 逐场景生成修复    ← 【各场景间无依赖，可多线程并行】          │
├─────────────────────────────────────────────────────────────────┤
│ 输入：每个有问题的场景：(scene_id, content, issues[])                │
│ 对每个场景：                                                       │
│   ├─ 构建修复 prompt（含原文 + 问题清单 + 修改指引）                   │
│   ├─ 调用 LLM 生成修复后的全文                                      │
│   └─ 写入 pending_modifies/{node_id}.json                         │
│                                                                     │
│ pending_modifies 文件格式（复用现有格式）：                          │
│   {                                                                 │
│     "node_id": "xxx",                                               │
│     "original_text": "原始场景正文",                                 │
│     "modified_text": "修复后的场景正文",                             │
│     "request_prompt": "修复请求描述（含问题列表）"                    │
│   }                                                                 │
│                                                                     │
│ 输出：系统数据/pending_modifies/{node_id}.json  ← 【环节二的最终产出】│
│                                                                     │
│ 关键属性：                                                          │
│  • 各场景的修复完全独立，可以安全地并行调用 LLM                         │
│  • 写入的是 pending_modifies，不修改源文件                           │
│  • 文件名使用 node_id，与现有 diff 合并系统完全兼容                    │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 修复 Prompt 结构

```
你是专业的小说校对编辑。以下是需要修正的场景内容和发现的问题。

## 角色设定参考
{所有出场角色的设定 JSON}

## 原文
{场景正文内容}

## 发现的问题（请逐一修正）
1. [L1-错误] 朱岚脚伤状态矛盾：场景5中脚被烫伤不能站立，
   但本文中"站起身快步走过去"，中间无恢复交代。

2. [L3-警告] 称呼问题：朱岚与汶晴是挚友关系，
   但文中汶晴称呼"朱岚同学"，应改为更亲近的称呼。

...

## 修改要求
1. 修改所有上述问题，保持原文整体结构和剧情不变。
2. 输出完整的修改后正文，不要省略任何段落。
3. 不要添加任何解释或注释，只输出纯正文。
```

### 5.3 与环节一的关系

- **环节二在环节一完全结束后才能开始**（需要完整的问题清单）
- 环节二是**可选的**：用户可以审阅 `proofread_issues.json` 后决定哪些问题需要自动修复
- 支持**按问题类型选择修复**：例如只修复 L1/L2，不修复 W1/W2
- 支持**按章节选择修复**：例如只修复第一章的场景

---

## 六、环节三：人工确认 Diff (Confirm)

### 6.1 设计原则：完全复用现有系统

现有的 diff 合并流程已经非常成熟，环节三直接复用：

| 现有组件 | 路径 | 作用 |
|---------|------|------|
| `PendingModifyManager` | `ui/diff_merge_dialog.py` | 管理 `pending_modifies/` 目录的读写（可选复用） |
| `DiffMergeDialog` | `ui/diff_merge_dialog.py` | 并排差异对比 + 逐差异点勾选 |
| `open_merge_dialog()` | `ui/mixins/novel_tree_mixin.py` | 打开合并窗口的入口 |

### 6.2 用户操作流程

```
环节二完成
  │
  │  已在 系统数据/pending_modifies/ 下生成了一批 {node_id}.json
  │
  ▼
┌─────────────────────────────────────────────────────────────────┐
│ 用户在 UI 中看到提示："校对修复完成，共 12 个场景有待确认的修改。"    │
├─────────────────────────────────────────────────────────────────┤
│                                                                     │
│  方式 A：在故事树中逐个点击挂有 pending 标记的场景节点               │
│          → 右键 → "查看校对 Diff"                                   │
│          → 弹出 DiffMergeDialog（左侧原文 / 右侧修复版）              │
│          → 逐差异点勾选/取消                                        │
│          → 点击"接受"写入最终正文                                   │
│                                                                     │
│  方式 B：通过新增"校对结果面板"统一浏览                              │
│          → 列表展示所有有待确认修改的场景                             │
│          → 点击任一场景打开 DiffMergeDialog                          │
│          → 支持"全部接受"快捷操作                                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 技术实现要点

- 环节二写入的 `pending_modifies/{node_id}.json` 与现有的 LLM 生成改写（重写/扩写/缩写）使用**完全相同的文件格式**
- `open_merge_dialog()` 方法按 `node_id` 查找 pending_modify，找到后弹出 `DiffMergeDialog`
- 用户确认后，`apply_merge_result()` 将合并后的文本写回 `正文/` 目录
- 这意味着**不需要为校对系统新增任何 UI 代码**（方式 B 的"校对结果面板"是可选的增强）

### 6.4 pending_modifies 生命周期

```
创建: 环节二写入
  │
使用: 环节三打开 DiffMergeDialog 读取
  │
清理: 用户点击"接受"后，apply_merge_result() 会删除 pending 文件
      (现有逻辑已实现)
  │  或：用户点击"拒绝"后，可手动清除（建议新增）
  │
异常: 若 outline_tree 中对应节点的 md5 已变更（用户在环节二期间自行修改了源文件），
      则 Diff 将基于过时的 original_text 生成，需要在打开前给出警告提示
```

---

## 七、多线程配置设计

### 7.1 依赖分析

```
环节一 Step 2 的各 Pass 之间的依赖关系：

    L1  (穿戴状态)   ─── 无依赖 ───┐
    L2  (角色登场)   ─── 无依赖 ───┤
    L3  (关系称呼)   ─── 无依赖 ───┤
    L4  (额外角色)   ─── 无依赖 ───┤
    L5  (时间线)    ─── 无依赖 ───┤
    L6  (地点可达性) ─── 无依赖 ───┤
    L7  (天气环境)   ─── 无依赖 ───┼── 全并行安全
    L8  (光照)      ─── 无依赖 ───┤
    L9  (物品道具)   ─── 无依赖 ───┤
    L10 (角色知识)   ─── 无依赖 ───┤
    L11 (数字数量)   ─── 无依赖 ───┤
    L12 (角色能力)   ─── 无依赖 ───┤
    L13 (行为逻辑)   ─── 无依赖 ───┤
    L14 (属性数值)   ─── 无依赖 ───┤
    L15 (对话归属)   ─── 无依赖 ───┤
    L16 (POV视角)   ─── 无依赖 ───┤
    L17 (叙述语气)   ─── 无依赖 ───┤
    L18 (事件缺失)   ─── 无依赖 ───┤
    L19 (因果断裂)   ─── 无依赖 ───┤
    L20 (数量突变)   ─── 无依赖 ───┤
    W1  (语言习惯)   ─── 无依赖 ───┤
    W2  (特征词重复) ─── 无依赖 ───┤
    W3  (感官描写)   ─── 无依赖 ───┤
    W4  (句式结构)   ─── 无依赖 ───┤
    W5  (标点规范)   ─── 无依赖 ───┘

    共享数据（只读）: scene_list, character_list, location_list
    各自写入（无冲突）: proofread_cache/ 下各自的子目录

结论：全部 Pass 之间零依赖，可在独立线程中并行执行，零竞态条件。
     建议按 LLM 调用量分组调度：高 LLM 依赖的 Pass 共享一个 LLM 速率限制器。
```

```
环节二 Step 2 的场景修复之间的依赖关系：

    scene_A ─── 无依赖 ───┐
    scene_B ─── 无依赖 ───┤
    scene_C ─── 无依赖 ───┼── 全并行安全
    scene_D ─── 无依赖 ───┤
    ...                    │

    共享数据（只读）: 无（每个场景携带自己的 issue 清单）
    各自写入（无冲突）: pending_modifies/{scene_id}.json（不同文件名）

结论：各场景修复完全独立，可以安全并行。
```

### 7.2 配置项

在工程配置（如 `setting.json` 或 workspace 配置中）增加以下配置段：

```json
{
    "proofread": {
        "parallel_passes": 4,
        "parallel_solves": 3,
        "cache_enabled": true
    }
}
```

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|-------|------|
| `parallel_passes` | int | 6 | 环节一 Step 2 中最多同时执行的 Pass 数量（1~25） |
| `parallel_solves` | int | 3 | 环节二 Step 2 中最多同时修复的场景数量（1~N） |
| `cache_enabled` | bool | true | 是否启用中间产物缓存 |

- `parallel_passes=1` 表示 Pass 串行执行，适合调试或 LLM 配额紧张时
- `parallel_solves=1` 表示逐场景依次修复，适合需要精确定位修复问题的场景
- 注意：并行度越高，LLM 速率限制的风险越大。推荐总值控制在 API 的 RPM（每分钟请求数）限制之内
- 建议高 LLM 调用量的 Pass（L1-L4, L9-L10, L12-L13, L18-L19）使用共享的 LLM 速率限制器，避免同时触发过多 API 请求

### 7.3 执行模式

使用 `concurrent.futures.ThreadPoolExecutor` 或 `QThreadPool` 执行：

```python
# 伪代码：环节一 Step 2 的并行调度
with ThreadPoolExecutor(max_workers=config.parallel_passes) as executor:
    futures = {
        executor.submit(pass_instance.run, scene_list, char_list): pass_name
        for pass_name, pass_instance in passes.items()
    }
    for future in as_completed(futures):
        result = future.result()
        # 收集该 Pass 的问题列表
```

---

## 八、模块结构

```
modules/proofread/
├── __init__.py                  # 模块入口
├── DESIGN.md                    # 本文档
│
├── loader.py                    # 数据加载层
│                                #   DataLoader.load_all(workspace_path) → WorkspaceData
│                                #   复用 WorkspaceManager 读取 outline_tree + 设定
│
├── pre_analyzer.py              # 场景预分析（环节一 Step 1）
│                                #   解析 summary → SceneContext
│                                #   提取文本结构（对话段、段落边界）
│                                #   管理缓存读写
│
├── engine.py                    # 校对引擎（三环节编排调度 + 中断/恢复）
│                                #   ProofreadEngine.run_find(stop_event)  → 环节一
│                                #   ProofreadEngine.resume_find()         → 恢复环节一
│                                #   ProofreadEngine.run_solve(stop_event) → 环节二
│                                #   ProofreadEngine.resume_solve()        → 恢复环节二
│                                #   ProofreadEngine.pause()               → 暂停并保存
│                                #   内部持有 stop_event (threading.Event)
│
├── cache_manager.py             # 中间产物缓存管理
│                                #   读写 manifest.json
│                                #   按场景 MD5 判断缓存有效性
│
├── checkpoint_manager.py        # 检查点管理（中断/恢复的核心）
│                                #   CheckpointManager.init_run()     → 创建新运行
│                                #   CheckpointManager.load_state()   → 读取运行状态
│                                #   CheckpointManager.save_task()    → 保存单个 task 结果
│                                #   CheckpointManager.save_llm_response() → 保存 LLM 原始响应
│                                #   CheckpointManager.get_pending_scenes() → 获取待处理场景
│                                #   CheckpointManager.promote_to_cache() → 提升到缓存
│                                #   CheckpointManager.cleanup()      → 清理检查点
│
├── issue_aggregator.py          # 问题聚合器（环节一 Step 3）
│                                #   合并去重排序 → proofread_issues.json
│
├── solve_engine.py              # 修复引擎（环节二核心）
│                                #   按场景构建修复 prompt
│                                #   调用 LLM → 写入 pending_modifies
│
├── passes/
│   ├── __init__.py
│   ├── base.py                  # Pass 基类
│   │                            #   def run(scene_list, char_list) → (issues, intermediates)
│   │                            #   def _call_llm(prompt_template, **vars) → JSON
│   ├── continuity.py            # L1: 穿戴/状态连续性
│   ├── appearance.py            # L2: 角色登场/退场
│   ├── relationships.py         # L3: 关系与称呼
│   ├── extra_characters.py      # L4: 额外角色检测
│   ├── timeline.py              # L5: 时间线逻辑
│   ├── location_reachability.py # L6: 地点可达性
│   ├── weather_env.py           # L7: 天气/环境一致性
│   ├── lighting.py              # L8: 光照一致性
│   ├── prop_continuity.py       # L9: 物品/道具连续性
│   ├── character_knowledge.py   # L10: 角色知识矛盾
│   ├── quantity.py              # L11: 数字/数量矛盾
│   ├── ability_consistency.py   # L12: 角色能力一致性
│   ├── behavior_logic.py        # L13: 角色行为逻辑
│   ├── attribute_values.py      # L14: 角色属性数值
│   ├── dialogue_attribution.py  # L15: 对话归属
│   ├── pov_consistency.py       # L16: POV视角一致性
│   ├── narrative_tone.py        # L17: 叙述语气
│   ├── missing_events.py        # L18: 关键事件缺失
│   ├── causality.py             # L19: 因果链断裂
│   ├── character_count.py       # L20: 角色数量突变
│   ├── language_style.py        # W1: 简体中文习惯
│   ├── repetition.py            # W2: 特征词过度重复
│   ├── sensory_balance.py       # W3: 感官描写平衡
│   ├── sentence_variety.py      # W4: 句式结构多样性
│   └── punctuation.py           # W5: 标点规范
│
├── reporters/
│   ├── __init__.py
│   └── issue_reporter.py        # 问题报告输出（JSON / Markdown / 终端摘要）
│
├── prompts/
│   ├── continuity_extract.txt   # L1: 状态提取 prompt
│   ├── continuity_check.txt     # L1: 连续性对比 prompt
│   ├── appearance_check.txt     # L2: 登场检查 prompt
│   ├── relationship_check.txt   # L3: 关系称呼 prompt
│   ├── extra_character_check.txt# L4: 额外角色 prompt
│   ├── timeline_check.txt       # L5: 时间线检查 prompt
│   ├── location_reachability.txt# L6: 地点可达性 prompt
│   ├── weather_check.txt        # L7: 天气环境 prompt
│   ├── lighting_check.txt       # L8: 光照检查 prompt
│   ├── prop_check.txt           # L9: 物品连续性 prompt
│   ├── knowledge_check.txt      # L10: 角色知识 prompt
│   ├── quantity_check.txt       # L11: 数字数量 prompt
│   ├── ability_check.txt        # L12: 能力一致性 prompt
│   ├── behavior_check.txt       # L13: 行为逻辑 prompt
│   ├── attribute_check.txt      # L14: 属性数值 prompt
│   ├── dialogue_check.txt       # L15: 对话归属 prompt
│   ├── pov_check.txt            # L16: POV视角 prompt
│   ├── tone_check.txt           # L17: 叙述语气 prompt
│   ├── missing_event_check.txt  # L18: 事件缺失 prompt
│   ├── causality_check.txt      # L19: 因果链 prompt
│   ├── character_count_check.txt# L20: 角色数量 prompt
│   ├── language_style_check.txt # W1: 语言习惯 prompt
│   ├── sensory_balance.txt      # W3: 感官描写 prompt
│   └── solve_fix.txt            # 环节二: 修复 prompt
│
└── proofread_config.py          # 校对配置管理（读/写 parallel_passes 等）
```

---

## 九、核心数据模型

### 9.1 数据结构

```python
@dataclass
class WorkspaceData:
    """环节一 Step 0 产出：工程全量数据"""
    project_name: str
    root_nodes: list[TreeNode]
    scene_list: list[SceneMeta]           # 按故事顺序的扁平场景列表
    characters: list[Character]
    locations: list[Location]
    character_alias_map: dict[str, str]   # "高个子" → "顾承骁"

@dataclass
class SceneMeta:
    """单个场景的元信息和内容"""
    node_id: str
    chapter_title: str
    section_title: str
    scene_title: str
    file_path: str
    content_md5: str                      # 从 outline_tree 继承
    summary_raw: str                      # 原始 summary 文本
    content_raw: str                      # 正文内容
    context: 'SceneContext | None'        # 预分析后填充

@dataclass
class SceneContext:
    """从 summary 解析的结构化场景上下文"""
    time_desc: str
    locations: list[str]
    characters_declared: list[str]        # 【人物】字段中声明的角色
    events: list[str]                     # 事件条目列表
    summary_narrative: str                # 【剧情总结】字段

@dataclass
class Issue:
    """单个校对问题"""
    id: str                               # 如 "L1-003"
    category: str                         # L1/L2/L3/L4/W1/W2
    pass_name: str                        # 如 "continuity"
    severity: str                         # error / warning / info
    title: str
    description: str
    location: IssueLocation
    suggestion: str                       # 修复建议（给 LLM 用的指引）
    evidence: list[str]                   # 原文引用
    ignored: bool = False                 # 用户标记为忽略

@dataclass
class IssueLocation:
    node_id: str
    chapter_title: str
    section_title: str
    scene_title: str
    file_path: str
    line_numbers: list[int]
```

### 9.2 中间产物类型

```python
@dataclass
class StateSnapshot:
    """Pass L1 中间产物：单角色在单场景结束时的状态"""
    scene_id: str
    character_name: str
    attire: dict[str, str]               # {"鞋子": "运动鞋", "上衣": "白色衬衫"}
    body_state: dict[str, str]            # {"左脚": "烟头烫伤起泡", "双手": "被绳索捆绑"}
    possessions: list[str]               # 持有的物品

@dataclass
class AppearanceRecord:
    """Pass L2 中间产物：角色在单场景中的出场记录"""
    scene_id: str
    character_name: str
    presence_type: str                    # "active"/"mentioned"/"dialogue_only"
    entry_line: int                       # 首次出现的行号

@dataclass
class DialogueSegment:
    """Pass L3 中间产物：单个对话段"""
    scene_id: str
    line_start: int
    line_end: int
    speaker_a: str
    speaker_b: str
    raw_text: str
    address_terms: list[str]              # 称呼用词列表

@dataclass
class CacheManifest:
    """manifest.json 的结构"""
    project_name: str
    last_full_run: str                    # ISO datetime
    scenes: dict[str, SceneCacheEntry]    # scene_id → entry

@dataclass
class SceneCacheEntry:
    md5: str
    cached_at: str                        # ISO datetime
    available_passes: list[str]           # 哪些 Pass 的中间产物已缓存

# ── 检查点相关类型 ──

@dataclass
class RunState:
    """run_state.json 的结构：一次校对会话的全局状态"""
    run_id: str
    phase: str                            # "find" | "solve"
    step: str                             # 当前子步骤
    status: str                           # "idle" | "running" | "paused" | "completed" | "error"
    md5_snapshot: dict[str, str]          # scene_id → md5 运行开始时的快照
    passes: dict[str, 'PassProgress']     # pass_name → 进度
    started_at: str
    updated_at: str
    llm_calls_total: int
    llm_tokens_total: int

@dataclass
class PassProgress:
    """单个 Pass 的运行进度"""
    pass_name: str
    status: str                            # "pending" | "running" | "completed" | "failed"
    scenes_completed: int
    scenes_total: int
    scenes_failed: int
    started_at: str | None
    completed_at: str | None
    last_scene_id: str | None

@dataclass
class TaskState:
    """单个 {pass}/{scene} 任务的执行状态"""
    task_id: str                           # "l1_continuity/scene_aef9ec80"
    pass_name: str
    scene_id: str
    status: str                            # "pending" | "running" | "completed" | "failed" | "skipped"
    attempts: int
    tokens_used: int
    issues_found: int
    started_at: str | None
    completed_at: str | None
    llm_response_file: str | None          # 指向 llm_responses/ 下的文件路径
    error_message: str | None              # 失败时的错误信息

@dataclass
class LLMResponseRecord:
    """单次 LLM 调用的完整记录"""
    task_id: str
    status: str
    request: dict                          # {"model": "gpt-4o", "prompt_hash": "sha256:xxx", "input_tokens": 2800}
    response: dict                         # {"output_tokens": 700, "raw_json": {...}, "finish_reason": "stop", "latency_ms": 4200}
    parsed_result: dict | None             # {"issues": [...], "intermediate_data": {...}}
    timestamp: str
```

---

## 十、各 Pass 详细设计

### 10.1 Pass L1：状态连续性检查 (`continuity.py`)

**两步法**：先提取状态（缓存中间产物），再对比检查。

```
Step 1: 状态提取（每场景独立，结果缓存到 l1_states.json）
  输入：场景正文 + 人物设定
  用 LLM 从正文提取每个出场角色在本场景中的状态变化序列：
    [
      { "角色": "朱岚", "初始状态": {"脚": "有旧烫伤"}, "状态变化": [
          {"触发事件": "站起身", "变更": {"脚": "忍痛站立"}}
      ], "结束状态": {"脚": "忍痛站立", "手上": "空"} }
    ]

Step 2: 连续性检查（比较相邻场景的结束状态→起始状态）
  对每个角色：
    遍历其出现的场景序列：
      若 scene_N 结束状态.脚 ≡ "严重烫伤无法着地"
      且 scene_N+1 中"大步流星走在前面"但中间无恢复事件
      → 报告 L1 问题
```

### 10.2 Pass L2：角色登场检查 (`appearance.py`)

```
Step 1: 提取每场景实际出场人物（缓存到 l2_appearances.json）
  LLM 输入：场景正文 + 人物列表（含别名）
  LLM 输出：实际出现的角色及出场方式

Step 2: 交叉比对
  对每个场景：
    声明列表 = SceneContext.characters_declared
    实际列表 = l2_appearances 提取结果
    实际但有互动但未声明 → 报告 L2 问题
    声明但完全未出现 → 报告 L2 问题（人物被设计了但没写）
```

### 10.3 Pass L3：关系称呼检查 (`relationships.py`)

```
Step 1: 构建关系矩阵（从人物设定，纯本地计算）
  { ("汶晴", "朱岚"): {"关系": "挚友室友", "预期称呼": ["朱岚", "小岚", "岚岚"]} }

Step 2: 提取对话段（缓存到 l3_dialogues.json）
  按正则匹配引号对话，提取说话人和听话人

Step 3: LLM 逐段检查
  输入：对话段 + 对话双方的关系设定
  输出：称呼是否恰当，不恰当时给出建议
```

### 10.4 Pass L4：额外角色检测 (`extra_characters.py`)

```
Step 1: 扫描每场景识别人名（本地 NER + LLM 辅助，缓存到 l4_names.json）

Step 2: 与人物设定集合求差
  额外角色池 = 识别的人名 - 人物设定中的合法名称（含别名）

Step 3: LLM 分类
  对每个额外角色，汇总其所有出场段落，让 LLM 判断：
    - "功能性龙套"（收银员）→ info
    - "需补设定的配角"（有对话有影响）→ warning
    - "突兀角色"（不合逻辑出现消失）→ error
```

### 10.5 Pass W1：简体中文检查 (`language_style.py`)

**规则优先，几乎不消耗 LLM token**。

```
Step 1: 规则层扫描（本地正则，100% 可缓存）
  维护一份映射表：
    繁简映射: {"透過": "通过", "螢幕": "屏幕", "爲": "为", ...}
    港台用语: {"的而且確": "(粤语)", "埋單": "结账", ...}
  全文扫描标记命中位置

Step 2: LLM 复核（仅对歧义情况）
  仅在输出为 info/warning 且用户要求复核时才调用 LLM
```

### 10.6 Pass W2：特征词重复检查 (`repetition.py`)

**规则优先，核心计算完全本地**。

```
Step 1: 特征词提取（从人物设定自动提取，本地）
  例如从汶晴.json 提取: ["杏眼", "乌黑如瀑", "小家碧玉", "怯生生", "纤细娇小"]

Step 2: 全文频率统计（本地，结果缓存到 w2_freq.json）
  精确匹配：每个特征短语在全文中出现的次数和行号
  语义聚类："170cm""一百七十公分""一米七"归为同一概念

Step 3: 判断（规则）
  同一短语出现 >10 次且分散在 ≥5 个不同场景 → 报告 W2 问题

Step 4: LLM 复核（可选）
  输入：高频短语 + 每次出现的上下文
  输出：判断是否构成过度重复 + 替代表达建议
```

---

### 10.7 Pass L5：时间线逻辑检查 (`timeline.py`)

**混合方法**：结构化解析 + LLM 交叉验证。

```
Step 1: 时间线数据提取（从各场景的 summary + 正文，缓存到 l5_timeline.json）
  对每个场景提取：
    - 绝对时间标记（如 "第12天清晨6:00"、"十月初"、"周六"）
    - 相对时间标记（如 "五分钟后"、"紧接上一场景"）
    - 正文中隐含的时间流逝线索（如 "天边泛起鱼肚白"→凌晨、"午后的阳光"→下午）
  用 LLM 将上述非结构化时间表达归一化到"故事时间轴"上的位置

Step 2: 时间线矛盾检测
  a. 流速矛盾：
     两个相邻场景，summary 声明时间间隔为"紧接上一场景"（几分钟），
     但正文中一场景发生了数小时的剧情 → 报告 L5
  b. 顺序反转：
     scene_N 的归一化时间在 scene_N-1 之后，但与 summary 中声明的先后顺序不一致
  c. 跨度矛盾：
     跨场景时间段 vs 场景数量：两个场景之间声明"三天后"，但中间没有过渡场景，
     且相邻场景中角色的状态没有三天应有的变化

Step 3: LLM 复核
  输入：可疑场景对 + 两者的时间标记 + 正文关键段落
  输出：确认是否确实存在时间线矛盾 + 具体证据
```

---

### 10.8 Pass L6：地点可达性检查 (`location_reachability.py`)

**规则驱动为主，LLM 辅助**。

```
Step 1: 构建地点连通图（从地点设定文件，纯本地计算）
  根据位置设定 JSON 中的"连接地点"字段 + "音乐学院.json"的分区结构，
  构建无向图：节点=地点名称，边=连通关系
  同时标注每个分区的可达性（如"后山可达教学区"）

Step 2: 提取场景地点转移轨迹
  从 SceneContext.locations 提取每个场景的地点，
  串联相邻场景形成转移序列（如 208宿舍→图书馆→音乐练习楼→208宿舍）

Step 3: 可达性检查
  对相邻场景的地点对 (A→B)：
    若 A 和 B 在连通图中既无直接边，也无两步之内的路径 → 报告 L6
    若 A→B 需要经过中间地点 C，但正文中没有经过 C 的描写 → 报告 L6

  特别规则：
    - 同一场景内的地点转移（如 summary 中有 "→" 箭头）也纳入检查
    - 允许"跳场"（时间跳跃导致的场景切换不要求地点可达）

Step 4: LLM 复核
  输入：可疑的地点转移 + 两地的设定文件 + 正文中可能的路程描写
  输出：确认是否确实不可达，或正文中是否有隐含的过渡描写（如"她们打了个车"）
```

---

### 10.9 Pass L7：天气/环境一致性检查 (`weather_env.py`)

**LLM 驱动**。

```
Step 1: 环境状态提取（缓存到 l7_weather.json）
  对每个场景，用 LLM 逐场景提取：
    - 天气条件（雨/晴/阴/雪/风）
    - 地面状态（干燥/泥泞/积水/积雪）
    - 气温体感（炎热/温暖/凉爽/寒冷）
    - 空气特征（潮湿/干燥/有雾/有烟尘）

Step 2: 连续性比对
  对相邻场景（尤其是有"紧接上一场景"标记的）：
    若 scene_N 结束天气:"大雨滂沱，地面泥泞"，scene_N+1 中"尘土飞扬的干燥路面"
    且两个场景之间无天气转晴/时间跳跃的交代 → 报告 L7

Step 3: 场景内部检查
  同一场景内（尤其是声明时间跨度极短的场景）：
    若开头"暴雨倾盆"，末尾"星光灿烂"且中间无天气变化描写 → 报告 L7
```

---

### 10.10 Pass L8：光照一致性检查 (`lighting.py`)

**LLM 驱动，结合时间标记交叉验证**。

```
Step 1: 光照条件提取（缓存到 l8_lighting.json）
  用 LLM 从正文提取每个场景或场景内部段落的光照来源和强度：
    - 光源类型（阳光/月光/灯光/无光源）
    - 能见度（清晰/模糊/黑暗）
    - 与时间标记的匹配度

Step 2: 光照-时间矛盾检测
  对每个场景：
    若时间标记为"深夜十二点"且室内场景，但出现"阳光从窗帘缝隙射入" → error
    若时间标记为"正午"但描写"黑暗得伸手不见五指"且无遮光交代 → warning
    若时间标记为"傍晚"且室内，灯未开但角色"看清了角落里的每个细节" → warning

Step 3: LLM 复核（对所有可疑匹配）
  输入：时间标记 + 光照描写段 + 合理的遮光/光源解释
  输出：是否存在矛盾
```

---

### 10.11 Pass L9：物品/道具连续性检查 (`prop_continuity.py`)

**LLM 驱动**。

```
Step 1: 物品追踪提取（缓存到 l9_props.json）
  对每个场景，LLM 识别：
    - 重要物品的出现、使用、离开视线
    - 标注每个物品的操作：获得/持有/放下/破坏/转移/丢失

Step 2: 物品生命线对比
  对每个识别的物品，构建其"出现→使用→归宿"序列：
    若物品A在scene_N被使用但从未被放下/交代归宿 → 标记悬而未决
    若物品A在scene_N"掉在地上"，scene_N+1角色从口袋掏出使用 → 报告 L9
    若物品A在scene_N已经"彻底毁坏"，scene_N+5中又被使用且无修复交代 → 报告 L9

Step 3: 跨场景约束
  - 同一角色在连续场景中持有的物品清单应保持连续性
  - 若某物品在A角色手中消失后在B角色手中出现，应有转移描写
```

---

### 10.12 Pass L10：角色知识一致性检查 (`character_knowledge.py`)

**LLM 驱动，需理解角色的信息获取渠道**。

```
Step 1: 信息获取事件提取
  遍历所有场景，识别每个角色获取信息的时刻：
    - 亲历事件（目击/参与/听到）
    - 被他人告知（对话中获悉）
    - 发现线索（阅读/观察）

Step 2: 角色知识库构建
  为每个角色构建一个"已知事实集合"：
    汶晴: { "矮个子叫黑子"(场景1获悉), "老疤是顾承骁"(场景?未获悉) }

Step 3: 矛盾检测
  a. 角色知道不该知道的信息：
     角色A引用了某事实，但回顾前文，角色A从未有渠道获取该信息 → 报告 L10
  b. 角色遗忘了已知信息：
     角色A在scene_N知道了关键信息X，
     在scene_N+5中对X表示惊讶/不知情 → 报告 L10
  c. 信息泄露：
     角色A告诉了B某信息，但C在未经B告知的情况下也知道了 → 报告 L10
```

---

### 10.13 Pass L11：数字/数量一致性检查 (`quantity.py`)

**规则 + LLM 混合**。

```
Step 1: 数值实体提取（缓存到 l11_quantities.json）
  LLM 从每个场景提取数值实体：
    - 人数: "两个歹徒前后夹击" → 歹徒=2
    - 年龄: "二十岁的汶晴" → 汶晴年龄=20
    - 距离/时间长度/金额等

Step 2: 规则层比对
  a. 同一场景内数量突变：
     "两个人围上去" → 后文 "三个人轮流..." → 报告 L11
  b. 相邻场景数量不对齐：
     scene_N: "剩下三个苹果"，scene_N+1: "她把五个苹果装进包里" → 报告 L11

Step 3: LLM 复核
  输入：可疑的数字实体 + 上下文
  输出：确认是否存在矛盾，排除"第三者加入"等合理情况
```

---

### 10.14 Pass L12：角色能力一致性检查 (`ability_consistency.py`)

**LLM 驱动，需对照人物设定**。

```
Step 1: 能力档案提取（从人物设定，纯本地）
  汶晴: {特长: "小提琴演奏(国际大赛级别)", "钢琴辅修"}
  朱岚: {特长: "指挥专业", 背景: "乡下果园家庭，体力好"}

Step 2: 能力表现提取（缓存到 l12_abilities.json）
  LLM 从正文识别每个角色在各场景中表现出的能力水平

Step 3: 矛盾检测
  a. 设定能力与表现不符：
     设定中"指挥系高材生"，场景中连基本节拍都打错 → 报告 L12
  b. 前文能力与后文能力矛盾：
     scene_N中角色展示了高超的某技能，scene_N+5中却对该技能的基础操作手足无措
  c. 体力/伤势状态与表现不符：
     角色刚经历长时间虐待体虚，场景中却完成了需要极强体力的动作 → 报告 L12
     （此类与 L1 可能重叠，去重时保留 L1）
```

---

### 10.15 Pass L13：角色行为逻辑断裂检查 (`behavior_logic.py`)

**LLM 驱动，全局理解角色弧**。

```
Step 1: 情感状态轨迹提取（缓存到 l13_emotions.json）
  对每个角色，LLM 逐场景提取其情感/心理状态标签：
    [scene_1: "恐惧、抵抗", scene_2: "崩溃", scene_3: "麻木",
     scene_4: "绝望", scene_5: "策划逃跑(坚定)"]

Step 2: 情感跳跃检测
  检测相邻场景间的情感跳跃是否合理：
    若"完全崩溃，瘫软在地" 立即跳跃到 "目光坚定地策划" 且无任何过渡描写 → 报告 L13
    若情绪在极短时间内剧烈摆动（恐惧→愤怒→冷静→恐惧）且无充分触发事件 → 报告 L13

Step 3: 动机一致性
  检查角色在连续场景中的行为是否与已建立的动机一致：
    角色一直想逃离某人，但有机会时不逃反而主动接近 → 报告 L13 (除非有新的动机建立)
```

---

### 10.16 Pass L14：角色属性数值检查 (`attribute_values.py`)

**规则驱动 + LLM 辅助**。

```
Step 1: 属性基准提取（从人物设定 JSON，纯本地）
  { 汶晴: {年龄: "20岁", 身高: 未在设定中显式写但可从正文推断} }

Step 2: 正文属性扫描（本地规则 + LLM 提取）
  用正则 + LLM 从正文中扫描所有与角色属性数值有关的表述：
    "二十岁的汶晴" → 年龄=20
    "汶晴，二十一岁" → 年龄=21 → 与设定矛盾
    "她那一米七的身躯" → 身高=170
    "一百七十公分的她" → 身高=170
    "一米六几的个子" → 身高≈165 → 与前文 170 矛盾 → 报告 L14

Step 3: 交叉验证
  设定文件中的"年龄" vs 正文各处出现的年龄数字
  若不一致 → 报告 L14
```

---

### 10.17 Pass L15：对话归属检查 (`dialogue_attribution.py`)

**规则 + LLM 混合**。

```
Step 1: 对话段落识别（本地正则，缓存到 l15_dialogues.json）
  正则匹配所有引号对话段，标注其起止位置和归属标记
  归属标记包括："XX说"、"XX道"、"XX问"、"XX回答"

Step 2: 归属检测
  a. 无归属检测：
     连续 N 段引号对话无任何归属标记，且语境无法区分说话人 → 报告 L15
  b. 归属冲突检测：
     两段相邻对话的归属标记分别指向A和B，
     但对话内容逻辑上应该由同一个人说出 → 报告 L15

Step 3: LLM 复核
  输入：可疑对话段 + 上下文
  输出：确认是否存在归属问题，给出正确的归属
```

---

### 10.18 Pass L16：POV视角一致性检查 (`pov_consistency.py`)

**LLM 驱动**。

```
Step 1: 视角风格分析（缓存到 l16_pov.json）
  LLM 对每个场景判断：
    - 叙事视角类型: 第三人称有限/第三人称全知/第一人称
    - 当前 POV 角色（若有限视角）
    - 叙事距离（远近亲疏）

Step 2: POV 违规检测
  若场景为第三人称有限视角（只跟汶晴的视点），
  但出现：
    - 其他角色的内心独白（"朱岚心想..."）
    - 汶晴不可能观察到的细节（"朱岚背对着她，表情狰狞"）
    - 汶晴不可能知道的信息（"高个子在三个街区外点燃了一支烟"）
  → 报告 L16

Step 3: 场景间 POV 切换检查
  若相邻场景 POV 角色不同，检查是否有明确的视角切换标志（段落分隔/空行）
  若多场景频繁无规律跳动 → warning
```

---

### 10.19 Pass L17：叙述语气一致性检查 (`narrative_tone.py`)

**LLM 驱动**。

```
Step 1: 语域/语气分析（缓存到 l17_tone.json）
  LLM 对每个场景的叙述段落分析其语域：
    - 语域: 正式/半正式/口语化/诗化/冷静白描
    - 节奏: 短句快节奏/长句慢节奏/混合

Step 2: 语域跳变检测
  同一场景内，叙述语域发生不自然的剧烈跳变：
    前半段为冷静白描("她走进房间，放下书包")，
    后半段突变为煽情排比("啊！她是多么痛苦！她的心在滴血！")
    且叙事事件本身未提供语域变化的理由 → 报告 L17
```

---

### 10.20 Pass L18：关键事件缺失检查 (`missing_events.py`)

**LLM 驱动，需要跨场景全局理解**。

```
Step 1: 场景事件图构建（缓存到 l18_events.json）
  由 LLM 提取每场景的核心事件列表和状态变更

Step 2: 过渡缺口检测
  对相邻场景，检查是否存在"剧情缺口"：
    状态 A → 需要有一个过渡事件 → 状态 B
    但场景之间缺少这个过渡事件 → 报告 L18

  典型模式：
    - 困境→自由：角色被锁在地下室(scene_N结束)，
      scene_N+1角色在校园自由行走，但中间无逃脱/被释放场景
    - 受伤→恢复：角色重伤(scene_N)，scene_N+1行动如常，无治疗过程
    - 任务→完成：角色被给予任务(scene_N)，scene_N+1任务已完成，无执行过程

  注意：需要区分"有意省略"和"无意遗漏"。
  若父级概要中声明了过渡事件（如"四天后清晨，歹徒闯入宿舍"），
  则不报告 L18（这是有意的场景切换）
```

---

### 10.21 Pass L19：因果链断裂检查 (`causality.py`)

**LLM 驱动，全局理解**。

```
Step 1: 因果对提取（缓存到 l19_causality.json）
  LLM 对每个场景提取：（前因）→（结果）对

Step 2: 因果链完整性检查
  对每个角色的行为线，检查行为是否有"前因"：
    角色在scene_N中主动做出重大决策（如去袭击别人、去找某人合作），
    但回顾前文，找不到支撑该决策的情感/事件铺垫 → 报告 L19

  典型模式：
    - 恐惧→主动接近：一直恐惧某人的角色，毫无征兆地主动去找该人
    - 无冲突→爆发：无明显触发事件，角色突然对另一个人极端愤怒
    - 未受威胁→服从：角色未经任何施压过程便开始服从指令
```

---

### 10.22 Pass L20：角色数量突变检查 (`character_count.py`)

**规则 + LLM 混合**。

```
Step 1: 每场景角色轨迹跟踪
  结合 L2 的登场人物数据，跟踪同一场景内不同阶段出现的人员

Step 2: 突变检测
  a. 消失检测：
     场景开始时参与互动的N个人，场景内某个角色中途"消失"（不再被提及），
     但未描写任何离开动作 → 报告 L20
  b. 凭空出现：
     场景中途一个新角色开始参与互动，但原文未交代此人何时进入
  c. 群体大小矛盾：
     "四个人围坐在桌边"，后文"六只手同时伸过来"→ 报告 L20
```

---

### 10.23 Pass W3：感官描写平衡检查 (`sensory_balance.py`)

**规则 + LLM 辅助**。

```
Step 1: 感官词频统计（本地）
  统计每个场景中五感描写的分布：
    视觉：颜色词、形状词、光影描述...
    听觉：声音描述、静默描述...
    嗅觉：气味描述...
    触觉：温度、质地描述...
    味觉：味道描述...

Step 2: 失衡检测
  若某角色的多个连续场景中，环境描写100%只依赖视觉 → 报告 W3
  若全文整体偏向某1-2种感官（>90%）→ 报告 W3

Step 3: LLM 建议（可选）
  输入：失衡场景的环境 + 人物
  输出：建议在什么位置补充什么感官描写
```

---

### 10.24 Pass W4：句式结构多样性检查 (`sentence_variety.py`)

**规则驱动**。

```
Step 1: 句式结构提取（本地 NLP 分析）
  对每个段落：
    - 统计句子长度（帮助发现过长或过短的连续句群）
    - 统计句子开头模式（"她XX了"连续出现N次）
    - 统计连接词使用（"然后""接着""于是"的连续使用频率）

Step 2: 单调性警告
  连续 N 句（N 可配，如 5）具有相同的句式结构 → 报告 W4
  连续 N 句以相同的词开头（如连续以"她"开头）→ 报告 W4
```

---

### 10.25 Pass W5：标点符号规范检查 (`punctuation.py`)

**纯规则驱动，零 LLM 消耗**。

```
Step 1: 规则层扫描（本地正则）
  检查项：
    a. 引号不闭合：
       统计全文中 "和「」" 的数量，计数奇偶和配对
    b. 省略号规范：
       "。。。" → "……"（错误的中文省略号写法）
       "....." → "……"（英文句点代替）
    c. 中英标点混用：
       中文文本中出现英文逗号、英文句号、英文分号
       检测模式：中文字符后紧跟英文逗号 → 报告 W5
    d. 破折号规范：
       "--" → "——"（中文应使用全角破折号）
    e. 感叹号/问号过度使用：
       单一段落中超过 N 个连续的感叹号 → 报告 W5

Step 2: 生成位置列表
  每个标点问题标注：场景 ID + 行号 + 具体问题
  全部为确定性问题（不含 LLM 歧义），100% 可靠
```

---

## 十一、LLM 调用成本优化

### 11.1 免 LLM 调用或低调用场景

| Pass | 本地计算比例 | LLM 调用 | 说明 |
|------|-------------|---------|------|
| L5 | ~30% | ~70% | 时间线提取和归一化需要 LLM |
| L6 | ~80% | ~20% | 连通图构建和路径查找全在本地，仅复核用 LLM |
| L7 | ~10% | ~90% | 环境状态语义提取需要 LLM |
| L8 | ~10% | ~90% | 光照条件语义提取需要 LLM |
| L9-L10 | ~10% | ~90% | 物品追踪、知识推理需要 LLM |
| L11 | ~60% | ~40% | 数字提取+本地比对为主，LLM复核 |
| L12-L13 | ~10% | ~90% | 能力评估、情感分析密集LLM调用 |
| L14 | ~80% | ~20% | 设定对比+正则扫描为主，LLM复核歧义 |
| L15 | ~70% | ~30% | 对话段提取本地正则，归属判断用LLM |
| L16-L17 | ~10% | ~90% | POV和语域判断语义密集型 |
| L18-L19 | ~5% | ~95% | 跨场景全局理解，最耗LLM token |
| L20 | ~70% | ~30% | 结合L2数据做本地比对为主 |
| W1 | ~95% | ~5% | 规则层解决绝大部分 |
| W2 | ~90% | ~10% | 统计和判断可本地完成 |
| W3 | ~90% | ~10% | 词频统计本地完成 |
| W4 | ~100% | ~0% | 纯句式分析 |
| W5 | ~100% | ~0% | 纯正则扫描 |

**总计**：25 个 Pass 中，9 个以规则驱动为主（LLM 调用极少），16 个需显著 LLM 参与。

### 11.2 按 LLM 调用量分组的调度建议

```
高 LLM 调用组（共享速率限制器）:
  L1, L2, L3, L4, L7, L8, L9, L10, L12, L13, L16, L17, L18, L19

低 LLM 调用组（可自由并行）:
  L5, L6, L11, L14, L15, L20

零 LLM 调用组（无限并行）:
  W1, W2, W3, W4, W5
```

### 11.3 缓存带来的 Token 节省

**场景**：200 个场景的全量校对中，用户修改了 5 个场景后重新校对。

| 无缓存 | 有缓存 |
|--------|--------|
| 全部 200 个场景需要重新分析 | 仅 5 个变更场景需要重新分析 |
| ~200 × 每次 LLM 调用 | ~5 × 每次 LLM 调用 |
| **节省 ~97.5% 的 token** | |

**场景**：修改了 W1 的规则表后重新校对。

| 无缓存 | 有缓存 |
|--------|--------|
| 全部 Pass 重跑 | 仅 W1 重跑（其他 24 个 Pass 缓存命中） |
| **节省 24/25 的 token** | |

---

## 十二、与现有系统集成点

### 12.1 复用清单

| 现有模块 | 复用方式 |
|---------|---------|
| `core/workspace_manager.py` | 调用 `load_outline_tree()`、递归读取设定目录文件（新增 `loader.py` 内实现）、读写 `pending_modifies`（`save/get/delete_pending_modify`） |
| `core/llm_client.py` | 所有 LLM 调用统一经过 `LLMClient` |
| `core/world_context_compressor.py` | 当单场景正文超长时压缩后再送入 LLM |
| `ui/diff_merge_dialog.py` | 环节三完全复用，作为 diff 查看/合并入口 |
| `diff_match_patch` | 环节三在 DiffMergeDialog 中计算逐行差异 |

### 12.2 新增模块

| 新增模块 | 说明 |
|---------|------|
| `modules/proofread/` | 校对功能的主目录，所有新增代码在此 |
| `系统数据/proofread_cache/` | 中间产物缓存目录（跨运行持久化） |
| `系统数据/proofread_checkpoint/` | 运行态检查点目录（中断恢复用） |
| `系统数据/proofread_issues.json` | 问题清单产出物 |

### 12.3 入口调用

```python
# 在 ui 层（如 main_window.py 或 novel_tree_mixin.py）中触发校对
from modules.proofread.engine import ProofreadEngine
from threading import Event

engine = ProofreadEngine(
    workspace_path=self.workspace.workspace_path,
    llm_client=self.llm_client,
    config=self.get_proofread_config(),
)

# 创建中断信号（UI 层持有，用户点击"暂停"时 set）
self.proofread_stop_event = Event()

# 如果上次有未完成的运行，先恢复：
# issues = engine.resume_find(
#     progress_callback=self.on_proofread_progress,
#     stop_event=self.proofread_stop_event,
# )

# 环节一：查找问题
try:
    issues = engine.run_find(
        progress_callback=self.on_proofread_progress,
        stop_event=self.proofread_stop_event,
    )
except InterruptedError:
    # 用户暂停了——进度已保存到 proofread_checkpoint/
    self.log("校对已暂停，可稍后继续")
    return

# 环节二：解决问题（用户可选择修复范围）
try:
    engine.run_solve(
        issues=issues,
        scope="selected",
        progress_callback=self.on_proofread_progress,
        stop_event=self.proofread_stop_event,
    )
except InterruptedError:
    self.log("修复已暂停，已生成的 pending_modifies 保留")

# 环节三：人工确认（UI 层面触发）
# 用户在故事树中看到 pending 标记 → 右键 "查看校对 Diff" → DiffMergeDialog

# 放弃本次校对：
# engine.abandon()  # 删除 proofread_checkpoint/ 目录
```

---

## 十三、实现阶段规划

### Phase 1：基础设施（无 LLM 依赖）
- `loader.py`：数据加载
- `pre_analyzer.py`：场景预分析 + summary 解析
- `cache_manager.py`：缓存管理
- `checkpoint_manager.py`：检查点管理（CheckpointManager 完整实现）
- `issue_aggregator.py`：问题聚合
- `passes/base.py`：Pass 基类框架（含 stop_event 检查点）
- `proofread_config.py`：配置管理

### Phase 2：规则层 Pass（零 LLM 依赖）
- `passes/language_style.py` (W1)：繁简映射表 + 全文扫描
- `passes/punctuation.py` (W5)：标点符号规范检查
- `passes/sentence_variety.py` (W4)：句式结构多样性
- `passes/sensory_balance.py` (W3)：感官描写平衡（规则部分）
- `passes/repetition.py` (W2)：特征词提取 + 频率统计
- `passes/location_reachability.py` (L6)：地点连通图（规则部分）
- `passes/attribute_values.py` (L14)：属性数值（规则部分）
- `passes/dialogue_attribution.py` (L15)：对话段提取（规则部分）

### Phase 3：混合层 Pass（规则+LLM）
- `passes/timeline.py` (L5)：时间线逻辑
- `passes/quantity.py` (L11)：数字/数量一致性
- `passes/character_count.py` (L20)：角色数量突变

### Phase 4：LLM 驱动 Pass（人物状态与互动）
- `passes/continuity.py` (L1)：状态连续性
- `passes/appearance.py` (L2)：角色登场/退场
- `passes/relationships.py` (L3)：关系与称呼
- `passes/extra_characters.py` (L4)：额外角色检测

### Phase 5：LLM 驱动 Pass（时空与物品）
- `passes/weather_env.py` (L7)：天气环境
- `passes/lighting.py` (L8)：光照一致性
- `passes/prop_continuity.py` (L9)：物品道具连续性

### Phase 6：LLM 驱动 Pass（深度逻辑）
- `passes/character_knowledge.py` (L10)：角色知识矛盾
- `passes/ability_consistency.py` (L12)：角色能力一致性
- `passes/behavior_logic.py` (L13)：角色行为逻辑
- `passes/pov_consistency.py` (L16)：POV视角
- `passes/narrative_tone.py` (L17)：叙述语气

### Phase 7：LLM 驱动 Pass（全局理解）
- `passes/missing_events.py` (L18)：关键事件缺失
- `passes/causality.py` (L19)：因果链断裂

### Phase 8：闭环与集成
- `solve_engine.py`：环节二修复引擎（含 checkpoint 支持）
- `prompts/`：所有 prompt 模板编写
- 环节三集成：确保 pending_modifies 格式兼容现有 DiffMergeDialog
- `engine.py`：三环节编排 + 多线程调度 + LLM 速率限制器 + 中断/恢复入口
- 端到端中断恢复测试：模拟暂停→继续→暂停→继续的完整流程

### Phase 9：UI 增强（可选）
- 校对进度面板
- "校对结果"列表视图
- 按问题类型/严重程度筛选
- W3/W4 的 LLM 复核与建议生成

---

## 十四、自动化测试策略

### 14.1 测试技术栈

| 层级 | 工具 | 说明 |
|------|------|------|
| 测试框架 | `unittest` | 与现有项目一致的测试框架，无额外依赖 |
| Mock 工具 | `unittest.mock` | 标准库，mock LLM 调用、文件 I/O、时间函数 |
| 测试数据 | `tests/proofread/fixtures/` | 最小合法的小型小说工程目录结构，用于所有测试的输入 |
| 覆盖率 | `coverage` (可选) | `pip install coverage`，用于检查测试覆盖度 |

### 14.2 测试目录结构

```
tests/proofread/
├── __init__.py
├── conftest.py                       # 共享 fixture 工厂函数
│                                     #   create_mock_llm_client()
│                                     #   create_minimal_workspace(tmp_path)
│                                     #   create_scene_list(n)
│                                     #   create_character_list()
│                                     #   create_location_list()
├── fixtures/
│   ├── minimal_workspace/
│   │   ├── 系统数据/
│   │   │   ├── outline_tree.json         # 3章×1节×2场景 = 6个场景的极简大纲
│   │   │   ├── proofread_cache/
│   │   │   │   └── manifest.json
│   │   │   └── proofread_checkpoint/
│   │   │       ├── run_state.json
│   │   │       └── tasks/
│   │   ├── 设定/
│   │   │   ├── 人物设定/
│   │   │   │   ├── 角色A.json
│   │   │   │   └── 角色B.json
│   │   │   └── 地点设定/
│   │   │       ├── 音乐学院.json
│   │   │       ├── 宿舍.json
│   │   │       └── 图书馆.json
│   │   └── 正文/
│   │       ├── 场景_0001.md
│   │       └── ...
│   └── sample_texts/
│       ├── state_continuity.txt          # 含穿戴矛盾的样本文本
│       ├── dialogue_no_attribution.txt   # 无归属对话段
│       ├── traditional_chinese.txt       # 繁体字/港台用语混合
│       ├── repeated_phrase.txt           # 特征词过度重复
│       ├── time_contradiction.txt        # 时间线矛盾
│       ├── location_unreachable.txt      # 地点不可达
│       └── broken_causality.txt          # 因果链断裂
├── test_loader.py                   # DataLoader 测试
├── test_pre_analyzer.py             # PreAnalyzer / summary 解析
├── test_cache_manager.py            # CacheManager 测试
├── test_checkpoint_manager.py       # CheckpointManager 测试
├── test_issue_aggregator.py         # IssueAggregator 测试
├── test_pass_base.py                # BasePass 框架测试
├── test_passes_rules/               # 规则层 Pass 测试（零 mock 或轻量 mock）
│   ├── test_w1_language_style.py
│   ├── test_w2_repetition.py
│   ├── test_w3_sensory_balance.py
│   ├── test_w4_sentence_variety.py
│   ├── test_w5_punctuation.py
│   ├── test_l6_location_reachability.py
│   └── test_l14_attribute_values.py
├── test_passes_llm/                 # LLM 驱动 Pass 测试（重度 mock）
│   ├── test_l1_continuity.py
│   ├── test_l2_appearance.py
│   ├── test_l3_relationships.py
│   ├── test_l4_extra_characters.py
│   ├── test_l5_timeline.py
│   ├── test_l7_weather.py
│   ├── test_l8_lighting.py
│   ├── test_l9_prop_continuity.py
│   ├── test_l10_knowledge.py
│   ├── test_l11_quantity.py
│   ├── test_l12_ability.py
│   ├── test_l13_behavior.py
│   ├── test_l15_dialogue.py
│   ├── test_l16_pov.py
│   ├── test_l17_tone.py
│   ├── test_l18_missing_events.py
│   ├── test_l19_causality.py
│   └── test_l20_character_count.py
├── test_solve_engine.py             # 环节二：修复引擎
├── test_engine.py                   # 三环节编排 + 中断恢复集成测试
└── test_reporter.py                 # 报告输出格式
```

### 14.3 LLM Mock 策略

**核心原则**：每个需要 LLM 的测试必须完全不发起真实网络请求。使用 `unittest.mock.patch` 在 `LLMClient` 层面拦截。

```python
# conftest.py —— 核心 mock 工厂函数

def create_mock_llm_client(response_map=None):
    """
    创建一个完全 mock 的 LLMClient。

    Args:
        response_map: dict[str, dict]
            key = prompt 文本的前 N 个字符的 hash（用于匹配请求）
            value = 模拟的 JSON 响应
            如果不提供，返回默认空响应

    用法示例：
        mock_llm = create_mock_llm_client({
            "你是专业的小说校对": {"issues": [...], "intermediate_data": {...}},
            "提取每个出场角色": {"characters": [{...}]},
        })

    内部实现：
        - patch LLMClient 的 generate_text 方法
        - 根据 prompt 内容的前缀匹配返回对应 mock 数据
        - 自动记录所有 LLM 调用记录供测试断言
    """
    ...

def assert_llm_called_with(mock_llm, expected_keyword, times=1):
    """断言 LLM 曾被以包含特定关键词的 prompt 调用过"""
    ...

def assert_llm_not_called(mock_llm):
    """断言 LLM 一次也没有被调用（用于验证纯规则 Pass 的独立性）"""
    ...
```

**Mock 分层策略**：

| Pass 类型 | Mock 层级 | 测试关注点 |
|-----------|----------|-----------|
| 纯规则 (W1,W2,W3,W4,W5) | 不 mock LLM | 验证 `assert_llm_not_called`，纯逻辑正确性 |
| 规则为主 (L6,L14,L15,L20) | Mock LLM 仅复核步骤 | 验证规则层发现问题的正确性；LLM 复核仅测试传递参数 |
| LLM 驱动 (L1-L5,L7-L13,L16-L19) | Mock LLM 完全替代 | 验证 prompt 构建正确性 + 结果解析正确性 + 边界情况 |

### 14.4 各层测试用例规划

#### 14.4.1 Loader (`test_loader.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| LDR-01 | 加载最小合法工程 | `minimal_workspace/` | `WorkspaceData` 含 6 个场景、2 个角色、3 个地点 |
| LDR-02 | 场景列表按大纲顺序排列 | `minimal_workspace/` | `scene_list` 顺序 = 章1节1场景1, 章1节1场景2, 章1节2场景1... |
| LDR-03 | 别名映射正确建立 | 人物设定中含"高个子"→"顾承骁" | `character_alias_map["高个子"] == "顾承骁"` |
| LDR-04 | 加载空工程（无场景） | `outline_tree.nodes=[]` | `scene_list` 为空，不抛异常 |
| LDR-05 | 场景文件缺失 | `file_path` 指向不存在的文件 | `content_raw` 为 `None`，记录 warning |
| LDR-06 | 人物设定文件缺少字段 | JSON 缺少 "性格" 字段 | 缺失字段使用 `""` 默认值 |
| LDR-07 | 地点设定含嵌套子目录 | 地点在 `后山/` 子目录中 | 正确递归加载，`locations` 包含子目录中的地点 |
| LDR-08 | 角色无别名场景 | 人物设定 JSON 无 "绰号" 字段 | `aliases` 为空列表 |

#### 14.4.2 PreAnalyzer (`test_pre_analyzer.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| PA-01 | 标准 summary 解析 | 含完整【时间】【地点】【人物】【事件】【剧情总结】 | 各字段正确填充 `SceneContext` |
| PA-02 | summary 含地点转移箭头 | "【地点】：A → B → C" | `locations = ["A", "B", "C"]` |
| PA-03 | summary 人物含参考说明 | "【人物】：汶晴、朱岚。详见设定" | 去除"详见设定"，`characters_declared = ["汶晴", "朱岚"]` |
| PA-04 | summary 为空字符串 | `summary=""` | `SceneContext` 各字段为空字符串/空列表 |
| PA-05 | 正文对话段提取 | 含多个带引号对话的自然段 | `text_chunks.json` 中正确标注对话段起止行 |
| PA-06 | 缓存命中逻辑 | MD5 未变 + context.json 存在 | 跳过解析，直接读取缓存 |
| PA-07 | 缓存失效逻辑 | MD5 已变 | 删除旧缓存，重新解析并写入 |

#### 14.4.3 CacheManager (`test_cache_manager.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| CM-01 | 首次写 manifest | 空 `proofread_cache/` | 创建 `manifest.json`，含全部场景条目 |
| CM-02 | 读取 manifest | 已有 `manifest.json` | 正确反序列化为 `CacheManifest` |
| CM-03 | 查缓存命中 | 场景 MD5 与缓存一致 | `is_cached(scene_id, pass_name) → True` |
| CM-04 | 查缓存未命中 | 场景 MD5 已变 | `is_cached → False` |
| CM-05 | 场景缓存失效 | 调用 `invalidate(scene_id)` | 该场景所有缓存文件 + manifest 条目被删除 |
| CM-06 | 写入中间产物 | 调用 `save_intermediate(scene_id, pass_name, data)` | 文件正确写入 + manifest 更新 |
| CM-07 | 读取中间产物 | 调用 `load_intermediate(scene_id, pass_name)` | 正确反序列化为对应类型 |
| CM-08 | promote_from_checkpoint | 将 checkpoint 数据提升到 cache | 文件从 checkpoint 目录复制到 cache 目录 |

#### 14.4.4 CheckpointManager (`test_checkpoint_manager.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| CK-01 | 初始化新运行 | `init_run(phase="find")` | 创建 `run_state.json`(status=running) + 25 个空 task 文件 |
| CK-02 | 初始化——旧 checkpoint 已存在 | 已有旧 checkpoint 文件 | 若旧 status=paused 则询问恢复/丢弃；否则自动清理 |
| CK-03 | 保存单个 task 结果 | task 完成 → `save_task()` | `tasks/l1_continuity.json` 中对应 scene 状态→completed |
| CK-04 | 保存 LLM 响应 | LLM 返回 → `save_llm_response()` | `llm_responses/l1/scene_xxx.json` 写入完整记录 |
| CK-05 | run_state 心跳更新 | 30s 定时触发 | `run_state.updated_at` 刷新，其余不变 |
| CK-06 | 暂停 | `pause()` | `run_state.status = "paused"` |
| CK-07 | 恢复运行 | `resume()` → 读取 checkpoint | 返回所有 status≠completed 和 status≠skipped 的 scene_id |
| CK-08 | 恢复——MD5 校验 | 恢复时场景 MD5 已变 | 该场景在所有 Pass 中被重置为 PENDING |
| CK-09 | Pass 完成标记 | 该 Pass 所有 scene task→completed | `run_state.passes[pass_name].status = "completed"` |
| CK-10 | 环节完成 | 所有 Pass completed | 自动调用 `promote_to_cache()` |
| CK-11 | 清理 | `cleanup()` | 删除整个 `proofread_checkpoint/` 目录 |
| CK-12 | 失败 task 重试 | `reset_failed_tasks()` | FAILED → PENDING，attempts 计数保留 |
| CK-13 | 原子写入保护 | 写入过程中进程崩溃 | `os.replace(tmp, target)` 保证不会读到损坏的 JSON |

#### 14.4.5 BasePass 框架 (`test_pass_base.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| BP-01 | Pass 正常执行完成 | mock LLM 返回合法数据 | `run()` 返回 `(issues, intermediates)` 双元组 |
| BP-02 | Pass 遇到 stop_event | 循环中途 `stop_event.set()` | 抛出 `InterruptedError`，已完成 scene 的结果已保存 |
| BP-03 | Pass 内部 LLM 调用失败 | mock LLM raise exception | 该 scene task 标记 FAILED，继续处理下一个 scene |
| BP-04 | 连续失败超阈值 | 连续 5 次 LLM 调用失败 | Pass 自动暂停，`status="failed"`，向上层报告 |
| BP-05 | 空场景列表 | `scene_list=[]` | 返回空 issues + 空 intermediates |
| BP-06 | 场景无内容 | `content_raw=None` | 跳过该场景，status=skipped |
| BP-07 | LLM 返回非法 JSON | mock LLM 返回畸形 JSON | 记录错误 + status=FAILED，不抛异常 |
| BP-08 | 批量 prompt 构建 | 传入多场景 | prompt 正确包含角色设定 + 场景正文 |

#### 14.4.6 规则层 Pass——W1 简体中文 (`test_w1_language_style.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| W1-01 | 繁体字检测 | "透過螢幕看見優質的她" | 发现 4 个问题，每个标记行号 + 建议对应简体 |
| W1-02 | 港台用语 | "的而且確，她埋單了" | 发现 2 个问题 |
| W1-03 | 纯简体文本 | "透过屏幕看见优质的她" | 零问题 |
| W1-04 | 混合文本——仅标记非简体部分 | "她通过了屏幕测试" | "通过" + "屏幕" 不触发（本身就是简体） |
| W1-05 | 有意保留的方言（角色语言风格） | 需 LLM 复核 | 仅 rule 层标记为 info，不自动判定为 error |
| W1-06 | 无 LLM 调用 | 任意文本 | `assert_llm_not_called`（规则层完全独立） |

#### 14.4.7 规则层 Pass——W2 特征词重复 (`test_w2_repetition.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| W2-01 | 高频短语检测 | "170cm的身躯" 出现 12 次，分散在 6 个场景 | 报告 W2 error |
| W2-02 | 合理重复——集中在少数场景 | "170cm" 出现 15 次但都在 2 个场景内 | 不报告（可能是剧情焦点） |
| W2-03 | 语义聚类 | "170cm""一百七十公分""一米七" 合计 15 次 | 归为同一概念，报告 W2 |
| W2-04 | 空特征词列表 | 人物设定无特征描述 | 零问题 |
| W2-05 | 不同角色的相似描述 | 角色A 和 角色B 都多次写"长发披肩" | 分开统计，各自判断 |

#### 14.4.8 规则层 Pass——W4 句式结构 (`test_w4_sentence_variety.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| W4-01 | 连续相同开头 | 连续 6 句以"她"开头 | 报告 W4 |
| W4-02 | 连续相同连接词 | 连续 5 句含"然后" | 报告 W4 |
| W4-03 | 句式多样性正常 | 长短句交替、开头多变 | 零问题 |
| W4-04 | 可配置阈值 | N=3 时更敏感 | 阈值可通过 config 调整 |

#### 14.4.9 规则层 Pass——W5 标点符号 (`test_w5_punctuation.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| W5-01 | 引号不闭合 | "她说："你好。 | 报告左引号无匹配右引号 |
| W5-02 | 中文省略号错误 | "她想了想。。。" | 报告应改为 "……" |
| W5-03 | 中英标点混用 | "她来了,然后走了." | 报告英文逗号、英文句号 |
| W5-04 | 破折号不规范 | "她--突然停住了" | 报告应改为 "——" |
| W5-05 | 感叹号过度 | 连续 4 个感叹号结尾的句子 | 报告过度使用 |
| W5-06 | 全规范文本 | 中文标点正确使用的完整段落 | 零问题 |

#### 14.4.10 规则层 Pass——L6 地点可达性 (`test_l6_location_reachability.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| L6-01 | 合法转移 | 宿舍→图书馆（设定中连通） | 零问题 |
| L6-02 | 非法转移 | 宿舍→山顶废墟（无直接路径，两步内无路径） | 报告 L6 |
| L6-03 | 需中间地点的转移 | 宿舍→后山小径（需经过校园）但正文有描写 | 零问题 |
| L6-04 | 跳场豁免 | 非连续场景，中间有时间跳跃 | 不检查地点可达性 |
| L6-05 | 同场景内地点转移 | summary 含 "A → B → C" | 检查 A→B 和 B→C 各自的可达性 |

#### 14.4.11 LLM 驱动 Pass——L1 状态连续性 (`test_l1_continuity.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| L1-01 | 穿戴矛盾 | scene1:穿鞋, scene2:光脚, 无脱鞋描写 | 报告 L1 error |
| L1-02 | 伤势矛盾 | scene1:脚烫伤无法站立, scene2:健步如飞, 无恢复 | 报告 L1 error |
| L1-03 | 合理状态转移 | scene1:受伤, scene2:数天后已包扎+拄拐 | 零问题 |
| L1-04 | LLM 状态提取 JSON 解析 | mock 返回标准格式 | `StateSnapshot` 正确反序列化 |
| L1-05 | prompt 包含必要信息 | 检查构造的 prompt | 含角色设定 + 场景正文 + 状态提取指令 |
| L1-06 | 无状态变化的场景 | 场景中角色未发生状态改变 | normal，不报告 |
| L1-07 | 多角色并行追踪 | 同一场景 3 个角色各有状态 | 分别提取、分别追踪 |

#### 14.4.12 LLM 驱动 Pass——L3 关系称呼 (`test_l3_relationships.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| L3-01 | 称呼与关系不符 | 挚友间称呼"朱岚同学" | 报告 L3 warning |
| L3-02 | 称呼符合关系 | 挚友间称呼"小岚""阿岚" | 零问题 |
| L3-03 | 关系矩阵构建 | 加载人物设定 | 正确构建对称关系映射 |
| L3-04 | 对话段提取 | 正文含多段对话 | 每段正确标注 speaker_a / speaker_b |
| L3-05 | 无对话的场景 | 场景无引号内容 | 跳过，零问题 |

#### 14.4.13 LLM 驱动 Pass——L10 角色知识 (`test_l10_knowledge.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| L10-01 | 角色知道不该知道的事 | A 在 scene5 提到 X，但 A 从未有渠道获知 X | 报告 L10 |
| L10-02 | 角色遗忘已知信息 | A 在 scene2 亲历 X，在 scene6 对 X 表示惊讶 | 报告 L10 |
| L10-03 | 正常信息流 | A 经历 X → A 在后续场景正常引用 X | 零问题 |
| L10-04 | 信息被告知 | A 在 scene3 被 B 告知 X → scene4 A 引用 X | 零问题（信息有正常传递路径） |

#### 14.4.14 LLM 驱动 Pass——L18 事件缺失 (`test_l18_missing_events.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| L18-01 | 困境→自由无过渡 | scene2:被锁地下室, scene3:在校园自由行走 | 报告 L18 |
| L18-02 | 有父级概要声明的过渡 | scene2 的父级 summary 含"四天后歹徒闯入宿舍" | 不报告（有意的场景切换） |
| L18-03 | 连续场景正常过渡 | scene2:出门, scene3:到达目的地 | 零问题 |
| L18-04 | 受伤→恢复无治疗 | scene4:重伤, scene5:正常行动 | 报告 L18（若时间跨度短于合理恢复期） |
| L18-05 | 数天后时间跳跃 | scene4:重伤, scene5:"三周后"康复行走 | 零问题（时间跳跃给了恢复空间） |

#### 14.4.15 环节二：SolveEngine (`test_solve_engine.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| SLV-01 | 按场景归并问题 | 5 个 issue 分布在 3 个场景 | `{scene_A: [2 issues], scene_B: [1], scene_C: [2]}` |
| SLV-02 | 过滤 info 级别 | 含 2 个 error + 3 个 info | 仅 error 参与修复 |
| SLV-03 | 构建修复 prompt | 场景正文 + 2 个 issue | prompt 含角色设定 + 原文 + 问题清单 + 修改要求 |
| SLV-04 | 写入 pending_modify | LLM 返回修复后全文 | `pending_modifies/{node_id}.json` 格式与现有完全一致 |
| SLV-05 | 空问题列表 | `issues=[]` | 不调用 LLM，不产生 pending_modifies |
| SLV-06 | 只修复指定章节 | `scope={"chapter": "第一章"}` | 仅第一章的场景产生 pending_modifies |
| SLV-07 | stop_event 中断 | 修复中途暂停 | 已写入的 pending_modifies 保留，未处理的场景状态保存 |
| SLV-08 | 恢复修复 | `resume_solve()` | 跳过已有 pending_modifies 的场景 |

#### 14.4.16 引擎编排 (`test_engine.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| ENG-01 | 完整环节一流程 | 正常 mock LLM | 返回完整 `proofread_issues.json` |
| ENG-02 | 环节一→暂停→恢复 | `run_find` 中 set stop_event → 再 `resume_find` | 最终 issue 数量 = 不中断的 issue 数量 |
| ENG-03 | 环节一 MD5 校验——恢复时文件已变 | 恢复前修改某场景文件 | 该场景相关 Pass 任务被重置 PENDING |
| ENG-04 | 环节二→暂停→恢复 | `run_solve` 中暂停 → `resume_solve` | 最终 pending_modifies 数量 = 不中断的数量 |
| ENG-05 | 放弃 `abandon()` | 调用 `engine.abandon()` | `proofread_checkpoint/` 被删除 |
| ENG-06 | 连续两次 `resume_find` | 恢复 → 完成 → 再次恢复 | 第二次恢复发现 status=completed，直接返回缓存结果 |
| ENG-07 | 多线程并行 Pass 执行 | `parallel_passes=4` | 4 个 Pass 并发执行，结果收集完整 |
| ENG-08 | 串行 Fallback | `parallel_passes=1` | Pass 逐个执行，顺序任意但结果完整 |
| ENG-09 | 异常传递 | 某 Pass 抛出非 InterruptedError 异常 | Engine 记录 err，标记该 Pass FAILED，其他 Pass 继续 |
| ENG-10 | 进度回调 | progress_callback 被正确调用 | 每完成一个 scene 触发一次回调（含百分比+当前 Pass 名称） |

#### 14.4.17 Reporter (`test_reporter.py`)

| 序号 | 用例 | 输入 | 预期 |
|------|------|------|------|
| REP-01 | JSON 格式输出 | 3 个 issue | 输出合法 JSON，含 summary 统计 |
| REP-02 | Markdown 格式输出 | 3 个 issue | 输出 Markdown 含标题、表格、证据引用 |
| REP-03 | 按严重程度分层统计 | error/warning/info 各若干 | `by_severity` 计数正确 |
| REP-04 | 按类别分层统计 | L1/L2/W1 等 | `by_category` 计数正确 |
| REP-05 | 空问题列表 | `issues=[]` | 正常输出，summary 显示 0 个问题 |
| REP-06 | 一个场景含多个问题 | 同一 scene_id 的 5 个 issue | 按场景位置正确分组，不丢失 |
| REP-07 | 问题去重（IssueAggregator） | 不同 Pass 发现同一问题 | 仅保留一条，注明来源 Pass 列表 |

### 14.5 测试数据设计原则

**`minimal_workspace/` 的最小要求**：

```
3 章 × 1 节 × 2 场景 = 6 个场景
  场景正文长度：每篇 400-800 字
  2 个角色 + 3 个地点 + 完整的 outline_tree.json

这个规模足够验证：
  - 跨场景对比（L1, L5, L9, L18, L19）
  - 树遍历（Loader）
  - 缓存命中/失效（CacheManager）
  - Checkpoint/Resume 的状态切换
```

**`sample_texts/` 的设计原则**：

每个样本文件**独立含有一个明确的问题实例**，确保测试断言精准：

```
state_continuity.txt:
  【场景1】角色穿上了红色高跟鞋，站在镜子前端详。
  【场景2】（标注"紧接场景1"）角色赤脚踩在瓷砖地上，脚趾因寒冷微蜷。
  → 预期 L1 问题：鞋子矛盾

dialogue_no_attribution.txt:
  "你今天来得好早。"
  "是啊，睡不着。"
  "咖啡在你桌上。"
  "谢谢。"
  → 预期 L15 问题：连续4段无归属对话
```

### 14.6 测试覆盖率目标

| 层级 | 代码覆盖率目标 | 说明 |
|------|-------------|------|
| `loader.py` | ≥ 90% | 异常分支多（文件缺失/字段缺失） |
| `pre_analyzer.py` | ≥ 90% | summary 解析的边界情况 |
| `cache_manager.py` | ≥ 95% | 逻辑集中且关键 |
| `checkpoint_manager.py` | ≥ 95% | 状态机完整性 + 原子写入 |
| `issue_aggregator.py` | ≥ 85% | 去重/排序逻辑 |
| `passes/base.py` | ≥ 90% | 所有 Pass 的公共基础 |
| 规则层 Pass (W1-W5, L6 规则, L14-L15 规则) | ≥ 90% | 纯逻辑，易测试 |
| LLM 驱动 Pass (L1-L5, L7-L13, L16-L20) | ≥ 75% | prompt 构造 + 结果解析 + 边界控制（LLM 本身不测） |
| `solve_engine.py` | ≥ 85% | prompt 组装 + pending_modifies 写入 |
| `engine.py` | ≥ 80% | 编排逻辑 + 中断恢复（集成测试） |
| `reporter.py` | ≥ 85% | 格式输出 |
| **总体** | **≥ 85%** | |

### 14.7 测试执行

```bash
# 运行全部校对模块测试
python -m unittest discover -s tests/proofread -v

# 仅运行规则层测试（无需 mock LLM，速度最快）
python -m unittest discover -s tests/proofread/test_passes_rules -v

# 运行单个 Pass 测试
python -m unittest tests/proofread/test_passes_rules/test_w1_language_style.py -v

# 运行检查点恢复集成测试
python -m unittest tests/proofread/test_engine.py -k "ENG-02" -v

# 生成覆盖率报告（需先安装 coverage）
pip install coverage
coverage run -m unittest discover -s tests/proofread -v
coverage report -m --include="modules/proofread/*"
coverage html  # 生成 HTML 报告
```

### 14.8 与现有 Phase 的关系

每个 Phase 的实现应与对应测试同步完成：

| Phase | 测试套件 | 要求 |
|-------|---------|------|
| Phase 1 | `test_loader`, `test_pre_analyzer`, `test_cache_manager`, `test_checkpoint_manager`, `test_issue_aggregator`, `test_pass_base` | **代码与测试同步提交** |
| Phase 2 | `test_passes_rules/*` (8 个文件) | 每完成一个 Pass 的规则层，同步编写测试 |
| Phase 3-7 | `test_passes_llm/*` (16 个文件) | LLM mock 工厂函数在 Phase 3 开始时完成；每个 Pass 完成后立即编写测试 |
| Phase 8 | `test_engine`, `test_solve_engine`, `test_reporter` | **全部通过后视为集成完成** |
| Phase 9 | UI 测试（可选，手动或 QtTest） | 不在本策略覆盖范围内 |
