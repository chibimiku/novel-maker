"""
自定义对话框模块
包含项目中使用的各种自定义 QDialog 子类。
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QTextEdit, QLineEdit, QPushButton)


class IdeaInputDialog(QDialog):
    """适配深色主题的自定义多行输入框"""
    def __init__(self, parent, title, hint):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 400)
        
        layout = QVBoxLayout(self)
        
        hint_label = QLabel(hint)
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("在这里输入您的设定概念或核心点子...")
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")
        
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def get_text(self):
        return self.text_edit.toPlainText()


class RenameNodeDialog(QDialog):
    """带清空按钮的节点重命名弹窗"""
    def __init__(self, parent, title, label_text, default_text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(350, 120)
        
        layout = QVBoxLayout(self)
        
        label = QLabel(label_text)
        layout.addWidget(label)
        
        # 使用 QLineEdit，完美支持 Ctrl+A 全选
        self.line_edit = QLineEdit(default_text)
        # 开启右侧自带的 'X' 清空按钮
        self.line_edit.setClearButtonEnabled(True) 
        layout.addWidget(self.line_edit)
        
        # 自动全选现有文本，方便直接输入覆盖
        self.line_edit.selectAll()
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")
        
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def get_text(self):
        return self.line_edit.text()
