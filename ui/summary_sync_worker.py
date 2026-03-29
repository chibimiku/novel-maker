import os
from PyQt6.QtCore import QThread, pyqtSignal
from core.context_builder import ContextBuilder

class SummarySyncWorker(QThread):
    progress_signal = pyqtSignal(str)
    success_signal = pyqtSignal(str, str)  # 返回 before_path, after_path
    error_signal = pyqtSignal(str)

    def __init__(self, target_node, level, llm_client, workspace_manager):
        super().__init__()
        self.target_node = target_node
        self.level = level
        self.llm_client = llm_client
        self.workspace = workspace_manager
        self.context_builder = ContextBuilder(workspace_manager)
        
        self.before_records = []
        self.after_records = []

    def run(self):
        try:
            self.progress_signal.emit(f"开始校验节点树: {self.target_node.get('title', '未命名')}")
            # 采用自底向上的后序遍历，确保父节点能拿到子节点最新的概要
            self.process_node(self.target_node, self.level)
            
            # 生成对比用的临时 Markdown 文件
            temp_dir = os.path.join(self.workspace.workspace_path, "temp_diff")
            os.makedirs(temp_dir, exist_ok=True)
            
            before_path = os.path.join(temp_dir, "summary_before.md")
            after_path = os.path.join(temp_dir, "summary_after.md")

            with open(before_path, 'w', encoding='utf-8') as f:
                f.write("# 校验前原概要汇总\n\n" + "\n\n".join(self.before_records))
                
            with open(after_path, 'w', encoding='utf-8') as f:
                f.write("# 校验后新概要汇总\n\n" + "\n\n".join(self.after_records))

            self.success_signal.emit(before_path, after_path)
        except Exception as e:
            self.error_signal.emit(f"校验同步失败: {str(e)}")

    def process_node(self, node, level):
        title = node.get("title", "未命名")
        old_summary = node.get("summary", "").strip()
        
        # 1. 递归处理所有子节点，收集子节点的新概要
        children_actual_contents = []
        if "children" in node and node["children"]:
            for child in node["children"]:
                child_new_summary = self.process_node(child, level + 1)
                children_actual_contents.append(f"<{child.get('title')}>: {child_new_summary}")
        
        self.progress_signal.emit(f"正在分析节点: {title} (层级 {level})...")

        # 2. 确定“实际内容”的参考来源
        if level == 3:
            # 场景节点：直接读取 MD 正文
            actual_content = self.context_builder._read_node_content(node).strip()
            # 截断过长的正文以防止超长上下文，保留首尾核心片段
            if len(actual_content) > 4000:
                actual_content = actual_content[:2000] + "\n\n...(中间省略)...\n\n" + actual_content[-2000:]
        else:
            # 章/节节点：使用子节点的最新概要汇总作为实际内容
            actual_content = "\n".join(children_actual_contents)

        # 记录修改前的状态
        self.before_records.append(f"## {title} (层级: {level})\n{old_summary if old_summary else '(空)'}\n")

        # 3. 如果底层场景既没有正文也没有旧概要，直接返回空，避免无意义的 LLM 调用
        if not old_summary and not actual_content:
            new_summary = ""
        else:
            # 4. 调用 LLM 进行校验和重写
            prompt = self.context_builder.build_summary_sync_prompt(title, level, old_summary, actual_content)
            new_summary = self.llm_client.generate_text(prompt=prompt).strip()

        # 记录修改后的状态
        self.after_records.append(f"## {title} (层级: {level})\n{new_summary}\n")

        return new_summary