import asyncio
import logging
import re
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from .utils import get_system_config_async

logger = logging.getLogger(__name__)


def filter_think_tags(text: str, should_filter: bool = True) -> str:
    """
    移除<think></think>标签及其内容

    Args:
        text: 原始文本
        should_filter: 是否进行过滤

    Returns:
        str: 过滤后的文本
    """
    if not should_filter:
        return text

    # 移除think标签及其内容（支持多行和嵌套）
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # 清理多余的空白行
    text = re.sub(r"\n\s*\n", "\n", text)
    # 去除首尾空白
    return text.strip()


async def _get_openai_client():
    """获取配置好的OpenAI客户端"""
    config = await get_system_config_async()
    return AsyncOpenAI(api_key=config.llm_api_key, base_url=config.llm_api_base)


async def call_llm_simple(
    user_input: str, conversation_history: list[dict] = None
) -> str:
    """
    简单LLM调用（不使用流式）

    Args:
        user_input: 用户输入
        conversation_history: 对话历史

    Returns:
        LLM响应文本
    """
    if conversation_history is None:
        conversation_history = []

    config = await get_system_config_async()
    client = await _get_openai_client()

    # 构建消息列表
    messages = []

    # 添加系统提示
    messages.append({"role": "system", "content": config.ai_system_prompt})

    # 添加历史对话
    for conv in conversation_history:
        messages.append({"role": "user", "content": conv["user"]})
        messages.append({"role": "assistant", "content": conv["assistant"]})

    # 添加当前用户输入（根据配置决定是否添加/nothink前缀）
    user_content = user_input
    if config.use_nothink_prefix:
        user_content = "/nothink " + user_input
    messages.append({"role": "user", "content": user_content})

    try:
        response = await client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            stream=False,
        )

        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content

            # 根据配置决定是否过滤think标签
            filtered_content = filter_think_tags(content, config.filter_think_tags)

            return (
                filtered_content.strip()
                if filtered_content.strip()
                else "抱歉，我无法理解您的问题。"
            )
        else:
            logger.error(f"[非流式调用] LLM响应格式错误: {response}")
            return "抱歉，我无法理解您的问题。"

    except Exception as e:
        logger.error(f"[非流式调用] LLM API请求失败: {e}")
        return "服务暂时不可用，请稍后再试。"


async def call_llm_stream(
    user_input: str, conversation_history: list[dict] = None
) -> AsyncGenerator[str, None]:
    """
    流式LLM调用

    Args:
        user_input: 用户输入
        conversation_history: 对话历史

    Yields:
        LLM响应文本片段
    """
    if conversation_history is None:
        conversation_history = []

    config = await get_system_config_async()
    client = await _get_openai_client()

    # 构建消息列表
    messages = []

    # 添加系统提示
    messages.append({"role": "system", "content": config.ai_system_prompt})

    # 添加历史对话
    for conv in conversation_history:
        messages.append({"role": "user", "content": conv["user"]})
        messages.append({"role": "assistant", "content": conv["assistant"]})

    # 添加当前用户输入（根据配置决定是否添加/nothink前缀）
    user_content = user_input
    if config.use_nothink_prefix:
        user_content = "/nothink " + user_input
    messages.append({"role": "user", "content": user_content})

    try:
        stream = await client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            stream=True,
        )

        chunk_count = 0

        # 实时流式处理 - 每收到chunk就立即yield
        async for chunk in stream:
            chunk_count += 1

            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta

                if delta.content:
                    content = delta.content

                    yield content  # 立即yield，不等待收集完成

        if chunk_count == 0:
            logger.warning("[流式调用] 没有收到任何chunks")
            yield "抱歉，我现在无法回答您的问题。"

    except Exception as e:
        logger.error(f"[流式调用] LLM API流式请求失败: {e}")
        import traceback

        logger.error(f"[流式调用] 详细错误信息: {traceback.format_exc()}")
        yield "服务暂时不可用，请稍后再试。"


async def test_llm_connection() -> dict[str, any]:
    """
    测试LLM连接

    Returns:
        测试结果字典
    """
    config = await get_system_config_async()
    client = await _get_openai_client()

    # 简单的测试消息（根据配置决定是否添加/nothink前缀）
    test_content = "请回复'连接测试成功'"
    if config.use_nothink_prefix:
        test_content = "/nothink " + test_content
    messages = [
        {"role": "system", "content": "你是一个测试助手。"},
        {"role": "user", "content": test_content},
    ]

    try:
        start_time = asyncio.get_event_loop().time()

        response = await client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=0.1,
            max_tokens=50,
            stream=False,
            timeout=15,
        )

        end_time = asyncio.get_event_loop().time()
        response_time = round((end_time - start_time) * 1000, 2)  # 毫秒

        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content

            # 根据配置决定是否过滤think标签
            filtered_content = filter_think_tags(content, config.filter_think_tags)

            return {
                "success": True,
                "response_time": response_time,
                "model": config.llm_model,
                "api_base": config.llm_api_base,
                "response": filtered_content.strip(),
                "message": "LLM连接测试成功",
            }
        else:
            logger.error(f"[连接测试] 响应格式错误: {response}")
            return {
                "success": False,
                "response_time": response_time,
                "error": "响应格式错误",
                "details": str(response),
            }

    except Exception as e:
        logger.error(f"[连接测试] 连接失败: {e}")
        import traceback

        logger.error(f"[连接测试] 详细错误信息: {traceback.format_exc()}")
        return {"success": False, "error": "连接失败", "details": str(e)}
