"""
GenerationMixin —— AI 单节点生成、重写与批量生成逻辑。
"""
from __future__ import annotations

import uuid
import os
import json
import re
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
)

from core.context_builder import ContextBuilder
from core.setting_relevance import compute_local_relevance
from core.setting_relevance_cache import SettingRelevanceCache
from ui.utils import find_item_by_data, get_item_level, get_missing_level3_nodes
from ui.workers import GenerateTaskThread

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class GenerationMixin:
    """处理 AI 生成正文、重写正文以及批量生成缺失场景。"""

    def regenerate_summary_for_tree_item(self: "NovelCreatorWindow", item):
        """对指定树节点触发“重新生成概要”（支持右键菜单触发）。"""
        if not item or item.text(0).startswith("+"):
            return
        if get_item_level(item) != 3:
            QMessageBox.information(
                self,  # type: ignore[arg-type]
                "提示",
                "该功能仅支持3级场景节点。",
            )
            return
        if getattr(self, "is_batch_summary_regenerating", False):
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "提示",
                "正在进行批量概要校验，请等待完成后再试。",
            )
            return

        self.novel_tree.setCurrentItem(item)
        self.on_novel_node_clicked(item, 0)
        if self.current_editing_item is not item:
            # 用户在“未保存内容”提示中取消切换时，终止本次操作
            return
        self.regenerate_summary()

    def _send_system_notification(
        self: "NovelCreatorWindow",
        title: str,
        message: str,
    ):
        """发送系统托盘通知；托盘不可用时回退到日志提示。"""
        if not bool(
            (self.config or {}).get("task_completion_notification_enabled", True)
        ):
            return

        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.log_console.append(
                f"系统通知（托盘不可用）: {title} - {message}"
            )
            return

        tray_icon = getattr(self, "_system_tray_icon", None)
        if tray_icon is None:
            app = QApplication.instance()
            style = app.style() if app else self.style()
            icon = self.windowIcon()
            if icon.isNull():
                icon = style.standardIcon(
                    QStyle.StandardPixmap.SP_MessageBoxInformation
                )
            tray_icon = QSystemTrayIcon(icon, self)  # type: ignore[arg-type]
            tray_icon.setToolTip("AI小说创作器")
            tray_icon.show()
            self._system_tray_icon = tray_icon

        tray_icon.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    # ================= AI 生成核心逻辑 ================= #
    def _create_context_builder(self: "NovelCreatorWindow") -> ContextBuilder:
        text_cfg = (self.config or {}).get("text_api", {})
        return ContextBuilder(
            self.workspace,
            model_context_size=text_cfg.get("model_context_size"),
            model_capabilities=text_cfg.get("model_capabilities", []),
            compression_profile=text_cfg.get("world_context_compression_profile", "balanced"),
            children_summary_compress_trigger_ratio=text_cfg.get(
                "children_summary_compress_trigger_ratio"
            ),
            children_summary_group_budget_ratio=text_cfg.get(
                "children_summary_group_budget_ratio"
            ),
        )
    
    def _get_current_node_from_tree(self: "NovelCreatorWindow"):
        """从当前选中的树节点获取最新的节点对象"""
        item = self.current_editing_item
        if item is None and hasattr(self, "novel_tree") and self.novel_tree:
            item = self.novel_tree.currentItem()
        if item is None or item.text(0).startswith("+"):
            return None
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        if node_id and node_id in self.node_map:
            self.current_editing_item = item
            self.current_editing_node = self.node_map[node_id]
            return self.node_map[node_id]
        return None

    def _collect_setting_candidates_for_smart_select(
        self: "NovelCreatorWindow",
    ) -> list[dict]:
        """收集可用于智能筛选的设定候选（按文件名/路径）。"""
        if not self.workspace:
            return []
        candidates: list[dict] = []
        setting_dirs = getattr(self.workspace, "setting_dirs", [])
        for cat in setting_dirs:
            cat_path = os.path.join(self.workspace.settings_path, cat)
            iter_fn = getattr(self, "_iter_setting_json_files", None)
            if not callable(iter_fn):
                continue
            build_name_fn = getattr(self, "_build_setting_display_name", None)
            for file_path in iter_fn(cat_path):
                display = (
                    build_name_fn(cat_path, file_path)
                    if callable(build_name_fn)
                    else os.path.basename(file_path).replace(".json", "")
                )
                rel_display = f"{cat}/{display}"
                candidates.append(
                    {
                        "path": file_path,
                        "display": rel_display.replace("\\", "/"),
                    }
                )
        candidates.sort(key=lambda x: x["display"])
        return candidates

    def _parse_smart_setting_selection_ids(
        self: "NovelCreatorWindow", raw_text: str
    ) -> list[int]:
        """解析 LLM 返回的智能勾选编号。"""
        if not raw_text:
            return []
        cleaned = raw_text.strip()
        parsed = None
        for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
            match = re.search(pattern, cleaned)
            if not match:
                continue
            try:
                parsed = json.loads(match.group(0))
                break
            except Exception:
                continue
        if parsed is None:
            try:
                parsed = json.loads(cleaned)
            except Exception:
                return []

        raw_ids = []
        if isinstance(parsed, dict):
            for key in ("selected_ids", "selected_indexes", "ids", "indexes"):
                if isinstance(parsed.get(key), list):
                    raw_ids = parsed.get(key) or []
                    break
        elif isinstance(parsed, list):
            raw_ids = parsed

        result: list[int] = []
        for value in raw_ids:
            try:
                idx = int(value)
            except Exception:
                continue
            if idx > 0:
                result.append(idx)
        return list(dict.fromkeys(result))

    def _get_relevance_cache(self: "NovelCreatorWindow") -> SettingRelevanceCache | None:
        if not self.workspace:
            return None
        if not hasattr(self, "_setting_relevance_cache"):
            self._setting_relevance_cache = SettingRelevanceCache(
                self.workspace.workspace_path
            )
        return self._setting_relevance_cache

    def _resolve_checked_settings_for_task(
        self: "NovelCreatorWindow",
        task_name: str,
        target_node: dict,
        task_context_prompt: str,
        use_cache: bool = True,
    ) -> list[str]:
        """按开关决定是否先本地计算相关性，再请求 LLM 对设定进行智能勾选。"""
        checked_paths = self.get_checked_settings()
        if not getattr(self, "_smart_setting_selection_enabled", False):
            return checked_paths
        if not self.llm_client:
            return checked_paths

        candidate_paths = checked_paths
        if not candidate_paths:
            all_candidates = self._collect_setting_candidates_for_smart_select()
            candidate_paths = [item["path"] for item in all_candidates]
        if not candidate_paths:
            return checked_paths

        node_id = target_node.get("id", "")
        node_title = target_node.get("title", "未知节点")
        node_summary = target_node.get("summary", "")

        MAX_LLM_CANDIDATES = 30

        cache = self._get_relevance_cache()
        relevance_scores: list[tuple[str, float]] = []

        if use_cache and cache is not None:
            cached_scores = cache.load(node_id, node_summary, candidate_paths)
            if cached_scores is not None:
                relevance_scores = cached_scores
                self.log_console.append(
                    "📦 已从缓存加载设定相关性评分"
                )

        if not relevance_scores:
            self.log_console.append(
                "🔍 正在本地计算设定相关性..."
            )
            relevance_scores = compute_local_relevance(
                node_summary, node_title, candidate_paths
            )
            if cache is not None:
                cache.save(node_id, node_summary, candidate_paths, relevance_scores)

        top_candidates = relevance_scores[:MAX_LLM_CANDIDATES]
        top_paths = [p for p, _ in top_candidates]

        if use_cache and cache is not None:
            cached_selection = cache.load_selection(
                node_id, node_summary, candidate_paths
            )
            if cached_selection is not None:
                valid = [p for p in cached_selection if p in top_paths]
                if valid:
                    selected_names = [os.path.basename(p) for p in valid[:8]]
                    self.log_console.append(
                        "📦 从缓存加载LLM勾选结果，已选设定："
                        + "、".join(selected_names)
                        + ("..." if len(valid) > 8 else "")
                    )
                    return valid
                self.log_console.append(
                    "📦 缓存勾选结果已失效，将重新请求LLM"
                )

        display_rows = []
        path_map: dict[int, str] = {}
        for i, (path, score) in enumerate(top_candidates, start=1):
            cat = os.path.basename(os.path.dirname(path))
            name = os.path.basename(path)
            display_rows.append(
                f"{i}. [{cat}] {name} (本地相关性: {score:.2f})"
            )
            path_map[i] = path

        context_preview = (task_context_prompt or "").strip()
        if len(context_preview) > 3500:
            context_preview = context_preview[:3500] + "\n...(已截断)..."

        selector_prompt = f"""你是小说创作设定筛选助手。请基于任务上下文和本地相关性评分，从候选设定文件中选出最相关的条目编号。

任务类型：{task_name}
目标节点：{target_node.get("title", "未知节点")}

候选设定文件（编号列表，含本地相关性评分，仅供参考）：
{chr(10).join(display_rows)}

任务上下文（节选）：
{context_preview if context_preview else "（无）"}

输出要求（必须严格遵守）：
1. 仅输出 JSON。
2. JSON 格式必须是：{{"selected_ids":[1,2,3]}}
3. selected_ids 只填上方候选编号，按相关性从高到低排序。
4. 如果都不相关，返回空数组。
5. 本地相关性评分仅供参考，请结合任务上下文独立判断。
"""
        selector_sys = (
            "你是严谨的 JSON 输出助手。"
            "你只能输出 JSON，不要输出任何解释文字。"
        )

        try:
            self.log_console.append(
                f"已启用设定集智能勾选：正在为【{task_name}】请求LLM筛选设定文件..."
            )
            selected_raw = self.llm_client.generate_text(
                selector_prompt.strip(),
                override_system_instruction=selector_sys,
                progress_callback=self.on_generate_progress,
            )
            selected_ids = self._parse_smart_setting_selection_ids(selected_raw)
            selected_paths = [
                path_map[idx] for idx in selected_ids if idx in path_map
            ]
            if not selected_paths:
                self.log_console.append(
                    "智能勾选结果为空：将回退为当前手动勾选设定。"
                )
                return checked_paths

            if cache is not None:
                try:
                    cache.save_selection(
                        node_id, node_summary, candidate_paths,
                        selected_paths, relevance_scores,
                    )
                except Exception:
                    pass

            selected_names = [os.path.basename(p) for p in selected_paths[:8]]
            self.log_console.append(
                "智能勾选完成，已选设定："
                + "、".join(selected_names)
                + ("..." if len(selected_paths) > 8 else "")
            )
            return selected_paths
        except Exception as e:
            self.log_console.append(
                f"<font color='orange'>智能勾选失败，已回退手动勾选设定：{e}</font>"
            )
            return checked_paths

    def generate_current_node(self: "NovelCreatorWindow"):
        current_node = self._get_current_node_from_tree() or self.current_editing_node
        if not current_node or not self.outline_tree_data:
            return

        if not self.llm_client:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "配置缺失",
                "尚未初始化大模型客户端，请检查 conf/setting.json 文件。",
            )
            return

        # 保存当前内容到回退缓冲区
        current_content = self.content_editor.toPlainText()
        self._save_to_undo_stack('content', current_content)

        self.save_current_node()

        node_title = current_node.get("title", "未知节点")
        self.log_console.append(f"开始构建【{node_title}】的上下文...")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("正在构建上下文并发送请求到LLM...")

        self.btn_generate.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)

        builder = self._create_context_builder()
        preview_messages = builder.build_generation_prompt(
            current_node,
            self.outline_tree_data,
            [],
            generate_image=self.cb_gen_image.isChecked(),
            word_count=self.spin_word_count.value(),
            include_next=self.cb_include_next.isChecked(),
        )
        checked_paths = self._resolve_checked_settings_for_task(
            "生成正文",
            current_node,
            preview_messages[-1]["content"],
            use_cache=getattr(self, "_batch_gen_use_cache", True),
        )

        messages = builder.build_generation_prompt(
            current_node,
            self.outline_tree_data,
            checked_paths,
            generate_image=self.cb_gen_image.isChecked(),
            word_count=self.spin_word_count.value(),
            include_next=self.cb_include_next.isChecked(),
        )

        prompt_content = messages[-1]["content"]

        self.log_console.append(
            "========== [System] 文本生成系统指令 =========="
        )
        mode_name = (
            "NSFW文本生成"
            if str((self.config or {}).get("text_generation_mode", "normal")).lower() == "nsfw"
            else "普通文本生成"
        )
        self.log_console.append(f"当前生成模式: {mode_name}")
        self.log_console.append(self.llm_client.system_instruction)
        self.log_console.append(
            "========== [User] 上下文与生成提示词 =========="
        )
        self.log_console.append(prompt_content)
        self.log_console.append("=================================================")
        self.log_console.append("发送请求至大语言模型，后台处理中，请稍候...")

        self.generate_thread = GenerateTaskThread(self.llm_client, prompt_content)
        self.generate_thread.progress_signal.connect(self.on_generate_progress)
        self.generate_thread.success_signal.connect(self.on_generate_success)
        self.generate_thread.error_signal.connect(self.on_generate_error)
        self.generate_thread.start()

    def rewrite_current_node(self: "NovelCreatorWindow"):
        current_node = self._get_current_node_from_tree() or self.current_editing_node
        if not current_node:
            return

        target_content = self.content_editor.toPlainText().strip()
        if not target_content or (
            target_content.startswith("#") and len(target_content.split("\n")) <= 3
        ):
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "无法重写",
                "当前节点尚未生成有效正文内容。\n"
                "请先使用【结合上下文生成正文】或手动输入一段基础剧情。",
            )
            return

        target_word_count = self.spin_word_count.value()

        reply = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "确认重写",
            f"确定要将当前节点的正文重写/扩写为约 {target_word_count} 字吗？\n"
            "警告：生成成功后，现有的正文将被不可逆地完全覆盖！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        self.save_current_node()

        self.btn_generate.setEnabled(False)
        self.btn_rewrite.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)

        node_title = current_node.get("title", "未知节点")
        self.log_console.append(f"开始构建【{node_title}】的重写请求...")

        builder = self._create_context_builder()
        preview_messages = builder.build_rewrite_prompt(
            current_node,
            self.outline_tree_data,
            [],
            target_word_count,
        )
        checked_paths = self._resolve_checked_settings_for_task(
            "重写正文",
            current_node,
            preview_messages[-1]["content"],
        )

        messages = builder.build_rewrite_prompt(
            current_node,
            self.outline_tree_data,
            checked_paths,
            target_word_count,
        )

        prompt_content = messages[-1]["content"]

        self.log_console.append("========== [User] 重写提示词 ==========")
        self.log_console.append(prompt_content)
        self.log_console.append(
            "发送重写请求至大模型，后台处理中，请耐心稍候..."
        )

        self.generate_thread = GenerateTaskThread(self.llm_client, prompt_content)
        self.generate_thread.progress_signal.connect(self.on_generate_progress)
        self.generate_thread.success_signal.connect(self.on_generate_success)
        self.generate_thread.error_signal.connect(self.on_generate_error)
        self.generate_thread.start()

    def on_generate_success(self: "NovelCreatorWindow", result: str):
        node_title = (
            (self.current_editing_node or {}).get("title", "当前节点")
            if self.current_editing_node
            else "当前节点"
        )
        self.content_editor.setText(result)
        self.log_console.append("生成成功！已填入编辑器并自动保存。")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("LLM 生成成功，已更新编辑器内容", 3000)

        self.save_current_node()

        self._restore_generate_ui_state()

        if self.is_batch_generating:
            self._process_next_batch_node()
            return

        if self._is_batch_generation_context:
            self._is_batch_generation_context = False
            return

        self._send_system_notification(
            "生成完成",
            f"《{node_title}》正文已生成完成。",
        )

    def regenerate_summary(self: "NovelCreatorWindow"):
        """重新生成当前节点的概要(Summary)"""
        current_node = self._get_current_node_from_tree() or self.current_editing_node
        if not current_node or not self.outline_tree_data:
            self.log_console.append(
                "<font color='orange'>未检测到有效节点或大纲未加载：请先在右侧大纲树选择一个章/节/场景节点，再点击重新生成概要。</font>"
            )
            return

        if not self.llm_client:
            self.log_console.append(
                "<font color='orange'>未初始化大模型客户端：请先检查模型/API配置。</font>"
            )
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "配置缺失",
                "尚未初始化大模型客户端，请检查 conf/setting.json 文件。",
            )
            return

        # 防止重复点击触发并发请求，避免后返回结果覆盖先返回结果
        # 注意：历史遗留链路可能留下已结束的线程对象，此时不应误判为“仍在生成”
        thread = self.generate_thread
        if thread is not None:
            is_running_fn = getattr(thread, "isRunning", None)
            is_running = bool(is_running_fn()) if callable(is_running_fn) else False
            if is_running:
                self.log_console.append("已有生成任务在进行中，请等待当前任务完成。")
                return
            self.generate_thread = None

        # 保存当前选中节点的 ID，用于在生成后恢复选择
        target_node_id = current_node.get("id")
        self._current_regenerating_node_id = target_node_id
        request_id = uuid.uuid4().hex
        self._active_summary_regen_request_id = request_id

        # 保存当前概要到回退缓冲区
        current_summary = self.summary_editor.toPlainText()
        self._save_to_undo_stack('summary', current_summary)
        # 先落盘当前节点内容，确保概要生成读取到最新正文
        self.save_current_node()

        node_title = current_node.get("title", "未知节点")
        self.log_console.append(f"开始重新生成【{node_title}】的概要...")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("正在发送请求到LLM生成概要...")

        # 禁用相关按钮
        self.btn_generate.setEnabled(False)
        self.btn_rewrite.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_regenerate_summary.setEnabled(False)

        builder = self._create_context_builder()
        preview_messages = builder.build_summary_prompt(
            current_node,
            self.outline_tree_data,
            [],
            llm_client=None,
            progress_callback=None,
        )
        checked_paths = self._resolve_checked_settings_for_task(
            "生成概要",
            current_node,
            preview_messages[-1]["content"],
        )

        messages = builder.build_summary_prompt(
            current_node,
            self.outline_tree_data,
            checked_paths,
            llm_client=self.llm_client,
            progress_callback=self.on_generate_progress,
        )

        prompt_content = messages[-1]["content"]

        self.log_console.append(
            "========== [System] 概要生成系统指令 (Summary Instructions) =========="
        )
        self.log_console.append(self.llm_client.summary_system_instruction)
        self.log_console.append(
            "========== [User] 生成概要提示词 =========="
        )
        self.log_console.append(prompt_content)
        self.log_console.append("=================================================")
        self.log_console.append("发送请求至大语言模型，后台处理中，请稍候...")

        self.generate_thread = GenerateTaskThread(
            self.llm_client,
            prompt_content,
            self.llm_client.summary_system_instruction,
        )
        self.generate_thread.progress_signal.connect(self.on_generate_progress)
        self.generate_thread._summary_request_id = request_id  # type: ignore[attr-defined]
        self.generate_thread._summary_target_node_id = target_node_id  # type: ignore[attr-defined]
        self.generate_thread.success_signal.connect(self.on_summary_generate_success)
        self.generate_thread.error_signal.connect(self.on_generate_error)
        self.generate_thread.start()

    def start_batch_regenerate_summaries_checked_nodes(
        self: "NovelCreatorWindow", checked_nodes
    ):
        """按勾选顺序依次校验并修正 3 级场景概要。"""
        if self.is_batch_generating:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "提示",
                "正在进行批量正文生成，请等待完成后再执行批量概要校验。",
            )
            return
        if getattr(self, "is_batch_summary_regenerating", False):
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "提示",
                "正在进行批量概要校验，请等待当前任务结束。",
            )
            return
        if not self.workspace or not self.outline_tree_data:
            QMessageBox.warning(self, "提示", "请先打开并加载一个工作区！")  # type: ignore[arg-type]
            return
        if not self.llm_client:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "配置缺失",
                "尚未初始化大模型客户端，请检查 conf/setting.json 文件。",
            )
            return

        valid_nodes = []
        skipped_info = []
        seen_node_ids = set()
        for item, node in checked_nodes or []:
            if get_item_level(item) != 3:
                skipped_info.append(f"跳过【{node.get('title', '未知节点')}】：仅支持3级场景")
                continue
            node_id = node.get("id")
            if not node_id:
                skipped_info.append(f"跳过【{node.get('title', '未知节点')}】：节点ID缺失")
                continue
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)

            # 若正文仍处于“待合并修改”状态，则磁盘正文并非最新，不适合直接校验概要
            if self.workspace.has_pending_modify(node_id):
                skipped_info.append(
                    f"跳过【{node.get('title', '未知节点')}】：存在待合并修改，请先合并后再校验概要"
                )
                continue

            valid_nodes.append((item, node))

        if not valid_nodes:
            detail = "\n".join(skipped_info) if skipped_info else "未找到可处理的3级场景节点。"
            QMessageBox.information(self, "提示", detail)  # type: ignore[arg-type]
            return

        reply = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "确认批量校验概要",
            f"已选中 {len(valid_nodes)} 个可处理场景节点。\n"
            "将基于当前正文与当前概要，依次校验并修正概要。\n"
            "确认开始吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        mode_dialog = QDialog(self)
        mode_dialog.setWindowTitle("批量概要校验模式")
        mode_dialog.setMinimumWidth(460)
        layout = QVBoxLayout()
        desc = QLabel(
            "请选择批量校验模式：\n"
            "勾选时仅生成校验前/后的 Diff 文档，不写回节点概要。"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)
        diff_only_cb = QCheckBox("只生成 Diff，不直接覆盖概要（推荐）")
        diff_only_cb.setChecked(True)
        layout.addWidget(diff_only_cb)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton("开始")
        cancel_btn = QPushButton("取消")
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        mode_dialog.setLayout(layout)
        ok_btn.clicked.connect(mode_dialog.accept)
        cancel_btn.clicked.connect(mode_dialog.reject)
        if mode_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.batch_summary_regen_queue = valid_nodes.copy()
        self.is_batch_summary_regenerating = True
        self.batch_summary_regen_diff_only = diff_only_cb.isChecked()
        self.batch_summary_regen_success_count = 0
        self.batch_summary_regen_fail_count = 0
        self._batch_summary_current_node_id = None
        self._batch_summary_current_node_title = ""
        self._batch_summary_current_node_old_summary = ""
        self.batch_summary_before_records = []
        self.batch_summary_after_records = []

        self.log_console.append(
            f"<font color='cyan'>🩺 开始批量校验概要，共 {len(self.batch_summary_regen_queue)} 个节点，"
            f"模式：{'仅生成Diff' if self.batch_summary_regen_diff_only else '直接覆盖概要'}...</font>"
        )
        for info in skipped_info:
            self.log_console.append(f"<font color='orange'>{info}</font>")
        self._process_next_batch_summary_node()

    def _process_next_batch_summary_node(self: "NovelCreatorWindow"):
        if not getattr(self, "is_batch_summary_regenerating", False):
            return

        if not getattr(self, "batch_summary_regen_queue", []):
            self.is_batch_summary_regenerating = False
            self._batch_summary_current_node_id = None
            self._batch_summary_current_node_title = ""
            self._batch_summary_current_node_old_summary = ""
            diff_only = bool(getattr(self, "batch_summary_regen_diff_only", False))
            before_path = ""
            after_path = ""
            if diff_only and self.workspace:
                try:
                    temp_dir = os.path.join(self.workspace.workspace_path, "temp_diff")
                    os.makedirs(temp_dir, exist_ok=True)
                    before_path = os.path.join(temp_dir, "batch_summary_before.md")
                    after_path = os.path.join(temp_dir, "batch_summary_after.md")
                    with open(before_path, "w", encoding="utf-8") as f:
                        f.write("# 批量概要校验前汇总\n\n")
                        f.write("\n\n".join(self.batch_summary_before_records))
                    with open(after_path, "w", encoding="utf-8") as f:
                        f.write("# 批量概要校验后汇总\n\n")
                        f.write("\n\n".join(self.batch_summary_after_records))
                except Exception as e:
                    self.log_console.append(
                        f"<font color='orange'>Diff 文件写入失败：{e}</font>"
                    )
            self._restore_generate_ui_state()
            self.log_console.append(
                "<b><font color='green'>🎉 批量概要校验完成！"
                f"成功：{self.batch_summary_regen_success_count} 个，"
                f"失败：{self.batch_summary_regen_fail_count} 个</font></b>"
            )
            if diff_only and before_path and after_path:
                self.log_console.append(
                    f"校验前文件: <a href='file:///{before_path}'>{before_path}</a>"
                )
                self.log_console.append(
                    f"校验后文件: <a href='file:///{after_path}'>{after_path}</a>"
                )
            QMessageBox.information(
                self,
                "批量概要校验完成",
                "批量概要校验已结束。\n"
                f"成功：{self.batch_summary_regen_success_count} 个\n"
                f"失败：{self.batch_summary_regen_fail_count} 个\n"
                + (
                    "\n已生成 Diff 汇总文件，请前往工作区 temp_diff 查看。"
                    if diff_only
                    else ""
                ),
            )
            self._send_system_notification(
                "批量概要校验完成",
                f"批量概要校验任务已结束（{'仅Diff' if diff_only else '直接覆盖'}）："
                f"成功 {self.batch_summary_regen_success_count}，失败 {self.batch_summary_regen_fail_count}。",
            )
            return

        item, node = self.batch_summary_regen_queue.pop(0)
        node_id = node.get("id")
        node_title = node.get("title", "未知节点")
        self._batch_summary_current_node_id = node_id
        self._batch_summary_current_node_title = node_title
        self._batch_summary_current_node_old_summary = (node.get("summary") or "").strip()
        self.log_console.append(
            f"<hr><b>🧪 正在校验概要: {node_title} (队列剩余 {len(self.batch_summary_regen_queue)} 个)</b>"
        )

        if not item or get_item_level(item) != 3:
            self.batch_summary_regen_fail_count += 1
            self.log_console.append(
                f"<font color='red'>❌ 节点【{node_title}】校验失败：无效节点或层级不正确。</font>"
            )
            self._process_next_batch_summary_node()
            return

        self.novel_tree.setCurrentItem(item)
        self.on_novel_node_clicked(item, 0)
        if self.current_editing_item is not item:
            self.batch_summary_regen_fail_count += 1
            self.log_console.append(
                f"<font color='orange'>⚠️ 节点【{node_title}】已跳过：切换节点被取消。</font>"
            )
            self._process_next_batch_summary_node()
            return

        self.regenerate_summary()

    def on_generate_progress(self: "NovelCreatorWindow", message: str):
        if not message:
            return
        self.log_console.append(f"<font color='gray'>[LLM进度] {message}</font>")
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage(message, 2500)

    def on_summary_generate_success(self: "NovelCreatorWindow", result: str):
        """处理概要生成成功的回调"""
        sender_thread = self.sender()
        callback_request_id = getattr(sender_thread, "_summary_request_id", None)
        active_request_id = getattr(self, "_active_summary_regen_request_id", None)
        # 忽略过期回调，防止并发请求导致旧结果覆盖新结果
        if callback_request_id and active_request_id and callback_request_id != active_request_id:
            self.log_console.append("检测到过期概要回调，已自动忽略。")
            return

        target_node_id = (
            getattr(sender_thread, "_summary_target_node_id", None)
            or getattr(self, "_current_regenerating_node_id", None)
        )
        target_node = self.node_map.get(target_node_id) if target_node_id else None
        if not target_node:
            self.log_console.append("<font color='orange'>概要生成完成，但目标节点不存在，未写回。</font>")
            self._active_summary_regen_request_id = None
            self._restore_generate_ui_state()
            return

        is_batch_summary = bool(getattr(self, "is_batch_summary_regenerating", False))
        diff_only = bool(getattr(self, "batch_summary_regen_diff_only", False))
        should_write_back = not (is_batch_summary and diff_only)
        if should_write_back:
            target_node["summary"] = result
        current_node = self._get_current_node_from_tree() or self.current_editing_node
        # 仅当当前编辑的就是目标节点时，才刷新编辑器内容，避免误改用户当前操作节点
        if should_write_back and current_node is target_node:
            self.summary_editor.setText(result)
            self.current_node_original_summary = result

        if should_write_back and self.workspace and self.outline_tree_data:
            self.workspace.save_outline_tree(self.outline_tree_data)
        if should_write_back:
            self.log_console.append("概要生成成功！已写回目标节点并保存。")
        else:
            self.log_console.append("概要生成成功！当前为仅Diff模式，未写回节点。")
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage("概要生成成功，已更新编辑器内容", 3000)

        self._active_summary_regen_request_id = None
        self._restore_generate_ui_state()
        target_title = target_node.get("title", "当前节点")
        if getattr(self, "is_batch_summary_regenerating", False):
            title = target_node.get("title", "当前节点")
            old_summary = getattr(self, "_batch_summary_current_node_old_summary", "")
            self.batch_summary_before_records.append(
                f"## {title}\n{old_summary if old_summary else '(空)'}"
            )
            self.batch_summary_after_records.append(
                f"## {title}\n{result.strip() if result.strip() else '(空)'}"
            )
            self.batch_summary_regen_success_count += 1
            self.log_console.append(
                f"<font color='green'>✅ 节点【{target_title}】概要校验成功。</font>"
            )
            self._process_next_batch_summary_node()
            return
        self._send_system_notification(
            "概要生成完成",
            f"《{target_title}》概要已生成完成。",
        )

    def on_generate_error(self: "NovelCreatorWindow", error_msg: str):
        self.log_console.append(
            f"<font color='red'>生成失败: {error_msg}</font>"
        )
        
        # 在状态栏显示信息
        statusbar = self.statusBar()
        if statusbar:
            statusbar.showMessage(f"LLM 请求失败: {error_msg[:50]}...", 3000)
        
        if not self.is_batch_generating and not getattr(
            self, "is_batch_summary_regenerating", False
        ):
            QMessageBox.critical(
                self,  # type: ignore[arg-type]
                "生成错误",
                f"大模型请求失败:\n{error_msg}",
            )
        self._active_summary_regen_request_id = None
        self._restore_generate_ui_state()

        if self.is_batch_generating:
            self.log_console.append(
                "<font color='orange'>"
                "\u26a0\ufe0f 当前节点生成失败，跳过并处理下一个..."
                "</font>"
            )
            self._process_next_batch_node()
        elif getattr(self, "is_batch_summary_regenerating", False):
            title = getattr(self, "_batch_summary_current_node_title", "未知节点")
            self.batch_summary_regen_fail_count += 1
            self.log_console.append(
                f"<font color='orange'>⚠️ 节点【{title}】概要校验失败，跳过并处理下一个...</font>"
            )
            self._process_next_batch_summary_node()

    def _restore_generate_ui_state(self: "NovelCreatorWindow"):
        if not self.is_batch_generating and not getattr(
            self, "is_batch_summary_regenerating", False
        ):
            self.btn_generate.setEnabled(True)
            self.btn_save.setEnabled(True)
            self.btn_rewrite.setEnabled(True)
            self.btn_regenerate_summary.setEnabled(True)
            if self.current_editing_node:
                self.btn_delete.setEnabled(
                    not bool(self.current_editing_node.get("children"))
                )
        self.generate_thread = None

    # ================= 批量生成 ================= #

    def start_batch_generate_checked_nodes(
        self: "NovelCreatorWindow", checked_nodes
    ):
        """按勾选顺序依次生成 3 级场景正文。"""
        if self.is_batch_generating:
            QMessageBox.warning(
                self, "提示", "正在进行批量生成，请等待完成或先停止当前任务。"
            )
            return

        if not self.workspace or not self.outline_tree_data:
            QMessageBox.warning(
                self, "提示", "请先打开并加载一个工作区！"  # type: ignore[arg-type]
            )
            return

        if not self.llm_client:
            QMessageBox.warning(
                self,  # type: ignore[arg-type]
                "配置缺失",
                "尚未初始化大模型客户端，请检查 conf/setting.json 文件。",
            )
            return

        valid_nodes = []
        skipped_info = []
        seen_node_ids = set()
        for item, node in checked_nodes or []:
            if get_item_level(item) != 3:
                skipped_info.append(f"跳过【{node.get('title', '未知节点')}】：仅支持3级场景")
                continue

            node_id = node.get("id")
            if not node_id:
                skipped_info.append(f"跳过【{node.get('title', '未知节点')}】：节点ID缺失")
                continue
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)

            if self.workspace.has_pending_modify(node_id):
                skipped_info.append(
                    f"跳过【{node.get('title', '未知节点')}】：存在待合并修改"
                )
                continue

            valid_nodes.append(node)

        if not valid_nodes:
            detail = "\n".join(skipped_info) if skipped_info else "未找到可生成的3级场景节点。"
            QMessageBox.information(self, "提示", detail)  # type: ignore[arg-type]
            return

        smart_select_enabled = bool(
            getattr(self, "_smart_setting_selection_enabled", False)
        )

        batch_dialog = QDialog(self)
        batch_dialog.setWindowTitle("批量生成选项")
        batch_dialog.setMinimumWidth(420)
        batch_layout = QVBoxLayout()

        info_label = QLabel(
            f"已选中 {len(valid_nodes)} 个可处理场景节点。\n"
            "请选择批量生成选项："
        )
        info_label.setWordWrap(True)
        batch_layout.addWidget(info_label)

        self._batch_gen_smart_cb = QCheckBox("智能选择设定集（基于节点概要自动筛选相关设定）")
        self._batch_gen_smart_cb.setChecked(smart_select_enabled)
        self._batch_gen_smart_cb.setToolTip(
            "勾选后，每个节点生成前会先本地计算设定相关性，"
            "再请求LLM一次来形成最终的设定勾选结果。"
        )
        batch_layout.addWidget(self._batch_gen_smart_cb)

        self._batch_gen_cache_cb = QCheckBox("使用缓存（跳过已计算的相关性）")
        self._batch_gen_cache_cb.setChecked(True)
        self._batch_gen_cache_cb.setToolTip(
            "勾选后，如果 .cache 目录中已有该节点的设定相关性缓存，"
            "则直接复用，不再重新计算和请求LLM。"
        )
        batch_layout.addWidget(self._batch_gen_cache_cb)

        batch_btn_layout = QHBoxLayout()
        batch_btn_layout.addStretch()
        batch_ok_btn = QPushButton("开始生成")
        batch_cancel_btn = QPushButton("取消")
        batch_btn_layout.addWidget(batch_ok_btn)
        batch_btn_layout.addWidget(batch_cancel_btn)
        batch_layout.addLayout(batch_btn_layout)
        batch_dialog.setLayout(batch_layout)

        batch_ok_btn.clicked.connect(batch_dialog.accept)
        batch_cancel_btn.clicked.connect(batch_dialog.reject)

        if batch_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._batch_gen_use_smart_select = self._batch_gen_smart_cb.isChecked()
        self._batch_gen_use_cache = self._batch_gen_cache_cb.isChecked()
        if self._batch_gen_use_smart_select:
            self._smart_setting_selection_enabled = True
        else:
            self._smart_setting_selection_enabled = False

        self.batch_generate_queue = valid_nodes.copy()
        self.is_batch_generating = True
        self._is_batch_generation_context = True
        self.btn_batch_generate.setText("\U0001f6d1 停止批量生成")
        self.log_console.append(
            f"<font color='cyan'>🚀 开始依次生成勾选正文，共 {len(self.batch_generate_queue)} 个节点...</font>"
        )
        for info in skipped_info:
            self.log_console.append(f"<font color='orange'>{info}</font>")
        self._process_next_batch_node()

    def start_batch_generate(self: "NovelCreatorWindow"):
        if self.is_batch_generating:
            self.is_batch_generating = False
            self.batch_generate_queue.clear()
            self.btn_batch_generate.setText("\U0001f680 批量生成缺失场景")
            self.log_console.append(
                "<font color='orange'>"
                "\u26a0\ufe0f 已发送停止指令，将在当前节点完成后终止批量任务。"
                "</font>"
            )
            return

        if not self.workspace or not self.outline_tree_data:
            QMessageBox.warning(
                self, "提示", "请先打开并加载一个工作区！"  # type: ignore[arg-type]
            )
            return

        missing_nodes = get_missing_level3_nodes(
            self.outline_tree_data.get("nodes", [])
        )
        if not missing_nodes:
            QMessageBox.information(
                self,  # type: ignore[arg-type]
                "提示",
                "当前没有缺失正文的场景节点（没有灰色节点）。",
            )
            return

        reply = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "确认批量生成",
            f"大纲中找到了 {len(missing_nodes)} 个缺失文件（灰色）的场景。\n"
            "确认开始依次自动生成吗？这可能需要较长时间。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.batch_generate_queue = missing_nodes
            self.is_batch_generating = True
            self._is_batch_generation_context = True
            self.btn_batch_generate.setText("\U0001f6d1 停止批量生成")
            self._process_next_batch_node()

    def _process_next_batch_node(self: "NovelCreatorWindow"):
        if not self.is_batch_generating:
            self._is_batch_generation_context = False
            return

        if not self.batch_generate_queue:
            self.is_batch_generating = False
            self._is_batch_generation_context = False
            self.btn_batch_generate.setText("\U0001f680 批量生成缺失场景")
            self.btn_generate.setEnabled(True)
            self.btn_save.setEnabled(True)
            self._smart_setting_selection_enabled = self._get_smart_setting_selection_enabled()
            self._batch_gen_use_cache = True
            self._batch_gen_use_smart_select = False
            self.log_console.append(
                "<b><font color='green'>"
                "\U0001f389 批量生成任务全部完成！"
                "</font></b>"
            )
            QMessageBox.information(
                self, "完成", "批量生成已结束。"  # type: ignore[arg-type]
            )
            self._send_system_notification(
                "批量生成完成",
                "批量生成缺失场景任务已全部完成。",
            )
            return

        next_node = self.batch_generate_queue.pop(0)
        self.log_console.append(
            f"<hr><b>\u23f3 正在自动处理节点: {next_node.get('title')} "
            f"(队列剩余 {len(self.batch_generate_queue)} 个)</b>"
        )

        self.current_editing_node = next_node
        self.current_setting_path = None

        self.summary_editor.setText(next_node.get("summary", ""))
        self.summary_editor.setEnabled(True)

        self.content_editor.setText(f"# {next_node.get('title')}\n\n")
        self.content_editor.setEnabled(True)

        node_id = None
        for nid, n in self.node_map.items():
            if n is next_node:
                node_id = nid
                break

        if node_id:
            item = find_item_by_data(self.novel_tree.invisibleRootItem(), node_id)
            if item:
                self.current_editing_item = item
                self.novel_tree.setCurrentItem(item)

        self._is_batch_generation_context = True
        self.generate_current_node()

        self.content_editor.setText("（自动批量生成中，请稍候...）")
