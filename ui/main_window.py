import sys
import os
import json
import uuid  # 【新增】用于生成唯一的文件名

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTextEdit, 
                             QPushButton, QSplitter, QMenuBar, QMenu, QTextBrowser,
                             QLabel, QFileDialog, QMessageBox, 
                             QInputDialog) # 【新增】导入 QInputDialog
from PyQt6.QtCore import Qt

# 导入核心逻辑层组件
# 假设运行入口在项目根目录，core 文件夹与 ui 文件夹平级
from core.workspace_manager import WorkspaceManager
from core.context_builder import ContextBuilder
from core.llm_client import LLMClient
from ui.settings_dialog import SettingsDialog

class NovelCreatorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI小说创作器")
        self.resize(1200, 800)
        
        # 运行时状态变量
        self.workspace = None             # 当前打开的工作区管理器实例
        self.outline_tree_data = None     # 内存中维护的小说树状结构字典
        self.current_editing_node = None  # 当前正在编辑器中编辑的节点字典数据
        self.current_editing_item = None  # 当前在树状视图中选中的 QTreeWidgetItem 实例
        self.current_setting_path = None  # 【新增】当前编辑的设定文件绝对路径
        self.node_map = {}                # 【新增】节点内存引用的绝对映射表
        
        # 加载配置并初始化 LLM 客户端
        self.config = self._load_config()
        self.llm_client = LLMClient(self.config) if self.config else None

        self.init_ui()

    def _get_item_level(self, item):
        """计算当前树节点的层级深度 (1=章, 2=节, 3=场景)"""
        level = 1
        p = item.parent()
        while p:
            level += 1
            p = p.parent()
        return level

    def _load_config(self) -> dict:
        """读取系统配置文件 conf/setting.json"""
        # 假设 ui 目录的上一级是项目根目录，conf 目录在根目录下
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, "conf", "setting.json")
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"读取配置文件失败: {e}")
        return {}

    def init_ui(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu('文件')
        open_action = file_menu.addAction('打开/创建工作区')
        open_action.triggered.connect(self.open_workspace)
        save_action = file_menu.addAction('保存全部')
        save_action.triggered.connect(self.save_all)
        setting_menu = menubar.addMenu('设置')
        settings_action = setting_menu.addAction('系统配置 (API/模型)')
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.setting_tree = QTreeWidget()
        self.setting_tree.setHeaderLabel("世界观设定")
        self.setting_tree.itemClicked.connect(self.on_setting_node_clicked)
        splitter.addWidget(self.setting_tree)

        self.novel_tree = QTreeWidget()
        self.novel_tree.setHeaderLabel("小说大纲结构")
        self.novel_tree.itemClicked.connect(self.on_novel_node_clicked)
        splitter.addWidget(self.novel_tree)

        # ================= 右侧：拆分概要与正文区 =================
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        
        editor_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # 上半部：概要 (保存在 JSON 中)
        summary_widget = QWidget()
        summary_layout = QVBoxLayout(summary_widget)
        summary_layout.setContentsMargins(0,0,0,0)
        summary_layout.addWidget(QLabel("节点概要 (Summary - 保存至系统数据):"))
        self.summary_editor = QTextEdit()
        self.summary_editor.setEnabled(False)
        summary_layout.addWidget(self.summary_editor)
        editor_splitter.addWidget(summary_widget)
        
        # 下半部：正文 (保存在 MD 文件中)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0,0,0,0)
        content_layout.addWidget(QLabel("节点正文 (Content - 保存至 .md 文件):"))
        self.content_editor = QTextEdit()
        self.content_editor.setEnabled(False)
        content_layout.addWidget(self.content_editor)
        editor_splitter.addWidget(content_widget)
        
        editor_splitter.setSizes([300, 700])
        detail_layout.addWidget(editor_splitter)
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        self.btn_generate = QPushButton("🔄 结合上下文生成正文")
        self.btn_save = QPushButton("💾 保存当前节点")
        self.btn_delete = QPushButton("🗑️ 删除当前节点")
        
        self.btn_generate.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        
        self.btn_generate.clicked.connect(self.generate_current_node)
        self.btn_save.clicked.connect(self.save_current_node)
        self.btn_delete.clicked.connect(self.delete_current_node)
        
        btn_layout.addWidget(self.btn_generate)
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_delete)
        detail_layout.addLayout(btn_layout)
        
        splitter.addWidget(detail_widget)
        splitter.setSizes([200, 300, 700])
        main_layout.addWidget(splitter, stretch=4)

        self.log_console = QTextBrowser()
        self.log_console.setFixedHeight(150)
        self.log_console.append("系统初始化完成。")
        main_layout.addWidget(self.log_console, stretch=1)

    def open_settings_dialog(self):
        """打开设置对话框，保存后自动重载配置"""
        dialog = SettingsDialog(self)
        # 如果用户点击了"保存配置"并成功写入，dialog.exec() 会返回 QDialog.DialogCode.Accepted
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.log_console.append("系统配置已更新，正在重新初始化模型客户端...")
            # 重新加载配置文件并实例化 LLMClient
            self.config = self._load_config()
            self.llm_client = LLMClient(self.config) if self.config else None
            self.log_console.append("模型客户端初始化完成！")

    # ================= UI 交互与业务逻辑 ================= #

    def open_workspace(self):
        """弹出文件夹选择框，初始化工作区"""
        folder_path = QFileDialog.getExistingDirectory(self, "选择或创建小说工程目录")
        if folder_path:
            try:
                self.workspace = WorkspaceManager(folder_path)
                self.workspace.init_workspace()
                
                self.log_console.append(f"成功加载工作区: {folder_path}")
                self.setWindowTitle(f"AI小说创作器 - {os.path.basename(folder_path)}")
                
                self.refresh_ui_from_workspace()
                
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法加载工作区:\n{str(e)}")
                self.log_console.append(f"<font color='red'>工作区加载失败: {e}</font>")

    def refresh_ui_from_workspace(self):
        """清空现有树并从 workspace 重新加载数据渲染"""
        if not self.workspace:
            return

        self.setting_tree.clear()
        self.novel_tree.clear()
        self.node_map.clear() # 【新增】每次刷新时清空内存映射表
        
        # 1. 渲染设定树 (保持不变)
        self.setting_tree.setHeaderLabel("世界观设定 (勾选参与上下文)")
        for cat in self.workspace.setting_dirs:
            cat_item = QTreeWidgetItem(self.setting_tree, [cat])
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            cat_item.setCheckState(0, Qt.CheckState.Checked)
            
            cat_path = os.path.join(self.workspace.settings_path, cat)
            if os.path.exists(cat_path):
                for file in os.listdir(cat_path):
                    if file.endswith('.json') and file != 'template.json':
                        file_item = QTreeWidgetItem(cat_item, [file.replace('.json', '')])
                        file_item.setData(0, Qt.ItemDataRole.UserRole, os.path.join(cat_path, file))

            add_btn = QTreeWidgetItem(cat_item, ["+ 新增设定..."])
            add_btn.setForeground(0, Qt.GlobalColor.blue)
        self.setting_tree.expandAll()

        # 2. 渲染小说目录树
        self.novel_tree.setHeaderLabel("小说大纲结构")
        self.outline_tree_data = self.workspace.load_outline_tree()
        
        if "nodes" not in self.outline_tree_data:
            self.outline_tree_data["nodes"] = []
        nodes_ref = self.outline_tree_data["nodes"]
        
        self._build_novel_tree_ui(nodes_ref, self.novel_tree, level=1)
        self.novel_tree.expandAll()

    def _build_novel_tree_ui(self, nodes: list, parent_widget, level=1):
        """递归构建小说目录树，按层级控制新增按钮的渲染"""
        import uuid
        
        # 场景的内部（第4级），严格禁止渲染任何子节点和按钮
        if level > 3:
            return

        for node in nodes:
            title = node.get("title", "未命名节点")
            item = QTreeWidgetItem(parent_widget, [title])
            
            node_id = str(uuid.uuid4())
            self.node_map[node_id] = node
            item.setData(0, Qt.ItemDataRole.UserRole, node_id)
            
            status = node.get("_status", "ok")
            if status == "missing":
                item.setForeground(0, Qt.GlobalColor.gray)
            elif status == "modified_externally":
                item.setForeground(0, Qt.GlobalColor.red)
                
            if "children" not in node:
                node["children"] = []
                
            # 递归下一层，层级 +1
            self._build_novel_tree_ui(node["children"], item, level + 1)
                
        # 根据当前层级提供对应的按钮名称
        titles = {1: "+ 新增章...", 2: "+ 新增节...", 3: "+ 新增场景..."}
        btn_text = titles.get(level, "+ 新增节点...")
        
        add_btn = QTreeWidgetItem(parent_widget, [btn_text])
        add_btn.setForeground(0, Qt.GlobalColor.blue)

    def on_novel_node_clicked(self, item, column):
        if not self.workspace:
            return

        if item.text(0).startswith("+"):
            parent_item = item.parent()
            if parent_item:
                parent_node_id = parent_item.data(0, Qt.ItemDataRole.UserRole)
                real_parent_node = self.node_map.get(parent_node_id)
                if real_parent_node is not None:
                    target_list = real_parent_node.setdefault("children", [])
                    parent_level = self._get_item_level(parent_item)
                    self.add_new_novel_node(target_list, parent_level + 1)
            else:
                if self.outline_tree_data is None:
                    self.outline_tree_data = {"nodes": []}
                target_list = self.outline_tree_data.setdefault("nodes", [])
                self.add_new_novel_node(target_list, 1)
            return

        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        real_node = self.node_map.get(node_id)
        if not real_node:
            return
            
        self.current_editing_node = real_node
        self.current_editing_item = item
        self.current_setting_path = None  
        node_level = self._get_item_level(item)
        
        self.btn_delete.setEnabled(not bool(real_node.get("children")))
        
        # === 1. 加载概要 (对所有节点开放) ===
        self.summary_editor.setText(real_node.get("summary", ""))
        self.summary_editor.setEnabled(True)
        self.btn_save.setEnabled(True)
        
        # === 2. 加载正文 (仅对第3级开放) ===
        if node_level == 3:
            rel_path = real_node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                if os.path.exists(full_path):
                    with open(full_path, 'r', encoding='utf-8') as f:
                        self.content_editor.setText(f.read())
                else:
                    self.content_editor.setText(f"# {real_node.get('title')}\n\n(文件尚未生成)")
            else:
                self.content_editor.setText("尚未配置正文路径。")
                
            self.content_editor.setEnabled(True)
            self.btn_generate.setEnabled(True)
        else:
            self.content_editor.setText("（当前层级仅支持填写概要，正文请在底层的“场景”节点中生成/编写）")
            self.content_editor.setEnabled(False)
            self.btn_generate.setEnabled(False)

    def add_new_novel_node(self, target_list: list, level: int):
        """弹出输入框，按层级创建小说新节点"""
        titles = {1: "章", 2: "节", 3: "场景"}
        node_type = titles.get(level, "节点")
        
        title, ok = QInputDialog.getText(self, f"新增{node_type}", f"请输入新{node_type}名称:")
        if ok and title.strip():
            title = title.strip()
            
            new_node = {
                "title": title,
                "summary": "",  # 默认附带空概要
                "children": [],
                "_status": "ok"
            }
            
            # 只有第3级场景才生成 Markdown 正文文件
            if level == 3:
                import uuid
                file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
                initial_content = f"# {title}\n\n请在此输入正文...\n"
                try:
                    initial_md5 = self.workspace.save_markdown_file(file_name, initial_content)
                    new_node["file_path"] = file_name
                    new_node["md5"] = initial_md5
                except Exception as e:
                    QMessageBox.critical(self, "错误", f"创建本地物理文件失败: {e}")
                    return

            target_list.append(new_node)
            
            if "nodes" not in self.outline_tree_data or not self.outline_tree_data["nodes"]:
                self.outline_tree_data["nodes"] = target_list
            
            try:
                with open(self.workspace.tree_json_file, 'w', encoding='utf-8') as f:
                    json.dump(self.outline_tree_data, f, ensure_ascii=False, indent=4)
                self.log_console.append(f"成功添加{node_type}: {title}")
                self.refresh_ui_from_workspace() 
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存大纲 JSON 失败:\n{e}")

    def on_setting_node_clicked(self, item, column):
        """处理设定树的点击事件（新增设定文件或读取现有设定）"""
        if not self.workspace:
            return

        # 1. 如果点击的是新增按钮
        if item.text(0).startswith("+"):
            parent_item = item.parent()
            if not parent_item: 
                return
            category = parent_item.text(0)

            new_name, ok = QInputDialog.getText(self, "新增设定", f"请输入【{category}】的设定名称:")
            if ok and new_name.strip():
                new_name = new_name.strip()
                cat_path = os.path.join(self.workspace.settings_path, category)
                template_path = os.path.join(cat_path, "template.json")
                new_file_path = os.path.join(cat_path, f"{new_name}.json")

                if os.path.exists(new_file_path):
                    QMessageBox.warning(self, "警告", "该设定文件已存在，请换一个名称！")
                    return

                try:
                    template_data = {}
                    if os.path.exists(template_path):
                        with open(template_path, 'r', encoding='utf-8') as f:
                            template_data = json.load(f)

                    with open(new_file_path, 'w', encoding='utf-8') as f:
                        json.dump(template_data, f, ensure_ascii=False, indent=4)

                    self.log_console.append(f"成功创建设定文件: {new_file_path}")
                    self.refresh_ui_from_workspace() 
                except Exception as e:
                    QMessageBox.critical(self, "错误", f"创建设定失败: {e}")
            return # 新增完毕后直接返回

        # 2. 如果点击的是普通的设定文件节点
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not file_path or not os.path.exists(file_path):
            return # 点击的可能是父级分类目录，不做处理

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.summary_editor.setText(content)
            self.log_console.append(f"打开设定文件: {os.path.basename(file_path)}")
            
            # 更新状态变量，清空小说节点的状态，记录当前设定的路径
            self.current_editing_node = None 
            self.current_setting_path = file_path
            
            self.summary_editor.setEnabled(True)
            self.content_editor.setEnabled(False)
            self.btn_save.setEnabled(True)
            self.btn_generate.setEnabled(False) # 设定文件是纯JSON数据，无需让AI续写正文
            
        except Exception as e:
            self.log_console.append(f"<font color='red'>读取设定失败: {e}</font>")

    def save_current_node(self):
        if not self.workspace:
            return
            
        # ====== 场景 A: 保存小说节点 ======
        if self.current_editing_node:
            # 1. 始终保存概要到内存字典
            self.current_editing_node["summary"] = self.summary_editor.toPlainText()
            
            # 2. 如果正文框是启用的(证明是场景节点)，则将正文落盘 MD 文件
            if self.content_editor.isEnabled():
                content = self.content_editor.toPlainText()
                rel_path = self.current_editing_node.get("file_path")
                if rel_path:
                    try:
                        new_md5 = self.workspace.save_markdown_file(rel_path, content)
                        self.current_editing_node["md5"] = new_md5
                        self.current_editing_node["_status"] = "ok"
                        if self.current_editing_item:
                            self.current_editing_item.setForeground(0, Qt.GlobalColor.black)
                    except Exception as e:
                        QMessageBox.critical(self, "错误", f"保存正文文件失败:\n{e}")
                        return
            
            # 3. 将包含概要的树结构统一保存至系统 JSON
            try:
                self.workspace.save_outline_tree(self.outline_tree_data)
                self.log_console.append(f"💾 节点保存成功: {self.current_editing_node.get('title')}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存大纲树失败:\n{e}")
            return

        # ====== 场景 B: 保存设定 JSON文件 (与上一版保持一致) ======
        if self.current_setting_path and os.path.exists(self.current_setting_path):
            try:
                parsed_json = json.loads(self.summary_editor.toPlainText()) # 这里假设你把设定也加载到上方的框里了
                with open(self.current_setting_path, 'w', encoding='utf-8') as f:
                    json.dump(parsed_json, f, ensure_ascii=False, indent=4)
                self.log_console.append(f"💾 设定保存成功: {os.path.basename(self.current_setting_path)}")
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "JSON 格式错误", f"保存失败！请检查 JSON 格式:\n{e}")
            return

    def delete_current_node(self):
        """处理带有二次确认的叶子节点删除操作"""
        if not self.current_editing_node or not self.outline_tree_data:
            return
            
        # 双重保险，防止UI状态异常导致非叶子节点被删
        if self.current_editing_node.get("children"):
            QMessageBox.warning(self, "不可删除", "当前节点包含子章节或场景，请先删除底层的子节点！")
            return
            
        node_title = self.current_editing_node.get('title', '未知节点')
        reply = QMessageBox.question(
            self, 
            '确认删除', 
            f"确定要永久删除节点【{node_title}】吗？\n警告：对应的 Markdown 文件也将被彻底删除！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
                                     
        if reply == QMessageBox.StandardButton.Yes:
            target_node = self.current_editing_node
            
            # 1. 递归扫描并从内存树列表中移除该字典
            def remove_from_list(nodes_list):
                for i, node in enumerate(nodes_list):
                    if node is target_node: # 内存地址级比对，绝对精准
                        del nodes_list[i]
                        return True
                    if remove_from_list(node.get("children", [])):
                        return True
                return False
                
            is_removed = remove_from_list(self.outline_tree_data.get("nodes", []))
            
            if is_removed:
                # 2. 尝试删除硬盘上的 Markdown 物理文件
                rel_path = target_node.get("file_path")
                if rel_path:
                    full_path = os.path.join(self.workspace.text_path, rel_path)
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                        except Exception as e:
                            self.log_console.append(f"<font color='orange'>警告：无法删除本地文件 {rel_path} ({e})</font>")
                
                # 3. 将更新后的结构写回 JSON，清空UI状态并刷新
                try:
                    with open(self.workspace.tree_json_file, 'w', encoding='utf-8') as f:
                        json.dump(self.outline_tree_data, f, ensure_ascii=False, indent=4)
                        
                    self.log_console.append(f"🗑️ 成功删除节点: {node_title}")
                    
                    # 清理右侧编辑器和按钮状态
                    # 【修复点 3：清理右侧编辑器和按钮状态，修改错误的变量名】
                    self.current_editing_node = None
                    
                    # 分别清空和禁用两个编辑框
                    self.summary_editor.clear()
                    self.summary_editor.setEnabled(False)
                    self.content_editor.clear()
                    self.content_editor.setEnabled(False)
                    self.btn_save.setEnabled(False)
                    self.btn_generate.setEnabled(False)
                    self.btn_delete.setEnabled(False)
                    
                    self.refresh_ui_from_workspace()
                except Exception as e:
                    QMessageBox.critical(self, "错误", f"保存系统大纲 JSON 失败:\n{e}")
            else:
                QMessageBox.warning(self, "错误", "在大纲树中未找到该节点，删除操作中断。")

    def save_all(self):
        """处理全局保存菜单动作"""
        if self.workspace and self.outline_tree_data:
            self.workspace.save_outline_tree(self.outline_tree_data)
            self.log_console.append("执行全局工作区设定保存...")
            QMessageBox.information(self, "提示", "工作区系统数据保存成功！")
        else:
            QMessageBox.warning(self, "警告", "当前没有活动的工作区或修改。")

    # ================= AI 生成核心逻辑 ================= #

    def get_checked_settings(self) -> list:
        """遍历设定树，获取所有打钩的 JSON 文件路径"""
        checked_paths = []
        root = self.setting_tree.invisibleRootItem()
        if not root:
            return checked_paths
            
        for i in range(root.childCount()):
            cat_item = root.child(i)
            for j in range(cat_item.childCount()):
                file_item = cat_item.child(j)
                # 检查该文件节点本身或其父级分类是否被勾选
                if file_item.checkState(0) == Qt.CheckState.Checked or cat_item.checkState(0) == Qt.CheckState.Checked:
                    path = file_item.data(0, Qt.ItemDataRole.UserRole)
                    if path and os.path.exists(path):
                        checked_paths.append(path)
        return list(set(checked_paths))

    def generate_current_node(self):
        """调用大模型生成正文"""
        if not self.current_editing_node or not self.outline_tree_data:
            return
            
        if not self.llm_client:
            QMessageBox.warning(self, "配置缺失", "尚未初始化大模型客户端，请检查 conf/setting.json 文件。")
            return

        node_title = self.current_editing_node.get('title', '未知节点')
        self.log_console.append(f"开始构建【{node_title}】的上下文...")
        self.btn_generate.setEnabled(False) 
        
        # 1. 实例化 Builder 并获取勾选的设定
        builder = ContextBuilder(self.workspace)
        checked_paths = self.get_checked_settings()
        
        # 2. 构建 Prompt 消息
        messages = builder.build_generation_prompt(
            self.current_editing_node, 
            self.outline_tree_data, 
            checked_paths
        )
        
        self.log_console.append("发送请求至大语言模型，请稍候...")
        
        try:
            # 3. 发送请求获取内容
            prompt_content = messages[-1]["content"]
            
            # 【修复点 1：将构建好的 Prompt 打印到 UI 日志框】
            self.log_console.append("========== 发送给大模型的完整 Prompt ==========")
            self.log_console.append(prompt_content)
            self.log_console.append("=================================================")
            QApplication.processEvents() # 强制刷新 UI，确保日志立刻显示在界面上
            
            result = self.llm_client.generate_text(prompt_content)
            
            # 【修复点 2：将 self.editor 修改为正确的 self.content_editor】
            self.content_editor.setText(result)
            self.log_console.append("生成成功！已填入编辑器，请确认后点击保存。")
            
        except Exception as e:
            self.log_console.append(f"<font color='red'>生成失败: {e}</font>")
            QMessageBox.critical(self, "生成错误", str(e))
        finally:
            self.btn_generate.setEnabled(True)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = NovelCreatorWindow()
    window.show()
    sys.exit(app.exec())