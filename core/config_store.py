"""配置文件读写与迁移。

集中管理 ``conf/setting.json`` 的加载、归一化与保存，避免 UI 各处各写一份
“读文件 → 拼字典 → 覆盖写回”的逻辑（旧实现会在保存时丢掉未知字段，
例如 ``providers`` / ``recent_api_configs``）。
"""
from __future__ import annotations

import copy
import json
import logging
import os

from core.model_config import (
    ensure_providers,
    load_catalog,
    resolve_runtime,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_DIR = os.path.join(PROJECT_ROOT, "conf")
CONFIG_PATH = os.path.join(CONF_DIR, "setting.json")
SYS_STATE_PATH = os.path.join(CONF_DIR, "sys_state.json")

#: 允许出现在 setting.json 里的顶层字段（其它字段保存时原样保留）
DEFAULT_CONFIG: dict = {
    "proxy": {"enabled": False, "url": "http://127.0.0.1:7897"},
    "providers": [],
    "text_api_profiles": {"normal": {}, "nsfw": {}},
    "text_api_active_profile": "normal",
    "text_generation_mode": "normal",
    "text_api": {},
    "image_api": {
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "dall-e-3",
    },
    "task_completion_notification_enabled": True,
    "recent_api_configs": [],
}


def config_path(path: str | None = None) -> str:
    return path or CONFIG_PATH


def load_raw_config(path: str | None = None) -> dict:
    """读取原始配置；不存在或损坏时返回默认配置的深拷贝。"""
    target = config_path(path)
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
            logger.warning("配置文件根节点不是对象，已忽略: %s", target)
        except Exception as exc:
            logger.error("读取配置文件失败 %s: %s", target, exc)
    return copy.deepcopy(DEFAULT_CONFIG)


def save_raw_config(config: dict, path: str | None = None) -> bool:
    """原子写入配置文件。"""
    target = config_path(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = f"{target}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=4)
            handle.write("\n")
        os.replace(tmp, target)
        return True
    except Exception as exc:
        logger.error("保存配置文件失败 %s: %s", target, exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def deep_merge(base: dict, updates: dict) -> dict:
    """递归合并字典，返回新字典；``updates`` 中的 None 也会覆盖。"""
    result = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in (updates or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def build_runtime_config(
    raw_config: dict,
    *,
    prefer_nsfw: bool = False,
    catalog: dict | None = None,
) -> dict:
    """把原始配置归一化为运行期配置。

    步骤：

    1. 补齐/迁移 ``providers``（旧配置自动升级为 provider → model 二级结构）；
    2. 解析当前生效画像对应的运行期模型；
    3. 生成一份向后兼容的 ``text_api`` 视图（模型字段来自运行期模型）。
    """
    catalog = catalog if catalog is not None else load_catalog()
    cfg = ensure_providers(copy.deepcopy(raw_config), catalog)

    profiles = cfg.get("text_api_profiles")
    if not isinstance(profiles, dict) or not profiles:
        profiles = {"normal": {}, "nsfw": {}}
        cfg["text_api_profiles"] = profiles

    active = "nsfw" if prefer_nsfw else str(
        cfg.get("text_api_active_profile", "normal")
    ).lower()
    if active not in ("normal", "nsfw"):
        active = "normal"
    cfg["text_api_active_profile"] = active
    cfg["text_generation_mode"] = active

    runtime = resolve_runtime(cfg, active)
    derived = copy.deepcopy(profiles.get(active) or {})
    derived["text_generation_mode"] = active
    derived.setdefault("model_context_size", 8192)
    derived.setdefault("model_max_output_tokens", 2048)
    derived.setdefault("model_capabilities", [])
    derived.setdefault("model_params", {})
    if runtime is not None:
        derived.update(
            {
                "type": runtime.api_type,
                "base_url": runtime.base_url,
                "api_key": runtime.api_key,
                "model": runtime.model_key,
                "model_context_size": runtime.context_size,
                "model_max_output_tokens": runtime.max_output_tokens,
                "model_capabilities": list(runtime.capabilities),
                "model_params": dict(runtime.params),
                "provider_id": runtime.provider_id,
                "model_id": runtime.model_id,
                "provider_name": runtime.provider_name,
            }
        )
    cfg["text_api"] = derived
    return cfg
