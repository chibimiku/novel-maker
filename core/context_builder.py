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

    def build_generation_prompt(self, target_node: dict, tree_data: dict, checked_setting_paths: list) -> list:
        """
        构建最终发送给大模型的上下文消息列表（OpenAI 格式）
        """
        # 1. 解析并拼接打钩的世界观设定
        settings_text = self._build_settings_text(checked_setting_paths)

        # 2. 在大纲树中寻找当前节点的位置，获取父节点（章/节）和兄弟节点（上一场景/下一场景）
        parents, prev_node, next_node = self._find_node_context(tree_data.get("nodes", []), target_node)
        
        # 3. 拼接上下文结构（大纲关联信息）
        outline_context_text = self._build_outline_context_text(parents, prev_node, next_node)

        # 4. 分离获取当前场景的【概要】和【已有正文】
        target_summary = target_node.get("summary", "").strip()
        if not target_summary:
            target_summary = "(当前场景暂无概要，请根据前后文和设定自由发挥)"

        target_content = self._read_node_content(target_node).strip()
        # 排除掉只有标题占位符的空文件情况
        if not target_content or (target_content.startswith("#") and len(target_content.split('\n')) <= 3):
            target_content = "(当前正文为空，请从头开始撰写本场景正文)"

        # 5. 组装终极 Prompt
        prompt = self._assemble_final_prompt(
            target_title=target_node.get("title", "未命名场景"),
            settings_text=settings_text,
            outline_context_text=outline_context_text,
            target_summary=target_summary,
            target_content=target_content
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

    def _find_node_context(self, nodes: list, target_node: dict, current_path: list = None):
        """
        深度优先搜索(DFS)，查找目标节点并返回：(父节点列表, 上一节点, 下一节点)
        """
        if current_path is None:
            current_path = []

        for i, node in enumerate(nodes):
            if node is target_node: 
                prev_node = nodes[i-1] if i > 0 else None
                next_node = nodes[i+1] if i < len(nodes) - 1 else None
                return current_path, prev_node, next_node
            
            if "children" in node:
                result = self._find_node_context(node["children"], target_node, current_path + [node])
                if result:
                    return result
                    
        return [], None, None

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
            if prev_summary:
                blocks.append(f"剧情概要: {prev_summary}")
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
            if next_summary:
                blocks.append(f"剧情概要: {next_summary}")
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

    def _assemble_final_prompt(self, target_title: str, settings_text: str, outline_context_text: str, target_summary: str, target_content: str) -> str:
        """拼接终极提示词，明确区分概要与正文的任务要求"""
        prompt = f"""
你是一个专业的小说创作者。请根据提供的世界观设定、大纲上下文，为指定的场景生成完整的小说正文内容。

### 一、 世界观与设定参考
{settings_text}

### 二、 大纲上下文位置
{outline_context_text}

### 三、 当前生成任务
你需要生成正文的当前场景是：【{target_title}】。

【本场景剧情概要】（你必须严格遵循此情节主线进行创作）：
{target_summary}

【本场景已有正文草稿】（如果提示为空，请直接撰写；如果有内容，请在此基础上进行润色或往后续写）：
{target_content}

### 四、 输出格式与要求
1. 请根据上述信息，充分发挥创造力，输出【{target_title}】的正文内容。内容应当连贯、生动、符合人物性格与世界观设定。
2. 必须直接输出小说正文，采用 Markdown 格式排版。
3. 【插图生成规则】：应当在你认为适合表现画面的位置（如人物初登场、宏大场景、激烈冲突）插入图片描述符。请严格按照以下格式留下引用占位符，务必使用英文描述画面细节以便后续AI画图：
   `![English description of the scene, highly detailed, cinematic lighting](/images/placeholder_xxx.png)`
   请自行替换描述内容和 xxx 的占位编号。
"""
        return prompt.strip()