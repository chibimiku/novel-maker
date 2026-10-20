"""
NovelCreatorWindow 主窗口 —— 仅保留 __init__ / init_ui / refresh_ui 编排逻辑。
具体业务方法已拆分至 ui.mixins 各子模块中。
"""
import sys
import os
from contextlib import contextmanager
from typing import Any

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QTreeWidget, QTextEdit,
                             QPushButton, QSplitter, QMenuBar, QMenu, QTextBrowser,
                             QLabel, QCheckBox, QSpinBox, QAbstractItemView,
                             QSizePolicy, QScrollArea, QFrame,
                             QToolButton, QMessageBox)
from PyQt6.QtGui import (
    QKeySequence,
    QAction,
    QShortcut,
    QCloseEvent,
    QTextCharFormat,
    QPalette,
)
from PyQt6.QtCore import Qt, QTimer, QEvent, QUrl
from PyQt6.QtGui import QDesktopServices

# 导入暗色主题配色常量
from ui.theme import TEXT_SECONDARY

# 导入时间线对话框
from ui.timeline_dialog import TimelineDialog

# 导入核心逻辑层组件
from core.llm_client import LLMClient
from core.workspace_manager import WorkspaceManager
from core.file_tracker import FileTracker
from ui.utils import get_item_level

# 导入 Mixin 子模块
from ui.mixins import (
    ConfigMixin,
    WorkspaceMixin,
    SettingTreeMixin,
    NovelTreeMixin,
    GenerationMixin,
    EditorMixin,
    TaskMixin,
    AgentMixin,
)

class NovelTreeWidget(QTreeWidget):
    """自定义的小说大纲树控件，负责拦截并处理拖拽逻辑"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window: 'NovelCreatorWindow | None' = None
        self._dragged_item = None

    def get_item_level(self, item):
        """辅助方法：获取节点层级，顶级为1"""
        level = 1
        parent = item.parent()
        while parent:
            level += 1
            parent = parent.parent()
        return level

    def dragMoveEvent(self, event: Any):
        if event is None:
            return
        super().dragMoveEvent(event)
        if not event.isAccepted():
            return

        target_item = self.itemAt(event.position().toPoint())
        dragged_items = self.selectedItems()
        if not dragged_items:
            event.ignore()
            return

        dragged_item = dragged_items[0]
        drop_pos = self.dropIndicatorPosition()

        # 检查是否包含真正的业务子节点（排除 "+" 按钮）
        has_real_children = False
        for i in range(dragged_item.childCount()):
            child = dragged_item.child(i)
            if child and not child.text(0).startswith("+"):
                has_real_children = True
                break

        if target_item:
            if target_item.text(0).startswith("+"):
                # 严禁任何放到 + 按钮内部(OnItem) 或 放到 + 按钮下方(BelowItem) 的操作
                if drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem or \
                   drop_pos == QAbstractItemView.DropIndicatorPosition.BelowItem:
                    event.ignore()
                    return

            target_level = self.get_item_level(target_item)
            new_level = target_level + 1 if drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem else target_level

            if has_real_children and new_level >= 3:
                event.ignore()
                return
            if new_level > 3:
                event.ignore()
                return


    def startDrag(self, supportedActions):
        """记录即将被拖拽的节点，并保存完整的树数据快照"""
        dragged_items = self.selectedItems()
        if dragged_items:
            self._dragged_item = dragged_items[0]
            # 保存完整的树数据快照
            if self.main_window and self.main_window.outline_tree_data:
                import copy
                self.main_window._pre_drag_tree_snapshot = copy.deepcopy(self.main_window.outline_tree_data)
        super().startDrag(supportedActions)

    def dropEvent(self, event: Any):
        if event is None:
            return
        target_item = self.itemAt(event.position().toPoint())
        dragged_items = self.selectedItems()
        if not dragged_items:
            event.ignore()
            return

        dragged_item = self._dragged_item if self._dragged_item else dragged_items[0]
        drop_pos = self.dropIndicatorPosition()

        has_real_children = False
        for i in range(dragged_item.childCount()):
            child = dragged_item.child(i)
            if child and not child.text(0).startswith("+"):
                has_real_children = True
                break

        if target_item:
            if target_item.text(0).startswith("+"):
                if drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem or \
                   drop_pos == QAbstractItemView.DropIndicatorPosition.BelowItem:
                    event.ignore()
                    return

            target_level = self.get_item_level(target_item)
            new_level = target_level + 1 if drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem else target_level

            if has_real_children and new_level >= 3:
                event.ignore()
                return
            if new_level > 3:
                event.ignore()
                return

        # 1. 先让 Qt 完成它的原生物理位置移动
        super().dropEvent(event)

        # 2. 【核心修复】：立刻执行自愈机制，修正 Qt 违规塞入的节点
        self._rescue_add_button_children()

        # 3. 延后同步逻辑，保障底层 C++ 移动动作已彻底完结
        if self.main_window:
            QTimer.singleShot(0, self.main_window._handle_drop_sync)

    def _rescue_add_button_children(self, parent_item: Any = None):
        """
        自愈机制：倒序遍历节点。如果发现 '+' 按钮被 Qt 强行塞了子节点，
        就把它提取出来，放到 '+' 按钮的前面（成为正常的同级节点）。
        """
        target = parent_item if parent_item else self.invisibleRootItem()
        if target is None:
            return
        # 必须倒序遍历，因为我们要执行插入操作，正序会打乱索引
        for i in range(target.childCount() - 1, -1, -1):
            child = target.child(i)
            if child is None:
                continue
            if child.text(0).startswith("+"):
                # 如果发现加号按钮有子节点（非法状态）
                while child.childCount() > 0:
                    # 将其提取出来
                    rescued_node = child.takeChild(0)
                    # 插入到 target 中，位置在当前加号按钮的正前方
                    if rescued_node is not None:
                        target.insertChild(i, rescued_node)
                    # 递归检查刚被提取出来的节点
                    self._rescue_add_button_children(rescued_node)
            else:
                self._rescue_add_button_children(child)


class SafeLogBrowser(QTextBrowser):
    """每次输出后重置当前字符样式，避免颜色串色到后续普通日志。"""

    def append(self, text: str) -> None:  # type: ignore[override]
        super().append(text)
        fmt = QTextCharFormat()
        fmt.setForeground(self.palette().color(QPalette.ColorRole.Text))
        self.setCurrentCharFormat(fmt)


class NovelCreatorWindow(
    EditorMixin,
    GenerationMixin,
    NovelTreeMixin,
    SettingTreeMixin,
    WorkspaceMixin,
    TaskMixin,
    AgentMixin,
    ConfigMixin,
    QMainWindow,
):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI小说创作器")
        self.resize(1200, 800)

        # 运行时状态变量
        self.workspace: WorkspaceManager | None = None  # 当前打开的工作区管理器实例
        self.outline_tree_data = None     # 内存中维护的小说树状结构字典
        self.current_editing_node = None  # 当前正在编辑器中编辑的节点字典数据
        self.current_editing_item = None  # 当前在树状视图中选中的 QTreeWidgetItem 实例
        self.current_setting_path = None  # 当前编辑的设定文件绝对路径
        self.node_map = {}                # 节点内存引用的绝对映射表
        self._updating_settings = False
        self.generate_thread = None       # 当前通用生成线程（正文/概要）

        # 批量生成相关的状态变量
        self.batch_generate_queue = []
        self.is_batch_generating = False
        self._is_batch_generation_context = False
        
        # 回退缓冲区相关变量
        self.undo_stack = []              # 回退缓冲区，最多保存20次变更
        self.max_undo_stack = 20          # 最大回退次数
        self.current_node_original_summary = ""  # 当前节点的原始概要
        self.current_node_original_content = ""  # 当前节点的原始内容
        
        # 拖拽前的树数据快照
        self._pre_drag_tree_snapshot = None
        # 节点剪贴板缓存（配合系统剪贴板 JSON）
        self._novel_node_clipboard = None
        # 统一 Debug Log 开关（持久化）
        self._debug_log_enabled = self._get_debug_log_enabled()
        # 兼容旧变量读取
        self._debug_add_button = self._debug_log_enabled
        os.environ["PROOFREAD_DEBUG_LOG"] = "1" if self._debug_log_enabled else "0"
        # “修改内容”时是否附加写作风格（持久化）
        self._append_writing_style_on_modify = self._get_modify_style_option()
        # 批量修改请求并发线程数（持久化）
        self._batch_modify_thread_count = self._get_batch_modify_thread_count()
        # 内容变更后自动导出 www 网页（持久化）
        self._auto_export_www_enabled = self._get_auto_export_www_enabled()
        # 生成前是否启用“设定集智能勾选”（会额外请求一次 LLM）
        self._smart_setting_selection_enabled = self._get_smart_setting_selection_enabled()
        # 工作区是否启用 NSFW 文本模型配置
        self._workspace_is_nsfw = False
        # 文件变更追踪器（保存前检测外部修改）
        self.file_tracker = FileTracker()

        self.config = self._load_config()
        self._configure_task_queue()
        self.llm_client: LLMClient | None = self._create_llm_client()

        self.init_ui()
        self._ui_state_save_timer = QTimer(self)
        self._ui_state_save_timer.setSingleShot(True)
        self._ui_state_save_timer.timeout.connect(self._save_window_ui_state)
        self._restore_window_ui_state()

        # 自动加载最近的工作区
        sys_state = self._load_sys_state()
        recent_workspaces = sys_state.get("recent_workspaces", [])
        if recent_workspaces and os.path.exists(recent_workspaces[0]):
            self._load_workspace_by_path(recent_workspaces[0])

    def _set_loading_state(self, message: str, loading: bool) -> None:
        """统一管理短时阻塞任务的可见状态，避免用户感知为“无响应”"""
        if loading:
            if not hasattr(self, "_loading_depth"):
                self._loading_depth = 0
            self._loading_depth += 1
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            statusbar = self.statusBar()
            if statusbar is not None:
                statusbar.showMessage(message or "处理中...")
            QApplication.processEvents()
            return

        depth = max(int(getattr(self, "_loading_depth", 0)) - 1, 0)
        self._loading_depth = depth
        if depth == 0:
            QApplication.restoreOverrideCursor()
            statusbar = self.statusBar()
            if statusbar is not None:
                statusbar.clearMessage()
            QApplication.processEvents()

    @contextmanager
    def loading_ui(self, message: str):
        self._set_loading_state(message, True)
        try:
            yield
        finally:
            self._set_loading_state("", False)

    # ================= 初始化 UI ================= #

    def init_ui(self):
        # 显式创建 QMenuBar，避免 Pylance 认为其可能为 None
        menubar = QMenuBar(self)
        self.setMenuBar(menubar)
        self._build_menus(menubar)

        nsfw_corner_widget = QWidget(self)
        nsfw_corner_layout = QHBoxLayout(nsfw_corner_widget)
        nsfw_corner_layout.setContentsMargins(0, 0, 8, 0)
        self.btn_open_workspace = QPushButton("打开工作区")
        self.btn_open_workspace.setEnabled(False)
        self.btn_open_workspace.clicked.connect(self.open_workspace_folder)
        nsfw_corner_layout.addWidget(self.btn_open_workspace)
        self.cb_workspace_nsfw = QCheckBox("NSFW工作区")
        self.cb_workspace_nsfw.setEnabled(False)
        self.cb_workspace_nsfw.setToolTip(
            "勾选后，该工作区将使用 NSFW 文本模型配置（独立的 API/模型/Prompt）。"
        )
        self.cb_workspace_nsfw.toggled.connect(self.on_workspace_nsfw_toggled)
        nsfw_corner_layout.addWidget(self.cb_workspace_nsfw)
        menubar.setCornerWidget(nsfw_corner_widget, Qt.Corner.TopRightCorner)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(True)
        self.main_splitter = splitter

        self.setting_tree = QTreeWidget()
        self.setting_tree.setMinimumWidth(0)
        self.setting_tree.setHeaderLabel("世界观设定")
        self.setting_tree.itemClicked.connect(self.on_setting_node_clicked)
        self.setting_tree.itemChanged.connect(self.on_setting_item_changed)
        self.setting_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setting_tree.customContextMenuRequested.connect(self.show_setting_context_menu)
        splitter.addWidget(self.setting_tree)

        # 使用我们自定义的 NovelTreeWidget
        self.novel_tree = NovelTreeWidget()
        self.novel_tree.setMinimumWidth(0)
        self.novel_tree.main_window = self  # 绑定主窗口引用
        self.novel_tree.setHeaderLabel("小说大纲结构")
        self.novel_tree.setItemsExpandable(True)
        self.novel_tree.setRootIsDecorated(True)
        self.novel_tree.setExpandsOnDoubleClick(True)
        # 说明：旧实现用 QProxyStyle(CompactTreeStyle) 缩小勾选框/缩进，
        # 但 Python 侧创建的 QProxyStyle 在进程退出时会被析构，导致
        # Windows 上出现 STATUS_STACK_BUFFER_OVERRUN 崩溃（退出码 0xC0000409）。
        # 改用 QTreeView 自带的 indentation 与样式表，行为一致且不会崩溃。
        self.novel_tree.setIndentation(14)
        self.novel_tree.setStyleSheet(
            "QTreeWidget { font-size: 12px; }"
            "QTreeView::indicator { width: 12px; height: 12px; }"
        )

        self.novel_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.novel_tree.customContextMenuRequested.connect(self.show_novel_context_menu)
        self.novel_tree.itemChanged.connect(self.on_novel_item_changed)
        self.novel_tree.itemExpanded.connect(self.on_novel_item_expanded)

        self.rename_shortcut = QShortcut(QKeySequence("F2"), self.novel_tree)
        self.rename_shortcut.activated.connect(self.rename_current_node)

        # 开启大纲树的拖拽支持
        self.novel_tree.setDragEnabled(True)
        self.novel_tree.setAcceptDrops(True)
        self.novel_tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        
        self.novel_tree.itemClicked.connect(self.on_novel_node_clicked)
        splitter.addWidget(self.novel_tree)

        # ================= 右侧：拆分概要与正文区 =================
        detail_widget = QWidget()
        detail_widget.setMinimumWidth(0)
        detail_layout = QVBoxLayout(detail_widget)

        editor_splitter = QSplitter(Qt.Orientation.Vertical)
        self.editor_splitter = editor_splitter

        # 上半部：概要 (保存在 JSON 中)
        summary_widget = QWidget()
        summary_layout = QVBoxLayout(summary_widget)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        self.summary_title_label = QLabel("节点概要 (Summary - 保存至系统数据):")
        summary_layout.addWidget(self.summary_title_label)
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

        self.cb_auto_export_www = QCheckBox("变动后自动更新www网页")
        self.cb_auto_export_www.setChecked(self._auto_export_www_enabled)
        self.cb_auto_export_www.toggled.connect(self.on_auto_export_www_toggled)
        param_layout.addWidget(self.cb_auto_export_www)

        self.cb_smart_setting_select = QCheckBox("智能选择设定集(额外请求LLM)")
        self.cb_smart_setting_select.setChecked(self._smart_setting_selection_enabled)
        self.cb_smart_setting_select.toggled.connect(self.on_smart_setting_selection_toggled)
        self.cb_smart_setting_select.setToolTip(
            "勾选后，生成正文/重写正文/生成概要前会先请求一次LLM，"
            "根据当前任务上下文和设定文件名自动筛选要引入的设定。"
        )
        param_layout.addWidget(self.cb_smart_setting_select)

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
        self.btn_regenerate_summary = QPushButton("📝 重新生成概要")
        self.btn_undo = QPushButton("↶ 后退")
        self.btn_save = QPushButton("💾 保存当前节点")
        self.btn_delete = QPushButton("🗑️ 删除当前节点")

        self.btn_batch_generate.clicked.connect(self.start_batch_generate)
        self.btn_generate.clicked.connect(self.generate_current_node)
        self.btn_rewrite.clicked.connect(self.rewrite_current_node)
        self.btn_regenerate_summary.clicked.connect(self.regenerate_summary)
        self.btn_undo.clicked.connect(self.undo_last_change)
        self.btn_save.clicked.connect(self.save_current_node)
        self.btn_delete.clicked.connect(self.delete_current_node)

        self.btn_generate.setEnabled(False)
        self.btn_rewrite.setEnabled(False)
        self.btn_regenerate_summary.setEnabled(False)
        self.btn_undo.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        compact_buttons = [
            self.btn_batch_generate,
            self.btn_generate,
            self.btn_rewrite,
            self.btn_regenerate_summary,
            self.btn_undo,
            self.btn_save,
            self.btn_delete,
        ]
        for btn in compact_buttons:
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        btn_layout.addWidget(self.btn_batch_generate)
        btn_layout.addWidget(self.btn_generate)
        btn_layout.addWidget(self.btn_rewrite)
        btn_layout.addWidget(self.btn_regenerate_summary)
        btn_layout.addWidget(self.btn_undo)
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_delete)
        detail_layout.addLayout(btn_layout)

        splitter.addWidget(detail_widget)
        splitter.setCollapsible(0, True)
        splitter.setCollapsible(1, True)
        splitter.setCollapsible(2, True)
        splitter.setSizes([180, 430, 590])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.splitterMoved.connect(self._schedule_window_ui_state_save)
        editor_splitter.splitterMoved.connect(self._schedule_window_ui_state_save)
        main_layout.addWidget(splitter, stretch=4)

        # ================= 📱 待处理 Issue 面板 =================
        self.issue_panel_frame = QFrame()
        self.issue_panel_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        self.issue_panel_frame.setVisible(False)

        issue_panel_layout = QVBoxLayout(self.issue_panel_frame)
        issue_panel_layout.setContentsMargins(0, 0, 0, 0)
        issue_panel_layout.setSpacing(0)

        self.issue_header_widget = QWidget()
        self.issue_header_widget.setFixedHeight(32)
        header_layout = QHBoxLayout(self.issue_header_widget)
        header_layout.setContentsMargins(8, 2, 8, 2)

        self.issue_toggle_btn = QToolButton()
        self.issue_toggle_btn.setArrowType(Qt.ArrowType.DownArrow)
        self.issue_toggle_btn.setToolTip("展开/折叠 Issue 面板")
        self.issue_toggle_btn.setAutoRaise(True)
        self.issue_toggle_btn.clicked.connect(self._toggle_issue_panel)
        header_layout.addWidget(self.issue_toggle_btn)

        self.issue_count_label = QLabel("📱 待处理 Issue (0)")
        header_layout.addWidget(self.issue_count_label)

        header_layout.addStretch()

        self.issue_refresh_btn = QPushButton("刷新")
        self.issue_refresh_btn.setFixedHeight(24)
        self.issue_refresh_btn.clicked.connect(self._refresh_issue_panel)
        header_layout.addWidget(self.issue_refresh_btn)

        self.issue_submit_all_btn = QPushButton("全部提交")
        self.issue_submit_all_btn.setFixedHeight(24)
        self.issue_submit_all_btn.clicked.connect(self._on_issue_submit_all)
        header_layout.addWidget(self.issue_submit_all_btn)

        issue_panel_layout.addWidget(self.issue_header_widget)

        self.issue_scroll_area = QScrollArea()
        self.issue_scroll_area.setWidgetResizable(True)
        self.issue_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.issue_scroll_area.setVisible(False)

        self.issue_list_widget = QWidget()
        self.issue_list_layout = QVBoxLayout(self.issue_list_widget)
        self.issue_list_layout.setContentsMargins(8, 4, 8, 4)
        self.issue_list_layout.setSpacing(4)
        self.issue_list_layout.addStretch()
        self.issue_scroll_area.setWidget(self.issue_list_widget)

        issue_panel_layout.addWidget(self.issue_scroll_area)

        main_layout.addWidget(self.issue_panel_frame, stretch=0)

        self.log_console = SafeLogBrowser()
        self.log_console.setFixedHeight(150)
        self.log_console.append("系统初始化完成。")
        main_layout.addWidget(self.log_console, stretch=1)

        # ================= 状态栏：Agent / 任务队列入口 =================
        statusbar = self.statusBar()
        self.btn_agent_console = QPushButton("🤖 Agent")
        self.btn_agent_console.setFlat(True)
        self.btn_agent_console.setToolTip("输入命令 → 自动规划步骤 → 逐步执行（Ctrl+Shift+A）")
        self.btn_agent_console.clicked.connect(self.open_agent_console)
        statusbar.addPermanentWidget(self.btn_agent_console)
        self.btn_task_queue = QPushButton("📋 任务队列")
        self.btn_task_queue.setFlat(True)
        self.btn_task_queue.setToolTip("查看每个动作下所有原子任务的输入/输出（Ctrl+T）")
        self.btn_task_queue.clicked.connect(self.open_task_queue_dialog)
        statusbar.addPermanentWidget(self.btn_task_queue)
        self.task_status_label = QLabel("")
        self.task_status_label.setStyleSheet(f"color: {TEXT_SECONDARY}; padding-right: 6px;")
        statusbar.addPermanentWidget(self.task_status_label)
        self._task_status_timer = QTimer(self)
        self._task_status_timer.setInterval(5000)
        self._task_status_timer.timeout.connect(self._update_task_status_label)
        self._task_status_timer.start()
        self._update_task_status_label()

    # ================= 菜单 ================= #

    def _build_menus(self, menubar: QMenuBar):
        """统一构建菜单栏：文件 / 编辑 / 视图 / 生成 / 工具 / 设置 / 帮助。"""
        self.menus: dict[str, QMenu] = {}

        def _action(menu, text, slot, shortcut=None, checkable=False, checked=False,
                    attr_name=None):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            if checkable:
                action.setCheckable(True)
                action.setChecked(checked)
            if slot is not None:
                # 可勾选动作监听 toggled，保证程序化 setChecked 也能生效
                if checkable:
                    action.toggled.connect(slot)
                else:
                    action.triggered.connect(slot)
            menu.addAction(action)
            if attr_name:
                setattr(self, attr_name, action)
            return action

        # ---------------- 文件 ----------------
        file_menu = menubar.addMenu("文件")
        self.menus["文件"] = file_menu
        _action(file_menu, "新建工作区", self.new_workspace, "Ctrl+N",
                attr_name="action_new_workspace")
        _action(file_menu, "打开工作区…", self.load_workspace, "Ctrl+O",
                attr_name="action_load_workspace")
        _action(file_menu, "重载工作区", self.reload_workspace, "Ctrl+R",
                attr_name="action_reload_workspace")
        file_menu.addSeparator()
        _action(file_menu, "保存全部", self.save_all, "Ctrl+S",
                attr_name="action_save_all")
        export_menu = file_menu.addMenu("导出")
        self.menus["导出"] = export_menu
        _action(export_menu, "🌐 导出为可阅读网页 (HTML)", self.export_to_html, "Ctrl+E",
                attr_name="action_export_html")
        _action(export_menu, "📖 导出为 EPUB 电子书", self.export_to_epub,
                attr_name="action_export_epub")
        file_menu.addSeparator()
        _action(file_menu, "在资源管理器中打开工作区", self.open_workspace_folder,
                attr_name="action_open_workspace_folder")
        file_menu.addSeparator()
        _action(file_menu, "退出", self.close, "Ctrl+Q", attr_name="action_quit")

        # ---------------- 编辑 ----------------
        edit_menu = menubar.addMenu("编辑")
        self.menus["编辑"] = edit_menu
        _action(edit_menu, "保存当前节点", self.save_current_node, "Ctrl+Shift+S",
                attr_name="action_save_node")
        _action(edit_menu, "删除当前节点", self.delete_current_node,
                attr_name="action_delete_node")
        edit_menu.addSeparator()
        _action(edit_menu, "后退一步（撤销本节点修改）", self.undo_last_change, "Ctrl+Z",
                attr_name="action_undo")
        edit_menu.addSeparator()
        _action(edit_menu, "全选大纲节点", self.select_all_nodes,
                attr_name="action_select_all_nodes")
        _action(edit_menu, "全不选大纲节点", self.select_none_nodes,
                attr_name="action_select_none_nodes")

        # ---------------- 视图 ----------------
        view_menu = menubar.addMenu("视图")
        self.menus["视图"] = view_menu
        _action(view_menu, "世界观设定面板",
                lambda checked: self.setting_tree.setVisible(checked),
                checkable=True, checked=True, attr_name="action_view_settings")
        _action(view_menu, "小说大纲面板",
                lambda checked: self.novel_tree.setVisible(checked),
                checkable=True, checked=True, attr_name="action_view_outline")
        _action(view_menu, "编辑区",
                lambda checked: self.editor_splitter.setVisible(checked),
                checkable=True, checked=True, attr_name="action_view_editor")
        _action(view_menu, "日志面板",
                lambda checked: self.log_console.setVisible(checked),
                checkable=True, checked=True, attr_name="action_view_log")
        _action(view_menu, "Issue 面板",
                lambda checked: self._set_issue_panel_visible(checked),
                checkable=True, checked=False, attr_name="action_view_issue")
        view_menu.addSeparator()
        self.action_debug_log = QAction("显示 Debug Log", self)
        self.action_debug_log.setCheckable(True)
        self.action_debug_log.setChecked(self._debug_log_enabled)
        self.action_debug_log.toggled.connect(self.set_debug_log_enabled)
        view_menu.addAction(self.action_debug_log)
        view_menu.addSeparator()
        _action(view_menu, "重置界面布局", self.reset_window_layout,
                attr_name="action_reset_layout")

        # ---------------- 生成 ----------------
        generate_menu = menubar.addMenu("生成")
        self.menus["生成"] = generate_menu
        _action(generate_menu, "结合上下文生成正文", self.generate_current_node, "Ctrl+G",
                attr_name="action_generate")
        _action(generate_menu, "基于原文重写（扩写/缩写）", self.rewrite_current_node,
                "Ctrl+Shift+R", attr_name="action_rewrite")
        _action(generate_menu, "重新生成概要", self.regenerate_summary,
                attr_name="action_regenerate_summary")
        generate_menu.addSeparator()
        _action(generate_menu, "批量生成缺失场景", self.start_batch_generate,
                attr_name="action_batch_generate")
        _action(generate_menu, "依次生成勾选节点正文",
                self._menu_batch_generate_checked,
                attr_name="action_batch_generate_checked")
        _action(generate_menu, "批量校验勾选节点概要",
                self._menu_batch_summary_checked,
                attr_name="action_batch_summary")
        _action(generate_menu, "批量修改勾选节点正文",
                self._menu_batch_modify_checked,
                attr_name="action_batch_modify")

        # ---------------- 工具 ----------------
        tool_menu = menubar.addMenu("工具")
        self.menus["工具"] = tool_menu
        _action(tool_menu, "🤖 Agent 控制台（命令 → 计划 → 执行）",
                self.open_agent_console, "Ctrl+Shift+A",
                attr_name="action_agent_console")
        _action(tool_menu, "📋 任务队列（动作 / 任务追踪）", self.open_task_queue_dialog,
                "Ctrl+T", attr_name="action_task_queue")
        tool_menu.addSeparator()
        _action(tool_menu, "小说校对（独立模块）", self.open_proofread_tool_entry,
                attr_name="action_proofread")
        _action(tool_menu, "时间线校对", self.open_timeline_dialog,
                attr_name="action_timeline")
        tool_menu.addSeparator()
        _action(tool_menu, "修复章节结构（补节后可新增场景）",
                self.auto_fix_outline_for_scene_addition,
                attr_name="action_repair_outline")
        _action(tool_menu, "一键全部合并待修改内容", self.batch_merge_all_pending,
                attr_name="action_merge_all_pending")
        tool_menu.addSeparator()
        _action(tool_menu, "从文本新建工作区", self.new_workspace_from_text,
                attr_name="action_new_workspace_from_text")
        _action(tool_menu, "输入点子生成世界观设定",
                lambda: self.open_world_building_dialog("init"),
                attr_name="action_world_build")
        _action(tool_menu, "生成三级大纲结构", self.open_outline_building_dialog,
                attr_name="action_outline_build")

        # ---------------- 设置 ----------------
        setting_menu = menubar.addMenu("设置")
        self.menus["设置"] = setting_menu
        _action(setting_menu, "系统配置（网络 / 通知 / 指令）", self.open_settings_dialog,
                "Ctrl+P", attr_name="action_settings")
        _action(setting_menu, "模型服务（提供商 / 模型 / 参数）",
                self.open_model_manager_dialog, "Ctrl+M",
                attr_name="action_model_service")
        self.menu_quick_model = setting_menu.addMenu("快速切换模型")
        self.menu_quick_model.aboutToShow.connect(self._populate_quick_model_menu)
        setting_menu.addSeparator()
        _action(setting_menu, "启用 NSFW 工作区配置",
                self._menu_toggle_workspace_nsfw, checkable=True,
                attr_name="action_nsfw_workspace")

        # ---------------- 帮助 ----------------
        help_menu = menubar.addMenu("帮助")
        self.menus["帮助"] = help_menu
        _action(help_menu, "使用说明（README）", self._open_readme,
                attr_name="action_help")
        _action(help_menu, "关于 AI 小说创作器", self._show_about,
                attr_name="action_about")

    # -- 菜单辅助动作 -------------------------------------------------
    def _populate_quick_model_menu(self):
        """动态生成“快速切换模型”子菜单。"""
        menu = self.menu_quick_model
        menu.clear()
        config = self.config or {}
        providers = config.get("providers", []) or []
        active_key = str(config.get("text_api_active_profile", "normal"))
        profiles = config.get("text_api_profiles") or {}
        profile = profiles.get(active_key) or {}
        current_provider = profile.get("provider_id")
        current_model = profile.get("model_id")

        header = menu.addAction(
            f"当前画像：{'NSFW' if active_key == 'nsfw' else '普通'}"
        )
        header.setEnabled(False)
        if not providers:
            empty_action = menu.addAction("（尚未配置模型，点击打开模型服务）")
            empty_action.triggered.connect(self.open_model_manager_dialog)
            return

        for provider in providers:
            models = provider.get("models", []) or []
            if not models:
                continue
            submenu = menu.addMenu(provider.get("name") or provider.get("id", ""))
            for model in models:
                display = model.get("display_name") or model.get("model", "")
                context = int(model.get("context_size", 0) or 0)
                action = submenu.addAction(f"{display}  ({context:,} ctx)")
                action.setCheckable(True)
                action.setChecked(
                    provider.get("id") == current_provider
                    and model.get("id") == current_model
                )
                action.triggered.connect(
                    lambda checked=False, p=provider.get("id", ""), m=model.get("id", ""): (
                        self.switch_active_model(p, m)
                    )
                )
        menu.addSeparator()
        manage_action = menu.addAction("管理模型服务…")
        manage_action.triggered.connect(self.open_model_manager_dialog)

    def _set_issue_panel_visible(self, checked: bool):
        if checked and not self.issue_panel_frame.isVisible():
            self._toggle_issue_panel()
        elif not checked and self.issue_panel_frame.isVisible():
            self._toggle_issue_panel()

    def reset_window_layout(self):
        """恢复默认分栏比例与面板可见性。"""
        for widget in (self.setting_tree, self.novel_tree):
            widget.setVisible(True)
        self.editor_splitter.setVisible(True)
        self.log_console.setVisible(True)
        self.main_splitter.setSizes([180, 430, 590])
        self.editor_splitter.setSizes([300, 700])
        for name in ("action_view_settings", "action_view_outline",
                     "action_view_editor", "action_view_log"):
            action = getattr(self, name, None)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(True)
                action.blockSignals(False)
        self.log_console.append("界面布局已重置。")

    def _menu_batch_generate_checked(self):
        nodes = self._collect_checked_novel_nodes()
        level3 = [(item, node) for item, node in nodes if get_item_level(item) == 3]
        if not level3:
            QMessageBox.information(
                self, "提示", "请先在大纲树中勾选至少一个 3 级场景节点。"
            )
            return
        self.start_batch_generate_checked_nodes(nodes)

    def _menu_batch_summary_checked(self):
        nodes = self._collect_checked_novel_nodes()
        level3 = [(item, node) for item, node in nodes if get_item_level(item) == 3]
        if not level3:
            QMessageBox.information(
                self, "提示", "请先在大纲树中勾选至少一个 3 级场景节点。"
            )
            return
        self.start_batch_regenerate_summaries_checked_nodes(nodes)

    def _menu_batch_modify_checked(self):
        nodes = self._collect_checked_novel_nodes()
        if not nodes:
            QMessageBox.information(
                self, "提示", "请先在大纲树中勾选要修改的节点。"
            )
            return
        self.open_batch_modify_request_dialog(nodes)

    def _sync_nsfw_menu_action(self, checked: bool):
        action = getattr(self, "action_nsfw_workspace", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(bool(checked))
            action.blockSignals(False)

    def _open_readme(self):
        readme = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")
        if os.path.exists(readme):
            QDesktopServices.openUrl(QUrl.fromLocalFile(readme))
        else:
            QMessageBox.information(self, "使用说明", "未找到 README.md。")

    def _show_about(self):
        QMessageBox.about(
            self,
            "关于 AI 小说创作器",
            "AI 小说创作器（novel-maker）\n\n"
            "PyQt6 桌面端 AI 辅助小说创作工具。\n"
            "支持提供商 → 模型两级配置、按模型计算 token 预算、"
            "动作/任务队列追踪。\n\n"
            "版本：1.1.0",
        )

    # ================= UI 刷新总调度 ================= #

    def _handle_drop_sync(self):
        """处理拖放动作完成后的数据同步与 UI 安全刷新"""
        current_id = None
        current_item = self.novel_tree.currentItem()
        if current_item and not current_item.text(0).startswith("+"):
            current_id = current_item.data(0, Qt.ItemDataRole.UserRole)

        # 1. 整理 UI：将可能被挤到上面的 "+" 按钮重新沉降到底部
        self._cleanup_tree_add_buttons()
        
        # 2. 反向同步：从已经排序好的完美 UI 树中提取数据，覆盖保存到 JSON 中
        self.sync_tree_data_from_ui()

        # 3. 重新渲染整棵树，确保每个父节点都补齐 "+" 占位按钮
        self._refresh_novel_tree()

        # 4. 尝试恢复拖拽前/后的当前选中项，避免刷新后焦点丢失
        if current_id:
            from ui.utils import find_item_by_data
            root_item = self.novel_tree.invisibleRootItem()
            item = find_item_by_data(root_item, current_id) if root_item else None
            if item:
                self.novel_tree.setCurrentItem(item)
                self.current_editing_item = item

    def undo_last_change(self):
        """回退到最近一次变更前的状态"""
        if not self.undo_stack:
            return

        # 弹出最近的变更
        last_change = self.undo_stack.pop()
        change_type = last_change.get('type')
        old_value = last_change.get('old_value')

        if change_type == 'summary':
            self.summary_editor.setText(old_value)
            if self.current_editing_node:
                self.current_editing_node['summary'] = old_value
        elif change_type == 'content':
            self.content_editor.setText(old_value)

        # 更新后退按钮状态
        self.btn_undo.setEnabled(len(self.undo_stack) > 0)
        self.log_console.append("已回退到上一次变更前的状态")

    def _save_to_undo_stack(self, change_type, old_value):
        """保存变更到回退缓冲区"""
        if not self.current_editing_node:
            return

        # 检查是否与上一次变更相同
        if self.undo_stack and self.undo_stack[-1].get('type') == change_type:
            return

        # 添加变更到缓冲区
        self.undo_stack.append({
            'type': change_type,
            'old_value': old_value
        })

        # 限制缓冲区大小
        if len(self.undo_stack) > self.max_undo_stack:
            self.undo_stack.pop(0)

        # 更新后退按钮状态
        self.btn_undo.setEnabled(True)

    def refresh_ui_from_workspace(self):
        """刷新整个 UI：分别委托给设定树和大纲树的渲染方法。"""
        if not self.workspace:
            return

        self.setting_tree.clear()
        self.novel_tree.clear()
        self.node_map.clear()
        
        # 清空当前选中节点状态和编辑器内容
        self.current_editing_node = None
        self.current_editing_item = None
        self.current_setting_path = None
        self.summary_editor.clear()
        self.summary_editor.setEnabled(False)
        self.summary_title_label.setText("节点概要 (Summary - 保存至系统数据):")
        self.summary_title_label.setToolTip("节点概要 (Summary - 保存至系统数据):")
        self.content_editor.clear()
        self.content_editor.setEnabled(False)
        self.word_count_label.setText("当前字数: 0")
        
        # 禁用相关按钮
        self.btn_generate.setEnabled(False)
        self.btn_rewrite.setEnabled(False)
        self.btn_regenerate_summary.setEnabled(False)
        self.btn_undo.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)

        # 渲染设定树（来自 SettingTreeMixin）
        self._refresh_setting_tree()

        # 渲染小说大纲树（来自 NovelTreeMixin）
        self._refresh_novel_tree()
        

    def on_novel_item_changed(self, item, column):
        """处理小说大纲树节点的勾选状态变化，实现 Windows 风格的勾选逻辑"""
        if column != 0:
            return
        
        # 使用标志位防止递归触发
        if hasattr(self, '_updating_check_state') and self._updating_check_state:
            return
        
        self._updating_check_state = True
        
        try:
            # 获取当前勾选状态
            current_state = item.checkState(0)

            # 递归处理子节点
            def update_children(parent_item, state):
                for i in range(parent_item.childCount()):
                    child = parent_item.child(i)
                    if not child.text(0).startswith("+"):
                        child.setCheckState(0, state)
                        update_children(child, state)

            # 处理子节点
            update_children(item, current_state)

            # 处理父节点
            def update_parent(parent_item):
                if not parent_item:
                    return

                checked_count = 0
                unchecked_count = 0
                total_count = 0

                for i in range(parent_item.childCount()):
                    child = parent_item.child(i)
                    if not child.text(0).startswith("+"):
                        total_count += 1
                        if child.checkState(0) == Qt.CheckState.Checked:
                            checked_count += 1
                        elif child.checkState(0) == Qt.CheckState.Unchecked:
                            unchecked_count += 1

                if total_count == 0:
                    return
                elif checked_count == total_count:
                    parent_item.setCheckState(0, Qt.CheckState.Checked)
                elif unchecked_count == total_count:
                    parent_item.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    parent_item.setCheckState(0, Qt.CheckState.PartiallyChecked)

                # 递归更新上层父节点
                update_parent(parent_item.parent())

            # 处理父节点
            update_parent(item.parent())
        finally:
            self._updating_check_state = False

    def select_all_nodes(self):
        """全选所有节点"""
        def select_all_recursive(item):
            if not item.text(0).startswith("+"):
                item.setCheckState(0, Qt.CheckState.Checked)
            for i in range(item.childCount()):
                select_all_recursive(item.child(i))

        root = self.novel_tree.invisibleRootItem()
        if root is None:
            return
        for i in range(root.childCount()):
            child = root.child(i)
            if child is not None:
                select_all_recursive(child)

    def select_none_nodes(self):
        """全不选所有节点"""
        def select_none_recursive(item):
            if not item.text(0).startswith("+"):
                item.setCheckState(0, Qt.CheckState.Unchecked)
            for i in range(item.childCount()):
                select_none_recursive(item.child(i))

        root = self.novel_tree.invisibleRootItem()
        if root is None:
            return
        for i in range(root.childCount()):
            child = root.child(i)
            if child is not None:
                select_none_recursive(child)

    def open_timeline_dialog(self):
        """打开时间线校对窗口"""
        if not self.outline_tree_data:
            # 如果没有大纲数据，显示提示
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "提示", "请先加载或创建工作区，以便获取小说大纲数据")
            return
        
        # 打开时间线校对窗口
        dialog = TimelineDialog(self.outline_tree_data, self, self.workspace)
        dialog.exec()

    def open_proofread_tool_entry(self):
        from PyQt6.QtWidgets import QMessageBox
        from ui.dialogs import ProofreadDialog

        workspace_path = ""
        if self.workspace:
            workspace_path = getattr(self.workspace, "workspace_path", "") or ""

        if not workspace_path:
            QMessageBox.information(self, "小说校对", "请先加载工作区，然后再使用小说校对。")
            return

        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return

        self.log_console.append("<b>已打开小说校对窗口。</b>")
        dialog = ProofreadDialog(
            self,
            workspace=self.workspace,
            llm_client=self.llm_client,
            config=self.config or {},
        )
        dialog.exec()

    def closeEvent(self, event: QCloseEvent):
        self._save_window_ui_state()
        super().closeEvent(event)

    def resizeEvent(self, event):
        self._schedule_window_ui_state_save()
        super().resizeEvent(event)

    def moveEvent(self, event):
        self._schedule_window_ui_state_save()
        super().moveEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            self._schedule_window_ui_state_save()
        super().changeEvent(event)

    def _schedule_window_ui_state_save(self, *args):
        if hasattr(self, "_ui_state_save_timer"):
            self._ui_state_save_timer.start(350)

    def _toggle_issue_panel(self):
        """展开/折叠 Issue 面板"""
        visible = self.issue_scroll_area.isVisible()
        if visible:
            self.issue_scroll_area.setVisible(False)
            self.issue_panel_frame.setFixedHeight(40)
            self.issue_toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        else:
            self.issue_scroll_area.setVisible(True)
            count = len(self._loaded_mobile_issues) if hasattr(self, '_loaded_mobile_issues') else 0
            rows = min(count, 6)
            self.issue_panel_frame.setFixedHeight(40 + rows * 110 + 12)
            self.issue_toggle_btn.setArrowType(Qt.ArrowType.DownArrow)
        action = getattr(self, "action_view_issue", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(self.issue_panel_frame.isVisible())
            action.blockSignals(False)

    def _menu_toggle_workspace_nsfw(self, checked: bool):
        if not self.workspace:
            QMessageBox.information(self, "提示", "请先加载工作区。")
            action = getattr(self, "action_nsfw_workspace", None)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(bool(self._workspace_is_nsfw))
                action.blockSignals(False)
            return
        self.cb_workspace_nsfw.setChecked(bool(checked))


if __name__ == '__main__':
    from ui.theme import get_dark_stylesheet
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(get_dark_stylesheet())
    window = NovelCreatorWindow()
    window.show()
    sys.exit(app.exec())
