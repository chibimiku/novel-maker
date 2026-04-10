"""
从文本导入工作区对话框模块
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton,
                             QFileDialog, QListWidget, QSpinBox, QListWidgetItem)


class ImportTextWorkspaceDialog(QDialog):
    """从文本新建工作区对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从文本新建工作区")
        self.resize(600, 500)
        self.selected_files = []
        
        layout = QVBoxLayout(self)
        
        # 步骤1: 选择文本文件
        step1_label = QLabel("步骤1: 选择文本文件（支持多选）")
        layout.addWidget(step1_label)
        
        file_layout = QHBoxLayout()
        self.file_list = QListWidget()
        file_layout.addWidget(self.file_list, stretch=4)
        
        file_btn_layout = QVBoxLayout()
        self.add_file_btn = QPushButton("添加文件")
        self.remove_file_btn = QPushButton("移除选中")
        self.remove_file_btn.setEnabled(False)
        file_btn_layout.addWidget(self.add_file_btn)
        file_btn_layout.addWidget(self.remove_file_btn)
        file_btn_layout.addStretch()
        file_layout.addLayout(file_btn_layout, stretch=1)
        layout.addLayout(file_layout)
        
        # 步骤2: 设置场景字数
        step2_label = QLabel("步骤2: 设置每个场景（3级节点）的字数")
        layout.addWidget(step2_label)
        
        word_count_layout = QHBoxLayout()
        word_count_layout.addWidget(QLabel("目标字数:"))
        self.word_count_spin = QSpinBox()
        self.word_count_spin.setRange(500, 20000)
        self.word_count_spin.setSingleStep(500)
        self.word_count_spin.setValue(3000)
        word_count_layout.addWidget(self.word_count_spin)
        word_count_layout.addStretch()
        layout.addLayout(word_count_layout)
        
        # 步骤3: 选择工作区目录
        step3_label = QLabel("步骤3: 选择工作区保存位置")
        layout.addWidget(step3_label)
        
        workspace_layout = QHBoxLayout()
        self.workspace_path_edit = QLineEdit()
        self.workspace_path_edit.setPlaceholderText("选择空文件夹作为工作区")
        self.workspace_path_edit.setReadOnly(True)
        workspace_layout.addWidget(self.workspace_path_edit, stretch=4)
        self.browse_workspace_btn = QPushButton("浏览...")
        workspace_layout.addWidget(self.browse_workspace_btn, stretch=1)
        layout.addLayout(workspace_layout)
        
        # 按钮
        layout.addStretch()
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.ok_btn = QPushButton("开始导入")
        self.ok_btn.setEnabled(False)
        self.cancel_btn = QPushButton("取消")
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
        layout.addLayout(btn_layout)
        
        # 连接信号
        self.add_file_btn.clicked.connect(self.add_files)
        self.remove_file_btn.clicked.connect(self.remove_files)
        self.file_list.itemSelectionChanged.connect(self.on_file_selection_changed)
        self.browse_workspace_btn.clicked.connect(self.browse_workspace)
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
    
    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文本文件", "", 
            "文本文件 (*.txt *.md);;所有文件 (*.*)"
        )
        if files:
            for file_path in files:
                if file_path not in self.selected_files:
                    self.selected_files.append(file_path)
                    item = QListWidgetItem(file_path)
                    self.file_list.addItem(item)
            self.update_ok_button_state()
    
    def remove_files(self):
        selected_items = self.file_list.selectedItems()
        for item in selected_items:
            file_path = item.text()
            if file_path in self.selected_files:
                self.selected_files.remove(file_path)
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
        self.update_ok_button_state()
    
    def on_file_selection_changed(self):
        self.remove_file_btn.setEnabled(len(self.file_list.selectedItems()) > 0)
    
    def browse_workspace(self):
        folder_path = QFileDialog.getExistingDirectory(
            self, "选择空文件夹作为工作区"
        )
        if folder_path:
            self.workspace_path_edit.setText(folder_path)
            self.update_ok_button_state()
    
    def update_ok_button_state(self):
        self.ok_btn.setEnabled(
            len(self.selected_files) > 0 and 
            len(self.workspace_path_edit.text().strip()) > 0
        )
    
    def get_selected_files(self):
        return self.selected_files
    
    def get_word_count(self):
        return self.word_count_spin.value()
    
    def get_workspace_path(self):
        return self.workspace_path_edit.text().strip()
