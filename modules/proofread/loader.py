from __future__ import annotations

import json
import logging
import os
from typing import Any

from core.workspace_manager import WorkspaceManager

from .debug_log import dlog
from .types import SceneMeta

logger = logging.getLogger(__name__)


class ProofreadDataLoader:
    def __init__(self, workspace: WorkspaceManager):
        self.workspace = workspace

    def load_workspace_data(
        self,
        include_chapters: set[str] | None = None,
        scene_title_keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        tree = self.workspace.load_outline_tree()
        dlog(logger, f"load_workspace_data: tree root nodes={len(tree.get('nodes', []))}")
        scene_list = self._extract_scene_list(
            tree.get("nodes", []),
            include_chapters=include_chapters,
            scene_title_keywords=scene_title_keywords,
        )
        characters = self._load_settings_jsons("人物设定")
        locations = self._load_settings_jsons("地点设定")
        alias_map = self._build_character_alias_map(characters)
        dlog(
            logger,
            f"workspace loaded: scenes={len(scene_list)}, characters={len(characters)}, locations={len(locations)}, alias={len(alias_map)}, filter_chapters={len(include_chapters or set())}, filter_keywords={len(scene_title_keywords or [])}",
        )
        return {
            "project_name": os.path.basename(self.workspace.workspace_path),
            "scene_list": scene_list,
            "characters": characters,
            "locations": locations,
            "character_alias_map": alias_map,
        }

    def _extract_scene_list(
        self,
        roots: list[dict[str, Any]],
        include_chapters: set[str] | None = None,
        scene_title_keywords: list[str] | None = None,
    ) -> list[SceneMeta]:
        normalized_keywords = [k.strip().lower() for k in (scene_title_keywords or []) if str(k).strip()]
        scenes: list[SceneMeta] = []
        for chapter in roots:
            chapter_title = chapter.get("title", "")
            if include_chapters and chapter_title not in include_chapters:
                continue
            for section in chapter.get("children", []) or []:
                section_title = section.get("title", "")
                for scene in section.get("children", []) or []:
                    scene_title = scene.get("title", "")
                    if normalized_keywords:
                        st = str(scene_title or "").lower()
                        if not any(k in st for k in normalized_keywords):
                            continue
                    rel_path = scene.get("file_path", "")
                    full_path = os.path.join(self.workspace.text_path, rel_path) if rel_path else ""
                    content = self.read_text_file_with_fallback(full_path) if full_path else ""
                    scenes.append(
                        SceneMeta(
                            node_id=str(scene.get("id", "")),
                            chapter_title=chapter_title,
                            section_title=section_title,
                            scene_title=scene_title,
                            file_path=rel_path,
                            summary_raw=str(scene.get("summary", "") or ""),
                            content_raw=content,
                            content_md5=str(scene.get("md5", "") or ""),
                        )
                    )
        dlog(logger, f"_extract_scene_list done: total={len(scenes)}")
        return scenes

    def _load_settings_jsons(self, setting_dir_name: str) -> list[dict[str, Any]]:
        base_dir = os.path.join(self.workspace.settings_path, setting_dir_name)
        if not os.path.exists(base_dir):
            return []

        result: list[dict[str, Any]] = []
        for root, _dirs, files in os.walk(base_dir):
            for fn in files:
                if not fn.lower().endswith(".json"):
                    continue
                if fn.lower() == "template.json":
                    continue
                full_path = os.path.join(root, fn)
                data = self._load_json_file_with_fallback(full_path)
                if isinstance(data, dict):
                    result.append(data)
        dlog(logger, f"_load_settings_jsons: dir={setting_dir_name}, count={len(result)}")
        return result

    def _load_json_file_with_fallback(self, full_path: str) -> dict[str, Any] | None:
        for enc in ("utf-8", "gbk"):
            try:
                with open(full_path, "r", encoding=enc) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
                return None
            except Exception:
                continue
        logger.warning("读取设定文件失败: %s", full_path)
        return None

    def read_text_file_with_fallback(self, file_path: str) -> str:
        if not file_path or not os.path.exists(file_path):
            return ""
        for enc in ("utf-8", "gbk"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read().replace("\r\n", "\n").replace("\r", "\n")
            except Exception:
                continue
        logger.warning("读取正文文件失败: %s", file_path)
        return ""

    def _build_character_alias_map(self, characters: list[dict[str, Any]]) -> dict[str, str]:
        alias_map: dict[str, str] = {}
        for char in characters:
            name = str(char.get("姓名", "") or "").strip()
            if not name:
                continue
            alias_map[name] = name
            for key in ("别名", "绰号", "昵称", "别称"):
                val = char.get(key)
                if isinstance(val, str):
                    aliases = [a.strip() for a in val.replace("，", ",").split(",") if a.strip()]
                elif isinstance(val, list):
                    aliases = [str(a).strip() for a in val if str(a).strip()]
                else:
                    aliases = []
                for alias in aliases:
                    alias_map[alias] = name
        return alias_map
