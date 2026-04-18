"""
NovelCreatorWindow 主窗口 —— 仅保留 __init__ / init_ui / refresh_ui 编排逻辑。
具体业务方法已拆分至 ui.mixins 各子模块中。
"""
import sys
import os

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTextEdit,
                             QPushButton, QSplitter, QMenuBar, QMenu, QTextBrowser,
                             QLabel, QCheckBox, QSpinBox, QAbstractItemView,
                             QProxyStyle, QStyle, QSizePolicy)
from PyQt6.QtGui import QKeySequence, QAction, QShortcut, QCloseEvent
from PyQt6.QtCore import Qt, QTimer, QEvent

# 导入暗色主题配色常量
from ui.theme import TEXT_SECONDARY

# 导入时间线对话框
from ui.timeline_dialog import TimelineDialog

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

    def dragMoveEvent(self, event):
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
            if not dragged_item.child(i).text(0).startswith("+"):
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

    def dropEvent(self, event):
        target_item = self.itemAt(event.position().toPoint())
        dragged_items = self.selectedItems()
        if not dragged_items:
            event.ignore()
            return

        dragged_item = self._dragged_item if self._dragged_item else dragged_items[0]
        drop_pos = self.dropIndicatorPosition()

        has_real_children = False
        for i in range(dragged_item.childCount()):
            if not dragged_item.child(i).text(0).startswith("+"):
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

    def _rescue_add_button_children(self, parent_item=None):
        """
        自愈机制：倒序遍历节点。如果发现 '+' 按钮被 Qt 强行塞了子节点，
        就把它提取出来，放到 '+' 按钮的前面（成为正常的同级节点）。
        """
        target = parent_item if parent_item else self.invisibleRootItem()
        # 必须倒序遍历，因为我们要执行插入操作，正序会打乱索引
        for i in range(target.childCount() - 1, -1, -1):
            child = target.child(i)
            if child.text(0).startswith("+"):
                # 如果发现加号按钮有子节点（非法状态）
                while child.childCount() > 0:
                    # 将其提取出来
                    rescued_node = child.takeChild(0)
                    # 插入到 target 中，位置在当前加号按钮的正前方
                    target.insertChild(i, rescued_node)
                    # 递归检查刚被提取出来的节点
                    self._rescue_add_button_children(rescued_node)
            else:
                self._rescue_add_button_children(child)


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
        self._is_batch_generation_context = False
        
        # 回退缓冲区相关变量
        self.undo_stack = []              # 回退缓冲区，最多保存20次变更
        self.max_undo_stack = 20          # 最大回退次数
        self.current_node_original_summary = ""  # 当前节点的原始概要
        self.current_node_original_content = ""  # 当前节点的原始内容
        
        # 拖拽前的树数据快照
        self._pre_drag_tree_snapshot = None
        # “+新增...”调试日志开关（持久化）
        self._debug_add_button = self._get_debug_add_button_enabled()
        # “修改内容”时是否附加写作风格（持久化）
        self._append_writing_style_on_modify = self._get_modify_style_option()

        self.config = self._load_config()
        self.llm_client = LLMClient(self.config) if self.config else None

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

        save_action = file_menu.addAction('保存全部')
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self.save_all)
        
        export_html_action = file_menu.addAction('🌐 导出为可阅读网页 (HTML)')
        export_html_action.setShortcut(QKeySequence("Ctrl+E"))
        export_html_action.triggered.connect(self.export_to_html)
        
        setting_menu = menubar.addMenu('设置')
        settings_action = setting_menu.addAction('系统配置 (API/模型)')
        settings_action.setShortcut(QKeySequence("Ctrl+P"))
        settings_action.triggered.connect(self.open_settings_dialog)

        # 添加工具菜单
        tool_menu = menubar.addMenu('工具')
        import_text_action = tool_menu.addAction('从文本新建工作区')
        import_text_action.triggered.connect(self.new_workspace_from_text)
        repair_outline_action = tool_menu.addAction('修复章节结构(补节后可新增场景)')
        repair_outline_action.triggered.connect(self.auto_fix_outline_for_scene_addition)
        debug_add_btn_action = tool_menu.addAction('显示AddBtn调试日志')
        debug_add_btn_action.setCheckable(True)
        debug_add_btn_action.setChecked(self._debug_add_button)
        debug_add_btn_action.toggled.connect(self.set_add_button_debug_enabled)
        timeline_action = tool_menu.addAction('时间线校对')
        timeline_action.triggered.connect(self.open_timeline_dialog)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(True)
        splitter.setCollapsible(0, True)
        splitter.setCollapsible(1, True)
        splitter.setCollapsible(2, True)
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
        splitter.setSizes([180, 430, 590])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.splitterMoved.connect(self._schedule_window_ui_state_save)
        editor_splitter.splitterMoved.connect(self._schedule_window_ui_state_save)
        main_layout.addWidget(splitter, stretch=4)

        self.log_console = QTextBrowser()
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
            item = find_item_by_data(self.novel_tree.invisibleRootItem(), current_id)
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
        for i in range(root.childCount()):
            select_all_recursive(root.child(i))

    def select_none_nodes(self):
        """全不选所有节点"""
        def select_none_recursive(item):
            if not item.text(0).startswith("+"):
                item.setCheckState(0, Qt.CheckState.Unchecked)
            for i in range(item.childCount()):
                select_none_recursive(item.child(i))

        root = self.novel_tree.invisibleRootItem()
        for i in range(root.childCount()):
            select_none_recursive(root.child(i))

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


if __name__ == '__main__':
    from ui.theme import get_dark_stylesheet
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(get_dark_stylesheet())
    window = NovelCreatorWindow()
    window.show()
    sys.exit(app.exec())
