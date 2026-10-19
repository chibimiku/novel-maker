"""模型服务管理窗口：提供商（Provider）→ 模型（Model）二级配置。

功能：

* 新增 / 编辑 / 删除提供商（类型、Base URL、API Key、超时、代理）；
* 每个提供商下新增 / 删除具体模型，可从内置“模型全集”批量挑选；
* 每个模型单独配置上下文窗口、最大输出、能力、支持的采样参数与参数值；
* 指定“普通 / NSFW”两个画像各自使用哪个提供商的哪个模型；
* 一键测试连接（真实发一次最小请求）。

配置保存回 ``conf/setting.json`` 的 ``providers`` 与 ``text_api_profiles``，
不会破坏其它字段。
"""
from __future__ import annotations

import copy
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config_store import load_raw_config, save_raw_config
from core.llm_client import test_runtime
from core.model_config import (
    PARAM_DEFS,
    PARAM_ORDER,
    RuntimeModel,
    catalog_choices,
    default_base_url,
    ensure_providers,
    load_catalog,
    new_id,
    normalize_model,
    normalize_params,
    normalize_provider,
    validate_config,
)
from core.token_budget import TokenBudget


PROFILE_LABELS = {"normal": "普通小说画像", "nsfw": "NSFW 小说画像"}


class CatalogPickerDialog(QDialog):
    """从内置模型全集里挑选模型（支持搜索 + 多选）。"""

    def __init__(self, catalog: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从模型全集添加")
        self.resize(560, 520)
        self.catalog = catalog
        self.selected_models: list[str] = []

        layout = QVBoxLayout(self)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键字过滤，例如 claude / gemini / deepseek")
        self.search_input.textChanged.connect(self._refresh)
        layout.addWidget(self.search_input)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        layout.addWidget(self.list_widget, stretch=1)

        self.hint_label = QLabel("提示：可按住 Ctrl / Shift 多选。")
        self.hint_label.setStyleSheet("color: gray;")
        layout.addWidget(self.hint_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh("")

    def _refresh(self, keyword: str):
        keyword = self.search_input.text().strip()
        self.list_widget.clear()
        choices = catalog_choices(self.catalog, keyword)
        self.hint_label.setText(
            f"共 {len(choices)} 个可选模型。提示：可按住 Ctrl / Shift 多选。"
        )
        for item in choices:
            context = item["context_size"]
            label = f"{item['model']}    (窗口 {context:,})"
            list_item = QListWidgetItem(label)
            list_item.setData(Qt.ItemDataRole.UserRole, item["model"])
            self.list_widget.addItem(list_item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _on_accept(self):
        models = []
        for item in self.list_widget.selectedItems():
            model_key = item.data(Qt.ItemDataRole.UserRole)
            if model_key:
                models.append(str(model_key))
        if not models:
            QMessageBox.information(self, "提示", "请至少选择一个模型。")
            return
        self.selected_models = models
        self.accept()


class ModelManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("模型服务：提供商 / 模型 / 参数")
        self.resize(1080, 760)

        self.catalog = load_catalog()
        self.raw_config = load_raw_config()
        self.config = ensure_providers(copy.deepcopy(self.raw_config), self.catalog)
        self.providers: list[dict] = self.config.get("providers", [])
        self.profiles: dict = self.config.get("text_api_profiles", {}) or {
            "normal": {},
            "nsfw": {},
        }
        for key in ("normal", "nsfw"):
            self.profiles.setdefault(key, {})

        self._current_provider_index = -1
        self._current_model_index = -1
        self._loading = False
        self._param_widgets: dict[str, dict] = {}

        self._build_ui()
        self._refresh_provider_list()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        profile_box = QGroupBox("画像绑定（决定该模式实际使用哪个模型）")
        profile_layout = QHBoxLayout(profile_box)
        profile_layout.addWidget(QLabel("配置画像:"))
        self.profile_combo = QComboBox()
        for key, label in PROFILE_LABELS.items():
            self.profile_combo.addItem(label, key)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_layout.addWidget(self.profile_combo)

        profile_layout.addWidget(QLabel("使用提供商:"))
        self.bind_provider_combo = QComboBox()
        self.bind_provider_combo.currentIndexChanged.connect(self._on_bind_provider_changed)
        profile_layout.addWidget(self.bind_provider_combo, stretch=1)

        profile_layout.addWidget(QLabel("模型:"))
        self.bind_model_combo = QComboBox()
        self.bind_model_combo.currentIndexChanged.connect(self._on_bind_model_changed)
        profile_layout.addWidget(self.bind_model_combo, stretch=1)
        layout.addWidget(profile_box)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)

        # 左：提供商
        provider_panel = QWidget()
        provider_layout = QVBoxLayout(provider_panel)
        provider_layout.setContentsMargins(0, 0, 0, 0)
        provider_layout.addWidget(QLabel("<b>提供商</b>"))
        self.provider_list = QListWidget()
        self.provider_list.currentRowChanged.connect(self._on_provider_selected)
        provider_layout.addWidget(self.provider_list, stretch=1)

        provider_buttons = QHBoxLayout()
        self.btn_add_provider = QPushButton("➕ 新增")
        self.btn_add_provider.clicked.connect(self._add_provider)
        self.btn_remove_provider = QPushButton("🗑️ 删除")
        self.btn_remove_provider.clicked.connect(self._remove_provider)
        self.btn_dup_provider = QPushButton("📄 复制")
        self.btn_dup_provider.clicked.connect(self._duplicate_provider)
        provider_buttons.addWidget(self.btn_add_provider)
        provider_buttons.addWidget(self.btn_dup_provider)
        provider_buttons.addWidget(self.btn_remove_provider)
        provider_layout.addLayout(provider_buttons)
        splitter.addWidget(provider_panel)

        # 右：提供商详情 + 模型
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.provider_group = QGroupBox("提供商配置")
        provider_form = QFormLayout(self.provider_group)
        self.provider_name_input = QLineEdit()
        provider_form.addRow("名称:", self.provider_name_input)

        self.provider_type_combo = QComboBox()
        self.provider_type_combo.addItem("OpenAI 兼容接口", "openai")
        self.provider_type_combo.addItem("Google Gemini", "gemini")
        self.provider_type_combo.currentIndexChanged.connect(self._on_provider_type_changed)
        provider_form.addRow("接口类型:", self.provider_type_combo)

        self.provider_base_url_input = QLineEdit()
        provider_form.addRow("Base URL:", self.provider_base_url_input)

        key_row = QHBoxLayout()
        self.provider_api_key_input = QLineEdit()
        self.provider_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_row.addWidget(self.provider_api_key_input, stretch=1)
        self.btn_toggle_key = QPushButton("显示")
        self.btn_toggle_key.setCheckable(True)
        self.btn_toggle_key.toggled.connect(self._on_toggle_key)
        key_row.addWidget(self.btn_toggle_key)
        provider_form.addRow("API Key:", key_row)

        self.provider_timeout_spin = QSpinBox()
        self.provider_timeout_spin.setRange(5, 3600)
        self.provider_timeout_spin.setSuffix(" 秒")
        provider_form.addRow("超时:", self.provider_timeout_spin)
        right_layout.addWidget(self.provider_group)

        model_group = QGroupBox("模型列表")
        model_layout = QVBoxLayout(model_group)
        self.model_table = QTableWidget(0, 5)
        self.model_table.setHorizontalHeaderLabels(
            ["模型名", "显示名", "上下文", "最大输出", "能力"]
        )
        self.model_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.model_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.model_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.model_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.model_table.itemSelectionChanged.connect(self._on_model_selected)
        model_layout.addWidget(self.model_table, stretch=1)

        model_buttons = QHBoxLayout()
        self.btn_add_catalog_model = QPushButton("📚 从全集添加")
        self.btn_add_catalog_model.clicked.connect(self._add_models_from_catalog)
        self.btn_add_custom_model = QPushButton("➕ 自定义模型")
        self.btn_add_custom_model.clicked.connect(self._add_custom_model)
        self.btn_remove_model = QPushButton("🗑️ 删除模型")
        self.btn_remove_model.clicked.connect(self._remove_model)
        model_buttons.addWidget(self.btn_add_catalog_model)
        model_buttons.addWidget(self.btn_add_custom_model)
        model_buttons.addWidget(self.btn_remove_model)
        model_buttons.addStretch()
        model_layout.addLayout(model_buttons)
        right_layout.addWidget(model_group, stretch=1)

        splitter.addWidget(right_panel)
        splitter.setSizes([230, 850])

        # 模型参数区（可滚动）
        params_group = QGroupBox("模型参数（未勾选的参数不会发送给接口）")
        params_outer = QVBoxLayout(params_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(250)
        params_container = QWidget()
        params_layout = QVBoxLayout(params_container)
        params_layout.setContentsMargins(4, 4, 4, 4)

        basic_form = QFormLayout()
        self.model_key_input = QComboBox()
        self.model_key_input.setEditable(True)
        self.model_key_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for choice in catalog_choices(self.catalog):
            self.model_key_input.addItem(choice["model"])
        self.model_key_input.currentTextChanged.connect(self._on_model_key_edited)
        basic_form.addRow("模型名（请求用）:", self.model_key_input)

        self.model_display_input = QLineEdit()
        basic_form.addRow("显示名:", self.model_display_input)

        self.model_context_spin = QSpinBox()
        self.model_context_spin.setRange(512, 20_000_000)
        self.model_context_spin.setSingleStep(1024)
        self.model_context_spin.setSuffix(" tokens")
        basic_form.addRow("上下文窗口:", self.model_context_spin)

        self.model_max_output_spin = QSpinBox()
        self.model_max_output_spin.setRange(128, 2_000_000)
        self.model_max_output_spin.setSingleStep(256)
        self.model_max_output_spin.setSuffix(" tokens")
        basic_form.addRow("最大输出:", self.model_max_output_spin)

        self.model_caps_input = QLineEdit()
        self.model_caps_input.setPlaceholderText("逗号分隔，例如 chat, vision, reasoning")
        basic_form.addRow("能力:", self.model_caps_input)

        self.budget_label = QLabel("")
        self.budget_label.setStyleSheet("color: gray;")
        basic_form.addRow("预算预览:", self.budget_label)
        params_layout.addLayout(basic_form)

        supported_group = QGroupBox("该模型支持的参数")
        supported_layout = QHBoxLayout(supported_group)
        self.supported_checkboxes: dict[str, QCheckBox] = {}
        for name in PARAM_ORDER:
            checkbox = QCheckBox(name)
            checkbox.toggled.connect(self._on_supported_toggled)
            self.supported_checkboxes[name] = checkbox
            supported_layout.addWidget(checkbox)
        supported_layout.addStretch()
        params_layout.addWidget(supported_group)

        values_group = QGroupBox("参数值")
        values_form = QFormLayout(values_group)
        for name in PARAM_ORDER:
            self._build_param_row(values_form, name)
        params_layout.addWidget(values_group)
        params_layout.addStretch()

        scroll.setWidget(params_container)
        params_outer.addWidget(scroll)
        layout.addWidget(splitter, stretch=3)
        layout.addWidget(params_group, stretch=2)

        # 底部按钮
        bottom = QHBoxLayout()
        self.btn_test = QPushButton("🔌 测试连接")
        self.btn_test.clicked.connect(self._test_connection)
        bottom.addWidget(self.btn_test)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")
        bottom.addWidget(self.status_label, stretch=1)
        self.btn_save = QPushButton("💾 保存")
        self.btn_save.clicked.connect(self._save)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_save)
        layout.addLayout(bottom)

    def _build_param_row(self, form: QFormLayout, name: str):
        definition = PARAM_DEFS[name]
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        enabled = QCheckBox("启用")
        row_layout.addWidget(enabled)

        if name in ("temperature", "top_p", "frequency_penalty", "presence_penalty",
                    "repetition_penalty", "min_p"):
            widget: Any = QDoubleSpinBox()
            widget.setDecimals(2)
            widget.setSingleStep(float(definition.get("step", 0.05)))
            widget.setRange(float(definition.get("min", 0.0)), float(definition.get("max", 2.0)))
        elif name == "stop":
            widget = QPlainTextEdit()
            widget.setFixedHeight(56)
            widget.setPlaceholderText("每行一个停止词，最多 4 个")
        else:
            widget = QSpinBox()
            widget.setRange(int(definition.get("min", 0)), int(definition.get("max", 2**31 - 1)))
            widget.setSingleStep(int(definition.get("step", 1)))

        enabled.toggled.connect(
            lambda checked, param_name=name: self._on_param_toggled(param_name, checked)
        )

        hint = QLabel(definition.get("hint", ""))
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        row_layout.addWidget(widget, stretch=1)
        form.addRow(f"{definition['label']}:", row)
        form.addRow("", hint)
        self._param_widgets[name] = {
            "enabled": enabled,
            "widget": widget,
            "hint": hint,
        }

    # ------------------------------------------------------------------
    # 数据 <-> UI
    # ------------------------------------------------------------------
    def _current_provider(self) -> dict | None:
        if 0 <= self._current_provider_index < len(self.providers):
            return self.providers[self._current_provider_index]
        return None

    def _current_model(self) -> dict | None:
        provider = self._current_provider()
        if provider is None:
            return None
        models = provider.get("models", [])
        if 0 <= self._current_model_index < len(models):
            return models[self._current_model_index]
        return None

    def _refresh_provider_list(self, select_index: int | None = None):
        self._loading = True
        try:
            self.provider_list.clear()
            for provider in self.providers:
                model_count = len(provider.get("models", []))
                item = QListWidgetItem(
                    f"{provider.get('name', '未命名')}  ({model_count} 模型)"
                )
                item.setData(Qt.ItemDataRole.UserRole, provider.get("id", ""))
                self.provider_list.addItem(item)
            if select_index is None:
                select_index = 0 if self.providers else -1
            if self.providers:
                select_index = max(0, min(select_index, len(self.providers) - 1))
                self.provider_list.setCurrentRow(select_index)
            else:
                select_index = -1
        finally:
            self._loading = False
        if select_index >= 0:
            self._load_provider(select_index)
        else:
            self._current_provider_index = -1
            self._current_model_index = -1
            self._clear_provider_form()
            self._refresh_model_table()
            self._refresh_profile_combos()

    def _refresh_model_table(self, select_index: int | None = None):
        self._loading = True
        try:
            provider = self._current_provider()
            models = provider.get("models", []) if provider else []
            self.model_table.setRowCount(len(models))
            for row, model in enumerate(models):
                values = [
                    model.get("model", ""),
                    model.get("display_name", ""),
                    f"{int(model.get('context_size', 0)):,}",
                    f"{int(model.get('max_output_tokens', 0)):,}",
                    ", ".join(model.get("capabilities", []) or []),
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if column in (2, 3):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self.model_table.setItem(row, column, item)
            if models:
                if select_index is None:
                    select_index = min(self._current_model_index, len(models) - 1)
                select_index = max(0, min(select_index, len(models) - 1))
                self.model_table.selectRow(select_index)
                self._current_model_index = select_index
                self._load_model_form(models[select_index])
            else:
                self._current_model_index = -1
                self._clear_model_form()
        finally:
            self._loading = False
        self._refresh_profile_combos()

    def _refresh_profile_combos(self):
        self._loading = True
        try:
            profile_key = self.profile_combo.currentData() or "normal"
            profile = self.profiles.setdefault(profile_key, {})

            self.bind_provider_combo.clear()
            for provider in self.providers:
                self.bind_provider_combo.addItem(provider.get("name", ""), provider.get("id", ""))
            provider_index = self.bind_provider_combo.findData(profile.get("provider_id", ""))
            if provider_index < 0 and self.providers:
                provider_index = 0
            if provider_index >= 0:
                self.bind_provider_combo.setCurrentIndex(provider_index)

            self.bind_model_combo.clear()
            bound_provider_id = self.bind_provider_combo.currentData()
            provider = next(
                (p for p in self.providers if p.get("id") == bound_provider_id), None
            )
            if provider is not None:
                for model in provider.get("models", []):
                    label = f"{model.get('display_name') or model.get('model')} ({model.get('model')})"
                    self.bind_model_combo.addItem(label, model.get("id", ""))
            model_index = self.bind_model_combo.findData(profile.get("model_id", ""))
            if model_index < 0 and self.bind_model_combo.count():
                model_index = 0
            if model_index >= 0:
                self.bind_model_combo.setCurrentIndex(model_index)
        finally:
            self._loading = False

    def _on_profile_changed(self, _index: int):
        self._refresh_profile_combos()

    def _on_bind_provider_changed(self, _index: int):
        if self._loading:
            return
        profile_key = self.profile_combo.currentData() or "normal"
        provider_id = self.bind_provider_combo.currentData()
        provider = next((p for p in self.providers if p.get("id") == provider_id), None)
        models = provider.get("models", []) if provider else []
        self._loading = True
        try:
            self.bind_model_combo.clear()
            for model in models:
                label = f"{model.get('display_name') or model.get('model')} ({model.get('model')})"
                self.bind_model_combo.addItem(label, model.get("id", ""))
            if models:
                self.bind_model_combo.setCurrentIndex(0)
        finally:
            self._loading = False
        profile = self.profiles.setdefault(profile_key, {})
        profile["provider_id"] = provider_id or ""
        profile["model_id"] = self.bind_model_combo.currentData() or ""

    def _on_bind_model_changed(self, _index: int):
        if self._loading:
            return
        profile_key = self.profile_combo.currentData() or "normal"
        profile = self.profiles.setdefault(profile_key, {})
        profile["provider_id"] = self.bind_provider_combo.currentData() or ""
        profile["model_id"] = self.bind_model_combo.currentData() or ""

    # -- 提供商 -------------------------------------------------------
    def _on_provider_selected(self, row: int):
        if self._loading:
            return
        self._store_provider_form()
        self._load_provider(row)

    def _load_provider(self, row: int):
        """只负责把指定提供商加载到表单/模型列表，不回写旧表单。"""
        self._current_provider_index = row
        self._current_model_index = 0
        provider = self._current_provider()
        if provider is None:
            self._clear_provider_form()
            self._refresh_model_table()
            return
        self.provider_group.setEnabled(True)
        self._loading = True
        try:
            self.provider_name_input.setText(provider.get("name", ""))
            index = self.provider_type_combo.findData(provider.get("type", "openai"))
            self.provider_type_combo.setCurrentIndex(max(0, index))
            self.provider_base_url_input.setText(provider.get("base_url", ""))
            self.provider_api_key_input.setText(provider.get("api_key", ""))
            self.provider_timeout_spin.setValue(int(provider.get("timeout", 120)))
        finally:
            self._loading = False
        self._refresh_model_table()

    def _store_provider_form(self):
        provider = self._current_provider()
        if provider is None:
            return
        provider["name"] = self.provider_name_input.text().strip() or "未命名提供商"
        provider["type"] = self.provider_type_combo.currentData() or "openai"
        provider["base_url"] = self.provider_base_url_input.text().strip() or default_base_url(
            provider["type"]
        )
        provider["api_key"] = self.provider_api_key_input.text().strip()
        provider["timeout"] = int(self.provider_timeout_spin.value())

    def _clear_provider_form(self):
        self._loading = True
        try:
            self.provider_name_input.clear()
            self.provider_type_combo.setCurrentIndex(0)
            self.provider_base_url_input.setText(default_base_url("openai"))
            self.provider_api_key_input.clear()
            self.provider_timeout_spin.setValue(120)
            self.provider_group.setEnabled(False)
        finally:
            self._loading = False

    def _on_provider_type_changed(self, _index: int):
        if self._loading:
            return
        api_type = self.provider_type_combo.currentData() or "openai"
        current = self.provider_base_url_input.text().strip()
        defaults = {default_base_url("openai"), default_base_url("gemini"), ""}
        if current in defaults:
            self.provider_base_url_input.setText(default_base_url(api_type))

    def _on_toggle_key(self, checked: bool):
        self.provider_api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.btn_toggle_key.setText("隐藏" if checked else "显示")

    def _add_provider(self):
        self._store_provider_form()
        provider = normalize_provider(
            {
                "id": new_id("pv"),
                "name": f"新提供商 {len(self.providers) + 1}",
                "type": "openai",
                "base_url": default_base_url("openai"),
                "models": [],
            },
            self.catalog,
        )
        self.providers.append(provider)
        self._current_provider_index = len(self.providers) - 1
        self._refresh_provider_list(self._current_provider_index)
        self.provider_group.setEnabled(True)
        self.provider_name_input.setFocus()

    def _duplicate_provider(self):
        self._store_provider_form()
        provider = self._current_provider()
        if provider is None:
            return
        clone = copy.deepcopy(provider)
        clone["id"] = new_id("pv")
        clone["name"] = f"{provider.get('name', '')} 副本"
        for model in clone.get("models", []):
            model["id"] = new_id("md")
        self.providers.append(clone)
        self._current_provider_index = len(self.providers) - 1
        self._refresh_provider_list(self._current_provider_index)

    def _remove_provider(self):
        provider = self._current_provider()
        if provider is None:
            return
        reply = QMessageBox.question(
            self,
            "删除提供商",
            f"确定删除提供商【{provider.get('name', '')}】及其下所有模型吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.providers.pop(self._current_provider_index)
        self._current_provider_index = min(
            self._current_provider_index, len(self.providers) - 1
        )
        self._current_model_index = 0
        self._refresh_provider_list(max(0, self._current_provider_index))
        self._fix_profile_bindings()

    def _fix_profile_bindings(self):
        """删除提供商/模型后，把失效的画像绑定指向第一个可用模型。"""
        default_provider = next(
            (p for p in self.providers if p.get("models")), None
        )
        valid_provider_ids = {p.get("id") for p in self.providers}
        for key in ("normal", "nsfw"):
            profile = self.profiles.setdefault(key, {})
            provider_id = profile.get("provider_id")
            provider = next(
                (p for p in self.providers if p.get("id") == provider_id), None
            )
            model_ids = {m.get("id") for m in (provider or {}).get("models", [])}
            if provider is None or profile.get("model_id") not in model_ids:
                if default_provider is not None:
                    profile["provider_id"] = default_provider["id"]
                    profile["model_id"] = default_provider["models"][0]["id"]
                else:
                    profile["provider_id"] = ""
                    profile["model_id"] = ""
            profile.setdefault("provider_id", "")
            profile.setdefault("model_id", "")
            if profile["provider_id"] and profile["provider_id"] not in valid_provider_ids:
                profile["provider_id"] = ""
        self._refresh_profile_combos()

    # -- 模型 ---------------------------------------------------------
    def _on_model_selected(self):
        if self._loading:
            return
        self._store_model_form()
        rows = self.model_table.selectionModel().selectedRows()
        if not rows:
            return
        self._current_model_index = rows[0].row()
        model = self._current_model()
        if model is not None:
            self._load_model_form(model)

    def _load_model_form(self, model: dict):
        self._loading = True
        try:
            self.model_key_input.setCurrentText(model.get("model", ""))
            self.model_display_input.setText(model.get("display_name", ""))
            self.model_context_spin.setValue(int(model.get("context_size", 8192)))
            self.model_max_output_spin.setValue(int(model.get("max_output_tokens", 2048)))
            self.model_caps_input.setText(", ".join(model.get("capabilities", []) or []))

            supported = set(model.get("supported_params", []) or [])
            params = model.get("params", {}) or {}
            for name, widgets in self._param_widgets.items():
                checkbox: QCheckBox = widgets["enabled"]
                supported_box = self.supported_checkboxes[name]
                supported_box.blockSignals(True)
                supported_box.setChecked(name in supported)
                supported_box.blockSignals(False)

                value = params.get(name)
                has_value = name in params and value not in (None, [], "")
                checkbox.blockSignals(True)
                checkbox.setChecked(has_value)
                checkbox.blockSignals(False)
                checkbox.setEnabled(name in supported)

                widget = widgets["widget"]
                definition = PARAM_DEFS[name]
                if isinstance(widget, QPlainTextEdit):
                    widget.setPlainText("\n".join(value) if isinstance(value, list) else "")
                elif isinstance(widget, QDoubleSpinBox):
                    widget.setValue(float(value if value is not None else definition.get("default") or 0.0))
                elif isinstance(widget, QSpinBox):
                    default = definition.get("default")
                    widget.setValue(int(value if value is not None else (default or int(definition.get("min", 0)))))
                widget.setEnabled(has_value and name in supported)
        finally:
            self._loading = False
        self._update_budget_preview()

    def _clear_model_form(self):
        self._loading = True
        try:
            self.model_key_input.setCurrentText("")
            self.model_display_input.clear()
            self.model_context_spin.setValue(8192)
            self.model_max_output_spin.setValue(2048)
            self.model_caps_input.clear()
            for name, widgets in self._param_widgets.items():
                widgets["enabled"].setChecked(False)
                widgets["enabled"].setEnabled(False)
                widgets["widget"].setEnabled(False)
                self.supported_checkboxes[name].setChecked(False)
        finally:
            self._loading = False
        self._update_budget_preview()

    def _on_model_key_edited(self, text: str):
        if self._loading:
            return
        # 从模型全集自动补全上下文/输出/能力（仅在字段为空时）
        keyword = (text or "").strip()
        if not keyword:
            return
        entry = None
        for choice in catalog_choices(self.catalog, keyword):
            if choice["model"].lower() == keyword.lower():
                entry = choice
                break
        if entry is None:
            return
        if not self.model_context_spin.value() or self.model_context_spin.value() == 8192:
            self.model_context_spin.setValue(int(entry["context_size"]))
        if not self.model_display_input.text().strip():
            self.model_display_input.setText(entry["model"].split("/")[-1])
        if not self.model_caps_input.text().strip():
            self.model_caps_input.setText(", ".join(entry["capabilities"]))
        if not any(self.supported_checkboxes[name].isChecked() for name in PARAM_ORDER):
            supported = entry.get("supported_params") or []
            for name in PARAM_ORDER:
                self.supported_checkboxes[name].setChecked(name in supported)
        self._update_budget_preview()

    def _on_supported_toggled(self, _checked: bool):
        if self._loading:
            return
        for name, widgets in self._param_widgets.items():
            supported = self.supported_checkboxes[name].isChecked()
            widgets["enabled"].setEnabled(supported)
            if not supported:
                widgets["enabled"].setChecked(False)
                widgets["widget"].setEnabled(False)
            elif widgets["enabled"].isChecked():
                widgets["widget"].setEnabled(True)

    def _on_param_toggled(self, name: str, checked: bool):
        widgets = self._param_widgets.get(name)
        if not widgets:
            return
        widgets["widget"].setEnabled(checked)
        self._update_budget_preview()

    def _collect_params_from_ui(self) -> dict:
        params: dict[str, Any] = {}
        for name, widgets in self._param_widgets.items():
            if not self.supported_checkboxes[name].isChecked():
                continue
            if not widgets["enabled"].isChecked():
                # 显式 None：表示“支持该参数但本次不发送”，避免被默认值重新填回
                params[name] = None
                continue
            widget = widgets["widget"]
            if isinstance(widget, QPlainTextEdit):
                text = widget.toPlainText().strip()
                values = [line.strip() for line in text.splitlines() if line.strip()]
                if values:
                    params[name] = values
            elif isinstance(widget, QDoubleSpinBox):
                params[name] = float(widget.value())
            elif isinstance(widget, QSpinBox):
                params[name] = int(widget.value())
        return normalize_params(
            params,
            [name for name in PARAM_ORDER if self.supported_checkboxes[name].isChecked()],
        )

    def _store_model_form(self):
        model = self._current_model()
        if model is None:
            return
        model_key = self.model_key_input.currentText().strip()
        if not model_key:
            return
        model["model"] = model_key
        model["display_name"] = self.model_display_input.text().strip() or model_key
        model["context_size"] = int(self.model_context_spin.value())
        model["max_output_tokens"] = int(self.model_max_output_spin.value())
        model["capabilities"] = [
            item.strip()
            for item in self.model_caps_input.text().replace("，", ",").split(",")
            if item.strip()
        ]
        model["supported_params"] = [
            name for name in PARAM_ORDER if self.supported_checkboxes[name].isChecked()
        ]
        model["params"] = self._collect_params_from_ui()
        self._refresh_model_row(self._current_model_index)

    def _refresh_model_row(self, row: int):
        provider = self._current_provider()
        models = provider.get("models", []) if provider else []
        if not (0 <= row < len(models)) or row >= self.model_table.rowCount():
            return
        model = models[row]
        values = [
            model.get("model", ""),
            model.get("display_name", ""),
            f"{int(model.get('context_size', 0)):,}",
            f"{int(model.get('max_output_tokens', 0)):,}",
            ", ".join(model.get("capabilities", []) or []),
        ]
        previous = self._loading
        self._loading = True
        try:
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (2, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.model_table.setItem(row, column, item)
        finally:
            self._loading = previous

    def _add_models_from_catalog(self):
        provider = self._current_provider()
        if provider is None:
            QMessageBox.information(self, "提示", "请先选择或新建一个提供商。")
            return
        picker = CatalogPickerDialog(self.catalog, self)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        existing = {model.get("model") for model in provider.get("models", [])}
        added = 0
        for model_key in picker.selected_models:
            if model_key in existing:
                continue
            model = normalize_model({"model": model_key}, self.catalog, api_type=provider.get("type", "openai"))
            provider.setdefault("models", []).append(model)
            added += 1
        self._current_model_index = max(0, len(provider.get("models", [])) - 1)
        self._refresh_model_table(self._current_model_index)
        self.status_label.setText(f"已添加 {added} 个模型。")

    def _add_custom_model(self):
        provider = self._current_provider()
        if provider is None:
            QMessageBox.information(self, "提示", "请先选择或新建一个提供商。")
            return
        self._store_model_form()
        model = normalize_model({"model": ""}, self.catalog, api_type=provider.get("type", "openai"))
        model["display_name"] = "自定义模型"
        provider.setdefault("models", []).append(model)
        self._current_model_index = len(provider["models"]) - 1
        self._refresh_model_table(self._current_model_index)
        self.model_key_input.setFocus()

    def _remove_model(self):
        provider = self._current_provider()
        model = self._current_model()
        if provider is None or model is None:
            return
        reply = QMessageBox.question(
            self,
            "删除模型",
            f"确定删除模型【{model.get('model', '')}】吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        provider["models"].pop(self._current_model_index)
        self._current_model_index = min(
            self._current_model_index, len(provider.get("models", [])) - 1
        )
        self._refresh_model_table(max(0, self._current_model_index))
        self._fix_profile_bindings()

    def _update_budget_preview(self):
        try:
            context_size = int(self.model_context_spin.value())
            max_output = int(self.model_max_output_spin.value())
        except Exception:
            return
        budget = TokenBudget(context_size=context_size, max_output_tokens=max_output)
        self.budget_label.setText(
            f"输入预算 ≈ {budget.input_budget():,} tokens "
            f"（窗口 {budget.context_size:,} - 输出 {budget.max_output_tokens:,} - 协议预留）"
        )

    # ------------------------------------------------------------------
    # 测试 / 保存
    # ------------------------------------------------------------------
    def _build_runtime_from_form(self) -> RuntimeModel | None:
        provider = self._current_provider()
        if provider is None:
            return None
        self._store_provider_form()
        self._store_model_form()
        model = self._current_model()
        if model is None:
            return None
        return RuntimeModel(
            profile_key=self.profile_combo.currentData() or "normal",
            provider_id=provider.get("id", ""),
            provider_name=provider.get("name", ""),
            api_type=provider.get("type", "openai"),
            base_url=provider.get("base_url", ""),
            api_key=provider.get("api_key", ""),
            timeout=int(provider.get("timeout", 120)),
            model_id=model.get("id", ""),
            model_key=model.get("model", ""),
            display_name=model.get("display_name", ""),
            context_size=int(model.get("context_size", 8192)),
            max_output_tokens=int(model.get("max_output_tokens", 2048)),
            capabilities=list(model.get("capabilities", [])),
            supported_params=list(model.get("supported_params", [])),
            params=dict(model.get("params", {})),
        )

    def _test_connection(self):
        runtime = self._build_runtime_from_form()
        if runtime is None:
            QMessageBox.information(self, "提示", "请先选择一个模型。")
            return
        if not runtime.api_key:
            QMessageBox.warning(self, "缺少 API Key", "请先填写该提供商的 API Key。")
            return
        self.status_label.setText("正在测试连接...")
        self.btn_test.setEnabled(False)
        try:
            ok, message = test_runtime(runtime)
        finally:
            self.btn_test.setEnabled(True)
        self.status_label.setText(message[:120])
        if ok:
            QMessageBox.information(self, "测试成功", message)
        else:
            QMessageBox.warning(self, "测试失败", message)

    def _save(self):
        self._store_provider_form()
        self._store_model_form()
        # 统一再归一化一次，保证保存进文件的都是合法结构
        normalized_providers = [
            normalize_provider(provider, self.catalog) for provider in self.providers
        ]
        config_to_save = copy.deepcopy(self.raw_config)
        config_to_save["providers"] = normalized_providers

        profiles = config_to_save.get("text_api_profiles")
        if not isinstance(profiles, dict):
            profiles = {}
        for key in ("normal", "nsfw"):
            profile = profiles.get(key)
            if not isinstance(profile, dict):
                profile = {}
            binding = self.profiles.get(key, {})
            profile["provider_id"] = binding.get("provider_id", "")
            profile["model_id"] = binding.get("model_id", "")
            profiles[key] = profile
        config_to_save["text_api_profiles"] = profiles

        issues = validate_config({"providers": normalized_providers})
        if issues:
            reply = QMessageBox.question(
                self,
                "配置可能不完整",
                "\n".join(issues) + "\n\n仍然保存吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        if not save_raw_config(config_to_save):
            QMessageBox.critical(self, "保存失败", "写入 conf/setting.json 失败，请检查文件权限。")
            return
        QMessageBox.information(self, "保存成功", "模型配置已保存，将在关闭窗口后生效。")
        self.accept()


__all__ = ["ModelManagerDialog", "CatalogPickerDialog", "PROFILE_LABELS"]
