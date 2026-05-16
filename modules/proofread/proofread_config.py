from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ProofreadConfig:
    parallel_passes: int = 4
    parallel_solves: int = 3
    cache_enabled: bool = True
    max_retry: int = 3
    backoff_sec: float = 1.5
    batch_enabled: bool = True
    batch_find_max_items: int = 3
    batch_find_max_raw_bytes: int = 30000
    batch_solve_max_items: int = 1
    batch_solve_max_raw_bytes: int = 30000

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProofreadConfig":
        data = data or {}
        sub = data.get("proofread", data)
        return cls(
            parallel_passes=max(1, int(sub.get("parallel_passes", 4))),
            parallel_solves=max(1, int(sub.get("parallel_solves", 3))),
            cache_enabled=bool(sub.get("cache_enabled", True)),
            max_retry=max(1, int(sub.get("max_retry", 3))),
            backoff_sec=max(0.1, float(sub.get("backoff_sec", 1.5))),
            batch_enabled=bool(sub.get("batch_enabled", True)),
            batch_find_max_items=max(1, int(sub.get("batch_find_max_items", 3))),
            batch_find_max_raw_bytes=max(4000, int(sub.get("batch_find_max_raw_bytes", 30000))),
            batch_solve_max_items=max(1, int(sub.get("batch_solve_max_items", 1))),
            batch_solve_max_raw_bytes=max(4000, int(sub.get("batch_solve_max_raw_bytes", 30000))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proofread": {
                "parallel_passes": self.parallel_passes,
                "parallel_solves": self.parallel_solves,
                "cache_enabled": self.cache_enabled,
                "max_retry": self.max_retry,
                "backoff_sec": self.backoff_sec,
                "batch_enabled": self.batch_enabled,
                "batch_find_max_items": self.batch_find_max_items,
                "batch_find_max_raw_bytes": self.batch_find_max_raw_bytes,
                "batch_solve_max_items": self.batch_solve_max_items,
                "batch_solve_max_raw_bytes": self.batch_solve_max_raw_bytes,
            }
        }
