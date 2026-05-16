from __future__ import annotations

import base64
import gzip
import hashlib
import json
from typing import Any


class TransportCodec:
    @staticmethod
    def encode_payload(payload: dict[str, Any], compress: bool = True) -> dict[str, Any]:
        raw_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        raw_bytes = raw_text.encode("utf-8")
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        if compress:
            data_bytes = gzip.compress(raw_bytes)
            encoding = "base64+gzip+utf8"
        else:
            data_bytes = raw_bytes
            encoding = "base64+utf8"

        payload_b64 = base64.urlsafe_b64encode(data_bytes).decode("ascii")
        return {
            "encoding": encoding,
            "payload_b64": payload_b64,
            "sha256": sha256,
            "raw_bytes": len(raw_bytes),
            "encoded_bytes": len(payload_b64),
        }

    @staticmethod
    def decode_payload(encoded: dict[str, Any]) -> dict[str, Any]:
        encoding = str(encoded.get("encoding", "")).lower().strip()
        payload_b64 = encoded.get("payload_b64")
        expected_sha256 = str(encoded.get("sha256", "")).lower().strip()
        if not payload_b64:
            raise ValueError("missing payload_b64")

        try:
            data_bytes = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        except Exception as exc:
            raise ValueError(f"base64 decode failed: {exc}") from exc

        if encoding == "base64+gzip+utf8":
            try:
                raw_bytes = gzip.decompress(data_bytes)
            except Exception as exc:
                raise ValueError(f"gzip decompress failed: {exc}") from exc
        elif encoding == "base64+utf8":
            raw_bytes = data_bytes
        else:
            raise ValueError(f"unsupported encoding: {encoding}")

        got_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if expected_sha256 and got_sha256 != expected_sha256:
            raise ValueError("sha256 mismatch")

        try:
            decoded = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"json decode failed: {exc}") from exc

        if not isinstance(decoded, dict):
            raise ValueError("decoded payload must be dict")
        return decoded

    @staticmethod
    def to_llm_prompt(encoded: dict[str, Any], prompt_header: str) -> str:
        return (
            f"{prompt_header}\n\n"
            f"## ENCODED_PAYLOAD\n"
            f"encoding={encoded['encoding']}\n"
            f"sha256={encoded['sha256']}\n"
            f"payload_b64={encoded['payload_b64']}\n\n"
            "请严格按要求处理，并仅返回 JSON。"
        )

