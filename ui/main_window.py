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
        
        # 加载配置并初始化 LLM 客户端
        self.config = self._load_config()
        self.llm_client = LLMClient(self.config) if self.config else None

        self.init_ui()

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
        # ================= 1. 顶部菜单栏 ================= #
        menubar = self.menuBar()
        file_menu = menubar.addMenu('文件')
        
        open_action = file_menu.addAction('打开/创建工作区')
        open_action.triggered.connect(self.open_workspace)
        
        save_action = file_menu.addAction('保存全部')
        save_action.triggered.connect(self.save_all)
        
        setting_menu = menubar.addMenu('设置')
        # 替换为：
        settings_action = setting_menu.addAction('系统配置 (API/模型)')
        settings_action.triggered.connect(self.open_settings_dialog)

        about_menu = menubar.addMenu('关于')

        # 主部件容器
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # ================= 2. 中间三栏工作区 ================= #
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # (1) 最左边：设定目录树
        self.setting_tree = QTreeWidget()
        self.setting_tree.setHeaderLabel("世界观设定 (需打开工作区)")
        # 【新增下面这行代码】
        self.setting_tree.itemClicked.connect(self.on_setting_node_clicked) 
        splitter.addWidget(self.setting_tree)

        # (2) 第二个：小说目录树
        self.novel_tree = QTreeWidget()
        self.novel_tree.setHeaderLabel("小说大纲结构 (需打开工作区)")
        self.novel_tree.itemClicked.connect(self.on_novel_node_clicked)
        splitter.addWidget(self.novel_tree)

        # (3) 最右边：详情与编辑区
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.addWidget(QLabel("节点内容编辑器 (支持Markdown):"))
        
        self.editor = QTextEdit()
        self.editor.setEnabled(False) # 未加载数据前禁用
        detail_layout.addWidget(self.editor)
        
        btn_layout = QHBoxLayout()
        self.btn_generate = QPushButton("🔄 结合上下文生成内容")
        self.btn_save = QPushButton("💾 保存当前节点")
        
        self.btn_generate.setEnabled(False)
        self.btn_save.setEnabled(False)
        
        # 绑定按钮事件
        self.btn_generate.clicked.connect(self.generate_current_node)
        self.btn_save.clicked.connect(self.save_current_node)
        
        btn_layout.addWidget(self.btn_generate)
        btn_layout.addWidget(self.btn_save)
        detail_layout.addLayout(btn_layout)
        
        splitter.addWidget(detail_widget)

        # 设置三栏默认宽度比例
        splitter.setSizes([200, 300, 700])
        main_layout.addWidget(splitter, stretch=4)

        # ================= 3. 下方：Log 显示区 ================= #
        self.log_console = QTextBrowser()
        self.log_console.setFixedHeight(150)
        self.log_console.append("系统初始化完成。请通过“文件”菜单打开或创建一个工程目录。")
        if not self.config:
            self.log_console.append("<font color='orange'>警告：未找到 conf/setting.json 配置文件，AI生成功能将受限。</font>")
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
        
        # 1. 渲染设定树
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
        
        # 【关键修复】强制将 nodes 字段写入原字典，保证 target_list 拿到的是根字典的真实内存引用
        if "nodes" not in self.outline_tree_data:
            self.outline_tree_data["nodes"] = []
        nodes_ref = self.outline_tree_data["nodes"]
        
        self._build_novel_tree_ui(nodes_ref, self.novel_tree)
        self.novel_tree.expandAll()

    def _build_novel_tree_ui(self, nodes: list, parent_widget):
        """递归构建小说目录树 UI"""
        for node in nodes:
            title = node.get("title", "未命名节点")
            item = QTreeWidgetItem(parent_widget, [title])
            
            item.setData(0, Qt.ItemDataRole.UserRole, node)
            
            status = node.get("_status", "ok")
            if status == "missing":
                item.setForeground(0, Qt.GlobalColor.gray)
            elif status == "modified_externally":
                item.setForeground(0, Qt.GlobalColor.red)
                
            if "children" not in node:
                node["children"] = []
                
            self._build_novel_tree_ui(node["children"], item)
                
        # 同级目录底部预留一个新增按钮
        btn_text = "+ 新增章..." if isinstance(parent_widget, QTreeWidget) else "+ 新增子节点..."
        add_btn = QTreeWidgetItem(parent_widget, [btn_text])
        add_btn.setForeground(0, Qt.GlobalColor.blue)
        
        # 绑定当前层级的列表引用
        add_btn.setData(0, Qt.ItemDataRole.UserRole, {"_is_add_btn": True, "target_list": nodes})

    def on_novel_node_clicked(self, item, column):
        """处理目录树节点点击事件，读取文件"""
        if not self.workspace:
            return

        node_data = item.data(0, Qt.ItemDataRole.UserRole)
        if not node_data:
            return
            
        if node_data.get("_is_add_btn"):
            self.add_new_novel_node(node_data["target_list"])
            return
            
        self.current_editing_node = node_data
        self.current_editing_item = item
        self.current_setting_path = None  
        
        rel_path = node_data.get("file_path")
        if not rel_path:
            self.editor.setText("该节点尚未配置 'file_path' 属性，无法读取内容。")
            self.editor.setEnabled(False)
            self.btn_save.setEnabled(False)
            self.btn_generate.setEnabled(False)
            return
            
        full_path = os.path.join(self.workspace.text_path, rel_path)
        
        if os.path.exists(full_path):
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.editor.setText(content)
                self.log_console.append(f"打开文件: {rel_path}")
            except Exception as e:
                self.editor.setText(f"读取文件失败: {e}")
                self.log_console.append(f"<font color='red'>读取错误: {e}</font>")
        else:
            self.editor.setText(f"# {node_data.get('title', '未命名')}\n\n(文件尚未生成，请在此输入内容并保存，或使用大模型生成)")
            self.log_console.append(f"节点对应的文件缺失: {rel_path}")
            
        self.editor.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_generate.setEnabled(True)

    def add_new_novel_node(self, target_list: list):
        """弹出输入框，创建小说新节点"""
        title, ok = QInputDialog.getText(self, "新增节点", "请输入新节点名称 (如: 第一章 / 场景1):")
        if ok and title.strip():
            title = title.strip()
            import uuid # 确保引入
            file_name = f"节点_{uuid.uuid4().hex[:8]}.md"
            
            initial_content = f"# {title}\n\n请在此输入【{title}】的概要或正文...\n"
            try:
                initial_md5 = self.workspace.save_markdown_file(file_name, initial_content)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"创建本地物理文件失败: {e}")
                return

            new_node = {
                "title": title,
                "file_path": file_name,
                "md5": initial_md5,
                "children": [],
                "_status": "ok"
            }
            
            # 追加到目标列表
            target_list.append(new_node)
            
            # 【强制兜底】检查根节点关联，防止任何意外的引用断裂
            if "nodes" not in self.outline_tree_data or not self.outline_tree_data["nodes"]:
                self.outline_tree_data["nodes"] = target_list
            
            try:
                # 【直接写入文件】绕过可能吞掉报错的底层方法，保证如果报错能立刻弹出对话框
                with open(self.workspace.tree_json_file, 'w', encoding='utf-8') as f:
                    json.dump(self.outline_tree_data, f, ensure_ascii=False, indent=4)
                    
                self.log_console.append(f"成功添加大纲节点: {title}")
                self.refresh_ui_from_workspace() 
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存大纲 JSON 失败:\n{e}")
                self.log_console.append(f"<font color='red'>大纲保存失败: {e}</font>")

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
            self.editor.setText(content)
            self.log_console.append(f"打开设定文件: {os.path.basename(file_path)}")
            
            # 更新状态变量，清空小说节点的状态，记录当前设定的路径
            self.current_editing_node = None 
            self.current_setting_path = file_path
            
            self.editor.setEnabled(True)
            self.btn_save.setEnabled(True)
            self.btn_generate.setEnabled(False) # 设定文件是纯JSON数据，无需让AI续写正文
            
        except Exception as e:
            self.log_console.append(f"<font color='red'>读取设定失败: {e}</font>")

    def save_current_node(self):
        """保存当前编辑器内容（支持小说Markdown和设定JSON的双轨保存）"""
        if not self.workspace:
            return
            
        content = self.editor.toPlainText()
        
        # ====== 场景 A: 正在编辑小说正文节点 ======
        if self.current_editing_node:
            rel_path = self.current_editing_node.get("file_path")
            if not rel_path:
                QMessageBox.warning(self, "警告", "当前节点无文件路径配置。")
                return
            try:
                new_md5 = self.workspace.save_markdown_file(rel_path, content)
                self.current_editing_node["md5"] = new_md5
                self.current_editing_node["_status"] = "ok"
                
                if self.current_editing_item:
                    self.current_editing_item.setForeground(0, Qt.GlobalColor.black)
                
                self.workspace.save_outline_tree(self.outline_tree_data)
                self.log_console.append(f"💾 正文保存成功: {rel_path}")
            except Exception as e:
                self.log_console.append(f"<font color='red'>正文保存失败: {e}</font>")
                QMessageBox.critical(self, "错误", f"保存正文文件失败:\n{e}")
            return

        # ====== 场景 B: 正在编辑设定 JSON 文件 ======
        if self.current_setting_path and os.path.exists(self.current_setting_path):
            try:
                # 写入前先尝试解析 JSON，如果用户手滑漏了引号或逗号，这里会拦截并报错
                parsed_json = json.loads(content)
                
                with open(self.current_setting_path, 'w', encoding='utf-8') as f:
                    # 重新格式化并写入，保证缩进美观
                    json.dump(parsed_json, f, ensure_ascii=False, indent=4)
                    
                self.log_console.append(f"💾 设定保存成功: {os.path.basename(self.current_setting_path)}")
                
                # 将格式化后的漂亮 JSON 重新刷回文本框
                self.editor.setText(json.dumps(parsed_json, ensure_ascii=False, indent=4))
                
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "JSON 格式错误", f"保存失败！请检查内容是否符合严格的 JSON 格式 (例如漏了引号或逗号):\n{e}")
            except Exception as e:
                self.log_console.append(f"<font color='red'>设定保存失败: {e}</font>")
                QMessageBox.critical(self, "错误", f"保存设定文件失败:\n{e}")
            return
            
        QMessageBox.warning(self, "警告", "当前没有正在编辑的有效节点。")

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
        QApplication.processEvents() # 强制刷新 UI，避免界面卡死导致的 Log 不显示
        
        try:
            # 3. 发送请求获取内容
            # 注意：在生产环境中，网络请求应放入 QThread 中执行，以免阻塞主线程 UI
            prompt_content = messages[-1]["content"]
            result = self.llm_client.generate_text(prompt_content)
            
            # 将生成结果写入编辑器
            self.editor.setText(result)
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