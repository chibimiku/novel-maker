"""系统配置窗口测试：保存时保留 providers，旧配置自动物化二级结构。"""
from __future__ import annotations

import copy

import pytest

from PyQt6.QtWidgets import QMessageBox


LEGACY_CONFIG = {
    "proxy": {"enabled": False, "url": "http://127.0.0.1:7897"},
    "text_api_profiles": {
        "normal": {
            "type": "openai",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-legacy",
            "model": "deepseek-chat",
            "model_context_size": 163840,
            "model_capabilities": ["chat"],
            "instructions": "旧版写作指令",
            "summary_instructions": "旧版概要指令",
            "modify_instructions": "旧版修改指令",
            "auto_continue_on_incomplete": True,
            "auto_continue_max_rounds": 3,
        },
        "nsfw": {
            "type": "openai",
            "base_url": "https://openrouter.ai/api/v1/",
            "api_key": "sk-nsfw",
            "model": "x-ai/grok-4.1-fast",
            "model_context_size": 2000000,
            "instructions": "NSFW 指令",
        },
    },
    "image_api": {"type": "openai", "base_url": "https://api.openai.com/v1", "api_key": "", "model": "dall-e-3"},
    "task_completion_notification_enabled": True,
    "recent_api_configs": [],
}

PROVIDER_CONFIG = {
    "proxy": {"enabled": False, "url": "http://127.0.0.1:7897"},
    "providers": [
        {
            "id": "pv_keep",
            "name": "保留提供商",
            "type": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-keep",
            "timeout": 60,
            "models": [
                {
                    "id": "md_keep",
                    "model": "keep-model",
                    "display_name": "Keep",
                    "context_size": 64000,
                    "max_output_tokens": 4096,
                    "capabilities": ["chat"],
                    "supported_params": ["temperature", "max_tokens"],
                    "params": {"temperature": 0.5},
                }
            ],
        }
    ],
    "text_api_profiles": {
        "normal": {"provider_id": "pv_keep", "model_id": "md_keep", "instructions": "写作"},
        "nsfw": {"provider_id": "pv_keep", "model_id": "md_keep", "instructions": "NSFW"},
    },
    "image_api": {"type": "openai", "base_url": "", "api_key": "", "model": "dall-e-3"},
    "task_completion_notification_enabled": True,
    "recent_api_configs": [],
}


@pytest.fixture
def patched_settings(qapp, monkeypatch):
    import ui.settings_dialog as module

    saved: dict = {"config": None}
    active = {"config": copy.deepcopy(LEGACY_CONFIG)}

    monkeypatch.setattr(
        module, "load_raw_config", lambda path=None: copy.deepcopy(active["config"])
    )
    monkeypatch.setattr(
        module,
        "save_raw_config",
        lambda cfg, path=None: saved.__setitem__("config", copy.deepcopy(cfg)) or True,
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    return module, active, saved


def test_legacy_config_is_materialized_on_save(patched_settings):
    module, active, saved = patched_settings
    dialog = module.SettingsDialog(None)
    try:
        assert "deepseek-chat" in dialog.model_service_label.text()
        dialog.save_config()
    finally:
        dialog.close()

    written = saved["config"]
    assert written is not None
    assert len(written["providers"]) == 2
    normal = written["text_api_profiles"]["normal"]
    assert normal["provider_id"]
    assert normal["model_id"]
    provider = next(p for p in written["providers"] if p["id"] == normal["provider_id"])
    assert provider["api_key"] == "sk-legacy"
    model = next(m for m in provider["models"] if m["id"] == normal["model_id"])
    assert model["model"] == "deepseek-chat"
    assert model["context_size"] == 163840
    assert written["text_api_profiles"]["normal"]["instructions"] == "旧版写作指令"
    # 模型目录不再重复写入画像
    assert normal["model_meta_catalog"] == {}


def test_existing_providers_are_preserved(patched_settings):
    module, active, saved = patched_settings
    active["config"] = copy.deepcopy(PROVIDER_CONFIG)
    dialog = module.SettingsDialog(None)
    try:
        assert "Keep" in dialog.model_service_label.text()
        dialog.save_config()
    finally:
        dialog.close()

    written = saved["config"]
    assert [p["id"] for p in written["providers"]] == ["pv_keep"]
    assert written["providers"][0]["models"][0]["model"] == "keep-model"
    assert written["providers"][0]["models"][0]["params"]["temperature"] == 0.5


def test_profile_switch_keeps_bindings(patched_settings):
    module, active, saved = patched_settings
    active["config"] = copy.deepcopy(PROVIDER_CONFIG)
    dialog = module.SettingsDialog(None)
    try:
        dialog.text_profile_combo.setCurrentIndex(1)  # nsfw
        dialog.save_config()
    finally:
        dialog.close()
    written = saved["config"]
    assert written["text_api_profiles"]["normal"]["model_id"] == "md_keep"
    assert written["text_api_profiles"]["nsfw"]["model_id"] == "md_keep"
