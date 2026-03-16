import sys
import os
import json
import uuid  # 【新增】用于生成唯一的文件名

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTextEdit, 
                             QPushButton, QSplitter, QMenuBar, QMenu, QTextBrowser,
                             QLabel, QFileDialog, QMessageBox, 
                             QInputDialog, QDialog, QCheckBox, QSpinBox) # 【新增】QCheckBox, QSpinBox
from PyQt6.QtGui import QKeySequence, QColor, QAction, QShortcut
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

# ================= 新增：JSON 原生字符串清洗工具 =================
def clean_json_string(text: str) -> str:
    """提取字符串中的 JSON 内容，完全不使用正则表达式"""
    start_dict = text.find('{')
    start_list = text.find('[')
    
    start_idx = -1
    if start_dict != -1 and start_list != -1:
        start_idx = min(start_dict, start_list)
    elif start_dict != -1:
        start_idx = start_dict
    elif start_list != -1:
        start_idx = start_list
        
    if start_idx == -1:
        return text 

    end_dict = text.rfind('}')
    end_list = text.rfind(']')
    end_idx = max(end_dict, end_list)
    
    if end_idx != -1 and end_idx >= start_idx:
        return text[start_idx:end_idx+1]
    return text

# ================= 新增：世界观批量生成线程 =================
class WorldBuildingThread(QThread):
    progress_signal = pyqtSignal(str)
    success_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, workspace, idea, prompt_1_tpl, prompt_2_tpl, mode="init", parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.workspace = workspace
        self.idea = idea
        self.mode = mode # 'init' 为全新生成，'supplement' 为补充生成
        self.prompt_1_tpl = prompt_1_tpl # 【新增】
        self.prompt_2_tpl = prompt_2_tpl # 【新增】

    def run(self):
        try:
            self.progress_signal.emit("正在分析点子，规划设定目录架构...")
            
            # 第一步：获取设定列表
            existing_context = ""
            if self.mode == "supplement":
                existing_context = "当前已有设定的概括如下，请基于这些内容进行针对性补充，避免重复：\n"
                # 简单读取现有的 index.json 或者文件名
                for cat in self.workspace.setting_dirs:
                    cat_path = os.path.join(self.workspace.settings_path, cat)
                    if os.path.exists(cat_path):
                        files = [f.replace('.json', '') for f in os.listdir(cat_path) if f.endswith('.json') and f != 'template.json']
                        existing_context += f"【{cat}】: {', '.join(files)}\n"

            prompt_1 = self.prompt_1_tpl.format(idea=self.idea, existing_context=existing_context)
            list_res_raw = self.llm_client.generate_text(prompt_1)

            if list_res_raw.strip().startswith("> **生成失败:**"):
                self.error_signal.emit(list_res_raw)
                return
                
            list_res = clean_json_string(list_res_raw)
            try:
                settings_list = json.loads(list_res)
            except json.JSONDecodeError:
                self.error_signal.emit("大模型返回的规划列表 JSON 格式解析失败。")
                return

            if not isinstance(settings_list, list):
                self.error_signal.emit("大模型返回的数据结构异常，要求数组结构。")
                return

            total = len(settings_list)
            self.progress_signal.emit(f"规划完成，共需要生成/补充 {total} 个设定。开始逐一生成细节...")

            # 第二步：循环请求每个设定的细节
            for idx, item in enumerate(settings_list):
                cat = item.get("category", "其他设定")
                name = item.get("name", f"未命名_{idx}")
                summary = item.get("summary", "")
                
                # 确保分类在既定目录中
                if cat not in self.workspace.setting_dirs:
                    cat = "其他设定"

                self.progress_signal.emit(f"({idx+1}/{total}) 正在生成细节: {cat} - {name} ...")
                
                # 读取模板
                template_path = os.path.join(self.workspace.settings_path, cat, "template.json")
                template_str = "{}"
                if os.path.exists(template_path):
                    with open(template_path, 'r', encoding='utf-8') as f:
                        template_str = f.read()

                prompt_2 = self.prompt_2_tpl.format(
                    cat=cat, name=name, summary=summary, template_str=template_str
                )

                detail_res_raw = self.llm_client.generate_text(prompt_2)
                detail_res = clean_json_string(detail_res_raw)
                
                try:
                    detail_data = json.loads(detail_res)
                    # 补充一个 name 字段防止模板里漏了
                    detail_data["_node_name"] = name
                except json.JSONDecodeError:
                    # 如果这一个节点解析失败，写入纯文本让用户手动修
                    detail_data = {"错误说明": "AI 生成了非法的 JSON", "raw_content": detail_res}

                # 写入文件
                file_path = os.path.join(self.workspace.settings_path, cat, f"{name}.json")
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(detail_data, f, ensure_ascii=False, indent=4)

            self.success_signal.emit()
            
        except Exception as e:
            self.error_signal.emit(str(e))

# ================= 新增：目录索引生成线程 =================
class IndexGenerateThread(QThread):
    progress_signal = pyqtSignal(str)
    success_signal = pyqtSignal(str, str) # category, index_content
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, workspace, category, prompt_tpl, parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.workspace = workspace
        self.category = category
        self.prompt_tpl = prompt_tpl # 【新增】

    def run(self):
        try:
            cat_path = os.path.join(self.workspace.settings_path, self.category)
            content_list = []
            
            for file in os.listdir(cat_path):
                if file.endswith('.json') and file not in ['template.json', 'index.json']:
                    with open(os.path.join(cat_path, file), 'r', encoding='utf-8') as f:
                        content_list.append(f"【文件名】: {file}\n【内容】: {f.read()}\n")
            
            if not content_list:
                self.error_signal.emit(f"目录【{self.category}】下没有有效设定文件，无需生成索引。")
                return

            self.progress_signal.emit(f"正在读取【{self.category}】内容，构建概括目录...")
            all_content = "\n".join(content_list)

            # 【修改】使用传入的模板进行格式化
            prompt = self.prompt_tpl.format(category=self.category, all_content=all_content)
            
            res_raw = self.llm_client.generate_text(prompt)
            res = clean_json_string(res_raw)
            
            try:
                json.loads(res) # 校验是否能正常解析
            except json.JSONDecodeError:
                self.error_signal.emit("大模型返回的索引 JSON 解析失败。")
                return
                
            self.success_signal.emit(self.category, res)
        except Exception as e:
            self.error_signal.emit(str(e))

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

    # ================= 【新增功能 ：重复文件检查】 =================
    def _find_duplicate_paths(self, nodes: list) -> set:
        """递归遍历大纲，寻找并返回重复使用的 file_path 集合"""
        path_counts = {}
        def traverse(node_list):
            for node in node_list:
                p = node.get("file_path")
                if p:
                    path_counts[p] = path_counts.get(p, 0) + 1
                traverse(node.get("children", []))
        traverse(nodes)
        return set(p for p, count in path_counts.items() if count > 1)

    def _get_item_level(self, item):
        # 计算当前树节点的层级深度 (1=章, 2=节, 3=场景)"""
        level = 1
        p = item.parent()
        while p:
            level += 1
            p = p.parent()
        return level

    def _load_config(self) -> dict:
        # 读取系统配置文件 conf/setting.json
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

    def _get_or_create_prompt_template(self, template_name: str, default_text: str, desc: str) -> str:
        #【新增】获取或创建 Prompt 模板
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prompts_dir = os.path.join(base_dir, "data", "prompts")
        os.makedirs(prompts_dir, exist_ok=True)
        
        file_path = os.path.join(prompts_dir, template_name)
        
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
                
        # 如果文件不存在，弹窗要求用户输入/确认
        text, ok = QInputDialog.getMultiLineText(
            self, 
            "需要初始化 Prompt 模板", 
            f"未找到模板文件：{template_name}\n用途：{desc}\n请核对并确认模板内容：", 
            default_text
        )
        # 如果用户点了取消，默认使用自带的 fallback 文本
        final_text = text if ok and text.strip() else default_text
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(final_text)
        except Exception as e:
            self.log_console.append(f"<font color='red'>保存模板文件失败: {e}</font>")
            
        return final_text

    def init_ui(self):
        # 【修复类型检查】：显式创建 QMenuBar，避免 Pylance 认为其可能为 None
        menubar = QMenuBar(self)
        self.setMenuBar(menubar)
        file_menu = menubar.addMenu('文件')
        
        # 【修改点 2】：分离新建工作区和加载工作区逻辑
        new_action = file_menu.addAction('新建工作区')
        new_action.triggered.connect(self.new_workspace)
        
        load_action = file_menu.addAction('加载工作区')
        load_action.triggered.connect(self.load_workspace)

        # 【新增功能 1】：重载工作区 (Ctrl+R)
        reload_action = file_menu.addAction('重载工作区')
        reload_action.setShortcut(QKeySequence("Ctrl+R"))
        reload_action.triggered.connect(self.reload_workspace)
        
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
        self.setting_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setting_tree.customContextMenuRequested.connect(self.show_setting_context_menu)
        splitter.addWidget(self.setting_tree)

        self.novel_tree = QTreeWidget()
        self.novel_tree.setHeaderLabel("小说大纲结构")

        # 【新增功能 2】：为大纲树绑定 F2 修改标题快捷键
        self.rename_shortcut = QShortcut(QKeySequence("F2"), self.novel_tree)
        self.rename_shortcut.activated.connect(self.rename_current_node)
        
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
                files = os.listdir(cat_path)
                # 如果有 index.json 优先置顶显示
                if 'index.json' in files:
                    index_item = QTreeWidgetItem(cat_item, ["🌟 总体概括 (index)"])
                    index_item.setData(0, Qt.ItemDataRole.UserRole, os.path.join(cat_path, 'index.json'))
                    # 默认不自动勾选 index，防止挤占正文上下文 token
                    index_item.setFlags(index_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    index_item.setCheckState(0, Qt.CheckState.Unchecked)
                    index_item.setForeground(0, QColor("#E0B0FF")) # 给个特别的淡紫色

                for file in files:
                    if file.endswith('.json') and file not in ['template.json', 'index.json']:
                        file_item = QTreeWidgetItem(cat_item, [file.replace('.json', '')])
                        file_item.setData(0, Qt.ItemDataRole.UserRole, os.path.join(cat_path, file))
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

        # 【新增功能 3】：在构建树之前，先找出重复的 file_path 并打印警告
        self._duplicate_paths = self._find_duplicate_paths(nodes_ref)
        if self._duplicate_paths:
            self.log_console.append(f"<font color='red'><b>⚠️ 严重警告：检测到多个场景节点指向相同的物理文件！可能导致剧情覆盖或丢失。<br>冲突的底层文件列表：{', '.join(self._duplicate_paths)}<br>请手动在冲突的节点中点击“保存当前节点”以重新生成独立的 MD 文件绑定。</b></font>")
            QMessageBox.warning(self, "节点冲突警告", "检测到多个大纲节点共用了同一个本地 Markdown 文件（树状图中已标红）。\n请留意控制台警告，并手动编辑处理冲突！")

        self._build_novel_tree_ui(nodes_ref, self.novel_tree, level=1)
        self.novel_tree.expandAll()
        
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
            file_path = node.get("file_path") # 【新增获取 file_path】
            
            # 【修改点 3】：基于层级进行渲染，仅第3级缺失文件变色，1/2级固定黑色
            if level == 3:
                # 优先判定冲突：如果文件路径在查出来的重复集合里
                if file_path and getattr(self, '_duplicate_paths', None) and file_path in self._duplicate_paths:
                    item.setForeground(0, QColor(NODE_ERROR)) # 使用原有的报错红
                    node["_status"] = "duplicate_conflict"
                elif status == "missing":
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

        # 2. 如果点击的是普通的设定文件或索引文件
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not file_path or not os.path.exists(file_path):
            return # 点击的可能是父级分类目录，不做处理

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # ================= 修改点：特殊渲染 index.json 为富文本 =================
            if os.path.basename(file_path) == 'index.json':
                try:
                    data = json.loads(content)
                    overview = data.get("category_overview", "暂无概述")
                    items = data.get("items", [])
                    
                    # 拼接 HTML 富文本
                    html_content = f"<h2 style='color: #E0B0FF;'>🌟 分类总体概括</h2>"
                    html_content += f"<p><b>体系概述：</b><br>{overview}</p><hr>"
                    html_content += "<h3>包含的设定列表：</h3><ul>"
                    for it in items:
                        file_name = it.get('file_name', '未知').replace('.json', '')
                        brief = it.get('brief', '')
                        html_content += f"<li style='margin-bottom: 8px;'><b>【{file_name}】</b>: {brief}</li>"
                    html_content += "</ul>"
                    html_content += "<br><p style='color: gray; font-size: 12px;'><i>提示：此目录为自动生成，如需更新，请在左侧树状图右键点击分类名称重新生成。</i></p>"
                    
                    self.summary_editor.setHtml(html_content)
                    self.summary_editor.setReadOnly(True) # 设为只读
                    self.btn_save.setEnabled(False)       # 禁用保存按钮，避免误覆盖
                    self.log_console.append(f"打开总体概括: {os.path.basename(os.path.dirname(file_path))}/index.json")
                    
                except json.JSONDecodeError:
                    # 如果 JSON 损坏，降级为普通文本显示并允许编辑修复
                    self.summary_editor.setPlainText(content)
                    self.summary_editor.setReadOnly(False)
                    self.btn_save.setEnabled(True)
                    self.log_console.append(f"<font color='orange'>警告：index.json 格式损坏，已降级为纯文本显示。</font>")
            else:
                # 普通设定文件，正常显示纯文本 JSON
                self.summary_editor.setPlainText(content)
                self.summary_editor.setReadOnly(False)
                self.btn_save.setEnabled(True)
                self.log_console.append(f"打开设定文件: {os.path.basename(file_path)}")
            # =====================================================================
            
            # 更新状态变量，清空小说节点的状态，记录当前设定的路径
            self.current_editing_node = None 
            self.current_setting_path = file_path
            
            self.summary_editor.setEnabled(True)
            self.content_editor.setEnabled(False)
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

    # ================= 【新增功能 1：重载工作区】 =================
    def reload_workspace(self):
        """重载当前工作区，丢弃未保存的更改"""
        if not self.workspace:
            QMessageBox.information(self, "提示", "当前未打开任何工作区，无法重载。")
            return
            
        reply = QMessageBox.question(
            self,
            "重载工作区",
            "确定要重新加载当前工作区吗？\n警告：由于是强制读取本地硬盘配置，您当前未保存的所有修改（包含正在编辑的正文和概要）都将丢失！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.log_console.append("开始重载工作区...")
            self._load_workspace_by_path(self.workspace.workspace_path)


    # ================= 【新增功能 2：F2 重命名节点】 =================
    def rename_current_node(self):
        """按下 F2 触发，修改当前选中节点的标题并自动保存"""
        item = self.novel_tree.currentItem()
        # 如果未选中节点，或者选中的是“+ 新增...”按钮，则忽略
        if not item or item.text(0).startswith("+"):
            return
            
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        real_node = self.node_map.get(node_id)
        if not real_node:
            return

        old_title = real_node.get("title", "")
        # 弹出对话框要求输入新标题
        new_title, ok = QInputDialog.getText(self, "重命名节点", "请输入新的节点名称:", text=old_title)
        
        if ok and new_title.strip() and new_title.strip() != old_title:
            clean_title = new_title.strip()
            # 1. 更新内存数据
            real_node["title"] = clean_title
            # 2. 更新 UI 显示
            item.setText(0, clean_title)
            
            # 3. 自动保存大纲配置
            if self.workspace and self.outline_tree_data:
                self.workspace.save_outline_tree(self.outline_tree_data)
                self.log_console.append(f"🔄 节点已重命名: 【{old_title}】 -> 【{clean_title}】 (已自动保存大纲)")

    def save_all(self):
        # 处理全局保存菜单动作 (Ctrl+S 触发)"""
        if self.workspace and self.outline_tree_data:
            self.workspace.save_outline_tree(self.outline_tree_data)
            
            # 顺便尝试把当前正在编辑的节点物理文件也保存一下
            if self.current_editing_node and self.content_editor.isEnabled():
                self.save_current_node()
                
            self.log_console.append("<b><font color='green'>[系统通知] 执行全局保存成功 (Ctrl+S)。</font></b>")
            # 【修复类型检查】：显式获取或创建状态栏并断言类型
            statusbar = self.statusBar()
            if statusbar is not None:
                statusbar.showMessage("全局保存成功", 1000)
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

    # ================= 【新增】世界观设定的右键菜单逻辑 =================
    def show_setting_context_menu(self, position):
        if not self.workspace:
            return
            
        menu = QMenu()
        item = self.setting_tree.itemAt(position)
        
        # 空白处或任意位置的全局操作
        gen_init_action = menu.addAction("💡 输入点子自动产生基础设定")
        gen_init_action.triggered.connect(lambda: self.open_world_building_dialog("init"))
        
        gen_sup_action = menu.addAction("💡 针对现有内容补充新设定")
        gen_sup_action.triggered.connect(lambda: self.open_world_building_dialog("supplement"))
        
        menu.addSeparator()

        # 仅针对特定的分类目录生成 Index
        if item and not item.parent() and not item.text(0).startswith("+"):
            category_name = item.text(0)
            gen_index_action = menu.addAction(f"📝 生成/更新【{category_name}】的总体概括 (index.json)")
            gen_index_action.triggered.connect(lambda: self.start_index_generation(category_name))

        menu.exec(self.setting_tree.viewport().mapToGlobal(position))

    def open_world_building_dialog(self, mode="init"):
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return
            
        title = "全新生成背景资料" if mode == "init" else "针对性补充背景资料"
        hint = "请输入您的核心点子或世界观框架概念（例如：一个由猫咪统治的赛博朋克城市）："
        
        idea, ok = QInputDialog.getMultiLineText(self, title, hint)
        if ok and idea.strip():
            self.log_console.append(f"启动设定生成引擎，模式：{mode}。后台处理中，请稍候...")
            self.btn_save.setEnabled(False) # 暂时禁用保存防止冲突

            default_p1 = "你是一个资深的小说世界观设计师，擅长根据简短的点子构建丰富的背景设定。请根据以下核心概念，帮我规划出一个适合小说创作的世界观分类体系（例如：种族、政治、科技、宗教等），并列出每个分类下的关键要素名称。"
            default_p2 = "请根据之前规划的世界观分类体系，针对每个要素进行详细的设定扩写，内容可以包含但不限于：外貌特征、社会结构、历史背景、与其他要素的关系等。请尽量丰富细节，帮助我构建一个生动立体的小说世界。"
            
            p1_tpl = self._get_or_create_prompt_template("world_build_list.txt", default_p1, "世界观分类规划")
            p2_tpl = self._get_or_create_prompt_template("world_build_detail.txt", default_p2, "世界观细节扩写")
            
            self.wb_thread = WorldBuildingThread(self.llm_client, self.workspace, idea.strip(), mode, p1_tpl, p2_tpl)
            self.wb_thread.progress_signal.connect(lambda msg: self.log_console.append(f"<font color='cyan'>{msg}</font>"))
            self.wb_thread.success_signal.connect(self.on_world_building_success)
            self.wb_thread.error_signal.connect(self.on_world_building_error)
            self.wb_thread.start()

    def on_world_building_success(self):
        self.log_console.append("<b><font color='green'>🎉 自动设定生成完毕！已将设定文件分配至对应目录。</font></b>")
        self.refresh_ui_from_workspace() # 刷新左侧树结构，自动加载新文件
        QMessageBox.information(self, "生成完成", "世界观设定生成完毕，请在左侧目录查看。")
        self.btn_save.setEnabled(True)

    def on_world_building_error(self, err_msg):
        self.log_console.append(f"<font color='red'>世界观生成中断或失败: {err_msg}</font>")
        QMessageBox.warning(self, "生成失败", f"流程中断:\n{err_msg}")
        self.btn_save.setEnabled(True)

    def start_index_generation(self, category):
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return
            
        self.log_console.append(f"开始为【{category}】生成总体概括目录...")

        # 【修正新增】：定义默认的 Prompt 并读取/创建模板文件
        default_index_prompt = "请根据以下内容，帮我提取出一份结构化的概括目录（JSON格式）：\n{all_content}"
        prompt_tpl = self._get_or_create_prompt_template(
            "index_generate.txt", 
            default_index_prompt, 
            f"{category}的总体概括索引生成"
        )

        self.index_thread = IndexGenerateThread(self.llm_client, self.workspace, category, prompt_tpl)
        self.index_thread.progress_signal.connect(lambda msg: self.log_console.append(f"<font color='cyan'>{msg}</font>"))
        self.index_thread.success_signal.connect(self.on_index_generation_success)
        self.index_thread.error_signal.connect(self.on_index_generation_error)
        self.index_thread.start()

    def on_index_generation_success(self, category, index_content):
        cat_path = os.path.join(self.workspace.settings_path, category)
        index_path = os.path.join(cat_path, "index.json")
        
        try:
            # 格式化一下 JSON 再保存
            parsed_json = json.loads(index_content)
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(parsed_json, f, ensure_ascii=False, indent=4)
            
            self.log_console.append(f"<b><font color='green'>🎉 【{category}】总体概括目录生成完毕！</font></b>")
            self.refresh_ui_from_workspace()
        except Exception as e:
            self.log_console.append(f"<font color='red'>保存 index.json 时出错: {e}</font>")

    def on_index_generation_error(self, err_msg):
        self.log_console.append(f"<font color='red'>索引生成失败: {err_msg}</font>")
        QMessageBox.warning(self, "生成失败", f"生成索引流程中断:\n{err_msg}")

if __name__ == '__main__':
    from ui.theme import get_dark_stylesheet
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(get_dark_stylesheet())
    window = NovelCreatorWindow()
    window.show()
    sys.exit(app.exec())