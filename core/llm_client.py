"""大模型客户端：按“当前模型”的上下文窗口与参数发起请求，并记录到任务队列。

相对旧实现的关键变化：

* 连接信息与模型参数来自 :mod:`core.model_config` 的 provider → model 二级配置；
* 每次请求都按当前模型的 ``context_size`` / ``max_output_tokens`` 计算 token 预算，
  不再是全局写死的 8192；
* 预算同时覆盖 system prompt 与上下文消息，并按模型裁剪，避免“只算用户提示词”；
* 采样参数（temperature / top_p / top_k ...）按模型声明过滤后发送；
* 每次真实请求都会写入 :mod:`core.task_queue`，可在界面中回看输入输出；
* 用服务端返回的 usage 校准 token 估算系数。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass

import httpx
from openai import OpenAI

from core.model_config import (
    RuntimeModel,
    active_profile_key,
    resolve_runtime,
)
from core.task_queue import get_task_queue
from core.token_budget import (
    TokenBudget,
    TokenEstimator,
    truncate_messages_to_budget,
    truncate_text_to_budget,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIBRATION_PATH = os.path.join(_PROJECT_ROOT, "conf", "token_calibration.json")


@dataclass
class PreparedRequest:
    """一次请求最终使用的消息与预算信息。"""

    messages: list[dict]
    system_prompt: str
    user_prompt: str
    prompt_trimmed: bool
    history_dropped: bool
    estimated_input_tokens: int
    budget: TokenBudget


class LLMClient:
    def __init__(self, config: dict, *, task_queue=None, workspace_path: str | None = None):
        """初始化客户端。

        :param config: 已归一化（含 providers / text_api_profiles）的配置字典
        :param task_queue: 可选的任务队列实例，默认使用全局单例
        :param workspace_path: 当前工作区路径（用于任务落盘）
        """
        self.config = config if isinstance(config, dict) else {}
        self._task_queue = task_queue
        self.workspace_path = workspace_path

        # 代理配置
        proxy_cfg = self.config.get("proxy", {}) or {}
        self.proxy_enabled = bool(proxy_cfg.get("enabled", False))
        self.proxy_url = str(proxy_cfg.get("url", "http://127.0.0.1:7890") or "")
        if self.proxy_enabled and self.proxy_url:
            os.environ["HTTP_PROXY"] = self.proxy_url
            os.environ["HTTPS_PROXY"] = self.proxy_url
        else:
            os.environ.pop("HTTP_PROXY", None)
            os.environ.pop("HTTPS_PROXY", None)

        self.profile_key = active_profile_key(self.config)
        self.runtime: RuntimeModel | None = resolve_runtime(self.config, self.profile_key)
        self._profile = self._resolve_profile(self.profile_key)

        self.system_instruction = str(self._profile.get("instructions") or "").strip() or (
            "你是一个专业的AI小说家。你的输出必须纯粹是小说情节文本，"
            "严禁包含任何前言、后语、剧情解释或'已为您生成'之类的助手客套话。"
        )
        self.summary_system_instruction = str(
            self._profile.get("summary_instructions") or ""
        ).strip() or "你是一个专业的小说编辑，擅长为小说节点生成精炼、准确的概要。"
        self.modify_system_instruction = str(
            self._profile.get("modify_instructions") or ""
        ).strip() or (
            "你是一个专业的小说改写编辑。你必须严格遵循用户的修改要求，"
            "对原文进行高质量改写。只输出修改后的完整正文，不要附加解释。"
        )

        self.text_generation_mode = self.profile_key
        self.text_type = self.runtime.api_type if self.runtime else "openai"
        self.text_model_name = self.runtime.model_key if self.runtime else ""
        self.text_context_size = (
            self.runtime.context_size if self.runtime else 8192
        )
        budget = self._current_budget()
        self.text_max_tokens = budget.max_output_tokens

        self.auto_continue_enabled = bool(
            self._profile.get("auto_continue_on_incomplete", True)
        )
        try:
            rounds = int(self._profile.get("auto_continue_max_rounds", 3))
        except (TypeError, ValueError):
            rounds = 3
        self.auto_continue_max_rounds = max(0, min(10, rounds))

        self._text_clients: dict[str, OpenAI] = {}
        self._gemini_client = None
        self._last_usage: dict = {}
        self._estimator = TokenEstimator(self._load_calibration())
        if self.runtime is None:
            logger.warning("没有可用的文本模型配置（providers 为空）")

    # -- 基础信息 -----------------------------------------------------
    def _resolve_profile(self, profile_key: str) -> dict:
        profiles = self.config.get("text_api_profiles", {})
        if isinstance(profiles, dict):
            profile = profiles.get(profile_key)
            if isinstance(profile, dict):
                return profile
        legacy = self.config.get("text_api")
        return legacy if isinstance(legacy, dict) else {}

    def _current_budget(self) -> TokenBudget:
        if self.runtime is None:
            return TokenBudget()
        return self.runtime.token_budget()

    def refresh(self, config: dict) -> None:
        """用新配置重建运行时（切换画像/提供商后调用）。"""
        self.__init__(config, task_queue=self._task_queue, workspace_path=self.workspace_path)

    def describe(self) -> str:
        if self.runtime is None:
            return "未配置文本模型"
        return (
            f"{self.runtime.label()} | {self.runtime.params_desc()} | "
            f"context={self.runtime.context_size}, max_out={self.text_max_tokens}"
        )

    def token_budget(self) -> TokenBudget:
        return self._current_budget()

    def estimate_tokens(self, text: str) -> int:
        model_key = self.runtime.model_key if self.runtime else ""
        return self._estimator.estimate(text, model_key)

    # -- token 校准 ---------------------------------------------------
    def _load_calibration(self) -> dict[str, float]:
        if not os.path.exists(CALIBRATION_PATH):
            return {}
        try:
            with open(CALIBRATION_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return {
                    str(key): float(value)
                    for key, value in data.get("factors", {}).items()
                }
        except Exception as exc:  # pragma: no cover - 损坏文件
            logger.warning("加载 token 校准文件失败: %s", exc)
        return {}

    def _save_calibration(self) -> None:
        try:
            os.makedirs(os.path.dirname(CALIBRATION_PATH), exist_ok=True)
            with open(CALIBRATION_PATH, "w", encoding="utf-8") as handle:
                json.dump(
                    {"factors": self._estimator.snapshot()},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:  # pragma: no cover - 磁盘异常
            logger.debug("保存 token 校准文件失败: %s", exc)

    def _calibrate(self, model_key: str, estimated: int, actual: int) -> None:
        if not model_key or not estimated or not actual:
            return
        before = self._estimator.factor_for(model_key)
        after = self._estimator.observe(model_key, estimated, actual)
        if abs(after - before) > 0.01:
            self._save_calibration()

    # -- HTTP / SDK 客户端 -------------------------------------------
    def _http_client(self) -> httpx.Client | None:
        if self.proxy_enabled and self.proxy_url:
            return httpx.Client(proxy=self.proxy_url)
        return None

    def _openai_client(self, runtime: RuntimeModel) -> OpenAI:
        key = runtime.provider_id or runtime.base_url
        client = self._text_clients.get(key)
        if client is None:
            client = OpenAI(
                api_key=runtime.api_key or "sk-placeholder",
                base_url=runtime.base_url or None,
                timeout=runtime.timeout,
                http_client=self._http_client(),
            )
            self._text_clients[key] = client
        return client

    def _task_queue_or_none(self):
        if self._task_queue is not None:
            return self._task_queue
        try:
            queue = get_task_queue()
            if self.workspace_path:
                from core.task_queue import workspace_task_root

                root = workspace_task_root(self.workspace_path)
                if root and queue.root != os.path.abspath(root):
                    queue.set_root(root)
            return queue
        except Exception:  # pragma: no cover - 队列不可用时不阻塞生成
            logger.debug("任务队列不可用", exc_info=True)
            return None

    # -- 请求构造 -----------------------------------------------------
    def _prepare_request(
        self,
        runtime: RuntimeModel,
        system_prompt: str,
        prompt: str,
        context_messages: list,
        budget: TokenBudget,
    ) -> PreparedRequest:
        model_key = runtime.model_key
        factor = self._estimator.factor_for(model_key)
        system_tokens = self._estimator.estimate(system_prompt, model_key)
        history_budget = budget.history_budget(system_tokens)
        trimmed_history, history_dropped = truncate_messages_to_budget(
            list(context_messages or []),
            history_budget,
            keep_recent=2,
            scale=factor,
        )
        context_tokens = self._estimator.estimate_messages(trimmed_history, model_key)
        prompt_budget = budget.prompt_budget(system_tokens, context_tokens)
        final_prompt, prompt_trimmed = truncate_text_to_budget(
            prompt, prompt_budget, scale=factor
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(trimmed_history)
        messages.append({"role": "user", "content": final_prompt})

        estimated_input = (
            system_tokens
            + context_tokens
            + self._estimator.estimate(final_prompt, model_key)
        )
        return PreparedRequest(
            messages=messages,
            system_prompt=system_prompt,
            user_prompt=final_prompt,
            prompt_trimmed=prompt_trimmed,
            history_dropped=history_dropped,
            estimated_input_tokens=estimated_input,
            budget=budget,
        )

    def _begin_task(
        self,
        runtime: RuntimeModel,
        prepared: PreparedRequest,
        *,
        original_prompt: str,
        original_context: list,
        task_meta: dict | None,
    ) -> str | None:
        queue = self._task_queue_or_none()
        if queue is None:
            return None
        meta = dict(task_meta or {})
        extra = {
            "prompt_trimmed": prepared.prompt_trimmed,
            "history_dropped": prepared.history_dropped,
            "requested_max_tokens": prepared.budget.max_output_tokens,
            "estimated_input_tokens": prepared.estimated_input_tokens,
            "profile": self.profile_key,
        }
        if prepared.prompt_trimmed or prepared.history_dropped:
            extra["sent_messages"] = prepared.messages
        try:
            return queue.create_task(
                label=str(meta.get("label") or "模型请求"),
                action_id=meta.get("action_id"),
                node_id=meta.get("node_id"),
                node_title=meta.get("node_title"),
                runtime=runtime,
                params=runtime.request_params(),
                context_size=runtime.context_size,
                max_output_tokens=prepared.budget.max_output_tokens,
                input_budget_tokens=prepared.budget.input_budget(),
                estimated_input_tokens=prepared.estimated_input_tokens,
                system_prompt=prepared.system_prompt,
                user_prompt=original_prompt,
                context_messages=original_context,
                extra=extra,
            )
        except Exception:  # pragma: no cover - 记录失败不影响生成
            logger.debug("创建任务记录失败", exc_info=True)
            return None

    def _complete_task(
        self,
        task_id: str | None,
        output: str,
        usage: dict,
        *,
        estimated_output_tokens: int = 0,
        attempts: list[dict] | None = None,
    ) -> None:
        if not task_id:
            return
        queue = self._task_queue_or_none()
        if queue is None:
            return
        try:
            queue.complete_task(
                task_id,
                output,
                usage=usage,
                estimated_output_tokens=estimated_output_tokens,
                extra={"attempts": attempts or []},
            )
        except Exception:  # pragma: no cover
            logger.debug("完成任务记录失败", exc_info=True)

    def _fail_task(self, task_id: str | None, error: str) -> None:
        if not task_id:
            return
        queue = self._task_queue_or_none()
        if queue is None:
            return
        try:
            queue.fail_task(task_id, error)
        except Exception:  # pragma: no cover
            logger.debug("标记任务失败失败", exc_info=True)

    # -- 续写判定 -----------------------------------------------------
    @staticmethod
    def _count_unmatched_brackets(text: str, left: str, right: str) -> int:
        if not text:
            return 0
        in_str = False
        escaped = False
        balance = 0
        for ch in text:
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == left:
                balance += 1
            elif ch == right and balance > 0:
                balance -= 1
        return balance

    def _looks_incomplete_by_content(self, text: str) -> tuple[bool, str]:
        stripped = (text or "").rstrip()
        if not stripped:
            return True, "empty_output"
        if stripped.count("```") % 2 == 1:
            return True, "unclosed_code_block"
        starts_like_json = stripped.startswith("{") or stripped.startswith("[")
        if starts_like_json:
            try:
                json.loads(stripped)
            except Exception:
                if (
                    self._count_unmatched_brackets(stripped, "{", "}") > 0
                    or self._count_unmatched_brackets(stripped, "[", "]") > 0
                ):
                    return True, "json_unclosed_brackets"
        unfinished_tail = ("...", "…", "（未完", "(未完", "待续", "to be continued")
        lowered = stripped.lower()
        if any(lowered.endswith(tail.lower()) for tail in unfinished_tail):
            return True, "unfinished_tail_marker"
        if re.search(r"[,，:：;；(（\[【{]$", stripped):
            return True, "dangling_tail_punctuation"
        return False, "complete"

    def _should_continue_response(
        self, partial_text: str, finish_reason: str | None
    ) -> tuple[bool, str]:
        reason = (finish_reason or "").strip().lower()
        if reason == "length":
            return True, "finish_reason=length"
        if reason in {"content_filter", "tool_calls"}:
            return False, f"finish_reason={reason}"
        return self._looks_incomplete_by_content(partial_text)

    @staticmethod
    def _extract_affordable_tokens(err_msg: str) -> int | None:
        lowered = (err_msg or "").lower()
        marker = "can only afford"
        index = lowered.find(marker)
        if index < 0:
            return None
        start = index + len(marker)
        while start < len(lowered) and lowered[start].isspace():
            start += 1
        end = start
        while end < len(lowered) and lowered[end].isdigit():
            end += 1
        if end <= start:
            return None
        try:
            value = int(lowered[start:end])
            return value if value > 0 else None
        except ValueError:
            return None

    @staticmethod
    def _emit(progress_callback, message: str) -> None:
        if callable(progress_callback):
            try:
                progress_callback(message)
            except Exception:
                logger.debug("progress_callback 执行失败", exc_info=True)

    def _completion_kwargs(
        self, runtime: RuntimeModel, budget: TokenBudget
    ) -> dict:
        params = runtime.request_params()
        requested = params.pop("max_tokens", None)
        max_tokens = budget.clamp_output(requested or budget.max_output_tokens)
        # OpenAI 官方推理模型要求 max_completion_tokens；其它兼容网关仍用 max_tokens
        if (
            runtime.max_tokens_field() == "max_completion_tokens"
            and "api.openai.com" in (runtime.base_url or "")
        ):
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens
        return params

    # -- 对外生成接口 -------------------------------------------------
    def generate_text(
        self,
        prompt: str,
        context_messages: list | None = None,
        override_system_instruction: str | None = None,
        progress_callback=None,
        task_meta: dict | None = None,
    ) -> str:
        """生成文本。

        :param prompt: 用户提示词
        :param context_messages: OpenAI 格式的上下文消息
        :param override_system_instruction: 覆盖本次请求的 system prompt
        :param progress_callback: 进度回调（字符串）
        :param task_meta: 任务归属信息 ``{label, action_id, node_id, node_title}``
        """
        context_messages = list(context_messages or [])
        runtime = self.runtime
        if runtime is None:
            return (
                "> **生成失败:** 尚未配置文本模型。\n"
                "> 请打开「设置 → 模型服务」添加提供商与模型。"
            )

        system_prompt = (
            override_system_instruction
            if override_system_instruction
            else self.system_instruction
        )
        budget = runtime.token_budget()
        self._last_usage = {}
        prepared = self._prepare_request(
            runtime, system_prompt, prompt or "", context_messages, budget
        )

        proxy_status = f"[已启用代理: {self.proxy_url}]" if self.proxy_enabled else "[未启用代理]"
        logger.info(
            "==> 文本生成请求 %s | 模式: %s | 模型: %s",
            proxy_status,
            self.text_generation_mode,
            runtime.model_key,
        )
        logger.info("接口: %s", runtime.endpoint_desc())
        logger.info("参数: %s", runtime.params_desc())
        logger.info(
            "预算: context=%s, max_output=%s, input_budget=%s, estimated_input≈%s, "
            "prompt_trimmed=%s, history_dropped=%s",
            budget.context_size,
            budget.max_output_tokens,
            budget.input_budget(),
            prepared.estimated_input_tokens,
            prepared.prompt_trimmed,
            prepared.history_dropped,
        )
        logger.info("System 指令:\n%s", system_prompt)
        logger.info("User 提示词:\n%s", prepared.user_prompt)

        self._emit(progress_callback, f"请求接口: {runtime.endpoint_desc()}")
        self._emit(progress_callback, f"参数: {runtime.params_desc()}")
        if prepared.prompt_trimmed or prepared.history_dropped:
            self._emit(
                progress_callback,
                "提示词超出当前模型预算，已按上下文窗口自动裁剪。",
            )
        self._emit(progress_callback, "已发送请求，等待模型返回...")

        task_id = self._begin_task(
            runtime,
            prepared,
            original_prompt=prompt or "",
            original_context=context_messages,
            task_meta=task_meta,
        )
        queue = self._task_queue_or_none()
        if task_id and queue is not None:
            queue.start_task(task_id)

        started = time.monotonic()
        try:
            if runtime.api_type == "gemini":
                output, usage, attempts = self._generate_gemini(
                    runtime, prepared, progress_callback, task_id
                )
            else:
                output, usage, attempts = self._generate_openai(
                    runtime, prepared, progress_callback, task_id
                )
        except Exception as exc:  # noqa: BLE001 - 保持原有“错误也返回字符串”的契约
            message = self._friendly_error(exc)
            logger.error("文本生成失败: %s", exc, exc_info=True)
            self._fail_task(task_id, message)
            return f"> **生成失败:** {message}"

        duration_ms = int((time.monotonic() - started) * 1000)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        if not completion_tokens:
            completion_tokens = self._estimator.estimate(output, runtime.model_key)
        logger.info(
            "生成完成: model=%s, %d ms, usage=%s",
            runtime.model_key,
            duration_ms,
            usage or "-",
        )
        self._complete_task(
            task_id,
            output,
            usage,
            estimated_output_tokens=completion_tokens,
            attempts=attempts,
        )
        # 用真实 usage 校准估算系数
        actual_prompt_tokens = int(usage.get("prompt_tokens") or 0)
        if actual_prompt_tokens:
            self._calibrate(
                runtime.model_key,
                prepared.estimated_input_tokens,
                actual_prompt_tokens,
            )
        self._last_usage = dict(usage)
        return output

    def _friendly_error(self, exc: Exception) -> str:
        message = str(exc)
        if "timeout" in message.lower():
            return (
                "请求超时（网络缓慢或模型响应时间过长）。建议增大超时时间或检查网络。\n"
                f"详细信息: {message}"
            )
        return f"{message}\n> 请检查网络、API Key 或模型配置。"

    # -- OpenAI 兼容接口 ---------------------------------------------
    def _generate_openai(
        self,
        runtime: RuntimeModel,
        prepared: PreparedRequest,
        progress_callback,
        task_id: str | None,
    ) -> tuple[str, dict, list[dict]]:
        client = self._openai_client(runtime)
        kwargs = self._completion_kwargs(runtime, prepared.budget)
        messages = list(prepared.messages)
        attempts: list[dict] = []
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        response = self._create_completion(
            client, runtime, messages, kwargs, progress_callback
        )
        self._accumulate_usage(usage_total, getattr(response, "usage", None))

        full_result = ""
        continuation_round = 0
        while True:
            choice = response.choices[0]
            part = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", None)
            full_result += part

            should_continue, continue_reason = self._should_continue_response(
                part, finish_reason
            )
            if not self.auto_continue_enabled:
                should_continue = False
            logger.info(
                "本轮返回: finish_reason=%s, part_len=%s, continue=%s, reason=%s",
                finish_reason,
                len(part),
                should_continue,
                continue_reason,
            )
            if not should_continue:
                break
            if continuation_round >= self.auto_continue_max_rounds:
                logger.warning("达到自动续请求上限，返回当前累计结果。")
                self._emit(progress_callback, "已达到自动续请求上限，返回当前结果。")
                break

            continuation_round += 1
            attempts.append(
                {"round": continuation_round, "reason": continue_reason}
            )
            self._emit(
                progress_callback,
                f"检测到返回可能未完整（{continue_reason}），正在自动续请求 "
                f"{continuation_round}/{self.auto_continue_max_rounds} ...",
            )
            if task_id:
                queue = self._task_queue_or_none()
                if queue is not None:
                    queue.add_attempt(task_id, continue_reason)

            messages.append({"role": "assistant", "content": part})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "你上一条回复可能被截断。请从上一条回复最后一句继续输出，"
                        "不要重复已经输出的内容；只输出续写内容，不要解释。"
                    ),
                }
            )
            # 续写会不断累积上下文，按预算裁剪早期消息
            history_budget = prepared.budget.history_budget(
                self._estimator.estimate(prepared.system_prompt, runtime.model_key)
            )
            factor = self._estimator.factor_for(runtime.model_key)
            if (
                self._estimator.estimate_messages(messages[1:], runtime.model_key)
                > history_budget
            ):
                trimmed, _ = truncate_messages_to_budget(
                    messages[1:], history_budget, keep_recent=4, scale=factor
                )
                messages = [messages[0], *trimmed]

            response = self._create_completion(
                client, runtime, messages, kwargs, progress_callback
            )
            self._accumulate_usage(usage_total, getattr(response, "usage", None))

        logger.info(
            "========== %s 合并后返回 ==========\n%s\n==================================",
            runtime.model_key,
            full_result,
        )
        return full_result, usage_total, attempts

    def _create_completion(
        self,
        client: OpenAI,
        runtime: RuntimeModel,
        messages: list[dict],
        kwargs: dict,
        progress_callback,
    ):
        try:
            return client.chat.completions.create(
                model=runtime.model_key, messages=messages, **kwargs
            )
        except Exception as first_error:
            message = str(first_error)
            affordable = self._extract_affordable_tokens(message)
            if affordable is None:
                raise
            current = int(kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 0)
            retry_tokens = max(256, min(current, affordable - 128))
            if retry_tokens >= current:
                raise
            logger.warning(
                "检测到 credits/token 限制，自动降级重试: max_tokens %s -> %s",
                current,
                retry_tokens,
            )
            self._emit(
                progress_callback,
                f"检测到额度限制，自动将 max_tokens 降为 {retry_tokens} 重试。",
            )
            retry_kwargs = dict(kwargs)
            if "max_completion_tokens" in retry_kwargs:
                retry_kwargs["max_completion_tokens"] = retry_tokens
            else:
                retry_kwargs["max_tokens"] = retry_tokens
            return client.chat.completions.create(
                model=runtime.model_key, messages=messages, **retry_kwargs
            )

    @staticmethod
    def _accumulate_usage(total: dict, usage) -> None:
        if usage is None:
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None)
            if value:
                total[key] = int(total.get(key, 0)) + int(value)

    # -- Gemini 接口 --------------------------------------------------
    def _generate_gemini(
        self,
        runtime: RuntimeModel,
        prepared: PreparedRequest,
        progress_callback,
        task_id: str | None,
    ) -> tuple[str, dict, list[dict]]:
        from google import genai

        genai.configure(api_key=runtime.api_key)
        history = []
        for message in prepared.messages[1:-1]:
            role = "user" if message.get("role") == "user" else "model"
            history.append({"role": role, "parts": [message.get("content", "")]})

        model = genai.GenerativeModel(
            model_name=runtime.model_key,
            system_instruction=prepared.system_prompt,
        )
        chat = model.start_chat(history=history)
        generation_config = runtime.gemini_generation_config()
        if not generation_config.get("max_output_tokens"):
            generation_config["max_output_tokens"] = prepared.budget.max_output_tokens

        attempts: list[dict] = []
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        full_result = ""
        current_prompt = prepared.user_prompt
        continuation_round = 0

        while True:
            response = chat.send_message(
                current_prompt, generation_config=generation_config
            )
            part = getattr(response, "text", "") or ""
            full_result += part
            self._accumulate_gemini_usage(usage_total, response)

            finish_reason = None
            try:
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    finish_reason = str(getattr(candidates[0], "finish_reason", None))
            except Exception:  # pragma: no cover
                finish_reason = None

            should_continue, continue_reason = self._should_continue_response(
                part, finish_reason
            )
            if not self.auto_continue_enabled:
                should_continue = False
            logger.info(
                "Gemini 本轮返回: finish_reason=%s, part_len=%s, continue=%s, reason=%s",
                finish_reason,
                len(part),
                should_continue,
                continue_reason,
            )
            if not should_continue:
                break
            if continuation_round >= self.auto_continue_max_rounds:
                logger.warning("Gemini 达到自动续请求上限，返回当前累计结果。")
                break
            continuation_round += 1
            attempts.append({"round": continuation_round, "reason": continue_reason})
            self._emit(
                progress_callback,
                f"检测到返回可能未完整（{continue_reason}），正在自动续请求 "
                f"{continuation_round}/{self.auto_continue_max_rounds} ...",
            )
            if task_id:
                queue = self._task_queue_or_none()
                if queue is not None:
                    queue.add_attempt(task_id, continue_reason)
            current_prompt = (
                "你上一条回复可能被截断。请从上一条回复最后一句继续输出，"
                "不要重复已经输出的内容；只输出续写内容，不要解释。"
            )

        return full_result, usage_total, attempts

    @staticmethod
    def _accumulate_gemini_usage(total: dict, response) -> None:
        metadata = getattr(response, "usage_metadata", None)
        if metadata is None:
            return
        prompt_tokens = getattr(metadata, "prompt_token_count", 0) or 0
        completion_tokens = getattr(metadata, "candidates_token_count", 0) or 0
        total["prompt_tokens"] = int(total.get("prompt_tokens", 0)) + int(prompt_tokens)
        total["completion_tokens"] = int(total.get("completion_tokens", 0)) + int(
            completion_tokens
        )
        total["total_tokens"] = total["prompt_tokens"] + total["completion_tokens"]

    # -- 图像生成 -----------------------------------------------------
    def generate_image(self, prompt: str, save_path: str | None = None) -> str:
        img_cfg = self.config.get("image_api", {}) or {}
        img_type = str(img_cfg.get("type", "openai")).lower()
        img_model = str(img_cfg.get("model", "dall-e-3"))
        try:
            if img_type == "openai":
                client = OpenAI(
                    api_key=img_cfg.get("api_key") or "sk-placeholder",
                    base_url=img_cfg.get("base_url") or None,
                    http_client=self._http_client(),
                )
                response = client.images.generate(
                    model=img_model,
                    prompt=prompt,
                    size="1024x1024",
                    n=1,
                )
                image_url = response.data[0].url
                logger.info("图像生成成功: %s", image_url)
                return image_url
            logger.warning("图像模型类型暂不支持: %s", img_type)
            return "/images/placeholder.png"
        except Exception as exc:  # noqa: BLE001
            logger.error("图像生成失败: %s", exc)
            return "/images/error_placeholder.png"


def test_runtime(runtime: RuntimeModel, prompt: str = "用一句话介绍你自己。") -> tuple[bool, str]:
    """对指定模型做一次最小请求，用于设置界面的“测试连接”。

    返回 ``(是否成功, 说明文本)``，不会写入任务队列。
    """
    if runtime is None:
        return False, "没有可用的模型配置。"
    started = time.monotonic()
    try:
        if runtime.api_type == "gemini":
            from google import genai

            genai.configure(api_key=runtime.api_key)
            model = genai.GenerativeModel(model_name=runtime.model_key)
            response = model.generate_content(
                prompt,
                generation_config={"max_output_tokens": 64},
            )
            text = getattr(response, "text", "") or ""
        else:
            client = OpenAI(
                api_key=runtime.api_key or "sk-placeholder",
                base_url=runtime.base_url or None,
                timeout=min(runtime.timeout, 60),
            )
            kwargs = runtime.request_params()
            kwargs.pop("max_tokens", None)
            if (
                runtime.max_tokens_field() == "max_completion_tokens"
                and "api.openai.com" in (runtime.base_url or "")
            ):
                kwargs["max_completion_tokens"] = 64
            else:
                kwargs["max_tokens"] = 64
            response = client.chat.completions.create(
                model=runtime.model_key,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            text = response.choices[0].message.content or ""
        elapsed = int((time.monotonic() - started) * 1000)
        return True, f"连接成功（{elapsed} ms）：{text.strip()[:200]}"
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - started) * 1000)
        return False, f"连接失败（{elapsed} ms）：{exc}"
