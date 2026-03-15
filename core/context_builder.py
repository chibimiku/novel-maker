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
    3. 严禁任何助手语气与客套话：直接输出重写后的纯小说正文内容，绝不允许添加“好的”、“已为您重写”等废话。
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

【本场景剧情概要】（你必须严格遵循此情节主线进行创作）：
{target_summary}

【本场景已有正文草稿】（如果提示为空，请直接撰写；如果有内容，请在此基础上进行润色或往后续写）：
{target_content}

### 四、 输出格式与要求（绝对红线）
1. 请根据上述信息，输出【{target_title}】的正文内容，采用 Markdown 格式排版。
2. **严禁任何助手语气与客套话**：你必须直接、且仅仅输出小说正文内容本身。绝对不允许在开头或结尾添加任何诸如“好的”、“已为您生成xxx”、“以下是为您创作的正文”、“希望您满意”等废话。你的输出第一个字必须是小说的正文，最后一个字必须是小说的结尾。{image_rule}
"""
        return prompt.strip()