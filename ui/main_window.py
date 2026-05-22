"""
NovelCreatorWindow 主窗口 —— 仅保留 __init__ / init_ui / refresh_ui 编排逻辑。
具体业务方法已拆分至 ui.mixins 各子模块中。
"""
import sys
import os
from contextlib import contextmanager
from typing import Any

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTextEdit,
                             QPushButton, QSplitter, QMenuBar, QMenu, QTextBrowser,
                             QLabel, QCheckBox, QSpinBox, QAbstractItemView,
                             QProxyStyle, QStyle, QSizePolicy, QScrollArea, QFrame,
                             QToolButton)
from PyQt6.QtGui import (
    QKeySequence,
    QAction,
    QShortcut,
    QCloseEvent,
    QTextCharFormat,
    QPalette,
)
from PyQt6.QtCore import Qt, QTimer, QEvent

# 导入暗色主题配色常量
from ui.theme import TEXT_SECONDARY

# 导入时间线对话框
from ui.timeline_dialog import TimelineDialog

# 导入核心逻辑层组件
from core.llm_client import LLMClient
from core.workspace_manager import WorkspaceManager

# 导入 Mixin 子模块
from ui.mixins import (
    ConfigMixin,
    WorkspaceMixin,
    SettingTreeMixin,
    NovelTreeMixin,
    GenerationMixin,
    EditorMixin,
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


class CompactTreeStyle(QProxyStyle):
    """紧凑树样式：缩小复选框、展开箭头与缩进，提升可视空间。"""
    def __init__(self, base_style=None, indicator_size=12, indentation=14):
        super().__init__(base_style)
        self._indicator_size = indicator_size
        self._indentation = indentation

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (
            QStyle.PixelMetric.PM_IndicatorWidth,
            QStyle.PixelMetric.PM_IndicatorHeight,
            QStyle.PixelMetric.PM_SmallIconSize,
        ):
            return self._indicator_size
        if metric == QStyle.PixelMetric.PM_TreeViewIndentation:
            return self._indentation
        return super().pixelMetric(metric, option, widget)

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

        self.config = self._load_config()
        self.llm_client: LLMClient | None = LLMClient(self.config) if self.config else None

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
        file_menu = QMenu('文件', self)
        menubar.addMenu(file_menu)

        new_action = QAction('新建工作区', self)
        new_action.setShortcut(QKeySequence("Ctrl+N"))
        file_menu.addAction(new_action)
        new_action.triggered.connect(self.new_workspace)

        load_action = QAction('加载工作区', self)
        load_action.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addAction(load_action)
        load_action.triggered.connect(self.load_workspace)

        reload_action = QAction('重载工作区', self)
        reload_action.setShortcut(QKeySequence("Ctrl+R"))
        file_menu.addAction(reload_action)
        reload_action.triggered.connect(self.reload_workspace)

        save_action = QAction('保存全部', self)
        file_menu.addAction(save_action)
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self.save_all)
        
        export_html_action = QAction('🌐 导出为可阅读网页 (HTML)', self)
        file_menu.addAction(export_html_action)
        export_html_action.setShortcut(QKeySequence("Ctrl+E"))
        export_html_action.triggered.connect(self.export_to_html)
        
        setting_menu = QMenu('设置', self)
        menubar.addMenu(setting_menu)
        settings_action = QAction('系统配置 (API/模型)', self)
        setting_menu.addAction(settings_action)
        settings_action.setShortcut(QKeySequence("Ctrl+P"))
        settings_action.triggered.connect(self.open_settings_dialog)

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

        # 添加工具菜单
        tool_menu = QMenu('工具', self)
        menubar.addMenu(tool_menu)
        import_text_action = QAction('从文本新建工作区', self)
        tool_menu.addAction(import_text_action)
        import_text_action.triggered.connect(self.new_workspace_from_text)
        repair_outline_action = QAction('修复章节结构(补节后可新增场景)', self)
        tool_menu.addAction(repair_outline_action)
        repair_outline_action.triggered.connect(self.auto_fix_outline_for_scene_addition)
        debug_log_action = QAction('显示 Debug Log', self)
        tool_menu.addAction(debug_log_action)
        debug_log_action.setCheckable(True)
        debug_log_action.setChecked(self._debug_log_enabled)
        debug_log_action.toggled.connect(self.set_debug_log_enabled)
        timeline_action = QAction('时间线校对', self)
        tool_menu.addAction(timeline_action)
        timeline_action.triggered.connect(self.open_timeline_dialog)
        proofread_action = QAction('小说校对（独立模块）', self)
        tool_menu.addAction(proofread_action)
        proofread_action.triggered.connect(self.open_proofread_tool_entry)

        tool_menu.addSeparator()
        batch_merge_all_action = QAction('一键全部合并待修改内容', self)
        tool_menu.addAction(batch_merge_all_action)
        batch_merge_all_action.triggered.connect(self.batch_merge_all_pending)

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
        self.novel_tree.setStyle(
            CompactTreeStyle(self.novel_tree.style(), indicator_size=12, indentation=14)
        )
        self.novel_tree.setStyleSheet("QTreeWidget { font-size: 12px; }")

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


if __name__ == '__main__':
    from ui.theme import get_dark_stylesheet
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(get_dark_stylesheet())
    window = NovelCreatorWindow()
    window.show()
    sys.exit(app.exec())
