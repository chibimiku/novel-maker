"""
时间线校对窗口模块
包含时间线校对功能的实现
"""
import os
import json
import uuid
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QTextEdit, QLineEdit, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QSplitter, QMessageBox,
                             QWidget, QAbstractItemView)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor


class TimelineDialog(QDialog):
    """时间线校对窗口"""
    def __init__(self, outline_tree_data, parent=None, workspace=None):
        super().__init__(parent)
        self.setWindowTitle("时间线校对")
        self.setMinimumSize(1000, 700)
        self.outline_tree_data = outline_tree_data
        self.workspace = workspace
        self.timeline_data = []  # 时间线数据
        
        # 时间线文件路径 - 基于当前工作空间
        if workspace:
            # 使用 workspace 的 sys_data_path 属性，它已经指向 "系统数据" 目录
            self.timeline_file = os.path.join(workspace.sys_data_path, "timeline.json")
            # 确保系统数据目录存在（虽然 workspace 初始化时应该已经创建了）
            os.makedirs(os.path.dirname(self.timeline_file), exist_ok=True)
            # 加载已保存的时间线数据
            self.load_timeline()
        else:
            self.timeline_file = None
            print("警告：没有提供工作区，时间线数据将不会被保存")

        self.init_ui()

    def load_timeline(self):
        """加载已保存的时间线数据"""
        try:
            if os.path.exists(self.timeline_file):
                with open(self.timeline_file, 'r', encoding='utf-8') as f:
                    self.timeline_data = json.load(f)
        except Exception as e:
            print(f"加载时间线数据失败: {e}")
            self.timeline_data = []

    def init_ui(self):
        """初始化 UI"""
        layout = QVBoxLayout(self)

        # 水平拆分器
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：小说大纲结构
        outline_widget = QWidget()
        outline_layout = QVBoxLayout(outline_widget)
        outline_layout.addWidget(QLabel("小说大纲结构"))
        self.outline_tree = QTreeWidget()
        self.outline_tree.setHeaderLabel("大纲")
        self.outline_tree.itemClicked.connect(self.on_outline_item_clicked)
        outline_layout.addWidget(self.outline_tree)
        main_splitter.addWidget(outline_widget)

        # 中间：时间轴树
        timeline_widget = QWidget()
        timeline_layout = QVBoxLayout(timeline_widget)
        timeline_layout.addWidget(QLabel("时间轴树"))
        self.timeline_tree = TimelineTreeWidget()
        self.timeline_tree.setHeaderLabel("时间线")
        self.timeline_tree.itemClicked.connect(self.on_timeline_item_clicked)
        # 启用拖拽功能
        self.timeline_tree.setDragEnabled(True)
        self.timeline_tree.setAcceptDrops(True)
        self.timeline_tree.setDropIndicatorShown(True)
        self.timeline_tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        # 保存拖拽前的状态，用于后续更新数据
        self.timeline_tree.itemChanged.connect(self.on_timeline_item_changed)
        timeline_layout.addWidget(self.timeline_tree)
        main_splitter.addWidget(timeline_widget)

        # 右侧：节点详情
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.addWidget(QLabel("节点详情"))

        # 时间输入
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("时间:"))
        self.time_edit = QLineEdit()
        self.time_edit.setPlaceholderText("例如：故事开始的第一天")
        time_layout.addWidget(self.time_edit)
        detail_layout.addLayout(time_layout)

        # 概要输入
        summary_layout = QVBoxLayout()
        summary_layout.addWidget(QLabel("概要:"))
        self.summary_edit = QTextEdit()
        self.summary_edit.setPlaceholderText("发生了什么重要事情")
        summary_layout.addWidget(self.summary_edit)
        detail_layout.addLayout(summary_layout)

        # 按钮
        btn_layout = QHBoxLayout()
        self.btn_add_time_node = QPushButton("添加时间节点")
        self.btn_add_time_node.clicked.connect(self.add_time_node)
        self.btn_associate = QPushButton("关联大纲节点")
        self.btn_associate.clicked.connect(self.associate_outline_node)
        self.btn_associate.setEnabled(False)  # 初始禁用
        self.btn_save = QPushButton("保存时间线")
        self.btn_save.clicked.connect(self.save_timeline)
        btn_layout.addWidget(self.btn_add_time_node)
        btn_layout.addWidget(self.btn_associate)
        btn_layout.addWidget(self.btn_save)
        detail_layout.addLayout(btn_layout)

        main_splitter.addWidget(detail_widget)
        main_splitter.setSizes([200, 400, 400])
        layout.addWidget(main_splitter)

        # 填充数据
        self.populate_outline_tree()
        self.populate_timeline_tree()
        
        # 连接选择事件，用于启用/禁用关联按钮
        self.outline_tree.itemSelectionChanged.connect(self.check_selection)
        self.timeline_tree.itemSelectionChanged.connect(self.check_selection)

    def populate_outline_tree(self):
        """填充小说大纲树"""
        if not self.outline_tree_data:
            return

        def add_nodes(parent_item, nodes):
            for node in nodes:
                # 确保每个节点都有 uuid
                if "id" not in node:
                    node["id"] = f"outline_{uuid.uuid4().hex[:8]}"
                item = QTreeWidgetItem([node["title"]])
                item.setData(0, Qt.ItemDataRole.UserRole, node)
                # 设置节点标志，允许选择
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                parent_item.addChild(item)
                if "children" in node:
                    add_nodes(item, node["children"])

        root = self.outline_tree.invisibleRootItem()
        # Access the 'nodes' key from the outline_tree_data dictionary
        nodes_data = self.outline_tree_data.get("nodes", [])
        add_nodes(root, nodes_data)
        # 展开所有节点
        self.outline_tree.expandAll()

    def populate_timeline_tree(self):
        """填充时间轴树"""
        self.timeline_tree.clear()

        # 如果没有时间线数据，创建默认数据
        if not self.timeline_data:
            # 创建默认时间节点
            time_node1 = {
                "id": f"time_{uuid.uuid4().hex[:8]}",
                "type": "time",
                "title": "故事开始",
                "time": "故事开始的第一天",
                "summary": "故事开始，主角登场",
                "nodes": []  # 关联的大纲节点
            }
            self.timeline_data.append(time_node1)

        # 构建大纲节点映射，用于快速查找
        outline_node_map = self._build_outline_node_map()

        # 添加时间节点和对应大纲内容
        for time_node in self.timeline_data:
            time_item = QTreeWidgetItem([time_node["title"]])
            time_item.setData(0, Qt.ItemDataRole.UserRole, time_node)
            self.timeline_tree.addTopLevelItem(time_item)

            # 添加关联的大纲节点
            self._add_outline_nodes_to_timeline(time_item, time_node.get("nodes", []), outline_node_map)

    def _build_outline_node_map(self):
        """构建大纲节点映射，用于快速查找节点"""
        node_map = {}
        
        def traverse_nodes(nodes):
            for node in nodes:
                # 确保每个节点都有 uuid
                if "id" not in node:
                    node["id"] = f"outline_{uuid.uuid4().hex[:8]}"
                node_id = node["id"]
                node_map[node_id] = node
                if "children" in node:
                    traverse_nodes(node["children"])
        
        if self.outline_tree_data:
            nodes_data = self.outline_tree_data.get("nodes", [])
            traverse_nodes(nodes_data)
        
        return node_map

    def _add_outline_nodes_to_timeline(self, parent_item, nodes, outline_node_map):
        """将大纲节点添加到时间线树中"""
        for node_info in nodes:
            node_id = node_info.get("id")
            node = outline_node_map.get(node_id)
            
            item = QTreeWidgetItem([node_info.get("title", "未知节点")])
            item.setData(0, Qt.ItemDataRole.UserRole, node_info)
            
            # 检查节点是否存在
            if not node:
                # 节点不存在，用灰色标识
                item.setForeground(0, QColor(128, 128, 128))
            
            parent_item.addChild(item)
            
            # 添加子节点
            if "children" in node_info:
                self._add_outline_nodes_to_timeline(item, node_info["children"], outline_node_map)

    def on_outline_item_clicked(self, item, column):
        """处理小说大纲节点点击"""
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if node:
            # 可以在这里添加大纲节点点击后的处理逻辑
            print(f"点击了大纲节点: {node.get('title')}")

    def on_timeline_item_clicked(self, item, column):
        """处理时间轴节点点击"""
        # 自动保存当前编辑的时间节点
        current_item = self.timeline_tree.currentItem()
        if current_item:
            current_node = current_item.data(0, Qt.ItemDataRole.UserRole)
            if current_node and current_node.get("type") == "time":
                current_node["time"] = self.time_edit.text()
                current_node["summary"] = self.summary_edit.toPlainText()
                self._save_timeline_to_file()
        
        # 处理新点击的节点
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if node and node.get("type") == "time":
            # 时间节点可编辑
            self.time_edit.setEnabled(True)
            self.summary_edit.setEnabled(True)
            self.time_edit.setText(node.get("time", ""))
            self.summary_edit.setText(node.get("summary", ""))
        else:
            # 大纲节点不可编辑
            self.time_edit.setEnabled(False)
            self.summary_edit.setEnabled(False)
            self.time_edit.setText("")
            self.summary_edit.setText("")

    def on_timeline_item_changed(self, item, column):
        """处理时间轴节点变化（包括拖拽）"""
        # 当节点位置发生变化时，更新时间线数据
        self._update_timeline_data_from_tree()
        self._save_timeline_to_file()

    def _update_timeline_data_from_tree(self):
        """从时间轴树更新时间线数据"""
        new_timeline_data = []
        root = self.timeline_tree.invisibleRootItem()
        
        def traverse_items(item):
            node = item.data(0, Qt.ItemDataRole.UserRole)
            if node:
                # 处理子节点
                if node.get("type") == "time":
                    # 时间节点，需要更新其子节点
                    new_node = node.copy()
                    new_node["nodes"] = []
                    for i in range(item.childCount()):
                        child_item = item.child(i)
                        child_node = child_item.data(0, Qt.ItemDataRole.UserRole)
                        if child_node:
                            new_node["nodes"].append(child_node)
                    return new_node
            return None
        
        for i in range(root.childCount()):
            item = root.child(i)
            time_node = traverse_items(item)
            if time_node:
                new_timeline_data.append(time_node)
        
        self.timeline_data = new_timeline_data

    def add_time_node(self):
        """添加时间节点"""
        # 创建新时间节点
        new_time_node = {
            "id": f"time_{uuid.uuid4().hex[:8]}",
            "type": "time",
            "title": f"时间节点 {len(self.timeline_data) + 1}",
            "time": "",
            "summary": "",
            "nodes": []  # 关联的大纲节点
        }
        self.timeline_data.append(new_time_node)

        # 更新时间轴树
        time_item = QTreeWidgetItem([new_time_node["title"]])
        time_item.setData(0, Qt.ItemDataRole.UserRole, new_time_node)
        self.timeline_tree.addTopLevelItem(time_item)
        
        # 保存到文件
        self._save_timeline_to_file()

    def _save_timeline_to_file(self):
        """将时间线数据保存到文件"""
        if not self.timeline_file:
            print("没有工作区，无法保存时间线数据")
            return False
        
        try:
            with open(self.timeline_file, 'w', encoding='utf-8') as f:
                json.dump(self.timeline_data, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"保存时间线数据失败: {e}")
            return False

    def check_selection(self):
        """检查选择状态，启用/禁用关联按钮"""
        outline_selected = bool(self.outline_tree.selectedItems())
        timeline_selected = bool(self.timeline_tree.selectedItems())
        
        # 只有当同时选择了大纲节点和时间节点时，才启用关联按钮
        if outline_selected and timeline_selected:
            # 检查选中的时间节点是否为顶级时间节点
            timeline_item = self.timeline_tree.selectedItems()[0]
            timeline_node = timeline_item.data(0, Qt.ItemDataRole.UserRole)
            if timeline_node and timeline_node.get("type") == "time":
                self.btn_associate.setEnabled(True)
                return
        
        self.btn_associate.setEnabled(False)

    def associate_outline_node(self):
        """关联大纲节点到时间节点"""
        # 获取选中的大纲节点和时间节点
        outline_items = self.outline_tree.selectedItems()
        timeline_items = self.timeline_tree.selectedItems()
        
        if not outline_items or not timeline_items:
            QMessageBox.warning(self, "错误", "请同时选择大纲节点和时间节点")
            return
        
        outline_item = outline_items[0]
        timeline_item = timeline_items[0]
        
        # 检查时间节点是否为顶级时间节点
        timeline_node = timeline_item.data(0, Qt.ItemDataRole.UserRole)
        if not timeline_node or timeline_node.get("type") != "time":
            QMessageBox.warning(self, "错误", "请选择时间节点作为关联目标")
            return
        
        # 获取大纲节点数据
        outline_node = outline_item.data(0, Qt.ItemDataRole.UserRole)
        if not outline_node:
            QMessageBox.warning(self, "错误", "选中的大纲节点无效")
            return
        
        # 获取大纲节点的 uuid
        outline_node_id = outline_node.get("id")
        if not outline_node_id:
            # 如果没有 uuid，生成一个
            outline_node_id = f"outline_{uuid.uuid4().hex[:8]}"
            outline_node["id"] = outline_node_id
        
        # 检查是否已经存在关联
        if self._check_existing_association(timeline_node, outline_node_id):
            QMessageBox.warning(self, "错误", "该大纲节点或其子节点已经关联到时间线中")
            return
        
        # 创建关联节点数据
        def create_associated_node(node):
            # 确保节点有 uuid
            if "id" not in node:
                node["id"] = f"outline_{uuid.uuid4().hex[:8]}"
            
            associated_node = {
                "id": node["id"],
                "title": node.get("title", ""),
                "children": []
            }
            
            # 递归添加子节点
            if "children" in node:
                for child in node["children"]:
                    child_node = create_associated_node(child)
                    associated_node["children"].append(child_node)
            
            return associated_node
        
        associated_node = create_associated_node(outline_node)
        
        # 添加到时间节点的关联列表
        if "nodes" not in timeline_node:
            timeline_node["nodes"] = []
        timeline_node["nodes"].append(associated_node)
        
        # 确保时间节点在 timeline_data 中
        found = False
        for i, time_node in enumerate(self.timeline_data):
            if time_node.get("id") == timeline_node.get("id"):
                self.timeline_data[i] = timeline_node
                found = True
                break
        if not found:
            self.timeline_data.append(timeline_node)
        
        # 更新时间轴树
        self.populate_timeline_tree()
        
        # 保存到文件
        self._save_timeline_to_file()
        
        QMessageBox.information(self, "提示", "大纲节点关联成功！")

    def _check_existing_association(self, timeline_node, node_id):
        """检查是否已经存在关联"""
        def check_node(nodes, target_id):
            for node in nodes:
                if node.get("id") == target_id:
                    return True
                if "children" in node:
                    if check_node(node["children"], target_id):
                        return True
            return False
        
        # 检查所有时间节点
        for time_node in self.timeline_data:
            if "nodes" in time_node:
                if check_node(time_node["nodes"], node_id):
                    return True
        
        return False

    def save_timeline(self):
        """保存时间线"""
        # 保存当前编辑的时间节点
        current_item = self.timeline_tree.currentItem()
        if current_item:
            node = current_item.data(0, Qt.ItemDataRole.UserRole)
            if node and node.get("type") == "time":
                node["time"] = self.time_edit.text()
                node["summary"] = self.summary_edit.toPlainText()

        # 保存到文件
        if self._save_timeline_to_file():
            QMessageBox.information(self, "提示", "时间线保存成功！")
        else:
            QMessageBox.warning(self, "错误", "时间线保存失败，请检查文件权限")


class TimelineTreeWidget(QTreeWidget):
    """自定义时间轴树控件，控制拖拽行为"""
    def __init__(self, parent=None):
        super().__init__(parent)

    def get_item_level(self, item):
        """辅助方法：获取节点层级，顶级(1级节点)为1"""
        level = 1
        parent = item.parent()
        while parent is not None:
            level += 1
            parent = parent.parent()
        return level

    def _set_level1_drop_enabled(self, enabled: bool):
        """核心修复：动态控制1级节点是否可以作为父节点接收拖放，以此从根本消除高亮框"""
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            flags = item.flags()
            if enabled:
                new_flags = flags | Qt.ItemFlag.ItemIsDropEnabled
            else:
                new_flags = flags & ~Qt.ItemFlag.ItemIsDropEnabled
            
            # 只有在标志确实需要改变时才执行操作，确保拖拽流畅度
            if flags != new_flags:
                item.setFlags(new_flags)

    def dragEnterEvent(self, event):
        """处理拖拽进入事件"""
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        """处理拖拽离开事件（包括按ESC取消拖拽）"""
        # 拖拽离开或取消时，确保恢复所有1级节点的状态
        self._set_level1_drop_enabled(True)
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event):
        """处理拖拽移动事件"""
        drag_item = self.currentItem()
        if drag_item:
            drag_level = self.get_item_level(drag_item)
            # 动态调整目标权限：只有当拖拽的【不是】1级节点时，才允许1级节点发光高亮
            self._set_level1_drop_enabled(drag_level != 1)

        # 必须先调用父类，系统会基于上面动态设置的 Flag 准确计算指示线，不再出现非法高亮
        super().dragMoveEvent(event)
        
        if not event.isAccepted():
            return

        target = self.itemAt(event.position().toPoint())
        
        if not drag_item:
            event.ignore()
            return

        drag_level = self.get_item_level(drag_item)
        drop_pos = self.dropIndicatorPosition()
        
        # 【规则1】拖拽项是 1 级节点（时间节点）
        if drag_level == 1:
            if target is None:
                return # 允许拖到空白区域
            
            target_level = self.get_item_level(target)
            if target_level == 1:
                # 因为上面临时禁用了 DropEnabled，此时 drop_pos 只能是 Above 或 Below
                return 
            
            event.ignore()
            return
            
        # 【规则2】拖拽项是 2 级及以下节点（大纲节点）
        else:
            if target is None:
                event.ignore()
                return
            
            target_level = self.get_item_level(target)
            
            # 限制大纲节点禁止放置在 1 级节点的同级位置
            if target_level == 1 and drop_pos != QAbstractItemView.DropIndicatorPosition.OnItem:
                event.ignore()
                return
            
            # 限制目标是 4 级节点，且试图作为其子节点
            if target_level >= 4 and drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem:
                event.ignore()
                return

    def dropEvent(self, event):
        """处理实际放置事件"""
        try:
            target = self.itemAt(event.position().toPoint())
            drag_item = self.currentItem()
            
            if not drag_item:
                event.ignore()
                return

            drag_level = self.get_item_level(drag_item)
            drop_pos = self.dropIndicatorPosition()
            
            if drag_level == 1:
                if target is not None:
                    target_level = self.get_item_level(target)
                    if target_level != 1 or drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem:
                        event.ignore()
                        return
            else:
                if target is None:
                    event.ignore()
                    return
                
                target_level = self.get_item_level(target)
                if target_level == 1 and drop_pos != QAbstractItemView.DropIndicatorPosition.OnItem:
                    event.ignore()
                    return
                
                if target_level >= 4 and drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem:
                    event.ignore()
                    return
            
            # 校验彻底通过后，交由原生父类处理 UI 层面真实的数据移动
            super().dropEvent(event)
            
        finally:
            # 【核心修复】必须在放下动作完全判定/执行结束后，再恢复1级节点的接收标志位
            # 这样系统在计算放置位置时，使用的是临时禁用的状态，严格保证仅能排序
            self._set_level1_drop_enabled(True)