import sys
import os
import json
import uuid  # 【新增】用于生成唯一的文件名

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTextEdit, 
                             QPushButton, QSplitter, QMenuBar, QMenu, QTextBrowser,
                             QLabel, QFileDialog, QMessageBox, 
                             QInputDialog, QDialog, QCheckBox, QSpinBox) # 【新增】QCheckBox, QSpinBox
from PyQt6.QtGui import QKeySequence, QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# 导入暗色主题配色常量
from ui.theme import NODE_NORMAL, NODE_MISSING, NODE_ERROR, NODE_ADD_BTN, TEXT_SECONDARY

# 导入核心逻辑层组件
# 假设运行入口在项目根目录，core 文件夹与 ui 文件夹平级
from core.workspace_manager import WorkspaceManager
from core.context_builder import ContextBuilder
from core.llm_client import LLMClient
from core.html_exporter import HtmlExporter
import webbrowser # 用于生成后自动在浏览器打开网页
from ui.settings_dialog import SettingsDialog

class GenerateTaskThread(QThread):
    # 定义两个信号，用于向主线程传递成功的结果或失败的错误信息
    success_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, prompt_content, parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.prompt_content = prompt_content

    def run(self):
        try:
            result = self.llm_client.generate_text(self.prompt_content)
            # 【防雪崩修复】：拦截 llm_client 返回的文本格式错误信息
            if result.strip().startswith("> **生成失败:**"):
                error_msg = result.replace("> **生成失败:**", "").strip()
                self.error_signal.emit(error_msg)
            else:
                self.success_signal.emit(result)
        except Exception as e:
            self.error_signal.emit(str(e))

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
        self._updating_settings = False   
        
        # 【新增】批量生成相关的状态变量
        self.batch_generate_queue = []
        self.is_batch_generating = False
        
        self.config = self._load_config()
        self.llm_client = LLMClient(self.config) if self.config else None

        self.init_ui()

        # 【新增】自动加载最近的工作区
        sys_state = self._load_sys_state()
        recent_workspaces = sys_state.get("recent_workspaces", [])
        if recent_workspaces and os.path.exists(recent_workspaces[0]):
            self._load_workspace_by_path(recent_workspaces[0])

    # ================= 新增：系统级状态管理（保存最近工作区） =================
    def _get_sys_state_path(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, "conf", "sys_state.json")

    def _load_sys_state(self) -> dict:
        path = self._get_sys_state_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"recent_workspaces": []}

    def _save_sys_state(self, workspace_path: str):
        state = self._load_sys_state()
        recents = state.get("recent_workspaces", [])
        # 如果已存在，先移出，再插到最前面
        if workspace_path in recents:
            recents.remove(workspace_path)
        recents.insert(0, workspace_path)
        # 只保留最近 10 个
        state["recent_workspaces"] = recents[:10]
        
        path = self._get_sys_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存系统状态失败: {e}")

    def _get_item_level(self, item):
        # 计算当前树节点的层级深度 (1=章, 2=节, 3=场景)"""
        level = 1
        p = item.parent()
        while p:
            level += 1
            p = p.parent()
        return level

    def _load_config(self) -> dict:
        # 读取系统配置文件 conf/setting.json"""
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
        
        # 【修改点 2】：分离新建工作区和加载工作区逻辑
        new_action = file_menu.addAction('新建工作区')
        new_action.triggered.connect(self.new_workspace)
        
        load_action = file_menu.addAction('加载工作区')
        load_action.triggered.connect(self.load_workspace)
        
        save_action = file_menu.addAction('保存全部')
        save_action.setShortcut(QKeySequence("Ctrl+S")) # 【新增】绑定 Ctrl+S 快捷键
        save_action.triggered.connect(self.save_all)
        export_html_action = file_menu.addAction('🌐 导出为可阅读网页 (HTML)')
        export_html_action.triggered.connect(self.export_to_html)
        setting_menu = menubar.addMenu('设置')
        settings_action = setting_menu.addAction('系统配置 (API/模型)')
        # 【修改点 1】：修复点击设置没有反应的问题，绑定打开对话框事件
        settings_action.triggered.connect(self.open_settings_dialog)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.setting_tree = QTreeWidget()
        self.setting_tree.setHeaderLabel("世界观设定")
        self.setting_tree.itemClicked.connect(self.on_setting_node_clicked)
        # 【修改】绑定项改变信号，处理父子勾选联动
        self.setting_tree.itemChanged.connect(self.on_setting_item_changed)
        splitter.addWidget(self.setting_tree)

        self.novel_tree = QTreeWidget()
        self.novel_tree.setHeaderLabel("小说大纲结构")
        
        # 【修改】开启大纲树的拖拽支持
        self.novel_tree.setDragEnabled(True)
        self.novel_tree.setAcceptDrops(True)
        self.novel_tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        # 劫持 dropEvent 以便在拖放结束后更新底层 JSON 数据
        original_drop_event = self.novel_tree.dropEvent
        def custom_drop_event(event):
            original_drop_event(event)
            self._cleanup_tree_add_buttons() # 修复按钮视觉位置
            self.sync_tree_data_from_ui()    # 同步并保存数据
        self.novel_tree.dropEvent = custom_drop_event
        
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
        
        # 【修改】增加字数统计标签布局
        content_header_layout = QHBoxLayout()
        content_header_layout.addWidget(QLabel("节点正文 (Content - 保存至 .md 文件):"))
        self.word_count_label = QLabel("当前字数: 0")
        self.word_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.word_count_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        content_header_layout.addWidget(self.word_count_label)
        content_layout.addLayout(content_header_layout)
        
        self.content_editor = QTextEdit()
        self.content_editor.setEnabled(False)
        self.content_editor.textChanged.connect(self.update_word_count) # 绑定字数统计
        content_layout.addWidget(self.content_editor)
        editor_splitter.addWidget(content_widget)
        
        editor_splitter.setSizes([300, 700])
        detail_layout.addWidget(editor_splitter)


        # ================= 生成参数控制栏 =================
        param_layout = QHBoxLayout()
        
        self.cb_gen_image = QCheckBox("生成图片占位符")
        self.cb_gen_image.setChecked(False) # 【修改】取消默认勾选
        param_layout.addWidget(self.cb_gen_image)
        
        # 【修改点 1】：加入后续节点开关
        self.cb_include_next = QCheckBox("纳入后续节点上下文")
        self.cb_include_next.setChecked(True) 
        param_layout.addWidget(self.cb_include_next)
        
        param_layout.addWidget(QLabel(" 目标生成字数:"))
        self.spin_word_count = QSpinBox()
        self.spin_word_count.setRange(50, 20000) # 允许范围：500 - 20000字
        self.spin_word_count.setSingleStep(500)   # 每次加减 500
        self.spin_word_count.setValue(5000)       # 默认 5000
        param_layout.addWidget(self.spin_word_count)
        
        param_layout.addStretch() # 将控件推到左侧
        detail_layout.addLayout(param_layout)
        # ==========================================================
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        # 【新增】批量生成按钮
        self.btn_batch_generate = QPushButton("🚀 批量生成缺失场景")
        self.btn_generate = QPushButton("🔄 结合上下文生成正文")
        self.btn_rewrite = QPushButton("✍️ 基于原文重写(扩/缩)")
        self.btn_save = QPushButton("💾 保存当前节点")
        self.btn_delete = QPushButton("🗑️ 删除当前节点")
        
        self.btn_batch_generate.clicked.connect(self.start_batch_generate)
        self.btn_generate.clicked.connect(self.generate_current_node)
        self.btn_rewrite.clicked.connect(self.rewrite_current_node)
        self.btn_save.clicked.connect(self.save_current_node)
        self.btn_delete.clicked.connect(self.delete_current_node)
        
        self.btn_generate.setEnabled(False)
        self.btn_rewrite.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        
        btn_layout.addWidget(self.btn_batch_generate)
        btn_layout.addWidget(self.btn_generate)
        btn_layout.addWidget(self.btn_rewrite)
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

    # ================= 【新增】UI 辅助方法 ================= #
    def update_word_count(self):
        # 实时更新正文字数统计"""
        text = self.content_editor.toPlainText()
        # 简单过滤掉换行和空格，更接近实际汉字/有效字符数
        clean_text = text.replace(' ', '').replace('\n', '').replace('\t', '')
        self.word_count_label.setText(f"当前字数: {len(clean_text)}")

    def on_setting_item_changed(self, item, column):
        # 【修改】处理设定树的勾选状态联动"""
        if self._updating_settings:
            return
        self._updating_settings = True

        state = item.checkState(column)
        
        # 1. 如果改变的是父节点（分类），将状态同步给子节点
        if item.parent() is None:
            for i in range(item.childCount()):
                child = item.child(i)
                # 跳过“+ 新增”按钮
                if not child.text(0).startswith("+"):
                    child.setCheckState(0, state)
                    
        # 2. 如果改变的是子节点（文件），向上计算父节点状态
        else:
            parent = item.parent()
            all_checked = True
            all_unchecked = True
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.text(0).startswith("+"):
                    continue
                if child.checkState(0) == Qt.CheckState.Checked:
                    all_unchecked = False
                elif child.checkState(0) == Qt.CheckState.Unchecked:
                    all_checked = False
                else: # PartiallyChecked
                    all_checked = False
                    all_unchecked = False

            if all_checked:
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif all_unchecked:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)

        self._updating_settings = False

    def _cleanup_tree_add_buttons(self, parent_item=None):
        # 【修改】拖拽后保证“+ 新增”按钮始终处于列表末尾"""
        target = parent_item if parent_item else self.novel_tree.invisibleRootItem()
        
        add_btn_index = -1
        for i in range(target.childCount()):
            child = target.child(i)
            if child.text(0).startswith("+"):
                add_btn_index = i
                break
                
        if add_btn_index != -1 and add_btn_index < target.childCount() - 1:
            add_btn = target.takeChild(add_btn_index)
            target.addChild(add_btn)
            
        for i in range(target.childCount()):
            child = target.child(i)
            if not child.text(0).startswith("+"):
                self._cleanup_tree_add_buttons(child)

    def sync_tree_data_from_ui(self):
        # 【修改】将UI界面上的树状结构反向同步回 JSON 内存模型并保存"""
        if not self.workspace or self.outline_tree_data is None:
            return
            
        new_nodes = []
        root = self.novel_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.text(0).startswith("+"): continue
            node_data = self._build_node_data_from_item(item)
            if node_data:
                new_nodes.append(node_data)
                
        self.outline_tree_data["nodes"] = new_nodes
        self.workspace.save_outline_tree(self.outline_tree_data)
        self.log_console.append("系统通知：节点位置结构已自动保存。")

    def _build_node_data_from_item(self, item):
        # 【修改】递归辅助函数：从 UI Item 重建字典数据"""
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        node_data = self.node_map.get(node_id)
        if not node_data:
            return None
            
        new_children = []
        for i in range(item.childCount()):
            child_item = item.child(i)
            if child_item.text(0).startswith("+"): continue
            child_data = self._build_node_data_from_item(child_item)
            if child_data:
                new_children.append(child_data)
                
        node_data["children"] = new_children
        return node_data

    # ================= UI 交互与业务逻辑 ================= #

    def open_settings_dialog(self):
        # 打开设置对话框，保存后自动重载配置"""
        dialog = SettingsDialog(self)
        # 如果用户点击了"保存配置"并成功写入，dialog.exec() 会返回 QDialog.DialogCode.Accepted
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.log_console.append("系统配置已更新，正在重新初始化模型客户端...")
            # 重新加载配置文件并实例化 LLMClient
            self.config = self._load_config()
            self.llm_client = LLMClient(self.config) if self.config else None
            self.log_console.append("模型客户端初始化完成！")
	# ================= UI 交互与业务逻辑 ================= #
    # ================= 【修改点 2：拆分的新建/加载逻辑】 =================
    def new_workspace(self):
        # 弹出文件夹选择框，初始化新的工作区（要求空文件夹）"""
        folder_path = QFileDialog.getExistingDirectory(self, "选择空文件夹创建新工作区")
        if folder_path:
            # 检查文件夹是否为空
            if os.listdir(folder_path):
                QMessageBox.warning(self, "操作取消", "为了防止意外覆盖文件，请选择一个【空文件夹】来初始化新工作区！")
                return
            
            try:
                # 只有新建工作区时才执行 init_workspace 生成基础目录和配置
                temp_workspace = WorkspaceManager(folder_path)
                temp_workspace.init_workspace() 
                self._load_workspace_by_path(folder_path)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"初始化新工作区失败:\n{str(e)}")

    def load_workspace(self):
        # 弹出文件夹选择框，加载已有工作区"""
        folder_path = QFileDialog.getExistingDirectory(self, "选择已有的工作区目录")
        if folder_path:
            self._load_workspace_by_path(folder_path)

    def _load_workspace_by_path(self, folder_path):
        # 核心加载工作区逻辑（不再自动 init_workspace）"""
        try:
            self.workspace = WorkspaceManager(folder_path)
            # 移除这里的 init_workspace 调用，防止在加载现有目录时创建冗余结构
            
            self._save_sys_state(folder_path) # 【新增】保存到最近工作区列表
            
            self.log_console.append(f"成功加载工作区: {folder_path}")
            self.setWindowTitle(f"AI小说创作器 - {os.path.basename(folder_path)}")
            
            self.refresh_ui_from_workspace()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法加载工作区:\n{str(e)}")
            self.log_console.append(f"<font color='red'>工作区加载失败: {e}</font>")
    # ===============================================================

    def refresh_ui_from_workspace(self):
        ### 清空现有树并从 workspace 重新加载数据渲染"""
        if not self.workspace:
            return

        self.setting_tree.clear()
        self.novel_tree.clear()
        self.node_map.clear() # 【新增】每次刷新时清空内存映射表
        
        # 1. 渲染设定树 (保持不变)
        self.setting_tree.setHeaderLabel("世界观设定 (勾选参与上下文)")
        self._updating_settings = True 
        
        # 即使加载不完整的目录，使用 getattr 或默认目录，这里简单兼容
        setting_dirs = getattr(self.workspace, 'setting_dirs', ["公共设定", "人物设定", "名词设定", "地点设定", "其他设定"])
        
        for cat in setting_dirs:
            cat_item = QTreeWidgetItem(self.setting_tree, [cat])
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            cat_item.setCheckState(0, Qt.CheckState.Checked)
            
            cat_path = os.path.join(self.workspace.settings_path, cat)
            if os.path.exists(cat_path):
                for file in os.listdir(cat_path):
                    if file.endswith('.json') and file != 'template.json':
                        file_item = QTreeWidgetItem(cat_item, [file.replace('.json', '')])
                        file_item.setData(0, Qt.ItemDataRole.UserRole, os.path.join(cat_path, file))
                        # 【修改】子节点同样开启勾选
                        file_item.setFlags(file_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        file_item.setCheckState(0, Qt.CheckState.Checked)

            add_btn = QTreeWidgetItem(cat_item, ["+ 新增设定..."])
            add_btn.setForeground(0, QColor(NODE_ADD_BTN))
            # 禁止勾选“新增”按钮
            add_btn.setFlags(add_btn.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            
        self._updating_settings = False
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
        # 递归构建小说目录树，按层级控制新增按钮的渲染"""
        import uuid
        
        # 场景的内部（第4级），严格禁止渲染任何子节点和按钮
        if level > 3:
            return

        for node in nodes:
            title = node.get("title", "未命名节点")
            item = QTreeWidgetItem(parent_widget, [title])
            
            # 【修改点 2】：利用标志位控制。针对第三级节点，移除其作为拖放目标的属性，从而杜绝下挂子节点
            if level == 3:
                item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
            
            node_id = str(uuid.uuid4())
            self.node_map[node_id] = node
            item.setData(0, Qt.ItemDataRole.UserRole, node_id)
            
            status = node.get("_status", "ok")
            
            # 【修改点 3】：基于层级进行渲染，仅第3级缺失文件变色，1/2级固定黑色
            if level == 3:
                if status == "missing":
                    item.setForeground(0, QColor(NODE_MISSING))
                elif status == "modified_externally":
                    item.setForeground(0, QColor(NODE_ERROR))
                else:
                    item.setForeground(0, QColor(NODE_NORMAL))
            else:
                item.setForeground(0, QColor(NODE_NORMAL))
                
            if "children" not in node:
                node["children"] = []
                
            # 递归下一层，层级 +1
            self._build_novel_tree_ui(node["children"], item, level + 1)
                
        # 根据当前层级提供对应的按钮名称
        titles = {1: "+ 新增章...", 2: "+ 新增节...", 3: "+ 新增场景..."}
        btn_text = titles.get(level, "+ 新增节点...")
        
        add_btn = QTreeWidgetItem(parent_widget, [btn_text])
        add_btn.setForeground(0, QColor(NODE_ADD_BTN))
        # 【修改】严格禁止“+新增”按钮被拖拽或接收拖放
        add_btn.setFlags(add_btn.flags() & ~Qt.ItemFlag.ItemIsDragEnabled & ~Qt.ItemFlag.ItemIsDropEnabled)

    def on_novel_node_clicked(self, item, column):
        if not self.workspace:
            return
        
        # 防止在批量生成时误触修改状态
        if self.is_batch_generating:
            QMessageBox.warning(self, "提示", "批量生成中，请先停止任务后再手动操作节点。")
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
                # 【修复点】：改成标准标题格式，避免大段非剧情文字干扰 AI
                self.content_editor.setText(f"# {real_node.get('title')}\n\n(尚未配置正文路径，在此输入内容并保存后将自动生成)")
                
            self.content_editor.setEnabled(True)
            self.btn_generate.setEnabled(True)
            self.btn_rewrite.setEnabled(True) # 【新增】激活重写按钮
        else:
            self.content_editor.setText("（当前层级仅支持填写概要，正文请在底层的“场景”节点中生成/编写）")
            self.content_editor.setEnabled(False)
            self.btn_generate.setEnabled(False)
            self.btn_rewrite.setEnabled(False) # 【新增】禁用重写按钮
            
        self.update_word_count() # 【修改】更新字数

    def add_new_novel_node(self, target_list: list, level: int):
        # 弹出输入框，按层级创建小说新节点"""
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
        # 处理设定树的点击事件（新增设定文件或读取现有设定）"""
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
            
        if self.current_editing_node:
            self.current_editing_node["summary"] = self.summary_editor.toPlainText()
            
            if self.content_editor.isEnabled():
                content = self.content_editor.toPlainText()
                rel_path = self.current_editing_node.get("file_path")
                
                # 【修复点】：如果没有配置文件路径，则自动生成一个并绑定
                if not rel_path:
                    import uuid
                    rel_path = f"场景_{uuid.uuid4().hex[:8]}.md"
                    self.current_editing_node["file_path"] = rel_path
                    
                try:
                    # 将内容写入新生成或已有的路径中
                    new_md5 = self.workspace.save_markdown_file(rel_path, content)
                    self.current_editing_node["md5"] = new_md5
                    self.current_editing_node["_status"] = "ok"
                    # 恢复树状图节点的颜色为黑色
                    if self.current_editing_item:
                        self.current_editing_item.setForeground(0, QColor(NODE_NORMAL))
                except Exception as e:
                    QMessageBox.critical(self, "错误", f"保存正文文件失败:\n{e}")
                    return
            
            try:
                self.workspace.save_outline_tree(self.outline_tree_data)
                self.log_console.append(f"💾 节点保存成功: {self.current_editing_node.get('title')}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存大纲树失败:\n{e}")
            return

        if self.current_setting_path and os.path.exists(self.current_setting_path):
            try:
                parsed_json = json.loads(self.summary_editor.toPlainText()) 
                with open(self.current_setting_path, 'w', encoding='utf-8') as f:
                    json.dump(parsed_json, f, ensure_ascii=False, indent=4)
                self.log_console.append(f"💾 设定保存成功: {os.path.basename(self.current_setting_path)}")
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "JSON 格式错误", f"保存失败！请检查 JSON 格式:\n{e}")
            return

    def delete_current_node(self):
        # 处理带有二次确认的叶子节点删除操作"""
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

    def export_to_html(self):
        """处理导出 HTML 网页的逻辑"""
        if not self.workspace or not self.outline_tree_data:
            QMessageBox.warning(self, "操作失败", "请先加载或新建一个工作区！")
            return
            
        # 导出前最好先做一次保存，保证网页内容是最新的
        self.save_all()
        
        try:
            exporter = HtmlExporter(self.workspace)
            output_file = exporter.export()
            
            self.log_console.append(f"<b><font color='green'>🎉 网页导出成功！文件已保存至: {output_file}</font></b>")
            
            # 询问用户是否立即预览
            reply = QMessageBox.question(
                self, 
                "导出成功", 
                f"已成功在工程目录的 www 文件夹下生成网页版小说。\n是否立即在浏览器中打开预览？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                # 使用系统默认浏览器打开该 HTML 文件
                webbrowser.open(f"file://{os.path.abspath(output_file)}")
                
        except Exception as e:
            QMessageBox.critical(self, "导出错误", f"导出网页失败:\n{str(e)}")
            self.log_console.append(f"<font color='red'>网页导出异常: {e}</font>")

    def save_all(self):
        # 处理全局保存菜单动作 (Ctrl+S 触发)"""
        if self.workspace and self.outline_tree_data:
            self.workspace.save_outline_tree(self.outline_tree_data)
            
            # 顺便尝试把当前正在编辑的节点物理文件也保存一下
            if self.current_editing_node and self.content_editor.isEnabled():
                self.save_current_node()
                
            self.log_console.append("<b><font color='green'>[系统通知] 执行全局保存成功 (Ctrl+S)。</font></b>")
            self.statusBar().showMessage("全局保存成功", 1000) # 可选：在底部状态栏闪烁1秒提示
        else:
            self.log_console.append("<font color='orange'>全局保存跳过：当前没有打开工作区。</font>")

    # ================= AI 生成核心逻辑 ================= #

    def get_checked_settings(self) -> list:
        # 遍历设定树，获取所有打钩的 JSON 文件路径"""
        checked_paths = []
        root = self.setting_tree.invisibleRootItem()
        if not root:
            return checked_paths
            
        for i in range(root.childCount()):
            cat_item = root.child(i)
            for j in range(cat_item.childCount()):
                file_item = cat_item.child(j)
                # 【修改】子节点勾选或处于部分勾选状态都会被包含进上下文
                if file_item.checkState(0) == Qt.CheckState.Checked:
                    path = file_item.data(0, Qt.ItemDataRole.UserRole)
                    if path and os.path.exists(path):
                        checked_paths.append(path)
        return list(set(checked_paths))

    # ================= 【新增】批量生成核心逻辑 =================
    def _get_missing_level3_nodes(self, nodes, level=1):
        """递归寻找所有状态为 missing 的第 3 级（场景）节点"""
        missing = []
        for node in nodes:
            if level == 3 and node.get("_status") == "missing":
                missing.append(node)
            if "children" in node:
                missing.extend(self._get_missing_level3_nodes(node["children"], level + 1))
        return missing

    def _find_item_by_data(self, parent_item, search_data):
        """辅助函数：通过 UserRole 数据定位树状图中的 UI 节点"""
        for i in range(parent_item.childCount()):
            item = parent_item.child(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == search_data:
                return item
            found = self._find_item_by_data(item, search_data)
            if found:
                return found
        return None

    def rewrite_current_node(self):
        if not self.current_editing_node:
            return

        # 检查是否有足够的正文供重写
        target_content = self.content_editor.toPlainText().strip()
        if not target_content or (target_content.startswith("#") and len(target_content.split('\n')) <= 3):
            QMessageBox.warning(self, "无法重写", "当前节点尚未生成有效正文内容。\n请先使用【结合上下文生成正文】或手动输入一段基础剧情。")
            return

        target_word_count = self.spin_word_count.value()

        # 二次确认拦截
        reply = QMessageBox.question(
            self,
            "确认重写",
            f"确定要将当前节点的正文重写/扩写为约 {target_word_count} 字吗？\n警告：生成成功后，现有的正文将被不可逆地完全覆盖！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.No:
            return

        # 保存现有内容，防止意外丢失
        self.save_current_node()

        self.btn_generate.setEnabled(False)
        self.btn_rewrite.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)

        node_title = self.current_editing_node.get('title', '未知节点')
        self.log_console.append(f"开始构建【{node_title}】的重写请求...")

        builder = ContextBuilder(self.workspace)
        checked_paths = self.get_checked_settings()

        # 调用新增的重写 Prompt 生成器
        messages = builder.build_rewrite_prompt(
            self.current_editing_node,
            self.outline_tree_data,
            checked_paths,
            target_word_count
        )

        prompt_content = messages[-1]["content"]
        
        self.log_console.append("========== [User] 重写提示词 ==========")
        self.log_console.append(prompt_content)
        self.log_console.append("发送重写请求至大模型，后台处理中，请耐心稍候...")

        # 复用 GenerateTaskThread 进行异步调用
        self.generate_thread = GenerateTaskThread(self.llm_client, prompt_content)
        self.generate_thread.success_signal.connect(self.on_generate_success)
        self.generate_thread.error_signal.connect(self.on_generate_error)
        self.generate_thread.start()

    def start_batch_generate(self):
        """处理点击批量生成按钮的事件"""
        if self.is_batch_generating:
            # 执行取消逻辑
            self.is_batch_generating = False
            self.batch_generate_queue.clear()
            self.btn_batch_generate.setText("🚀 批量生成缺失场景")
            self.log_console.append("<font color='orange'>⚠️ 已发送停止指令，将在当前节点完成后终止批量任务。</font>")
            return

        if not self.workspace or not self.outline_tree_data:
            QMessageBox.warning(self, "提示", "请先打开并加载一个工作区！")
            return

        missing_nodes = self._get_missing_level3_nodes(self.outline_tree_data.get("nodes", []))
        if not missing_nodes:
            QMessageBox.information(self, "提示", "当前没有缺失正文的场景节点（没有灰色节点）。")
            return

        reply = QMessageBox.question(
            self, 
            "确认批量生成", 
            f"大纲中找到了 {len(missing_nodes)} 个缺失文件（灰色）的场景。\n确认开始依次自动生成吗？这可能需要较长时间。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.batch_generate_queue = missing_nodes
            self.is_batch_generating = True
            self.btn_batch_generate.setText("🛑 停止批量生成")
            self._process_next_batch_node()

    def _process_next_batch_node(self):
        """从队列中抽取下一个节点并触发生成"""
        if not self.is_batch_generating:
            return

        # 队列执行完毕的处理
        if not self.batch_generate_queue:
            self.is_batch_generating = False
            self.btn_batch_generate.setText("🚀 批量生成缺失场景")
            self.btn_generate.setEnabled(True)
            self.btn_save.setEnabled(True)
            self.log_console.append("<b><font color='green'>🎉 批量生成任务全部完成！</font></b>")
            QMessageBox.information(self, "完成", "批量生成已结束。")
            return

        # 取出队列第一个节点
        next_node = self.batch_generate_queue.pop(0)
        self.log_console.append(f"<hr><b>⏳ 正在自动处理节点: {next_node.get('title')} (队列剩余 {len(self.batch_generate_queue)} 个)</b>")

        # 将当前编辑环境切换到目标节点
        self.current_editing_node = next_node
        self.current_setting_path = None

        self.summary_editor.setText(next_node.get("summary", ""))
        self.summary_editor.setEnabled(True)
        
        # 【修复点】：千万不要直接填入“生成中...”，会被保存进物理文件并污染AI的上下文。
        # 这里填入标准的空标题，ContextBuilder 内部判定时会自动将其当做空文件处理。
        self.content_editor.setText(f"# {next_node.get('title')}\n\n")
        self.content_editor.setEnabled(True)

        # 尝试在左侧树状视图中高亮选中该节点，方便用户追踪进度
        node_id = None
        for nid, n in self.node_map.items():
            if n is next_node:
                node_id = nid
                break

        if node_id:
            item = self._find_item_by_data(self.novel_tree.invisibleRootItem(), node_id)
            if item:
                self.current_editing_item = item
                self.novel_tree.setCurrentItem(item)

        # 触发单节点的生成逻辑 (这一步会先抽取当前编辑器内容进行 save_current_node)
        self.generate_current_node()
        
        # 【修复点】：在触发生成、上下文已经构建并发送给大模型后，再更新 UI 提示给用户看。
        self.content_editor.setText("（自动批量生成中，请稍候...）")

    # ================= 原有 AI 生成逻辑（微调回调流） =================

    def generate_current_node(self):
        # 调用大模型生成正文（使用 QThread 异步执行）"""
        if not self.current_editing_node or not self.outline_tree_data:
            return
            
        if not self.llm_client:
            QMessageBox.warning(self, "配置缺失", "尚未初始化大模型客户端，请检查 conf/setting.json 文件。")
            return
        
        # ================== 【修改点 1：生成前先强制保存】 ==================
        # 确保界面上刚输入的 summary 能立即更新到内存节点并落盘，防止传给大模型的是空字符串
        self.save_current_node()
        # ================================================================

        node_title = self.current_editing_node.get('title', '未知节点')
        self.log_console.append(f"开始构建【{node_title}】的上下文...")
        
        # 禁用相关按钮，防止用户在生成期间重复点击或误操作
        self.btn_generate.setEnabled(False) 
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        
        # 1. 实例化 Builder 并获取勾选的设定
        builder = ContextBuilder(self.workspace)
        checked_paths = self.get_checked_settings()
        
        # 2. 构建 Prompt 消息
        messages = builder.build_generation_prompt(
            self.current_editing_node, 
            self.outline_tree_data, 
            checked_paths,
            generate_image=self.cb_gen_image.isChecked(),
            word_count=self.spin_word_count.value(),
            include_next=self.cb_include_next.isChecked()
        )
        
        prompt_content = messages[-1]["content"]
        
        # 【修改】分别打印 System 指令和 User 提示词，避免产生未生效的错觉
        self.log_console.append("========== [System] 模型系统指令 (Instructions) ==========")
        self.log_console.append(self.llm_client.system_instruction)
        self.log_console.append("========== [User] 上下文与生成提示词 ==========")
        self.log_console.append(prompt_content)
        self.log_console.append("=================================================")
        self.log_console.append("发送请求至大语言模型，后台处理中，请稍候...")
        
        # 注意：不再需要 QApplication.processEvents()，因为主线程已经不会被阻塞了

        # 3. 创建并启动后台任务线程
        # 必须将线程实例挂载到 self 上，防止它在方法结束后被 Python 垃圾回收销毁
        self.generate_thread = GenerateTaskThread(self.llm_client, prompt_content)
        
        # 连接信号与槽（回调函数）
        self.generate_thread.success_signal.connect(self.on_generate_success)
        self.generate_thread.error_signal.connect(self.on_generate_error)
        
        # 启动线程
        self.generate_thread.start()

    # ================= 新增：异步生成的回调方法 =================

    def on_generate_success(self, result: str):
        # 接收子线程成功的信号"""
        self.content_editor.setText(result)
        self.log_console.append("生成成功！已填入编辑器并自动保存。")

        # ================== 【修改点 2：生成成功后自动保存】 ==================
        self.save_current_node()
        # ==================================================================

        self._restore_generate_ui_state()
        
        # 【修改】如果处于批量生成模式，触发下一个
        if self.is_batch_generating:
            self._process_next_batch_node()

    def on_generate_error(self, error_msg: str):
        # 接收子线程失败的信号"""
        self.log_console.append(f"<font color='red'>生成失败: {error_msg}</font>")
        if not self.is_batch_generating:
            QMessageBox.critical(self, "生成错误", f"大模型请求失败:\n{error_msg}")
        self._restore_generate_ui_state()
        
        # 【修改】如果处于批量生成模式，记录错误并继续往下跑
        if self.is_batch_generating:
            self.log_console.append("<font color='orange'>⚠️ 当前节点生成失败，跳过并处理下一个...</font>")
            self._process_next_batch_node()

    def _restore_generate_ui_state(self):
        # 【修改】只有在非批量状态下，才立即恢复按钮启用状态
        if not self.is_batch_generating:
            self.btn_generate.setEnabled(True)
            self.btn_save.setEnabled(True)
            self.btn_rewrite.setEnabled(True) # 【新增】恢复重写按钮
            if self.current_editing_node:
                self.btn_delete.setEnabled(not bool(self.current_editing_node.get("children")))
        self.generate_thread = None

if __name__ == '__main__':
    from ui.theme import get_dark_stylesheet
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(get_dark_stylesheet())
    window = NovelCreatorWindow()
    window.show()
    sys.exit(app.exec())