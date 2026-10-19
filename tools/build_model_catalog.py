"""从本地 setting.json 的历史模型目录生成内置模型全集 ``data/model_catalog.json``。

用法::

    python tools/build_model_catalog.py [--source conf/setting.json] [--output data/model_catalog.json]

生成规则：

* 上下文窗口取自历史配置里的 ``model_meta_catalog`` / ``model_context_size``；
* 最大输出 token 按模型族推导（不足则回落为窗口的 1/4）；
* 能力（chat/vision/reasoning/free/audio/image）按模型名启发式标注；
* 支持的采样参数由 ``core.model_config.infer_supported_params`` 推导。

该脚本只依赖标准库与项目内模块，可在无网络环境运行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.model_config import (  # noqa: E402
    infer_supported_params,
    is_reasoning_model,
    default_max_output_for,
)


VISION_MARKERS = (
    "vision",
    "-vl",
    "/vl-",
    "vl-",
    "omni",
    "pixtral",
    "llava",
    "internvl",
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "gemini-2",
    "gemini-3",
    "claude-3",
    "claude-opus",
    "claude-sonnet",
    "qwen2.5-vl",
    "qwen3-vl",
    "mimo-v2-omni",
    "seed",
    "ui-tars",
    "nemotron-nano-12b-v2-vl",
)

IMAGE_GEN_MARKERS = ("dall-e", "gpt-image", "flux", "stable-diffusion", "image-preview", "flash-image")
AUDIO_MARKERS = ("audio", "whisper", "tts", "lyria", "music")


def infer_capabilities(model_key: str) -> list[str]:
    lowered = model_key.lower()
    caps = ["chat"]
    if any(marker in lowered for marker in VISION_MARKERS):
        caps.append("vision")
    if is_reasoning_model(model_key):
        caps.append("reasoning")
    if ":free" in lowered:
        caps.append("free")
    if any(marker in lowered for marker in AUDIO_MARKERS):
        caps.append("audio")
    if any(marker in lowered for marker in IMAGE_GEN_MARKERS):
        caps.append("image_generation")
    if "embed" in lowered:
        caps = ["embedding"]
    if "guard" in lowered:
        caps.append("moderation")
    return caps


def collect_models(cfg: dict) -> dict[str, int]:
    """从配置中收集 模型名 -> 上下文窗口。"""
    collected: dict[str, int] = {}

    def _merge(catalog: dict, fallback_context: int) -> None:
        for name, meta in (catalog or {}).items():
            key = str(name).strip()
            if not key:
                continue
            context = fallback_context
            if isinstance(meta, dict):
                try:
                    context = int(meta.get("context_size", fallback_context))
                except (TypeError, ValueError):
                    context = fallback_context
            if context <= 0:
                context = fallback_context
            # 同一个模型出现多次时取较大窗口，避免低估
            collected[key] = max(collected.get(key, 0), context)

    profiles = cfg.get("text_api_profiles")
    if isinstance(profiles, dict):
        for profile in profiles.values():
            if not isinstance(profile, dict):
                continue
            fallback = int(profile.get("model_context_size", 8192) or 8192)
            _merge(profile.get("model_meta_catalog", {}), fallback)
            model_name = str(profile.get("model") or "").strip()
            if model_name:
                collected[model_name] = max(collected.get(model_name, 0), fallback)
    legacy = cfg.get("text_api")
    if isinstance(legacy, dict):
        fallback = int(legacy.get("model_context_size", 8192) or 8192)
        _merge(legacy.get("model_meta_catalog", {}), fallback)
        model_name = str(legacy.get("model") or "").strip()
        if model_name:
            collected[model_name] = max(collected.get(model_name, 0), fallback)
    return collected


def build_catalog(cfg: dict) -> dict:
    models: dict[str, dict] = {}
    for model_key, context in sorted(collect_models(cfg).items()):
        capabilities = infer_capabilities(model_key)
        models[model_key] = {
            "context": context,
            "max_out": default_max_output_for(context, model_key),
            "caps": capabilities,
            "params": infer_supported_params(model_key, "openai", capabilities),
        }
    return {
        "version": 1,
        "description": "内置模型全集：上下文窗口 / 输出上限 / 能力 / 参数支持；用户可覆盖。",
        "models": models,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成内置模型全集")
    parser.add_argument("--source", default=os.path.join(PROJECT_ROOT, "conf", "setting.json"))
    parser.add_argument(
        "--output", default=os.path.join(PROJECT_ROOT, "data", "model_catalog.json")
    )
    args = parser.parse_args()

    if not os.path.exists(args.source):
        print(f"源配置不存在: {args.source}")
        return 1
    with open(args.source, "r", encoding="utf-8") as handle:
        cfg = json.load(handle)

    catalog = build_catalog(cfg)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"已生成 {len(catalog['models'])} 个模型 -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
