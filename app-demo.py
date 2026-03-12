import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTextEdit, 
                             QPushButton, QSplitter, QMenuBar, QMenu, QTextBrowser,
                             QLabel)
from PyQt6.QtCore import Qt

class NovelCreatorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI小说创作器")
        self.resize(1200, 800)
        
        self.init_ui()

    def init_ui(self):
        # 1. 顶部菜单栏
        menubar = self.menuBar()
        file_menu = menubar.addMenu('文件')
        file_menu.addAction('打开工作区')
        file_menu.addAction('保存全部')
        
        setting_menu = menubar.addMenu('设置')
        setting_menu.addAction('系统配置 (API/模型)')
        
        about_menu = menubar.addMenu('关于')

        # 主部件容器
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # 2. 中间三栏工作区 (使用QSplitter支持拖拽缩放)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # (1) 最左边：设定目录树
        self.setting_tree = QTreeWidget()
        self.setting_tree.setHeaderLabel("世界观设定 (勾选参与上下文)")
        self._init_setting_tree()
        splitter.addWidget(self.setting_tree)

        # (2) 第二个：小说目录树
        self.novel_tree = QTreeWidget()
        self.novel_tree.setHeaderLabel("小说大纲结构")
        self._init_novel_tree()
        splitter.addWidget(self.novel_tree)

        # (3) 最右边：详情与编辑区
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.addWidget(QLabel("节点内容编辑器 (支持Markdown):"))
        self.editor = QTextEdit()
        detail_layout.addWidget(self.editor)
        
        btn_layout = QHBoxLayout()
        btn_generate = QPushButton("🔄 结合上下文生成内容")
        btn_save = QPushButton("💾 保存当前节点")
        btn_layout.addWidget(btn_generate)
        btn_layout.addWidget(btn_save)
        detail_layout.addLayout(btn_layout)
        
        splitter.addWidget(detail_widget)

        # 设置三栏的初始宽度比例 (例如 2:3:5)
        splitter.setSizes([200, 300, 700])
        main_layout.addWidget(splitter, stretch=4)

        # 3. 下方：Log 显示区
        self.log_console = QTextBrowser()
        self.log_console.setFixedHeight(150)
        self.log_console.append("系统初始化完成。等待加载工作区...")
        main_layout.addWidget(self.log_console, stretch=1)

    def _init_setting_tree(self):
        """初始化设定树结构"""
        categories = ["公共设定", "人物设定", "名词设定", "地点设定", "其他设定"]
        for cat in categories:
            item = QTreeWidgetItem(self.setting_tree, [cat])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            
            # 模拟添加按钮节点
            add_btn = QTreeWidgetItem(item, ["+ 新增设定..."])
            add_btn.setForeground(0, Qt.GlobalColor.blue)

    def _init_novel_tree(self):
        """初始化小说目录树结构"""
        chapter1 = QTreeWidgetItem(self.novel_tree, ["第一章：初入异世"])
        section1 = QTreeWidgetItem(chapter1, ["第一节：迷雾森林"])
        
        scene1 = QTreeWidgetItem(section1, ["场景1：苏醒"])
        # 模拟文件不存在时的灰色显示
        scene2 = QTreeWidgetItem(section1, ["场景2：遭遇魔兽 (文件缺失)"])
        scene2.setForeground(0, Qt.GlobalColor.gray) 
        
        QTreeWidgetItem(section1, ["+ 新增场景..."])
        QTreeWidgetItem(chapter1, ["+ 新增节..."])
        QTreeWidgetItem(self.novel_tree, ["+ 新增章..."])
        
        self.novel_tree.expandAll()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = NovelCreatorWindow()
    window.show()
    sys.exit(app.exec())