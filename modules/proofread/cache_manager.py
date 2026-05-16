from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime
from typing import Any

from .debug_log import dlog

logger = logging.getLogger(__name__)


class ProofreadCacheManager:
    def __init__(self, workspace_path: str):
        self.cache_dir = os.path.join(workspace_path, "系统数据", "proofread_cache")
        self.manifest_path = os.path.join(self.cache_dir, "manifest.json")
        self._manifest_lock = threading.Lock()
        os.makedirs(self.cache_dir, exist_ok=True)

    def load_manifest(self) -> dict[str, Any]:
        if not os.path.exists(self.manifest_path):
            return {"version": "v2", "updated_at": "", "scenes": {}}
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": "v2", "updated_at": "", "scenes": {}}
        data.setdefault("scenes", {})
        return data

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._atomic_write_json(self.manifest_path, manifest)
        dlog(logger, f"cache save_manifest: scenes={len(manifest.get('scenes', {}))}")

    def is_scene_pass_cache_valid(self, scene_id: str, scene_md5: str, pass_name: str) -> bool:
        manifest = self.load_manifest()
        scene_entry = manifest.get("scenes", {}).get(scene_id, {})
        if scene_entry.get("md5") != scene_md5:
            return False
        cached_passes = scene_entry.get("cached_passes", [])
        if pass_name not in cached_passes:
            return False
        return os.path.exists(self._scene_pass_file(scene_id, pass_name))

    def load_scene_pass_data(self, scene_id: str, pass_name: str) -> dict[str, Any] | None:
        fp = self._scene_pass_file(scene_id, pass_name)
        if not os.path.exists(fp):
            return None
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None

    def save_scene_pass_data(self, scene_id: str, pass_name: str, data: dict[str, Any]) -> None:
        fp = self._scene_pass_file(scene_id, pass_name)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        self._atomic_write_json(fp, data)
        dlog(logger, f"cache save_scene_pass_data: scene={scene_id}, pass={pass_name}")

    def mark_scene_pass_cached(self, scene_id: str, scene_md5: str, pass_name: str) -> None:
        with self._manifest_lock:
            manifest = self.load_manifest()
            scenes = manifest.setdefault("scenes", {})
            scene_entry = scenes.setdefault(scene_id, {})
            scene_entry["md5"] = scene_md5
            cached_passes = set(scene_entry.get("cached_passes", []))
            cached_passes.add(pass_name)
            scene_entry["cached_passes"] = sorted(cached_passes)
            scene_entry["cached_at"] = datetime.now().isoformat(timespec="seconds")
            self.save_manifest(manifest)

    def invalidate_scene(self, scene_id: str) -> None:
        scene_dir = os.path.join(self.cache_dir, f"scene_{scene_id}")
        if os.path.exists(scene_dir):
            shutil.rmtree(scene_dir, ignore_errors=True)
        with self._manifest_lock:
            manifest = self.load_manifest()
            manifest.get("scenes", {}).pop(scene_id, None)
            self.save_manifest(manifest)
        dlog(logger, f"cache invalidate_scene: scene={scene_id}")

    def _scene_pass_file(self, scene_id: str, pass_name: str) -> str:
        return os.path.join(self.cache_dir, f"scene_{scene_id}", f"{pass_name}.json")

    @staticmethod
    def _atomic_write_json(path: str, data: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, dir=os.path.dirname(path), suffix=".tmp"
        ) as tf:
            json.dump(data, tf, ensure_ascii=False, indent=2)
            tf.flush()
            os.fsync(tf.fileno())
            temp_path = tf.name
        os.replace(temp_path, path)
