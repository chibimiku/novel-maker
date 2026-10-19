"""模型服务管理窗口测试：二级结构、参数编辑、画像绑定。"""
from __future__ import annotations

import copy

import pytest

from ui.model_manager_dialog import CatalogPickerDialog, ModelManagerDialog

FIXTURE = {
    "providers": [
        {
            "id": "pv_a",
            "name": "提供商 A",
            "type": "openai",
            "base_url": "https://api.a.com/v1",
            "api_key": "sk-a",
            "timeout": 60,
            "models": [
                {
                    "id": "md_a1",
                    "model": "gpt-4o",
                    "display_name": "GPT-4o",
                    "context_size": 128000,
                    "max_output_tokens": 16384,
                    "capabilities": ["chat", "vision"],
                    "supported_params": [
                        "temperature",
                        "top_p",
                        "max_tokens",
                    ],
                    "params": {"temperature": 0.4, "top_p": 0.9},
                }
            ],
        }
    ],
    "text_api_profiles": {
        "normal": {"provider_id": "pv_a", "model_id": "md_a1", "instructions": "写作"},
        "nsfw": {"provider_id": "pv_a", "model_id": "md_a1", "instructions": "NSFW"},
    },
}


@pytest.fixture
def dialog(qapp, monkeypatch):
    import ui.model_manager_dialog as module

    config = copy.deepcopy(FIXTURE)
    monkeypatch.setattr(module, "load_raw_config", lambda: copy.deepcopy(config))
    monkeypatch.setattr(module, "save_raw_config", lambda cfg, path=None: True)
    dlg = ModelManagerDialog(None)
    yield dlg
    dlg.close()


def test_loads_provider_and_model(dialog):
    assert dialog.provider_list.count() == 1
    assert dialog.model_table.rowCount() == 1
    assert dialog.model_key_input.currentText() == "gpt-4o"
    assert dialog.model_context_spin.value() == 128000
    assert dialog.model_max_output_spin.value() == 16384
    assert "视口" not in dialog.budget_label.text()
    assert "输入预算" in dialog.budget_label.text()


def test_supported_params_reflect_model(dialog):
    assert dialog.supported_checkboxes["temperature"].isChecked()
    assert dialog.supported_checkboxes["top_p"].isChecked()
    assert not dialog.supported_checkboxes["top_k"].isChecked()
    assert dialog._param_widgets["temperature"]["enabled"].isChecked()


def test_param_edit_updates_model(dialog):
    widgets = dialog._param_widgets["temperature"]
    widgets["widget"].setValue(1.1)
    dialog._store_model_form()
    model = dialog._current_model()
    assert model["params"]["temperature"] == pytest.approx(1.1)


def test_unchecking_param_removes_value(dialog):
    dialog.supported_checkboxes["top_p"].setChecked(False)
    dialog._store_model_form()
    model = dialog._current_model()
    assert "top_p" not in model["params"]
    assert "top_p" not in model["supported_params"]


def test_disable_param_checkbox_omits_value(dialog):
    dialog._param_widgets["temperature"]["enabled"].setChecked(False)
    dialog._store_model_form()
    assert "temperature" not in dialog._current_model()["params"]


def test_edit_limits_persist(dialog):
    dialog.model_context_spin.setValue(32000)
    dialog.model_max_output_spin.setValue(4096)
    dialog.model_caps_input.setText("chat, reasoning")
    dialog._store_model_form()
    model = dialog._current_model()
    assert model["context_size"] == 32000
    assert model["max_output_tokens"] == 4096
    assert model["capabilities"] == ["chat", "reasoning"]


def test_add_custom_model(dialog):
    dialog._add_custom_model()
    assert dialog.model_table.rowCount() == 2
    assert len(dialog.providers[0]["models"]) == 2


def test_add_models_from_catalog(dialog, monkeypatch):
    import ui.model_manager_dialog as module
    from PyQt6.QtWidgets import QDialog

    class FakePicker:
        def __init__(self, catalog, parent=None):
            self.catalog = catalog
            self.selected_models = ["google/gemini-2.5-pro"]

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "CatalogPickerDialog", FakePicker)
    dialog._add_models_from_catalog()
    assert dialog.model_table.rowCount() == 2
    added = dialog.providers[0]["models"][1]
    assert added["model"] == "google/gemini-2.5-pro"
    assert added["context_size"] == 1048576


def test_binding_change_updates_profile(dialog):
    dialog.profile_combo.setCurrentIndex(1)  # nsfw
    assert dialog.profiles["nsfw"]["provider_id"] == "pv_a"
    assert dialog.profiles["nsfw"]["model_id"] == "md_a1"


def test_catalog_picker_filters(qapp):
    from core.model_config import load_catalog

    picker = CatalogPickerDialog(load_catalog())
    total = picker.list_widget.count()
    assert total > 0
    picker.search_input.setText("gemini")
    assert picker.list_widget.count() < total
    assert picker.list_widget.count() > 0
    picker.close()


def test_fetch_models_from_provider(dialog, monkeypatch):
    import ui.model_manager_dialog as module
    from PyQt6.QtWidgets import QMessageBox

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "fresh-model"}, {"id": "gpt-4o"}]}

    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(module.requests, "get", fake_get)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    dialog._fetch_models_from_provider()
    models = [model["model"] for model in dialog.providers[0]["models"]]
    assert "fresh-model" in models
    assert models.count("gpt-4o") == 1  # 已存在不重复
    assert captured["url"].endswith("/models")


def test_budget_preview_scales_with_context(dialog):
    dialog.model_context_spin.setValue(8000)
    dialog.model_max_output_spin.setValue(1000)
    dialog._update_budget_preview()
    text = dialog.budget_label.text()
    assert "6,488" in text or "6488" in text
