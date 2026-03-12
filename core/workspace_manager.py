import os
import json
import hashlib
import logging

logger = logging.getLogger(__name__)

class WorkspaceManager:
    def __init__(self, workspace_path: str):
        """
        初始化工作区管理器
        :param workspace_path: 小说工程的根目录路径
        """
        self.workspace_path = workspace_path
        self.settings_path = os.path.join(workspace_path, "设定")
        self.text_path = os.path.join(workspace_path, "正文")
        self.sys_data_path = os.path.join(workspace_path, "系统数据")
        
        self.tree_json_file = os.path.join(self.sys_data_path, "outline_tree.json")
        self.setting_dirs = ["公共设定", "人物设定", "名词设定", "地点设定", "其他设定"]

    def init_workspace(self):
        """
        初始化工程目录。如果目录或基础文件不存在，则自动创建。
        """
        # 1. 创建基础目录
        os.makedirs(self.settings_path, exist_ok=True)
        os.makedirs(self.text_path, exist_ok=True)
        os.makedirs(os.path.join(self.text_path, "images"), exist_ok=True)
        os.makedirs(self.sys_data_path, exist_ok=True)

        # 2. 创建5个设定子目录及 template.json
        self._create_setting_templates()

        # 3. 初始化小说大纲的 json 树（如果不存在）
        if not os.path.exists(self.tree_json_file):
            initial_tree = {
                "project_name": os.path.basename(self.workspace_path),
                "nodes": [] # 存放章、节、场景的树状结构
            }
            self.save_outline_tree(initial_tree)
            logger.info("已创建初始 outline_tree.json")

    def _create_setting_templates(self):
        """为设定目录生成默认的 template.json 指引文件"""
        templates = {
            "人物设定": {"姓名": "", "年龄": "", "性别": "", "性格": "", "背景描述": "", "特殊能力": ""},
            "地点设定": {"地点名称": "", "地理位置": "", "环境特征": "", "历史背景": ""},
            "名词设定": {"专有名词": "", "类型": "", "详细定义": "", "关联设定": ""},
            "公共设定": {"设定名称": "", "设定描述": "", "适用范围": ""},
            "其他设定": {"设定名称": "", "详细描述": ""}
        }

        for dir_name in self.setting_dirs:
            dir_path = os.path.join(self.settings_path, dir_name)
            os.makedirs(dir_path, exist_ok=True)
            
            template_file = os.path.join(dir_path, "template.json")
            if not os.path.exists(template_file):
                with open(template_file, 'w', encoding='utf-8') as f:
                    json.dump(templates.get(dir_name, {}), f, ensure_ascii=False, indent=4)

    def load_outline_tree(self) -> dict:
        """
        加载并校验 outline_tree.json。
        在加载时会自动比对记录的 MD5 与本地文件的实际 MD5。
        """
        if not os.path.exists(self.tree_json_file):
            return {"nodes": []}

        try:
            with open(self.tree_json_file, 'r', encoding='utf-8') as f:
                tree_data = json.load(f)
            
            # 递归校验树中节点的 MD5，标记文件是否被外部修改或缺失
            self._verify_tree_md5(tree_data.get("nodes", []))
            return tree_data
            
        except Exception as e:
            logger.error(f"加载 outline_tree.json 失败: {e}")
            return {"nodes": []}

    def save_outline_tree(self, tree_data: dict):
        """保存小说目录树"""
        try:
            with open(self.tree_json_file, 'w', encoding='utf-8') as f:
                json.dump(tree_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"保存 outline_tree.json 失败: {e}")

    def save_markdown_file(self, rel_path: str, content: str) -> str:
        """
        保存正文 Markdown 文件，并返回新的 MD5 哈希值。
        :param rel_path: 相对于“正文”目录的路径 (例如 "第一章/场景1.md")
        :param content: Markdown 文本内容
        :return: 文件的 MD5 字符串
        """
        full_path = os.path.join(self.text_path, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        return self.calculate_md5(full_path)

    def calculate_md5(self, file_path: str) -> str:
        """计算文件的 MD5 值"""
        if not os.path.exists(file_path):
            return ""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def _verify_tree_md5(self, nodes: list):
        """
        递归遍历节点，检查文件是否存在以及 MD5 是否匹配。
        将状态直接注入到内存中的节点数据里，供 UI 层读取。
        """
        for node in nodes:
            # 章、节、场景都可以有对应的文件路径
            rel_path = node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.text_path, rel_path)
                if not os.path.exists(full_path):
                    node["_status"] = "missing"  # UI可据此将节点置灰
                else:
                    current_md5 = self.calculate_md5(full_path)
                    if current_md5 != node.get("md5"):
                        node["_status"] = "modified_externally" # UI可提示用户有外部修改
                        node["md5"] = current_md5 # 可选：自动更新内存中的MD5
                    else:
                        node["_status"] = "ok"
            
            # 递归处理子节点
            if "children" in node:
                self._verify_tree_md5(node["children"])