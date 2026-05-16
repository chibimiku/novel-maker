import os
import logging
import re
import json
from datetime import datetime
from openai import OpenAI
from google import genai
import httpx

# ================= 新增：日志目录与文件配置 =================
log_dir = "log"
os.makedirs(log_dir, exist_ok=True) # 如果 log 目录不存在则自动创建

# 按天生成日志文件，例如：log/llm_output_20260313.log
log_filename = os.path.join(log_dir, f"llm_output_{datetime.now().strftime('%Y%m%d')}.log")

# 配置日志：同时输出到文件和控制台，并指定 utf-8 编码防止中文乱码
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
# =========================================================

class LLMClient:
    def __init__(self, config: dict):
        """
        初始化 LLM 客户端
        :param config: 包含 text_api 和 image_api 配置的字典（通常从 setting.json 读取）
        """
        self.config = config
        # 提取代理配置
        self.proxy_cfg = self.config.get("proxy", {})
        self.proxy_enabled = self.proxy_cfg.get("enabled", False)
        self.proxy_url = self.proxy_cfg.get("url", "http://127.0.0.1:7890")

        # 针对 Gemini 等部分依赖环境变量的库，设置全局代理环境变量
        if self.proxy_enabled and self.proxy_url:
            os.environ["HTTP_PROXY"] = self.proxy_url
            os.environ["HTTPS_PROXY"] = self.proxy_url
        else:
            os.environ.pop("HTTP_PROXY", None)
            os.environ.pop("HTTPS_PROXY", None)
        self._init_text_client()
        self._init_image_client()

    def _emit_progress(self, progress_callback, message: str):
        if callable(progress_callback):
            try:
                progress_callback(message)
            except Exception:
                logger.debug("progress_callback 执行失败", exc_info=True)

    def _build_text_endpoint_desc(self) -> str:
        text_cfg = self.config.get("text_api", {}) if isinstance(self.config, dict) else {}
        text_type = str(text_cfg.get("type", self.text_type if hasattr(self, "text_type") else "unknown")).strip().lower()
        model = str(text_cfg.get("model", getattr(self, "text_model_name", ""))).strip() or "-"
        if text_type == "openai":
            base_url = str(text_cfg.get("base_url", "")).strip() or "(未配置)"
            return f"type=openai, model={model}, base_url={base_url}"
        if text_type == "gemini":
            return f"type=gemini, model={model}, endpoint=google-genai-sdk"
        return f"type={text_type or 'unknown'}, model={model}"

    def _init_text_client(self):
        text_cfg = self.config.get("text_api", {})
        self.text_generation_mode = str(
            text_cfg.get(
                "text_generation_mode",
                self.config.get("text_generation_mode", "normal"),
            )
        ).strip().lower()
        if self.text_generation_mode not in {"normal", "nsfw"}:
            self.text_generation_mode = "normal"
        self.text_type = text_cfg.get("type", "openai").lower()
        self.text_model_name = text_cfg.get("model", "gpt-4o")
        self.text_max_tokens = self._resolve_text_max_tokens(
            text_cfg.get("max_tokens"),
            text_cfg.get("model_context_size"),
        )
        self.auto_continue_enabled = bool(
            text_cfg.get("auto_continue_on_incomplete", True)
        )
        try:
            self.auto_continue_max_rounds = int(
                text_cfg.get("auto_continue_max_rounds", 3)
            )
        except Exception:
            self.auto_continue_max_rounds = 3
        self.auto_continue_max_rounds = max(0, min(10, self.auto_continue_max_rounds))
        # 【修改点】：强化默认的系统人设约束，防止生成废话
        default_sys_prompt = "你是一个专业的AI小说家。你的输出必须纯粹是小说情节文本，严禁包含任何前言、后语、剧情解释或'已为您生成'之类的助手客套话。"
        self.system_instruction = text_cfg.get("instructions", default_sys_prompt)
        # 概要生成系统指令
        default_summary_sys_prompt = "你是一个专业的小说编辑，擅长为小说节点生成精炼、准确的概要。"
        self.summary_system_instruction = text_cfg.get("summary_instructions", default_summary_sys_prompt)
        
        if self.text_type == "openai":
            # 构建 httpx 客户端用于代理
            http_client = None
            if self.proxy_enabled and self.proxy_url:
                http_client = httpx.Client(proxy=self.proxy_url)
            # 兼容 OpenAI 格式的接口 (如 DeepSeek, Moonshot 等，只需修改 base_url)
            self.text_client = OpenAI(
                api_key=text_cfg.get("api_key"),
                base_url=text_cfg.get("base_url"),
                timeout=text_cfg.get("timeout", 120),
                http_client=http_client  # 注入定制的 http 客户端
            )
        elif self.text_type == "gemini":
            # 原生 Gemini 接口
            genai.configure(api_key=text_cfg.get("api_key"))
            self.gemini_text_model = genai.GenerativeModel(
                model_name=self.text_model_name,
                system_instruction=self.system_instruction
            )
        else:
            logger.warning(f"未知的文本模型类型配置: {self.text_type}")

    def _resolve_text_max_tokens(self, configured_value, model_context_size) -> int:
        """
        解析文本生成 max_tokens。
        默认策略：若未配置则取 min(8192, model_context_size 的 25%)，并设置硬边界防止异常大值。
        """
        resolved = None
        try:
            if configured_value is not None:
                resolved = int(configured_value)
        except Exception:
            resolved = None

        if resolved is None or resolved <= 0:
            try:
                context_size = int(model_context_size)
            except Exception:
                context_size = 8192
            if context_size <= 0:
                context_size = 8192
            resolved = int(context_size * 0.25)

        # 防止过小导致频繁截断，也防止过大导致 OpenRouter 按高上限校验 credits。
        return max(256, min(8192, resolved))

    def _estimate_token_count(self, text: str) -> int:
        """
        粗略估算 token 数：
        - 中文按 1 字约 1 token
        - 英文/数字按约 4 字符 1 token
        """
        if not text:
            return 0
        cjk_count = 0
        for ch in text:
            code_point = ord(ch)
            if 0x4E00 <= code_point <= 0x9FFF:
                cjk_count += 1
        non_cjk = max(0, len(text) - cjk_count)
        return cjk_count + int(non_cjk / 4) + 1

    def _calc_input_budget_tokens(self) -> int:
        """
        估算单次请求可用输入预算（不含输出 max_tokens）。
        """
        text_cfg = self.config.get("text_api", {})
        try:
            context_size = int(text_cfg.get("model_context_size", 8192))
        except Exception:
            context_size = 8192
        if context_size <= 0:
            context_size = 8192

        # 预留输出 token + 协议开销，输入预算不得超过窗口上限；
        # 同时加一层软上限，避免在超长上下文模型上把提示词拉得过大。
        window_limited = max(1024, context_size - self.text_max_tokens - 1024)
        soft_cap = max(4096, self.text_max_tokens * 4)
        return min(window_limited, soft_cap)

    def _truncate_prompt_to_budget(self, prompt: str, budget_tokens: int) -> tuple[str, bool]:
        """
        按输入 token 预算裁剪用户提示词，优先保留前后文头尾信息。
        """
        if not prompt:
            return prompt, False
        current_tokens = self._estimate_token_count(prompt)
        if current_tokens <= budget_tokens:
            return prompt, False

        # 基于字符比例近似裁剪，保留头尾。
        ratio = budget_tokens / max(1, current_tokens)
        keep_chars = max(1200, int(len(prompt) * ratio))
        head_len = int(keep_chars * 0.7)
        tail_len = max(200, keep_chars - head_len)
        truncated = (
            prompt[:head_len].rstrip()
            + "\n\n...(中间内容已按预算压缩)...\n\n"
            + prompt[-tail_len:].lstrip()
        )
        # 二次保障：极端情况下继续缩小。
        while self._estimate_token_count(truncated) > budget_tokens and keep_chars > 800:
            keep_chars = int(keep_chars * 0.9)
            head_len = int(keep_chars * 0.7)
            tail_len = max(200, keep_chars - head_len)
            truncated = (
                prompt[:head_len].rstrip()
                + "\n\n...(中间内容已按预算压缩)...\n\n"
                + prompt[-tail_len:].lstrip()
            )
        return truncated, True

    def _extract_affordable_tokens(self, err_msg: str) -> int | None:
        """
        从 OpenRouter 的 402 文案中解析可承担 token 上限。
        典型格式：can only afford 60813
        """
        lower_msg = (err_msg or "").lower()
        marker = "can only afford"
        idx = lower_msg.find(marker)
        if idx < 0:
            return None

        start = idx + len(marker)
        while start < len(lower_msg) and lower_msg[start].isspace():
            start += 1

        end = start
        while end < len(lower_msg) and lower_msg[end].isdigit():
            end += 1

        if end <= start:
            return None

        try:
            value = int(lower_msg[start:end])
            return value if value > 0 else None
        except Exception:
            return None

    def _count_unmatched_brackets(self, text: str, left: str, right: str) -> int:
        """
        统计特定括号的未闭合数量（忽略字符串字面量中的括号）。
        """
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
        """
        基于输出内容做兜底判断（用于缺少 finish_reason 时）。
        """
        stripped = (text or "").rstrip()
        if not stripped:
            return True, "empty_output"

        # Markdown 代码块未闭合
        if stripped.count("```") % 2 == 1:
            return True, "unclosed_code_block"

        # JSON 风格输出未闭合：优先尝试 JSON 解析，再退化到括号平衡判断
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

        # 结尾出现明显未完成信号
        unfinished_tail = (
            "...",
            "…",
            "（未完",
            "(未完",
            "待续",
            "to be continued",
        )
        lower_stripped = stripped.lower()
        if any(lower_stripped.endswith(tail.lower()) for tail in unfinished_tail):
            return True, "unfinished_tail_marker"

        # 结尾为明显“未结束符号”
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
        # finish_reason 缺失/不可靠时做内容兜底判断
        return self._looks_incomplete_by_content(partial_text)

    def _init_image_client(self):
        img_cfg = self.config.get("image_api", {})
        self.img_type = img_cfg.get("type", "openai").lower()
        self.img_model_name = img_cfg.get("model", "dall-e-3")
        
        if self.img_type == "openai":
            # 构建 httpx 客户端用于代理
            http_client = None
            if self.proxy_enabled and self.proxy_url:
                http_client = httpx.Client(proxy=self.proxy_url)

            self.img_client = OpenAI(
                api_key=img_cfg.get("api_key"),
                base_url=img_cfg.get("base_url"),
                http_client=http_client  # 注入定制的 http 客户端
            )
        elif self.img_type == "gemini":
            # Gemini 目前的图像生成通常通过 Vertex AI 或特定的 REST 端点，这里使用通用占位或后续接入
            logger.info("已配置 Gemini 图像模型类型。")
            # 视具体可用的 Gemini Image API 版本而定，可在此补充具体实现
        else:
             logger.warning(f"未知的图像模型类型配置: {self.img_type}")

    def generate_text(
        self,
        prompt: str,
        context_messages: list = None,
        override_system_instruction: str = None,
        progress_callback=None,
    ) -> str:
        """
        生成文本。支持传入 override_system_instruction 覆盖默认的系统指令。
        """
        if context_messages is None:
            context_messages = []

        # 确定本次请求使用的 System Prompt
        current_sys_prompt = override_system_instruction if override_system_instruction else self.system_instruction

        try:
            # === 新增请求日志记录 ===
            proxy_status = f"[已启用代理: {self.proxy_url}]" if self.proxy_enabled else "[未启用代理]"
            logger.info(
                f"==> 准备发起文本生成请求 {proxy_status} | 模型: {self.text_model_name} | 模式: {self.text_generation_mode}"
            )
            endpoint_desc = self._build_text_endpoint_desc()
            logger.info(f"本次请求接口: {endpoint_desc}")
            logger.info(
                "自动续请求配置: "
                f"enabled={self.auto_continue_enabled}, "
                f"max_rounds={self.auto_continue_max_rounds}"
            )
            self._emit_progress(
                progress_callback,
                f"请求接口: {endpoint_desc}",
            )
            self._emit_progress(
                progress_callback,
                "已发送请求，等待模型返回...",
            )
            
            # 打印请求日志，方便后续排查
            logger.info("\n========== [Request 发送] System 指令 ==========")
            logger.info(current_sys_prompt)
            logger.info("========== [Request 发送] User 提示词 ==========")
            logger.info(prompt)
            if context_messages:
                logger.info("========== [Request 发送] Context 上下文 ==========")
                logger.info(context_messages)
            logger.info("==================================================\n")

            if self.text_type == "openai":
                messages = [{"role": "system", "content": current_sys_prompt}]
                messages.extend(context_messages)
                input_budget = self._calc_input_budget_tokens()
                compressed_prompt, prompt_trimmed = self._truncate_prompt_to_budget(
                    prompt, input_budget
                )
                messages.append({"role": "user", "content": compressed_prompt})

                logger.info(
                    f"本次请求参数: temperature=0.7, max_tokens={self.text_max_tokens}"
                )
                logger.info(
                    "本次预算估算: "
                    f"input_budget_tokens≈{input_budget}, "
                    f"system_tokens≈{self._estimate_token_count(current_sys_prompt)}, "
                    f"user_tokens≈{self._estimate_token_count(compressed_prompt)}, "
                    f"prompt_trimmed={prompt_trimmed}"
                )
                logger.info("\n========== [Request 实际发送 User Prompt(压缩后)] ==========")
                logger.info(compressed_prompt)
                logger.info("===========================================================\n")
                try:
                    response = self.text_client.chat.completions.create(
                        model=self.text_model_name,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=self.text_max_tokens,
                    )
                except Exception as first_err:
                    # OpenRouter 常见场景：402 提示 credits 不足以覆盖请求的 max_tokens。
                    # 这里尝试按服务端给出的可承担额度降级重试一次。
                    first_err_msg = str(first_err)
                    affordable_tokens = self._extract_affordable_tokens(first_err_msg)
                    if affordable_tokens is None:
                        raise

                    retry_max_tokens = max(256, min(self.text_max_tokens, affordable_tokens - 128))
                    if retry_max_tokens >= self.text_max_tokens:
                        raise

                    logger.warning(
                        "检测到 credits/token 限制，自动降级重试: "
                        f"max_tokens {self.text_max_tokens} -> {retry_max_tokens}"
                    )
                    response = self.text_client.chat.completions.create(
                        model=self.text_model_name,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=retry_max_tokens,
                    )

                full_result = ""
                current_response = response
                continuation_round = 0
                while True:
                    choice = current_response.choices[0]
                    current_part = choice.message.content or ""
                    finish_reason = getattr(choice, "finish_reason", None)
                    full_result += current_part

                    should_continue, continue_reason = self._should_continue_response(
                        current_part,
                        finish_reason,
                    )
                    if not self.auto_continue_enabled:
                        should_continue = False

                    logger.info(
                        "本轮返回状态: "
                        f"finish_reason={finish_reason}, "
                        f"part_len={len(current_part)}, "
                        f"should_continue={should_continue}, "
                        f"reason={continue_reason}"
                    )

                    if not should_continue:
                        break
                    if continuation_round >= self.auto_continue_max_rounds:
                        logger.warning(
                            "达到自动续请求上限，停止续请求并返回当前累计结果。"
                        )
                        self._emit_progress(
                            progress_callback,
                            "检测到可能未完整，但已达到自动续请求上限，返回当前结果。",
                        )
                        break

                    continuation_round += 1
                    self._emit_progress(
                        progress_callback,
                        f"检测到返回可能未完整（{continue_reason}），正在自动续请求 "
                        f"{continuation_round}/{self.auto_continue_max_rounds} ...",
                    )
                    logger.info(
                        "触发自动续请求: "
                        f"round={continuation_round}, reason={continue_reason}"
                    )

                    messages.append({"role": "assistant", "content": current_part})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "你上一条回复可能被截断。请从上一条回复最后一句继续输出，"
                                "不要重复已经输出的内容；只输出续写内容，不要解释。"
                            ),
                        }
                    )
                    current_response = self.text_client.chat.completions.create(
                        model=self.text_model_name,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=self.text_max_tokens,
                    )

                logger.info(
                    f"\n========== {self.text_model_name} 合并后返回 ==========\n"
                    f"{full_result}\n==================================================\n"
                )
                return full_result

            elif self.text_type == "gemini":
                history = []
                for msg in context_messages:
                    role = "user" if msg["role"] == "user" else "model"
                    history.append({"role": role, "parts": [msg["content"]]})
                
                # Gemini 的系统指令是在模型实例化时绑定的。如果有覆盖指令，就临时实例化一个新模型
                if override_system_instruction:
                    temp_model = genai.GenerativeModel(
                        model_name=self.text_model_name,
                        system_instruction=current_sys_prompt
                    )
                    chat = temp_model.start_chat(history=history)
                else:
                    chat = self.gemini_text_model.start_chat(history=history)
                    
                full_result = ""
                current_prompt = prompt
                continuation_round = 0
                while True:
                    response = chat.send_message(current_prompt)
                    result = response.text or ""
                    full_result += result

                    finish_reason = None
                    try:
                        candidates = getattr(response, "candidates", None) or []
                        if candidates:
                            finish_reason = str(
                                getattr(candidates[0], "finish_reason", None)
                            )
                    except Exception:
                        finish_reason = None

                    should_continue, continue_reason = self._should_continue_response(
                        result, finish_reason
                    )
                    if not self.auto_continue_enabled:
                        should_continue = False

                    logger.info(
                        "Gemini 本轮返回状态: "
                        f"finish_reason={finish_reason}, "
                        f"part_len={len(result)}, "
                        f"should_continue={should_continue}, "
                        f"reason={continue_reason}"
                    )

                    if not should_continue:
                        break
                    if continuation_round >= self.auto_continue_max_rounds:
                        logger.warning(
                            "Gemini 达到自动续请求上限，停止续请求并返回当前累计结果。"
                        )
                        self._emit_progress(
                            progress_callback,
                            "检测到可能未完整，但已达到自动续请求上限，返回当前结果。",
                        )
                        break

                    continuation_round += 1
                    self._emit_progress(
                        progress_callback,
                        f"检测到返回可能未完整（{continue_reason}），正在自动续请求 "
                        f"{continuation_round}/{self.auto_continue_max_rounds} ...",
                    )
                    current_prompt = (
                        "你上一条回复可能被截断。请从上一条回复最后一句继续输出，"
                        "不要重复已经输出的内容；只输出续写内容，不要解释。"
                    )

                logger.info(
                    f"\n========== Gemini 合并后返回 ==========\n{full_result}\n"
                    "=======================================\n"
                )
                return full_result
                
        except Exception as e:
            err_msg = str(e)
            # 【新增】超时失败处理
            if "timeout" in err_msg.lower():
                err_msg = f"请求超时（网络缓慢或模型响应时间过长）。建议在设置中增大超时时间或检查网络。\n详细信息: {err_msg}"
            else:
                err_msg = f"{err_msg}\n> 请检查网络、API Key 或模型配置。"
            logger.error(f"文本生成失败: {e}")
            return f"> **生成失败:** {e}\n> 请检查网络、API Key 或模型配置。"

    def generate_image(self, prompt: str, save_path: str = None) -> str:
        """
        生成小说插图
        :param prompt: 画面描述提示词
        :param save_path: 可选，如果提供则直接将图片下载并保存到本地路径
        :return: 图像的 URL 或者本地相对路径
        """
        try:
            if self.img_type == "openai":
                response = self.img_client.images.generate(
                    model=self.img_model_name,
                    prompt=prompt,
                    size="1024x1024",
                    quality="standard",
                    n=1,
                )
                image_url = response.data[0].url
                logger.info(f"图像生成成功: {image_url}")
                
                # 如果传入了 save_path，可以在这里添加 requests 下载代码并保存本地
                # if save_path:
                #    download_and_save_image(image_url, save_path)
                #    return save_path
                
                return image_url
                
            elif self.img_type == "gemini":
                 logger.warning("Gemini 图像生成暂未实现完整 SDK 调用。")
                 return "/images/placeholder.png"
                 
        except Exception as e:
            logger.error(f"图像生成失败: {e}")
            return "/images/error_placeholder.png"
