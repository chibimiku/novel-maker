from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class WorldSettingNode:
    category: str
    name: str
    payload: dict[str, Any]
    priority: float = 1.0


class WorldContextCompressor:
    """
    压缩世界观节点文本，尽量在有限上下文内保留高价值信息。
    """

    def __init__(
        self,
        model_context_size: int | None = None,
        model_capabilities: list[str] | None = None,
        compression_profile: str | None = None,
    ):
        self.model_context_size = self._normalize_context_size(model_context_size)
        self.model_capabilities = [
            str(c).strip().lower() for c in (model_capabilities or []) if str(c).strip()
        ]
        self.compression_profile = self._normalize_profile(compression_profile)

    def compress(self, nodes: list[WorldSettingNode]) -> str:
        if not nodes:
            return "（无特定世界观设定）"

        full_blocks = [self._render_full_node(n) for n in nodes]
        full_text = "\n\n".join(full_blocks)
        max_chars = self._get_world_budget_chars()
        if len(full_text) <= max_chars:
            return full_text

        node_budgets = self._allocate_node_char_budgets(nodes, max_chars)
        compact_blocks = []
        for node in nodes:
            compact_blocks.append(
                self._render_compact_node(
                    node=node,
                    char_budget=node_budgets.get(node.name, 240),
                )
            )

        compact_text = "\n\n".join(compact_blocks)
        if len(compact_text) <= max_chars:
            return compact_text

        # 兜底二次截断：每块最小保留标题 + 简短内容，确保可控。
        safety_blocks: list[str] = []
        avg = max(80, max_chars // max(len(nodes), 1))
        for blk in compact_blocks:
            if len(blk) <= avg:
                safety_blocks.append(blk)
            else:
                safety_blocks.append(blk[: max(40, avg - 12)].rstrip() + " ...(已压缩)")
        return "\n\n".join(safety_blocks)[:max_chars]

    def _normalize_context_size(self, value: int | None) -> int:
        if not value:
            return 8192
        try:
            parsed = int(value)
            return max(1024, parsed)
        except Exception:
            return 8192

    def _normalize_profile(self, profile: str | None) -> str:
        value = str(profile or "balanced").strip().lower()
        if value in ("conservative", "balanced", "aggressive"):
            return value
        return "balanced"

    def _get_world_budget_chars(self) -> int:
        # 基础预算系数由压缩策略决定
        # conservative: 0.45 / balanced: 0.32 / aggressive: 0.22
        profile_ratio_map = {
            "conservative": 0.45,
            "balanced": 0.32,
            "aggressive": 0.22,
        }
        ratio = profile_ratio_map.get(self.compression_profile, 0.32)
        if any("long_context" in c or "long-context" in c for c in self.model_capabilities):
            ratio += 0.08
        elif any("reasoning" in c for c in self.model_capabilities):
            ratio += 0.04
        ratio = min(0.65, ratio)
        budget_tokens = int(self.model_context_size * ratio)
        # 粗略估算：中文上下文按 1 token ~= 1.2~1.6 字符，取 1.4 作为中间值
        estimated_chars = max(1200, int(budget_tokens * 1.4))
        # 即使模型标称上下文极大，也限制“世界观块”的硬上限，避免提示词成本飙升。
        hard_caps = {
            "conservative": 22000,
            "balanced": 16000,
            "aggressive": 11000,
        }
        return min(estimated_chars, hard_caps.get(self.compression_profile, 16000))

    def _allocate_node_char_budgets(
        self, nodes: list[WorldSettingNode], total_budget: int
    ) -> dict[str, int]:
        profile_base_map = {
            "conservative": 180,
            "balanced": 120,
            "aggressive": 80,
        }
        base = profile_base_map.get(self.compression_profile, 120)
        remain = max(0, total_budget - base * len(nodes))
        total_weight = sum(max(0.2, n.priority) for n in nodes)
        result: dict[str, int] = {}
        for n in nodes:
            weight = max(0.2, n.priority) / total_weight if total_weight else 1 / len(nodes)
            result[n.name] = base + int(remain * weight)
        return result

    def _render_full_node(self, node: WorldSettingNode) -> str:
        lines = [f"【{node.category} - {node.name}】"]
        for key, value in node.payload.items():
            rendered = self._safe_to_text(value)
            if rendered.strip():
                lines.append(f"- {key}: {rendered}")
        return "\n".join(lines)

    def _render_compact_node(self, node: WorldSettingNode, char_budget: int) -> str:
        lines = [f"【{node.category} - {node.name}】"]
        if char_budget <= len(lines[0]) + 16:
            return lines[0]

        top_items = self._rank_items(node.payload)
        used = len(lines[0]) + 1
        for key, value in top_items:
            if used >= char_budget:
                break
            remain = max(20, char_budget - used - len(key) - 6)
            rendered = self._truncate_text(self._safe_to_text(value), remain)
            if not rendered.strip():
                continue
            line = f"- {key}: {rendered}"
            lines.append(line)
            used += len(line) + 1

        if len(lines) == 1:
            lines.append("- 详情: (信息较少或已压缩)")
        return "\n".join(lines)

    def _rank_items(self, payload: dict[str, Any]) -> list[tuple[str, Any]]:
        keys = list(payload.keys())
        keys.sort(key=self._key_weight, reverse=True)
        return [(k, payload[k]) for k in keys]

    def _key_weight(self, key: str) -> float:
        k = str(key).lower()
        score = 1.0
        keywords = [
            ("名称", 2.4),
            ("姓名", 2.4),
            ("关系", 2.2),
            ("背景", 2.0),
            ("设定", 1.9),
            ("定义", 1.9),
            ("目标", 1.8),
            ("冲突", 1.8),
            ("能力", 1.7),
            ("规则", 1.7),
            ("限制", 1.7),
            ("地点", 1.6),
            ("历史", 1.5),
            ("外貌", 1.4),
            ("装备", 1.4),
        ]
        for kw, bonus in keywords:
            if kw in key:
                score += bonus
        if "name" in k or "title" in k:
            score += 1.6
        if "summary" in k or "desc" in k or "description" in k:
            score += 1.2
        return score

    def _safe_to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _truncate_text(self, text: str, max_chars: int) -> str:
        profile_cap_map = {
            "conservative": 320,
            "balanced": 220,
            "aggressive": 140,
        }
        max_chars = min(max_chars, profile_cap_map.get(self.compression_profile, 220))
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 11)].rstrip() + "...(省略)"
