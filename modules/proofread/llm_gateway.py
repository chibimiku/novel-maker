from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .debug_log import dlog
from .transport_codec import TransportCodec

logger = logging.getLogger(__name__)


DEFAULT_PROMPT_HEADER = """你是小说校对系统的数据处理器。
你会收到一个经过编码的载荷，请先解码再处理。
输出必须是 JSON，禁止 markdown 代码块，禁止解释性文字。
输出结构必须包含：ok, issues, intermediate_data, error。"""

BATCH_PROMPT_HEADER = """你是小说校对系统的数据处理器。
你会收到一个经过编码的载荷，请先解码再处理。
解码后结构为：
{
  "batch": [
    {"task_id": "...", "payload": {...}},
    ...
  ]
}

请对 batch 中每个 item 的 payload 独立处理，并为每个 item 输出一条结果：
{
  "task_id": "...",
  "ok": true|false,
  "issues": [...],
  "intermediate_data": {...},
  "error": "..."
}

最终只输出一个 JSON 对象：
{"ok":true,"results":[...]}

禁止 markdown 代码块，禁止解释性文字。"""


class ProofreadLLMGateway:
    def __init__(self, llm_client: Any, max_retry: int = 3, backoff_sec: float = 1.5):
        self.llm_client = llm_client
        self.max_retry = max(1, int(max_retry))
        self.backoff_sec = max(0.1, float(backoff_sec))
        self.default_progress_callback = None
        self.on_call_success = None

    def call_json(
        self,
        payload: dict[str, Any],
        task_id: str,
        system_instruction: str | None = None,
        progress_callback=None,
    ) -> dict[str, Any]:
        if progress_callback is None:
            progress_callback = self.default_progress_callback
        last_error = ""
        raw_text = ""
        encoded = TransportCodec.encode_payload(payload, compress=True)
        dlog(
            logger,
            f"call_json start: task_id={task_id}, encoding={encoded.get('encoding')}, raw_bytes={encoded.get('raw_bytes')}, encoded_bytes={encoded.get('encoded_bytes')}",
        )
        if progress_callback:
            progress_callback(
                f"[{task_id}] 编码完成: raw_bytes={encoded.get('raw_bytes')}, encoded_bytes={encoded.get('encoded_bytes')}"
            )
        prompt = TransportCodec.to_llm_prompt(encoded, DEFAULT_PROMPT_HEADER)

        for attempt in range(1, self.max_retry + 1):
            if progress_callback:
                progress_callback(f"[{task_id}] 请求第 {attempt}/{self.max_retry} 次")
            dlog(logger, f"call_json attempt={attempt}/{self.max_retry}, task_id={task_id}")
            if progress_callback:
                progress_callback(f"[{task_id}] 已发送请求，等待模型返回...")
            raw_text = self.llm_client.generate_text(
                prompt=prompt,
                override_system_instruction=system_instruction,
                progress_callback=progress_callback,
            )
            if progress_callback:
                progress_callback(f"[{task_id}] 已收到返回，开始解析 JSON（raw_len={len(raw_text or '')}）...")
            parsed = self._parse_json(raw_text)
            if parsed is not None:
                dlog(
                    logger,
                    f"call_json parsed ok: task_id={task_id}, attempt={attempt}, keys={list(parsed.keys())[:8]}",
                )
                if self.on_call_success:
                    try:
                        self.on_call_success(task_id, parsed, raw_text)
                    except Exception:
                        pass
                return {
                    "ok": bool(parsed.get("ok", True)),
                    "result": parsed,
                    "raw_text": raw_text,
                    "attempts": attempt,
                    "error": None,
                }

            last_error = "json parse failed"
            dlog(logger, f"call_json parse failed: task_id={task_id}, attempt={attempt}, raw_len={len(raw_text or '')}")
            if attempt < self.max_retry:
                if progress_callback:
                    progress_callback(f"[{task_id}] JSON 解析失败，准备重试（等待 {self.backoff_sec * (2 ** (attempt - 1)):.1f}s）...")
                time.sleep(self.backoff_sec * (2 ** (attempt - 1)))
                prompt = self._build_retry_prompt(encoded, raw_text)

        dlog(logger, f"call_json failed: task_id={task_id}, error={last_error}")
        if progress_callback:
            progress_callback(f"[{task_id}] 失败: {last_error or 'unknown error'}")
        return {
            "ok": False,
            "result": None,
            "raw_text": raw_text,
            "attempts": self.max_retry,
            "error": last_error or "unknown error",
        }

    def call_json_batch(
        self,
        batch: list[dict[str, Any]],
        batch_id: str,
        system_instruction: str | None = None,
        progress_callback=None,
    ) -> dict[str, dict[str, Any]]:
        if progress_callback is None:
            progress_callback = self.default_progress_callback
        if not batch:
            return {}

        payload = {"batch": [{"task_id": x.get("task_id"), "payload": x.get("payload")} for x in batch]}
        encoded = TransportCodec.encode_payload(payload, compress=True)
        prompt = TransportCodec.to_llm_prompt(encoded, BATCH_PROMPT_HEADER)
        raw_text = ""
        last_error = ""

        for attempt in range(1, self.max_retry + 1):
            if progress_callback:
                progress_callback(f"[{batch_id}] Batch 请求第 {attempt}/{self.max_retry} 次（items={len(batch)}）")
                progress_callback(f"[{batch_id}] 编码完成: raw_bytes={encoded.get('raw_bytes')}, encoded_bytes={encoded.get('encoded_bytes')}")
                progress_callback(f"[{batch_id}] 已发送请求，等待模型返回...")
            raw_text = self.llm_client.generate_text(
                prompt=prompt,
                override_system_instruction=system_instruction,
                progress_callback=progress_callback,
            )
            if progress_callback:
                progress_callback(f"[{batch_id}] 已收到返回，开始解析 JSON（raw_len={len(raw_text or '')}）...")
            parsed = self._parse_json(raw_text)
            if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
                out: dict[str, dict[str, Any]] = {}
                for item in parsed.get("results", []):
                    if not isinstance(item, dict):
                        continue
                    task_id = str(item.get("task_id", "") or "")
                    if not task_id:
                        continue
                    out[task_id] = {
                        "ok": bool(item.get("ok", True)),
                        "result": {
                            "ok": bool(item.get("ok", True)),
                            "issues": item.get("issues", []) or [],
                            "intermediate_data": item.get("intermediate_data", {}) or {},
                            "error": item.get("error", None),
                        },
                        "raw_text": None,
                        "attempts": attempt,
                        "error": item.get("error", None),
                    }
                return out

            last_error = "batch json parse failed"
            if attempt < self.max_retry:
                if progress_callback:
                    progress_callback(
                        f"[{batch_id}] JSON 解析失败，准备重试（等待 {self.backoff_sec * (2 ** (attempt - 1)):.1f}s）..."
                    )
                time.sleep(self.backoff_sec * (2 ** (attempt - 1)))
                prompt = self._build_retry_prompt(encoded, raw_text)

        if progress_callback:
            progress_callback(f"[{batch_id}] Batch 失败: {last_error or 'unknown error'}")
        out: dict[str, dict[str, Any]] = {}
        for x in batch:
            tid = str(x.get("task_id", "") or "")
            if tid:
                out[tid] = {
                    "ok": False,
                    "result": None,
                    "raw_text": None,
                    "attempts": self.max_retry,
                    "error": last_error or "unknown error",
                }
        return out

    @staticmethod
    def split_batch_items(
        items: list[dict[str, Any]],
        max_items: int,
        max_raw_bytes: int,
    ) -> list[list[dict[str, Any]]]:
        max_items = max(1, int(max_items or 1))
        max_raw_bytes = max(4000, int(max_raw_bytes or 4000))
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_bytes = 0

        for it in items:
            payload = it.get("payload")
            try:
                payload_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            except Exception:
                payload_bytes = 0
            if current and (len(current) >= max_items or (current_bytes + payload_bytes) > max_raw_bytes):
                batches.append(current)
                current = []
                current_bytes = 0
            current.append(it)
            current_bytes += payload_bytes

        if current:
            batches.append(current)
        return batches

    def _build_retry_prompt(self, encoded: dict[str, Any], previous_raw: str) -> str:
        retry_header = (
            DEFAULT_PROMPT_HEADER
            + "\n上一轮输出不是合法 JSON，请修复格式。"
            + "\n不要解释，不要注释，不要 markdown，只输出 JSON。"
        )
        return (
            TransportCodec.to_llm_prompt(encoded, retry_header)
            + f"\n\n## 上一轮原始输出\n{previous_raw}"
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        text = text.strip()
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
