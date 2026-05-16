import os
import json
import copy
import requests
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QComboBox, QTextEdit, 
                             QMessageBox, QTabWidget, QWidget, QFormLayout, QSpinBox, QCheckBox, QInputDialog, QDoubleSpinBox)
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
        self.text_api_profiles = {"normal": {}, "nsfw": {}}
        self._current_text_profile_key = "normal"
        self._is_switching_text_profile = False
        self.recent_api_configs = []
        
        self.init_ui()
        self.populate_data()

    def load_config(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                QMessageBox.warning(self, "警告", f"读取配置文件失败，将使用默认空配置。\n{e}")
        return {
            "text_api": {},
            "image_api": {},
            "task_completion_notification_enabled": True,
        }

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # 使用选项卡分离文本模型和图像模型的配置
        self.tabs = QTabWidget()
        
        # --- 文本模型 Tab ---
        self.text_tab = QWidget()
        self.text_layout = QFormLayout(self.text_tab)

        self.text_profile_combo = QComboBox()
        self.text_profile_combo.addItem("普通小说配置", "normal")
        self.text_profile_combo.addItem("NSFW小说配置", "nsfw")
        self.text_profile_combo.currentIndexChanged.connect(self.on_text_profile_changed)
        self.text_layout.addRow("配置目标:", self.text_profile_combo)
        
        self.txt_type_combo = QComboBox()
        self.txt_type_combo.addItems(["openai", "gemini"])
        self.text_layout.addRow("接口类型 (Type):", self.txt_type_combo)
        
        self.txt_base_url_input = QLineEdit()
        self.txt_base_url_input.setPlaceholderText("例如: https://api.openai.com/v1")
        self.text_layout.addRow("请求地址 (Base URL):", self.txt_base_url_input)
        
        self.txt_api_key_input = QLineEdit()
        self.txt_api_key_input.setEchoMode(QLineEdit.EchoMode.Password) # 默认掩码
        txt_api_key_layout = QHBoxLayout()
        txt_api_key_layout.addWidget(self.txt_api_key_input, stretch=1)
        self.btn_toggle_txt_api_key = QPushButton("显示 Key")
        self.btn_toggle_txt_api_key.clicked.connect(
            lambda: self.toggle_api_key_visibility(
                self.txt_api_key_input, self.btn_toggle_txt_api_key
            )
        )
        txt_api_key_layout.addWidget(self.btn_toggle_txt_api_key)
        self.text_layout.addRow("API Key:", txt_api_key_layout)
        
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

        self.cb_auto_continue_on_incomplete = QCheckBox("返回疑似截断时自动续请求")
        self.text_layout.addRow("自动续请求:", self.cb_auto_continue_on_incomplete)

        self.spin_auto_continue_max_rounds = QSpinBox()
        self.spin_auto_continue_max_rounds.setRange(0, 10)
        self.spin_auto_continue_max_rounds.setSuffix(" 轮")
        self.text_layout.addRow("最大续请求轮次:", self.spin_auto_continue_max_rounds)

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

        self.spin_children_compress_trigger_ratio = QDoubleSpinBox()
        self.spin_children_compress_trigger_ratio.setRange(5.0, 80.0)
        self.spin_children_compress_trigger_ratio.setDecimals(1)
        self.spin_children_compress_trigger_ratio.setSingleStep(1.0)
        self.spin_children_compress_trigger_ratio.setSuffix(" %")
        self.spin_children_compress_trigger_ratio.setToolTip(
            "当“子节点概要总量”超过上下文窗口的该比例时，触发分组压缩。"
        )
        self.text_layout.addRow("子节点压缩触发阈值:", self.spin_children_compress_trigger_ratio)

        self.spin_children_group_budget_ratio = QDoubleSpinBox()
        self.spin_children_group_budget_ratio.setRange(4.0, 60.0)
        self.spin_children_group_budget_ratio.setDecimals(1)
        self.spin_children_group_budget_ratio.setSingleStep(1.0)
        self.spin_children_group_budget_ratio.setSuffix(" %")
        self.spin_children_group_budget_ratio.setToolTip(
            "分组压缩时，每组可占上下文窗口的比例。"
        )
        self.text_layout.addRow("子节点分组预算比例:", self.spin_children_group_budget_ratio)

        
        self.tabs.addTab(self.text_tab, "📝 文本生成模型")

        # --- 系统指令 Tab ---
        self.instructions_tab = QWidget()
        self.instructions_tab_layout = QVBoxLayout(self.instructions_tab)

        instructions_profile_layout = QHBoxLayout()
        instructions_profile_layout.addWidget(QLabel("配置目标:"))
        self.instructions_profile_combo = QComboBox()
        self.instructions_profile_combo.addItem("普通小说配置", "normal")
        self.instructions_profile_combo.addItem("NSFW小说配置", "nsfw")
        self.instructions_profile_combo.currentIndexChanged.connect(
            self.on_text_profile_changed
        )
        instructions_profile_layout.addWidget(self.instructions_profile_combo, stretch=1)
        self.instructions_tab_layout.addLayout(instructions_profile_layout)
        
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
        self.img_api_key_input.setEchoMode(QLineEdit.EchoMode.Password) # 默认掩码
        img_api_key_layout = QHBoxLayout()
        img_api_key_layout.addWidget(self.img_api_key_input, stretch=1)
        self.btn_toggle_img_api_key = QPushButton("显示 Key")
        self.btn_toggle_img_api_key.clicked.connect(
            lambda: self.toggle_api_key_visibility(
                self.img_api_key_input, self.btn_toggle_img_api_key
            )
        )
        img_api_key_layout.addWidget(self.btn_toggle_img_api_key)
        self.image_layout.addRow("API Key:", img_api_key_layout)
        
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

        # --- 通知 Tab ---
        self.notify_tab = QWidget()
        self.notify_layout = QFormLayout(self.notify_tab)
        self.cb_task_completion_notification = QCheckBox(
            "任务执行结束时弹出系统通知（Windows）"
        )
        self.cb_task_completion_notification.setChecked(True)
        self.notify_layout.addRow(self.cb_task_completion_notification)
        self.tabs.addTab(self.notify_tab, "🔔 通知")
        
        main_layout.addWidget(self.tabs)

        # --- 底部按钮 ---
        btn_layout = QHBoxLayout()
        self.btn_load_recent_api = QPushButton("🕘 最近使用")
        self.btn_load_recent_api.clicked.connect(self.load_recent_api_config)
        btn_layout.addWidget(self.btn_load_recent_api)
        btn_layout.addStretch()
        self.btn_save = QPushButton("💾 保存配置")
        self.btn_cancel = QPushButton("取消")
        
        self.btn_save.clicked.connect(self.save_config)
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        main_layout.addLayout(btn_layout)

    def toggle_api_key_visibility(self, input_widget: QLineEdit, toggle_button: QPushButton):
        """切换 API Key 的显示/隐藏状态。默认隐藏，仅在用户点击后显示。"""
        is_password = input_widget.echoMode() == QLineEdit.EchoMode.Password
        if is_password:
            input_widget.setEchoMode(QLineEdit.EchoMode.Normal)
            toggle_button.setText("隐藏 Key")
        else:
            input_widget.setEchoMode(QLineEdit.EchoMode.Password)
            toggle_button.setText("显示 Key")

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

    def _default_text_profile(self):
        return {
            "type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o",
            "timeout": 120,
            "model_capabilities": [],
            "model_context_size": 8192,
            "world_context_compression_profile": "balanced",
            "children_summary_compress_trigger_ratio": 0.27,
            "children_summary_group_budget_ratio": 0.24,
            "auto_continue_on_incomplete": True,
            "auto_continue_max_rounds": 3,
            "model_meta_catalog": {},
            "instructions": "你是一个专业的AI小说家。你的输出必须纯粹是小说情节文本，严禁包含任何前言、后语、剧情解释或'已为您生成'之类的助手客套话。",
            "instructions_history": [],
            "summary_instructions": "你是一个专业的小说编辑，擅长为小说节点生成精炼、准确的概要。",
            "summary_instructions_history": [],
            "modify_instructions": "你是一个专业的小说改写编辑。你必须严格遵循用户的修改要求，对原文进行高质量改写。只输出修改后的完整正文，不要附加解释。",
            "modify_instructions_history": [],
        }

    def _normalize_text_profile(self, raw_profile):
        profile = self._default_text_profile()
        if isinstance(raw_profile, dict):
            profile.update(raw_profile)
        profile["model_capabilities"] = self._normalize_capabilities(
            profile.get("model_capabilities", [])
        )
        profile["model_meta_catalog"] = (
            profile.get("model_meta_catalog", {})
            if isinstance(profile.get("model_meta_catalog", {}), dict)
            else {}
        )
        profile["world_context_compression_profile"] = self._normalize_compression_profile(
            profile.get("world_context_compression_profile", "balanced")
        )
        profile["children_summary_compress_trigger_ratio"] = self._normalize_ratio(
            profile.get("children_summary_compress_trigger_ratio", 0.27),
            default_value=0.27,
            min_value=0.05,
            max_value=0.8,
        )
        profile["children_summary_group_budget_ratio"] = self._normalize_ratio(
            profile.get("children_summary_group_budget_ratio", 0.24),
            default_value=0.24,
            min_value=0.04,
            max_value=0.6,
        )
        profile["auto_continue_on_incomplete"] = bool(
            profile.get("auto_continue_on_incomplete", True)
        )
        try:
            rounds = int(profile.get("auto_continue_max_rounds", 3))
        except Exception:
            rounds = 3
        profile["auto_continue_max_rounds"] = max(0, min(10, rounds))
        return profile

    def _normalize_ratio(self, raw_value, default_value, min_value, max_value):
        """归一化比例：支持 0-1 小数和 0-100 百分数。"""
        try:
            value = float(raw_value)
        except Exception:
            value = default_value
        if value > 1:
            value = value / 100.0
        if value <= 0:
            value = default_value
        return max(min_value, min(max_value, value))

    def _extract_text_api_profiles(self):
        raw_profiles = self.config.get("text_api_profiles", {})
        if isinstance(raw_profiles, dict):
            normal_raw = raw_profiles.get("normal")
            nsfw_raw = raw_profiles.get("nsfw")
        else:
            normal_raw = None
            nsfw_raw = None
        legacy_text = self.config.get("text_api", {})
        if not isinstance(legacy_text, dict):
            legacy_text = {}
        if normal_raw is None:
            normal_raw = legacy_text
        if nsfw_raw is None:
            nsfw_raw = copy.deepcopy(normal_raw if isinstance(normal_raw, dict) else legacy_text)
        return {
            "normal": self._normalize_text_profile(normal_raw),
            "nsfw": self._normalize_text_profile(nsfw_raw),
        }

    def _collect_current_text_profile_from_ui(self):
        current_text_instruction = self.text_instruction_input.toPlainText().strip()
        if current_text_instruction and not any(
            inst.get("content") == current_text_instruction
            for inst in self.text_instructions_history
        ):
            self.text_instructions_history.append({"name": "", "content": current_text_instruction})

        current_summary_instruction = self.summary_instruction_input.toPlainText().strip()
        if current_summary_instruction and not any(
            inst.get("content") == current_summary_instruction
            for inst in self.summary_instructions_history
        ):
            self.summary_instructions_history.append({"name": "", "content": current_summary_instruction})

        current_modify_instruction = self.modify_instruction_input.toPlainText().strip()
        if current_modify_instruction and not any(
            inst.get("content") == current_modify_instruction
            for inst in self.modify_instructions_history
        ):
            self.modify_instructions_history.append({"name": "", "content": current_modify_instruction})

        model_name = self.txt_model_combo.currentText().strip()
        model_capabilities = self._normalize_capabilities(
            self.txt_model_capabilities_input.text().strip()
        )
        model_context_size = int(self.spin_model_context_size.value())
        compression_profile = self._normalize_compression_profile(
            self.world_compression_profile_combo.currentData()
        )
        children_trigger_ratio = self._normalize_ratio(
            self.spin_children_compress_trigger_ratio.value() / 100.0,
            default_value=0.27,
            min_value=0.05,
            max_value=0.8,
        )
        children_group_budget_ratio = self._normalize_ratio(
            self.spin_children_group_budget_ratio.value() / 100.0,
            default_value=0.24,
            min_value=0.04,
            max_value=0.6,
        )
        if model_name:
            self.text_model_meta_catalog[model_name] = {
                "capabilities": model_capabilities,
                "context_size": model_context_size,
            }

        return {
            "type": self.txt_type_combo.currentText(),
            "base_url": self.txt_base_url_input.text().strip(),
            "api_key": self.txt_api_key_input.text().strip(),
            "model": model_name,
            "timeout": self.spin_timeout.value(),
            "model_capabilities": model_capabilities,
            "model_context_size": model_context_size,
            "world_context_compression_profile": compression_profile,
            "children_summary_compress_trigger_ratio": children_trigger_ratio,
            "children_summary_group_budget_ratio": children_group_budget_ratio,
            "auto_continue_on_incomplete": self.cb_auto_continue_on_incomplete.isChecked(),
            "auto_continue_max_rounds": int(self.spin_auto_continue_max_rounds.value()),
            "model_meta_catalog": self.text_model_meta_catalog,
            "instructions": current_text_instruction,
            "instructions_history": self.text_instructions_history,
            "summary_instructions": current_summary_instruction,
            "summary_instructions_history": self.summary_instructions_history,
            "modify_instructions": current_modify_instruction,
            "modify_instructions_history": self.modify_instructions_history,
        }

    def _apply_text_profile_to_ui(self, text_cfg):
        text_cfg = self._normalize_text_profile(text_cfg)
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
        self.spin_children_compress_trigger_ratio.setValue(
            float(text_cfg.get("children_summary_compress_trigger_ratio", 0.27)) * 100.0
        )
        self.spin_children_group_budget_ratio.setValue(
            float(text_cfg.get("children_summary_group_budget_ratio", 0.24)) * 100.0
        )
        self.cb_auto_continue_on_incomplete.setChecked(
            bool(text_cfg.get("auto_continue_on_incomplete", True))
        )
        self.spin_auto_continue_max_rounds.setValue(
            int(text_cfg.get("auto_continue_max_rounds", 3))
        )

        raw_text_history = text_cfg.get("instructions_history", [])
        self.text_instructions_history = []
        for item in raw_text_history:
            if isinstance(item, str):
                self.text_instructions_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                self.text_instructions_history.append(item)
        text_current_instruction = text_cfg.get("instructions", self._default_text_profile()["instructions"])
        if text_current_instruction and not any(
            inst.get("content") == text_current_instruction
            for inst in self.text_instructions_history
        ):
            self.text_instructions_history.insert(0, {"name": "", "content": text_current_instruction})
        self.update_text_instruction_combo()
        self.text_instruction_input.setPlainText(text_current_instruction)
        idx = next(
            (i for i, inst in enumerate(self.text_instructions_history) if inst.get("content") == text_current_instruction),
            -1,
        )
        if idx >= 0:
            self.text_instruction_combo.setCurrentIndex(idx)

        raw_summary_history = text_cfg.get("summary_instructions_history", [])
        self.summary_instructions_history = []
        for item in raw_summary_history:
            if isinstance(item, str):
                self.summary_instructions_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                self.summary_instructions_history.append(item)
        summary_current_instruction = text_cfg.get(
            "summary_instructions", self._default_text_profile()["summary_instructions"]
        )
        if summary_current_instruction and not any(
            inst.get("content") == summary_current_instruction
            for inst in self.summary_instructions_history
        ):
            self.summary_instructions_history.insert(0, {"name": "", "content": summary_current_instruction})
        self.update_summary_instruction_combo()
        self.summary_instruction_input.setPlainText(summary_current_instruction)
        idx = next(
            (i for i, inst in enumerate(self.summary_instructions_history) if inst.get("content") == summary_current_instruction),
            -1,
        )
        if idx >= 0:
            self.summary_instruction_combo.setCurrentIndex(idx)

        raw_modify_history = text_cfg.get("modify_instructions_history", [])
        self.modify_instructions_history = []
        for item in raw_modify_history:
            if isinstance(item, str):
                self.modify_instructions_history.append({"name": "", "content": item})
            elif isinstance(item, dict):
                self.modify_instructions_history.append(item)
        modify_current_instruction = text_cfg.get(
            "modify_instructions", self._default_text_profile()["modify_instructions"]
        )
        if modify_current_instruction and not any(
            inst.get("content") == modify_current_instruction
            for inst in self.modify_instructions_history
        ):
            self.modify_instructions_history.insert(0, {"name": "", "content": modify_current_instruction})
        self.update_modify_instruction_combo()
        self.modify_instruction_input.setPlainText(modify_current_instruction)
        idx = next(
            (i for i, inst in enumerate(self.modify_instructions_history) if inst.get("content") == modify_current_instruction),
            -1,
        )
        if idx >= 0:
            self.modify_instruction_combo.setCurrentIndex(idx)

    def on_text_profile_changed(self, index):
        if self._is_switching_text_profile:
            return
        sender_combo = self.sender()
        if isinstance(sender_combo, QComboBox):
            new_key = sender_combo.itemData(index) or "normal"
        else:
            new_key = self.text_profile_combo.itemData(index) or "normal"
        if new_key == self._current_text_profile_key:
            return
        self.text_api_profiles[self._current_text_profile_key] = self._collect_current_text_profile_from_ui()
        self._current_text_profile_key = str(new_key)
        self._is_switching_text_profile = True
        try:
            sync_idx = self.text_profile_combo.findData(self._current_text_profile_key)
            if sync_idx >= 0:
                self.text_profile_combo.blockSignals(True)
                self.text_profile_combo.setCurrentIndex(sync_idx)
                self.text_profile_combo.blockSignals(False)
                self.instructions_profile_combo.blockSignals(True)
                self.instructions_profile_combo.setCurrentIndex(sync_idx)
                self.instructions_profile_combo.blockSignals(False)
            self._apply_text_profile_to_ui(self.text_api_profiles.get(self._current_text_profile_key, {}))
            self._refresh_generation_instruction_labels()
        finally:
            self._is_switching_text_profile = False

    def _refresh_generation_instruction_labels(self):
        mode_name = "NSFW文本生成" if self._current_text_profile_key == "nsfw" else "普通文本生成"
        self.instructions_sub_tabs.setTabText(0, f"📝 {mode_name}系统指令")
        self.text_instruction_input.setPlaceholderText(
            f"{mode_name}系统提示词，例如：你是一个专业的小说家..."
        )

    def populate_data(self):
        self.text_api_profiles = self._extract_text_api_profiles()
        initial_key = "normal"
        if self.parent() is not None and bool(getattr(self.parent(), "_workspace_is_nsfw", False)):
            initial_key = "nsfw"
        idx = self.text_profile_combo.findData(initial_key)
        if idx < 0:
            idx = 0
            initial_key = "normal"
        self._current_text_profile_key = initial_key
        self.text_profile_combo.blockSignals(True)
        self.text_profile_combo.setCurrentIndex(idx)
        self.text_profile_combo.blockSignals(False)
        self.instructions_profile_combo.blockSignals(True)
        self.instructions_profile_combo.setCurrentIndex(idx)
        self.instructions_profile_combo.blockSignals(False)
        self._apply_text_profile_to_ui(self.text_api_profiles.get(self._current_text_profile_key, {}))
        self._refresh_generation_instruction_labels()

        img_cfg = self.config.get("image_api", {})
        self.img_type_combo.setCurrentText(img_cfg.get("type", "openai"))
        self.img_base_url_input.setText(img_cfg.get("base_url", "https://api.openai.com/v1"))
        self.img_api_key_input.setText(img_cfg.get("api_key", ""))
        self.img_model_combo.setCurrentText(img_cfg.get("model", "dall-e-3"))

        proxy_cfg = self.config.get("proxy", {})
        self.cb_enable_proxy.setChecked(proxy_cfg.get("enabled", False))
        self.proxy_url_input.setText(proxy_cfg.get("url", "http://127.0.0.1:7890"))
        self.cb_task_completion_notification.setChecked(
            bool(self.config.get("task_completion_notification_enabled", True))
        )
        self.recent_api_configs = self._deduplicate_recent_api_configs(
            self.config.get("recent_api_configs", [])
        )[:20]



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

    def _normalize_image_api(self, raw_image_api):
        image_api = raw_image_api if isinstance(raw_image_api, dict) else {}
        return {
            "type": str(image_api.get("type", "openai")).strip() or "openai",
            "base_url": str(
                image_api.get("base_url", "https://api.openai.com/v1")
            ).strip()
            or "https://api.openai.com/v1",
            "api_key": str(image_api.get("api_key", "")).strip(),
            "model": str(image_api.get("model", "dall-e-3")).strip() or "dall-e-3",
        }

    def _normalize_proxy_config(self, raw_proxy):
        proxy = raw_proxy if isinstance(raw_proxy, dict) else {}
        return {
            "enabled": bool(proxy.get("enabled", False)),
            "url": str(proxy.get("url", "http://127.0.0.1:7890")).strip()
            or "http://127.0.0.1:7890",
        }

    def _normalize_api_snapshot(self, raw_snapshot):
        snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
        raw_text_profiles = snapshot.get("text_api_profiles", {})
        if isinstance(raw_text_profiles, dict):
            normal_raw = raw_text_profiles.get("normal", {})
            nsfw_raw = raw_text_profiles.get("nsfw", {})
        else:
            normal_raw = {}
            nsfw_raw = {}
        text_api_profiles = {
            "normal": self._normalize_text_profile(normal_raw),
            "nsfw": self._normalize_text_profile(nsfw_raw),
        }
        active_profile = str(snapshot.get("text_api_active_profile", "normal")).strip().lower()
        if active_profile not in ("normal", "nsfw"):
            active_profile = "normal"
        return {
            "text_api_profiles": text_api_profiles,
            "text_api_active_profile": active_profile,
            "image_api": self._normalize_image_api(snapshot.get("image_api", {})),
            "proxy": self._normalize_proxy_config(snapshot.get("proxy", {})),
        }

    def _snapshot_to_unique_key(self, snapshot):
        normalized = self._normalize_api_snapshot(snapshot)
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True)

    def _deduplicate_recent_api_configs(self, recent_list):
        normalized_list = []
        seen = set()
        for item in recent_list if isinstance(recent_list, list) else []:
            normalized = self._normalize_api_snapshot(item)
            key = self._snapshot_to_unique_key(normalized)
            if key in seen:
                continue
            seen.add(key)
            normalized_list.append(normalized)
        return normalized_list

    def _build_current_api_snapshot(self):
        current_profiles = {
            "normal": self._normalize_text_profile(self.text_api_profiles.get("normal", {})),
            "nsfw": self._normalize_text_profile(self.text_api_profiles.get("nsfw", {})),
        }
        active_profile = self._current_text_profile_key if self._current_text_profile_key in ("normal", "nsfw") else "normal"
        return {
            "text_api_profiles": current_profiles,
            "text_api_active_profile": active_profile,
            "image_api": self._normalize_image_api(
                {
                    "type": self.img_type_combo.currentText(),
                    "base_url": self.img_base_url_input.text().strip(),
                    "api_key": self.img_api_key_input.text().strip(),
                    "model": self.img_model_combo.currentText().strip(),
                }
            ),
            "proxy": self._normalize_proxy_config(
                {
                    "enabled": self.cb_enable_proxy.isChecked(),
                    "url": self.proxy_url_input.text().strip(),
                }
            ),
        }

    def _build_recent_api_item_label(self, item, index):
        snapshot = self._normalize_api_snapshot(item)
        active_profile = snapshot.get("text_api_active_profile", "normal")
        text_profile = snapshot.get("text_api_profiles", {}).get(active_profile, {})
        text_model = str(text_profile.get("model", "")).strip() or "-"
        text_type = str(text_profile.get("type", "")).strip() or "-"
        image_cfg = snapshot.get("image_api", {})
        image_model = str(image_cfg.get("model", "")).strip() or "-"
        return f"{index + 1}. [{active_profile}] 文本:{text_type}/{text_model} | 图像:{image_model}"

    def _apply_recent_api_snapshot_to_ui(self, snapshot):
        normalized = self._normalize_api_snapshot(snapshot)
        self.text_api_profiles = copy.deepcopy(normalized["text_api_profiles"])
        self._current_text_profile_key = normalized["text_api_active_profile"]

        idx = self.text_profile_combo.findData(self._current_text_profile_key)
        if idx < 0:
            idx = 0
            self._current_text_profile_key = "normal"

        self._is_switching_text_profile = True
        try:
            self.text_profile_combo.blockSignals(True)
            self.text_profile_combo.setCurrentIndex(idx)
            self.text_profile_combo.blockSignals(False)
            self.instructions_profile_combo.blockSignals(True)
            self.instructions_profile_combo.setCurrentIndex(idx)
            self.instructions_profile_combo.blockSignals(False)
        finally:
            self._is_switching_text_profile = False

        self._apply_text_profile_to_ui(
            self.text_api_profiles.get(self._current_text_profile_key, {})
        )
        self._refresh_generation_instruction_labels()

        image_cfg = normalized["image_api"]
        self.img_type_combo.setCurrentText(image_cfg.get("type", "openai"))
        self.img_base_url_input.setText(image_cfg.get("base_url", "https://api.openai.com/v1"))
        self.img_api_key_input.setText(image_cfg.get("api_key", ""))
        self.img_model_combo.setCurrentText(image_cfg.get("model", "dall-e-3"))

        proxy_cfg = normalized["proxy"]
        self.cb_enable_proxy.setChecked(proxy_cfg.get("enabled", False))
        self.proxy_url_input.setText(proxy_cfg.get("url", "http://127.0.0.1:7890"))

    def load_recent_api_config(self):
        if not self.recent_api_configs:
            QMessageBox.information(self, "提示", "暂无最近使用的 API 配置。")
            return
        labels = [
            self._build_recent_api_item_label(item, idx)
            for idx, item in enumerate(self.recent_api_configs)
        ]
        selected_text, ok = QInputDialog.getItem(
            self,
            "最近使用",
            "请选择要加载的 API 配置：",
            labels,
            0,
            False,
        )
        if not ok or not selected_text:
            return
        selected_index = labels.index(selected_text)
        self._apply_recent_api_snapshot_to_ui(self.recent_api_configs[selected_index])
        QMessageBox.information(self, "成功", "已加载最近使用的 API 配置。")

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
        self.text_api_profiles[self._current_text_profile_key] = (
            self._collect_current_text_profile_from_ui()
        )
        normal_profile = self._normalize_text_profile(
            self.text_api_profiles.get("normal", {})
        )
        nsfw_profile = self._normalize_text_profile(
            self.text_api_profiles.get("nsfw", {})
        )
        text_api_profiles = {"normal": normal_profile, "nsfw": nsfw_profile}
        text_api_active_profile = "normal"
        if self.parent() is not None and bool(getattr(self.parent(), "_workspace_is_nsfw", False)):
            text_api_active_profile = "nsfw"
        active_text_api = copy.deepcopy(text_api_profiles[text_api_active_profile])
        active_text_api["text_generation_mode"] = text_api_active_profile
        current_snapshot = self._build_current_api_snapshot()
        merged_recent = [current_snapshot] + self.recent_api_configs
        self.recent_api_configs = self._deduplicate_recent_api_configs(merged_recent)[:20]

        new_config = {
            "proxy": {
                "enabled": self.cb_enable_proxy.isChecked(),
                "url": self.proxy_url_input.text().strip()
            },
            "text_api_profiles": text_api_profiles,
            "text_api_active_profile": text_api_active_profile,
            "text_generation_mode": text_api_active_profile,
            "text_api": active_text_api,
            "image_api": {
                "type": self.img_type_combo.currentText(),
                "base_url": self.img_base_url_input.text().strip(),
                "api_key": self.img_api_key_input.text().strip(),
                "model": self.img_model_combo.currentText().strip()
            },
            "task_completion_notification_enabled": (
                self.cb_task_completion_notification.isChecked()
            ),
            "recent_api_configs": self.recent_api_configs,
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
