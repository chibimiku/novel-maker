"""
后台工作线程模块
包含所有 QThread 子类，用于异步执行耗时的 AI 生成任务。
"""
import os
import json

from PyQt6.QtCore import QThread, pyqtSignal

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
            res_raw = self.llm_client.generate_text(prompt, override_system_instruction=json_sys_prompt)
            
            if res_raw.strip().startswith("> **生成失败:**"):
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
            list_res_raw = self.llm_client.generate_text(prompt_1, override_system_instruction=json_sys_prompt)

            if list_res_raw.strip().startswith("> **生成失败:**"):
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

                detail_res_raw = self.llm_client.generate_text(prompt_2, override_system_instruction=json_sys_prompt)
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
            
            res_raw = self.llm_client.generate_text(prompt, override_system_instruction=json_sys_prompt)
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
    success_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, llm_client, prompt_content, parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.prompt_content = prompt_content

    def run(self):
        try:
            result = self.llm_client.generate_text(self.prompt_content)
            # 【防雪崩修复】：拦截 llm_client 返回的文本格式错误信息
            if result.strip().startswith("> **生成失败:**"):
                error_msg = result.replace("> **生成失败:**", "").strip()
                self.error_signal.emit(error_msg)
            else:
                self.success_signal.emit(result)
        except Exception as e:
            self.error_signal.emit(str(e))
