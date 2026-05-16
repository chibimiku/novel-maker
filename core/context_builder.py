import os
import json
import logging
import re
from core.world_context_compressor import WorldContextCompressor, WorldSettingNode

logger = logging.getLogger(__name__)

class ContextBuilder:
    def __init__(
        self,
        workspace_manager,
        model_context_size: int | None = None,
        model_capabilities: list[str] | None = None,
        compression_profile: str | None = None,
        children_summary_compress_trigger_ratio: float | None = None,
        children_summary_group_budget_ratio: float | None = None,
    ):
        """
        初始化上下文构建器
        :param workspace_manager: WorkspaceManager 实例，用于读取本地文件
        """
        self.workspace = workspace_manager
        self.world_compressor = WorldContextCompressor(
            model_context_size=model_context_size,
            model_capabilities=model_capabilities,
            compression_profile=compression_profile,
        )
        self._compression_profile = self.world_compressor.compression_profile
        self._children_summary_compress_trigger_ratio = self._normalize_ratio(
            children_summary_compress_trigger_ratio,
            default_value=0.27,
            min_value=0.05,
            max_value=0.8,
        )
        self._children_summary_group_budget_ratio = self._normalize_ratio(
            children_summary_group_budget_ratio,
            default_value=0.24,
            min_value=0.04,
            max_value=0.6,
        )

    def _normalize_ratio(
        self,
        ratio: float | None,
        default_value: float,
        min_value: float,
        max_value: float,
    ) -> float:
        """归一化比例参数，支持 [0,1] 小数和 [0,100] 百分数输入。"""
        if ratio is None:
            return default_value
        try:
            value = float(ratio)
        except Exception:
            return default_value
        # 兼容把 27 当作 27% 的输入
        if value > 1:
            value = value / 100.0
        if value <= 0:
            return default_value
        return max(min_value, min(max_value, value))

    def _get_children_summary_trigger_tokens(self) -> int:
        """按模型上下文长度计算子节点压缩触发 token 阈值。"""
        context_size = max(2048, int(self.world_compressor.model_context_size or 8192))
        return max(600, int(context_size * self._children_summary_compress_trigger_ratio))

    def _get_children_group_budget_tokens(self) -> int:
        """按模型上下文长度计算分组压缩单组 token 预算。"""
        context_size = max(2048, int(self.world_compressor.model_context_size or 8192))
        return max(450, int(context_size * self._children_summary_group_budget_ratio))

    # 修改 context_builder.py，在类中新增以下方法：

    def build_rewrite_prompt(self, target_node: dict, tree_data: dict, checked_setting_paths: list, word_count: int) -> list:
        """构建重写/扩写/缩写的专属上下文"""
        settings_text = self._build_settings_text(checked_setting_paths)
        target_title = target_node.get("title", "未命名场景")
        target_summary = target_node.get("summary", "").strip()
        target_content = self._read_node_content(target_node).strip()

        prompt = f"""你是一个专业的小说创作者。现在的任务是对一段已有的场景正文进行【重写/扩写/缩写】。

    ### 一、 世界观与设定参考
    {settings_text}

    ### 二、 当前场景概要
    【{target_title}】
    {target_summary if target_summary else "(暂无概要)"}

    ### 三、 原文内容（需要你重写的目标核心）
    {target_content}

    ### 四、 修改要求（绝对红线）
    1. 请根据上述设定和概要，将【原文内容】重新改写，使字数达到大约 {word_count} 字左右。
    2. 你可以补充细节描写、对话、心理活动来扩写，亦可精简冗余内容来缩写，必须保持原有剧情的核心事件和走向不变。
    3. **细节落实警告**：在重写时，请务必核对并保留原文或概要中提及的【时间】、【地点轨迹】、【事件交互】以及【人物衣着/装备/心境的变化】。这些核心要素绝对不能在重写过程中丢失。
    4. 严禁任何助手语气与客套话：直接输出重写后的纯小说正文内容，绝不允许添加"好的"、"已为您重写"等废话。
    """
        return [{"role": "user", "content": prompt.strip()}]

    # 【修改点 1】：新增 include_next 参数
    def build_generation_prompt(self, target_node: dict, tree_data: dict, checked_setting_paths: list, generate_image: bool = True, word_count: int = 5000, include_next: bool = True) -> list:
        """
        构建最终发送给大模型的上下文消息列表（OpenAI 格式）
        """
        # 1. 解析并拼接打钩的世界观设定
        settings_text = self._build_settings_text(checked_setting_paths)

        # 2. 在大纲树中寻找当前节点的位置
        parents, prev_node, next_node = self._find_node_context(tree_data.get("nodes", []), target_node)
        
        # 3. 拼接上下文结构（大纲关联信息）
        # 【修改点 2】：根据 UI 传来的开关，决定是否丢弃 next_node 的信息
        outline_context_text = self._build_outline_context_text(parents, prev_node, next_node if include_next else None)

        # 4. 分离获取当前场景的【概要】和【已有正文】
        target_summary = target_node.get("summary", "").strip()
        if not target_summary:
            target_summary = "(当前场景暂无概要，请根据前后文和设定自由发挥)"

        target_content = self._read_node_content(target_node).strip()
        if not target_content or (target_content.startswith("#") and len(target_content.split('\n')) <= 3):
            target_content = "(当前正文为空，请从头开始撰写本场景正文)"

        # 5. 组装终极 Prompt，传入新增的参数
        prompt = self._assemble_final_prompt(
            target_title=target_node.get("title", "未命名场景"),
            settings_text=settings_text,
            outline_context_text=outline_context_text,
            target_summary=target_summary,
            target_content=target_content,
            generate_image=generate_image,
            word_count=word_count
        )

        return [{"role": "user", "content": prompt}]

    def _build_settings_text(self, setting_paths: list) -> str:
        """读取选中的 JSON 设定文件，转化为可读文本"""
        if not setting_paths:
            return "（无特定世界观设定）"

        nodes: list[WorldSettingNode] = []
        for path in setting_paths:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                setting_name = os.path.basename(path).replace(".json", "")
                cat_name = os.path.basename(os.path.dirname(path))
                if isinstance(data, dict):
                    nodes.append(
                        WorldSettingNode(
                            category=cat_name,
                            name=setting_name,
                            payload=data,
                            priority=self._estimate_setting_priority(cat_name, data),
                        )
                    )
                else:
                    logger.warning(f"设定文件格式非 JSON 对象，已跳过: {path}")
            except Exception as e:
                logger.error(f"读取设定文件失败 {path}: {e}")

        return self.world_compressor.compress(nodes)

    def _estimate_setting_priority(self, category: str, data: dict) -> float:
        """根据设定类型和字段密度，粗略估计压缩时优先级。"""
        base_by_category = {
            "人物设定": 1.35,
            "公共设定": 1.25,
            "地点设定": 1.15,
            "名词设定": 1.05,
            "其他设定": 1.0,
        }
        base = base_by_category.get(category, 1.0)
        filled_fields = 0
        for value in data.values():
            if isinstance(value, str) and value.strip():
                filled_fields += 1
            elif isinstance(value, (list, dict)) and value:
                filled_fields += 1
        return base + min(0.8, filled_fields * 0.05)

    def _find_node_context(self, nodes: list, target_node: dict):
        """
        深度优先搜索(DFS)，将大纲树展平以寻找【全局】的上一场景和下一场景（跨章节寻找）。
        返回：(父节点列表, 上一节点, 下一节点)
        """
        flat_scenes = []
        
        # 辅助函数：递归展平，收集所有最底层的场景及其父级路径
        def flatten(current_nodes, current_path):
            for node in current_nodes:
                path = current_path + [node]
                if not node.get("children"): # 没有子节点，说明是底层场景
                    flat_scenes.append((node, current_path))
                else:
                    flatten(node.get("children", []), path)
                    
        flatten(nodes, [])
        
        parents = []
        prev_node = None
        next_node = None
        
        # 在展平的全局场景列表中定位当前节点
        for i, (node, path) in enumerate(flat_scenes):
            if node is target_node:
                parents = path
                if i > 0:
                    prev_node = flat_scenes[i-1][0]
                if i < len(flat_scenes) - 1:
                    next_node = flat_scenes[i+1][0]
                break
                
        return parents, prev_node, next_node

    def _build_outline_context_text(self, parents: list, prev_node: dict, next_node: dict) -> str:
        """
        构建上级大纲（概要）和前后文（概要+正文片段）的提示词文本。
        注意：父级（1/2级）概要始终原样保留，不做压缩；
        仅在预算不足时压缩相邻场景（prev/next）的概要文本。
        """
        blocks: list[str] = []

        # 1) 父级节点（章、节）不压缩
        if parents:
            blocks.append("【所属章节大纲】")
            for p in parents:
                title = p.get("title", "未命名")
                summary = p.get("summary", "").strip() or "(该层级无概要)"
                blocks.append(f"<{title}> 概要:\n{summary}\n")
        parent_text_len = len("\n".join(blocks))

        adjacent_items: list[dict] = []
        if prev_node:
            prev_content = self._read_node_content(prev_node).strip()
            adjacent_items.append(
                {
                    "label": f"【上一相邻场景: {prev_node.get('title')}】",
                    "summary": prev_node.get("summary", "").strip(),
                    "content_ref": (
                        prev_content[-500:] if len(prev_content) > 500 else prev_content
                    ),
                    "content_prefix": "正文结尾参考:\n...",
                    "content_suffix": "\n",
                }
            )
        if next_node:
            next_content = self._read_node_content(next_node).strip()
            adjacent_items.append(
                {
                    "label": f"【下一相邻场景: {next_node.get('title')}】",
                    "summary": next_node.get("summary", "").strip(),
                    "content_ref": (
                        next_content[:300] if len(next_content) > 300 else next_content
                    ),
                    "content_prefix": "正文开篇参考:\n",
                    "content_suffix": "...\n",
                }
            )

        summary_limit = None
        if adjacent_items:
            raw_adjacent_len = 0
            for item in adjacent_items:
                raw_adjacent_len += (
                    len(item["label"])
                    + len(item["summary"])
                    + len(item["content_ref"])
                    + 64
                )
            available = max(
                320, self._get_outline_adjacent_budget_chars() - parent_text_len
            )
            if raw_adjacent_len > available:
                summary_limit = self._get_adjacent_summary_limit(
                    available_chars=available,
                    summary_count=len(adjacent_items),
                )

        # 2) 相邻场景：按需压缩概要（仅概要，父级不动）
        for item in adjacent_items:
            blocks.append(item["label"])
            summary_text = item["summary"] or "(本场景暂无概要)"
            if summary_limit and item["summary"]:
                summary_text = self._truncate_text_with_ellipsis(
                    item["summary"], summary_limit
                )
            blocks.append(f"剧情概要: {summary_text}")
            if item["content_ref"]:
                blocks.append(
                    f"{item['content_prefix']}{item['content_ref']}{item['content_suffix']}"
                )

        return "\n".join(blocks) if blocks else "（无相关大纲上下文）"

    def _get_outline_adjacent_budget_chars(self) -> int:
        """估算前后相邻场景在提示词中可占用的字符预算。"""
        ratio_map = {
            "conservative": 0.22,
            "balanced": 0.16,
            "aggressive": 0.12,
        }
        ratio = ratio_map.get(self._compression_profile, 0.16)
        budget_tokens = int(self.world_compressor.model_context_size * ratio)
        estimated_chars = max(900, int(budget_tokens * 1.4))
        hard_caps = {
            "conservative": 9000,
            "balanced": 6500,
            "aggressive": 4500,
        }
        return min(estimated_chars, hard_caps.get(self._compression_profile, 6500))

    def _get_adjacent_summary_limit(self, available_chars: int, summary_count: int) -> int:
        """计算每个相邻概要可保留的最大长度。"""
        if summary_count <= 0:
            return 120
        profile_caps = {
            "conservative": 260,
            "balanced": 170,
            "aggressive": 110,
        }
        hard_cap = profile_caps.get(self._compression_profile, 170)
        # 给概要分配约 38% 的可用预算，其余留给相邻正文片段结构
        per_limit = int(max(60, (available_chars * 0.38) / summary_count))
        return min(hard_cap, per_limit)

    def _truncate_text_with_ellipsis(self, text: str, max_chars: int) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 11)].rstrip() + "...(省略)"

    def _read_node_content(self, node: dict) -> str:
        """辅助方法：通过节点字典读取对应的 Markdown 文件内容"""
        if not node or not self.workspace:
            return ""
            
        rel_path = node.get("file_path")
        if not rel_path:
            return ""
            
        full_path = os.path.join(self.workspace.text_path, rel_path)
        if os.path.exists(full_path):
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception:
                return ""
        return ""

    def _estimate_token_count(self, text: str) -> int:
        """粗略估算 token 数，便于在本地做超长判断。"""
        if not text:
            return 0
        cjk_count = 0
        for ch in text:
            code_point = ord(ch)
            if 0x4E00 <= code_point <= 0x9FFF:
                cjk_count += 1
        non_cjk = max(0, len(text) - cjk_count)
        return cjk_count + int(non_cjk / 4) + 1

    def _split_children_for_llm_compress(
        self,
        child_items: list[dict],
        group_token_budget: int,
    ) -> list[list[dict]]:
        """按估算 token 预算将子节点切分为多个分组。"""
        if not child_items:
            return []
        groups: list[list[dict]] = []
        current_group: list[dict] = []
        current_tokens = 0
        for item in child_items:
            item_tokens = self._estimate_token_count(
                f"{item['idx']}. {item['title']}\n{item['summary']}\n"
            )
            if current_group and current_tokens + item_tokens > group_token_budget:
                groups.append(current_group)
                current_group = [item]
                current_tokens = item_tokens
                continue
            current_group.append(item)
            current_tokens += item_tokens
        if current_group:
            groups.append(current_group)
        return groups

    def _parse_compacted_children_json(
        self,
        raw_text: str,
        fallback_group: list[dict],
    ) -> list[dict]:
        """
        解析 LLM 返回的子节点压缩 JSON；失败时回退到原文。
        期望格式: [{"idx":1,"title":"场景1","summary":"..."}, ...]
        """
        try:
            cleaned = raw_text.strip()
            match = re.search(r"\[[\s\S]*\]", cleaned)
            if match:
                cleaned = match.group(0)
            data = json.loads(cleaned)
            if not isinstance(data, list):
                raise ValueError("not list")
            parsed: list[dict] = []
            for i, item in enumerate(data):
                if not isinstance(item, dict):
                    continue
                idx = int(item.get("idx", fallback_group[i]["idx"] if i < len(fallback_group) else i + 1))
                title = str(item.get("title", "")).strip() or (
                    fallback_group[i]["title"] if i < len(fallback_group) else f"子节点{idx}"
                )
                summary = str(item.get("summary", "")).strip()
                if not summary and i < len(fallback_group):
                    summary = fallback_group[i]["summary"]
                parsed.append({"idx": idx, "title": title, "summary": summary})
            if parsed:
                return parsed
        except Exception:
            pass
        return fallback_group

    def _compress_children_summaries_with_llm(
        self,
        children: list[dict],
        llm_client,
        progress_callback=None,
    ) -> list[dict]:
        """
        对超长子节点概要执行两阶段压缩：
        1) 分组压缩
        2) 全局去重拼接
        """
        child_items = []
        for idx, child in enumerate(children, start=1):
            child_items.append(
                {
                    "idx": idx,
                    "title": child.get("title", "未命名"),
                    "summary": child.get("summary", "").strip() or "(暂无概要)",
                }
            )
        if not child_items:
            return []

        # 单组预算按模型窗口比例计算，避免不同模型下过早或过晚切分
        groups = self._split_children_for_llm_compress(
            child_items,
            group_token_budget=self._get_children_group_budget_tokens(),
        )
        compacted_groups: list[list[dict]] = []

        for group_index, group in enumerate(groups, start=1):
            if callable(progress_callback):
                progress_callback(
                    f"子节点概要过长，正在执行分组压缩 {group_index}/{len(groups)}..."
                )
            group_payload = []
            for item in group:
                group_payload.append(
                    f"{item['idx']}. <{item['title']}>\n{item['summary']}"
                )
            group_prompt = f"""你是小说大纲压缩助手。请压缩以下子节点概要。

要求：
1. 保留每个子节点的核心剧情，不得杜撰，不得打乱顺序。
2. 每个子节点保留时间/地点/人物/事件主干，压缩为 70-160 字。
3. 仅输出 JSON 数组，不要任何解释文字。
4. 输出格式严格为：
[
  {{"idx": 1, "title": "场景1", "summary": "压缩后的概要"}}
]

待压缩内容：
{chr(10).join(group_payload)}
"""
            try:
                if callable(progress_callback):
                    progress_callback(
                        f"正在提交压缩请求（分组 {group_index}/{len(groups)}）..."
                    )
                raw = llm_client.generate_text(
                    prompt=group_prompt.strip(),
                    override_system_instruction=llm_client.summary_system_instruction,
                    progress_callback=progress_callback,
                )
                compacted_groups.append(
                    self._parse_compacted_children_json(raw, group)
                )
            except Exception:
                compacted_groups.append(group)

        if len(compacted_groups) == 1:
            return compacted_groups[0]

        if callable(progress_callback):
            progress_callback("正在拼接各分组概要并去重...")
        merge_source = []
        for group in compacted_groups:
            for item in group:
                merge_source.append(
                    {
                        "idx": item["idx"],
                        "title": item["title"],
                        "summary": item["summary"],
                    }
                )
        merge_prompt = f"""你将收到同一批子节点的分组压缩结果，请去重并拼接为最终版本。

要求：
1. 保持 idx 顺序递增。
2. 删除重复信息，但不能丢失关键剧情主干。
3. 每个节点 summary 控制在 70-160 字。
4. 仅输出 JSON 数组，不要解释。
5. 输出格式：
[
  {{"idx": 1, "title": "场景1", "summary": "最终去重后的概要"}}
]

分组结果：
{json.dumps(merge_source, ensure_ascii=False, indent=2)}
"""
        try:
            if callable(progress_callback):
                progress_callback("正在提交压缩请求（最终去重拼接）...")
            merged_raw = llm_client.generate_text(
                prompt=merge_prompt.strip(),
                override_system_instruction=llm_client.summary_system_instruction,
                progress_callback=progress_callback,
            )
            merged = self._parse_compacted_children_json(merged_raw, merge_source)
            return sorted(merged, key=lambda x: int(x.get("idx", 0)))
        except Exception:
            # 去重拼接失败时，回退到分组结果直连
            merged_fallback = []
            for group in compacted_groups:
                merged_fallback.extend(group)
            return sorted(merged_fallback, key=lambda x: int(x.get("idx", 0)))

    def _build_children_context_text(
        self,
        children: list[dict],
        llm_client=None,
        progress_callback=None,
    ) -> str:
        """构建子节点信息文本；超长时触发分组压缩+去重拼接。"""
        if not children:
            return ""
        raw_blocks = []
        for child in children:
            child_title = child.get("title", "未命名")
            child_summary = child.get("summary", "").strip() or "(暂无概要)"
            raw_blocks.append(f"<{child_title}> 概要:\n{child_summary}\n")
        raw_text = "\n".join(raw_blocks)

        trigger_tokens = self._get_children_summary_trigger_tokens()
        raw_tokens = self._estimate_token_count(raw_text)
        if not llm_client or raw_tokens <= trigger_tokens:
            return raw_text
        if callable(progress_callback):
            progress_callback(
                "检测到子节点概要超长，触发分组压缩："
                f"当前约 {raw_tokens} tokens，阈值约 {trigger_tokens} tokens。"
            )

        compacted_items = self._compress_children_summaries_with_llm(
            children=children,
            llm_client=llm_client,
            progress_callback=progress_callback,
        )
        compacted_blocks = []
        for item in compacted_items:
            compacted_blocks.append(
                f"{item.get('idx', 0)}. <{item.get('title', '未命名')}> 概要:\n{item.get('summary', '(暂无概要)')}\n"
            )
        return "\n".join(compacted_blocks)
    
    def build_summary_sync_prompt(self, node_title: str, node_level: int, old_summary: str, actual_content: str) -> str:
        """构建校验和同步概要的提示词"""
        level_str = "场景(底层)" if node_level == 3 else "章/节(父级)"
        prompt = f"""你是一个专业的小说编辑。你的任务是校对小说大纲中节点的【原概要】与【实际正文/子节点内容】是否匹配。
如果存在冲突、不匹配或者原概要为空，请严格以【实际内容参考】为准事实，重写该节点的剧情概要。
如果基本匹配，请进行适当润色，补充缺失的关键细节。

### 当前节点信息
- 节点名称：【{node_title}】
- 节点层级：{level_str}

### 核心要素提取要求（最高优先级，必须100%遵守）
无论实际内容有多长、多复杂，你必须逐一捕捉并在概要中明确写出以下所有维度的信息，缺一不可：

1. **时间要素**：
   - 必须提取并写明故事发生的【时间】（无论是"开皇三年"、"八月十五"等绝对时间，还是"三天后"、"黄昏时分"、"酒过三巡"等相对时间）
   - 如果实际内容没有明确时间，也要写明"（无明确时间交代）"

2. **地点要素**：
   - 必须写明故事发生的【具体地点】（如"长安城皇宫太极殿"、"东海渔村码头"）
   - 若发生【场景转移】，必须明确指出【从哪里移动到了哪里】，移动轨迹要清晰

3. **人物要素**：
   - 必须列出所有【出场的核心人物】姓名
   - 必须提炼出人物的【心境/情绪转变】（如"从愤怒逐渐转为平静"）
   - 必须写明【衣着/装备/外貌的改变】（如"换上了夜行衣"、"身上增添了新的伤痕"）
   - 必须记录【队伍成员的增减/聚散变化】（如"张三加入队伍"、"李四离队"）

4. **事件交互要素**：
   - 必须明确交代【谁和谁】在该时间地点【具体完成了什么事】
   - 必须写明【关键冲突或转折点】
   - 必须记录【重要物品的获得/失去/转移】

### 【原概要】
{old_summary if old_summary else "(空)"}

### 【实际内容参考】
{actual_content if actual_content else "(空)"}

### 输出要求（绝对红线）
1. 必须完全以【实际内容参考】作为事实依据，不要自行捏造不存在的剧情。
2. 概要必须完整包含上述"核心要素提取要求"的全部4个维度，缺一则视为不合格。
3. 请直接输出最终的概要内容，必须采用结构化的格式输出，包含以下明确的标识：
   【时间】：...
   【地点】：...
   【人物】：...
   【事件】：...
   【剧情总结】：（简短概括核心剧情，控制在100-200字以内，不要大段摘抄原文）
4. 禁止输出任何前缀、解释、说明，绝对不允许包含"好的"、"为您修改"等客套废话。
"""
        return prompt.strip()

    def build_summary_prompt(
        self,
        target_node: dict,
        tree_data: dict,
        checked_setting_paths: list,
        llm_client=None,
        progress_callback=None,
    ) -> list:
        """构建生成场景概要(Summary)的专属上下文"""
        target_title = target_node.get("title", "未命名场景")
        settings_text = self._build_settings_text(checked_setting_paths)
        
        # 获取父节点、子节点和同级节点
        parents = []
        children = target_node.get("children", [])
        siblings = []
        target_level = 0
        prev_node = None
        next_node = None
        
        # 寻找父节点、同级节点和目标节点的层级
        def find_node_info(current_nodes, current_path, level):
            nonlocal parents, siblings, target_level
            for i, node in enumerate(current_nodes):
                path = current_path + [node]
                if node is target_node:
                    parents = current_path
                    target_level = level
                    # 收集同级节点
                    for j, sibling in enumerate(current_nodes):
                        if sibling is not target_node:
                            siblings.append((j, sibling))
                    return True
                if node.get("children"):
                    if find_node_info(node.get("children", []), path, level + 1):
                        return True
            return False
        
        find_node_info(tree_data.get("nodes", []), [], 1)
        
        # 如果是第3级节点，也查找前后相邻节点
        if target_level == 3:
            flat_scenes = []
            
            def flatten(current_nodes, current_path):
                for node in current_nodes:
                    path = current_path + [node]
                    if not node.get("children"):
                        flat_scenes.append((node, current_path))
                    else:
                        flatten(node.get("children", []), path)
                    
            flatten(tree_data.get("nodes", []), [])
            
            for i, (node, path) in enumerate(flat_scenes):
                if node is target_node:
                    if i > 0:
                        prev_node = flat_scenes[i-1][0]
                    if i < len(flat_scenes) - 1:
                        next_node = flat_scenes[i+1][0]
                    break
        
        # 构建上下文文本
        context_blocks = []
        
        # 父节点信息
        if parents:
            context_blocks.append("【所属章节大纲】")
            for p in parents:
                title = p.get("title", "未命名")
                summary = p.get("summary", "").strip() or "(该层级无概要)"
                context_blocks.append(f"<{title}> 概要:\n{summary}\n")
        
        # 同级节点信息（仅针对2级节点）
        if target_level == 2 and siblings:
            context_blocks.append("【同级节点信息】")
            # 按顺序排列同级节点
            sorted_siblings = sorted(siblings, key=lambda x: x[0])
            for idx, (original_idx, sibling) in enumerate(sorted_siblings):
                sibling_title = sibling.get("title", "未命名")
                sibling_summary = sibling.get("summary", "").strip() or "(暂无概要)"
                context_blocks.append(f"{original_idx + 1}. <{sibling_title}> 概要:\n{sibling_summary}\n")
            
            # 说明目标节点在同级中的位置
            # 找到目标节点在原列表中的位置
            target_position = -1
            all_nodes = tree_data.get("nodes", [])
            if parents:
                parent_node = parents[-1]
                all_nodes = parent_node.get("children", [])
            
            for i, node in enumerate(all_nodes):
                if node is target_node:
                    target_position = i + 1
                    break
            
            if target_position != -1:
                context_blocks.append(f"\n【目标节点位置】\n当前节点【{target_title}】在同级节点中位于第 {target_position} 位。\n")
        
        # 子节点信息
        if children:
            context_blocks.append("【子节点信息】")
            children_context = self._build_children_context_text(
                children=children,
                llm_client=llm_client,
                progress_callback=progress_callback,
            )
            context_blocks.append(children_context)
        
        # 对于第3级节点，添加正文内容和前后相邻节点信息
        if target_level == 3:
            target_content = self._read_node_content(target_node).strip()
            if target_content and not (target_content.startswith("#") and len(target_content.split('\n')) <= 3):
                context_blocks.append("【当前场景正文】")
                context_blocks.append(target_content)
            
            if prev_node:
                prev_title = prev_node.get("title")
                prev_summary = prev_node.get("summary", "").strip()
                prev_content = self._read_node_content(prev_node).strip()
                
                context_blocks.append(f"\n【上一相邻场景: {prev_title}】")
                context_blocks.append(f"剧情概要: {prev_summary if prev_summary else '(本场景暂无概要)'}")
                
                if prev_content:
                    tail = prev_content[-500:] if len(prev_content) > 500 else prev_content
                    context_blocks.append(f"正文结尾参考:\n...{tail}")
            
            if next_node:
                next_title = next_node.get("title")
                next_summary = next_node.get("summary", "").strip()
                next_content = self._read_node_content(next_node).strip()
                
                context_blocks.append(f"\n【下一相邻场景: {next_title}】")
                context_blocks.append(f"剧情概要: {next_summary if next_summary else '(本场景暂无概要)'}")
                
                if next_content:
                    head = next_content[:300] if len(next_content) > 300 else next_content
                    context_blocks.append(f"正文开篇参考:\n{head}...")
        
        context_text = "\n".join(context_blocks) if context_blocks else "（无相关上下文）"
        
        # 构建提示词
        prompt = f"""
你是一个专业的小说编辑。请根据提供的上下文信息，为指定的场景生成一个精准、结构化的剧情概要(Summary)。

### 一、 世界观与设定参考
{settings_text}

### 二、 核心要素提取要求（最高优先级，必须100%遵守）
无论正文有多长、内容有多复杂，你必须逐一捕捉并在概要中明确写出以下所有维度的信息，缺一不可：

1. **时间要素**：
   - 必须提取并写明故事发生的【时间】（无论是"开皇三年"、"八月十五"等绝对时间，还是"三天后"、"黄昏时分"、"酒过三巡"等相对时间）
   - 如果正文没有明确时间，也要写明"（无明确时间交代）"

2. **地点要素**：
   - 必须写明故事发生的【具体地点】（如"长安城皇宫太极殿"、"东海渔村码头"）
   - 若发生【场景转移】，必须明确指出【从哪里移动到了哪里】，移动轨迹要清晰

3. **人物要素**：
   - 必须列出所有【出场的核心人物】姓名
   - 必须提炼出人物的【心境/情绪转变】（如"从愤怒逐渐转为平静"）
   - 必须写明【衣着/装备/外貌的改变】（如"换上了夜行衣"、"身上增添了新的伤痕"）
   - 必须记录【队伍成员的增减/聚散变化】（如"张三加入队伍"、"李四离队"）

4. **事件交互要素**：
   - 必须明确交代【谁和谁】在该时间地点【具体完成了什么事】
   - 必须写明【关键冲突或转折点】
   - 必须记录【重要物品的获得/失去/转移】

### 三、 当前任务
请为场景【{target_title}】生成剧情概要，要求：
1. 概要必须完整包含上述第一部分"核心要素提取要求"的全部4个维度，缺一则视为不合格
2. 语言凝练，控制在 150-400 字之间（为确保要素完整，可适当超出400字）
3. 突出场景的关键冲突、人物和情节发展
4. 与父节点的主题保持一致，同时为子节点的发展做铺垫
5. 如果是2级节点，请确保概要与同级节点的内容连贯，符合其在序列中的位置
6. 如果是3级场景节点，请完全基于提供的【当前场景正文】内容生成概要，不要自行捏造不存在的剧情

### 四、 输出格式与要求（绝对红线）
1. 请直接输出概要内容，不要添加任何前缀或后缀。
2. 必须采用结构化的格式输出，包含以下明确的标识：
   【时间】：...
   【地点】：...
   【人物】：...
   【事件】：...
   【剧情总结】：（简短概括核心剧情，控制在100-200字以内，不要大段摘抄原文）
3. 严禁任何助手语气与客套话，如"好的"、"已为您生成"等
4. 确保概要内容与上下文信息逻辑连贯

### 五、 上下文信息
{context_text}
"""
        
        return [{"role": "user", "content": prompt.strip()}]

    def _assemble_final_prompt(self, target_title: str, settings_text: str, outline_context_text: str, target_summary: str, target_content: str, generate_image: bool, word_count: int) -> str:
        """拼接终极提示词，明确区分概要与正文的任务要求"""
        
        # 动态处理图片生成规则
        image_rule = ""
        if generate_image:
            image_rule = """
   【插图生成规则】：应当在你认为适合表现画面的位置（如人物初登场、宏大场景、激烈冲突）插入图片描述符。请严格按照以下格式留下引用占位符，务必使用英文描述画面细节以便后续AI画图：
   `![English description of the scene, highly detailed, cinematic lighting](/images/placeholder_xxx.png)`
   请自行替换描述内容和 xxx 的占位编号。"""

        prompt = f"""
你是一个专业的小说创作者。请根据提供的世界观设定、大纲上下文，为指定的场景生成完整的小说正文内容。

### 一、 世界观与设定参考
{settings_text}

### 二、 大纲上下文位置
{outline_context_text}

### 三、 当前生成任务
你需要生成正文的当前场景是：【{target_title}】。
【字数要求】：请务必生成大约 {word_count} 字左右的内容，细节要丰满。
【剧情延续性警告】：本场景仅为宏大故事链条中的一个过渡或阶段。请务必保持故事的开放性、悬念与发展空间，严禁在本场景中强行完结整个故事或写出类似大结局的总结性段落！

【剧情演绎强制规范】（非常重要）：
由于本场景的剧情概要中包含了严谨的【时间、地点、人物交互与状态变化】，你在撰写正文时，必须将这些要素饱满地演绎出来：
1. 准确落实概要中提及的时间点与场景地点，如有位置转移必须生动写明移动的过程与沿途画面。
2. 细致描写概要中强调的人物心境变化、衣着/装备/外貌的改变，将其自然融入动作与神态中。
3. 将概要中的"事件交互"通过具体的对话、动作和心理描写深度展开，切勿敷衍了事。

【本场景剧情概要】（你必须严格遵循此情节主线进行创作）：
{target_summary}

【本场景已有正文草稿】（如果提示为空，请直接撰写；如果有内容，请在此基础上进行润色或往后续写）：
{target_content}

### 四、 输出格式与要求（绝对红线）
1. 请根据上述信息，输出【{target_title}】的正文内容，采用 Markdown 格式排版。
2. **严禁任何助手语气与客套话**：你必须直接、且仅仅输出小说正文内容本身。绝对不允许在开头或结尾添加任何诸如"好的"、"已为您生成xxx"、"以下是为您创作的正文"、"希望您满意"等废话。你的输出第一个字必须是小说的正文，最后一个字必须是小说的结尾。{image_rule}
"""
        return prompt.strip()
