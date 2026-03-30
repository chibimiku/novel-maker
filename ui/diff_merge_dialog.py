"""
DiffMergeDialog - 文本差异对比与合并对话框（增强版）
支持逐差异点选择合并，类似Beyond Compare
"""
from __future__ import annotations

import os
import json
from typing import TYPE_CHECKING, List, Tuple, Dict, Any

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QPalette, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
    QSplitter,
    QMessageBox,
    QWidget,
    QScrollArea,
    QFrame,
    QToolButton,
    QCheckBox,
    QGridLayout,
)

from diff_match_patch import diff_match_patch

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class DiffSegment:
    """表示文本的一段差异片段"""
    def __init__(self, segment_type: str, text: str):
        self.segment_type = segment_type  # "equal", "delete", "insert"
        self.text = text


class DiffLine:
    """表示一行差异数据"""
    def __init__(self, line_type: str, original_line: str = "", modified_line: str = "", diff_index: int = -1):
        self.line_type = line_type  # "equal", "delete", "insert", "replace"
        self.original_line = original_line
        self.modified_line = modified_line
        self.diff_index = diff_index
        self.selected = True  # 默认选中
        self.original_segments: List[DiffSegment] = []  # 原文细粒度差异
        self.modified_segments: List[DiffSegment] = []  # 修改版细粒度差异


class DiffLineWidget(QWidget):
    """单行差异显示控件"""
    selection_changed = pyqtSignal(int, bool)  # diff_index, selected

    def __init__(self, diff_line: DiffLine, parent=None):
        super().__init__(parent)
        self.diff_line = diff_line
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 复选框区域 - 使用theme默认样式
        if self.diff_line.line_type in ["insert", "delete", "replace"]:
            self.checkbox = QCheckBox()
            self.checkbox.setChecked(self.diff_line.selected)
            self.checkbox.stateChanged.connect(self.on_selection_changed)
            layout.addWidget(self.checkbox)
        else:
            layout.addWidget(QLabel("    "))

        # 左侧原文 - 使用QTextEdit支持富文本
        self.left_text = QTextEdit()
        self.left_text.setReadOnly(True)
        self.left_text.setFont(QFont("Consolas", 10))
        self.left_text.setMaximumHeight(80)
        self.left_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        if self.diff_line.line_type == "delete":
            self.left_text.setStyleSheet("background-color: #3d2020; padding: 4px 8px; border-radius: 4px;")
        elif self.diff_line.line_type == "replace":
            self.left_text.setStyleSheet("background-color: #3d3020; padding: 4px 8px; border-radius: 4px;")
        else:
            self.left_text.setStyleSheet("background-color: transparent; padding: 4px 8px;")
        
        self._render_text_with_segments(self.left_text, self.diff_line.original_segments, "original")
        self._adjust_textedit_height(self.left_text)
        layout.addWidget(self.left_text, stretch=45)

        # 中间箭头
        arrow_label = QLabel()
        if self.diff_line.line_type == "insert":
            arrow_label.setText(" → ")
            arrow_label.setStyleSheet("color: #66bb6a; font-weight: bold; font-size: 16px;")
        elif self.diff_line.line_type == "delete":
            arrow_label.setText(" ← ")
            arrow_label.setStyleSheet("color: #ef5350; font-weight: bold; font-size: 16px;")
        elif self.diff_line.line_type == "replace":
            arrow_label.setText(" ⇄ ")
            arrow_label.setStyleSheet("color: #ffa726; font-weight: bold; font-size: 16px;")
        else:
            arrow_label.setText("   ")
        arrow_label.setFont(QFont("Arial", 14))
        layout.addWidget(arrow_label)

        # 右侧修改版 - 使用QTextEdit支持富文本
        self.right_text = QTextEdit()
        self.right_text.setReadOnly(True)
        self.right_text.setFont(QFont("Consolas", 10))
        self.right_text.setMaximumHeight(80)
        self.right_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.right_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        if self.diff_line.line_type == "insert":
            self.right_text.setStyleSheet("background-color: #203d20; padding: 4px 8px; border-radius: 4px;")
        elif self.diff_line.line_type == "replace":
            self.right_text.setStyleSheet("background-color: #203d30; padding: 4px 8px; border-radius: 4px;")
        else:
            self.right_text.setStyleSheet("background-color: transparent; padding: 4px 8px;")
        
        self._render_text_with_segments(self.right_text, self.diff_line.modified_segments, "modified")
        self._adjust_textedit_height(self.right_text)
        layout.addWidget(self.right_text, stretch=45)

        self.setLayout(layout)

    def _render_text_with_segments(self, text_edit: QTextEdit, segments: List[DiffSegment], side: str):
        """使用细粒度差异渲染文本"""
        cursor = text_edit.textCursor()
        
        for segment in segments:
            char_format = QTextCharFormat()
            
            if segment.segment_type == "delete":
                char_format.setForeground(QColor("#ff6b6b"))
                char_format.setBackground(QColor("#2d1010"))
            elif segment.segment_type == "insert":
                char_format.setForeground(QColor("#66bb6a"))
                char_format.setBackground(QColor("#102d10"))
            else:
                char_format.setForeground(QColor("#e0e0e0"))
            
            cursor.setCharFormat(char_format)
            cursor.insertText(segment.text)
        
        text_edit.setTextCursor(cursor)

    def _adjust_textedit_height(self, text_edit: QTextEdit):
        """根据内容自动调整QTextEdit高度"""
        doc = text_edit.document()
        doc.setTextWidth(text_edit.viewport().width())
        height = doc.size().height() + 16
        text_edit.setMaximumHeight(int(height))
        text_edit.setMinimumHeight(int(height))

    def on_selection_changed(self, state):
        selected = state == Qt.CheckState.Checked.value
        self.diff_line.selected = selected
        self.selection_changed.emit(self.diff_line.diff_index, selected)


class AdvancedDiffMergeDialog(QDialog):
    """高级差异对比与合并对话框 - 逐差异点选择"""

    def __init__(self, parent: NovelCreatorWindow, node_id: str, original_text: str, modified_text: str, node_title: str):
        super().__init__(parent)
        self.main_window = parent
        self.node_id = node_id
        self.original_text = original_text
        self.modified_text = modified_text
        self.node_title = node_title
        self.result_text = modified_text

        self.dmp = diff_match_patch()
        self.diff_lines: List[DiffLine] = []
        
        self.init_ui()
        self.parse_and_render_diff()

    def init_ui(self):
        self.setWindowTitle(f"合并修改 - {self.node_title}")
        
        # 先设置一个大尺寸，然后最大化
        self.resize(1600, 1000)
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # 顶部标题和工具栏
        top_layout = QHBoxLayout()
        
        title_label = QLabel(f"节点: {self.node_title}")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        top_layout.addWidget(title_label)
        
        top_layout.addStretch()

        # 全部选择按钮
        self.btn_select_all = QPushButton("✅ 全选差异")
        self.btn_select_all.setMinimumHeight(36)
        self.btn_select_all.clicked.connect(self.select_all)
        top_layout.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("❌ 取消全选")
        self.btn_deselect_all.setMinimumHeight(36)
        self.btn_deselect_all.clicked.connect(self.deselect_all)
        top_layout.addWidget(self.btn_deselect_all)

        main_layout.addLayout(top_layout)

        # 图例
        legend_layout = QHBoxLayout()
        legend_layout.addWidget(QLabel("图例:"))
        
        del_label = QLabel("删除")
        del_label.setStyleSheet("background-color: #3d2020; color: #ff6b6b; padding: 4px 12px; border-radius: 4px;")
        legend_layout.addWidget(del_label)
        
        ins_label = QLabel("插入")
        ins_label.setStyleSheet("background-color: #203d20; color: #66bb6a; padding: 4px 12px; border-radius: 4px;")
        legend_layout.addWidget(ins_label)
        
        rep_label = QLabel("替换")
        rep_label.setStyleSheet("background-color: #3d3020; color: #ffa726; padding: 4px 12px; border-radius: 4px;")
        legend_layout.addWidget(rep_label)
        
        legend_layout.addStretch()
        main_layout.addLayout(legend_layout)

        # 差异列表滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        self.diff_container = QWidget()
        self.diff_layout = QVBoxLayout()
        self.diff_layout.setContentsMargins(8, 8, 8, 8)
        self.diff_layout.setSpacing(6)
        self.diff_container.setLayout(self.diff_layout)
        
        scroll.setWidget(self.diff_container)
        main_layout.addWidget(scroll, stretch=1)

        # 底部预览区域
        preview_label = QLabel("📄 合并结果预览:")
        preview_font = QFont()
        preview_font.setBold(True)
        preview_font.setPointSize(12)
        preview_label.setFont(preview_font)
        main_layout.addWidget(preview_label)

        self.preview_editor = QTextEdit()
        self.preview_editor.setReadOnly(True)
        self.preview_editor.setFont(QFont("Consolas", 11))
        self.preview_editor.setMaximumHeight(280)
        main_layout.addWidget(self.preview_editor)

        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_discard = QPushButton("❌ 丢弃所有修改")
        self.btn_discard.setMinimumHeight(40)
        self.btn_discard.clicked.connect(self.discard_changes)
        btn_layout.addWidget(self.btn_discard)

        self.btn_restore = QPushButton("↩️ 重置选择")
        self.btn_restore.setMinimumHeight(40)
        self.btn_restore.clicked.connect(self.reset_selection)
        btn_layout.addWidget(self.btn_restore)

        self.btn_save = QPushButton("💾 合并保存选中项")
        self.btn_save.setStyleSheet("background-color: #7c4dff; color: white; font-weight: bold; padding: 12px 24px; font-size: 12px;")
        self.btn_save.setMinimumHeight(40)
        self.btn_save.clicked.connect(self.save_merge)
        btn_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setMinimumHeight(40)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        main_layout.addLayout(btn_layout)

        self.setLayout(main_layout)
        
        # 最后再最大化，确保样式应用后显示
        self.showMaximized()

    def parse_and_render_diff(self):
        """解析并渲染差异"""
        # 使用diff-match-patch计算差异
        diffs = self.dmp.diff_main(self.original_text, self.modified_text)
        self.dmp.diff_cleanupSemantic(diffs)
        
        # 转换为行级差异，包含细粒度信息
        self.diff_lines = self._convert_to_line_diffs_with_granularity(diffs)
        
        # 渲染
        self._render_diff_lines()
        self._update_preview()

    def _convert_to_line_diffs_with_granularity(self, diffs) -> List[DiffLine]:
        """将diff-match-patch的结果转换为行级差异，保留细粒度信息"""
        result: List[DiffLine] = []
        
        # 将diffs按行分割
        original_buffer = []
        modified_buffer = []
        original_segments_buffer = []
        modified_segments_buffer = []
        
        for op, text in diffs:
            # 按行分割文本
            lines = text.splitlines(True)
            
            for i, line in enumerate(lines):
                has_newline = line.endswith('\n')
                clean_line = line.rstrip('\n')
                
                if op == -1:  # 删除
                    original_buffer.append(clean_line)
                    original_segments_buffer.append(DiffSegment("delete", clean_line))
                elif op == 1:  # 插入
                    modified_buffer.append(clean_line)
                    modified_segments_buffer.append(DiffSegment("insert", clean_line))
                else:  # 相等
                    original_buffer.append(clean_line)
                    modified_buffer.append(clean_line)
                    original_segments_buffer.append(DiffSegment("equal", clean_line))
                    modified_segments_buffer.append(DiffSegment("equal", clean_line))
                
                # 如果有换行，或者是最后一行，创建DiffLine
                if has_newline or i == len(lines) - 1:
                    orig_line = ''.join(original_buffer)
                    mod_line = ''.join(modified_buffer)
                    
                    if orig_line == mod_line:
                        diff_line = DiffLine("equal", orig_line, mod_line, len(result))
                        diff_line.original_segments = original_segments_buffer.copy()
                        diff_line.modified_segments = modified_segments_buffer.copy()
                    elif not orig_line:
                        diff_line = DiffLine("insert", "", mod_line, len(result))
                        diff_line.modified_segments = modified_segments_buffer.copy()
                    elif not mod_line:
                        diff_line = DiffLine("delete", orig_line, "", len(result))
                        diff_line.original_segments = original_segments_buffer.copy()
                    else:
                        diff_line = DiffLine("replace", orig_line, mod_line, len(result))
                        diff_line.original_segments = original_segments_buffer.copy()
                        diff_line.modified_segments = modified_segments_buffer.copy()
                    
                    result.append(diff_line)
                    
                    # 清空缓冲区
                    original_buffer = []
                    modified_buffer = []
                    original_segments_buffer = []
                    modified_segments_buffer = []
        
        # 过滤掉无意义的空行差异
        filtered_result = []
        for diff_line in result:
            # 检查是否是无意义的空行差异
            # 情况: 替换类型，但两边都是空行或纯空白（比如只是换行符差异）
            if diff_line.line_type == "replace":
                orig_stripped = diff_line.original_line.strip()
                mod_stripped = diff_line.modified_line.strip()
                if not orig_stripped and not mod_stripped:
                    # 两边都是空行，转换为equal类型，不显示为差异
                    diff_line.line_type = "equal"
                    filtered_result.append(diff_line)
                    continue
            # 其他情况（正常的insert、delete、equal、有实际内容的replace）都保留
            filtered_result.append(diff_line)
        
        return filtered_result

    def _render_diff_lines(self):
        """渲染差异行"""
        # 清空现有内容
        while self.diff_layout.count():
            child = self.diff_layout.takeAt(0)
            if child is not None:
                widget = child.widget()
                if widget is not None:
                    widget.deleteLater()
        # 添加差异行
        for diff_line in self.diff_lines:
            widget = DiffLineWidget(diff_line)
            widget.selection_changed.connect(self.on_line_selection_changed)
            self.diff_layout.addWidget(widget)
        
        self.diff_layout.addStretch()

    def on_line_selection_changed(self, diff_index, selected):
        """行选择变化时更新预览"""
        self._update_preview()

    def _update_preview(self):
        """更新合并结果预览"""
        result = []
        for diff_line in self.diff_lines:
            if diff_line.line_type == "equal":
                result.append(diff_line.original_line)
            elif diff_line.line_type == "insert":
                if diff_line.selected:
                    result.append(diff_line.modified_line)
            elif diff_line.line_type == "delete":
                if not diff_line.selected:
                    result.append(diff_line.original_line)
            elif diff_line.line_type == "replace":
                if diff_line.selected:
                    result.append(diff_line.modified_line)
                else:
                    result.append(diff_line.original_line)
        
        self.result_text = "\n".join(result)
        self.preview_editor.setPlainText(self.result_text)

    def select_all(self):
        """全选所有差异"""
        for diff_line in self.diff_lines:
            diff_line.selected = True
        self._render_diff_lines()
        self._update_preview()

    def deselect_all(self):
        """取消全选"""
        for diff_line in self.diff_lines:
            diff_line.selected = False
        self._render_diff_lines()
        self._update_preview()

    def reset_selection(self):
        """重置选择到默认状态（全选）"""
        self.select_all()

    def discard_changes(self):
        """丢弃所有修改"""
        reply = QMessageBox.question(
            self,
            "确认丢弃",
            "确定要丢弃本次LLM生成的所有修改吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.result_text = self.original_text
            self.accept()

    def save_merge(self):
        """保存合并结果"""
        self.accept()

    def get_result(self) -> str:
        """获取最终结果"""
        return self.result_text


# 保持向后兼容的别名
DiffMergeDialog = AdvancedDiffMergeDialog


class PendingModifyManager:
    """待合并修改管理器 - 负责持久化存储LLM生成的修改结果"""

    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.pending_dir = os.path.join(workspace_path, "系统数据", "pending_modifies")
        os.makedirs(self.pending_dir, exist_ok=True)

    def _get_pending_file(self, node_id: str) -> str:
        return os.path.join(self.pending_dir, f"{node_id}.json")

    def has_pending(self, node_id: str) -> bool:
        """检查是否有待合并的修改"""
        return os.path.exists(self._get_pending_file(node_id))

    def save_pending(self, node_id: str, original_text: str, modified_text: str, request_prompt: str) -> None:
        """保存待合并的修改"""
        data = {
            "node_id": node_id,
            "original_text": original_text,
            "modified_text": modified_text,
            "request_prompt": request_prompt,
        }
        with open(self._get_pending_file(node_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def load_pending(self, node_id: str) -> dict | None:
        """加载待合并的修改"""
        file_path = self._get_pending_file(node_id)
        if not os.path.exists(file_path):
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def delete_pending(self, node_id: str) -> None:
        """删除待合并的修改"""
        file_path = self._get_pending_file(node_id)
        if os.path.exists(file_path):
            os.remove(file_path)
