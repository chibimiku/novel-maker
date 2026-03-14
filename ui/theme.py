"""
暗色主题样式表模块
提供精心调校的 QSS 暗色主题，适配 AI小说创作器的所有组件。
"""

# ===================== 调色板常量 =====================
# 背景层级（由深到浅）
BG_BASE      = "#1a1a2e"    # 最底层背景
BG_SURFACE   = "#16213e"    # 面板/卡片背景
BG_ELEVATED  = "#1f2b47"    # 编辑器/输入框背景
BG_HOVER     = "#2a3a5c"    # 悬停高亮
BG_SELECTED  = "#0f3460"    # 选中高亮

# 文字层级
TEXT_PRIMARY   = "#e0e0e0"  # 主要文字
TEXT_SECONDARY = "#a0a8b8"  # 次要文字/标签
TEXT_DISABLED  = "#5a6070"  # 禁用文字
TEXT_LINK      = "#64b5f6"  # 链接/可点击文字

# 强调色
ACCENT         = "#7c4dff"  # 主强调色（紫色）
ACCENT_HOVER   = "#9c7cff"  # 强调色悬停
ACCENT_PRESSED = "#5c2ddf"  # 强调色按下

# 功能色
SUCCESS  = "#66bb6a"
WARNING  = "#ffa726"
ERROR    = "#ef5350"
INFO     = "#42a5f5"

# 边框
BORDER       = "#2a3a5c"
BORDER_FOCUS = "#7c4dff"

# 按钮色
BTN_BG       = "#2a3a5c"
BTN_HOVER    = "#3a4a6c"
BTN_PRESSED  = "#1a2a4c"

# 节点状态专用色
NODE_NORMAL  = "#e0e0e0"   # 正常节点文字
NODE_MISSING = "#78909c"   # 缺失文件（柔灰蓝）
NODE_ERROR   = "#ef5350"   # 外部修改/异常
NODE_ADD_BTN = "#64b5f6"   # "+ 新增" 按钮

# ===================== QSS 样式表 =====================

DARK_THEME_QSS = f"""
/* ==================== 全局 ==================== */
QMainWindow, QDialog {{
    background-color: {BG_BASE};
    color: {TEXT_PRIMARY};
}}

QWidget {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}

/* ==================== 菜单栏 ==================== */
QMenuBar {{
    background-color: {BG_SURFACE};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BORDER};
    padding: 2px 0px;
}}

QMenuBar::item {{
    padding: 6px 12px;
    border-radius: 4px;
    margin: 1px 2px;
}}

QMenuBar::item:selected {{
    background-color: {BG_HOVER};
}}

QMenuBar::item:pressed {{
    background-color: {ACCENT};
}}

QMenu {{
    background-color: {BG_SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 28px 6px 12px;
    border-radius: 4px;
    margin: 1px 4px;
}}

QMenu::item:selected {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
}}

QMenu::separator {{
    height: 1px;
    background-color: {BORDER};
    margin: 4px 8px;
}}

/* ==================== 标签 ==================== */
QLabel {{
    color: {TEXT_SECONDARY};
    padding: 1px;
}}

/* ==================== 按钮 ==================== */
QPushButton {{
    background-color: {BTN_BG};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 500;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {BTN_HOVER};
    border-color: {ACCENT};
}}

QPushButton:pressed {{
    background-color: {BTN_PRESSED};
    border-color: {ACCENT_PRESSED};
}}

QPushButton:disabled {{
    background-color: {BG_SURFACE};
    color: {TEXT_DISABLED};
    border-color: {BORDER};
}}

/* 特殊按钮：批量生成（强调色） */
QPushButton#btn_batch_generate {{
    background-color: {ACCENT};
    color: white;
    border: none;
    font-weight: bold;
}}

QPushButton#btn_batch_generate:hover {{
    background-color: {ACCENT_HOVER};
}}

QPushButton#btn_batch_generate:pressed {{
    background-color: {ACCENT_PRESSED};
}}

/* ==================== 文本编辑器 ==================== */
QTextEdit, QTextBrowser {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px;
    selection-background-color: {BG_SELECTED};
    selection-color: white;
    font-size: 14px;
    line-height: 1.6;
}}

QTextEdit:focus {{
    border-color: {ACCENT};
}}

QTextEdit:disabled {{
    background-color: {BG_SURFACE};
    color: {TEXT_DISABLED};
}}

/* ==================== 树状视图 ==================== */
QTreeWidget {{
    background-color: {BG_SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    outline: 0px;
    font-size: 13px;
}}

QTreeWidget::item {{
    padding: 5px 4px;
    border-radius: 4px;
    margin: 1px 0px;
}}

QTreeWidget::item:hover {{
    background-color: {BG_HOVER};
}}

QTreeWidget::item:selected {{
    background-color: {BG_SELECTED};
    color: white;
}}

QTreeWidget::branch {{
    background-color: transparent;
}}

QTreeWidget::branch:has-siblings:!adjoins-item {{
    border-image: none;
}}

QTreeWidget::branch:has-siblings:adjoins-item {{
    border-image: none;
}}

QTreeWidget::branch:!has-children:!has-siblings:adjoins-item {{
    border-image: none;
}}

QHeaderView::section {{
    background-color: {BG_SURFACE};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 2px solid {ACCENT};
    padding: 6px 8px;
    font-weight: bold;
    font-size: 13px;
}}

/* ==================== 分割器 ==================== */
QSplitter::handle {{
    background-color: {BORDER};
    margin: 0px 2px;
}}

QSplitter::handle:horizontal {{
    width: 2px;
}}

QSplitter::handle:vertical {{
    height: 2px;
}}

QSplitter::handle:hover {{
    background-color: {ACCENT};
}}

/* ==================== 复选框 ==================== */
QCheckBox {{
    color: {TEXT_PRIMARY};
    spacing: 6px;
    padding: 2px;
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {BORDER};
    border-radius: 4px;
    background-color: {BG_ELEVATED};
}}

QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}

QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}

QCheckBox::indicator:checked:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}

/* ==================== 数字输入框 ==================== */
QSpinBox {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 22px;
}}

QSpinBox:focus {{
    border-color: {ACCENT};
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {BTN_BG};
    border: none;
    border-radius: 3px;
    width: 20px;
    margin: 1px;
}}

QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {BTN_HOVER};
}}

QSpinBox::up-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-bottom: 5px solid {TEXT_SECONDARY};
    width: 0px;
    height: 0px;
}}

QSpinBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid {TEXT_SECONDARY};
    width: 0px;
    height: 0px;
}}

/* ==================== 下拉框 ==================== */
QComboBox {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 22px;
}}

QComboBox:focus {{
    border-color: {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid {TEXT_SECONDARY};
    width: 0px;
    height: 0px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: {BG_SELECTED};
    selection-color: white;
    padding: 4px;
}}

/* ==================== 单行输入框 ==================== */
QLineEdit {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {BG_SELECTED};
    selection-color: white;
}}

QLineEdit:focus {{
    border-color: {ACCENT};
}}

QLineEdit:disabled {{
    background-color: {BG_SURFACE};
    color: {TEXT_DISABLED};
}}

/* ==================== 选项卡 ==================== */
QTabWidget::pane {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    top: -1px;
}}

QTabBar::tab {{
    background-color: {BG_BASE};
    color: {TEXT_SECONDARY};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 16px;
    margin-right: 2px;
    min-width: 100px;
}}

QTabBar::tab:selected {{
    background-color: {BG_SURFACE};
    color: {ACCENT};
    border-color: {BORDER};
    font-weight: bold;
}}

QTabBar::tab:hover:!selected {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
}}

/* ==================== 滚动条 ==================== */
QScrollBar:vertical {{
    background-color: transparent;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical {{
    background-color: {BORDER};
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {BG_HOVER};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal {{
    background-color: {BORDER};
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {BG_HOVER};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ==================== 消息框 ==================== */
QMessageBox {{
    background-color: {BG_BASE};
}}

QMessageBox QLabel {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}

/* ==================== 输入对话框 ==================== */
QInputDialog {{
    background-color: {BG_BASE};
}}

/* ==================== 状态栏 ==================== */
QStatusBar {{
    background-color: {BG_SURFACE};
    color: {TEXT_SECONDARY};
    border-top: 1px solid {BORDER};
}}

/* ==================== 工具提示 ==================== */
QToolTip {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
}}

/* ==================== 表单布局标签 ==================== */
QFormLayout QLabel {{
    color: {TEXT_SECONDARY};
    font-weight: 500;
}}
"""


def get_dark_stylesheet() -> str:
    """返回完整的暗色主题 QSS 样式表"""
    return DARK_THEME_QSS
