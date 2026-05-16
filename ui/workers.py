"""
后台工作线程模块
包含所有 QThread 子类，用于异步执行耗时的 AI 生成任务。
"""
import os
import json
import uuid
import re

from PyQt6.QtCore import QThread, pyqtSignal

from core.workspace_manager import WorkspaceManager
from core.html_exporter import HtmlExporter
from ui.utils import clean_json_string


# ================= 大纲自动生成线程 =================
class OutlineBuildingThread(QThread):
    progress_signal = pyqtSignal(str)
    success_signal = pyqtSignal(dict) # 返回解析后的 JSON 字典
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, idea, settings_text, prompt_tpl, parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.idea = idea
        self.settings_text = settings_text
        self.prompt_tpl = prompt_tpl

    def run(self):
        try:
            self.progress_signal.emit("正在分析点子和设定，构建三级大纲结构...")
            
            # 强化 JSON 格式的系统指令
            json_sys_prompt = "你现在是一个专业的数据结构化助手。你必须严格按照用户的要求输出纯 JSON 格式的数据。绝对不允许使用 Markdown 代码块（严禁出现 ```json 和 ```），严禁包含任何前言或解释说明。"
            
            prompt = self.prompt_tpl.format(idea=self.idea, settings_text=self.settings_text)
            res_raw = self.llm_client.generate_text(
                prompt,
                override_system_instruction=json_sys_prompt,
                progress_callback=self.progress_signal.emit,
            )
            
            if res_raw and res_raw.strip().startswith("> **生成失败:**"):
                self.error_signal.emit(res_raw)
                return
                
            res = clean_json_string(res_raw)
            try:
                outline_data = json.loads(res)
            except json.JSONDecodeError:
                self.error_signal.emit("大模型返回的大纲 JSON 解析失败，格式错乱。")
                return
            
            if not isinstance(outline_data, dict) or "nodes" not in outline_data:
                self.error_signal.emit("大模型返回的数据结构异常，必须包含 'nodes' 顶级键。")
                return
            
            self.success_signal.emit(outline_data)
        except Exception as e:
            self.error_signal.emit(str(e))


# ================= 世界观批量生成线程 =================
class WorldBuildingThread(QThread):
    progress_signal = pyqtSignal(str)
    success_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, workspace, idea, prompt_1_tpl, prompt_2_tpl, mode="init", parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.workspace = workspace
        self.idea = idea
        self.mode = mode # 'init' 为全新生成，'supplement' 为补充生成
        self.prompt_1_tpl = prompt_1_tpl
        self.prompt_2_tpl = prompt_2_tpl

    def run(self):
        try:
            self.progress_signal.emit("正在分析点子，规划设定目录架构...")

            # 定义世界观生成的专用系统指令，优化后的专用系统指令，强调数组和去格式化
            json_sys_prompt = "你现在是一个专业的数据结构化助手。你必须严格按照用户的要求输出纯 JSON 格式的数据。绝对不允许使用 Markdown 代码块（严禁出现 ```json 和 ```），严禁包含任何前言或解释说明。请确保你的输出直接以 '[' 开头，以 ']' 结尾。"
            
            # 第一步：获取设定列表
            existing_context = ""
            if self.mode == "supplement":
                existing_context = "当前已有设定的概括如下，请基于这些内容进行针对性补充，避免重复：\n"
                for cat in self.workspace.setting_dirs:
                    cat_path = os.path.join(self.workspace.settings_path, cat)
                    if os.path.exists(cat_path):
                        files = [f.replace('.json', '') for f in os.listdir(cat_path) if f.endswith('.json') and f != 'template.json']
                        existing_context += f"【{cat}】: {', '.join(files)}\n"

            prompt_1 = self.prompt_1_tpl.format(idea=self.idea, existing_context=existing_context)
            list_res_raw = self.llm_client.generate_text(
                prompt_1,
                override_system_instruction=json_sys_prompt,
                progress_callback=self.progress_signal.emit,
            )

            if list_res_raw and list_res_raw.strip().startswith("> **生成失败:**"):
                self.error_signal.emit(list_res_raw)
                return
                
            list_res = clean_json_string(list_res_raw)
            try:
                settings_list = json.loads(list_res)
            except json.JSONDecodeError:
                self.error_signal.emit("大模型返回的规划列表 JSON 格式解析失败。")
                return

            if not isinstance(settings_list, list):
                self.error_signal.emit("大模型返回的数据结构异常，要求数组结构。")
                return

            total = len(settings_list)
            self.progress_signal.emit(f"规划完成，共需要生成/补充 {total} 个设定。开始逐一生成细节...")

            # 第二步：循环请求每个设定的细节
            for idx, item in enumerate(settings_list):
                cat = item.get("category", "其他设定")
                name = item.get("name", f"未命名_{idx}")
                summary = item.get("summary", "")
                
                # 确保分类在既定目录中
                if cat not in self.workspace.setting_dirs:
                    cat = "其他设定"

                self.progress_signal.emit(f"({idx+1}/{total}) 正在生成细节: {cat} - {name} ...")
                
                # 读取模板
                template_path = os.path.join(self.workspace.settings_path, cat, "template.json")
                template_str = "{}"
                if os.path.exists(template_path):
                    with open(template_path, 'r', encoding='utf-8') as f:
                        template_str = f.read()

                prompt_2 = self.prompt_2_tpl.format(
                    cat=cat, name=name, summary=summary, template_str=template_str
                )

                detail_res_raw = self.llm_client.generate_text(
                    prompt_2,
                    override_system_instruction=json_sys_prompt,
                    progress_callback=self.progress_signal.emit,
                )
                detail_res = clean_json_string(detail_res_raw)
                
                try:
                    detail_data = json.loads(detail_res)
                    
                    # 【防御性编程】：如果 AI 强行返回了数组（例如 [{...}]），则提取第一个字典元素
                    if isinstance(detail_data, list):
                        if len(detail_data) > 0 and isinstance(detail_data[0], dict):
                            detail_data = detail_data[0]
                        else:
                            detail_data = {"错误说明": "AI 生成的数组中没有有效的字典对象", "raw_content": detail_res}
                            
                    # 补充一个 name 字段防止模板里漏了
                    if isinstance(detail_data, dict):
                        detail_data["_node_name"] = name
                    else:
                        detail_data = {"错误说明": "AI 返回的数据类型不是对象或数组", "raw_content": detail_res}
                        
                except json.JSONDecodeError:
                    # 如果这一个节点解析失败，写入纯文本让用户手动修
                    detail_data = {"错误说明": "AI 生成了非法的 JSON", "raw_content": detail_res}

                # 写入文件
                file_path = os.path.join(self.workspace.settings_path, cat, f"{name}.json")
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(detail_data, f, ensure_ascii=False, indent=4)

            self.success_signal.emit()
            
        except Exception as e:
            self.error_signal.emit(str(e))


# ================= 目录索引生成线程 =================
class IndexGenerateThread(QThread):
    progress_signal = pyqtSignal(str)
    success_signal = pyqtSignal(str, str) # category, index_content
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, workspace, category, prompt_tpl, parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.workspace = workspace
        self.category = category
        self.prompt_tpl = prompt_tpl

    def run(self):
        try:
            cat_path = os.path.join(self.workspace.settings_path, self.category)
            content_list = []
            
            for file in os.listdir(cat_path):
                if file.endswith('.json') and file not in ['template.json', 'index.json']:
                    with open(os.path.join(cat_path, file), 'r', encoding='utf-8') as f:
                        content_list.append(f"【文件名】: {file}\n【内容】: {f.read()}\n")
            
            if not content_list:
                self.error_signal.emit(f"目录【{self.category}】下没有有效设定文件，无需生成索引。")
                return

            self.progress_signal.emit(f"正在读取【{self.category}】内容，构建概括目录...")
            all_content = "\n".join(content_list)

            json_sys_prompt = "你现在是一个专业的数据归纳与结构化助手。你必须严格按照用户的要求输出纯 JSON 格式的数据，绝对不允许包含任何多余的解释文本或 Markdown 代码块。"
            prompt = self.prompt_tpl.format(category=self.category, all_content=all_content)
            
            res_raw = self.llm_client.generate_text(
                prompt,
                override_system_instruction=json_sys_prompt,
                progress_callback=self.progress_signal.emit,
            )
            res = clean_json_string(res_raw)
            
            try:
                json.loads(res) # 校验是否能正常解析
            except json.JSONDecodeError:
                self.error_signal.emit("大模型返回的索引 JSON 解析失败。")
                return
                
            self.success_signal.emit(self.category, res)
        except Exception as e:
            self.error_signal.emit(str(e))


# ================= 通用文本生成线程 =================
class GenerateTaskThread(QThread):
    # 定义两个信号，用于向主线程传递成功的结果或失败的错误信息
    progress_signal = pyqtSignal(str)
    success_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, prompt_content, override_system_instruction=None, parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.prompt_content = prompt_content
        self.override_system_instruction = override_system_instruction

    def run(self):
        try:
            result = self.llm_client.generate_text(
                self.prompt_content,
                override_system_instruction=self.override_system_instruction,
                progress_callback=self.progress_signal.emit,
            )
            # 【防雪崩修复】：拦截 llm_client 返回的文本格式错误信息
            if result and result.strip().startswith("> **生成失败:**"):
                error_msg = result.replace("> **生成失败:**", "").strip()
                self.error_signal.emit(error_msg)
            else:
                self.success_signal.emit(result)
        except Exception as e:
            self.error_signal.emit(str(e))


class ProofreadThread(QThread):
    progress_signal = pyqtSignal(str)
    meta_signal = pyqtSignal(dict)
    success_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        llm_client,
        workspace,
        config,
        mode: str,
        resume: bool,
        stop_event=None,
        skip_existing_pending: bool = True,
        include_categories: set[str] | None = None,
        include_chapters: set[str] | None = None,
        find_include_chapters: set[str] | None = None,
        find_scene_title_keywords: list[str] | None = None,
        parallel_passes: int | None = None,
        parallel_solves: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.llm_client = llm_client
        self.workspace = workspace
        self.config = config or {}
        self.mode = str(mode or "").strip().lower()
        self.resume = bool(resume)
        self.stop_event = stop_event
        self.skip_existing_pending = bool(skip_existing_pending)
        self.include_categories = include_categories
        self.include_chapters = include_chapters
        self.find_include_chapters = find_include_chapters
        self.find_scene_title_keywords = find_scene_title_keywords
        self.parallel_passes = parallel_passes
        self.parallel_solves = parallel_solves

    def run(self):
        try:
            if not self.workspace:
                self.error_signal.emit("工作区为空，无法启动校对。")
                return
            if not self.llm_client:
                self.error_signal.emit("未配置大模型 API，无法启动校对。请先在“设置”中配置。")
                return

            import os
            import json

            from modules.proofread import create_default_engine
            from modules.proofread.engine import build_issue_from_dict

            runtime_config = dict(self.config or {})
            proofread_cfg = dict(runtime_config.get("proofread", {}) or {})
            if self.parallel_passes is not None:
                proofread_cfg["parallel_passes"] = int(self.parallel_passes)
            if self.parallel_solves is not None:
                proofread_cfg["parallel_solves"] = int(self.parallel_solves)
            runtime_config["proofread"] = proofread_cfg

            engine = create_default_engine(workspace=self.workspace, llm_client=self.llm_client, config=runtime_config)

            if self.mode == "find":
                data = engine.loader.load_workspace_data(
                    include_chapters=self.find_include_chapters,
                    scene_title_keywords=self.find_scene_title_keywords,
                )
                scene_list = data.get("scene_list", []) or []
                tasks_total = len(scene_list) * len(engine.pass_instances)
                self.meta_signal.emit(
                    {
                        "phase": "find",
                        "scenes_total": len(scene_list),
                        "passes_total": len(engine.pass_instances),
                        "tasks_total": tasks_total,
                        "resume": self.resume,
                        "parallel_passes": proofread_cfg.get("parallel_passes"),
                        "filters": {
                            "chapters": sorted(self.find_include_chapters) if self.find_include_chapters else [],
                            "keywords": [str(x) for x in (self.find_scene_title_keywords or []) if str(x).strip()],
                        },
                    }
                )
                try:
                    if self.resume:
                        issues = engine.resume_find_scoped(
                            include_chapters=self.find_include_chapters,
                            scene_title_keywords=self.find_scene_title_keywords,
                            stop_event=self.stop_event,
                            progress_callback=self.progress_signal.emit,
                        )
                    else:
                        issues = engine.run_find_scoped(
                            include_chapters=self.find_include_chapters,
                            scene_title_keywords=self.find_scene_title_keywords,
                            stop_event=self.stop_event,
                            progress_callback=self.progress_signal.emit,
                        )
                except InterruptedError:
                    self.success_signal.emit({"phase": "find", "status": "paused"})
                    return

                self.success_signal.emit(
                    {
                        "phase": "find",
                        "status": "completed",
                        "issues_count": len(issues or []),
                    }
                )
                return

            if self.mode == "solve":
                issues_path = os.path.join(self.workspace.sys_data_path, "proofread_issues.json")
                if not os.path.exists(issues_path):
                    self.error_signal.emit("没有找到问题清单 proofread_issues.json，请先执行“查找问题（Find）”。")
                    return
                with open(issues_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                issue_items = None
                if isinstance(raw, list):
                    issue_items = raw
                elif isinstance(raw, dict):
                    issue_items = raw.get("issues")
                if not isinstance(issue_items, list):
                    self.error_signal.emit("proofread_issues.json 格式异常，期望为数组或包含 issues 的对象。")
                    return
                issues = [build_issue_from_dict(x) for x in issue_items if isinstance(x, dict)]

                grouped = engine.solve_engine.group_issues_by_scene(
                    issues=issues,
                    include_categories=self.include_categories,
                    include_chapters=self.include_chapters,
                )
                pending_skipped = 0
                if self.skip_existing_pending and grouped:
                    for node_id in grouped.keys():
                        if self.workspace.has_pending_modify(node_id):
                            pending_skipped += 1
                self.meta_signal.emit(
                    {
                        "phase": "solve",
                        "scenes_total": max(0, len(grouped) - pending_skipped),
                        "pending_skipped": pending_skipped,
                        "filters": {
                            "categories": sorted(self.include_categories) if self.include_categories else [],
                            "chapters": sorted(self.include_chapters) if self.include_chapters else [],
                        },
                        "resume": self.resume,
                        "parallel_solves": proofread_cfg.get("parallel_solves"),
                    }
                )
                try:
                    if self.resume:
                        stats = engine.resume_solve(
                            issues=issues,
                            stop_event=self.stop_event,
                            progress_callback=self.progress_signal.emit,
                            include_categories=self.include_categories,
                            include_chapters=self.include_chapters,
                            skip_existing_pending=self.skip_existing_pending,
                        )
                    else:
                        stats = engine.run_solve(
                            issues=issues,
                            stop_event=self.stop_event,
                            progress_callback=self.progress_signal.emit,
                            include_categories=self.include_categories,
                            include_chapters=self.include_chapters,
                            skip_existing_pending=self.skip_existing_pending,
                        )
                except InterruptedError:
                    self.success_signal.emit({"phase": "solve", "status": "paused"})
                    return

                self.success_signal.emit(
                    {
                        "phase": "solve",
                        "status": "completed",
                        "stats": stats or {},
                    }
                )
                return

            self.error_signal.emit(f"未知的校对模式: {self.mode}")
        except Exception as e:
            self.error_signal.emit(str(e))


# ================= 从文本导入工作区线程 =================
class ImportTextWorkspaceThread(QThread):
    progress_signal = pyqtSignal(str)
    success_signal = pyqtSignal(str) # 返回工作区路径
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, selected_files, word_count, workspace_path, get_prompt_template_func, parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.selected_files = selected_files
        self.word_count = word_count
        self.workspace_path = workspace_path
        self.get_prompt_template_func = get_prompt_template_func

    def _get_prompt_template(self, template_name, default_content=""):
        return self.get_prompt_template_func(template_name, default_content)

    def _split_text_into_segments(self, content, target_word_count):
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

    def _create_chapter_node(self, title, summary):
        return {
            "id": str(uuid.uuid4()),
            "title": title,
            "summary": summary,
            "children": [],
            "_status": "ok"
        }

    def _create_section_node(self, title, summary):
        return {
            "id": str(uuid.uuid4()),
            "title": title,
            "summary": summary,
            "children": [],
            "_status": "ok"
        }

    def _create_scene_node(self, title, summary, content, workspace):
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

    def _append_to_scene_node(self, scene_node, content, workspace):
        file_path = scene_node.get("file_path")
        if file_path:
            full_path = os.path.join(workspace.text_path, file_path)
            if os.path.exists(full_path):
                with open(full_path, 'r', encoding='utf-8') as f:
                    existing_content = f.read()
                new_content = existing_content + '\n\n' + content
                new_md5 = workspace.save_markdown_file(file_path, new_content)
                scene_node["md5"] = new_md5

    def _process_text_files_simple(self, selected_files, word_count, workspace):
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

    def _split_into_sections(self, content):
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

    def _split_content_by_word_count(self, content, target_word_count):
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

    def run(self):
        try:
            self.progress_signal.emit("开始从文本创建工作区...")

            temp_workspace = WorkspaceManager(self.workspace_path)
            temp_workspace.init_workspace()

            if self.llm_client:
                outline_tree_data = self._process_text_files_with_llm(
                    self.selected_files, self.word_count, temp_workspace
                )
            else:
                outline_tree_data = self._process_text_files_simple(
                    self.selected_files, self.word_count, temp_workspace
                )

            temp_workspace.save_outline_tree(outline_tree_data)

            if self.llm_client:
                self._extract_settings_from_text(self.selected_files, temp_workspace)

            self.success_signal.emit(self.workspace_path)

        except Exception as e:
            self.error_signal.emit(f"从文本创建工作区失败:\n{str(e)}")

    def _process_text_files_with_llm(self, selected_files, word_count, workspace):
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
                self.progress_signal.emit(f"处理第 {segment_idx + 1}/{len(segments)} 段文本...")

                current_outline_str = json.dumps(outline_tree_data, ensure_ascii=False, indent=2)

                prompt = self._get_prompt_template("text_import_process.txt", "")
                prompt = prompt.format(
                    current_outline=current_outline_str,
                    text_content=segment
                )

                try:
                    response = self.llm_client.generate_text(
                        prompt,
                        progress_callback=self.progress_signal.emit,
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
                    self.progress_signal.emit(f"<font color='red'>LLM处理失败，使用简单模式: {e}</font>")
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

    def _extract_settings_from_text(self, selected_files, workspace):
        self.progress_signal.emit("正在使用LLM提取设定...")

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
                progress_callback=self.progress_signal.emit,
            )
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
                self.progress_signal.emit("设定提取完成！")
        except Exception as e:
            self.progress_signal.emit(f"<font color='red'>设定提取失败: {e}</font>")

    def _save_setting_to_workspace(self, workspace, category, name, data):
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', name)
        file_name = f"{safe_name}_{uuid.uuid4().hex[:8]}.json"
        file_path = os.path.join(workspace.settings_path, category, file_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)


# ================= HTML 导出线程 =================
class HtmlExportThread(QThread):
    success_signal = pyqtSignal(str)  # output_file
    error_signal = pyqtSignal(str)

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace

    def run(self):
        try:
            exporter = HtmlExporter(self.workspace)
            output_file = exporter.export()
            self.success_signal.emit(output_file)
        except Exception as e:
            self.error_signal.emit(str(e))
