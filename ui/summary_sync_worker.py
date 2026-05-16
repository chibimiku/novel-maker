import os
from PyQt6.QtCore import QThread, pyqtSignal
from core.context_builder import ContextBuilder

class SummarySyncWorker(QThread):
    progress_signal = pyqtSignal(str)
    # 修改点1：增加一个 dict 参数用于传回需要覆盖的3级节点数据
    success_signal = pyqtSignal(str, str, dict)  
    error_signal = pyqtSignal(str)

    def __init__(self, target_node, level, llm_client, workspace_manager, force_mode=False):
        super().__init__()
        self.target_node = target_node
        self.level = level
        self.llm_client = llm_client
        self.workspace = workspace_manager
        self.context_builder = ContextBuilder(workspace_manager)
        self.force_mode = force_mode  # 修改点2：接收强制模式参数
        
        self.before_records = []
        self.after_records = []
        self.level3_updates = {} # 存放需要强制更新的3级节点ID和新概要

    def run(self):
        try:
            self.progress_signal.emit(f"开始校验节点树: {self.target_node.get('title', '未命名')}")
            self.process_node(self.target_node, self.level)
            
            temp_dir = os.path.join(self.workspace.workspace_path, "temp_diff")
            os.makedirs(temp_dir, exist_ok=True)
            
            before_path = os.path.join(temp_dir, "summary_before.md")
            after_path = os.path.join(temp_dir, "summary_after.md")

            with open(before_path, 'w', encoding='utf-8') as f:
                f.write("# 校验前原概要汇总\n\n" + "\n\n".join(self.before_records))
                
            with open(after_path, 'w', encoding='utf-8') as f:
                f.write("# 校验后新概要汇总\n\n" + "\n\n".join(self.after_records))

            # 修改点3：将 level3_updates 一并发送回主线程
            self.success_signal.emit(before_path, after_path, self.level3_updates)
        except Exception as e:
            self.error_signal.emit(f"校验同步失败: {str(e)}")

    def process_node(self, node, level):
        title = node.get("title", "未命名")
        old_summary = node.get("summary", "").strip()
        node_id = node.get("id")
        
        children_actual_contents = []
        if "children" in node and node["children"]:
            for child in node["children"]:
                child_new_summary = self.process_node(child, level + 1)
                children_actual_contents.append(f"<{child.get('title')}>: {child_new_summary}")
        
        self.progress_signal.emit(f"正在分析节点: {title} (层级 {level})...")

        if level == 3:
            actual_content = self.context_builder._read_node_content(node).strip()
            if len(actual_content) > 4000:
                actual_content = actual_content[:2000] + "\n\n...(中间省略)...\n\n" + actual_content[-2000:]
        else:
            actual_content = "\n".join(children_actual_contents)

        self.before_records.append(f"## {title} (层级: {level})\n{old_summary if old_summary else '(空)'}\n")

        if not old_summary and not actual_content:
            new_summary = ""
        else:
            prompt = self.context_builder.build_summary_sync_prompt(title, level, old_summary, actual_content)
            new_summary = self.llm_client.generate_text(
                prompt=prompt,
                override_system_instruction=self.llm_client.summary_system_instruction,
                progress_callback=self.progress_signal.emit,
            ).strip()

        self.after_records.append(f"## {title} (层级: {level})\n{new_summary}\n")

        # 修改点4：如果是强制模式，并且当前处理的是3级场景节点，则记录它的新概要
        if self.force_mode and level == 3 and new_summary:
            if node_id:
                self.level3_updates[node_id] = new_summary

        return new_summary
