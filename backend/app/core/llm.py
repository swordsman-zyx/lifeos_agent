from langchain_openai import ChatOpenAI

from .config import settings


def create_llm(callbacks=None):
    if not settings.openai_api_key.strip():
        raise ValueError("缺少 OPENAI_API_KEY，请在项目根目录 .env 配置 OpenAI 兼容模型的密钥")
    thinking = settings.openai_enable_thinking
    if thinking is None and settings.openai_model.lower().startswith("qwen"):
        thinking = False
    return ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        temperature=0,
        timeout=45,
        max_retries=1,
        callbacks=callbacks,
        extra_body={"enable_thinking": thinking} if thinking is not None else None,
    )
