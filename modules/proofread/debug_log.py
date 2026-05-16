from __future__ import annotations

import logging
import os
from typing import Any


def is_debug_enabled(shared_data: dict[str, Any] | None = None) -> bool:
    if isinstance(shared_data, dict):
        if "debug_log_enabled" in shared_data:
            return bool(shared_data.get("debug_log_enabled"))
    val = str(os.environ.get("PROOFREAD_DEBUG_LOG", "0")).strip().lower()
    return val in ("1", "true", "yes", "on")


def dlog(logger: logging.Logger, msg: str, shared_data: dict[str, Any] | None = None) -> None:
    if is_debug_enabled(shared_data):
        logger.info("[ProofreadDebug] %s", msg)

