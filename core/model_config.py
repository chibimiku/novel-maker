"""模型配置：提供商（provider）→ 具体模型（model）的二级配置。

结构说明（``conf/setting.json``）::

    "providers": [
        {
            "id": "pv_xxx",
            "name": "DeepSeek 官方",
            "type": "openai",              # openai / gemini
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-...",
            "timeout": 120,
            "models": [
                {
                    "id": "md_xxx",
                    "model": "deepseek-chat",   # 真实请求时使用的模型名
                    "display_name": "DeepSeek Chat",
                    "context_size": 131072,
                    "max_output_tokens": 8192,
                    "capabilities": ["chat"],
                    "supported_params": ["temperature", "top_p", ...],
                    "params": {"temperature": 0.7, "top_k": null, ...}
                }
            ]
        }
    ]

两级分类的好处：同一提供商下可以挂多个模型；每个模型单独维护上下文窗口、
输出上限、支持的采样参数与参数值。模型支持的参数并不统一（例如 OpenAI 的
推理模型不接受 temperature，Gemini 支持 top_k，多数 OpenAI 兼容网关不支持
top_k），因此每个模型都带一份 ``supported_params``，请求时只发送受支持的参数。

``data/model_catalog.json`` 是内置的“模型全集”，记录已知模型的上下文窗口、
输出上限、能力与参数支持情况；用户新增模型时可以从全集挑选默认值。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.token_budget import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_MAX_OUTPUT_TOKENS,
    TokenBudget,
)

logger = logging.getLogger(__name__)

API_TYPES = ("openai", "gemini")

# ---------------------------------------------------------------------------
# 参数定义
# ---------------------------------------------------------------------------

PARAM_DEFS: dict[str, dict[str, Any]] = {
    "temperature": {
        "label": "温度 (temperature)",
        "type": "float",
        "default": 0.7,
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "hint": "越高越发散。推理类模型通常不支持。",
    },
    "top_p": {
        "label": "核采样 (top_p)",
        "type": "float",
        "default": 1.0,
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "hint": "与 temperature 二选一调整，通常保持 1.0。",
    },
    "top_k": {
        "label": "Top-K (top_k)",
        "type": "int",
        "default": None,
        "min": 1,
        "max": 1000,
        "step": 1,
        "hint": "Gemini / Anthropic / 部分本地网关支持，OpenAI 官方接口不支持。",
    },
    "max_tokens": {
        "label": "最大输出 (max_tokens)",
        "type": "int",
        "default": None,
        "min": 128,
        "max": 200000,
        "step": 256,
        "hint": "留空表示自动（按模型上下文与输出上限推导）。",
    },
    "frequency_penalty": {
        "label": "频率惩罚 (frequency_penalty)",
        "type": "float",
        "default": None,
        "min": -2.0,
        "max": 2.0,
        "step": 0.05,
        "hint": "降低重复用词；默认不发送，需要时手动启用。",
    },
    "presence_penalty": {
        "label": "存在惩罚 (presence_penalty)",
        "type": "float",
        "default": None,
        "min": -2.0,
        "max": 2.0,
        "step": 0.05,
        "hint": "鼓励引入新话题；默认不发送，需要时手动启用。",
    },
    "repetition_penalty": {
        "label": "重复惩罚 (repetition_penalty)",
        "type": "float",
        "default": None,
        "min": 0.1,
        "max": 2.0,
        "step": 0.05,
        "hint": "部分 OpenAI 兼容网关（如 vLLM/Ollama）支持。",
    },
    "min_p": {
        "label": "最小概率 (min_p)",
        "type": "float",
        "default": None,
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "hint": "少数第三方网关支持。",
    },
    "seed": {
        "label": "随机种子 (seed)",
        "type": "int",
        "default": None,
        "min": 0,
        "max": 2**31 - 1,
        "step": 1,
        "hint": "固定采样结果，便于复现。",
    },
    "stop": {
        "label": "停止词 (stop)",
        "type": "list",
        "default": [],
        "hint": "每行一个停止词，最多 4 个。",
    },
}

PARAM_ORDER: tuple[str, ...] = tuple(PARAM_DEFS.keys())

#: OpenAI 官方（及大多数兼容网关）默认支持的参数
_OPENAI_DEFAULT_PARAMS = [
    "temperature",
    "top_p",
    "max_tokens",
    "frequency_penalty",
    "presence_penalty",
    "stop",
]
_OPENAI_FULL_PARAMS = _OPENAI_DEFAULT_PARAMS + ["seed"]
_GEMINI_PARAMS = ["temperature", "top_p", "top_k", "max_tokens", "stop", "seed"]

#: 推理类模型：不接受 temperature/top_p，且官方接口要求 max_completion_tokens
_REASONING_MARKERS = ("o1", "o3", "o4", "gpt-5", "reasoning", "thinking", "qwq")

#: 已知模型的输出上限覆盖表（按模型名子串匹配，先匹配先生效）
_MAX_OUTPUT_OVERRIDES: tuple[tuple[str, int], ...] = (
    ("claude-sonnet-4", 64000),
    ("claude-opus-4", 32000),
    ("claude-3-7", 64000),
    ("claude-3.5", 8192),
    ("claude-3", 4096),
    ("gpt-4.1", 32768),
    ("gpt-4o", 16384),
    ("gpt-4-turbo", 4096),
    ("gpt-4", 8192),
    ("gpt-5", 32768),
    ("deepseek", 8192),
    ("gemini-2.5", 32768),
    ("gemini-2.0", 8192),
    ("gemini", 8192),
    ("qwen", 8192),
    ("grok", 8192),
    ("glm", 8192),
    ("kimi", 8192),
    ("minimax", 8192),
    ("llama-3.3", 8192),
    ("llama-3.1", 8192),
    ("llama-3", 8192),
)


def default_max_output_for(context_size: int, model_key: str = "") -> int:
    """根据上下文窗口与模型名推导一个保守的最大输出 token。"""
    lowered = (model_key or "").lower()
    for marker, value in _MAX_OUTPUT_OVERRIDES:
        if marker in lowered:
            return max(256, min(value, max(256, context_size // 2)))
    return max(256, min(DEFAULT_MAX_OUTPUT_TOKENS, max(256, context_size // 4)))


# ---------------------------------------------------------------------------
# 参数归一化
# ---------------------------------------------------------------------------

def _coerce_param(name: str, value):
    """把任意来源的参数值转换成参数定义要求的类型；无法转换时返回 None。"""
    definition = PARAM_DEFS.get(name)
    if definition is None:
        return value
    kind = definition["type"]
    if value is None or value == "":
        return None
    try:
        if kind == "float":
            return float(value)
        if kind == "int":
            return int(value)
        if kind == "list":
            if isinstance(value, str):
                return [line.strip() for line in value.splitlines() if line.strip()]
            if isinstance(value, (list, tuple)):
                return [str(item) for item in value if str(item).strip()]
            return []
    except (TypeError, ValueError):
        return None
    return value


def clamp_param(name: str, value):
    """把参数值夹到合法区间。"""
    definition = PARAM_DEFS.get(name)
    if definition is None or value is None:
        return value
    if definition["type"] in ("float", "int") and "min" in definition:
        low, high = definition["min"], definition["max"]
        if value < low:
            return low
        if value > high:
            return high
    return value


def normalize_params(params: dict | None, supported: Iterable[str] | None = None) -> dict:
    """只保留合法且受支持的参数，并按定义类型/区间归一化。

    规则：

    * 参数名必须在 :data:`PARAM_DEFS` 里；
    * 传入了 ``supported`` 时，不在其中的参数会被丢弃（是否发送以模型的
      ``supported_params`` 为准，UI 里可以勾选让模型“支持”某个参数）；
    * 值为 ``None``/空字符串/空列表一律剔除，避免生成非法请求体；
    * 显式写 ``null`` 表示“不发送该参数”，因此不会被默认值重新填回。
    """
    supported_set = set(supported or [])
    normalized: dict[str, Any] = {}
    raw = params if isinstance(params, dict) else {}
    explicit: set[str] = set()
    for name, value in raw.items():
        if name not in PARAM_DEFS:
            continue
        explicit.add(name)
        if supported_set and name not in supported_set:
            continue
        coerced = _coerce_param(name, value)
        if coerced is None:
            continue
        if isinstance(coerced, list) and not coerced:
            continue
        normalized[name] = clamp_param(name, coerced)
    # 未出现的受支持参数给出默认值（显式 null 的除外）
    for name in supported_set:
        if name in explicit or name in normalized or name not in PARAM_DEFS:
            continue
        default = PARAM_DEFS[name]["default"]
        if default is None:
            continue
        if isinstance(default, list) and not default:
            continue
        normalized[name] = default
    return {name: normalized[name] for name in PARAM_ORDER if name in normalized}


def infer_supported_params(
    model_key: str,
    api_type: str = "openai",
    capabilities: Iterable[str] | None = None,
) -> list[str]:
    """根据接口类型与模型名推断“这个模型支持哪些采样参数”。

    推断只用于给出默认勾选，用户可以在模型配置里任意增删。
    """
    api_type = (api_type or "openai").lower()
    lowered = (model_key or "").lower()
    caps = {str(item).lower() for item in (capabilities or [])}

    if api_type == "gemini":
        params = list(_GEMINI_PARAMS)
    else:
        params = list(_OPENAI_FULL_PARAMS)

    is_reasoning = any(marker in lowered for marker in _REASONING_MARKERS)
    if is_reasoning:
        params = [p for p in params if p not in ("temperature", "top_p")]

    # 明确的“思考/推理”能力也按推理模型处理
    if "reasoning" in caps and "temperature" in params:
        params = [p for p in params if p not in ("temperature", "top_p")]

    # 允许 top_k 的家族
    if api_type == "openai" and (
        "claude" in lowered
        or "anthropic" in lowered
        or "top_k" in caps
        or "top-k" in caps
    ):
        params.append("top_k")

    # Anthropic 原生接口不支持 frequency/presence penalty（经 OpenRouter 转发同样会报错）
    if "claude" in lowered or "anthropic" in lowered:
        params = [
            p for p in params if p not in ("frequency_penalty", "presence_penalty")
        ]

    if any(marker in lowered for marker in ("vllm", "ollama", "lmstudio", "sglang")):
        for extra in ("top_k", "repetition_penalty", "min_p"):
            if extra not in params:
                params.append(extra)

    return [p for p in PARAM_ORDER if p in params]


def is_reasoning_model(model_key: str) -> bool:
    lowered = (model_key or "").lower()
    return any(marker in lowered for marker in _REASONING_MARKERS)


# ---------------------------------------------------------------------------
# 模型全集（catalog）
# ---------------------------------------------------------------------------

_DEFAULT_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "model_catalog.json"
)


def catalog_path() -> str:
    return _DEFAULT_CATALOG_PATH


def load_catalog(path: str | None = None) -> dict:
    """加载模型全集；文件不存在或损坏时返回空目录结构。"""
    target = path or _DEFAULT_CATALOG_PATH
    empty = {"version": 1, "models": {}}
    if not os.path.exists(target):
        return empty
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:  # pragma: no cover - 损坏文件
        logger.warning("加载模型全集失败 %s: %s", target, exc)
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        return empty
    data.setdefault("version", 1)
    return data


def lookup_catalog_entry(catalog: dict | None, model_key: str) -> dict | None:
    """在模型全集里查找模型元数据（精确 → 大小写 → 后缀匹配）。"""
    if not catalog or not model_key:
        return None
    models = catalog.get("models", {})
    if model_key in models:
        return models[model_key]
    lowered = model_key.lower()
    for key, value in models.items():
        if key.lower() == lowered:
            return value
    # "deepseek-chat" 与 "deepseek/deepseek-chat" 视为同一模型
    if "/" not in model_key:
        suffix = "/" + lowered
        for key, value in models.items():
            if key.lower().endswith(suffix):
                return value
    else:
        tail = lowered.split("/")[-1]
        for key, value in models.items():
            if key.lower() == tail:
                return value
    return None


def catalog_choices(catalog: dict | None, keyword: str = "") -> list[dict]:
    """返回可选模型列表（供 UI 下拉/搜索），按模型名排序。"""
    if not catalog:
        return []
    keyword = (keyword or "").strip().lower()
    result = []
    for key, value in catalog.get("models", {}).items():
        if keyword and keyword not in key.lower():
            continue
        entry = value if isinstance(value, dict) else {}
        result.append(
            {
                "model": key,
                "context_size": int(entry.get("context", DEFAULT_CONTEXT_SIZE) or DEFAULT_CONTEXT_SIZE),
                "max_output_tokens": int(entry.get("max_out", 0) or 0)
                or default_max_output_for(
                    int(entry.get("context", DEFAULT_CONTEXT_SIZE) or DEFAULT_CONTEXT_SIZE), key
                ),
                "capabilities": list(entry.get("caps", []) or []),
                "supported_params": list(entry.get("params", []) or []),
            }
        )
    result.sort(key=lambda item: item["model"].lower())
    return result


# ---------------------------------------------------------------------------
# 模型 / 提供商归一化
# ---------------------------------------------------------------------------

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _normalize_capabilities(raw) -> list[str]:
    if isinstance(raw, str):
        items = [part.strip() for part in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple, set)):
        items = [str(item).strip() for item in raw]
    else:
        items = []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def normalize_model(
    raw: dict | None,
    catalog: dict | None = None,
    *,
    api_type: str = "openai",
) -> dict:
    """归一化单个模型配置；缺失字段从模型全集补齐。"""
    model_cfg = dict(raw) if isinstance(raw, dict) else {}
    model_key = str(model_cfg.get("model") or "").strip()
    if not model_key:
        model_key = str(model_cfg.get("id") or "").strip()
    entry = lookup_catalog_entry(catalog, model_key) or {}

    capabilities = _normalize_capabilities(
        model_cfg.get("capabilities") or entry.get("caps") or []
    )

    raw_context = model_cfg.get("context_size", entry.get("context", DEFAULT_CONTEXT_SIZE))
    try:
        context_size = int(raw_context)
    except (TypeError, ValueError):
        context_size = DEFAULT_CONTEXT_SIZE
    if context_size <= 0:
        context_size = DEFAULT_CONTEXT_SIZE

    raw_max_out = model_cfg.get("max_output_tokens", entry.get("max_out"))
    if raw_max_out is None:
        max_output_tokens = default_max_output_for(context_size, model_key)
    else:
        try:
            max_output_tokens = int(raw_max_out)
        except (TypeError, ValueError):
            max_output_tokens = default_max_output_for(context_size, model_key)
        if max_output_tokens <= 0:
            max_output_tokens = default_max_output_for(context_size, model_key)
    max_output_tokens = min(max_output_tokens, max(256, context_size // 2))

    raw_supported = model_cfg.get("supported_params") or entry.get("params")
    if raw_supported:
        supported = [
            name
            for name in PARAM_ORDER
            if name in {str(item).strip() for item in raw_supported}
        ]
    else:
        supported = infer_supported_params(model_key, api_type, capabilities)

    params = normalize_params(model_cfg.get("params"), supported)

    display_name = str(model_cfg.get("display_name") or "").strip() or model_key
    return {
        "id": str(model_cfg.get("id") or "").strip() or new_id("md"),
        "model": model_key,
        "display_name": display_name,
        "context_size": context_size,
        "max_output_tokens": max_output_tokens,
        "capabilities": capabilities,
        "supported_params": supported,
        "params": params,
    }


def default_base_url(api_type: str) -> str:
    return "https://generativelanguage.googleapis.com" if api_type == "gemini" else "https://api.openai.com/v1"


def normalize_provider(
    raw: dict | None,
    catalog: dict | None = None,
) -> dict:
    """归一化单个提供商配置。"""
    provider = dict(raw) if isinstance(raw, dict) else {}
    api_type = str(provider.get("type") or "openai").strip().lower()
    if api_type not in API_TYPES:
        api_type = "openai"

    raw_models = provider.get("models")
    models_raw = raw_models if isinstance(raw_models, list) else []
    models = [normalize_model(item, catalog, api_type=api_type) for item in models_raw]
    # 去掉重复的模型名（保留先出现的）
    deduped: list[dict] = []
    seen_models: set[str] = set()
    for model in models:
        key = model["model"]
        if key and key in seen_models:
            continue
        seen_models.add(key)
        deduped.append(model)

    try:
        timeout = int(provider.get("timeout", 120))
    except (TypeError, ValueError):
        timeout = 120
    timeout = max(5, min(3600, timeout))

    return {
        "id": str(provider.get("id") or "").strip() or new_id("pv"),
        "name": str(provider.get("name") or "").strip() or "未命名提供商",
        "type": api_type,
        "base_url": str(provider.get("base_url") or "").strip() or default_base_url(api_type),
        "api_key": str(provider.get("api_key") or "").strip(),
        "timeout": timeout,
        "models": deduped,
    }


def _find_provider_for_legacy(
    providers: list[dict], api_type: str, base_url: str, api_key: str
) -> dict | None:
    for provider in providers:
        if (
            provider.get("type") == api_type
            and (provider.get("base_url") or "").rstrip("/") == (base_url or "").rstrip("/")
            and provider.get("api_key") == api_key
        ):
            return provider
    return None


def migrate_legacy_config(cfg: dict, catalog: dict | None = None) -> dict:
    """把旧版“单一 text_api”配置迁移成 providers + 画像引用。

    不会修改传入的 ``cfg``，返回一份新的配置字典。若配置里完全没有配置过任何
    连接信息（全新安装），则返回空的 providers，由 UI 引导用户新建。
    """
    migrated = copy.deepcopy(cfg) if isinstance(cfg, dict) else {}
    raw_profiles = migrated.get("text_api_profiles")
    if not isinstance(raw_profiles, dict):
        raw_profiles = {}
    legacy_text = migrated.get("text_api") if isinstance(migrated.get("text_api"), dict) else {}

    def _has_connection_info(profile: Any) -> bool:
        if not isinstance(profile, dict):
            return False
        return any(
            str(profile.get(key) or "").strip()
            for key in ("base_url", "api_key", "model", "type")
        )

    profile_candidates = [value for value in raw_profiles.values()] + [legacy_text]
    if not any(_has_connection_info(profile) for profile in profile_candidates):
        migrated["providers"] = []
        migrated["text_api_profiles"] = _bind_profiles(raw_profiles, [])
        return migrated

    providers: list[dict] = []
    for key in ("normal", "nsfw"):
        profile = raw_profiles.get(key)
        if not isinstance(profile, dict) or not profile:
            profile = legacy_text if key == "normal" else copy.deepcopy(raw_profiles.get("normal") or legacy_text)
        if not isinstance(profile, dict):
            profile = {}

        api_type = str(profile.get("type") or "openai").strip().lower()
        if api_type not in API_TYPES:
            api_type = "openai"
        base_url = str(profile.get("base_url") or "").strip() or default_base_url(api_type)
        api_key = str(profile.get("api_key") or "").strip()
        try:
            timeout = int(profile.get("timeout", 120))
        except (TypeError, ValueError):
            timeout = 120

        provider = _find_provider_for_legacy(providers, api_type, base_url, api_key)
        if provider is None:
            provider = normalize_provider(
                {
                    "name": _provider_name_from_url(base_url, api_type),
                    "type": api_type,
                    "base_url": base_url,
                    "api_key": api_key,
                    "timeout": timeout,
                    "models": [],
                },
                catalog,
            )
            providers.append(provider)

        model_key = str(profile.get("model") or "").strip() or "gpt-4o"
        existing = next((m for m in provider["models"] if m["model"] == model_key), None)
        if existing is None:
            entry = lookup_catalog_entry(catalog, model_key) or {}
            context_size = profile.get("model_context_size", entry.get("context", DEFAULT_CONTEXT_SIZE))
            existing = normalize_model(
                {
                    "model": model_key,
                    "capabilities": profile.get("model_capabilities", entry.get("caps", [])),
                    "context_size": context_size,
                    "max_output_tokens": profile.get("max_tokens"),
                    "params": profile.get("model_params") or profile.get("params"),
                },
                catalog,
                api_type=api_type,
            )
            provider["models"].append(existing)
        profile["provider_id"] = provider["id"]
        profile["model_id"] = existing["id"]
        raw_profiles[key] = profile

    migrated["providers"] = providers
    migrated["text_api_profiles"] = raw_profiles
    return migrated


def _provider_name_from_url(base_url: str, api_type: str) -> str:
    if api_type == "gemini":
        return "Google Gemini"
    host = (base_url or "").split("//")[-1].split("/")[0]
    if not host:
        return "OpenAI 兼容接口"
    return host


# ---------------------------------------------------------------------------
# 运行期模型
# ---------------------------------------------------------------------------

@dataclass
class RuntimeModel:
    """一次请求实际使用的模型运行时配置。"""

    profile_key: str
    provider_id: str
    provider_name: str
    api_type: str
    base_url: str
    api_key: str
    timeout: int
    model_id: str
    model_key: str
    display_name: str
    context_size: int
    max_output_tokens: int
    capabilities: list[str] = field(default_factory=list)
    supported_params: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    # -- 展示 ---------------------------------------------------------
    def label(self) -> str:
        return f"{self.display_name} @ {self.provider_name}"

    def endpoint_desc(self) -> str:
        if self.api_type == "gemini":
            return f"type=gemini, model={self.model_key}, endpoint=google-genai-sdk"
        return f"type={self.api_type}, model={self.model_key}, base_url={self.base_url}"

    def token_budget(self, protocol_reserve: int | None = None) -> TokenBudget:
        requested_output = self.params.get("max_tokens")
        budget = TokenBudget(
            context_size=self.context_size,
            max_output_tokens=self.max_output_tokens,
            protocol_reserve=protocol_reserve if protocol_reserve is not None else 512,
        )
        if requested_output:
            budget.max_output_tokens = budget.clamp_output(requested_output)
        return budget

    # -- 请求参数 -----------------------------------------------------
    def request_params(self) -> dict:
        """转换成 OpenAI 兼容接口的请求参数（只包含受支持且已配置的参数）。"""
        supported = set(self.supported_params)
        params = self.params or {}
        result: dict[str, Any] = {}

        if "temperature" in supported and params.get("temperature") is not None:
            result["temperature"] = float(params["temperature"])
        if "top_p" in supported and params.get("top_p") is not None:
            result["top_p"] = float(params["top_p"])
        if "top_k" in supported and params.get("top_k") is not None:
            result["top_k"] = int(params["top_k"])
        if "frequency_penalty" in supported and params.get("frequency_penalty") is not None:
            result["frequency_penalty"] = float(params["frequency_penalty"])
        if "presence_penalty" in supported and params.get("presence_penalty") is not None:
            result["presence_penalty"] = float(params["presence_penalty"])
        if "repetition_penalty" in supported and params.get("repetition_penalty") is not None:
            result["repetition_penalty"] = float(params["repetition_penalty"])
        if "min_p" in supported and params.get("min_p") is not None:
            result["min_p"] = float(params["min_p"])
        if "seed" in supported and params.get("seed") is not None:
            result["seed"] = int(params["seed"])
        if "stop" in supported and params.get("stop"):
            result["stop"] = list(params["stop"])[:4]
        if "max_tokens" in supported and params.get("max_tokens"):
            result["max_tokens"] = int(params["max_tokens"])
        return result

    def max_tokens_field(self) -> str:
        """推理模型在 OpenAI 官方接口要求 max_completion_tokens。"""
        return "max_completion_tokens" if is_reasoning_model(self.model_key) else "max_tokens"

    def gemini_generation_config(self) -> dict:
        supported = set(self.supported_params)
        params = self.params or {}
        config: dict[str, Any] = {}
        if "temperature" in supported and params.get("temperature") is not None:
            config["temperature"] = float(params["temperature"])
        if "top_p" in supported and params.get("top_p") is not None:
            config["top_p"] = float(params["top_p"])
        if "top_k" in supported and params.get("top_k") is not None:
            config["top_k"] = int(params["top_k"])
        if "max_tokens" in supported and params.get("max_tokens"):
            config["max_output_tokens"] = int(params["max_tokens"])
        if "stop" in supported and params.get("stop"):
            config["stop_sequences"] = list(params["stop"])[:4]
        if "seed" in supported and params.get("seed") is not None:
            config["seed"] = int(params["seed"])
        return config

    def params_desc(self) -> str:
        if not self.params:
            return "(默认)"
        parts = []
        for name in PARAM_ORDER:
            if name not in self.params:
                continue
            value = self.params[name]
            parts.append(f"{name}={value}")
        return ", ".join(parts) or "(默认)"


# ---------------------------------------------------------------------------
# 配置归一化 / 运行时解析
# ---------------------------------------------------------------------------

def ensure_providers(cfg: dict, catalog: dict | None = None) -> dict:
    """确保配置里有 providers；有旧字段时自动迁移。

    返回新的配置字典（不修改入参）。
    """
    cfg = dict(cfg) if isinstance(cfg, dict) else {}
    raw_providers = cfg.get("providers")
    if isinstance(raw_providers, list):
        cfg["providers"] = [normalize_provider(item, catalog) for item in raw_providers]
        # 保证每个画像都指向存在且唯一的模型
        cfg["text_api_profiles"] = _bind_profiles(
            cfg.get("text_api_profiles", {}), cfg["providers"]
        )
        return cfg
    return migrate_legacy_config(cfg, catalog)


def _bind_profiles(raw_profiles: Any, providers: list[dict]) -> dict:
    profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
    provider_ids = {provider["id"] for provider in providers}
    model_ids = {
        model["id"]: provider["id"]
        for provider in providers
        for model in provider["models"]
    }

    default_provider = next(
        (provider for provider in providers if provider["models"]), None
    )
    default_model_id = (
        default_provider["models"][0]["id"] if default_provider is not None else ""
    )

    result: dict[str, dict] = {}
    for key in ("normal", "nsfw"):
        profile = dict(profiles.get(key) or {})
        provider_id = str(profile.get("provider_id") or "").strip()
        model_id = str(profile.get("model_id") or "").strip()
        if provider_id not in provider_ids or model_id not in model_ids:
            provider_id = default_provider["id"] if default_provider else ""
            model_id = default_model_id
        profile["provider_id"] = provider_id
        profile["model_id"] = model_id
        result[key] = profile
    return result


def resolve_runtime(config: dict, profile_key: str = "normal") -> RuntimeModel | None:
    """根据配置解析出当前画像对应的运行期模型。"""
    if not isinstance(config, dict):
        return None
    providers = config.get("providers")
    if not isinstance(providers, list) or not providers:
        return None

    profiles = config.get("text_api_profiles")
    profile = profiles.get(profile_key) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        profile = {}

    provider = next(
        (item for item in providers if item.get("id") == profile.get("provider_id")),
        None,
    )
    if provider is None:
        provider = next((item for item in providers if item.get("models")), providers[0])
    models = provider.get("models") or []
    if not models:
        return None

    model = next(
        (item for item in models if item.get("id") == profile.get("model_id")),
        models[0],
    )

    return RuntimeModel(
        profile_key=profile_key,
        provider_id=provider.get("id", ""),
        provider_name=provider.get("name", ""),
        api_type=provider.get("type", "openai"),
        base_url=provider.get("base_url", ""),
        api_key=provider.get("api_key", ""),
        timeout=int(provider.get("timeout", 120) or 120),
        model_id=model.get("id", ""),
        model_key=model.get("model", ""),
        display_name=model.get("display_name", model.get("model", "")),
        context_size=int(model.get("context_size", DEFAULT_CONTEXT_SIZE) or DEFAULT_CONTEXT_SIZE),
        max_output_tokens=int(
            model.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS) or DEFAULT_MAX_OUTPUT_TOKENS
        ),
        capabilities=list(model.get("capabilities", []) or []),
        supported_params=list(model.get("supported_params", []) or []),
        params=dict(model.get("params", {}) or {}),
    )


def active_profile_key(config: dict, prefer_nsfw: bool = False) -> str:
    """返回当前生效的画像 key。"""
    if prefer_nsfw:
        return "nsfw"
    key = str((config or {}).get("text_api_active_profile", "normal")).lower()
    return key if key in ("normal", "nsfw") else "normal"


def validate_config(config: dict) -> list[str]:
    """校验提供商/模型配置，返回人类可读的问题列表。"""
    issues: list[str] = []
    providers = (config or {}).get("providers")
    if not isinstance(providers, list) or not providers:
        issues.append("尚未配置任何模型提供商。")
        return issues
    for provider in providers:
        name = provider.get("name") or provider.get("id") or "未命名提供商"
        if not provider.get("api_key"):
            issues.append(f"提供商【{name}】缺少 API Key。")
        models = provider.get("models") or []
        if not models:
            issues.append(f"提供商【{name}】下还没有配置任何模型。")
        for model in models:
            if not model.get("model"):
                issues.append(f"提供商【{name}】存在缺少模型名的条目。")
            if int(model.get("context_size", 0) or 0) < 1024:
                issues.append(
                    f"模型【{model.get('model', '')}】的上下文窗口过小（<1024）。"
                )
            if int(model.get("max_output_tokens", 0) or 0) > int(
                model.get("context_size", 0) or 0
            ):
                issues.append(
                    f"模型【{model.get('model', '')}】的最大输出超过上下文窗口。"
                )
    return issues


def provider_models(provider: dict) -> list[dict]:
    return list(provider.get("models", []) or [])


def find_model(provider: dict, model_id: str) -> dict | None:
    for model in provider_models(provider):
        if model.get("id") == model_id:
            return model
    return None
