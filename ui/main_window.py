"""
NovelCreatorWindow 主窗口 —— 仅保留 __init__ / init_ui / refresh_ui 编排逻辑。
具体业务方法已拆分至 ui.mixins 各子模块中。
"""
import sys
import os

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTextEdit,
                             QPushButton, QSplitter, QMenuBar, QMenu, QTextBrowser,
                             QLabel, QCheckBox, QSpinBox)
from PyQt6.QtGui import QKeySequence, QAction, QShortcut
from PyQt6.QtCore import Qt

# 导入暗色主题配色常量
from ui.theme import TEXT_SECONDARY

# 导入核心逻辑层组件
from core.llm_client import LLMClient

# 导入 Mixin 子模块
from ui.mixins import (
    ConfigMixin,
    WorkspaceMixin,
    SettingTreeMixin,
    NovelTreeMixin,
    GenerationMixin,
    EditorMixin,
)


class NovelCreatorWindow(
    EditorMixin,
    GenerationMixin,
    NovelTreeMixin,
    SettingTreeMixin,
    WorkspaceMixin,
    ConfigMixin,
    QMainWindow,
):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI小说创作器")
        self.resize(1200, 800)

        # 运行时状态变量
        self.workspace = None             # 当前打开的工作区管理器实例
        self.outline_tree_data = None     # 内存中维护的小说树状结构字典
        self.current_editing_node = None  # 当前正在编辑器中编辑的节点字典数据
        self.current_editing_item = None  # 当前在树状视图中选中的 QTreeWidgetItem 实例
        self.current_setting_path = None  # 当前编辑的设定文件绝对路径
        self.node_map = {}                # 节点内存引用的绝对映射表
        self._updating_settings = False

        # 批量生成相关的状态变量
        self.batch_generate_queue = []
        self.is_batch_generating = False

        self.config = self._load_config()
        self.llm_client = LLMClient(self.config) if self.config else None

        self.init_ui()

        # 自动加载最近的工作区
        sys_state = self._load_sys_state()
        recent_workspaces = sys_state.get("recent_workspaces", [])
        if recent_workspaces and os.path.exists(recent_workspaces[0]):
            self._load_workspace_by_path(recent_workspaces[0])

    # ================= 初始化 UI ================= #

    def init_ui(self):
        # 显式创建 QMenuBar，避免 Pylance 认为其可能为 None
        menubar = QMenuBar(self)
        self.setMenuBar(menubar)
        file_menu = QMenu('文件', self)
        menubar.addMenu(file_menu)

        new_action = QAction('新建工作区', self)
        file_menu.addAction(new_action)
        new_action.triggered.connect(self.new_workspace)

        load_action = QAction('加载工作区', self)
        file_menu.addAction(load_action)
        load_action.triggered.connect(self.load_workspace)

        reload_action = QAction('重载工作区', self)
        reload_action.setShortcut(QKeySequence("Ctrl+R"))
        file_menu.addAction(reload_action)
        reload_action.triggered.connect(self.reload_workspace)

        save_action = file_menu.addAction('保存全部')
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self.save_all)
        export_html_action = file_menu.addAction('🌐 导出为可阅读网页 (HTML)')
        export_html_action.triggered.connect(self.export_to_html)
        setting_menu = menubar.addMenu('设置')
        settings_action = setting_menu.addAction('系统配置 (API/模型)')
        settings_action.triggered.connect(self.open_settings_dialog)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.setting_tree = QTreeWidget()
        self.setting_tree.setHeaderLabel("世界观设定")
        self.setting_tree.itemClicked.connect(self.on_setting_node_clicked)
        self.setting_tree.itemChanged.connect(self.on_setting_item_changed)
        self.setting_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setting_tree.customContextMenuRequested.connect(self.show_setting_context_menu)
        splitter.addWidget(self.setting_tree)

        self.novel_tree = QTreeWidget()
        self.novel_tree.setHeaderLabel("小说大纲结构")

        self.novel_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.novel_tree.customContextMenuRequested.connect(self.show_novel_context_menu)

        self.rename_shortcut = QShortcut(QKeySequence("F2"), self.novel_tree)
        self.rename_shortcut.activated.connect(self.rename_current_node)

        # 开启大纲树的拖拽支持
        self.novel_tree.setDragEnabled(True)
        self.novel_tree.setAcceptDrops(True)
        self.novel_tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        original_drop_event = self.novel_tree.dropEvent
        def custom_drop_event(event):
            original_drop_event(event)
            self._cleanup_tree_add_buttons()
            self.sync_tree_data_from_ui()
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
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.addWidget(QLabel("节点概要 (Summary - 保存至系统数据):"))
        self.summary_editor = QTextEdit()
        self.summary_editor.setEnabled(False)
        summary_layout.addWidget(self.summary_editor)
        editor_splitter.addWidget(summary_widget)

        # 下半部：正文 (保存在 MD 文件中)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)

        content_header_layout = QHBoxLayout()
        content_header_layout.addWidget(QLabel("节点正文 (Content - 保存至 .md 文件):"))
        self.word_count_label = QLabel("当前字数: 0")
        self.word_count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.word_count_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        content_header_layout.addWidget(self.word_count_label)
        content_layout.addLayout(content_header_layout)

        self.content_editor = QTextEdit()
        self.content_editor.setEnabled(False)
        self.content_editor.textChanged.connect(self.update_word_count)
        content_layout.addWidget(self.content_editor)
        editor_splitter.addWidget(content_widget)

        editor_splitter.setSizes([300, 700])
        detail_layout.addWidget(editor_splitter)

        # ================= 生成参数控制栏 =================
        param_layout = QHBoxLayout()

        self.cb_gen_image = QCheckBox("生成图片占位符")
        self.cb_gen_image.setChecked(False)
        param_layout.addWidget(self.cb_gen_image)

        self.cb_include_next = QCheckBox("纳入后续节点上下文")
        self.cb_include_next.setChecked(True)
        param_layout.addWidget(self.cb_include_next)

        param_layout.addWidget(QLabel(" 目标生成字数:"))
        self.spin_word_count = QSpinBox()
        self.spin_word_count.setRange(50, 20000)
        self.spin_word_count.setSingleStep(500)
        self.spin_word_count.setValue(5000)
        param_layout.addWidget(self.spin_word_count)

        param_layout.addStretch()
        detail_layout.addLayout(param_layout)

        # 底部按钮
        btn_layout = QHBoxLayout()
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

    # ================= UI 刷新总调度 ================= #

    def refresh_ui_from_workspace(self):
        """刷新整个 UI：分别委托给设定树和大纲树的渲染方法。"""
        if not self.workspace:
            return

        self.setting_tree.clear()
        self.novel_tree.clear()
        self.node_map.clear()

        # 渲染设定树（来自 SettingTreeMixin）
        self._refresh_setting_tree()

        # 渲染小说大纲树（来自 NovelTreeMixin）
        self._refresh_novel_tree()


if __name__ == '__main__':
    from ui.theme import get_dark_stylesheet
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(get_dark_stylesheet())
    window = NovelCreatorWindow()
    window.show()
    sys.exit(app.exec())
