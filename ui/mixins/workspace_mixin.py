"""
WorkspaceMixin —— 工作区的新建、加载、重载逻辑。
"""
from __future__ import annotations

import os
import uuid
import re
import json
from typing import TYPE_CHECKING, Any

from PyQt6.QtWidgets import QFileDialog, QMessageBox
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl

from core.llm_client import LLMClient
from core.workspace_manager import WorkspaceManager
from ui.import_text_dialog import ImportTextWorkspaceDialog
from ui.workers import ImportTextWorkspaceThread

if TYPE_CHECKING:
    from ui.main_window import NovelCreatorWindow


class WorkspaceMixin:
    """处理工作区的新建 / 加载 / 重载操作。"""

    def open_workspace_folder(self: Any):
        """在系统文件管理器中打开当前工作区目录。"""
        if not self.workspace:
            QMessageBox.information(
                self, "提示", "当前未打开任何工作区。"  # type: ignore[arg-type]
            )
            return

        workspace_path = self.workspace.workspace_path
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(workspace_path))
        if not ok:
            QMessageBox.warning(
                self,
                "打开失败",
                f"无法打开工作区目录：\n{workspace_path}",  # type: ignore[arg-type]
            )

    def on_workspace_nsfw_toggled(self: Any, checked: bool):
        """切换工作区 NSFW 状态，并立即切换生效的文本模型配置。"""
        if not self.workspace:
            return
        self.workspace.set_is_nsfw(bool(checked))
        self._workspace_is_nsfw = bool(checked)
        self.config = self._load_config()
        self.llm_client = LLMClient(self.config) if self.config else None
        self._apply_workspace_instruction_profile()
        mode_name = "NSFW" if checked else "普通"
        self.log_console.append(f"已切换工作区模式：{mode_name}（文本模型配置已重载）")

    def _get_prompt_template(self: Any, template_name: str, default_content: str = "") -> str:
        """获取prompt模板"""
        prompt_path = os.path.join("data", "prompts", template_name)
        if os.path.exists(prompt_path):
            try:
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception:
                pass
        return default_content

    def new_workspace_from_text(self: Any):
        """从文本新建工作区"""
        dialog = ImportTextWorkspaceDialog(self)
        if dialog.exec() != ImportTextWorkspaceDialog.DialogCode.Accepted:
            return
        
        selected_files = dialog.get_selected_files()
        word_count = dialog.get_word_count()
        workspace_path = dialog.get_workspace_path()
        
        if not selected_files or not workspace_path:
            return
        
        if os.listdir(workspace_path):
            QMessageBox.warning(
                self, "操作取消",
                "为了防止意外覆盖文件，请选择一个【空文件夹】来初始化新工作区！"
            )
            return
        
        self.log_console.append("开始从文本创建工作区...")
        
        worker = ImportTextWorkspaceThread(
            self.llm_client, 
            selected_files, 
            word_count, 
            workspace_path, 
            self._get_prompt_template, 
            self
        )
        
        worker.progress_signal.connect(lambda msg: self.log_console.append(msg))
        worker.success_signal.connect(self._on_import_success)
        worker.error_signal.connect(lambda msg: QMessageBox.critical(self, "错误", msg))
        
        worker.start()
    
    def _on_import_success(self: Any, workspace_path):
        """导入成功回调"""
        self._load_workspace_by_path(workspace_path)
        self.log_console.append("<font color='green'>工作区创建成功！</font>")
        self._send_system_notification(
            "导入完成",
            "从文本创建工作区任务已完成。",
        )
    
    def _process_text_files_with_llm(self: Any, selected_files, word_count, workspace):
        """使用LLM智能处理文本文件并转换为小说大纲结构"""
        outline_tree_data = {
            "project_name": os.path.basename(workspace.workspace_path),
            "nodes": []
        }
        
        chapter_counter = 1
        section_counter = 1
        scene_counter = 1
        
        for file_path in selected_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                try:
                    with open(file_path, 'r', encoding='gbk') as f:
                        content = f.read()
                except Exception:
                    continue
            
            content = content.replace('\r\n', '\n').replace('\r', '\n')
            
            segments = self._split_text_into_segments(content, word_count)
            
            current_chapter = None
            current_section = None
            current_scene = None
            
            for segment_idx, segment in enumerate(segments):
                self.log_console.append(f"处理第 {segment_idx + 1}/{len(segments)} 段文本...")
                
                current_outline_str = json.dumps(outline_tree_data, ensure_ascii=False, indent=2)
                
                prompt = self._get_prompt_template("text_import_process.txt", "")
                prompt = prompt.format(
                    current_outline=current_outline_str,
                    text_content=segment
                )
                
                try:
                    response = self.llm_client.generate_text(
                        prompt,
                        progress_callback=lambda msg: self.log_console.append(
                            f"<font color='gray'>[LLM进度] {msg}</font>"
                        ),
                    )
                    json_match = re.search(r'\{[\s\S]*\}', response)
                    
                    if json_match:
                        result = json.loads(json_match.group())
                        action = result.get("action", "new_scene")
                        summary = result.get("summary", "")
                        new_node_title = result.get("new_node_title", "")
                        
                        if action == "new_chapter":
                            chapter_title = new_node_title or f"第{chapter_counter}章"
                            chapter_counter += 1
                            current_chapter = self._create_chapter_node(chapter_title, summary)
                            outline_tree_data["nodes"].append(current_chapter)
                            
                            section_title = "第1节"
                            current_section = self._create_section_node(section_title, "")
                            current_chapter["children"].append(current_section)
                            
                            scene_title = f"场景{scene_counter}"
                            scene_counter += 1
                            scene_content = result.get("new_content", segment)
                            current_scene = self._create_scene_node(scene_title, summary, scene_content, workspace)
                            current_section["children"].append(current_scene)
                        
                        elif action == "new_section":
                            if not current_chapter:
                                chapter_title = f"第{chapter_counter}章"
                                chapter_counter += 1
                                current_chapter = self._create_chapter_node(chapter_title, "")
                                outline_tree_data["nodes"].append(current_chapter)
                            
                            section_title = new_node_title or f"第{section_counter}节"
                            section_counter += 1
                            current_section = self._create_section_node(section_title, summary)
                            current_chapter["children"].append(current_section)
                            
                            scene_title = f"场景{scene_counter}"
                            scene_counter += 1
                            scene_content = result.get("new_content", segment)
                            current_scene = self._create_scene_node(scene_title, summary, scene_content, workspace)
                            current_section["children"].append(current_scene)
                        
                        elif action == "new_scene":
                            if not current_chapter:
                                chapter_title = f"第{chapter_counter}章"
                                chapter_counter += 1
                                current_chapter = self._create_chapter_node(chapter_title, "")
                                outline_tree_data["nodes"].append(current_chapter)
                            
                            if not current_section:
                                section_title = f"第{section_counter}节"
                                section_counter += 1
                                current_section = self._create_section_node(section_title, "")
                                current_chapter["children"].append(current_section)
                            
                            scene_title = new_node_title or f"场景{scene_counter}"
                            scene_counter += 1
                            scene_content = result.get("new_content", segment)
                            current_scene = self._create_scene_node(scene_title, summary, scene_content, workspace)
                            current_section["children"].append(current_scene)
                        
                        elif action == "append_to_current":
                            if current_scene:
                                self._append_to_scene_node(current_scene, segment, workspace)
                            else:
                                scene_title = f"场景{scene_counter}"
                                scene_counter += 1
                                current_scene = self._create_scene_node(scene_title, summary, segment, workspace)
                                if not current_section:
                                    if not current_chapter:
                                        chapter_title = f"第{chapter_counter}章"
                                        chapter_counter += 1
                                        current_chapter = self._create_chapter_node(chapter_title, "")
                                        outline_tree_data["nodes"].append(current_chapter)
                                    section_title = f"第{section_counter}节"
                                    section_counter += 1
                                    current_section = self._create_section_node(section_title, "")
                                    current_chapter["children"].append(current_section)
                                current_section["children"].append(current_scene)
                        
                        elif action == "split":
                            append_content = result.get("append_content", "")
                            new_content = result.get("new_content", segment)
                            
                            if current_scene and append_content:
                                self._append_to_scene_node(current_scene, append_content, workspace)
                            
                            if new_content:
                                scene_title = new_node_title or f"场景{scene_counter}"
                                scene_counter += 1
                                current_scene = self._create_scene_node(scene_title, summary, new_content, workspace)
                                if not current_section:
                                    if not current_chapter:
                                        chapter_title = f"第{chapter_counter}章"
                                        chapter_counter += 1
                                        current_chapter = self._create_chapter_node(chapter_title, "")
                                        outline_tree_data["nodes"].append(current_chapter)
                                    section_title = f"第{section_counter}节"
                                    section_counter += 1
                                    current_section = self._create_section_node(section_title, "")
                                    current_chapter["children"].append(current_section)
                                current_section["children"].append(current_scene)
                    
                except Exception as e:
                    self.log_console.append(f"<font color='red'>LLM处理失败，使用简单模式: {e}</font>")
                    if not current_chapter:
                        chapter_title = f"第{chapter_counter}章"
                        chapter_counter += 1
                        current_chapter = self._create_chapter_node(chapter_title, "")
                        outline_tree_data["nodes"].append(current_chapter)
                    if not current_section:
                        section_title = f"第{section_counter}节"
                        section_counter += 1
                        current_section = self._create_section_node(section_title, "")
                        current_chapter["children"].append(current_section)
                    scene_title = f"场景{scene_counter}"
                    scene_counter += 1
                    current_scene = self._create_scene_node(scene_title, "", segment, workspace)
                    current_section["children"].append(current_scene)
        
        return outline_tree_data
    
    def _split_text_into_segments(self: Any, content, target_word_count):
        """将文本分割成合适大小的片段，在换行处分割"""
        segments = []
        lines = content.split('\n')
        
        current_segment = []
        current_word_count = 0
        
        for line in lines:
            line_word_count = len(line.replace(' ', ''))
            
            if current_word_count + line_word_count > target_word_count * 1.2 and current_segment:
                segments.append('\n'.join(current_segment).strip())
                current_segment = [line]
                current_word_count = line_word_count
            else:
                current_segment.append(line)
                current_word_count += line_word_count
        
        if current_segment:
            segments.append('\n'.join(current_segment).strip())
        
        return segments
    
    def _create_chapter_node(self: Any, title, summary):
        """创建章节节点"""
        return {
            "id": str(uuid.uuid4()),
            "title": title,
            "summary": summary,
            "children": [],
            "_status": "ok"
        }
    
    def _create_section_node(self: Any, title, summary):
        """创建节节点"""
        return {
            "id": str(uuid.uuid4()),
            "title": title,
            "summary": summary,
            "children": [],
            "_status": "ok"
        }
    
    def _create_scene_node(self: Any, title, summary, content, workspace):
        """创建场景节点"""
        file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
        initial_content = f"# {title}\n\n{content}"
        initial_md5 = workspace.save_markdown_file(file_name, initial_content)
        
        return {
            "id": str(uuid.uuid4()),
            "title": title,
            "summary": summary,
            "file_path": file_name,
            "md5": initial_md5,
            "children": [],
            "_status": "ok"
        }
    
    def _append_to_scene_node(self: Any, scene_node, content, workspace):
        """追加内容到现有场景节点"""
        file_path = scene_node.get("file_path")
        if file_path:
            full_path = os.path.join(workspace.text_path, file_path)
            if os.path.exists(full_path):
                with open(full_path, 'r', encoding='utf-8') as f:
                    existing_content = f.read()
                
                new_content = existing_content + '\n\n' + content
                new_md5 = workspace.save_markdown_file(file_path, new_content)
                scene_node["md5"] = new_md5
    
    def _process_text_files_simple(self: Any, selected_files, word_count, workspace):
        """简单模式处理文本文件（无LLM）"""
        nodes = []
        chapter_counter = 1
        scene_counter = 1
        
        for file_idx, file_path in enumerate(selected_files):
            file_name = os.path.basename(file_path)
            base_name = os.path.splitext(file_name)[0]
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                try:
                    with open(file_path, 'r', encoding='gbk') as f:
                        content = f.read()
                except Exception:
                    continue
            
            content = content.replace('\r\n', '\n').replace('\r', '\n')
            
            chapter_title = f"第{chapter_counter}章 - {base_name}"
            chapter_counter += 1
            
            chapter_node = {
                "id": str(uuid.uuid4()),
                "title": chapter_title,
                "summary": "",
                "children": [],
                "_status": "ok"
            }
            
            sections = self._split_into_sections(content)
            
            if not sections:
                sections = [content]
            
            section_counter = 1
            for section_content in sections:
                section_title = f"第{section_counter}节"
                section_counter += 1
                
                section_node = {
                    "id": str(uuid.uuid4()),
                    "title": section_title,
                    "summary": "",
                    "children": [],
                    "_status": "ok"
                }
                
                scenes = self._split_content_by_word_count(section_content, word_count)
                
                for scene_content in scenes:
                    scene_title = f"场景{scene_counter}"
                    scene_counter += 1
                    
                    file_name = f"场景_{uuid.uuid4().hex[:8]}.md"
                    initial_content = f"# {scene_title}\n\n{scene_content}"
                    initial_md5 = workspace.save_markdown_file(file_name, initial_content)
                    
                    scene_node = {
                        "id": str(uuid.uuid4()),
                        "title": scene_title,
                        "summary": "",
                        "file_path": file_name,
                        "md5": initial_md5,
                        "children": [],
                        "_status": "ok"
                    }
                    section_node["children"].append(scene_node)
                
                chapter_node["children"].append(section_node)
            
            nodes.append(chapter_node)
        
        outline_tree_data = {
            "project_name": os.path.basename(workspace.workspace_path),
            "nodes": nodes
        }
        
        return outline_tree_data
    
    def _split_into_sections(self: Any, content):
        """根据标题或章节标记将内容分割成节"""
        sections = []
        pattern = r'^#{1,6}\s+.*$|^第[一二三四五六七八九十百千\d]+[章节卷篇].*$'
        
        lines = content.split('\n')
        current_section = []
        
        for line in lines:
            if re.match(pattern, line.strip()):
                if current_section:
                    sections.append('\n'.join(current_section).strip())
                current_section = [line]
            else:
                current_section.append(line)
        
        if current_section:
            sections.append('\n'.join(current_section).strip())
        
        if len(sections) == 1 and not re.match(pattern, sections[0].split('\n')[0].strip()):
            return []
        
        return sections
    
    def _split_content_by_word_count(self: Any, content, target_word_count):
        """按字数分割内容成多个场景"""
        scenes = []
        paragraphs = content.split('\n\n')
        
        current_scene = []
        current_word_count = 0
        
        for para in paragraphs:
            para_word_count = len(para.replace(' ', '').replace('\n', ''))
            
            if current_word_count + para_word_count > target_word_count * 1.2 and current_scene:
                scenes.append('\n\n'.join(current_scene).strip())
                current_scene = [para]
                current_word_count = para_word_count
            else:
                current_scene.append(para)
                current_word_count += para_word_count
        
        if current_scene:
            scenes.append('\n\n'.join(current_scene).strip())
        
        return scenes
    
    def _extract_settings_from_text(self: Any, selected_files, workspace):
        """使用LLM从文本中提取设定（人物、场景等）"""
        self.log_console.append("正在使用LLM提取设定...")
        
        all_content = ""
        for file_path in selected_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    all_content += f.read() + "\n"
            except Exception:
                continue
        
        if len(all_content) > 10000:
            all_content = all_content[:10000]
        
        prompt = f"""请从以下文本中提取：
1. 重要人物（姓名、性格特点等）
2. 重要地点/场景
3. 重要专有名词

文本内容：
{all_content}

请以JSON格式返回，格式如下：
{{
  "characters": [
    {{"name": "人物名", "description": "人物描述"}}
  ],
  "locations": [
    {{"name": "地点名", "description": "地点描述"}}
  ],
  "terms": [
    {{"name": "专有名词", "description": "名词描述"}}
  ]
}}

只返回JSON，不要其他文字。"""
        
        try:
            response = self.llm_client.generate_text(
                prompt,
                progress_callback=lambda msg: self.log_console.append(
                    f"<font color='gray'>[LLM进度] {msg}</font>"
                ),
            )
            import json
            
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                settings_data = json.loads(json_match.group())
                
                for char in settings_data.get("characters", []):
                    self._save_setting_to_workspace(
                        workspace, "人物设定", 
                        char.get("name", "未命名人物"), 
                        {
                            "姓名": char.get("name", ""),
                            "年龄": "",
                            "性别": "",
                            "性格": "",
                            "背景描述": char.get("description", ""),
                            "特殊能力": ""
                        }
                    )
                
                for loc in settings_data.get("locations", []):
                    self._save_setting_to_workspace(
                        workspace, "地点设定",
                        loc.get("name", "未命名地点"),
                        {
                            "地点名称": loc.get("name", ""),
                            "地理位置": "",
                            "环境特征": loc.get("description", ""),
                            "历史背景": ""
                        }
                    )
                
                for term in settings_data.get("terms", []):
                    self._save_setting_to_workspace(
                        workspace, "名词设定",
                        term.get("name", "未命名名词"),
                        {
                            "专有名词": term.get("name", ""),
                            "类型": "",
                            "详细定义": term.get("description", ""),
                            "关联设定": ""
                        }
                    )
                
                self.log_console.append("设定提取完成！")
        except Exception as e:
            self.log_console.append(f"<font color='red'>设定提取失败: {e}</font>")
    
    def _save_setting_to_workspace(self: Any, workspace, category, name, data):
        """保存单个设定到工作区"""
        import json
        import uuid
        
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', name)
        file_name = f"{safe_name}_{uuid.uuid4().hex[:8]}.json"
        file_path = os.path.join(workspace.settings_path, category, file_name)
        
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def new_workspace(self: Any):
        folder_path = QFileDialog.getExistingDirectory(
            self, "选择空文件夹创建新工作区"  # type: ignore[arg-type]
        )
        if folder_path:
            if os.listdir(folder_path):
                QMessageBox.warning(
                    self,  # type: ignore[arg-type]
                    "操作取消",
                    "为了防止意外覆盖文件，请选择一个【空文件夹】来初始化新工作区！",
                )
                return

            try:
                temp_workspace = WorkspaceManager(folder_path)
                temp_workspace.init_workspace()
                self._load_workspace_by_path(folder_path)
            except Exception as e:
                QMessageBox.critical(
                    self, "错误", f"初始化新工作区失败:\n{str(e)}"  # type: ignore[arg-type]
                )

    def load_workspace(self: Any):
        folder_path = QFileDialog.getExistingDirectory(
            self, "选择已有的工作区目录"  # type: ignore[arg-type]
        )
        if folder_path:
            self._load_workspace_by_path(folder_path)

    def _load_workspace_by_path(self: Any, folder_path: str):
        try:
            self.workspace = WorkspaceManager(folder_path)
            self._save_sys_state(folder_path)
            if hasattr(self, "btn_open_workspace"):
                self.btn_open_workspace.setEnabled(True)
            self._workspace_is_nsfw = self.workspace.get_is_nsfw()
            if hasattr(self, "cb_workspace_nsfw"):
                self.cb_workspace_nsfw.blockSignals(True)
                self.cb_workspace_nsfw.setEnabled(True)
                self.cb_workspace_nsfw.setChecked(self._workspace_is_nsfw)
                self.cb_workspace_nsfw.blockSignals(False)
            self.config = self._load_config()
            self.llm_client = LLMClient(self.config) if self.config else None

            self.log_console.append(f"成功加载工作区: {folder_path}")
            self.setWindowTitle(f"AI小说创作器 - {os.path.basename(folder_path)}")
            self._apply_workspace_instruction_profile()

            self._loaded_mobile_issues = self.workspace.load_mobile_issues()
            if self._loaded_mobile_issues:
                self.issue_panel_frame.setVisible(True)
            self._refresh_issue_panel()

            self.refresh_ui_from_workspace()
        except Exception as e:
            QMessageBox.critical(
                self, "错误", f"无法加载工作区:\n{str(e)}"  # type: ignore[arg-type]
            )
            self.log_console.append(f"<font color='red'>工作区加载失败: {e}</font>")

    def reload_workspace(self: Any):
        if not self.workspace:
            QMessageBox.information(
                self, "提示", "当前未打开任何工作区，无法重载。"  # type: ignore[arg-type]
            )
            return

        reply = QMessageBox.question(
            self,  # type: ignore[arg-type]
            "重载工作区",
            "确定要重新加载当前工作区吗？\n警告：由于是强制读取本地硬盘配置，"
            "您当前未保存的所有修改（包含正在编辑的正文和概要）都将丢失！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.log_console.append("开始重载工作区...")
            # 重置大纲树数据
            self.outline_tree_data = None
            self._load_workspace_by_path(self.workspace.workspace_path)
