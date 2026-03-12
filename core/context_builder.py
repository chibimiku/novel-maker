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
        :param target_node: 当前需要生成的节点字典引用
        :param tree_data: 完整的 outline_tree 数据
        :param checked_setting_paths: 用户在左侧勾选的设定 JSON 文件路径列表
        :return: 包含 Prompt 的消息列表 [{"role": "user", "content": "..."}]
        """
        # 1. 解析并拼接打钩的世界观设定
        settings_text = self._build_settings_text(checked_setting_paths)

        # 2. 在大纲树中寻找当前节点的位置，获取父节点（章/节）和兄弟节点（上一场景/下一场景）
        parents, prev_node, next_node = self._find_node_context(tree_data.get("nodes", []), target_node)
        
        # 3. 拼接上下文结构（大纲关联信息）
        outline_context_text = self._build_outline_context_text(parents, prev_node, next_node)

        # 4. 获取当前节点已有的文本（作为本节概要或扩写基础）
        target_content = self._read_node_content(target_node)
        if not target_content.strip():
            target_content = "(当前节点暂无概要，请根据上下文自由发挥)"

        # 5. 组装终极 Prompt
        prompt = self._assemble_final_prompt(
            target_title=target_node.get("title", "未命名"),
            settings_text=settings_text,
            outline_context_text=outline_context_text,
            target_content=target_content
        )

        # 返回标准的消息列表格式
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
                
                # 获取文件名作为设定类别名
                setting_name = os.path.basename(path).replace(".json", "")
                cat_name = os.path.basename(os.path.dirname(path))
                
                text_blocks.append(f"【{cat_name} - {setting_name}】")
                for key, value in data.items():
                    if value.strip():  # 只加入有内容的字段
                        text_blocks.append(f"- {key}: {value}")
                text_blocks.append("") # 空行分隔
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
            if node is target_node: # 内存地址比对，精确定位
                prev_node = nodes[i-1] if i > 0 else None
                next_node = nodes[i+1] if i < len(nodes) - 1 else None
                return current_path, prev_node, next_node
            
            if "children" in node:
                result = self._find_node_context(node["children"], target_node, current_path + [node])
                if result:
                    return result
                    
        return [], None, None

    def _build_outline_context_text(self, parents: list, prev_node: dict, next_node: dict) -> str:
        """构建上级大纲和前后文概要的提示词文本"""
        blocks = []
        
        if parents:
            blocks.append("【所属章节概要】")
            for p in parents:
                title = p.get("title", "未命名")
                content = self._read_node_content(p)
                # 若上级包含极长文本，可适当截断或让大模型自行提炼，这里我们直接输入前500字作为概要
                summary = content[:500] + "..." if len(content) > 500 else content
                summary = summary.strip() or "(无概要)"
                blocks.append(f"<{title}>:\n{summary}\n")

        if prev_node:
            prev_title = prev_node.get("title")
            prev_content = self._read_node_content(prev_node)
            summary = prev_content[-500:] # 取上一场景的最后500字作为衔接参考
            blocks.append(f"【上一相邻节点: {prev_title} 的结尾参考】\n{summary}\n")

        if next_node:
            next_title = next_node.get("title")
            next_content = self._read_node_content(next_node)
            if next_content.strip():
                summary = next_content[:300]
                blocks.append(f"【下一相邻节点: {next_title} 的开篇参考】\n{summary}\n")

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

    def _assemble_final_prompt(self, target_title: str, settings_text: str, outline_context_text: str, target_content: str) -> str:
        """拼接终极提示词，明确任务和输出格式（含插图约定的占位符要求）"""
        prompt = f"""
你是一个专业的小说创作者。请根据提供的世界观设定、大纲上下文，生成当前节点的完整正文内容。

### 一、 世界观与设定参考
{settings_text}

### 二、 大纲上下文位置
{outline_context_text}

### 三、 当前生成任务
你需要生成正文的节点是：【{target_title}】。
该节点作者已提供的概要或前期草稿如下：
{target_content}

### 四、 输出格式与要求
1. 请根据上述信息，充分发挥创造力，续写或扩写【{target_title}】的正文内容。内容应当连贯、生动、符合人物性格与世界观。
2. 必须直接输出小说正文，采用 Markdown 格式排版。
3. 【插图生成规则】：应当在你认为适合表现画面的位置（如人物初登场、宏大场景、激烈冲突）插入图片描述符。请严格按照以下格式留下引用占位符，务必使用英文描述画面细节以便后续AI画图：
   `![English description of the scene, highly detailed, cinematic lighting](/images/placeholder_xxx.png)`
   请自行替换描述内容和 xxx 的占位编号。
"""
        return prompt.strip()