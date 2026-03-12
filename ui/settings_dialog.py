import os
import json
import requests
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QComboBox, QTextEdit, 
                             QMessageBox, QTabWidget, QWidget, QFormLayout)
from PyQt6.QtCore import Qt

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("系统配置 (API/模型)")
        self.resize(500, 450)
        
        # 确定配置文件的路径 (假设工程结构为 root/ui/ 和 root/conf/)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config_dir = os.path.join(base_dir, "conf")
        self.config_path = os.path.join(self.config_dir, "setting.json")
        
        # 加载现有配置
        self.config = self.load_config()
        
        self.init_ui()
        self.populate_data()

    def load_config(self) -> dict:
        """从本地读取 setting.json"""
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
        
        self.txt_instruction_input = QTextEdit()
        self.txt_instruction_input.setPlaceholderText("系统提示词，例如：你是一个专业的小说家...")
        self.text_layout.addRow("系统指令 (Instructions):", self.txt_instruction_input)
        
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

    def populate_data(self):
        """将加载的字典数据填充到 UI 组件中"""
        text_cfg = self.config.get("text_api", {})
        self.txt_type_combo.setCurrentText(text_cfg.get("type", "openai"))
        self.txt_base_url_input.setText(text_cfg.get("base_url", "https://api.openai.com/v1"))
        self.txt_api_key_input.setText(text_cfg.get("api_key", ""))
        self.txt_model_combo.setCurrentText(text_cfg.get("model", "gpt-4o"))
        self.txt_instruction_input.setPlainText(text_cfg.get("instructions", "你是一个专业的AI小说家，擅长根据设定和上下文构建引人入胜的故事。"))

        img_cfg = self.config.get("image_api", {})
        self.img_type_combo.setCurrentText(img_cfg.get("type", "openai"))
        self.img_base_url_input.setText(img_cfg.get("base_url", "https://api.openai.com/v1"))
        self.img_api_key_input.setText(img_cfg.get("api_key", ""))
        self.img_model_combo.setCurrentText(img_cfg.get("model", "dall-e-3"))

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
            
            try:
                # 设置 5 秒超时，防止界面卡死
                response = requests.get(url, headers=headers, timeout=5)
                response.raise_for_status() # 检查 HTTP 错误
                
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
        """将 UI 中的数据收集并覆写到 setting.json"""
        new_config = {
            "text_api": {
                "type": self.txt_type_combo.currentText(),
                "base_url": self.txt_base_url_input.text().strip(),
                "api_key": self.txt_api_key_input.text().strip(),
                "model": self.txt_model_combo.currentText().strip(),
                "instructions": self.txt_instruction_input.toPlainText()
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