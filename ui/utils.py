"""
通用工具函数模块
"""
import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidgetItem


def find_duplicate_paths(nodes: list) -> set:
    """递归遍历大纲，寻找并返回重复使用的 file_path 集合"""
    path_counts: dict[str, int] = {}

    def traverse(node_list: list):
        for node in node_list:
            p = node.get("file_path")
            if p:
                path_counts[p] = path_counts.get(p, 0) + 1
            traverse(node.get("children", []))

    traverse(nodes)
    return set(p for p, count in path_counts.items() if count > 1)


def get_item_level(item: QTreeWidgetItem) -> int:
    """返回 QTreeWidgetItem 在树中的层级（根节点为 1）"""
    level = 1
    p = item.parent()
    while p:
        level += 1
        p = p.parent()
    return level


def get_missing_level3_nodes(nodes: list, level: int = 1) -> list:
    """递归寻找所有状态为 missing 的第 3 级（场景）节点"""
    missing: list = []
    for node in nodes:
        if level == 3 and node.get("_status") == "missing":
            missing.append(node)
        if "children" in node:
            missing.extend(get_missing_level3_nodes(node["children"], level + 1))
    return missing


def find_item_by_data(parent_item: QTreeWidgetItem, search_data) -> QTreeWidgetItem | None:
    """辅助函数：通过 UserRole 数据定位树状图中的 UI 节点"""
    for i in range(parent_item.childCount()):
        item = parent_item.child(i)
        if item.data(0, Qt.ItemDataRole.UserRole) == search_data:
            return item
        found = find_item_by_data(item, search_data)
        if found:
            return found
    return None


def clean_json_string(text: str) -> str:
    """提取字符串中的 JSON 内容，完全不使用正则表达式"""
    start_dict = text.find('{')
    start_list = text.find('[')
    
    start_idx = -1
    if start_dict != -1 and start_list != -1:
        start_idx = min(start_dict, start_list)
    elif start_dict != -1:
        start_idx = start_dict
    elif start_list != -1:
        start_idx = start_list
        
    if start_idx == -1:
        return text 

    end_dict = text.rfind('}')
    end_list = text.rfind(']')
    end_idx = max(end_dict, end_list)
    
    if end_idx != -1 and end_idx >= start_idx:
        return text[start_idx:end_idx+1]
    return text
