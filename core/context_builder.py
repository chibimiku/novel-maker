import os
import json
import logging

logger = logging.getLogger(__name__)

class ContextBuilder:
    def __init__(self, workspace_manager):
        """
        初始化上下文构建器
        :param workspace_manager: WorkspaceManager 实例，用于读取本地文件
        """
        self.workspace = workspace_manager

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
    4. 严禁任何助手语气与客套话：直接输出重写后的纯小说正文内容，绝不允许添加“好的”、“已为您重写”等废话。
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

        text_blocks = []
        for path in setting_paths:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                setting_name = os.path.basename(path).replace(".json", "")
                cat_name = os.path.basename(os.path.dirname(path))
                
                text_blocks.append(f"【{cat_name} - {setting_name}】")
                for key, value in data.items():
                    # 特别处理字典或列表类型的复合设定值
                    if isinstance(value, str) and value.strip():  
                        text_blocks.append(f"- {key}: {value}")
                    elif isinstance(value, (dict, list)):
                        text_blocks.append(f"- {key}: {json.dumps(value, ensure_ascii=False)}")
                text_blocks.append("") 
            except Exception as e:
                logger.error(f"读取设定文件失败 {path}: {e}")
                
        return "\n".join(text_blocks)

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
        """构建上级大纲（概要）和前后文（概要+正文片段）的提示词文本"""
        blocks = []
        
        # 1. 父级节点（章、节）：只读取字典里的 summary
        if parents:
            blocks.append("【所属章节大纲】")
            for p in parents:
                title = p.get("title", "未命名")
                summary = p.get("summary", "").strip() or "(该层级无概要)"
                blocks.append(f"<{title}> 概要:\n{summary}\n")

        # 2. 上一相邻节点：读取 summary，并读取 MD 文件末尾作为文风和情节衔接
        if prev_node:
            prev_title = prev_node.get("title")
            prev_summary = prev_node.get("summary", "").strip()
            prev_content = self._read_node_content(prev_node).strip()
            
            blocks.append(f"【上一相邻场景: {prev_title}】")
            
            # 【修复点 3】：强制输出概要，即使为空也给占位提示，确保上下文结构完整
            blocks.append(f"剧情概要: {prev_summary if prev_summary else '(本场景暂无概要)'}")
            
            if prev_content:
                # 截取最后 500 个字符用于衔接
                tail = prev_content[-500:] if len(prev_content) > 500 else prev_content
                blocks.append(f"正文结尾参考:\n...{tail}\n")

        # 3. 下一相邻节点：同理，读取 MD 文件开头
        if next_node:
            next_title = next_node.get("title")
            next_summary = next_node.get("summary", "").strip()
            next_content = self._read_node_content(next_node).strip()
            
            blocks.append(f"【下一相邻场景: {next_title}】")
            
            # 【修复点 3】：同上，强制输出下一场景的概要
            blocks.append(f"剧情概要: {next_summary if next_summary else '(本场景暂无概要)'}")
            
            if next_content:
                # 截取开篇 300 个字符
                head = next_content[:300] if len(next_content) > 300 else next_content
                blocks.append(f"正文开篇参考:\n{head}...\n")

        return "\n".join(blocks) if blocks else "（无相关大纲上下文）"

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
    
    def build_summary_sync_prompt(self, node_title: str, node_level: int, old_summary: str, actual_content: str) -> str:
        """构建校验和同步概要的提示词"""
        level_str = "场景(底层)" if node_level == 3 else "章/节(父级)"
        prompt = f"""你是一个专业的小说编辑。你的任务是校对小说大纲中节点的【原概要】与【实际正文/子节点内容】是否匹配。
如果存在冲突、不匹配或者原概要为空，请严格以【实际内容参考】为准事实，重写该节点的剧情概要。
如果基本匹配，请进行适当润色，补充缺失的关键细节。

### 当前节点信息
- 节点名称：【{node_title}】
- 节点层级：{level_str}

### 核心要素提取要求（修改或重写概要时必须遵循）
1. **时间与事件交互**：若实际内容包含时间描述（绝对时间或相对时间），概要中必须明确交代【时间】、【地点】、【出场人物】，以及明确的【事件交互】（谁和谁具体完成了什么事）。
2. **状态与变化**：必须提炼出人物的【心境/情绪转变】、【衣着/装备/外貌的改变】，以及【队伍成员的增减变化】。
3. **场景与轨迹**：必须写明【具体地点】。若有场景转移，指出【从哪里移动到了哪里】。

### 【原概要】
{old_summary if old_summary else "(空)"}

### 【实际内容参考】
{actual_content if actual_content else "(空)"}

### 输出要求（绝对红线）
1. 必须完全以【实际内容参考】作为事实依据，不要自行捏造不存在的剧情。
2. 概要需要结构清晰，将上述要求的时间、地点、人物变化、核心交互等信息概括进去。
3. 请直接输出最终的概要内容，禁止输出任何前缀、解释、说明，绝对不允许包含“好的”、“为您修改”等客套废话。
"""
        return prompt.strip()

    def build_summary_prompt(self, target_node: dict, tree_data: dict, checked_setting_paths: list) -> list:
        """构建生成场景概要(Summary)的专属上下文"""
        target_title = target_node.get("title", "未命名场景")
        
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
            for child in children:
                child_title = child.get("title", "未命名")
                child_summary = child.get("summary", "").strip() or "(暂无概要)"
                context_blocks.append(f"<{child_title}> 概要:\n{child_summary}\n")
        
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
你是一个专业的小说创作者。请根据提供的上下文信息，为指定的场景生成一个精准、结构化的剧情概要(Summary)。

### 一、 上下文信息
{context_text}

### 二、 核心要素提取要求（绝对红线）
在概括剧情时，你必须敏锐捕捉并明确写出以下维度的信息：
1. **时间与事件交互**：若正文包含时间描述（无论是“某年某月”的绝对时间，还是“三天后”、“黄昏”等相对时间），必须交代清楚该时间点下的【时间】、【地点】、【出场人物】，以及明确的【事件交互】（谁和谁具体完成了什么事）。
2. **人物状态与变化**：必须提炼出核心人物的【心境/情绪转变】、【衣着/装备/外貌的改变】，以及【队伍成员的增减/聚散变化】。
3. **场景与移动轨迹**：必须写明故事发生的【具体地点】。若发生位置转移，需明确指出【从哪里移动到了哪里】。

### 三、 当前任务
请为场景【{target_title}】生成剧情概要，要求：
1. 概要必须融合上述"核心要素提取要求"，不仅要概括大意，更要突出变化。
2. 语言凝练，控制在 150-400 字之间（为确保细节完整，字数可适当浮动）。
3. 突出场景的关键冲突、人物和情节发展。
4. 与父节点的主题保持一致，同时为子节点的发展做铺垫。
5. 如果是2级节点，请确保概要与同级节点的内容连贯，符合其在序列中的位置。
6. 如果是3级场景节点，请完全基于提供的【当前场景正文】内容生成概要，不要自行捏造不存在的剧情。

### 四、 输出格式与要求（绝对红线）
1. 请直接输出概要内容，不要添加任何前缀或后缀。
2. 严禁任何助手语气与客套话，如"好的"、"已为您生成"等。
3. 确保概要内容与上下文信息逻辑连贯。
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
3. 将概要中的“事件交互”通过具体的对话、动作和心理描写深度展开，切勿敷衍了事。

【本场景剧情概要】（你必须严格遵循此情节主线进行创作）：
{target_summary}

【本场景已有正文草稿】（如果提示为空，请直接撰写；如果有内容，请在此基础上进行润色或往后续写）：
{target_content}

### 四、 输出格式与要求（绝对红线）
1. 请根据上述信息，输出【{target_title}】的正文内容，采用 Markdown 格式排版。
2. **严禁任何助手语气与客套话**：你必须直接、且仅仅输出小说正文内容本身。绝对不允许在开头或结尾添加任何诸如“好的”、“已为您生成xxx”、“以下是为您创作的正文”、“希望您满意”等废话。你的输出第一个字必须是小说的正文，最后一个字必须是小说的结尾。{image_rule}
"""
        return prompt.strip()