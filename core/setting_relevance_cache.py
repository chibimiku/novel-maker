from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

from core.setting_relevance import build_relevance_cache_key, get_setting_mtimes

logger = logging.getLogger(__name__)


class SettingRelevanceCache:
    def __init__(self, workspace_path: str):
        self.cache_dir = os.path.join(workspace_path, ".cache", "smart_setting_selection")
        self.manifest_path = os.path.join(self.cache_dir, "manifest.json")
        self._lock = threading.Lock()
        os.makedirs(self.cache_dir, exist_ok=True)

    def _load_manifest(self) -> dict:
        if not os.path.exists(self.manifest_path):
            return {"version": "v1", "updated_at": "", "entries": {}}
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": "v1", "updated_at": "", "entries": {}}
        data.setdefault("entries", {})
        return data

    def _save_manifest(self, manifest: dict) -> None:
        manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.manifest_path)

    def _entry_file(self, cache_key: str) -> str:
        return os.path.join(self.cache_dir, f"{cache_key}.json")

    def is_valid(
        self,
        node_id: str,
        node_summary: str,
        setting_paths: list[str],
    ) -> bool:
        cache_key = build_relevance_cache_key(node_id, node_summary, setting_paths)
        entry_file = self._entry_file(cache_key)
        if not os.path.exists(entry_file):
            return False
        manifest = self._load_manifest()
        entry = manifest.get("entries", {}).get(cache_key)
        if not entry:
            return False
        cached_mtimes = entry.get("setting_mtimes", {})
        current_mtimes = get_setting_mtimes(setting_paths)
        for path, mtime in current_mtimes.items():
            cached_mtime = cached_mtimes.get(path)
            if cached_mtime is None or abs(cached_mtime - mtime) > 0.01:
                return False
        for path in cached_mtimes:
            if path not in current_mtimes:
                return False
        return True

    def load(
        self,
        node_id: str,
        node_summary: str,
        setting_paths: list[str],
    ) -> list[tuple[str, float]] | None:
        if not self.is_valid(node_id, node_summary, setting_paths):
            return None
        cache_key = build_relevance_cache_key(node_id, node_summary, setting_paths)
        entry_file = self._entry_file(cache_key)
        try:
            with open(entry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            scores = data.get("scores", [])
            result: list[tuple[str, float]] = []
            for item in scores:
                path = item.get("path")
                score = item.get("score")
                if path and isinstance(score, (int, float)):
                    result.append((path, float(score)))
            return result if result else None
        except Exception as e:
            logger.warning(f"读取相关性缓存失败: {e}")
            return None

    def save(
        self,
        node_id: str,
        node_summary: str,
        setting_paths: list[str],
        scores: list[tuple[str, float]],
    ) -> None:
        cache_key = build_relevance_cache_key(node_id, node_summary, setting_paths)
        entry_file = self._entry_file(cache_key)
        payload = {
            "cache_key": cache_key,
            "node_id": node_id,
            "scores": [{"path": p, "score": s} for p, s in scores],
            "cached_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp = entry_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, entry_file)

        with self._lock:
            manifest = self._load_manifest()
            manifest.setdefault("entries", {})[cache_key] = {
                "node_id": node_id,
                "setting_mtimes": get_setting_mtimes(setting_paths),
                "cached_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save_manifest(manifest)

    def load_selection(
        self,
        node_id: str,
        node_summary: str,
        setting_paths: list[str],
    ) -> list[str] | None:
        if not self.is_valid(node_id, node_summary, setting_paths):
            return None
        cache_key = build_relevance_cache_key(node_id, node_summary, setting_paths)
        entry_file = self._entry_file(cache_key)
        try:
            with open(entry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            selected = data.get("llm_selected_paths")
            if isinstance(selected, list) and selected:
                return [p for p in selected if isinstance(p, str)]
            return None
        except Exception as e:
            logger.warning(f"读取LLM勾选缓存失败: {e}")
            return None

    def save_selection(
        self,
        node_id: str,
        node_summary: str,
        setting_paths: list[str],
        selected_paths: list[str],
        relevance_scores: list[tuple[str, float]],
    ) -> None:
        cache_key = build_relevance_cache_key(node_id, node_summary, setting_paths)
        entry_file = self._entry_file(cache_key)
        payload = {
            "cache_key": cache_key,
            "node_id": node_id,
            "scores": [{"path": p, "score": s} for p, s in relevance_scores],
            "llm_selected_paths": selected_paths,
            "cached_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp = entry_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, entry_file)

        with self._lock:
            manifest = self._load_manifest()
            manifest.setdefault("entries", {})[cache_key] = {
                "node_id": node_id,
                "setting_mtimes": get_setting_mtimes(setting_paths),
                "cached_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save_manifest(manifest)
