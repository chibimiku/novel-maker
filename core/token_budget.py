"""Token 估算与预算计算。

本模块把“一次请求能用多少 token”从散落在各处的经验公式收敛成一套可测试的规则：

* :func:`estimate_tokens` —— 中英混排文本的 token 估算；
* :class:`TokenEstimator` —— 带在线校准的估算器（用服务端返回的 usage 修正系数）；
* :class:`TokenBudget` —— 依据具体模型的上下文窗口与最大输出 token 计算输入预算。

设计目标：不同模型上下文窗口差异极大（8K ~ 2M），因此预算必须以“当前模型”为准，
而不是全局写死一个数字。
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

DEFAULT_CONTEXT_SIZE = 8192
DEFAULT_MAX_OUTPUT_TOKENS = 2048

#: 协议开销：每个 message 的角色/分隔符，以及 assistant 回复引导符。
MESSAGE_OVERHEAD_TOKENS = 4
REPLY_PRIMING_TOKENS = 3

#: 一次请求里为协议、工具调用等预留的 token 数下限。
MIN_PROTOCOL_RESERVE = 512


def _is_cjk(code_point: int) -> bool:
    """是否属于 CJK 表意文字/标点（按 1 token/字符 估算）。"""
    return (
        0x4E00 <= code_point <= 0x9FFF        # 基本汉字
        or 0x3400 <= code_point <= 0x4DBF     # 扩展 A
        or 0x20000 <= code_point <= 0x2FA1F   # 扩展 B-F
        or 0x3000 <= code_point <= 0x303F     # 中文标点
        or 0xFF00 <= code_point <= 0xFFEF     # 全角字符
    )


def _is_latin_alnum(code_point: int) -> bool:
    return (
        0x30 <= code_point <= 0x39            # 0-9
        or 0x41 <= code_point <= 0x5A         # A-Z
        or 0x61 <= code_point <= 0x7A         # a-z
    )


def estimate_tokens(text: str | None) -> int:
    """估算一段文本的 token 数。

    规则（偏保守，宁可多算一点也不要超出上下文窗口）：

    * CJK 字符：1 token / 字符；
    * 拉丁字母与数字：约 4 字符 / token（按单词边界粗算）；
    * 空白与换行：约 8 字符 / token；
    * 其它符号/emoji：约 2 字符 / token。

    英文按“连续字母数字串”聚合，避免 ``Hello world`` 被算成 11 个 token。
    """
    if not text:
        return 0

    tokens = 0.0
    run = 0          # 当前连续拉丁字母/数字串长度
    other = 0        # 当前连续其它符号串长度

    def flush_run() -> None:
        nonlocal run, tokens
        if run:
            tokens += max(1.0, math.ceil(run / 4.0))
            run = 0

    def flush_other() -> None:
        nonlocal other, tokens
        if other:
            tokens += max(1.0, math.ceil(other / 2.0))
            other = 0

    for ch in text:
        cp = ord(ch)
        if _is_latin_alnum(cp):
            flush_other()
            run += 1
            continue
        flush_run()
        if _is_cjk(cp):
            flush_other()
            tokens += 1.0
        elif ch in " \t\r\n":
            flush_other()
            tokens += 0.125
        else:
            other += 1
    flush_run()
    flush_other()
    return max(1, int(math.ceil(tokens)))


def estimate_messages_tokens(messages: list[dict] | None) -> int:
    """按 OpenAI 的消息格式估算 token 总数（含协议开销）。"""
    if not messages:
        return 0
    total = REPLY_PRIMING_TOKENS
    for message in messages:
        if not isinstance(message, dict):
            continue
        total += MESSAGE_OVERHEAD_TOKENS
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += estimate_tokens(part["text"])
        name = message.get("name")
        if isinstance(name, str) and name:
            total += estimate_tokens(name)
    return total


def estimate_json_tokens(obj) -> int:
    """估算一个 JSON 可序列化结构的 token 数（用于工具结果/结构化提示词）。"""
    try:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(obj)
    return estimate_tokens(text)


@dataclass
class TokenBudget:
    """单个模型的 token 预算。

    :param context_size: 模型上下文窗口（输入 + 输出）
    :param max_output_tokens: 本次请求预留的最大输出 token
    :param protocol_reserve: 协议与安全余量
    :param history_ratio: 输入预算里允许分配给“上下文消息”的比例上限
    """

    context_size: int = DEFAULT_CONTEXT_SIZE
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    protocol_reserve: int = MIN_PROTOCOL_RESERVE
    history_ratio: float = 0.75

    def __post_init__(self) -> None:
        self.context_size = max(1024, int(self.context_size or DEFAULT_CONTEXT_SIZE))
        # 输出最多占窗口的一半，避免出现“输入为负”的病态配置
        max_allowed_output = max(256, self.context_size // 2)
        self.max_output_tokens = max(
            256, min(int(self.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS), max_allowed_output)
        )
        self.protocol_reserve = max(128, min(int(self.protocol_reserve), self.context_size // 4))

    # -- 预算 ---------------------------------------------------------
    def input_budget(self) -> int:
        """输入侧预算（system + 上下文 + 用户提示词）。"""
        budget = self.context_size - self.max_output_tokens - self.protocol_reserve
        return max(512, budget)

    def prompt_budget(self, system_tokens: int = 0, context_tokens: int = 0) -> int:
        """留给“用户本次提示词”的预算。"""
        remaining = self.input_budget() - max(0, system_tokens) - max(0, context_tokens)
        return max(256, remaining)

    def history_budget(self, system_tokens: int = 0) -> int:
        """允许上下文消息占用的预算（不超过输入预算的 ``history_ratio``）。"""
        available = max(0, self.input_budget() - max(0, system_tokens))
        return max(0, int(available * max(0.0, min(1.0, self.history_ratio))))

    def clamp_output(self, requested: int | None) -> int:
        """把用户/模型配置里的输出上限夹到窗口允许的范围内。"""
        if requested is None or int(requested) <= 0:
            return self.max_output_tokens
        return max(128, min(int(requested), self.max_output_tokens))

    def usage_ratio(self, used_tokens: int) -> float:
        """已用输入 token 占输入预算的比例，用于日志/UI 展示。"""
        budget = self.input_budget()
        return float(used_tokens) / float(budget) if budget else 0.0

    def summary(self) -> str:
        return (
            f"context={self.context_size}, max_output={self.max_output_tokens}, "
            f"input_budget={self.input_budget()}"
        )


def truncate_text_to_budget(
    text: str,
    budget_tokens: int,
    *,
    keep_head_ratio: float = 0.7,
    marker: str = "\n\n...(中间内容已按预算压缩)...\n\n",
    scale: float = 1.0,
) -> tuple[str, bool]:
    """按 token 预算裁剪文本，保留头尾。

    ``scale`` 用于把估算器校准系数带进来（实际 token ≈ 估算 × scale）。

    返回 ``(裁剪后的文本, 是否发生裁剪)``。
    """
    if not text:
        return text, False
    if budget_tokens <= 0:
        return "", True
    scale = max(0.05, float(scale or 1.0))

    def _est(value: str) -> int:
        return int(math.ceil(estimate_tokens(value) * scale))

    current = _est(text)
    if current <= budget_tokens:
        return text, False

    keep_ratio = max(0.05, min(1.0, budget_tokens / float(current)))
    keep_chars = max(200, int(len(text) * keep_ratio))
    head_ratio = max(0.1, min(0.95, keep_head_ratio))

    def _cut(chars: int) -> str:
        head_len = max(1, int(chars * head_ratio))
        tail_len = max(1, chars - head_len)
        return text[:head_len].rstrip() + marker + text[-tail_len:].lstrip()

    truncated = _cut(keep_chars)
    # 估算误差可能让首次裁剪仍超出预算，逐级收缩
    while _est(truncated) > budget_tokens and keep_chars > 240:
        keep_chars = int(keep_chars * 0.85)
        truncated = _cut(keep_chars)
    return truncated, True


def truncate_messages_to_budget(
    messages: list[dict],
    budget_tokens: int,
    *,
    keep_recent: int = 2,
    scale: float = 1.0,
) -> tuple[list[dict], bool]:
    """从最早的消息开始丢弃上下文，直到满足预算。

    末尾 ``keep_recent`` 条消息始终保留（通常是当前节点/最近上下文）。
    """
    if not messages:
        return messages, False
    scale = max(0.05, float(scale or 1.0))

    def _est(items: list[dict]) -> int:
        return int(math.ceil(estimate_messages_tokens(items) * scale))

    if _est(messages) <= budget_tokens:
        return messages, False

    kept = list(messages)
    min_keep = max(1, min(keep_recent, len(kept)))
    dropped = False
    while len(kept) > min_keep and _est(kept) > budget_tokens:
        kept.pop(0)
        dropped = True
    if _est(kept) > budget_tokens:
        # 单条消息仍然超预算：直接裁剪内容
        trimmed: list[dict] = []
        per_message_budget = max(128, int(budget_tokens / max(1, len(kept))))
        for message in kept:
            content = message.get("content")
            if isinstance(content, str):
                new_content, was_cut = truncate_text_to_budget(
                    content,
                    per_message_budget,
                    scale=scale,
                )
                if was_cut:
                    dropped = True
                    message = {**message, "content": new_content}
            trimmed.append(message)
        kept = trimmed
    return kept, dropped


class TokenEstimator:
    """带校准系数的 token 估算器。

    用服务端返回的真实 usage 做指数平滑，逐步逼近当前模型分词器的真实比例。
    系数按 ``model_key`` 分别保存，避免不同模型互相污染。
    """

    MIN_FACTOR = 0.35
    MAX_FACTOR = 3.0

    def __init__(self, factors: dict[str, float] | None = None) -> None:
        self._factors: dict[str, float] = dict(factors or {})
        self._lock = threading.Lock()

    def factor_for(self, model_key: str) -> float:
        with self._lock:
            return float(self._factors.get(model_key or "", 1.0))

    def estimate(self, text: str | None, model_key: str = "") -> int:
        """按校准系数估算 token 数。"""
        raw = estimate_tokens(text)
        if raw <= 0:
            return 0
        factor = self.factor_for(model_key)
        return max(1, int(math.ceil(raw * factor)))

    def estimate_messages(self, messages: list[dict] | None, model_key: str = "") -> int:
        raw = estimate_messages_tokens(messages)
        if raw <= 0:
            return 0
        factor = self.factor_for(model_key)
        # 协议开销不随分词器变化，只缩放内容部分
        overhead = REPLY_PRIMING_TOKENS + MESSAGE_OVERHEAD_TOKENS * len(messages or [])
        return max(1, int(math.ceil((raw - overhead) * factor)) + overhead)

    def observe(self, model_key: str, estimated: int, actual: int) -> float:
        """用一次真实 usage 校准系数，返回最新系数。"""
        if not model_key or estimated <= 0 or actual <= 0:
            return self.factor_for(model_key)
        observed = float(actual) / float(estimated)
        observed = max(self.MIN_FACTOR, min(self.MAX_FACTOR, observed))
        with self._lock:
            current = float(self._factors.get(model_key, 1.0))
            updated = current * 0.85 + observed * 0.15
            updated = max(self.MIN_FACTOR, min(self.MAX_FACTOR, updated))
            self._factors[model_key] = updated
        return updated

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._factors)
