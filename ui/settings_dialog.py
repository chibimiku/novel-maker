import os
import json
import requests
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QComboBox, QTextEdit, 
                             QMessageBox, QTabWidget, QWidget, QFormLayout, QSpinBox, QCheckBox, QInputDialog)
from PyQt6.QtCore import Qt

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("系统配置 (API/模型)")
        self.resize(550, 600) # 稍微加大一点窗口以容纳新组件
        
        # 确定配置文件的路径 (假设工程结构为 root/ui/ 和 root/conf/)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config_dir = os.path.join(base_dir, "conf")
        self.config_path = os.path.join(self.config_dir, "setting.json")
        
        # 加载现有配置
        self.config = self.load_config()
        self.text_instructions_history = [] # 存放文本生成历史指令的列表
        self.summary_instructions_history = [] # 存放概要生成历史指令的列表
        self.modify_instructions_history = [] # 存放编辑改写历史指令的列表
        self.text_model_meta_catalog = {}
        
        self.init_ui()
        self.populate_data()

    def load_config(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                QMessageBox.warning(self, "警告", f"读取配置文件失败，将使用默认空配置。\n{e}")
        return {"text_api": {}, "image_api": {}}

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # 使用选项卡分离文本模型和图像模型的配置
        self.tabs = QTabWidget()
        
        # --- 文本模型 Tab ---
        self.text_tab = QWidget()
        self.text_layout = QFormLayout(self.text_tab)
        
        self.txt_type_combo = QComboBox()
        self.txt_type_combo.addItems(["openai", "gemini"])
        self.text_layout.addRow("接口类型 (Type):", self.txt_type_combo)
        
        self.txt_base_url_input = QLineEdit()
        self.txt_base_url_input.setPlaceholderText("例如: https://api.openai.com/v1")
        self.text_layout.addRow("请求地址 (Base URL):", self.txt_base_url_input)
        
        self.txt_api_key_input = QLineEdit()
        self.txt_api_key_input.setEchoMode(QLineEdit.EchoMode.Password) # 密码掩码
        self.text_layout.addRow("API Key:", self.txt_api_key_input)
        
        # 模型选择与获取按钮组合
        self.txt_model_combo = QComboBox()
        self.txt_model_combo.setEditable(True) # 允许用户手动输入或选择
        self.btn_fetch_txt_models = QPushButton("获取可用模型")
        self.btn_fetch_txt_models.clicked.connect(lambda: self.fetch_models("text"))
        self.txt_model_combo.currentTextChanged.connect(self.on_text_model_changed)
        
        txt_model_layout = QHBoxLayout()
        txt_model_layout.addWidget(self.txt_model_combo, stretch=1)
        txt_model_layout.addWidget(self.btn_fetch_txt_models)
        self.text_layout.addRow("模型名称 (Model):", txt_model_layout)

        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(10, 600)
        self.spin_timeout.setSuffix(" 秒")
        self.text_layout.addRow("请求超时时间 (Timeout):", self.spin_timeout)

        self.txt_model_capabilities_input = QLineEdit()
        self.txt_model_capabilities_input.setPlaceholderText("例如: chat, reasoning, long_context")
        self.text_layout.addRow("模型能力 (Capabilities):", self.txt_model_capabilities_input)

        self.spin_model_context_size = QSpinBox()
        self.spin_model_context_size.setRange(512, 2_000_000)
        self.spin_model_context_size.setSingleStep(1024)
        self.spin_model_context_size.setSuffix(" tokens")
        self.text_layout.addRow("上下文大小 (Context):", self.spin_model_context_size)

        self.world_compression_profile_combo = QComboBox()
        self.world_compression_profile_combo.addItem(
            "保守 (预算系数=0.45, 节点基线=180, 字段上限=320)", "conservative"
        )
        self.world_compression_profile_combo.addItem(
            "平衡 (预算系数=0.32, 节点基线=120, 字段上限=220)", "balanced"
        )
        self.world_compression_profile_combo.addItem(
            "激进 (预算系数=0.22, 节点基线=80, 字段上限=140)", "aggressive"
        )
        self.text_layout.addRow("世界观压缩策略:", self.world_compression_profile_combo)

        
        self.tabs.addTab(self.text_tab, "📝 文本生成模型")

        # --- 系统指令 Tab ---
        self.instructions_tab = QWidget()
        self.instructions_tab_layout = QVBoxLayout(self.instructions_tab)
        
        # 使用子选项卡分离文本生成和概要生成指令
        self.instructions_sub_tabs = QTabWidget()
        
        # --- 文本生成系统指令子 Tab ---
        self.text_instructions_tab = QWidget()
        self.text_instructions_layout = QFormLayout(self.text_instructions_tab)
        
        text_history_layout = QHBoxLayout()
        self.text_instruction_combo = QComboBox()
        self.btn_save_text_instruction = QPushButton("💾 存为新模板")
        self.btn_edit_text_name = QPushButton("✏️ 编辑名称")
        self.btn_delete_text_instruction = QPushButton("🗑️ 删除该模板")
        
        text_history_layout.addWidget(self.text_instruction_combo, stretch=1)
        text_history_layout.addWidget(self.btn_save_text_instruction)
        text_history_layout.addWidget(self.btn_edit_text_name)
        text_history_layout.addWidget(self.btn_delete_text_instruction)
        self.text_instructions_layout.addRow("历史系统指令:", text_history_layout)
        
        self.text_instruction_input = QTextEdit()
        self.text_instruction_input.setPlaceholderText("系统提示词，例如：你是一个专业的小说家...")
        self.text_instructions_layout.addRow("当前指令内容:", self.text_instruction_input)
        
        # 绑定文本生成指令历史记录相关事件
        self.text_instruction_combo.currentIndexChanged.connect(self.on_text_instruction_changed)
        self.btn_save_text_instruction.clicked.connect(self.save_text_instruction_to_history)
        self.btn_edit_text_name.clicked.connect(self.edit_instruction_name)
        self.btn_delete_text_instruction.clicked.connect(self.delete_instruction_from_history)
        
        self.instructions_sub_tabs.addTab(self.text_instructions_tab, "📝 文本生成系统指令")
        
        # --- 概要生成系统指令子 Tab ---
        self.summary_instructions_tab = QWidget()
        self.summary_instructions_layout = QFormLayout(self.summary_instructions_tab)
        
        summary_history_layout = QHBoxLayout()
        self.summary_instruction_combo = QComboBox()
        self.btn_save_summary_instruction = QPushButton("💾 存为新模板")
        self.btn_edit_summary_name = QPushButton("✏️ 编辑名称")
        self.btn_delete_summary_instruction = QPushButton("🗑️ 删除该模板")
        
        summary_history_layout.addWidget(self.summary_instruction_combo, stretch=1)
        summary_history_layout.addWidget(self.btn_save_summary_instruction)
        summary_history_layout.addWidget(self.btn_edit_summary_name)
        summary_history_layout.addWidget(self.btn_delete_summary_instruction)
        self.summary_instructions_layout.addRow("历史系统指令:", summary_history_layout)
        
        self.summary_instruction_input = QTextEdit()
        self.summary_instruction_input.setPlaceholderText("概要生成系统提示词，例如：你是一个专业的小说编辑，擅长为小说节点生成精炼的概要...")
        self.summary_instructions_layout.addRow("当前指令内容:", self.summary_instruction_input)
        
        # 绑定概要生成指令历史记录相关事件
        self.summary_instruction_combo.currentIndexChanged.connect(self.on_summary_instruction_changed)
        self.btn_save_summary_instruction.clicked.connect(self.save_summary_instruction_to_history)
        self.btn_edit_summary_name.clicked.connect(self.edit_instruction_name)
        self.btn_delete_summary_instruction.clicked.connect(self.delete_instruction_from_history)
        
        self.instructions_sub_tabs.addTab(self.summary_instructions_tab, "📋 概要生成系统指令")

        # --- 编辑改写系统指令子 Tab ---
        self.modify_instructions_tab = QWidget()
        self.modify_instructions_layout = QFormLayout(self.modify_instructions_tab)

        modify_history_layout = QHBoxLayout()
        self.modify_instruction_combo = QComboBox()
        self.btn_save_modify_instruction = QPushButton("💾 存为新模板")
        self.btn_edit_modify_name = QPushButton("✏️ 编辑名称")
        self.btn_delete_modify_instruction = QPushButton("🗑️ 删除该模板")

        modify_history_layout.addWidget(self.modify_instruction_combo, stretch=1)
        modify_history_layout.addWidget(self.btn_save_modify_instruction)
        modify_history_layout.addWidget(self.btn_edit_modify_name)
        modify_history_layout.addWidget(self.btn_delete_modify_instruction)
        self.modify_instructions_layout.addRow("历史系统指令:", modify_history_layout)

        self.modify_instruction_input = QTextEdit()
        self.modify_instruction_input.setPlaceholderText("编辑改写系统提示词，例如：你是一个专业的小说改写编辑...")
        self.modify_instructions_layout.addRow("当前指令内容:", self.modify_instruction_input)

        # 绑定编辑改写指令历史记录相关事件
        self.modify_instruction_combo.currentIndexChanged.connect(self.on_modify_instruction_changed)
        self.btn_save_modify_instruction.clicked.connect(self.save_modify_instruction_to_history)
        self.btn_edit_modify_name.clicked.connect(self.edit_instruction_name)
        self.btn_delete_modify_instruction.clicked.connect(self.delete_instruction_from_history)

        self.instructions_sub_tabs.addTab(self.modify_instructions_tab, "✏️ 编辑改写系统指令")
        
        self.instructions_tab_layout.addWidget(self.instructions_sub_tabs)
        self.tabs.addTab(self.instructions_tab, "📋 系统指令")

        # --- 图像模型 Tab ---
        self.image_tab = QWidget()
        self.image_layout = QFormLayout(self.image_tab)
        
        self.img_type_combo = QComboBox()
        self.img_type_combo.addItems(["openai", "gemini"])
        self.image_layout.addRow("接口类型 (Type):", self.img_type_combo)
        
        self.img_base_url_input = QLineEdit()
        self.img_base_url_input.setPlaceholderText("例如: https://api.openai.com/v1")
        self.image_layout.addRow("请求地址 (Base URL):", self.img_base_url_input)
        
        self.img_api_key_input = QLineEdit()
        self.img_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.image_layout.addRow("API Key:", self.img_api_key_input)
        
        self.img_model_combo = QComboBox()
        self.img_model_combo.setEditable(True)
        self.btn_fetch_img_models = QPushButton("获取可用模型")
        self.btn_fetch_img_models.clicked.connect(lambda: self.fetch_models("image"))
        
        img_model_layout = QHBoxLayout()
        img_model_layout.addWidget(self.img_model_combo, stretch=1)
        img_model_layout.addWidget(self.btn_fetch_img_models)
        self.image_layout.addRow("模型名称 (Model):", img_model_layout)
        
        self.tabs.addTab(self.image_tab, "🎨 图像生成模型")

        # --- 网络代理 Tab ---
        self.proxy_tab = QWidget()
        self.proxy_layout = QFormLayout(self.proxy_tab)
        
        self.cb_enable_proxy = QCheckBox("启用全局 HTTP/HTTPS 代理")
        self.proxy_layout.addRow(self.cb_enable_proxy)
        
        self.proxy_url_input = QLineEdit()
        self.proxy_url_input.setPlaceholderText("例如: http://127.0.0.1:7890")
        self.proxy_layout.addRow("代理地址 (URL):", self.proxy_url_input)
        
        self.tabs.addTab(self.proxy_tab, "🌐 网络代理")
        
        main_layout.addWidget(self.tabs)

        # --- 底部按钮 ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_save = QPushButton("💾 保存配置")
        self.btn_cancel = QPushButton("取消")
        
        self.btn_save.clicked.connect(self.save_config)
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        main_layout.addLayout(btn_layout)

    # ================= 历史指令的逻辑处理 =================
    def update_text_instruction_combo(self):
        """刷新文本生成指令下拉列表视图"""
        self.text_instruction_combo.blockSignals(True)
        self.text_instruction_combo.clear()
        for inst in self.text_instructions_history:
            display_name = inst.get("name", "")
            if not display_name:
                content = inst.get("content", "")
                display_name = content[:15].replace("\n", " ") + ("..." if len(content) > 15 else "")
            self.text_instruction_combo.addItem(display_name, inst)
        self.text_instruction_combo.blockSignals(False)

    def on_text_instruction_changed(self, index):
        """当文本生成指令下拉框切换时，更新文本框内容"""
        if index >= 0:
            data = self.text_instruction_combo.itemData(index)
            content = data.get("content", "") if data else ""
            self.text_instruction_input.setPlainText(content)

    def save_text_instruction_to_history(self):
        """将当前文本生成指令编辑框的内容保存为新的历史记录"""
        current_text = self.text_instruction_input.toPlainText().strip()
        if not current_text:
            QMessageBox.warning(self, "提示", "指令内容不能为空！")
            return
        
        name, ok = QInputDialog.getText(self, "保存指令模板", "请输入指令名称：")
        if not ok:
            return
        
        name = name.strip()
        if not name:
            name = current_text[:15].replace("\n", " ") + ("..." if len(current_text) > 15 else "")
        
        exists = False
        for inst in self.text_instructions_history:
            if inst.get("content") == current_text:
                exists = True
                break
        
        if not exists:
            self.text_instructions_history.append({"name": name, "content": current_text})
            self.update_text_instruction_combo()
            self.text_instruction_combo.setCurrentIndex(len(self.text_instructions_history) - 1)
            QMessageBox.information(self, "成功", f"已保存为新的指令模板：{name}")
        else:
            QMessageBox.information(self, "提示", "该指令模板已存在于记录中。")

    def update_summary_instruction_combo(self):
        """刷新概要生成指令下拉列表视图"""
        self.summary_instruction_combo.blockSignals(True)
        self.summary_instruction_combo.clear()
        for inst in self.summary_instructions_history:
            display_name = inst.get("name", "")
            if not display_name:
                content = inst.get("content", "")
                display_name = content[:15].replace("\n", " ") + ("..." if len(content) > 15 else "")
            self.summary_instruction_combo.addItem(display_name, inst)
        self.summary_instruction_combo.blockSignals(False)

    def on_summary_instruction_changed(self, index):
        """当概要生成指令下拉框切换时，更新文本框内容"""
        if index >= 0:
            data = self.summary_instruction_combo.itemData(index)
            content = data.get("content", "") if data else ""
            self.summary_instruction_input.setPlainText(content)

    def update_modify_instruction_combo(self):
        """刷新编辑改写指令下拉列表视图"""
        self.modify_instruction_combo.blockSignals(True)
        self.modify_instruction_combo.clear()
        for inst in self.modify_instructions_history:
            display_name = inst.get("name", "")
            if not display_name:
                content = inst.get("content", "")
                display_name = content[:15].replace("\n", " ") + ("..." if len(content) > 15 else "")
            self.modify_instruction_combo.addItem(display_name, inst)
        self.modify_instruction_combo.blockSignals(False)

    def on_modify_instruction_changed(self, index):
        """当编辑改写指令下拉框切换时，更新文本框内容"""
        if index >= 0:
            data = self.modify_instruction_combo.itemData(index)
            content = data.get("content", "") if data else ""
            self.modify_instruction_input.setPlainText(content)

    def save_modify_instruction_to_history(self):
        """将当前编辑改写指令编辑框的内容保存为新的历史记录"""
        current_text = self.modify_instruction_input.toPlainText().strip()
        if not current_text:
            QMessageBox.warning(self, "提示", "指令内容不能为空！")
            return

        name, ok = QInputDialog.getText(self, "保存指令模板", "请输入指令名称：")
        if not ok:
            return

        name = name.strip()
        if not name:
            name = current_text[:15].replace("\n", " ") + ("..." if len(current_text) > 15 else "")

        exists = False
        for inst in self.modify_instructions_history:
            if inst.get("content") == current_text:
                exists = True
                break

        if not exists:
            self.modify_instructions_history.append({"name": name, "content": current_text})
            self.update_modify_instruction_combo()
            self.modify_instruction_combo.setCurrentIndex(len(self.modify_instructions_history) - 1)
            QMessageBox.information(self, "成功", f"已保存为新的指令模板：{name}")
        else:
            QMessageBox.information(self, "提示", "该指令模板已存在于记录中。")

    def save_summary_instruction_to_history(self):
        """将当前概要生成指令编辑框的内容保存为新的历史记录"""
        current_text = self.summary_instruction_input.toPlainText().strip()
        if not current_text:
            QMessageBox.warning(self, "提示", "指令内容不能为空！")
            return
        
        name, ok = QInputDialog.getText(self, "保存指令模板", "请输入指令名称：")
        if not ok:
            return
        
        name = name.strip()
        if not name:
            name = current_text[:15].replace("\n", " ") + ("..." if len(current_text) > 15 else "")
        
        exists = False
        for inst in self.summary_instructions_history:
            if inst.get("content") == current_text:
                exists = True
                break
        
        if not exists:
            self.summary_instructions_history.append({"name": name, "content": current_text})
            self.update_summary_instruction_combo()
            self.summary_instruction_combo.setCurrentIndex(len(self.summary_instructions_history) - 1)
            QMessageBox.information(self, "成功", f"已保存为新的指令模板：{name}")
        else:
            QMessageBox.information(self, "提示", "该指令模板已存在于记录中。")

    def edit_instruction_name(self):
        """编辑当前选中指令的名称（根据当前活跃的子标签页判断编辑哪个）"""
        current_tab = self.instructions_sub_tabs.currentIndex()
        if current_tab == 0:
            combo = self.text_instruction_combo
            history = self.text_instructions_history
            update_func = self.update_text_instruction_combo
        elif current_tab == 1:
            combo = self.summary_instruction_combo
            history = self.summary_instructions_history
            update_func = self.update_summary_instruction_combo
        else:
            combo = self.modify_instruction_combo
            history = self.modify_instructions_history
            update_func = self.update_modify_instruction_combo
        
        index = combo.currentIndex()
        if index < 0:
            QMessageBox.warning(self, "提示", "请先选择一个指令模板！")
            return
        
        current_data = history[index]
        old_name = current_data.get("name", "")
        
        name, ok = QInputDialog.getText(self, "编辑指令名称", "请输入新的指令名称：", text=old_name)
        if not ok:
            return
        
        name = name.strip()
        if name:
            history[index]["name"] = name
            update_func()
            combo.setCurrentIndex(index)
            QMessageBox.information(self, "成功", f"指令名称已更新为：{name}")

    def delete_instruction_from_history(self):
        """删除当前选中的历史记录（根据当前活跃的子标签页判断删除哪个）"""
        current_tab = self.instructions_sub_tabs.currentIndex()
        if current_tab == 0:
            combo = self.text_instruction_combo
            history = self.text_instructions_history
            input_widget = self.text_instruction_input
            update_func = self.update_text_instruction_combo
        elif current_tab == 1:
            combo = self.summary_instruction_combo
            history = self.summary_instructions_history
            input_widget = self.summary_instruction_input
            update_func = self.update_summary_instruction_combo
        else:
            combo = self.modify_instruction_combo
            history = self.modify_instructions_history
            input_widget = self.modify_instruction_input
            update_func = self.update_modify_instruction_combo
        
        index = combo.currentIndex()
        if index >= 0:
            del history[index]
            update_func()
            if history:
                content = history[0].get("content", "")
                input_widget.setPlainText(content)
            else:
                input_widget.clear()
            QMessageBox.information(self, "成功", "已删除该指令模板！")
    # ==========================================================

    def populate_data(self):
        text_cfg = self.config.get("text_api", {})
        self.txt_type_combo.setCurrentText(text_cfg.get("type", "openai"))
        self.txt_base_url_input.setText(text_cfg.get("base_url", "https://api.openai.com/v1"))
        self.txt_api_key_input.setText(text_cfg.get("api_key", ""))
        current_text_model = text_cfg.get("model", "gpt-4o")
        self.txt_model_combo.setCurrentText(current_text_model)
        self.spin_timeout.setValue(text_cfg.get("timeout", 120))
        self.text_model_meta_catalog = {}
        raw_catalog = text_cfg.get("model_meta_catalog", {})
        if isinstance(raw_catalog, dict):
            for model_name, meta in raw_catalog.items():
                model_name = str(model_name).strip()
                if model_name:
                    self.text_model_meta_catalog[model_name] = self._normalize_model_meta(meta)
        direct_meta = self._normalize_model_meta(
            {
                "capabilities": text_cfg.get("model_capabilities", []),
                "context_size": text_cfg.get("model_context_size", 8192),
            }
        )
        self.text_model_meta_catalog[current_text_model] = direct_meta
        self.txt_model_capabilities_input.setText(", ".join(direct_meta["capabilities"]))
        self.spin_model_context_size.setValue(direct_meta["context_size"])
        compression_profile = self._normalize_compression_profile(
            text_cfg.get("world_context_compression_profile", "balanced")
        )
        idx = self.world_compression_profile_combo.findData(compression_profile)
        if idx >= 0:
            self.world_compression_profile_combo.setCurrentIndex(idx)
        
        # 加载文本生成历史指令列表
        raw_text_history = text_cfg.get("instructions_history", [])
        self.text_instructions_history = []
        for item in raw_text_history:
            if isinstance(item, str):
                self.text_instructions_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                self.text_instructions_history.append(item)
        
        text_current_instruction = text_cfg.get("instructions", "你是一个专业的AI小说家。你的输出必须纯粹是小说情节文本，严禁包含任何前言、后语、剧情解释或'已为您生成'之类的助手客套话。")
        
        # 确保当前文本生成指令在历史列表中
        if text_current_instruction:
            exists = False
            for inst in self.text_instructions_history:
                if inst.get("content") == text_current_instruction:
                    exists = True
                    break
            if not exists:
                self.text_instructions_history.insert(0, {"name": "", "content": text_current_instruction})
            
        self.update_text_instruction_combo()
        
        # 设置当前选中的文本生成指令文本
        self.text_instruction_input.setPlainText(text_current_instruction)
        # 尝试在下拉框中定位到当前指令
        idx = -1
        for i, inst in enumerate(self.text_instructions_history):
            if inst.get("content") == text_current_instruction:
                idx = i
                break
        if idx >= 0:
            self.text_instruction_combo.setCurrentIndex(idx)
        
        # 加载概要生成历史指令列表
        raw_summary_history = text_cfg.get("summary_instructions_history", [])
        self.summary_instructions_history = []
        for item in raw_summary_history:
            if isinstance(item, str):
                self.summary_instructions_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                self.summary_instructions_history.append(item)
        
        summary_current_instruction = text_cfg.get("summary_instructions", "你是一个专业的小说编辑，擅长为小说节点生成精炼、准确的概要。")
        
        # 确保当前概要生成指令在历史列表中
        if summary_current_instruction:
            exists = False
            for inst in self.summary_instructions_history:
                if inst.get("content") == summary_current_instruction:
                    exists = True
                    break
            if not exists:
                self.summary_instructions_history.insert(0, {"name": "", "content": summary_current_instruction})
            
        self.update_summary_instruction_combo()
        
        # 设置当前选中的概要生成指令文本
        self.summary_instruction_input.setPlainText(summary_current_instruction)
        # 尝试在下拉框中定位到当前指令
        idx = -1
        for i, inst in enumerate(self.summary_instructions_history):
            if inst.get("content") == summary_current_instruction:
                idx = i
                break
        if idx >= 0:
            self.summary_instruction_combo.setCurrentIndex(idx)

        # 加载编辑改写历史指令列表
        raw_modify_history = text_cfg.get("modify_instructions_history", [])
        self.modify_instructions_history = []
        for item in raw_modify_history:
            if isinstance(item, str):
                self.modify_instructions_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                self.modify_instructions_history.append(item)

        modify_current_instruction = text_cfg.get(
            "modify_instructions",
            "你是一个专业的小说改写编辑。你必须严格遵循用户的修改要求，对原文进行高质量改写。只输出修改后的完整正文，不要附加解释。",
        )

        # 确保当前编辑改写指令在历史列表中
        if modify_current_instruction:
            exists = False
            for inst in self.modify_instructions_history:
                if inst.get("content") == modify_current_instruction:
                    exists = True
                    break
            if not exists:
                self.modify_instructions_history.insert(0, {"name": "", "content": modify_current_instruction})

        self.update_modify_instruction_combo()

        # 设置当前选中的编辑改写指令文本
        self.modify_instruction_input.setPlainText(modify_current_instruction)
        idx = -1
        for i, inst in enumerate(self.modify_instructions_history):
            if inst.get("content") == modify_current_instruction:
                idx = i
                break
        if idx >= 0:
            self.modify_instruction_combo.setCurrentIndex(idx)

        img_cfg = self.config.get("image_api", {})
        self.img_type_combo.setCurrentText(img_cfg.get("type", "openai"))
        self.img_base_url_input.setText(img_cfg.get("base_url", "https://api.openai.com/v1"))
        self.img_api_key_input.setText(img_cfg.get("api_key", ""))
        self.img_model_combo.setCurrentText(img_cfg.get("model", "dall-e-3"))

        proxy_cfg = self.config.get("proxy", {})
        self.cb_enable_proxy.setChecked(proxy_cfg.get("enabled", False))
        self.proxy_url_input.setText(proxy_cfg.get("url", "http://127.0.0.1:7890"))



    def _normalize_capabilities(self, raw_value):
        if isinstance(raw_value, str):
            items = [x.strip() for x in raw_value.replace(";", ",").split(",")]
        elif isinstance(raw_value, list):
            items = [str(x).strip() for x in raw_value]
        else:
            items = []
        result = []
        seen = set()
        for item in items:
            if not item:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _normalize_model_meta(self, raw_meta):
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        capabilities = self._normalize_capabilities(meta.get("capabilities", []))
        context_size = meta.get("context_size", 8192)
        try:
            context_size = int(context_size)
        except Exception:
            context_size = 8192
        if context_size <= 0:
            context_size = 8192
        return {"capabilities": capabilities, "context_size": context_size}

    def _normalize_compression_profile(self, raw_value):
        value = str(raw_value or "balanced").strip().lower()
        if value in ("conservative", "balanced", "aggressive"):
            return value
        return "balanced"

    def _extract_model_meta(self, model_item):
        if not isinstance(model_item, dict):
            return {"capabilities": [], "context_size": 8192}
        capabilities = []
        for key in ("capabilities", "ability", "abilities", "features", "tags"):
            if key in model_item:
                capabilities = self._normalize_capabilities(model_item.get(key))
                if capabilities:
                    break
        context_size = None
        for key in (
            "context_size",
            "context_length",
            "max_context_tokens",
            "max_input_tokens",
            "max_tokens",
        ):
            if key in model_item and model_item.get(key) is not None:
                try:
                    context_size = int(model_item.get(key))
                    if context_size > 0:
                        break
                except Exception:
                    context_size = None
        return self._normalize_model_meta(
            {"capabilities": capabilities, "context_size": context_size or 8192}
        )

    def on_text_model_changed(self, model_name: str):
        model_name = (model_name or "").strip()
        if not model_name:
            return
        if model_name not in self.text_model_meta_catalog:
            return
        meta = self.text_model_meta_catalog.get(model_name, {})
        normalized = self._normalize_model_meta(meta)
        self.txt_model_capabilities_input.setText(", ".join(normalized["capabilities"]))
        self.spin_model_context_size.setValue(normalized["context_size"])

    def fetch_models(self, api_type: str):
        """拉取服务商支持的具体模型类型"""
        if api_type == "text":
            base_url = self.txt_base_url_input.text().strip()
            api_key = self.txt_api_key_input.text().strip()
            combo_box = self.txt_model_combo
            provider_type = self.txt_type_combo.currentText()
        else:
            base_url = self.img_base_url_input.text().strip()
            api_key = self.img_api_key_input.text().strip()
            combo_box = self.img_model_combo
            provider_type = self.img_type_combo.currentText()

        if not api_key:
            QMessageBox.warning(self, "提示", "请先输入 API Key 再尝试获取模型列表。")
            return

        # 针对 OpenAI 兼容接口的通用获取逻辑
        if provider_type == "openai":
            # 确保 url 以 /models 结尾
            url = base_url if base_url.endswith("/models") else f"{base_url.rstrip('/')}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            
            # 动态判断是否需要使用代理
            proxies = None
            if self.cb_enable_proxy.isChecked():
                proxy_url = self.proxy_url_input.text().strip()
                if proxy_url:
                    proxies = {"http": proxy_url, "https": proxy_url}
            
            try:
                # 传入 proxies 参数
                response = requests.get(url, headers=headers, timeout=5, proxies=proxies)
                response.raise_for_status()
                
                data = response.json()
                model_items = data.get("data", [])
                models = [item.get("id") for item in model_items if isinstance(item, dict) and "id" in item]

                if models:
                    combo_box.clear()
                    if api_type == "text":
                        text_items = [item for item in model_items if isinstance(item, dict)]
                        for item in sorted(text_items, key=lambda x: str(x.get("id", ""))):
                            model_id = str(item.get("id", "")).strip()
                            if not model_id:
                                continue
                            meta = self._extract_model_meta(item)
                            self.text_model_meta_catalog[model_id] = meta
                            combo_box.addItem(model_id, meta)
                        self.on_text_model_changed(self.txt_model_combo.currentText())
                    else:
                        combo_box.addItems(sorted(models))
                    QMessageBox.information(self, "成功", f"成功获取到 {len(models)} 个模型！")
                else:
                    QMessageBox.warning(self, "提示", "请求成功，但返回的模型列表为空。")
                    
            except Exception as e:
                QMessageBox.critical(self, "获取失败", f"无法连接到 API 获取模型列表:\n{e}\n\n建议直接手动输入模型名称。")
        else:
            QMessageBox.information(self, "提示", "当前仅支持自动拉取 OpenAI 兼容格式 (如 DeepSeek, Moonshot 等) 的模型列表。对于 Gemini，请手动输入模型名称（如 gemini-1.5-pro）。")

    def save_config(self):
        # 在保存配置时，如果当前文本框里的内容不在历史记录里，自动帮用户存一份
        current_text_instruction = self.text_instruction_input.toPlainText().strip()
        if current_text_instruction:
            exists = False
            for inst in self.text_instructions_history:
                if inst.get("content") == current_text_instruction:
                    exists = True
                    break
            if not exists:
                self.text_instructions_history.append({"name": "", "content": current_text_instruction})
        
        # 同样处理概要生成指令
        current_summary_instruction = self.summary_instruction_input.toPlainText().strip()
        if current_summary_instruction:
            exists = False
            for inst in self.summary_instructions_history:
                if inst.get("content") == current_summary_instruction:
                    exists = True
                    break
            if not exists:
                self.summary_instructions_history.append({"name": "", "content": current_summary_instruction})

        # 同样处理编辑改写指令
        current_modify_instruction = self.modify_instruction_input.toPlainText().strip()
        if current_modify_instruction:
            exists = False
            for inst in self.modify_instructions_history:
                if inst.get("content") == current_modify_instruction:
                    exists = True
                    break
            if not exists:
                self.modify_instructions_history.append({"name": "", "content": current_modify_instruction})

        model_name = self.txt_model_combo.currentText().strip()
        model_capabilities = self._normalize_capabilities(
            self.txt_model_capabilities_input.text().strip()
        )
        model_context_size = int(self.spin_model_context_size.value())
        compression_profile = self._normalize_compression_profile(
            self.world_compression_profile_combo.currentData()
        )
        if model_name:
            self.text_model_meta_catalog[model_name] = {
                "capabilities": model_capabilities,
                "context_size": model_context_size,
            }

        new_config = {
            "proxy": {
                "enabled": self.cb_enable_proxy.isChecked(),
                "url": self.proxy_url_input.text().strip()
            },
            "text_api": {
                "type": self.txt_type_combo.currentText(),
                "base_url": self.txt_base_url_input.text().strip(),
                "api_key": self.txt_api_key_input.text().strip(),
                "model": self.txt_model_combo.currentText().strip(),
                "timeout": self.spin_timeout.value(),
                "model_capabilities": model_capabilities,
                "model_context_size": model_context_size,
                "world_context_compression_profile": compression_profile,
                "model_meta_catalog": self.text_model_meta_catalog,
                "instructions": current_text_instruction,
                "instructions_history": self.text_instructions_history,
                "summary_instructions": current_summary_instruction,
                "summary_instructions_history": self.summary_instructions_history,
                "modify_instructions": current_modify_instruction,
                "modify_instructions_history": self.modify_instructions_history,
            },
            "image_api": {
                "type": self.img_type_combo.currentText(),
                "base_url": self.img_base_url_input.text().strip(),
                "api_key": self.img_api_key_input.text().strip(),
                "model": self.img_model_combo.currentText().strip()
            }
        }
        
        # 确保 conf 目录存在
        os.makedirs(self.config_dir, exist_ok=True)
        
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(new_config, f, ensure_ascii=False, indent=4)
            QMessageBox.information(self, "保存成功", "系统配置已保存。\n配置将在关闭此窗口后立即生效。")
            self.accept() # 关闭对话框并返回 accepted 状态
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入文件时发生错误:\n{e}")
