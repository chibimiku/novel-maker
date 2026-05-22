from __future__ import annotations

import json
import os
import re
import hashlib
from typing import Callable


_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]{2,}")
_TERM_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\w]{2,}")


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _TERM_PATTERN.finditer(text):
        token = match.group(0).lower()
        if len(token) >= 2:
            tokens.add(token)
    return tokens


def _extract_cjk_phrases(text: str, min_len: int = 2, max_len: int = 6) -> set[str]:
    phrases: set[str] = set()
    chars: list[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf":
            chars.append(ch)
        else:
            if len(chars) >= min_len:
                for length in range(min_len, min(max_len, len(chars)) + 1):
                    for i in range(len(chars) - length + 1):
                        phrases.add("".join(chars[i : i + length]))
            chars = []
    if len(chars) >= min_len:
        for length in range(min_len, min(max_len, len(chars)) + 1):
            for i in range(len(chars) - length + 1):
                phrases.add("".join(chars[i : i + length]))
    return phrases


def _read_setting_content(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""

    if isinstance(data, dict):
        parts: list[str] = []
        for key, value in data.items():
            if isinstance(value, str):
                parts.append(f"{key}: {value}")
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        parts.append(f"{key}: {item}")
                    elif isinstance(item, dict):
                        parts.append(json.dumps(item, ensure_ascii=False))
            elif isinstance(value, dict):
                parts.append(json.dumps(value, ensure_ascii=False))
        return " ".join(parts)
    return json.dumps(data, ensure_ascii=False)


def compute_local_relevance(
    node_summary: str,
    node_title: str,
    setting_paths: list[str],
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[tuple[str, float]]:
    if not setting_paths:
        return []

    node_text = f"{node_title} {node_summary}"
    node_tokens = _tokenize(node_text)
    node_cjk = _extract_cjk_phrases(node_text)

    total = len(setting_paths)
    results: list[tuple[str, float]] = []
    for idx, path in enumerate(setting_paths):
        if progress_callback:
            progress_callback(idx, total)

        setting_text = _read_setting_content(path)
        if not setting_text:
            results.append((path, 0.0))
            continue

        setting_name = os.path.basename(path).replace(".json", "")
        setting_cat = os.path.basename(os.path.dirname(path))

        setting_tokens = _tokenize(f"{setting_name} {setting_text}")
        setting_cjk = _extract_cjk_phrases(setting_text)

        token_overlap = node_tokens & setting_tokens
        token_union = node_tokens | setting_tokens
        token_jaccard = (
            len(token_overlap) / len(token_union) if token_union else 0.0
        )

        cjk_overlap = node_cjk & setting_cjk
        cjk_union = node_cjk | setting_cjk
        cjk_jaccard = len(cjk_overlap) / len(cjk_union) if cjk_union else 0.0

        cat_weights: dict[str, float] = {
            "人物设定": 1.4,
            "地点设定": 1.3,
            "公共设定": 1.15,
            "名词设定": 1.1,
            "其他设定": 1.0,
        }
        cat_weight = cat_weights.get(setting_cat, 1.0)

        name_in_summary = 0.0
        for name_token in _tokenize(setting_name):
            if name_token in node_tokens:
                name_in_summary = 1.0
                break
        if name_in_summary == 0.0:
            for phrase in _extract_cjk_phrases(setting_name, min_len=1, max_len=5):
                if phrase in node_text:
                    name_in_summary = 0.6
                    break

        score = (
            token_jaccard * 0.35
            + cjk_jaccard * 0.35
            + name_in_summary * 0.30
        ) * cat_weight

        score = min(1.0, score)
        results.append((path, round(score, 4)))

    if progress_callback:
        progress_callback(total, total)

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def build_relevance_cache_key(
    node_id: str,
    node_summary: str,
    setting_paths: list[str],
) -> str:
    paths_key = ",".join(sorted(setting_paths))
    raw = f"{node_id}|{node_summary}|{paths_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_setting_mtimes(setting_paths: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for path in setting_paths:
        try:
            result[path] = os.path.getmtime(path)
        except OSError:
            result[path] = 0.0
    return result
