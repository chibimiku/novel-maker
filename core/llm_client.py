import os
import logging
from datetime import datetime
from openai import OpenAI
from google import genai

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
        self._init_text_client()
        self._init_image_client()

    def _init_text_client(self):
        text_cfg = self.config.get("text_api", {})
        self.text_type = text_cfg.get("type", "openai").lower()
        self.text_model_name = text_cfg.get("model", "gpt-4o")
        # 【修改点】：强化默认的系统人设约束，防止生成废话
        default_sys_prompt = "你是一个专业的AI小说家。你的输出必须纯粹是小说情节文本，严禁包含任何前言、后语、剧情解释或'已为您生成'之类的助手客套话。"
        self.system_instruction = text_cfg.get("instructions", default_sys_prompt)
        
        if self.text_type == "openai":
            # 兼容 OpenAI 格式的接口 (如 DeepSeek, Moonshot 等，只需修改 base_url)
            self.text_client = OpenAI(
                api_key=text_cfg.get("api_key"),
                base_url=text_cfg.get("base_url")
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

    def _init_image_client(self):
        img_cfg = self.config.get("image_api", {})
        self.img_type = img_cfg.get("type", "openai").lower()
        self.img_model_name = img_cfg.get("model", "dall-e-3")
        
        if self.img_type == "openai":
            self.img_client = OpenAI(
                api_key=img_cfg.get("api_key"),
                base_url=img_cfg.get("base_url")
            )
        elif self.img_type == "gemini":
            # Gemini 目前的图像生成通常通过 Vertex AI 或特定的 REST 端点，这里使用通用占位或后续接入
            logger.info("已配置 Gemini 图像模型类型。")
            # 视具体可用的 Gemini Image API 版本而定，可在此补充具体实现
        else:
             logger.warning(f"未知的图像模型类型配置: {self.img_type}")

    def generate_text(self, prompt: str, context_messages: list = None) -> str:
        """
        生成小说正文或概要
        """
        if context_messages is None:
            context_messages = []

        try:
            if self.text_type == "openai":
                messages = [{"role": "system", "content": self.system_instruction}]
                messages.extend(context_messages)
                messages.append({"role": "user", "content": prompt})
                
                response = self.text_client.chat.completions.create(
                    model=self.text_model_name,
                    messages=messages,
                    temperature=0.7
                )
                
                # 【修改点】：提取文本，写入日志后再返回
                result = response.choices[0].message.content
                logger.info(f"\n========== {self.text_model_name} 原始返回 ==========\n{result}\n==================================================\n")
                return result

            elif self.text_type == "gemini":
                history = []
                for msg in context_messages:
                    role = "user" if msg["role"] == "user" else "model"
                    history.append({"role": role, "parts": [msg["content"]]})
                
                chat = self.gemini_text_model.start_chat(history=history)
                response = chat.send_message(prompt)
                
                # 【修改点】：提取文本，写入日志后再返回
                result = response.text
                logger.info(f"\n========== Gemini 原始返回 ==========\n{result}\n=======================================\n")
                return result
                
        except Exception as e:
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