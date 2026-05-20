"""
NovelTreeMixin —— 小说大纲树的渲染、节点交互、拖拽同步、大纲生成。
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
import copy
import time
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from ui.summary_sync_worker import SummarySyncWorker

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ui.theme import NODE_ADD_BTN, NODE_ERROR, NODE_MISSING, NODE_NORMAL
from ui.diff_merge_dialog import DiffMergeDialog
from ui.dialogs import IdeaInputDialog, RenameNodeDialog
from ui.utils import clean_json_string, find_duplicate_paths, get_item_level, find_item_by_data
from ui.workers import OutlineBuildingThread, GenerateTaskThread, SettingSelectionThread
from core.context_builder import ContextBuilder

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class NovelTreeMixin:
    """小说大纲树的渲染、节点交互与大纲自动生成。"""

    _NODE_CLIPBOARD_MAGIC = "novel_node_clipboard"
    _NODE_CLIPBOARD_VERSION = 1
    _MAX_TREE_LEVEL = 3

    def _get_node_subtree_depth(self: "NovelCreatorWindow", node: dict) -> int:
        """返回节点子树深度（自身为 1 层）。"""
        children = node.get("children", [])
        if not children:
            return 1
        return 1 + max(self._get_node_subtree_depth(child) for child in children)

    def _iter_node_ids(self: "NovelCreatorWindow", node: dict) -> set[str]:
        """收集子树中所有节点 id，用于剪切后粘贴的合法性判断。"""
        ids = set()
        node_id = node.get("id")
        if node_id:
            ids.add(node_id)
        for child in node.get("children", []):
            ids.update(self._iter_node_ids(child))
        return ids

    def _build_node_clipboard_payload(
        self: "NovelCreatorWindow", node: dict, mode: str
    ) -> dict:
        data = copy.deepcopy(node)
        return {
            "magic": self._NODE_CLIPBOARD_MAGIC,
            "version": self._NODE_CLIPBOARD_VERSION,
            "mode": mode,
            "node": data,
            "subtree_depth": self._get_node_subtree_depth(data),
        }

    def _set_node_clipboard_payload(self: "NovelCreatorWindow", payload: dict):
        """同时写入应用内缓存和系统剪贴板。"""
        self._novel_node_clipboard = payload
        try:
            QApplication.clipboard().setText(
                json.dumps(payload, ensure_ascii=False, indent=2)
            )
        except Exception:
            # 系统剪贴板写失败时，仍保留应用内缓存
            pass

    def _get_valid_node_clipboard_payload(self: "NovelCreatorWindow") -> dict | None:
        """读取并校验节点剪贴板内容（优先系统剪贴板）。"""
        text = ""
        try:
            text = QApplication.clipboard().text() or ""
        except Exception:
            text = ""
        if text.strip():
            try:
                payload = json.loads(text)
                if (
                    isinstance(payload, dict)
                    and payload.get("magic") == self._NODE_CLIPBOARD_MAGIC
                    and payload.get("version") == self._NODE_CLIPBOARD_VERSION
                    and payload.get("mode") in ("cut", "copy")
                    and isinstance(payload.get("node"), dict)
                ):
                    self._novel_node_clipboard = payload
                    return payload
            except Exception:
                pass

        payload = getattr(self, "_novel_node_clipboard", None)
        if (
            isinstance(payload, dict)
            and payload.get("magic") == self._NODE_CLIPBOARD_MAGIC
            and payload.get("version") == self._NODE_CLIPBOARD_VERSION
            and payload.get("mode") in ("cut", "copy")
            and isinstance(payload.get("node"), dict)
        ):
            return payload
        return None

    def _find_node_and_parent_list_by_id(
        self: "NovelCreatorWindow", node_id: str
    ) -> tuple[dict | None, list | None, int]:
        """在 outline_tree_data 里定位节点以及它所在列表索引。"""
        if not self.outline_tree_data:
            return None, None, -1

        def walk(nodes: list):
            for idx, node in enumerate(nodes):
                if node.get("id") == node_id:
                    return node, nodes, idx
                child_nodes = node.get("children", [])
                found = walk(child_nodes)
                if found[0] is not None:
                    return found
            return None, None, -1

        return walk(self.outline_tree_data.get("nodes", []))

    def _regenerate_ids_and_files_for_copy(self: "NovelCreatorWindow", node: dict):
        """复制粘贴时为整棵子树生成新 id，并复制正文文件。"""
        node["id"] = str(uuid.uuid4())
        if "children" not in node or not isinstance(node["children"], list):
            node["children"] = []

        rel_path = node.get("file_path")
        if rel_path and self.workspace:
            full_path = os.path.join(self.workspace.text_path, rel_path)
            old_content = ""
            if os.path.exists(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        old_content = f.read()
                except Exception:
                    old_content = ""
            if not old_content:
                old_content = f"# {node.get('title', '未命名节点')}\n\n"

            new_file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
            new_md5 = self.workspace.save_markdown_file(new_file_name, old_content)
            node["file_path"] = new_file_name
            node["md5"] = new_md5

        for child in node["children"]:
            if isinstance(child, dict):
                self._regenerate_ids_and_files_for_copy(child)

    def _can_paste_to_item(
        self: "NovelCreatorWindow", target_item, payload: dict | None
    ) -> tuple[bool, str]:
        """判断目标节点是否允许粘贴。"""
        if not payload:
            return False, "剪贴板中没有可粘贴的节点数据。"
        if not target_item or target_item.text(0).startswith("+"):
            return False, "请选择一个实际节点作为粘贴目标。"

        target_level = get_item_level(target_item)
        subtree_depth = int(payload.get("subtree_depth") or 0)
        if subtree_depth <= 0:
            subtree_depth = self._get_node_subtree_depth(payload.get("node", {}))

        # 粘贴后子树最深层 = 目标节点层级 + 粘贴子树深度
        if target_level + subtree_depth > self._MAX_TREE_LEVEL:
            return False, "目标节点的下级层级空间不足，粘贴后会超过 3 层限制。"

        mode = payload.get("mode")
        source_id = payload.get("node", {}).get("id")
        if mode == "cut" and source_id:
            target_id = target_item.data(0, Qt.ItemDataRole.UserRole)
            source_node, _, _ = self._find_node_and_parent_list_by_id(source_id)
            if not source_node:
                return False, "剪切来源节点不存在，可能已被删除或刷新。"
            source_ids = self._iter_node_ids(source_node)
            if target_id in source_ids:
                return False, "不能将节点粘贴到它自身或其后代节点下。"

        return True, ""

    def copy_current_node_to_clipboard(self: "NovelCreatorWindow", item):
        if not item or item.text(0).startswith("+"):
            return
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        node = self.node_map.get(node_id) if node_id else None
        if not node:
            return
        payload = self._build_node_clipboard_payload(node, "copy")
        self._set_node_clipboard_payload(payload)
        self.log_console.append(f"已复制节点到剪贴板：{node.get('title', '未命名节点')}")

    def cut_current_node_to_clipboard(self: "NovelCreatorWindow", item):
        if not item or item.text(0).startswith("+"):
            return
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        node = self.node_map.get(node_id) if node_id else None
        if not node:
            return
        payload = self._build_node_clipboard_payload(node, "cut")
        self._set_node_clipboard_payload(payload)
        self.log_console.append(
            f"已剪切节点到剪贴板（待粘贴）：{node.get('title', '未命名节点')}"
        )

    def paste_node_from_clipboard(self: "NovelCreatorWindow", target_item):
        if not self.workspace or not self.outline_tree_data:
            return
        payload = self._get_valid_node_clipboard_payload()
        can_paste, reason = self._can_paste_to_item(target_item, payload)
        if not can_paste:
            QMessageBox.warning(self, "无法粘贴", reason)  # type: ignore[arg-type]
            return

        target_id = target_item.data(0, Qt.ItemDataRole.UserRole)
        target_node = self.node_map.get(target_id) if target_id else None
        if not target_node:
            QMessageBox.warning(self, "无法粘贴", "未找到粘贴目标节点。")  # type: ignore[arg-type]
            return

        target_children = target_node.setdefault("children", [])
        mode = payload.get("mode")
        source_node = payload.get("node", {})

        inserted_node = None
        if mode == "cut":
            source_id = source_node.get("id")
            found_node, parent_list, idx = self._find_node_and_parent_list_by_id(source_id)
            if found_node is None or parent_list is None or idx < 0:
                QMessageBox.warning(
                    self, "无法粘贴", "剪切来源节点不存在，可能已被删除。"
                )  # type: ignore[arg-type]
                return
            inserted_node = parent_list.pop(idx)
            target_children.append(inserted_node)
            self._novel_node_clipboard = None
        else:
            inserted_node = copy.deepcopy(source_node)
            self._regenerate_ids_and_files_for_copy(inserted_node)
            target_children.append(inserted_node)

        self.workspace.save_outline_tree(self.outline_tree_data)
        self._auto_export_html_if_enabled("粘贴节点")
        self.refresh_ui_from_workspace()

        if inserted_node and inserted_node.get("id"):
            new_item = find_item_by_data(
                self.novel_tree.invisibleRootItem(), inserted_node["id"]
            )
            if new_item:
                parent = new_item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.novel_tree.setCurrentItem(new_item)
                self.novel_tree.scrollToItem(new_item)

        action_text = "移动" if mode == "cut" else "复制"
        self.log_console.append(
            f"已{action_text}节点到【{target_node.get('title', '未命名节点')}】下。"
        )

    def _trace_add_btn(self: "NovelCreatorWindow", msg: str):
        """统一输出调试日志（当前主要覆盖 +新增 按钮相关链路）。"""
        debug_enabled = bool(
            getattr(
                self,
                "_debug_log_enabled",
                getattr(self, "_debug_add_button", False),
            )
        )
        if not debug_enabled:
            return
        text = f"[DebugLog][AddBtn] {msg}"
        if hasattr(self, "log_console") and self.log_console:
            self.log_console.append(f"<font color='#9AA0A6'>{text}</font>")
        else:
            print(text)

    def set_debug_log_enabled(self: "NovelCreatorWindow", enabled: bool):
        """设置统一 Debug Log 开关并持久化。"""
        enabled = bool(enabled)
        self._debug_log_enabled = enabled
        # 兼容旧属性名，避免其它分支逻辑读取失败
        self._debug_add_button = enabled
        self._save_debug_log_enabled(enabled)
        # 校对模块 debug 开关（独立模块也可读）
        os.environ["PROOFREAD_DEBUG_LOG"] = "1" if enabled else "0"
        if hasattr(self, "log_console") and self.log_console:
            status = "已开启" if enabled else "已关闭"
            self.log_console.append(f"Debug Log {status}。")

    def set_add_button_debug_enabled(self: "NovelCreatorWindow", enabled: bool):
        """兼容旧方法名：重定向到统一 Debug Log 开关。"""
        self.set_debug_log_enabled(enabled)

    def _get_item_path(self: "NovelCreatorWindow", item) -> str:
        """返回节点路径，便于调试定位。"""
        if not item:
            return "(None)"
        if item == self.novel_tree.invisibleRootItem():
            return "(ROOT)"
        names = []
        cur = item
        while cur and cur != self.novel_tree.invisibleRootItem():
            names.append(cur.text(0))
            cur = cur.parent()
        names.reverse()
        return " / ".join(names) if names else "(ROOT)"

    def _scan_add_button_health(self: "NovelCreatorWindow", stage: str):
        """扫描整棵树每个可添加子节点的父节点，输出按钮完整性。"""
        debug_enabled = bool(
            getattr(
                self,
                "_debug_log_enabled",
                getattr(self, "_debug_add_button", False),
            )
        )
        if not debug_enabled:
            return
        root = self.novel_tree.invisibleRootItem()
        checked = 0
        issue_count = 0

        def scan(parent_item):
            nonlocal checked, issue_count
            child_level = 1 if parent_item == root else get_item_level(parent_item) + 1
            if child_level > 3:
                return

            add_count = 0
            for i in range(parent_item.childCount()):
                if parent_item.child(i).text(0).startswith("+"):
                    add_count += 1

            checked += 1
            path = self._get_item_path(parent_item)
            real_count, add_count_detail, child_titles = self._get_children_debug_info(parent_item)
            self._trace_add_btn(
                f"{stage}: check parent='{path}', child_level={child_level}, "
                f"childCount={parent_item.childCount()}, realChildCount={real_count}, addBtnCount={add_count_detail}, "
                f"children={child_titles}"
            )
            if add_count_detail != 1:
                issue_count += 1

            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if not child.text(0).startswith("+"):
                    scan(child)

        scan(root)
        self._trace_add_btn(f"{stage}: summary checked={checked}, issueParents={issue_count}")

    def _get_full_node_name_from_item(self: "NovelCreatorWindow", item) -> str:
        """根据树节点回溯完整路径名称（章/节/场景）。"""
        if not item:
            return ""
        names = []
        cur = item
        while cur and cur != self.novel_tree.invisibleRootItem():
            if cur.text(0).startswith("+"):
                cur = cur.parent()
                continue
            node_id = cur.data(0, Qt.ItemDataRole.UserRole)
            node = self.node_map.get(node_id) if node_id else None
            title = (node or {}).get("title") or cur.text(0)
            names.append(title)
            cur = cur.parent()
        names.reverse()
        return " / ".join(names)

    def _get_node_path_from_outline(self: "NovelCreatorWindow", node_id: str) -> str:
        """从 outline_tree_data 中查找节点的完整层级路径（章/节/场景）。"""
        def walk(nodes, path):
            for node in nodes:
                current_path = path + [node.get("title", "未知节点")]
                if node.get("id") == node_id:
                    return current_path
                children = node.get("children")
                if children:
                    result = walk(children, current_path)
                    if result:
                        return result
            return None

        result = walk(self.outline_tree_data.get("nodes", []), [])
        return " / ".join(result) if result else ""

    def _update_summary_header(self: "NovelCreatorWindow", full_name: str = ""):
        """复用概要区既有标题行，按需附加完整节点名称。"""
        base = "节点概要 (Summary - 保存至系统数据):"
        label = getattr(self, "summary_title_label", None)
        if not label:
            return
        if full_name:
            label.setText(f"{base} {full_name}")
            label.setToolTip(full_name)
        else:
            label.setText(base)
            label.setToolTip(base)

    def _get_children_debug_info(self: "NovelCreatorWindow", parent_item):
        """返回子节点调试信息：真实子节点数、加号按钮数、标题列表。"""
        child_titles = []
        real_count = 0
        add_count = 0
        for i in range(parent_item.childCount()):
            t = parent_item.child(i).text(0)
            child_titles.append(t)
            if t.startswith("+"):
                add_count += 1
            else:
                real_count += 1
        return real_count, add_count, child_titles

    # ================= 小说树渲染 ================= #

    def _refresh_novel_tree(self: "NovelCreatorWindow"):
        """渲染右侧的小说大纲目录树（带折叠状态记忆）。"""
        self._trace_add_btn("refresh start")
        
        # 1. 在清空前，记录当前展开的节点 ID
        is_first_load = self.novel_tree.topLevelItemCount() == 0
        expanded_ids = set()
        
        if not is_first_load:
            def collect_expanded(parent_item):
                for i in range(parent_item.childCount()):
                    child = parent_item.child(i)
                    if child.isExpanded():
                        n_id = child.data(0, Qt.ItemDataRole.UserRole)
                        if n_id:
                            expanded_ids.add(n_id)
                    # 递归检查子节点
                    collect_expanded(child)
            
            collect_expanded(self.novel_tree.invisibleRootItem())

        # 2. 原有的清理和加载逻辑
        self.novel_tree.clear()
        self.node_map.clear()
        self.current_editing_item = None  # 清除已删除的item引用
        self.novel_tree.setHeaderLabel("小说大纲结构")
        self.outline_tree_data = self.workspace.load_outline_tree()

        if "nodes" not in self.outline_tree_data:
            self.outline_tree_data["nodes"] = []
        nodes_ref = self.outline_tree_data["nodes"]

        # 冲突警告逻辑保持不变
        self._duplicate_paths = find_duplicate_paths(nodes_ref)
        if self._duplicate_paths:
            dup_list = ", ".join(self._duplicate_paths)
            self.log_console.append(
                "<font color='red'><b>"
                "\u26a0\ufe0f 严重警告：检测到多个场景节点指向相同的物理文件！"
                "可能导致剧情覆盖或丢失。<br>"
                f"冲突的底层文件列表：{dup_list}<br>"
                "请手动在冲突的节点中点击\u201c保存当前节点\u201d"
                "以重新生成独立的 MD 文件绑定。"
                "</b></font>"
            )
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "节点冲突警告",
                "检测到多个大纲节点共用了同一个本地 Markdown 文件"
                "（树状图中已标红）。\n请留意控制台警告，并手动编辑处理冲突！",
            )

        # 构建 UI 树
        self._build_novel_tree_ui(nodes_ref, self.novel_tree, level=1)
        self._scan_add_button_health("after_build")
        # 兜底自愈：补齐/纠正所有层级的“+新增...”按钮
        self._ensure_novel_tree_add_buttons()
        self._scan_add_button_health("after_ensure_all")
        
        # 3. 恢复折叠状态
        if is_first_load:
            # 首次加载默认展开到“节”层，便于直接看到“+ 新增场景...”
            root = self.novel_tree.invisibleRootItem()
            for i in range(root.childCount()):
                child = root.child(i)
                if not child.text(0).startswith("+"):
                    child.setExpanded(True)
                    for j in range(child.childCount()):
                        sec = child.child(j)
                        if not sec.text(0).startswith("+"):
                            sec.setExpanded(True)
        else:
            # 非首次加载时严格恢复用户上次的展开/收起状态
            def restore_expanded(parent_item):
                for i in range(parent_item.childCount()):
                    child = parent_item.child(i)
                    n_id = child.data(0, Qt.ItemDataRole.UserRole)
                    if n_id:
                        child.setExpanded(n_id in expanded_ids)
                    restore_expanded(child)

            restore_expanded(self.novel_tree.invisibleRootItem())
        self._trace_add_btn("refresh done")

    def _ensure_novel_tree_add_buttons(self: "NovelCreatorWindow"):
        """递归自愈“+新增...”按钮：缺失补齐、重复去重、并固定在末尾。"""
        titles = {1: "+ 新增章...", 2: "+ 新增节...", 3: "+ 新增场景..."}

        def get_child_level(parent_item) -> int:
            # parent_item 为不可见根节点时，子节点层级是 1
            if parent_item == self.novel_tree.invisibleRootItem():
                return 1
            return get_item_level(parent_item) + 1

        def ensure_for_parent(parent_item):
            child_level = get_child_level(parent_item)
            if child_level > 3:
                return

            expected_text = titles.get(child_level, "+ 新增节点...")
            add_btn_indexes = []
            add_btn_items = []
            path = self._get_item_path(parent_item)
            real_count, add_count_detail, child_titles = self._get_children_debug_info(parent_item)

            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if child.text(0).startswith("+"):
                    add_btn_indexes.append(i)
                    add_btn_items.append(child)

            self._trace_add_btn(
                f"ensure_all: parent='{path}', child_level={child_level}, "
                f"childCount={parent_item.childCount()}, realChildCount={real_count}, "
                f"addBtnFound={len(add_btn_items)}, expected='{expected_text}', children={child_titles}"
            )

            # 若没有“+”按钮则创建一个；若有多个则只保留最后一个并删除其余。
            if not add_btn_items:
                add_btn = QTreeWidgetItem(parent_item, [expected_text])
                add_btn.setForeground(0, QColor(NODE_ADD_BTN))
                add_btn.setToolTip(0, expected_text)
                add_btn.setFlags(
                    add_btn.flags()
                    & ~Qt.ItemFlag.ItemIsDragEnabled
                    & ~Qt.ItemFlag.ItemIsDropEnabled
                )
                self._trace_add_btn(
                    f"ensure_all: create add button parent='{path}', text='{expected_text}'"
                )
            else:
                keep_btn = add_btn_items[-1]
                keep_btn.setText(0, expected_text)
                keep_btn.setForeground(0, QColor(NODE_ADD_BTN))
                keep_btn.setToolTip(0, expected_text)
                keep_btn.setFlags(
                    keep_btn.flags()
                    & ~Qt.ItemFlag.ItemIsDragEnabled
                    & ~Qt.ItemFlag.ItemIsDropEnabled
                )
                self._trace_add_btn(
                    f"ensure_all: keep/normalize parent='{path}', keepIndex={add_btn_indexes[-1]}, text='{expected_text}'"
                )

                # 删除多余“+”按钮（从后往前删，避免索引漂移）
                for idx in reversed(add_btn_indexes[:-1]):
                    parent_item.takeChild(idx)
                    self._trace_add_btn(
                        f"ensure_all: remove duplicate parent='{path}', removedIndex={idx}"
                    )

                # 将保留按钮移动到末尾
                idx_keep = -1
                for i in range(parent_item.childCount()):
                    if parent_item.child(i) is keep_btn:
                        idx_keep = i
                        break
                if idx_keep != -1 and idx_keep < parent_item.childCount() - 1:
                    moved = parent_item.takeChild(idx_keep)
                    parent_item.addChild(moved)
                    self._trace_add_btn(
                        f"ensure_all: move-to-tail parent='{path}', fromIndex={idx_keep}"
                    )

            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if not child.text(0).startswith("+"):
                    ensure_for_parent(child)

        ensure_for_parent(self.novel_tree.invisibleRootItem())

    def _ensure_add_button_for_item(self: "NovelCreatorWindow", item):
        """单节点兜底：确保章/节节点下存在且仅存在一个正确的“+新增...”按钮。"""
        if not item or item.text(0).startswith("+"):
            self._trace_add_btn("ensure_one: skip (None or add-button item)")
            return

        item_level = get_item_level(item)
        if item_level >= 3:
            self._trace_add_btn(
                f"ensure_one: skip level>=3 path='{self._get_item_path(item)}', level={item_level}"
            )
            return

        expected_by_level = {1: "+ 新增节...", 2: "+ 新增场景..."}
        expected_text = expected_by_level.get(item_level)
        if not expected_text:
            self._trace_add_btn(
                f"ensure_one: skip no-expected-text path='{self._get_item_path(item)}', level={item_level}"
            )
            return

        add_btn_indexes = []
        add_btn_items = []
        for i in range(item.childCount()):
            child = item.child(i)
            if child.text(0).startswith("+"):
                add_btn_indexes.append(i)
                add_btn_items.append(child)
        path = self._get_item_path(item)
        real_count, _, child_titles = self._get_children_debug_info(item)
        self._trace_add_btn(
            f"ensure_one: parent='{path}', level={item_level}, childCount={item.childCount()}, "
            f"realChildCount={real_count}, addBtnFound={len(add_btn_items)}, expected='{expected_text}', "
            f"children={child_titles}"
        )
        if item_level == 1 and real_count == 0:
            self._trace_add_btn(
                f"ensure_one: chapter '{path}' has no real section node yet, only add button."
            )

        if not add_btn_items:
            add_btn = QTreeWidgetItem(item, [expected_text])
            add_btn.setForeground(0, QColor(NODE_ADD_BTN))
            add_btn.setToolTip(0, expected_text)
            add_btn.setFlags(
                add_btn.flags()
                & ~Qt.ItemFlag.ItemIsDragEnabled
                & ~Qt.ItemFlag.ItemIsDropEnabled
            )
            self._trace_add_btn(
                f"ensure_one: create parent='{path}', text='{expected_text}'"
            )
            return

        keep_btn = add_btn_items[-1]
        keep_btn.setText(0, expected_text)
        keep_btn.setForeground(0, QColor(NODE_ADD_BTN))
        keep_btn.setToolTip(0, expected_text)
        keep_btn.setFlags(
            keep_btn.flags()
            & ~Qt.ItemFlag.ItemIsDragEnabled
            & ~Qt.ItemFlag.ItemIsDropEnabled
        )
        self._trace_add_btn(
            f"ensure_one: keep/normalize parent='{path}', keepIndex={add_btn_indexes[-1]}"
        )

        for idx in reversed(add_btn_indexes[:-1]):
            item.takeChild(idx)
            self._trace_add_btn(
                f"ensure_one: remove duplicate parent='{path}', removedIndex={idx}"
            )

        idx_keep = -1
        for i in range(item.childCount()):
            if item.child(i) is keep_btn:
                idx_keep = i
                break
        if idx_keep != -1 and idx_keep < item.childCount() - 1:
            moved = item.takeChild(idx_keep)
            item.addChild(moved)
            self._trace_add_btn(
                f"ensure_one: move-to-tail parent='{path}', fromIndex={idx_keep}"
            )

    def on_novel_item_expanded(self: "NovelCreatorWindow", item):
        """节点展开时执行局部自愈，防止个别节点“+新增...”丢失。"""
        self._trace_add_btn(
            f"expanded: path='{self._get_item_path(item)}', level={get_item_level(item) if item else 'N/A'}"
        )
        self._ensure_add_button_for_item(item)

    def _build_novel_tree_ui(
        self: "NovelCreatorWindow", nodes: list, parent_widget, level: int = 1
    ):
        # 场景的内部（第4级），严格禁止渲染任何子节点和按钮
        if level > 3:
            return

        for node in nodes:
            title = node.get("title", "未命名节点")
            # 对于3级节点，添加文件修改时间
            if level == 3:
                file_path = node.get("file_path")
                if file_path:
                    full_path = os.path.join(self.workspace.text_path, file_path)
                    if os.path.exists(full_path):
                        mtime = os.path.getmtime(full_path)
                        # 格式化时间为 YYYY-MM-DD HH:MM
                        formatted_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                        title = f"{title} ({formatted_time})"
            item = QTreeWidgetItem(parent_widget, [title])

            # 添加勾选框
            item.setCheckState(0, Qt.CheckState.Unchecked)
            
            if level == 3:
                item.setFlags(
                    (item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                    & ~Qt.ItemFlag.ItemIsDropEnabled
                )
            else:
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsDropEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                )

            # 使用节点的id属性作为node_id
            node_id = node.get("id")
            self.node_map[node_id] = node
            item.setData(0, Qt.ItemDataRole.UserRole, node_id)
            item.setToolTip(0, self._get_full_node_name_from_item(item))

            status = node.get("_status", "ok")
            file_path = node.get("file_path")

            # 检查是否有待合并的修改
            has_pending = False
            if self.workspace and node_id:
                has_pending = self.workspace.has_pending_modify(node_id)

            if level == 3:
                if has_pending:
                    # 有待合并修改，显示红色
                    item.setForeground(0, QColor("#FF4444"))
                elif (
                    file_path
                    and getattr(self, "_duplicate_paths", None)
                    and file_path in self._duplicate_paths
                ):
                    item.setForeground(0, QColor(NODE_ERROR))
                    node["_status"] = "duplicate_conflict"
                elif status == "missing":
                    item.setForeground(0, QColor(NODE_MISSING))
                elif status == "modified_externally":
                    item.setForeground(0, QColor(NODE_ERROR))
                else:
                    item.setForeground(0, QColor(NODE_NORMAL))
            else:
                if has_pending:
                    item.setForeground(0, QColor("#FF4444"))
                else:
                    item.setForeground(0, QColor(NODE_NORMAL))

            if "children" not in node:
                node["children"] = []

            self._build_novel_tree_ui(node["children"], item, level + 1)

        # 用父节点在树中的真实深度决定按钮文案，避免层级参数偏移导致按钮错误/缺失
        titles = {1: "+ 新增章...", 2: "+ 新增节...", 3: "+ 新增场景..."}
        if parent_widget == self.novel_tree:
            add_level = 1
        else:
            add_level = get_item_level(parent_widget) + 1
        btn_text = titles.get(add_level, "+ 新增节点...")

        add_btn = QTreeWidgetItem(parent_widget, [btn_text])
        add_btn.setForeground(0, QColor(NODE_ADD_BTN))
        add_btn.setToolTip(0, btn_text)
        add_btn.setFlags(
            add_btn.flags()
            & ~Qt.ItemFlag.ItemIsDragEnabled
            & ~Qt.ItemFlag.ItemIsDropEnabled
        )

    # ================= 树结构同步辅助 ================= #

    def _cleanup_tree_add_buttons(self: "NovelCreatorWindow", parent_item=None):
        target = (
            parent_item if parent_item else self.novel_tree.invisibleRootItem()
        )

        add_btn_index = -1
        for i in range(target.childCount()):
            child = target.child(i)
            if child.text(0).startswith("+"):
                add_btn_index = i
                break

        if add_btn_index != -1 and add_btn_index < target.childCount() - 1:
            add_btn = target.takeChild(add_btn_index)
            target.addChild(add_btn)

        for i in range(target.childCount()):
            child = target.child(i)
            if not child.text(0).startswith("+"):
                self._cleanup_tree_add_buttons(child)

    def sync_tree_data_from_ui(self: "NovelCreatorWindow"):
        if not self.workspace or self.outline_tree_data is None:
            return

        new_nodes: list = []
        root = self.novel_tree.invisibleRootItem()
        
        all_nodes_dict = {}
        
        def collect_all_nodes(nodes):
            for node in nodes:
                if "id" in node:
                    all_nodes_dict[node["id"]] = node
                if "children" in node:
                    collect_all_nodes(node["children"])
        
        if hasattr(self, '_pre_drag_tree_snapshot') and self._pre_drag_tree_snapshot:
            collect_all_nodes(self._pre_drag_tree_snapshot.get("nodes", []))
        else:
            collect_all_nodes(self.outline_tree_data.get("nodes", []))
        
        for i in range(root.childCount()):
            item = root.child(i)
            if item.text(0).startswith("+"):
                continue
            node_data = self._build_node_data_from_item(item, all_nodes_dict)
            if node_data:
                new_nodes.append(node_data)

        self.outline_tree_data["nodes"] = new_nodes
        self.workspace.save_outline_tree(self.outline_tree_data)
        self._auto_export_html_if_enabled("拖拽调整结构")
        
        if hasattr(self, '_pre_drag_tree_snapshot'):
            self._pre_drag_tree_snapshot = None
        
        self.log_console.append("系统通知：节点位置结构已自动保存。")

    def auto_fix_outline_for_scene_addition(self: "NovelCreatorWindow"):
        """一键修复：给没有“节”子节点的章（含序章）补默认节，便于新增场景。"""
        if not self.workspace:
            QMessageBox.information(
                self, "提示", "请先加载工作区。"  # type: ignore[arg-type]
            )
            return

        if self.outline_tree_data is None:
            self.outline_tree_data = self.workspace.load_outline_tree()
        nodes = self.outline_tree_data.setdefault("nodes", [])

        changed = False
        fixed_titles = []
        normalized_sections = 0
        fixed_section_ids = []

        for chapter in nodes:
            if not isinstance(chapter, dict):
                continue

            chapter_title = chapter.get("title", "未命名章")
            children = chapter.get("children")
            if not isinstance(children, list):
                children = []
                chapter["children"] = children
                changed = True

            # 核心修复：章下没有任何节时，自动补一个空节（包含序章）。
            if len(children) == 0:
                new_section = {
                    "id": str(uuid.uuid4()),
                    "title": "第1节",
                    "summary": "",
                    "children": [],
                    "_status": "ok",
                }
                children.append(new_section)
                fixed_titles.append(chapter_title)
                fixed_section_ids.append(new_section["id"])
                changed = True
                self._trace_add_btn(
                    f"repair_action: add default section for chapter='{chapter_title}'"
                )

            # 兼容老数据：保证每个“节”至少有 children 字段，便于显示“+新增场景...”
            for section in children:
                if isinstance(section, dict) and not isinstance(section.get("children"), list):
                    section["children"] = []
                    normalized_sections += 1
                    changed = True

        if not changed:
            QMessageBox.information(
                self, "修复结果", "未发现需要修复的章结构。当前已可直接新增节/场景。"  # type: ignore[arg-type]
            )
            self.log_console.append("结构修复：未发现异常，无需修改。")
            return

        self.workspace.save_outline_tree(self.outline_tree_data)
        self._refresh_novel_tree()

        # 修复后自动展开相关节点，确保“+ 新增场景...”立即可见
        root = self.novel_tree.invisibleRootItem()
        for i in range(root.childCount()):
            ch_item = root.child(i)
            if ch_item.text(0).startswith("+"):
                continue
            if ch_item.text(0) in fixed_titles:
                ch_item.setExpanded(True)
                for j in range(ch_item.childCount()):
                    sec_item = ch_item.child(j)
                    if sec_item.text(0).startswith("+"):
                        continue
                    sec_id = sec_item.data(0, Qt.ItemDataRole.UserRole)
                    if sec_id in fixed_section_ids:
                        sec_item.setExpanded(True)

        msg_parts = []
        if fixed_titles:
            msg_parts.append(f"已补齐空节的章：{', '.join(fixed_titles)}")
        if normalized_sections:
            msg_parts.append(f"已规范化节节点 children 字段：{normalized_sections} 处")
        final_msg = "；".join(msg_parts) if msg_parts else "结构修复完成。"

        self.log_console.append(f"结构修复完成：{final_msg}")
        QMessageBox.information(
            self, "修复完成", final_msg  # type: ignore[arg-type]
        )

    def _build_node_data_from_item(self: "NovelCreatorWindow", item, all_nodes_dict=None, original_parent_data=None):
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        
        # 【修复核心】：Qt内部拖拽父节点会导致子节点UserRole丢失。在此通过标题匹配进行回退恢复。
        if not node_id and original_parent_data and "children" in original_parent_data:
            item_text = item.text(0)
            for orig_child in original_parent_data["children"]:
                # 3级节点标题在UI中附加了修改时间，因此使用 startswith 或 in 进行匹配
                if item_text.startswith(orig_child.get("title", "")):
                    node_id = orig_child.get("id")
                    if node_id:
                        # 恢复ID并重新写回UI，防止后续流程再次丢失
                        item.setData(0, Qt.ItemDataRole.UserRole, node_id)
                        break
        
        node_data = None
        if all_nodes_dict and node_id in all_nodes_dict:
            node_data = all_nodes_dict[node_id]
        if not node_data:
            node_data = self.node_map.get(node_id)
        
        if not node_data:
            return None

        new_children: list = []
        for i in range(item.childCount()):
            child_item = item.child(i)
            if child_item.text(0).startswith("+"):
                continue
            # 递归调用时，将当前的 node_data 作为 original_parent_data 传给子节点
            child_data = self._build_node_data_from_item(child_item, all_nodes_dict, node_data)
            if child_data:
                new_children.append(child_data)

        node_data["children"] = new_children
        return node_data

    # ================= 小说大纲树交互 ================= #

    def on_novel_node_clicked(self: "NovelCreatorWindow", item, column):
        if not self.workspace:
            return

        if self.is_batch_generating:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "提示",
                "批量生成中，请先停止任务后再手动操作节点。",
            )
            return

        # === 【修复核心 1】：将未保存更改的检查逻辑移动到最前面 ===
        if self.current_editing_node:
            current_summary = self.summary_editor.toPlainText()
            current_content = self.content_editor.toPlainText()
            original_summary = getattr(self, "current_node_original_summary", "")
            original_content = getattr(self, "current_node_original_content", "")
            
            # 【修复核心 2】：统一换行符标准，防止 Windows/Mac 换行符差异导致的“假变动”误报
            is_summary_changed = current_summary.replace('\r\n', '\n') != original_summary.replace('\r\n', '\n')
            is_content_changed = current_content.replace('\r\n', '\n') != original_content.replace('\r\n', '\n')
            
            if is_summary_changed or is_content_changed:
                reply = QMessageBox.question(
                    self,  # type: ignore[arg-type]
                    "保存更改",
                    "当前节点有未保存的更改，是否在切换前保存？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                
                if reply == QMessageBox.StandardButton.Cancel:
                    # 【修复核心 3】：如果用户取消切换，强行把大纲树的高亮选择框拉回正在编辑的节点
                    if getattr(self, "current_editing_item", None):
                        self.novel_tree.setCurrentItem(self.current_editing_item)
                    return
                elif reply == QMessageBox.StandardButton.Yes:
                    self.save_current_node()

        # === 拦截并处理 “+新增” 按钮 ===
        if item.text(0).startswith("+"):
            parent_item = item.parent()
            parent_path = self._get_item_path(parent_item) if parent_item else "(ROOT)"
            self._trace_add_btn(
                f"click_add: text='{item.text(0)}', parent='{parent_path}', "
                f"parentLevel={get_item_level(parent_item) if parent_item else 0}"
            )
            if parent_item:
                parent_node_id = parent_item.data(0, Qt.ItemDataRole.UserRole)
                real_parent_node = self.node_map.get(parent_node_id)
                if real_parent_node is not None:
                    target_list = real_parent_node.setdefault("children", [])
                    parent_level = get_item_level(parent_item)
                    self.add_new_novel_node(target_list, parent_level + 1)
            else:
                if self.outline_tree_data is None:
                    self.outline_tree_data = {"nodes": []}
                target_list = self.outline_tree_data.setdefault("nodes", [])
                self.add_new_novel_node(target_list, 1)
            return

        # === 以下为原有的常规节点点击处理逻辑 ===
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        real_node = self.node_map.get(node_id)
        if not real_node:
            return

        # (保留你原来的后续代码，比如记录 current_editing_node、重置 Undo 等...)
        self.current_editing_node = real_node
        self.current_editing_item = item
        self.current_setting_path = None
        node_level = get_item_level(item)
        self._update_summary_header(self._get_full_node_name_from_item(item))
        
        # 重置回退缓冲区
        self.undo_stack = []
        self.btn_undo.setEnabled(False)
        
        # 保存当前节点的原始状态
        self.current_node_original_summary = real_node.get("summary", "")
        if node_level == 3:
            rel_path = real_node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                if os.path.exists(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        self.current_node_original_content = f.read()
                else:
                    self.current_node_original_content = f"# {real_node.get('title')}\n\n(文件尚未生成)"
            else:
                self.current_node_original_content = f"# {real_node.get('title')}\n\n"
        else:
            self.current_node_original_content = "（当前层级仅支持填写概要，正文请在底层的\u201c场景\u201d节点中生成/编写）"

        self.btn_delete.setEnabled(not bool(real_node.get("children")))

        # 1. 加载概要
        self.summary_editor.setText(real_node.get("summary", ""))
        self.summary_editor.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_regenerate_summary.setEnabled(True)

        # 2. 加载正文 (仅对第3级开放)
        if node_level == 3:
            rel_path = real_node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                if os.path.exists(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        self.content_editor.setText(f.read())
                else:
                    self.content_editor.setText(
                        f"# {real_node.get('title')}\n\n(文件尚未生成)"
                    )
            else:
                self.content_editor.setText(
                    f"# {real_node.get('title')}\n\n"
                    "(尚未配置正文路径，在此输入内容并保存后将自动生成)"
                )

            self.content_editor.setEnabled(True)
            self.btn_generate.setEnabled(True)
            self.btn_rewrite.setEnabled(True)
        else:
            self.content_editor.setText(
                "（当前层级仅支持填写概要，正文请在底层的"
                "\u201c场景\u201d节点中生成/编写）"
            )
            self.content_editor.setEnabled(False)
            self.btn_generate.setEnabled(False)
            self.btn_rewrite.setEnabled(False)

        self.update_word_count()

    def add_new_novel_node(self: "NovelCreatorWindow", target_list: list, level: int):
        titles = {1: "章", 2: "节", 3: "场景"}
        node_type = titles.get(level, "节点")

        title, ok = QInputDialog.getText(
            self, f"新增{node_type}", f"请输入新{node_type}名称:"  # type: ignore[arg-type]
        )
        if ok and title.strip():
            title = title.strip()

            new_node = {
                "id": str(uuid.uuid4()),
                "title": title,
                "summary": "",
                "children": [],
                "_status": "ok",
            }

            if level == 3:
                file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
                initial_content = f"# {title}\n\n请在此输入正文...\n"
                try:
                    initial_md5 = self.workspace.save_markdown_file(
                        file_name, initial_content
                    )
                    new_node["file_path"] = file_name
                    new_node["md5"] = initial_md5
                except Exception as e:
                    QMessageBox.critical(
                        self,  # type: ignore[arg-type]
                        "错误",
                        f"创建本地物理文件失败: {e}",
                    )
                    return

            target_list.append(new_node)

            try:
                self.workspace.save_outline_tree(self.outline_tree_data)
                self.log_console.append(f"成功添加{node_type}: {title}")
                self._auto_export_html_if_enabled(f"新增{node_type}")
                self.refresh_ui_from_workspace()

                root = self.novel_tree.invisibleRootItem()
                new_item = find_item_by_data(root, new_node["id"])
                if new_item:
                    parent = new_item.parent()
                    while parent:
                        parent.setExpanded(True)
                        parent = parent.parent()
                    self.novel_tree.setCurrentItem(new_item)
                    self.novel_tree.scrollToItem(new_item)
            except Exception as e:
                QMessageBox.critical(
                    self, "错误", f"保存大纲 JSON 失败:\n{e}"  # type: ignore[arg-type]
                )

    def rename_current_node(self: "NovelCreatorWindow"):
        item = self.novel_tree.currentItem()
        if not item or item.text(0).startswith("+"):
            return

        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        real_node = self.node_map.get(node_id)
        if not real_node:
            return

        old_title = real_node.get("title", "")
        dialog = RenameNodeDialog(
            self, "重命名节点", "请输入新的节点名称:", default_text=old_title  # type: ignore[arg-type]
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_title = dialog.get_text()
            if new_title.strip() and new_title.strip() != old_title:
                clean_title = new_title.strip()
                real_node["title"] = clean_title
                item.setText(0, clean_title)

                if self.workspace and self.outline_tree_data:
                    self.workspace.save_outline_tree(self.outline_tree_data)
                    self._auto_export_html_if_enabled("重命名节点")
                    self.log_console.append(
                        f"\U0001f504 节点已重命名: 【{old_title}】 -> 【{clean_title}】"
                        " (已自动保存大纲)"
                    )

    # ================= 小说大纲右键菜单 ================= #

    def expand_all_novel_nodes(self: "NovelCreatorWindow"):
        if not self.workspace:
            return
        self.novel_tree.expandAll()

    def collapse_all_novel_nodes(self: "NovelCreatorWindow"):
        if not self.workspace:
            return
        self.novel_tree.collapseAll()

    def expand_single_novel_node(self: "NovelCreatorWindow", item):
        if not item or item.text(0).startswith("+"):
            return
        if item.childCount() > 0:
            item.setExpanded(True)

    def collapse_single_novel_node(self: "NovelCreatorWindow", item):
        if not item or item.text(0).startswith("+"):
            return
        if item.childCount() > 0:
            item.setExpanded(False)

    def _collect_checked_novel_nodes(self: "NovelCreatorWindow"):
        """收集所有打钩节点（按树上的显示顺序）。"""
        checked_nodes = []
        root = self.novel_tree.invisibleRootItem()

        def collect_checked_items(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.text(0).startswith("+"):
                    continue
                if child.checkState(0) == Qt.CheckState.Checked:
                    node_id = child.data(0, Qt.ItemDataRole.UserRole)
                    node = self.node_map.get(node_id) if node_id else None
                    if node:
                        checked_nodes.append((child, node))
                collect_checked_items(child)

        collect_checked_items(root)
        return checked_nodes

    def show_novel_context_menu(self: "NovelCreatorWindow", position):
        if not self.workspace:
            return

        menu = QMenu()
        
        # 获取当前点击的节点
        item = self.novel_tree.itemAt(position)
        real_node = None
        level = 0
        node_id = None
        has_pending = False
        
        # 检查是否有被勾选的节点
        checked_nodes = self._collect_checked_novel_nodes()
        checked_level3_nodes = [
            (checked_item, checked_node)
            for checked_item, checked_node in checked_nodes
            if get_item_level(checked_item) == 3
        ]
        
        # 如果有被勾选的节点，添加批量修改选项
        if checked_nodes:
            batch_modify_action = menu.addAction("📝 批量修改勾选节点")
            batch_modify_action.triggered.connect(lambda: self.open_batch_modify_request_dialog(checked_nodes))
            if checked_level3_nodes:
                batch_generate_action = menu.addAction("🚀 依次生成勾选节点正文")
                batch_generate_action.triggered.connect(
                    lambda: self.start_batch_generate_checked_nodes(checked_nodes)
                )
                batch_summary_regen_action = menu.addAction("🩺 依次校验勾选节点概要")
                batch_summary_regen_action.triggered.connect(
                    lambda: self.start_batch_regenerate_summaries_checked_nodes(checked_nodes)
                )
            menu.addSeparator()
        
        if item and not item.text(0).startswith("+"):
            node_id = item.data(0, Qt.ItemDataRole.UserRole)
            real_node = self.node_map.get(node_id)
            if real_node:
                level = get_item_level(item)
                has_pending = self.workspace.has_pending_modify(node_id) if node_id else False

                select_all_action = menu.addAction("✅ 全选")
                select_all_action.triggered.connect(self.select_all_nodes)
                select_none_action = menu.addAction("⬜ 全不选")
                select_none_action.triggered.connect(self.select_none_nodes)
                expand_all_action = menu.addAction("⬇️ 展开全部")
                expand_all_action.triggered.connect(self.expand_all_novel_nodes)
                collapse_all_action = menu.addAction("⬆️ 收起全部")
                collapse_all_action.triggered.connect(self.collapse_all_novel_nodes)
                menu.addSeparator()

                if item.childCount() > 0:
                    expand_action = menu.addAction("⬇️ 展开此节点")
                    expand_action.triggered.connect(lambda checked=False, target=item: self.expand_single_novel_node(target))
                    collapse_action = menu.addAction("⬆️ 收起此节点")
                    collapse_action.triggered.connect(lambda checked=False, target=item: self.collapse_single_novel_node(target))
                    if item.isExpanded():
                        expand_action.setEnabled(False)
                    else:
                        collapse_action.setEnabled(False)
                    menu.addSeparator()
                
                # 只对1级和2级节点添加增加子节点的选项
                if level in [1, 2]:
                    add_children_action = menu.addAction(
                        "\U0001f4a1 根据要求增加子节点"
                    )
                    add_children_action.triggered.connect(lambda: self.open_add_children_dialog(real_node, level))
                    menu.addSeparator()

                cut_action = menu.addAction("✂️ 剪切节点")
                cut_action.triggered.connect(
                    lambda checked=False, target=item: self.cut_current_node_to_clipboard(target)
                )
                copy_action = menu.addAction("📋 复制节点")
                copy_action.triggered.connect(
                    lambda checked=False, target=item: self.copy_current_node_to_clipboard(target)
                )
                paste_action = menu.addAction("📥 粘贴为子节点")
                paste_action.triggered.connect(
                    lambda checked=False, target=item: self.paste_node_from_clipboard(target)
                )
                can_paste, _ = self._can_paste_to_item(
                    item, self._get_valid_node_clipboard_payload()
                )
                paste_action.setEnabled(can_paste)
                menu.addSeparator()
                
                # 只对3级节点添加修改内容的选项
                if level == 3:
                    regen_summary_action = menu.addAction("🧪 校验并修正概要（基于当前正文）")
                    regen_summary_action.triggered.connect(
                        lambda checked=False, target=item: self.regenerate_summary_for_tree_item(target)
                    )
                    menu.addSeparator()
                    if has_pending:
                        # 有待合并修改时，"修改内容"变灰，"进行合并"可用
                        modify_action = menu.addAction("✏️ 修改内容")
                        modify_action.setEnabled(False)
                        
                        merge_action = menu.addAction("🔄 进行合并")
                        merge_action.triggered.connect(lambda: self.open_merge_dialog(node_id, real_node))
                        
                        discard_action = menu.addAction("❌ 丢弃修改")
                        discard_action.triggered.connect(lambda: self.discard_pending_modify(node_id))
                    else:
                        # 无待合并修改时，保持单节点“修改内容”行为不变
                        modify_action = menu.addAction("✏️ 修改内容")
                        modify_action.triggered.connect(
                            lambda: self.open_modify_request_dialog(real_node, node_id)
                        )
                    split_action = menu.addAction("🧩 按转折点拆分节点")
                    split_action.triggered.connect(
                        lambda: self.open_split_scene_dialog(real_node, node_id)
                    )
                    if has_pending:
                        split_action.setEnabled(False)
                
                    open_file_action = menu.addAction("📁 在文件管理器中打开文件位置")
                    open_file_action.triggered.connect(
                        lambda: self._open_node_file_location(real_node)
                    )
                    rel_path = real_node.get("file_path")
                    if not rel_path or not os.path.exists(os.path.join(self.workspace.text_path, rel_path)):
                        open_file_action.setEnabled(False)
                
                    menu.addSeparator()

        gen_outline_action = menu.addAction(
            "\U0001f4a1 结合当前点子与左侧勾选设定，自动生成大纲"
        )
        gen_outline_action.triggered.connect(self.open_outline_building_dialog)

        if item and not item.text(0).startswith("+") and real_node:
            menu.addSeparator()
            sync_summary_action = menu.addAction("\U0001f504 校验并同步该节点及子节点概要 (生成Diff)")
            sync_summary_action.triggered.connect(lambda: self.start_summary_sync(real_node, level))

        menu.exec(self.novel_tree.viewport().mapToGlobal(position))

    def _open_node_file_location(self: "NovelCreatorWindow", node: dict):
        rel_path = node.get("file_path")
        if not rel_path:
            return
        full_path = os.path.abspath(os.path.join(self.workspace.text_path, rel_path))
        if not os.path.exists(full_path):
            return
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", full_path])
        else:
            subprocess.Popen(["open", os.path.dirname(full_path)])

    def start_summary_sync(self: "NovelCreatorWindow", target_node: dict, level: int):
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。") # type: ignore[arg-type]
            return
            
        # 构建一个自定义的确认对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("确认执行校验同步")
        dialog.setMinimumWidth(450)
        
        layout = QVBoxLayout()
        
        msg_label = QLabel(
            f"即将遍历校验节点【{target_node.get('title')}】及其所有子节点。\n\n"
            "默认情况下，系统只会生成两份临时 Markdown 文件供您进行 Diff 比较，不会修改原数据。"
        )
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)
        
        # 新增强制覆盖模式复选框
        force_cb = QCheckBox("开启【强制覆盖模式】（仅对底层场景生效）")
        force_cb.setToolTip("勾选后，所有3级场景节点的概要将直接被新版本覆盖。1级(章)、2级(节)节点过于复杂，依然只生成 Diff 供人工确认。")
        # 默认不选中
        force_cb.setChecked(False)
        layout.addWidget(force_cb)
        
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定开始")
        cancel_btn = QPushButton("取消")
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        
        dialog.setLayout(layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            force_mode = force_cb.isChecked()
            mode_str = "【自动覆盖3级场景】" if force_mode else "【仅生成Diff】"
            
            self.log_console.append(f"<font color='cyan'>启动概要同步校验引擎 {mode_str}，根节点：{target_node.get('title')}...</font>")
            self.btn_save.setEnabled(False)
            
            self.sync_worker = SummarySyncWorker(
                target_node=target_node,
                level=level,
                llm_client=self.llm_client,
                workspace_manager=self.workspace,
                force_mode=force_mode  # 传入勾选状态
            )
            self.sync_worker.progress_signal.connect(lambda msg: self.log_console.append(f"<font color='gray'>{msg}</font>"))
            self.sync_worker.success_signal.connect(self.on_summary_sync_success)
            self.sync_worker.error_signal.connect(self.on_summary_sync_error)
            self.sync_worker.start()

    def on_summary_sync_success(self: "NovelCreatorWindow", before_path: str, after_path: str, level3_updates: dict):
        updated_count = 0
        
        # 如果有需要强制更新的3级节点，直接覆盖内存中的大纲树并保存
        if level3_updates:
            for node_id, new_summary in level3_updates.items():
                if node_id in self.node_map:
                    self.node_map[node_id]["summary"] = new_summary
                    updated_count += 1
            
            if updated_count > 0 and self.workspace and self.outline_tree_data:
                self.workspace.save_outline_tree(self.outline_tree_data)
                
                # 如果当前UI右侧正文编辑器正在编辑刚才被覆盖的节点，自动刷新其内容
                if self.current_editing_node and self.current_editing_node.get("id") in level3_updates:
                    self.summary_editor.setText(self.current_editing_node["summary"])

        self.btn_save.setEnabled(True)
        self.log_console.append("<font color='green'><b>✅ 概要校验与同步完成！</b></font>")
        
        if updated_count > 0:
            self.log_console.append(f"<font color='yellow'>已自动覆盖 {updated_count} 个3级场景的概要。</font>")
            
        self.log_console.append(f"旧版概要文件: <a href='file:///{before_path}'>{before_path}</a>")
        self.log_console.append(f"新版概要文件: <a href='file:///{after_path}'>{after_path}</a>")
        
        # 弹窗提示
        msg = "大纲概要校验已完成！\n\n"
        if updated_count > 0:
            msg += f"已为您自动覆盖 {updated_count} 个3级场景节点的概要，大纲已自动保存。\n\n"
            
        msg += (
            "系统已将所有节点（含1、2、3级）修改前后的汇总输出为以下文件：\n"
            f"1. {os.path.basename(before_path)}\n2. {os.path.basename(after_path)}\n\n"
            "（请前往工作区的 temp_diff 文件夹查阅章/节的 Diff 并手动合并）"
        )
        
        QMessageBox.information(self, "处理完成", msg) # type: ignore[arg-type]
        self._send_system_notification(
            "概要校验完成",
            "概要校验与同步任务已完成。",
        )

    def on_summary_sync_error(self: "NovelCreatorWindow", error_msg: str):
        self.btn_save.setEnabled(True)
        self.log_console.append(f"<font color='red'>❌ 概要校验失败: {error_msg}</font>")
        QMessageBox.critical(self, "错误", f"概要校验过程中发生异常:\n{error_msg}") # type: ignore[arg-type]ype: ignore[arg-type]

    def open_outline_building_dialog(self: "NovelCreatorWindow"):
        if not self.llm_client:
            QMessageBox.warning(
                self, "未配置", "请先在设置中配置大模型 API。"  # type: ignore[arg-type]
            )
            return

        dialog = IdeaInputDialog(
            self,  # type: ignore[arg-type]
            "自动生成小说大纲",
            "请输入关于大纲的剧情发展点子、主线走向或期待的章节结构：\n"
            "（左侧打钩的世界观设定也会作为参考上下文发送给AI）",
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            idea = dialog.get_text()
            if idea.strip():
                self.log_console.append("启动大纲生成引擎。后台处理中，请稍候...")
                self.btn_save.setEnabled(False)

                checked_paths = self.get_checked_settings()
                text_cfg = (self.config or {}).get("text_api", {})
                builder = ContextBuilder(
                    self.workspace,
                    model_context_size=text_cfg.get("model_context_size"),
                    model_capabilities=text_cfg.get("model_capabilities", []),
                    compression_profile=text_cfg.get("world_context_compression_profile", "balanced"),
                )
                settings_text = builder._build_settings_text(checked_paths)

                default_prompt = ""
                prompt_tpl = self._get_or_create_prompt_template(
                    "outline_build.txt", default_prompt, "根据点子和设定生成大纲"
                )

                self.ob_thread = OutlineBuildingThread(
                    llm_client=self.llm_client,
                    idea=idea.strip(),
                    settings_text=settings_text,
                    prompt_tpl=prompt_tpl,
                )
                self.ob_thread.progress_signal.connect(
                    lambda msg: self.log_console.append(
                        f"<font color='cyan'>{msg}</font>"
                    )
                )
                self.ob_thread.success_signal.connect(
                    self.on_outline_building_success
                )
                self.ob_thread.error_signal.connect(self.on_outline_building_error)
                self.ob_thread.start()

    def on_outline_building_success(self: "NovelCreatorWindow", outline_data):
        def process_nodes(nodes, level):
            for node in nodes:
                if "id" not in node:
                    node["id"] = str(uuid.uuid4())
                node["_status"] = "ok"
                if level == 3:
                    node["file_path"] = f"场景_{uuid.uuid4().hex[:8]}.md"
                    node["children"] = []
                else:
                    process_nodes(node.get("children", []), level + 1)

        new_nodes = outline_data.get("nodes", [])
        process_nodes(new_nodes, 1)

        if "nodes" not in self.outline_tree_data:
            self.outline_tree_data["nodes"] = []

        self.outline_tree_data["nodes"].extend(new_nodes)

        try:
            with open(
                self.workspace.tree_json_file, "w", encoding="utf-8"
            ) as f:
                json.dump(self.outline_tree_data, f, ensure_ascii=False, indent=4)

            self.log_console.append(
                "<b><font color='green'>"
                "\U0001f389 大纲生成完毕！已追加到目录树末尾。"
                "</font></b>"
            )
            self._auto_export_html_if_enabled("自动生成大纲")
            self.refresh_ui_from_workspace()
            self._send_system_notification(
                "大纲生成完成",
                "大纲生成任务已完成。",
            )
        except Exception as e:
            QMessageBox.critical(
                self, "错误", f"保存大纲 JSON 失败:\n{e}"  # type: ignore[arg-type]
            )
        finally:
            self.btn_save.setEnabled(True)

    def open_add_children_dialog(self: "NovelCreatorWindow", target_node: dict, level: int):
        """打开增加子节点的对话框"""
        if not self.llm_client:
            QMessageBox.warning(
                self, "未配置", "请先在设置中配置大模型 API。"  # type: ignore[arg-type]
            )
            return

        class AddChildrenDialog(QDialog):
            def __init__(self, parent):
                super().__init__(parent)
                self.setWindowTitle("根据要求增加子节点")
                self.setMinimumWidth(500)
                
                layout = QVBoxLayout()
                
                # 输入要求
                layout.addWidget(QLabel("请输入子节点的生成要求："))
                self.requirement_edit = QTextEdit()
                self.requirement_edit.setPlaceholderText("例如：生成3个关于主角在森林中冒险的场景")
                layout.addWidget(self.requirement_edit)
                
                # 输入节点个数
                layout.addWidget(QLabel("请输入要生成的子节点个数："))
                self.count_edit = QLineEdit()
                self.count_edit.setPlaceholderText("输入数字，如：3")
                layout.addWidget(self.count_edit)
                
                # 按钮
                btn_layout = QHBoxLayout()
                self.ok_btn = QPushButton("确定")
                self.cancel_btn = QPushButton("取消")
                btn_layout.addStretch()
                btn_layout.addWidget(self.ok_btn)
                btn_layout.addWidget(self.cancel_btn)
                layout.addLayout(btn_layout)
                
                self.ok_btn.clicked.connect(self.accept)
                self.cancel_btn.clicked.connect(self.reject)
                
                self.setLayout(layout)
            
            def get_requirement(self):
                return self.requirement_edit.toPlainText().strip()
            
            def get_count(self):
                try:
                    return int(self.count_edit.text().strip())
                except:
                    return 0

        dialog = AddChildrenDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            requirement = dialog.get_requirement()
            count = dialog.get_count()
            
            if not requirement:
                QMessageBox.warning(
                    self, "输入错误", "请输入生成要求。"  # type: ignore[arg-type]
                )
                return
            
            if count <= 0:
                QMessageBox.warning(
                    self, "输入错误", "请输入有效的节点个数。"  # type: ignore[arg-type]
                )
                return
            
            self.generate_children_nodes(target_node, level, requirement, count)

    def generate_children_nodes(self: "NovelCreatorWindow", target_node: dict, level: int, requirement: str, count: int):
        """根据要求生成子节点"""
        node_title = target_node.get("title", "未知节点")
        self.log_console.append(f"开始生成【{node_title}】的子节点...")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("正在构建上下文并发送请求到LLM生成子节点...")
        
        # 禁用相关按钮
        self.btn_save.setEnabled(False)
        
        try:
            # 构建上下文
            text_cfg = (self.config or {}).get("text_api", {})
            builder = ContextBuilder(
                self.workspace,
                model_context_size=text_cfg.get("model_context_size"),
                model_capabilities=text_cfg.get("model_capabilities", []),
                compression_profile=text_cfg.get("world_context_compression_profile", "balanced"),
            )
            checked_paths = self.get_checked_settings()
            
            # 获取父节点和同级节点
            parents = []
            siblings = []
            
            def find_node_info(current_nodes, current_path):
                nonlocal parents, siblings
                for node in current_nodes:
                    path = current_path + [node]
                    if node is target_node:
                        parents = current_path
                        # 收集同级节点
                        for sibling in current_nodes:
                            if sibling is not target_node:
                                siblings.append(sibling)
                        return True
                    if node.get("children"):
                        if find_node_info(node.get("children", []), path):
                            return True
                return False
            
            find_node_info(self.outline_tree_data.get("nodes", []), [])
            
            # 构建上下文文本
            context_blocks = []
            
            # 父节点信息
            if parents:
                context_blocks.append("【父节点信息】")
                for p in parents:
                    title = p.get("title", "未命名")
                    summary = p.get("summary", "").strip() or "(该层级无概要)"
                    context_blocks.append(f"<{title}> 概要:\n{summary}\n")
            
            # 同级节点信息
            if siblings:
                context_blocks.append("【同级节点信息】")
                for sibling in siblings:
                    title = sibling.get("title", "未命名")
                    summary = sibling.get("summary", "").strip() or "(暂无概要)"
                    context_blocks.append(f"<{title}> 概要:\n{summary}\n")
            
            # 当前节点信息
            context_blocks.append("【当前节点信息】")
            current_summary = target_node.get("summary", "").strip() or "(暂无概要)"
            context_blocks.append(f"<{node_title}> 概要:\n{current_summary}\n")
            
            context_text = "\n".join(context_blocks) if context_blocks else "（无相关上下文）"
            
            # 构建提示词
            child_type = "节" if level == 1 else "场景"
            prompt = f"""
你是一个专业的小说创作者。请根据提供的上下文信息，为指定的节点生成子节点。

### 一、 上下文信息
{context_text}

### 二、 生成要求与核心要素规范（绝对红线）
1. 请为节点【{node_title}】生成 {count} 个 {child_type} 子节点，需围绕此要求展开：{requirement}
2. 每个子节点需要包含标题和概要内容。如果当前节点【{node_title}】没有概要，请一并生成其概要内容。
3. **【核心概要提取强制规范】**：你生成的**所有概要**（包括补充的父节点概要和新建的子节点概要），都必须结构严谨，明确写出以下信息：
   - **时间与事件交互**：明确交代该剧情发生时的【时间】、【具体地点】、【出场人物】，以及明确的【事件交互】（谁和谁具体完成了什么事）。
   - **状态与变化**：必须体现核心人物的【心境/情绪转变】、【衣着/装备/外貌的改变】，或【队伍成员的增减变化】。
   - **场景与轨迹**：若发生位置转移，需明确指出【从哪里移动到了哪里】。
4. 生成的子节点应该与上下文信息逻辑连贯。只需要生成概要内容，不需要生成正文。

### 三、 输出格式
请严格按照以下 JSON 格式输出：
{{
  "current_node_summary": "当前节点的概要内容（如果需要生成，也必须符合上述核心要素规范）",
  "children": [
    {{
      "title": "子节点1标题",
      "summary": "子节点1概要（必须符合上述核心要素规范）"
    }},
    {{
      "title": "子节点2标题",
      "summary": "子节点2概要（必须符合上述核心要素规范）"
    }}
  ]
}}

请确保输出是有效的 JSON 格式，不要包含任何其他内容。
"""
            
            self.log_console.append("提示词构建完成，准备发送请求...")
            
            # 发送请求
            self.generate_thread = GenerateTaskThread(self.llm_client, prompt)
            self.generate_thread.progress_signal.connect(
                lambda msg: self._on_llm_progress(msg)
            )
            self.generate_thread.success_signal.connect(lambda result: self.on_children_generate_success(result, target_node))
            self.generate_thread.error_signal.connect(self.on_children_generate_error)
            self.generate_thread.start()
            
            self.log_console.append("生成线程已启动，等待LLM响应...")
        except Exception as e:
            self.log_console.append(f"<font color='red'>生成子节点时发生错误: {e}</font>")
            # 在状态栏显示错误信息
            statusbar = self.statusBar()
            if statusbar:
                statusbar.showMessage(f"生成子节点时发生错误: {e[:50]}...", 3000)
            # 恢复按钮状态
            self.btn_save.setEnabled(True)

    def on_children_generate_success(self: "NovelCreatorWindow", result: str, target_node: dict):
        """处理子节点生成成功的回调"""
        try:
            data = json.loads(result)
            
            # 更新当前节点的概要（如果有）
            if "current_node_summary" in data and data["current_node_summary"]:
                target_node["summary"] = data["current_node_summary"]
            
            # 添加子节点
            if "children" in data and isinstance(data["children"], list):
                children = target_node.setdefault("children", [])
                for child in data["children"]:
                    if "title" in child:
                        new_child = {
                                "id": str(uuid.uuid4()),
                                "title": child["title"],
                                "summary": child.get("summary", ""),
                                "children": [],
                                "_status": "ok"
                            }
                        # 如果是生成场景节点，添加文件路径
                        if len(target_node.get("children", [])) + len(data["children"]) <= 3:
                            if target_node.get("children", []) and len(target_node["children"]) == 2:
                                # 第三个子节点，应该是场景
                                file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
                                new_child["file_path"] = file_name
                        children.append(new_child)
            
            # 保存大纲
            self.workspace.save_outline_tree(self.outline_tree_data)
            self.log_console.append("子节点生成成功！")
            self._auto_export_html_if_enabled("生成子节点")
            
            # 在状态栏显示信息
            statusbar = self.statusBar()
            if statusbar:
                statusbar.showMessage("子节点生成成功，已更新大纲树", 3000)
            
            # 刷新 UI
            self.refresh_ui_from_workspace()
            target_title = target_node.get("title", "当前节点")
            self._send_system_notification(
                "子节点生成完成",
                f"《{target_title}》子节点生成任务已完成。",
            )
        except Exception as e:
            self.log_console.append(f"<font color='red'>处理生成结果失败: {e}</font>")
            # 在状态栏显示错误信息
            statusbar = self.statusBar()
            if statusbar:
                statusbar.showMessage(f"处理生成结果失败: {e[:50]}...", 3000)
        finally:
            self.btn_save.setEnabled(True)
            self.generate_thread = None

    def on_children_generate_error(self: "NovelCreatorWindow", error_msg: str):
        """处理子节点生成错误的回调"""
        self.log_console.append(
            f"<font color='red'>子节点生成失败: {error_msg}</font>"
        )
        # 在状态栏显示错误信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage(f"子节点生成失败: {error_msg[:50]}...", 3000)
        # 恢复按钮状态
        self.btn_save.setEnabled(True)
        self.generate_thread = None

    def on_outline_building_error(self: "NovelCreatorWindow", err_msg: str):
        self.log_console.append(
            f"<font color='red'>大纲生成失败: {err_msg}</font>"
        )
        QMessageBox.warning(
            self, "生成失败", f"大纲生成流程中断:\n{err_msg}"  # type: ignore[arg-type]
        )
        self.btn_save.setEnabled(True)

    # ================= 场景按转折点拆分 ================= #

    def open_split_scene_dialog(
        self: "NovelCreatorWindow", real_node: dict, node_id: str
    ):
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return
        if not real_node or not node_id:
            QMessageBox.warning(self, "提示", "未找到可拆分的节点。")
            return
        if self.workspace.has_pending_modify(node_id):
            QMessageBox.warning(self, "提示", "该节点已有待合并修改，请先处理后再拆分。")
            return

        class SplitSceneDialog(QDialog):
            def __init__(self, parent, node_title: str):
                super().__init__(parent)
                self.setWindowTitle(f"拆分节点 - {node_title}")
                self.setMinimumWidth(560)

                layout = QVBoxLayout()
                layout.addWidget(QLabel("目标转折点数量（X）："))

                self.turning_points_spin = QSpinBox()
                self.turning_points_spin.setRange(1, 20)
                self.turning_points_spin.setValue(3)
                self.turning_points_spin.setSuffix(" 个")
                layout.addWidget(self.turning_points_spin)

                layout.addWidget(
                    QLabel(
                        "补充要求（可选）：\n"
                        "例如：优先按冲突升级节点拆分；保留原文风格与对白节奏。"
                    )
                )
                self.requirement_edit = QTextEdit()
                self.requirement_edit.setPlaceholderText("可留空")
                self.requirement_edit.setMinimumHeight(110)
                layout.addWidget(self.requirement_edit)

                btn_layout = QHBoxLayout()
                btn_layout.addStretch()
                cancel_btn = QPushButton("取消")
                ok_btn = QPushButton("开始拆分")
                ok_btn.setStyleSheet(
                    "background-color: #2196F3; color: white; font-weight: bold;"
                )
                cancel_btn.clicked.connect(self.reject)
                ok_btn.clicked.connect(self.accept)
                btn_layout.addWidget(cancel_btn)
                btn_layout.addWidget(ok_btn)
                layout.addLayout(btn_layout)
                self.setLayout(layout)

            def get_turning_points(self) -> int:
                return int(self.turning_points_spin.value())

            def get_requirement(self) -> str:
                return self.requirement_edit.toPlainText().strip()

        dialog = SplitSceneDialog(self, real_node.get("title", "未知节点"))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            turning_points = dialog.get_turning_points()
            requirement = dialog.get_requirement()
            self.start_split_scene_node(real_node, node_id, turning_points, requirement)

    def start_split_scene_node(
        self: "NovelCreatorWindow",
        real_node: dict,
        node_id: str,
        turning_points: int,
        requirement: str = "",
    ):
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return
        if turning_points <= 0:
            QMessageBox.warning(self, "输入错误", "转折点数量必须大于 0。")
            return

        if not hasattr(self, "splitting_nodes"):
            self.splitting_nodes = set()
        if node_id in self.splitting_nodes:
            QMessageBox.warning(self, "提示", "该节点正在拆分中，请稍候。")
            return
        if self.workspace.has_pending_modify(node_id):
            QMessageBox.warning(self, "提示", "该节点已有待合并修改，请先处理。")
            return

        rel_path = real_node.get("file_path")
        original_text = ""
        if rel_path:
            full_path = os.path.join(self.workspace.text_path, rel_path)
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    original_text = f.read()
        if not original_text.strip():
            QMessageBox.warning(self, "提示", "该节点正文为空，无法拆分。")
            return

        node_title = real_node.get("title", "未命名场景")
        node_summary = real_node.get("summary", "")
        self.splitting_nodes.add(node_id)
        self.btn_save.setEnabled(False)
        self.log_console.append(
            f"<font color='cyan'>开始拆分节点【{node_title}】，目标转折点：{turning_points}...</font>"
        )

        requirement_block = requirement.strip() or "（无额外要求）"
        prompt = f"""你是资深小说结构编辑。请把下面“单个场景正文”按剧情转折点拆分为多个连贯片段，用于创建同级场景节点。

### 拆分目标
1. 目标转折点数量 X = {turning_points}（尽量接近，可在内容不足时略微调整，但至少拆成 2 段）
2. 优先在“事件目标变化 / 冲突升级 / 场景切换 / 时间明显跳跃 / 关系状态变化”处切分
3. 每段必须是原文连续片段，不得改写事实，不得凭空补剧情
4. 各段拼接后应与原文信息等价，避免遗漏关键内容

### 额外要求
{requirement_block}

### 原节点信息
- 标题：{node_title}
- 概要：{node_summary or "(无概要)"}

### 原正文
{original_text}

### 输出格式（仅 JSON，不要 Markdown，不要解释）
{{
  "segments": [
    {{
      "title": "场景标题1",
      "summary": "该段概要",
      "content": "该段正文（markdown）"
    }}
  ]
}}
"""

        self.split_scene_thread = GenerateTaskThread(self.llm_client, prompt)
        self.split_scene_thread.progress_signal.connect(
            lambda msg: self._on_llm_progress(msg)
        )
        self.split_scene_thread.success_signal.connect(
            lambda result: self.on_split_scene_success(
                result, real_node, node_id, original_text, turning_points
            )
        )
        self.split_scene_thread.error_signal.connect(
            lambda error: self.on_split_scene_error(error, node_id)
        )
        self.split_scene_thread.start()

    def _normalize_scene_markdown(
        self: "NovelCreatorWindow", title: str, content: str
    ) -> str:
        body = (content or "").strip()
        if not body:
            body = "（该拆分段为空，建议手动补充）"
        if body.startswith("#"):
            return body
        return f"# {title}\n\n{body}\n"

    def _build_split_nodes_from_segments(
        self: "NovelCreatorWindow",
        real_node: dict,
        segments: list,
    ) -> list[dict]:
        split_nodes: list[dict] = []
        old_title = real_node.get("title", "未命名场景")
        old_rel_path = real_node.get("file_path")

        for idx, seg in enumerate(segments):
            title = str(seg.get("title") or "").strip() or f"{old_title}-片段{idx + 1}"
            summary = str(seg.get("summary") or "").strip()
            content = self._normalize_scene_markdown(title, str(seg.get("content") or ""))

            if idx == 0:
                node = real_node
                target_file = old_rel_path or f"场景_{uuid.uuid4().hex[:8]}.md"
                new_md5 = self.workspace.save_markdown_file(target_file, content)
                node["title"] = title
                node["summary"] = summary
                node["file_path"] = target_file
                node["md5"] = new_md5
                node["children"] = []
                node["_status"] = "ok"
                split_nodes.append(node)
                continue

            file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
            md5 = self.workspace.save_markdown_file(file_name, content)
            split_nodes.append(
                {
                    "id": str(uuid.uuid4()),
                    "title": title,
                    "summary": summary,
                    "children": [],
                    "_status": "ok",
                    "file_path": file_name,
                    "md5": md5,
                }
            )

        return split_nodes

    def on_split_scene_success(
        self: "NovelCreatorWindow",
        result: str,
        real_node: dict,
        node_id: str,
        original_text: str,
        turning_points: int,
    ):
        if hasattr(self, "splitting_nodes") and node_id in self.splitting_nodes:
            self.splitting_nodes.remove(node_id)
        try:
            data = json.loads(clean_json_string(result))
            segments = data.get("segments", []) if isinstance(data, dict) else []
            if not isinstance(segments, list):
                raise ValueError("返回格式不正确：segments 不是数组。")
            valid_segments = [
                seg
                for seg in segments
                if isinstance(seg, dict) and str(seg.get("content") or "").strip()
            ]
            if len(valid_segments) < 2:
                raise ValueError("拆分结果不足 2 段，无法执行节点拆分。")

            found_node, parent_list, idx = self._find_node_and_parent_list_by_id(node_id)
            if found_node is None or parent_list is None or idx < 0:
                raise ValueError("未在当前大纲中定位到目标节点，可能已被刷新。")

            split_nodes = self._build_split_nodes_from_segments(real_node, valid_segments)
            parent_list[idx : idx + 1] = split_nodes
            self.workspace.save_outline_tree(self.outline_tree_data)
            self._auto_export_html_if_enabled("拆分场景")
            self.refresh_ui_from_workspace()

            first_id = split_nodes[0].get("id")
            if first_id:
                item = find_item_by_data(self.novel_tree.invisibleRootItem(), first_id)
                if item:
                    parent = item.parent()
                    while parent:
                        parent.setExpanded(True)
                        parent = parent.parent()
                    self.novel_tree.setCurrentItem(item)
                    self.novel_tree.scrollToItem(item)
                    self.on_novel_node_clicked(item, 0)

            self.log_console.append(
                "<font color='green'>✅ 节点拆分完成："
                f"按目标 {turning_points} 个转折点，已生成 {len(split_nodes)} 个同级场景节点。</font>"
            )
            self._send_system_notification(
                "节点拆分完成",
                f"《{real_node.get('title', '场景')}》已拆分为 {len(split_nodes)} 个同级场景节点。",
            )
        except Exception as e:
            self.log_console.append(f"<font color='red'>场景拆分失败: {e}</font>")
            QMessageBox.critical(self, "错误", f"场景拆分失败:\n{e}")
            # 失败时尽量恢复原文，避免部分写入导致正文异常
            try:
                rel_path = real_node.get("file_path")
                if rel_path:
                    self.workspace.save_markdown_file(rel_path, original_text)
            except Exception:
                pass
        finally:
            self.btn_save.setEnabled(True)
            self.split_scene_thread = None

    def on_split_scene_error(self: "NovelCreatorWindow", error_msg: str, node_id: str):
        if hasattr(self, "splitting_nodes") and node_id in self.splitting_nodes:
            self.splitting_nodes.remove(node_id)
        self.log_console.append(f"<font color='red'>场景拆分失败: {error_msg}</font>")
        QMessageBox.critical(self, "错误", f"场景拆分过程中发生异常:\n{error_msg}")
        self.btn_save.setEnabled(True)
        self.split_scene_thread = None

    # ================= 修改内容暂存与合并功能 ================= #

    def open_modify_request_dialog(self: "NovelCreatorWindow", real_node: dict, node_id: str):
        """打开修改请求对话框"""
        class ModifyRequestDialog(QDialog):
            def __init__(self, parent, node_title, append_writing_style_default: bool):
                super().__init__(parent)
                self.setWindowTitle(f"修改内容 - {node_title}")
                self.setMinimumWidth(600)
                self.setMinimumHeight(400)
                self.result_text = ""

                layout = QVBoxLayout()
                layout.addWidget(QLabel("请输入修改要求："))

                self.requirement_edit = QTextEdit()
                self.requirement_edit.setPlaceholderText("例如：将这段内容的语气改得更加轻松幽默，或者增加一些环境描写...")
                layout.addWidget(self.requirement_edit)

                self.append_writing_style_cb = QCheckBox("附加当前写作风格（写作Instruction）")
                self.append_writing_style_cb.setChecked(bool(append_writing_style_default))
                layout.addWidget(self.append_writing_style_cb)

                btn_layout = QHBoxLayout()
                btn_layout.addStretch()

                self.cancel_btn = QPushButton("取消")
                self.cancel_btn.clicked.connect(self.reject)
                btn_layout.addWidget(self.cancel_btn)

                self.ok_btn = QPushButton("生成修改")
                self.ok_btn.clicked.connect(self.accept)
                self.ok_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
                btn_layout.addWidget(self.ok_btn)

                layout.addLayout(btn_layout)
                self.setLayout(layout)

            def get_requirement(self):
                return self.requirement_edit.toPlainText().strip()

            def should_append_writing_style(self):
                return self.append_writing_style_cb.isChecked()

        dialog = ModifyRequestDialog(
            self,
            real_node.get("title", "未知节点"),
            bool(getattr(self, "_append_writing_style_on_modify", False)),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            requirement = dialog.get_requirement()
            if requirement:
                append_writing_style = dialog.should_append_writing_style()
                self._append_writing_style_on_modify = append_writing_style
                self._save_modify_style_option(append_writing_style)
                self._save_workspace_instruction_profile()
                self.start_modify_content(
                    real_node, node_id, requirement, append_writing_style
                )

    def _is_llm_refusal_or_no_change(self: "NovelCreatorWindow", result: str, original_text: str) -> tuple[bool, str]:
        if not result or not result.strip():
            return True, "LLM 返回了空内容"

        result_stripped = result.strip()
        original_stripped = original_text.strip()

        refusal_patterns = [
            "抱歉，我无法", "对不起，我无法", "抱歉，我不能", "我很抱歉",
            "我无法修改", "我无法进行", "我不能修改", "我不能这样做",
            "我做不到", "我不能这么做", "我无法这样做",
            "作为AI", "作为人工智能", "作为一个AI",
            "无法完成", "无法执行", "不能完成",
            "请提供更多", "请告诉我", "请说明",
            "这不适合", "这不符合", "违反",
            "无法满足", "不能满足", "不能提供",
            "I'm sorry", "I cannot", "I can't", "I'm unable",
            "As an AI", "As a language model",
        ]

        def _contains_refusal(text: str) -> tuple[bool, str]:
            for pattern in refusal_patterns:
                if pattern in text:
                    return True, pattern
            return False, ""

        has_refusal, matched_pattern = _contains_refusal(result_stripped)

        if len(original_stripped) > 50:
            matcher = SequenceMatcher(None, result_stripped, original_stripped)
            similarity = matcher.ratio()
            if similarity > 0.95:
                if has_refusal:
                    return True, f"LLM 返回了拒绝修改的回复（相似度 {similarity:.1%}，检测到关键词: \"{matched_pattern}\"）"
                return False, ""

        if len(original_stripped) > 200:
            ratio = len(result_stripped) / len(original_stripped)
            if ratio < 0.01:
                if has_refusal:
                    return True, f"LLM 返回了极短拒绝回复（仅为原文的 {ratio:.1%}，检测到关键词: \"{matched_pattern}\"）"
                return False, ""

        if has_refusal:
            return True, f"LLM 返回了拒绝修改的回复（检测到关键词: \"{matched_pattern}\"）"

        return False, ""

    def _build_modify_system_instruction(
        self: "NovelCreatorWindow", append_writing_style: bool
    ) -> str:
        text_cfg = (self.config or {}).get("text_api", {})
        modify_instruction = text_cfg.get(
            "modify_instructions", self._get_default_modify_instruction()
        )
        writing_instruction = text_cfg.get(
            "instructions", self._get_default_writing_instruction()
        )
        if append_writing_style:
            return (
                f"{modify_instruction}\n\n"
                "【附加要求】在执行修改时，必须继续遵循当前写作风格 Instruction：\n"
                f"{writing_instruction}"
            )
        return modify_instruction

    def start_modify_content(
        self: "NovelCreatorWindow",
        real_node: dict,
        node_id: str,
        requirement: str,
        append_writing_style: bool = False,
    ):
        """开始修改内容的LLM请求"""
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return

        if not hasattr(self, 'modifying_nodes'):
            self.modifying_nodes = set()

        node_title = real_node.get("title", "未知节点")

        if node_id in self.modifying_nodes:
            msg = f"节点【{node_title}】正在生成修改中，请勿重复发起。"
            self.log_console.append(f"<font color='orange'>{msg}</font>")
            QMessageBox.warning(self, "提示", msg)
            return

        if self.workspace.has_pending_modify(node_id):
            msg = f"节点【{node_title}】已有待合并的修改，请先处理。"
            self.log_console.append(f"<font color='orange'>{msg}</font>")
            QMessageBox.warning(self, "提示", msg)
            return

        # 读取原文
        original_text = ""
        rel_path = real_node.get("file_path")
        if rel_path:
            full_path = os.path.join(self.workspace.text_path, rel_path)
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    original_text = f.read()

        if not original_text:
            QMessageBox.warning(self, "提示", "该节点暂无正文内容可修改。")
            return

        self.log_console.append(f"<font color='cyan'>开始修改节点【{real_node.get('title')}】的内容...</font>")
        self.btn_save.setEnabled(False)
        self.modifying_nodes.add(node_id)

        checked_paths = self.get_checked_settings()
        settings_text = ""
        if checked_paths:
            try:
                builder = self._create_context_builder()
                settings_text = builder._build_settings_text(checked_paths).strip()
            except Exception as e:
                self.log_console.append(
                    f"<font color='orange'>构建背景设定上下文失败，已降级为仅使用原文修改：{e}</font>"
                )

        settings_context_block = (
            f"\n\n### 背景设定参考（来自左侧勾选设定）：\n{settings_text}"
            if settings_text
            else ""
        )

        # 构建提示词
        prompt = f"""你是一个专业的小说编辑助手。请根据用户的修改要求，对提供的小说正文进行修改。

### 修改要求：
{requirement}

### 原文内容：
{original_text}
{settings_context_block}

### 输出要求：
1. 只输出修改后的完整文本
2. 保持原文的基本结构和格式
3. 不要添加任何额外的解释性文字
4. 如果原文有标题（# 开头），请保留
"""

        modify_system_instruction = self._build_modify_system_instruction(
            append_writing_style
        )
        mode_text = "编辑Instruction + 写作风格" if append_writing_style else "仅编辑Instruction"
        self.log_console.append(
            f"<font color='gray'>本次修改使用系统指令模式：{mode_text}</font>"
        )

        # 发送请求
        self.modify_thread = GenerateTaskThread(
            self.llm_client, prompt, modify_system_instruction
        )
        self.modify_thread.progress_signal.connect(
            lambda msg: self._on_llm_progress(msg)
        )
        self.modify_thread.success_signal.connect(lambda result: self.on_modify_success(result, real_node, node_id, original_text, requirement))
        self.modify_thread.error_signal.connect(lambda error: self.on_modify_error(error, node_id))
        self.modify_thread.start()

    def on_modify_success(self: "NovelCreatorWindow", result: str, real_node: dict, node_id: str, original_text: str, requirement: str):
        """修改成功回调"""
        if hasattr(self, 'modifying_nodes') and node_id in self.modifying_nodes:
            self.modifying_nodes.remove(node_id)
        try:
            is_refusal, reason = self._is_llm_refusal_or_no_change(result, original_text)
            if is_refusal:
                node_title = real_node.get("title", "当前节点")
                self.log_console.append(f"<font color='orange'>⚠️ 节点【{node_title}】修改被跳过：{reason}</font>")
                self.log_console.append(f"<font color='gray'>LLM 原始返回（前200字）：{result[:200]}...</font>")
                getattr(self, '_issue_submitting_map', {}).pop(node_id, None)
                if hasattr(self, '_issue_submit_queue') and self._issue_submit_queue:
                    self._process_next_issue_submit()
                return

            self.workspace.save_pending_modify(node_id, original_text, result, requirement)

            issue_id = getattr(self, '_issue_submitting_map', {}).pop(node_id, None)
            if issue_id and self.workspace:
                self.workspace.delete_mobile_issue(issue_id)
                self._refresh_issue_panel()

            self.log_console.append(f"<font color='green'>✅ 修改内容生成完成！节点标题已变红，请右键选择【进行合并】来查看差异并合并。</font>")
            node_title = real_node.get("title", "当前节点")
            self._send_system_notification(
                "修改任务完成",
                f"《{node_title}》修改内容已生成完成。",
            )

            self._refresh_novel_tree()

            if hasattr(self, '_issue_submit_queue') and self._issue_submit_queue:
                self._process_next_issue_submit()

        except Exception as e:
            self.log_console.append(f"<font color='red'>保存待合并修改失败: {e}</font>")
            QMessageBox.critical(self, "错误", f"保存待合并修改失败:\n{e}")
        finally:
            self.btn_save.setEnabled(True)

    def on_modify_error(self: "NovelCreatorWindow", error_msg: str, node_id: str):
        """修改失败回调"""
        if hasattr(self, 'modifying_nodes') and node_id in self.modifying_nodes:
            self.modifying_nodes.remove(node_id)
        getattr(self, '_issue_submitting_map', {}).pop(node_id, None)
        self.log_console.append(f"<font color='red'>修改内容失败: {error_msg}</font>")
        QMessageBox.critical(self, "错误", f"修改内容过程中发生异常:\n{error_msg}")
        self.btn_save.setEnabled(True)
        if hasattr(self, '_issue_submit_queue') and self._issue_submit_queue:
            self._process_next_issue_submit()

    def open_merge_dialog(self: "NovelCreatorWindow", node_id: str, real_node: dict):
        """打开合并对话框"""
        pending_data = self.workspace.get_pending_modify(node_id)
        if not pending_data:
            QMessageBox.warning(self, "提示", "没有找到待合并的修改。")
            return

        with self.loading_ui("正在准备差异对比..."):
            dialog = DiffMergeDialog(
                self,
                node_id,
                pending_data["original_text"],
                pending_data["modified_text"],
                real_node.get("title", "未知节点")
            )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            result_text = dialog.get_result()
            self.apply_merge_result(node_id, real_node, result_text)

    def apply_merge_result(self: "NovelCreatorWindow", node_id: str, real_node: dict, result_text: str):
        """应用合并结果"""
        try:
            with self.loading_ui("正在应用合并结果..."):
                # 保存到文件
                rel_path = real_node.get("file_path")
                if rel_path:
                    new_md5 = self.workspace.save_markdown_file(rel_path, result_text)
                    real_node["md5"] = new_md5

                # 删除待合并修改
                self.workspace.delete_pending_modify(node_id)

                # 保存大纲树
                self.workspace.save_outline_tree(self.outline_tree_data)
                self._auto_export_html_if_enabled("合并修改内容")

                # 刷新UI
                self._refresh_novel_tree()
            
            # 重新查找并设置当前编辑的节点（刷新树后旧item已无效）
            if self.current_editing_node and self.current_editing_node.get("id") == node_id:
                # 从新构建的树中重新找到对应的item和节点对象
                item = find_item_by_data(self.novel_tree.invisibleRootItem(), node_id)
                if item:
                    self.current_editing_item = item
                # 更新当前编辑的节点为新的节点对象
                if node_id in self.node_map:
                    self.current_editing_node = self.node_map[node_id]
                # 刷新编辑器内容（避免再次从磁盘读取）
                self.content_editor.setText(result_text)
                # 更新原始内容比较基准
                self.current_node_original_content = result_text
                self.current_node_original_summary = self.summary_editor.toPlainText()
            
            self.log_console.append(f"<font color='green'>✅ 合并成功！节点内容已更新。</font>")

        except Exception as e:
            self.log_console.append(f"<font color='red'>合并失败: {e}</font>")
            QMessageBox.critical(self, "错误", f"合并失败:\n{e}")

    def batch_merge_all_pending(self: "NovelCreatorWindow"):
        """一键全部合并所有待合并修改（不进入逐行对比，直接应用LLM修改版）"""
        if not self.workspace:
            QMessageBox.warning(self, "提示", "请先打开工作区。")
            return

        pending_ids = self.workspace.get_all_pending_node_ids()

        merge_list = []
        for node_id in pending_ids:
            pending_data = self.workspace.get_pending_modify(node_id)
            if not pending_data:
                continue
            real_node = self.node_map.get(node_id)
            if not real_node:
                continue
            rel_path = real_node.get("file_path")
            if not rel_path:
                continue
            merge_list.append((node_id, real_node, pending_data))

        if not merge_list:
            QMessageBox.information(self, "提示", "当前没有待合并的修改。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("一键全部合并")
        dialog.setMinimumWidth(450)
        dialog.setMinimumHeight(300)

        dialog_layout = QVBoxLayout()

        info_label = QLabel(
            f"即将直接应用以下 {len(merge_list)} 个节点的修改内容（不进入逐行对比）："
        )
        info_label.setWordWrap(True)
        dialog_layout.addWidget(info_label)

        node_list = QListWidget()
        for node_id, real_node, _ in merge_list:
            full_path = self._get_node_path_from_outline(node_id) or real_node.get("title", "未知节点")
            node_list.addItem(QListWidgetItem(f"  • {full_path}"))
        dialog_layout.addWidget(node_list)

        warn_label = QLabel("此操作将直接用 LLM 生成的修改版覆盖原文。\n确定要全部合并吗？")
        warn_label.setWordWrap(True)
        dialog_layout.addWidget(warn_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        yes_btn = QPushButton("确定合并")
        no_btn = QPushButton("取消")
        btn_layout.addWidget(yes_btn)
        btn_layout.addWidget(no_btn)
        dialog_layout.addLayout(btn_layout)

        dialog.setLayout(dialog_layout)
        yes_btn.clicked.connect(dialog.accept)
        no_btn.clicked.connect(dialog.reject)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        success_count = 0
        fail_count = 0
        current_node_was_merged = False
        current_node_result = ""

        for node_id, real_node, pending_data in merge_list:
            try:
                result_text = pending_data["modified_text"]
                rel_path = real_node.get("file_path")

                new_md5 = self.workspace.save_markdown_file(rel_path, result_text)
                real_node["md5"] = new_md5

                self.workspace.delete_pending_modify(node_id)

                node_title = real_node.get("title", "未知节点")
                self.log_console.append(
                    f"<font color='green'>✅ 已合并：【{node_title}】</font>"
                )
                success_count += 1

                if (
                    self.current_editing_node
                    and self.current_editing_node.get("id") == node_id
                ):
                    current_node_was_merged = True
                    current_node_result = result_text

            except Exception as e:
                node_title = real_node.get("title", "未知节点")
                self.log_console.append(
                    f"<font color='red'>合并失败：【{node_title}】: {e}</font>"
                )
                fail_count += 1

        self.workspace.save_outline_tree(self.outline_tree_data)
        self._auto_export_html_if_enabled("一键全部合并")
        self._refresh_novel_tree()

        if current_node_was_merged:
            self.content_editor.setText(current_node_result)
            self.current_node_original_content = current_node_result
            self.current_node_original_summary = self.summary_editor.toPlainText()

        QMessageBox.information(
            self,
            "全部合并完成",
            f"合并完成！\n成功：{success_count} 个\n失败：{fail_count} 个",
        )

    def discard_pending_modify(self: "NovelCreatorWindow", node_id: str):
        """丢弃待合并修改"""
        reply = QMessageBox.question(
            self,
            "确认丢弃",
            "确定要丢弃这次修改吗？LLM生成的结果将被永久删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.workspace.delete_pending_modify(node_id)
            self._refresh_novel_tree()
            self.log_console.append(f"<font color='yellow'>已丢弃待合并的修改。</font>")
    
    def _get_batch_modify_prompt_history(self: "NovelCreatorWindow") -> list[str]:
        state = self._load_sys_state()
        history = state.get("batch_modify_prompt_history", [])
        if not isinstance(history, list):
            return []
        return [item for item in history if isinstance(item, str) and item.strip()]

    def _add_batch_modify_prompt_to_history(self: "NovelCreatorWindow", prompt: str):
        prompt = prompt.strip()
        if not prompt:
            return
        state = self._load_sys_state()
        history = state.get("batch_modify_prompt_history", [])
        if not isinstance(history, list):
            history = []
        if prompt in history:
            history.remove(prompt)
        history.insert(0, prompt)
        max_history = 50
        if len(history) > max_history:
            history = history[:max_history]
        state["batch_modify_prompt_history"] = history
        self._write_sys_state(state)

    def _remove_batch_modify_prompt_from_history(self: "NovelCreatorWindow", index: int):
        state = self._load_sys_state()
        history = state.get("batch_modify_prompt_history", [])
        if not isinstance(history, list):
            return
        if 0 <= index < len(history):
            history.pop(index)
            state["batch_modify_prompt_history"] = history
            self._write_sys_state(state)

    def _clear_batch_modify_prompt_history(self: "NovelCreatorWindow"):
        state = self._load_sys_state()
        state["batch_modify_prompt_history"] = []
        self._write_sys_state(state)


    class BatchModifyPromptHistoryDialog(QDialog):
        def __init__(self, parent, batch_modify_dialog=None):
            super().__init__(parent)
            self._parent_window = parent
            self._batch_modify_dialog = batch_modify_dialog
            self.setWindowTitle("管理历史Prompt")
            self.setMinimumWidth(550)
            self.setMinimumHeight(400)

            layout = QVBoxLayout()

            layout.addWidget(QLabel("历史使用的批量修改Prompt（点击可编辑内容，选中后可删除）："))

            self.prompt_list = QListWidget()
            self.prompt_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
            self._refresh_list()
            self.prompt_list.currentRowChanged.connect(self._on_selection_changed)
            layout.addWidget(self.prompt_list)

            detail_layout = QVBoxLayout()
            detail_layout.addWidget(QLabel("Prompt 内容："))
            self.detail_edit = QTextEdit()
            self.detail_edit.setPlaceholderText("选中左侧条目可查看和编辑内容...")
            self.detail_edit.setMinimumHeight(100)
            self.detail_edit.setReadOnly(True)
            detail_layout.addWidget(self.detail_edit)
            layout.addLayout(detail_layout)

            btn_layout = QHBoxLayout()
            self.edit_save_btn = QPushButton("编辑")
            self.edit_save_btn.setEnabled(False)
            self.edit_save_btn.clicked.connect(self._toggle_edit_save)
            btn_layout.addWidget(self.edit_save_btn)

            self.delete_btn = QPushButton("删除选中")
            self.delete_btn.setEnabled(False)
            self.delete_btn.clicked.connect(self._delete_selected)
            btn_layout.addWidget(self.delete_btn)

            btn_layout.addStretch()

            self.clear_all_btn = QPushButton("清空全部")
            self.clear_all_btn.clicked.connect(self._clear_all)
            btn_layout.addWidget(self.clear_all_btn)

            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(self.accept)
            btn_layout.addWidget(close_btn)

            layout.addLayout(btn_layout)
            self.setLayout(layout)

            self._is_editing = False
            self._current_index = -1

        def _refresh_list(self):
            self.prompt_list.blockSignals(True)
            self.prompt_list.clear()
            prompts = self._parent_window._get_batch_modify_prompt_history()
            for p in prompts:
                display = p[:80] + "..." if len(p) > 80 else p
                self.prompt_list.addItem(QListWidgetItem(display))
            self.prompt_list.blockSignals(False)

        def _on_selection_changed(self, row):
            self._current_index = row
            if row >= 0:
                prompts = self._parent_window._get_batch_modify_prompt_history()
                if row < len(prompts):
                    self.detail_edit.setPlainText(prompts[row])
                    self.detail_edit.setReadOnly(True)
                    self.edit_save_btn.setText("编辑")
                    self.edit_save_btn.setEnabled(True)
                    self.delete_btn.setEnabled(True)
                    self._is_editing = False
                else:
                    self.detail_edit.clear()
                    self.edit_save_btn.setEnabled(False)
                    self.delete_btn.setEnabled(False)
            else:
                self.detail_edit.clear()
                self.edit_save_btn.setEnabled(False)
                self.delete_btn.setEnabled(False)

        def _toggle_edit_save(self):
            if self._is_editing:
                new_text = self.detail_edit.toPlainText().strip()
                if new_text and self._current_index >= 0:
                    self._parent_window._remove_batch_modify_prompt_from_history(self._current_index)
                    self._parent_window._add_batch_modify_prompt_to_history(new_text)
                    self._refresh_list()
                    self.prompt_list.setCurrentRow(0)
                    if self._batch_modify_dialog:
                        self._batch_modify_dialog._refresh_prompt_history_combo()
                self.detail_edit.setReadOnly(True)
                self.edit_save_btn.setText("编辑")
                self._is_editing = False
            else:
                if self._current_index >= 0:
                    self.detail_edit.setReadOnly(False)
                    self.detail_edit.setFocus()
                    self.edit_save_btn.setText("保存")
                    self._is_editing = True

        def _delete_selected(self):
            if self._current_index >= 0:
                reply = QMessageBox.question(
                    self, "确认删除",
                    "确定要删除这条历史Prompt吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._parent_window._remove_batch_modify_prompt_from_history(self._current_index)
                    self._refresh_list()
                    self.detail_edit.clear()
                    self.edit_save_btn.setEnabled(False)
                    self.delete_btn.setEnabled(False)
                    self._is_editing = False
                    self.edit_save_btn.setText("编辑")
                    if self._batch_modify_dialog:
                        self._batch_modify_dialog._refresh_prompt_history_combo()

        def _clear_all(self):
            reply = QMessageBox.question(
                self, "确认清空",
                "确定要清空全部历史Prompt吗？此操作不可撤销！",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._parent_window._clear_batch_modify_prompt_history()
                self._refresh_list()
                self.detail_edit.clear()
                self.edit_save_btn.setEnabled(False)
                self.delete_btn.setEnabled(False)
                self._is_editing = False
                self.edit_save_btn.setText("编辑")
                if self._batch_modify_dialog:
                    self._batch_modify_dialog._refresh_prompt_history_combo()

    def open_batch_modify_request_dialog(self: "NovelCreatorWindow", checked_nodes):
        """打开批量修改请求对话框"""
        # 过滤节点：只保留3级且没有待合并修改的节点
        valid_nodes = []
        skipped_info = []
        
        if not hasattr(self, 'modifying_nodes'):
            self.modifying_nodes = set()
        
        for item, node in checked_nodes:
            level = get_item_level(item)
            node_id = node.get("id")
            node_title = node.get('title', '未知')
            
            if level in [1, 2]:
                skipped_info.append(f"跳过【{node_title}】：1级/2级节点无正文")
                continue
            
            if self.workspace.has_pending_modify(node_id):
                msg = f"跳过【{node_title}】：已处于待合并状态"
                skipped_info.append(msg)
                self.log_console.append(f"<font color='orange'>{msg}</font>")
                continue
                
            if node_id in self.modifying_nodes:
                msg = f"跳过【{node_title}】：正在生成修改中"
                skipped_info.append(msg)
                self.log_console.append(f"<font color='orange'>{msg}</font>")
                continue
            
            # 读取原文，检查是否有正文
            original_text = ""
            rel_path = node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                if os.path.exists(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        original_text = f.read()
            
            if not original_text:
                skipped_info.append(f"跳过【{node.get('title', '未知')}】：无正文内容")
                continue
            
            valid_nodes.append((item, node))
        
        if not valid_nodes:
            info_text = "没有符合条件的节点可以处理。\n\n" + "\n".join(skipped_info)
            QMessageBox.information(self, "提示", info_text)
            return
        
        class BatchModifyRequestDialog(QDialog):
            def __init__(
                self,
                parent,
                valid_nodes_info,
                skipped_info_list,
                default_thread_count: int,
                default_smart_select: bool = False,
            ):
                super().__init__(parent)
                self.setWindowTitle("批量修改内容")
                self.setMinimumWidth(600)
                self.setMinimumHeight(550)
                self.result_text = ""
                
                layout = QVBoxLayout()
                
                # 显示待处理的节点列表
                layout.addWidget(QLabel(f"准备处理 {len(valid_nodes_info)} 个节点："))
                node_list = QListWidget()
                for _, node in valid_nodes_info:
                    node_list.addItem(QListWidgetItem(f"✅ {node.get('title', '未知')}"))
                layout.addWidget(node_list)
                
                # 显示跳过的节点信息（如果有）
                if skipped_info_list:
                    layout.addWidget(QLabel("\n以下节点将被跳过："))
                    skipped_list = QListWidget()
                    for info in skipped_info_list:
                        skipped_list.addItem(QListWidgetItem(f"⚠️ {info}"))
                    layout.addWidget(skipped_list)
                
                # 历史 Prompt 选择
                history_layout = QHBoxLayout()
                history_layout.addWidget(QLabel("历史Prompt："))
                self.prompt_history_combo = QComboBox()
                self.prompt_history_combo.setEditable(False)
                self.prompt_history_combo.setMinimumWidth(200)
                self.prompt_history_combo.addItem("-- 选择历史Prompt --", "")
                prompts = parent._get_batch_modify_prompt_history()
                for p in prompts:
                    display = p[:60] + "..." if len(p) > 60 else p
                    self.prompt_history_combo.addItem(display, p)
                self.prompt_history_combo.currentIndexChanged.connect(self._on_history_selected)
                history_layout.addWidget(self.prompt_history_combo, 1)
                self.manage_history_btn = QPushButton("管理")
                self.manage_history_btn.setToolTip("管理历史使用的Prompt")
                self.manage_history_btn.clicked.connect(self._open_manage_history_dialog)
                history_layout.addWidget(self.manage_history_btn)
                layout.addLayout(history_layout)

                # 输入修改要求
                layout.addWidget(QLabel("请输入修改要求（将应用于所有选中节点）："))
                self.requirement_edit = QTextEdit()
                self.requirement_edit.setPlaceholderText("例如：将这段内容的语气改得更加轻松幽默，或者增加一些环境描写...")
                self.requirement_edit.setMinimumHeight(120)
                layout.addWidget(self.requirement_edit)

                # 请求线程数
                thread_layout = QHBoxLayout()
                thread_layout.addWidget(QLabel("请求线程数："))
                self.thread_count_spin = QSpinBox()
                self.thread_count_spin.setRange(1, 20)
                self.thread_count_spin.setValue(max(1, min(20, int(default_thread_count))))
                self.thread_count_spin.setSuffix(" 线程")
                thread_layout.addWidget(self.thread_count_spin)
                thread_layout.addStretch()
                layout.addLayout(thread_layout)

                # 智能选择设定集选项
                self.smart_select_cb = QCheckBox("智能选择设定集（基于节点概要自动筛选相关设定）")
                self.smart_select_cb.setChecked(default_smart_select)
                self.smart_select_cb.setToolTip(
                    "勾选后，每个节点修改前会先本地计算设定相关性，"
                    "再请求LLM一次来形成最终的设定勾选结果。"
                )
                layout.addWidget(self.smart_select_cb)

                self.use_cache_cb = QCheckBox("使用缓存（跳过已计算的相关性）")
                self.use_cache_cb.setChecked(default_smart_select)
                self.use_cache_cb.setEnabled(default_smart_select)
                self.use_cache_cb.setToolTip(
                    "勾选后，如果 .cache 目录中已有该节点的设定相关性缓存，"
                    "则直接复用，不再重新计算和请求LLM。"
                )
                self.smart_select_cb.toggled.connect(self.use_cache_cb.setEnabled)
                self.smart_select_cb.toggled.connect(self.use_cache_cb.setChecked)
                layout.addWidget(self.use_cache_cb)
                
                # 按钮
                btn_layout = QHBoxLayout()
                btn_layout.addStretch()
                
                self.cancel_btn = QPushButton("取消")
                self.cancel_btn.clicked.connect(self.reject)
                btn_layout.addWidget(self.cancel_btn)
                
                self.ok_btn = QPushButton("开始批量修改")
                self.ok_btn.clicked.connect(self.accept)
                self.ok_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
                btn_layout.addWidget(self.ok_btn)
                
                layout.addLayout(btn_layout)
                self.setLayout(layout)

            def _on_history_selected(self, index):
                if index <= 0:
                    return
                prompt_text = self.prompt_history_combo.currentData()
                if prompt_text:
                    self.requirement_edit.setPlainText(prompt_text)

            def _open_manage_history_dialog(self):
                self._manage_dialog = BatchModifyPromptHistoryDialog(self.parent(), self)
                self._manage_dialog.exec()
                self._refresh_prompt_history_combo()

            def _refresh_prompt_history_combo(self):
                self.prompt_history_combo.blockSignals(True)
                self.prompt_history_combo.clear()
                self.prompt_history_combo.addItem("-- 选择历史Prompt --", "")
                prompts = self.parent()._get_batch_modify_prompt_history()
                for p in prompts:
                    display = p[:60] + "..." if len(p) > 60 else p
                    self.prompt_history_combo.addItem(display, p)
                self.prompt_history_combo.setCurrentIndex(0)
                self.prompt_history_combo.blockSignals(False)

            def get_requirement(self):
                return self.requirement_edit.toPlainText().strip()
            
            def get_thread_count(self):
                return self.thread_count_spin.value()

            def get_smart_select(self):
                return self.smart_select_cb.isChecked()

            def get_use_cache(self):
                return self.use_cache_cb.isChecked()
        
        dialog = BatchModifyRequestDialog(
            self,
            valid_nodes,
            skipped_info,
            getattr(self, "_batch_modify_thread_count", 3),
            default_smart_select=bool(
                getattr(self, "_smart_setting_selection_enabled", False)
            ),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            requirement = dialog.get_requirement()
            if requirement:
                thread_count = dialog.get_thread_count()
                use_smart_select = dialog.get_smart_select()
                use_cache = dialog.get_use_cache()
                self._batch_modify_thread_count = thread_count
                self._save_batch_modify_thread_count(thread_count)
                self._add_batch_modify_prompt_to_history(requirement)
                self.start_batch_modify_content(
                    valid_nodes, requirement, thread_count,
                    use_smart_select=use_smart_select,
                    use_cache=use_cache,
                )
    
    def _batch_modify_log(
        self: "NovelCreatorWindow", stage: str, detail: str = "",
    ):
        queue_n = len(getattr(self, "batch_modify_queue", []))
        inflight = int(getattr(self, "batch_modify_inflight", 0))
        threads_n = len(getattr(self, "batch_modify_threads", {}))
        thread_ids = sorted(
            f"0x{id(t):x}"
            for t in getattr(self, "batch_modify_threads", {}).values()
        ) if hasattr(self, "batch_modify_threads") else []
        alive_ids = sorted(
            tid for tid in thread_ids
            if any(
                f"0x{id(t):x}" == tid
                and t.isRunning()
                for t in getattr(self, "batch_modify_threads", {}).values()
            )
        )
        self.log_console.append(
            f"<font color='#888' size='2'>[批修] {stage}"
            f" | queue={queue_n} inflight={inflight} threads={threads_n}"
            f" | tids={thread_ids} alive={alive_ids}"
            f"{' | ' + detail if detail else ''}</font>"
        )

    def start_batch_modify_content(
        self: "NovelCreatorWindow", valid_nodes, requirement, thread_count: int = 3,
        use_smart_select: bool = False, use_cache: bool = True,
    ):
        """开始批量修改内容"""
        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return
        
        # 初始化批量处理状态
        if not hasattr(self, 'batch_modify_queue'):
            self.batch_modify_queue = []
        if not hasattr(self, 'is_batch_modifying'):
            self.is_batch_modifying = False
        
        if self.is_batch_modifying:
            QMessageBox.warning(self, "提示", "正在进行批量修改，请等待完成或停止后再进行新的批量操作。")
            return
        
        self.batch_modify_queue = valid_nodes.copy()
        self.is_batch_modifying = True
        self.batch_modify_requirement = requirement
        self.batch_modify_max_workers_raw = thread_count

        self._batch_modify_node_settings: dict[str, str] = {}

        if use_smart_select:
            previous_smart_flag = getattr(self, "_smart_setting_selection_enabled", False)
            self._smart_setting_selection_enabled = True
            self._batch_modify_log("SMART_SEL_START", f"nodes={len(valid_nodes)} use_cache={use_cache}")

            all_candidate_paths = self.get_checked_settings()
            if not all_candidate_paths:
                all_candidates = self._collect_setting_candidates_for_smart_select()
                all_candidate_paths = [item["path"] for item in all_candidates]

            if not all_candidate_paths:
                self._smart_setting_selection_enabled = previous_smart_flag
                self._batch_modify_log("SMART_SEL_DONE", "no_candidates")
            else:
                nodes_to_process = []
                for _item, node in valid_nodes:
                    node_id = node.get("id", "")
                    if node_id:
                        nodes_to_process.append((_item, node))

                self._batch_modify_smart_sel_prev_flag = previous_smart_flag
                self._batch_modify_smart_sel_queue = nodes_to_process
                self._batch_modify_smart_sel_candidates = all_candidate_paths
                self._batch_modify_smart_sel_use_cache = use_cache
                self._batch_modify_log(
                    "SMART_SEL_BATCH_START", f"total={len(nodes_to_process)}"
                )
                self._process_next_batch_modify_smart_sel()
                return

        if not self._batch_modify_node_settings:
            checked_paths = self.get_checked_settings()
            self.batch_modify_settings_text = ""
            if checked_paths:
                try:
                    builder = self._create_context_builder()
                    self.batch_modify_settings_text = builder._build_settings_text(
                        checked_paths
                    ).strip()
                    self._batch_modify_log("GLOBAL_SETTINGS", f"path_count={len(checked_paths)}")
                except Exception as e:
                    self.log_console.append(
                        f"<font color='orange'>构建批量修改背景设定上下文失败，已降级为仅使用原文修改：{e}</font>"
                    )

        self.batch_modify_success_count = 0
        self.batch_modify_fail_count = 0
        self.batch_modify_max_workers = max(1, min(20, int(thread_count)))
        self.batch_modify_inflight = 0
        self.batch_modify_threads = {}

        self.log_console.append(
            f"<font color='cyan'>🚀 开始批量修改，共 {len(self.batch_modify_queue)} 个节点，"
            f"并发 {self.batch_modify_max_workers} 线程...</font>"
        )
        self._batch_modify_log(
            "START",
            f"total={len(self.batch_modify_queue)} max_workers={self.batch_modify_max_workers}",
        )
        self.btn_save.setEnabled(False)

        self._process_next_batch_modify_node()

    def _process_next_batch_modify_smart_sel(self: "NovelCreatorWindow"):
        if not hasattr(self, "_batch_modify_smart_sel_queue"):
            self._batch_modify_log("SMART_SEL_ERR", "queue_missing")
            self._finalize_batch_modify_smart_sel()
            return
        if not self._batch_modify_smart_sel_queue:
            self._finish_batch_modify_smart_sel()
            return

        _item, node = self._batch_modify_smart_sel_queue[0]
        candidate_paths = getattr(self, "_batch_modify_smart_sel_candidates", [])
        use_cache = getattr(self, "_batch_modify_smart_sel_use_cache", True)

        if not self.workspace:
            self._batch_modify_smart_sel_queue.pop(0)
            self._process_next_batch_modify_smart_sel()
            return

        self._batch_modify_log(
            "SMART_SEL_NODE",
            f"node={node.get('title', '?')} id={node.get('id', '?')}"
        )

        self._batch_modify_sel_thread = SettingSelectionThread(
            self.llm_client,
            self.workspace.workspace_path,
            "批量修改",
            node,
            node.get("summary", ""),
            use_cache,
            self.get_checked_settings(),
            candidate_paths,
        )
        self._batch_modify_sel_thread.progress_signal.connect(
            self._on_batch_modify_smart_sel_progress
        )
        self._batch_modify_sel_thread.success_signal.connect(
            self._on_batch_modify_smart_sel_resolved
        )
        self._batch_modify_sel_thread.error_signal.connect(
            self._on_batch_modify_smart_sel_error
        )
        self._batch_modify_sel_thread.start()

    def _on_batch_modify_smart_sel_error(
        self: "NovelCreatorWindow", error_msg_or_paths
    ):
        if isinstance(error_msg_or_paths, list):
            self.log_console.append(
                "<font color='red'>❌ 批量修改设定筛选异常，已回退手动勾选设定</font>"
            )
            self._on_batch_modify_smart_sel_resolved(error_msg_or_paths)
        else:
            self.log_console.append(
                f"<font color='red'>❌ 批量修改设定筛选异常：{error_msg_or_paths}</font>"
            )
            QTimer.singleShot(0, self._process_next_batch_modify_smart_sel)

    def _on_batch_modify_smart_sel_progress(
        self: "NovelCreatorWindow", message: str
    ):
        self.log_console.append(
            f"<font color='gray'>[批修-LLM] {message}</font>"
        )

    def _on_batch_modify_smart_sel_resolved(
        self: "NovelCreatorWindow", selected_paths: list
    ):
        if not hasattr(self, "_batch_modify_smart_sel_queue"):
            self._batch_modify_log("SMART_SEL_ERR", "queue_lost_on_resolve")
            self._finalize_batch_modify_smart_sel()
            return
        if not self._batch_modify_smart_sel_queue:
            self._finish_batch_modify_smart_sel()
            return

        _item, node = self._batch_modify_smart_sel_queue.pop(0)
        node_id = node.get("id", "")
        candidate_paths = getattr(self, "_batch_modify_smart_sel_candidates", [])

        try:
            if selected_paths and selected_paths != candidate_paths:
                builder = self._create_context_builder()
                settings_text = builder._build_settings_text(
                    selected_paths
                ).strip()
                if settings_text and node_id:
                    self._batch_modify_node_settings[node_id] = settings_text
        except Exception as e:
            self.log_console.append(
                f"<font color='orange'>节点【{node.get('title', '未知')}】"
                f"设定智能筛选失败：{e}</font>"
            )

        QTimer.singleShot(0, self._process_next_batch_modify_smart_sel)

    def _finish_batch_modify_smart_sel(self: "NovelCreatorWindow"):
        previous_smart_flag = getattr(
            self, "_batch_modify_smart_sel_prev_flag", False
        )
        self._smart_setting_selection_enabled = previous_smart_flag

        matched = len(self._batch_modify_node_settings)
        total = matched + len(getattr(self, "batch_modify_queue", []))
        self._batch_modify_log(
            "SMART_SEL_DONE",
            f"matched={matched}/{total}",
        )

        self._batch_modify_smart_sel_queue = None
        self._batch_modify_smart_sel_candidates = None
        self._batch_modify_smart_sel_use_cache = None
        self._batch_modify_smart_sel_prev_flag = None

        self._finalize_batch_modify_smart_sel()

    def _finalize_batch_modify_smart_sel(self: "NovelCreatorWindow"):
        if not self._batch_modify_node_settings:
            checked_paths = self.get_checked_settings()
            self.batch_modify_settings_text = ""
            if checked_paths:
                try:
                    builder = self._create_context_builder()
                    self.batch_modify_settings_text = builder._build_settings_text(
                        checked_paths
                    ).strip()
                    self._batch_modify_log(
                        "GLOBAL_SETTINGS", f"path_count={len(checked_paths)}"
                    )
                except Exception as e:
                    self.log_console.append(
                        f"<font color='orange'>构建批量修改背景设定上下文失败，已降级为仅使用原文修改：{e}</font>"
                    )

        self.batch_modify_success_count = 0
        self.batch_modify_fail_count = 0
        thread_count = getattr(self, "batch_modify_max_workers_raw", 3)
        self.batch_modify_max_workers = max(1, min(20, int(thread_count)))
        self.batch_modify_inflight = 0
        self.batch_modify_threads = {}

        self.log_console.append(
            f"<font color='cyan'>🚀 开始批量修改，共 {len(self.batch_modify_queue)} 个节点，"
            f"并发 {self.batch_modify_max_workers} 线程...</font>"
        )
        self._batch_modify_log(
            "START",
            f"total={len(self.batch_modify_queue)} max_workers={self.batch_modify_max_workers}",
        )
        self.btn_save.setEnabled(False)

        self._process_next_batch_modify_node()

    def _process_next_batch_modify_node(self: "NovelCreatorWindow"):
        """按并发上限持续分发批量修改任务"""
        if not self.is_batch_modifying:
            self._batch_modify_log("DISPATCH_SKIP", "is_batch_modifying=False")
            return

        while (
            self.batch_modify_queue
            and self.batch_modify_inflight < self.batch_modify_max_workers
        ):
            next_item, next_node = self.batch_modify_queue.pop(0)
            node_title = next_node.get('title', '未知节点')
            node_id = next_node.get('id')

            self.log_console.append(
                f"<hr><b>⏳ 正在处理节点: {node_title} "
                f"(队列剩余 {len(self.batch_modify_queue)} 个)</b>"
            )
            self._batch_modify_log(
                "DISPATCH",
                f"node={node_title} id={node_id} inflight_before={self.batch_modify_inflight}",
            )

            if not hasattr(self, 'modifying_nodes'):
                self.modifying_nodes = set()
            self.modifying_nodes.add(node_id)

            # 读取原文
            original_text = ""
            rel_path = next_node.get("file_path")
            if rel_path:
                full_path = os.path.join(self.workspace.text_path, rel_path)
                if os.path.exists(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        original_text = f.read()

            if not original_text:
                if hasattr(self, 'modifying_nodes') and node_id in self.modifying_nodes:
                    self.modifying_nodes.remove(node_id)
                self.log_console.append(
                    f"<font color='orange'>跳过【{node_title}】：无正文内容</font>"
                )
                self._batch_modify_log("DISPATCH_SKIP", f"node={node_title} reason=no_content")
                self.batch_modify_fail_count += 1
                continue

            # 构建提示词
            node_settings_text = (
                getattr(self, "_batch_modify_node_settings", {}).get(node_id, "")
                or ""
            ).strip()
            global_settings_text = (getattr(self, "batch_modify_settings_text", "") or "").strip()
            settings_text = node_settings_text or global_settings_text
            settings_context_block = (
                f"\n\n### 背景设定参考（来自智能筛选设定）：\n{settings_text}"
                if node_settings_text
                else (
                    f"\n\n### 背景设定参考（来自左侧勾选设定）：\n{settings_text}"
                    if settings_text
                    else ""
                )
            )
            prompt = f"""你是一个专业的小说编辑助手。请根据用户的修改要求，对提供的小说正文进行修改。

### 修改要求：
{self.batch_modify_requirement}

### 原文内容：
{original_text}
{settings_context_block}

### 输出要求：
1. 只输出修改后的完整文本
2. 保持原文的基本结构和格式
3. 不要添加任何额外的解释性文字
4. 如果原文有标题（# 开头），请保留
"""
        
            # 发送请求
            thread = GenerateTaskThread(self.llm_client, prompt)
            thread.progress_signal.connect(lambda msg: self._on_llm_progress(msg))
            thread.success_signal.connect(
                lambda result, n=next_node, nid=node_id, o=original_text: self.on_batch_modify_success(result, n, nid, o)
            )
            thread.error_signal.connect(
                lambda error, t=node_title, nid=node_id: self.on_batch_modify_error(error, t, nid)
            )
            self.batch_modify_threads[node_id] = thread
            self.batch_modify_inflight += 1
            thread.start()
            thread_id_hex = f"0x{id(thread):x}"
            self._batch_modify_log(
                "THREAD_START",
                f"node={node_title} tid={thread_id_hex}"
                f" isRunning={thread.isRunning()}"
            )

        self._finalize_batch_modify_if_done()

    def _finalize_batch_modify_if_done(self: "NovelCreatorWindow"):
        if not self.is_batch_modifying:
            return
        if self.batch_modify_queue or self.batch_modify_inflight > 0:
            self._batch_modify_log(
                "FINALIZE_PENDING",
                f"queue={len(self.batch_modify_queue)} inflight={self.batch_modify_inflight}",
            )
            return

        self._batch_modify_log(
            "FINALIZE_DONE",
            f"success={self.batch_modify_success_count} fail={self.batch_modify_fail_count}",
        )
        self.is_batch_modifying = False
        self.btn_save.setEnabled(True)
        self.log_console.append(
            f"<b><font color='green'>🎉 批量修改完成！成功：{self.batch_modify_success_count} 个，"
            f"失败：{self.batch_modify_fail_count} 个</font></b>"
        )
        QMessageBox.information(
            self,
            "批量修改完成",
            f"批量修改已结束！\n成功：{self.batch_modify_success_count} 个\n失败：{self.batch_modify_fail_count} 个\n\n节点已变红，请右键选择【进行合并】查看差异并合并。"
        )
        self._send_system_notification(
            "批量修改完成",
            f"批量修改任务已结束：成功 {self.batch_modify_success_count}，失败 {self.batch_modify_fail_count}。",
        )
        self._refresh_novel_tree()

    def _on_llm_progress(self: "NovelCreatorWindow", message: str):
        if not message:
            return
        self.log_console.append(f"<font color='gray'>[LLM进度] {message}</font>")
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage(message, 2500)
    
    def _pop_batch_thread(self: "NovelCreatorWindow", node_id: str):
        """安全清理批量修改线程引用——先等待线程结束再释放 Python 引用。"""
        thread = None
        was_running = False
        if hasattr(self, "batch_modify_threads"):
            thread = self.batch_modify_threads.pop(node_id, None)
        if thread is not None:
            was_running = thread.isRunning()
            if was_running:
                self._batch_modify_log(
                    "POP_WAIT",
                    f"node_id={node_id} tid=0x{id(thread):x}",
                )
                thread.wait(5000)
                self._batch_modify_log(
                    "POP_WAIT_DONE",
                    f"node_id={node_id} isRunning_after={thread.isRunning()}",
                )
            else:
                self._batch_modify_log(
                    "POP_NO_WAIT",
                    f"node_id={node_id} tid=0x{id(thread):x} already_stopped",
                )
        else:
            self._batch_modify_log("POP_MISSING", f"node_id={node_id} thread_not_found")
        if hasattr(self, "batch_modify_inflight"):
            self.batch_modify_inflight = max(0, self.batch_modify_inflight - 1)

    def on_batch_modify_success(self: "NovelCreatorWindow", result: str, real_node: dict, node_id: str, original_text: str):
        """批量修改单个节点成功回调"""
        node_title = real_node.get('title', '未知节点')
        self._batch_modify_log("CALLBACK_SUCCESS", f"node={node_title} id={node_id}")
        if hasattr(self, 'modifying_nodes') and node_id in self.modifying_nodes:
            self.modifying_nodes.remove(node_id)
        self._pop_batch_thread(node_id)
        try:
            is_refusal, reason = self._is_llm_refusal_or_no_change(result, original_text)
            if is_refusal:
                self.batch_modify_fail_count += 1
                self._batch_modify_log("CALLBACK_REFUSAL", f"node={node_title} reason={reason}")
                self.log_console.append(f"<font color='orange'>⚠️ 节点【{node_title}】修改被跳过：{reason}</font>")
                self.log_console.append(f"<font color='gray'>LLM 原始返回（前200字）：{result[:200]}...</font>")
                return

            self.workspace.save_pending_modify(node_id, original_text, result, self.batch_modify_requirement)

            self.batch_modify_success_count += 1
            self.log_console.append(f"<font color='green'>✅ 节点【{node_title}】修改成功！</font>")

        except Exception as e:
            self.batch_modify_fail_count += 1
            self._batch_modify_log("CALLBACK_SAVE_ERR", f"node={node_title} error={e}")
            self.log_console.append(f"<font color='red'>保存待合并修改失败: {e}</font>")
        finally:
            self._process_next_batch_modify_node()
    
    def on_batch_modify_error(self: "NovelCreatorWindow", error_msg: str, node_title: str, node_id: str):
        """批量修改单个节点失败回调"""
        self._batch_modify_log("CALLBACK_ERROR", f"node={node_title} id={node_id} error={error_msg[:100]}")
        if hasattr(self, 'modifying_nodes') and node_id in self.modifying_nodes:
            self.modifying_nodes.remove(node_id)
        self._pop_batch_thread(node_id)
        self.batch_modify_fail_count += 1
        self.log_console.append(f"<font color='red'>❌ 节点【{node_title}】修改失败: {error_msg}</font>")
        self._process_next_batch_modify_node()

    # ================= 📱 手机端 Issue 处理 ================= #

    def _refresh_issue_panel(self: "NovelCreatorWindow"):
        """刷新待处理 Issue 面板"""
        if not self.workspace:
            return

        issues = self.workspace.load_mobile_issues()
        self._loaded_mobile_issues = issues

        count = len(issues)
        self.issue_count_label.setText(f"📱 待处理 Issue ({count})")

        if not issues:
            self.issue_panel_frame.setVisible(False)
            return

        self.issue_panel_frame.setVisible(True)

        while self.issue_list_layout.count() > 1:
            item = self.issue_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for issue in issues:
            card = self._build_issue_card(issue)
            self.issue_list_layout.insertWidget(self.issue_list_layout.count() - 1, card)

        if self.issue_scroll_area.isVisible():
            rows = min(count, 6)
            self.issue_panel_frame.setFixedHeight(40 + rows * 110 + 12)

    def _build_issue_card(self: "NovelCreatorWindow", issue: dict) -> QFrame:
        """构建单个 Issue 卡片"""
        card = QFrame()
        card.setFrameStyle(QFrame.Shape.StyledPanel)
        card.setStyleSheet("QFrame { background: #2d2d2d; border: 1px solid #444; border-radius: 4px; padding: 4px; }")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        submitted_at = issue.get("submitted_at", "")[:16].replace("T", " ")
        node_id = issue.get("node_id", "")
        node_path = self._get_node_path_from_outline(node_id) or "(节点已删除)"

        time_label = QLabel(f"🕐 {submitted_at}")
        time_label.setStyleSheet("color: #7f8c8d; font-size: 10px; border: none; background: transparent;")
        layout.addWidget(time_label)

        path_label = QLabel(f"📍 {node_path}")
        path_label.setStyleSheet("color: #e0c36a; font-weight: bold; font-size: 11px; border: none; background: transparent;")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        req_text = issue.get("requirement", "")
        if len(req_text) > 60:
            req_text = req_text[:60] + "..."
        req_label = QLabel(f"📝 {req_text}")
        req_label.setStyleSheet("color: #ccc; font-size: 11px; border: none; background: transparent;")
        req_label.setWordWrap(True)
        layout.addWidget(req_label)

        quoted = issue.get("quoted_text", "")
        if quoted:
            if len(quoted) > 40:
                quoted = quoted[:40] + "..."
            quote_label = QLabel(f"📎 {quoted}")
            quote_label.setStyleSheet("color: #95a5a6; font-size: 10px; border: none; background: transparent;")
            quote_label.setWordWrap(True)
            layout.addWidget(quote_label)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        edit_btn = QPushButton("✏️ 编辑")
        edit_btn.setFixedHeight(22)
        edit_btn.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 8px; }")
        edit_btn.clicked.connect(lambda checked=False, iss=issue: self._on_issue_edit(iss))
        btn_layout.addWidget(edit_btn)

        submit_btn = QPushButton("🚀 提交修改")
        submit_btn.setFixedHeight(22)
        submit_btn.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 8px; background: #2196F3; color: white; }")
        submit_btn.clicked.connect(lambda checked=False, iss=issue: self._on_issue_submit(iss))
        btn_layout.addWidget(submit_btn)

        close_btn = QPushButton("✔ 关闭")
        close_btn.setFixedHeight(22)
        close_btn.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 8px; }")
        close_btn.clicked.connect(lambda checked=False, iss=issue: self._on_issue_close(iss))
        btn_layout.addWidget(close_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        return card

    def _on_issue_edit(self: "NovelCreatorWindow", issue: dict):
        """编辑 Issue 需求文本"""
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑修改需求")
        dialog.setMinimumWidth(450)
        dialog.setMinimumHeight(300)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("修改需求："))

        edit = QTextEdit()
        edit.setPlainText(issue.get("requirement", ""))
        layout.addWidget(edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_req = edit.toPlainText().strip()
            if new_req and new_req != issue.get("requirement", ""):
                self.workspace.update_mobile_issue(issue["issue_id"], {"requirement": new_req})
                self._refresh_issue_panel()
                self.log_console.append("已更新 Issue 需求文本。")

    def _on_issue_close(self: "NovelCreatorWindow", issue: dict):
        """关闭（删除）Issue"""
        node_path = self._get_node_path_from_outline(issue.get("node_id", "")) or "(未知节点)"
        reply = QMessageBox.question(
            self,
            "确认关闭",
            f"确定要关闭此 Issue 吗？\n\n节点：{node_path}\n需求：{issue.get('requirement', '')[:60]}...",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.workspace.delete_mobile_issue(issue["issue_id"])
            self._refresh_issue_panel()
            self.log_console.append("<font color='yellow'>已关闭 Issue。</font>")

    def _on_issue_submit(self: "NovelCreatorWindow", issue: dict):
        """提交修改：取 issue.requirement → start_modify_content"""
        node_id = issue.get("node_id", "")
        real_node = self.node_map.get(node_id)
        if not real_node:
            QMessageBox.warning(self, "错误", "找不到对应节点，可能已被删除。")
            return

        if not self.llm_client:
            QMessageBox.warning(self, "未配置", "请先在设置中配置大模型 API。")
            return

        requirement = issue.get("requirement", "")
        quoted = issue.get("quoted_text", "")
        if quoted:
            requirement = f"{requirement}\n\n用户引用的原文片段：\n{quoted}"

        if not hasattr(self, '_issue_submitting_map'):
            self._issue_submitting_map = {}
        self._issue_submitting_map[node_id] = issue["issue_id"]

        self.start_modify_content(real_node, node_id, requirement)

    def _on_issue_submit_all(self: "NovelCreatorWindow"):
        """全部提交：遍历所有 Issue，逐个转入 LLM 修改流程"""
        if not self.workspace:
            return

        issues = self._loaded_mobile_issues if hasattr(self, '_loaded_mobile_issues') else self.workspace.load_mobile_issues()
        if not issues:
            QMessageBox.information(self, "提示", "当前没有待处理的 Issue。")
            return

        valid_issues = []
        for issue in issues:
            node_id = issue.get("node_id", "")
            real_node = self.node_map.get(node_id)
            if not real_node:
                self.log_console.append(f"<font color='orange'>跳过 Issue：找不到节点 {issue.get('node_id', '')}</font>")
                continue
            if self.workspace.has_pending_modify(node_id):
                self.log_console.append(f"<font color='orange'>跳过 Issue：节点已有待合并修改</font>")
                continue
            valid_issues.append((issue, real_node, node_id))

        if not valid_issues:
            QMessageBox.information(self, "提示", "没有可提交的 Issue（节点缺失或已有待合并修改）。")
            return

        reply = QMessageBox.question(
            self,
            "确认全部提交",
            f"即将把 {len(valid_issues)} 个 Issue 全部转入 LLM 修改流程。\n\n确定要全部提交吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if not hasattr(self, '_issue_submitting_map'):
            self._issue_submitting_map = {}

        self._issue_submit_queue = valid_issues
        self.log_console.append(f"<font color='cyan'>开始逐个提交 {len(valid_issues)} 个 Issue...</font>")
        self._process_next_issue_submit()

    def _process_next_issue_submit(self: "NovelCreatorWindow"):
        """从队列中取出下一个 Issue 并提交修改"""
        if not hasattr(self, '_issue_submit_queue') or not self._issue_submit_queue:
            if hasattr(self, '_issue_submit_queue'):
                del self._issue_submit_queue
            self.log_console.append("<font color='green'>✅ 所有 Issue 已提交完毕。</font>")
            return

        issue, real_node, node_id = self._issue_submit_queue.pop(0)

        if node_id in getattr(self, 'modifying_nodes', set()):
            self.log_console.append(f"<font color='orange'>跳过 Issue：节点正在修改中</font>")
            self._process_next_issue_submit()
            return

        requirement = issue.get("requirement", "")
        quoted = issue.get("quoted_text", "")
        if quoted:
            requirement = f"{requirement}\n\n用户引用的原文片段：\n{quoted}"

        if not hasattr(self, '_issue_submitting_map'):
            self._issue_submitting_map = {}
        self._issue_submitting_map[node_id] = issue["issue_id"]

        self.start_modify_content(real_node, node_id, requirement)
