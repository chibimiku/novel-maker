import os
import json
import requests
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QComboBox, QTextEdit, 
                             QMessageBox, QTabWidget, QWidget, QFormLayout, QSpinBox, QCheckBox)
from PyQt6.QtCore import Qt

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("系统配置 (API/模型)")
        self.resize(550, 500) # 稍微加大一点窗口以容纳新组件
        
        # 确定配置文件的路径 (假设工程结构为 root/ui/ 和 root/conf/)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config_dir = os.path.join(base_dir, "conf")
        self.config_path = os.path.join(self.config_dir, "setting.json")
        
        # 加载现有配置
        self.config = self.load_config()
        self.instructions_history = [] # 新增：存放历史指令的列表
        
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
        
        txt_model_layout = QHBoxLayout()
        txt_model_layout.addWidget(self.txt_model_combo, stretch=1)
        txt_model_layout.addWidget(self.btn_fetch_txt_models)
        self.text_layout.addRow("模型名称 (Model):", txt_model_layout)
        
        # ================= 新增：历史系统指令的管理 UI =================
        history_layout = QHBoxLayout()
        self.instruction_combo = QComboBox()
        self.btn_save_instruction = QPushButton("💾 存为新模板")
        self.btn_delete_instruction = QPushButton("🗑️ 删除该模板")
        
        history_layout.addWidget(self.instruction_combo, stretch=1)
        history_layout.addWidget(self.btn_save_instruction)
        history_layout.addWidget(self.btn_delete_instruction)
        self.text_layout.addRow("历史系统指令:", history_layout)
        
        self.txt_instruction_input = QTextEdit()
        self.txt_instruction_input.setPlaceholderText("系统提示词，例如：你是一个专业的小说家...")
        self.text_layout.addRow("当前指令内容:", self.txt_instruction_input)
        
        # 绑定历史记录相关事件
        self.instruction_combo.currentIndexChanged.connect(self.on_instruction_changed)
        self.btn_save_instruction.clicked.connect(self.save_instruction_to_history)
        self.btn_delete_instruction.clicked.connect(self.delete_instruction_from_history)
        # ===============================================================

        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(10, 600)
        self.spin_timeout.setSuffix(" 秒")
        self.text_layout.addRow("请求超时时间 (Timeout):", self.spin_timeout)

        
        self.tabs.addTab(self.text_tab, "📝 文本生成模型")

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

    # ================= 新增：历史指令的逻辑处理 =================
    def update_instruction_combo(self):
        """刷新下拉列表视图"""
        self.instruction_combo.blockSignals(True)
        self.instruction_combo.clear()
        for inst in self.instructions_history:
            # 截取前 15 个字符作为摘要标题
            display_name = inst[:15].replace("\n", " ") + ("..." if len(inst) > 15 else "")
            self.instruction_combo.addItem(display_name, inst)
        self.instruction_combo.blockSignals(False)

    def on_instruction_changed(self, index):
        """当下拉框切换时，更新文本框内容"""
        if index >= 0:
            content = self.instruction_combo.itemData(index)
            self.txt_instruction_input.setPlainText(content)

    def save_instruction_to_history(self):
        """将当前编辑框的内容保存为新的历史记录"""
        current_text = self.txt_instruction_input.toPlainText().strip()
        if not current_text:
            return
            
        if current_text not in self.instructions_history:
            self.instructions_history.append(current_text)
            self.update_instruction_combo()
            self.instruction_combo.setCurrentIndex(len(self.instructions_history) - 1)
            QMessageBox.information(self, "成功", "已保存为新的指令模板！")
        else:
            QMessageBox.information(self, "提示", "该指令模板已存在于记录中。")

    def delete_instruction_from_history(self):
        """删除当前选中的历史记录"""
        index = self.instruction_combo.currentIndex()
        if index >= 0:
            del self.instructions_history[index]
            self.update_instruction_combo()
            if self.instructions_history:
                self.txt_instruction_input.setPlainText(self.instructions_history[0])
            else:
                self.txt_instruction_input.clear()
            QMessageBox.information(self, "成功", "已删除该指令模板！")
    # ==========================================================

    def populate_data(self):
        text_cfg = self.config.get("text_api", {})
        self.txt_type_combo.setCurrentText(text_cfg.get("type", "openai"))
        self.txt_base_url_input.setText(text_cfg.get("base_url", "https://api.openai.com/v1"))
        self.txt_api_key_input.setText(text_cfg.get("api_key", ""))
        self.txt_model_combo.setCurrentText(text_cfg.get("model", "gpt-4o"))
        self.spin_timeout.setValue(text_cfg.get("timeout", 120))
        
        # 加载历史指令列表
        self.instructions_history = text_cfg.get("instructions_history", [])
        current_instruction = text_cfg.get("instructions", "你是一个专业的AI小说家，擅长根据设定和上下文构建引人入胜的故事。")
        
        # 确保当前指令在历史列表中
        if current_instruction and current_instruction not in self.instructions_history:
            self.instructions_history.insert(0, current_instruction)
            
        self.update_instruction_combo()
        
        # 设置当前选中的指令文本
        self.txt_instruction_input.setPlainText(current_instruction)
        # 尝试在下拉框中定位到当前指令
        idx = self.instruction_combo.findData(current_instruction)
        if idx >= 0:
            self.instruction_combo.setCurrentIndex(idx)

        img_cfg = self.config.get("image_api", {})
        self.img_type_combo.setCurrentText(img_cfg.get("type", "openai"))
        self.img_base_url_input.setText(img_cfg.get("base_url", "https://api.openai.com/v1"))
        self.img_api_key_input.setText(img_cfg.get("api_key", ""))
        self.img_model_combo.setCurrentText(img_cfg.get("model", "dall-e-3"))

        proxy_cfg = self.config.get("proxy", {})
        self.cb_enable_proxy.setChecked(proxy_cfg.get("enabled", False))
        self.proxy_url_input.setText(proxy_cfg.get("url", "http://127.0.0.1:7890"))



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
                models = [item.get("id") for item in data.get("data", []) if "id" in item]
                
                if models:
                    combo_box.clear()
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
        current_instruction = self.txt_instruction_input.toPlainText().strip()
        if current_instruction and current_instruction not in self.instructions_history:
            self.instructions_history.append(current_instruction)

        new_config = {
            "proxy": {
                "enabled": self.cb_enable_proxy.isChecked(),
                "url": self.proxy_url_input.text().strip()
            },
            "text_api": {
                # ... 保持你原有的 text_api 内容不变 ...
                "type": self.txt_type_combo.currentText(),
                "base_url": self.txt_base_url_input.text().strip(),
                "api_key": self.txt_api_key_input.text().strip(),
                "model": self.txt_model_combo.currentText().strip(),
                "timeout": self.spin_timeout.value(),
                "instructions": current_instruction,
                "instructions_history": self.instructions_history # 新增字段落盘
            },
            "image_api": {
                # ... 保持你原有的 image_api 内容不变 ...
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