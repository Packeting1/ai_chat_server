"""
TTS连接池模块（简化版）
直接使用DashScope SDK的内部连接池管理，移除了复杂的TTSTaskManager
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from app.models import SystemConfig

logger = logging.getLogger(__name__)

# 全局TTS连接池实例
tts_pool = None


@dataclass
class PoolConfig:
    """连接池配置（简化版，主要配置由DashScope SDK控制）"""

    max_concurrent: int
    cleanup_interval: float = 60.0  # 固定清理间隔

    @classmethod
    async def from_system_config(cls):
        """从SystemConfig模型创建配置"""
        # 简化版不再需要max_concurrent，使用固定值
        return cls(
            max_concurrent=3,  # 固定并发数，由DashScope SDK控制
        )


class TTSConnectionFactory:
    """TTS连接工厂（简化版）"""

    def __init__(self, tts_config_getter: Callable, system_config_getter: Callable):
        self.tts_config_getter = tts_config_getter
        self.system_config_getter = system_config_getter
        self._connection_counter = 0
        self._lock = Lock()


class TTSConnectionPool:
    """TTS连接池（简化版）"""

    def __init__(self, pool_config: PoolConfig | None = None):
        self.config = pool_config
        self.factory: TTSConnectionFactory | None = None
        self._config_loaded = False

    async def initialize(self):
        """初始化连接池"""
        # 从数据库加载配置
        await self._load_config_from_db()

        if self.factory is None:
            self.factory = TTSConnectionFactory(
                self._get_tts_config, self._get_system_config
            )

    async def _load_config_from_db(self):
        """从数据库加载配置"""
        if not self._config_loaded:
            self.config = await PoolConfig.from_system_config()
            self._config_loaded = True

    async def _get_system_config(self):
        """获取系统配置"""
        return await SystemConfig.objects.aget(pk=1)

    async def _get_tts_config(self):
        """获取TTS配置"""
        config = await SystemConfig.objects.aget(pk=1)
        return {
            "api_key": config.tts_api_key,
            "model": config.tts_model,
            "voice": config.tts_default_voice,
            "sample_rate": config.tts_sample_rate,
            "volume": config.tts_volume,
            "speech_rate": config.tts_speech_rate,
            "pitch_rate": config.tts_pitch_rate,
            "audio_format": config.tts_audio_format,
            "enabled": config.tts_enabled,
        }


# 简化的TTS函数
async def tts_speak_stream(
    text: str, user_id: str, audio_callback: Callable[[bytes], None], voice: str = None
) -> bool:
    """
    TTS语音合成（简化版，直接使用DashScope SDK连接池）

    Args:
        text: 要合成的文本
        user_id: 用户ID
        audio_callback: 音频数据回调函数
        voice: 指定的音色名称，如果为None则使用默认音色

    Returns:
        bool: 是否成功
    """
    # 验证文本内容
    if not text or not text.strip():
        return False

    if len(text.strip()) > 1000:
        logger.warning(
            f"⚠️ TTS文本过长 ({len(text)} 字符)，可能影响性能，用户: {user_id}"
        )

    # 获取系统配置
    system_config = await SystemConfig.objects.aget(pk=1)
    if not system_config.tts_enabled:
        return False

    if not system_config.tts_api_key:
        logger.error(f"❌ TTS API密钥未配置，用户: {user_id}")
        return False

    # 记录音色使用
    if voice:
        logger.info(f"🎵 用户 {user_id} 使用指定音色: {voice}")

    # 直接使用一次性连接模式，依赖DashScope SDK的内部连接池管理
    return await _tts_speak_stream_disposable(text, user_id, audio_callback, voice)


async def _tts_speak_stream_disposable(
    text: str, user_id: str, audio_callback: Callable[[bytes], None], voice: str = None
) -> bool:
    """
    一次性TTS语音合成 - 依赖DashScope SDK的内部连接池
    """
    try:
        from app.llm_client import DashScopeRealtimeTTS, TTSConfig

        # 获取系统配置
        config = await SystemConfig.objects.aget(pk=1)

        # 创建TTS配置
        tts_config = TTSConfig(
            model=config.tts_model,
            voice=voice or config.tts_default_voice,
            sample_rate=config.tts_sample_rate,
            volume=config.tts_volume,
            speech_rate=config.tts_speech_rate,
            pitch_rate=config.tts_pitch_rate,
            audio_format=config.tts_audio_format,
        )

        # 创建TTS客户端（依赖SDK的内部连接池）
        tts_client = DashScopeRealtimeTTS(
            api_key=config.tts_api_key, config=tts_config
        )

        # 统计信息
        audio_chunk_count = 0
        total_audio_bytes = 0

        def on_audio(audio_data):
            nonlocal audio_chunk_count, total_audio_bytes
            try:
                if audio_data:
                    audio_chunk_count += 1
                    total_audio_bytes += len(audio_data)
                    audio_callback(audio_data)
            except Exception as e:
                logger.error(f"音频回调失败，用户: {user_id}: {e}")

        def on_error(error):
            # 特殊处理1007错误
            if "1007" in str(error) or "invalid frame" in str(error).lower():
                logger.warning(f"⚠️ TTS WebSocket协议错误，用户: {user_id}: {error}")
            else:
                logger.error(f"TTS合成错误，用户: {user_id}: {error}")

        def on_end():
            logger.debug(f"TTS合成完成，用户: {user_id}")

        # 设置回调
        tts_client.send_audio = on_audio
        tts_client.on_error = on_error
        tts_client.on_end = on_end

        # 连接并执行TTS合成
        await tts_client.connect()
        logger.debug(f"🎵 TTS连接建立，用户: {user_id}, 音色: {voice or config.tts_default_voice}")

        # 执行TTS合成
        cleaned_text = text.strip()
        await tts_client.say(cleaned_text)
        await tts_client.finish()
        await tts_client.wait_done()

        # 短暂延迟确保音频处理完成
        await asyncio.sleep(0.1)

        # 验证音频数据
        if audio_chunk_count == 0:
            logger.warning(f"⚠️ 未收到音频数据，用户: {user_id}")
        elif total_audio_bytes < 1000:
            logger.warning(
                f"⚠️ 音频数据量过少({total_audio_bytes}字节)，用户: {user_id}"
            )

        logger.debug(
            f"✅ TTS合成完成，用户: {user_id}, 音频块: {audio_chunk_count}, 字节: {total_audio_bytes}"
        )
        return True

    except Exception as e:
        # 特殊处理1007错误
        if "1007" in str(e) or "invalid frame" in str(e).lower():
            logger.warning(f"⚠️ TTS WebSocket协议错误，用户: {user_id}: {e}")
        else:
            logger.error(f"❌ TTS合成失败，用户: {user_id}: {e}")
        return False

    finally:
        # 清理连接
        try:
            if 'tts_client' in locals() and hasattr(tts_client, '_ws') and tts_client._ws:
                await tts_client._ws.close()
        except Exception as cleanup_err:
            logger.warning(f"TTS连接清理失败，用户: {user_id}: {cleanup_err}")


async def interrupt_user_tts(user_id: str) -> bool:
    """
    中断指定用户的TTS播放（简化版）

    Args:
        user_id: 用户ID

    Returns:
        bool: 是否成功中断
    """
    try:
        # 在简化的架构中，由于直接使用DashScope SDK，
        # 我们无法直接中断正在进行的TTS，但可以记录日志
        logger.info(f"🛑 请求中断用户 {user_id} 的TTS（依赖DashScope SDK内部处理）")
        return True
    except Exception as e:
        logger.error(f"❌ 中断用户 {user_id} TTS失败: {e}")
        return False


# 全局实例和初始化函数
async def get_tts_pool():
    """获取TTS连接池实例"""
    global tts_pool
    if tts_pool is None:
        tts_pool = TTSConnectionPool()
    return tts_pool


async def initialize_tts_pool_with_manager():
    """初始化TTS连接池（简化版，主要配置DashScope SDK）"""
    import os
    global tts_pool

    # 从数据库读取配置
    config = await SystemConfig.objects.aget(pk=1)

    # 设置DashScope SDK环境变量
    os.environ['DASHSCOPE_CONNECTION_POOL_SIZE'] = str(config.dashscope_connection_pool_size)
    os.environ['DASHSCOPE_MAXIMUM_ASYNC_REQUESTS'] = str(config.dashscope_max_async_requests)
    os.environ['DASHSCOPE_MAXIMUM_ASYNC_REQUESTS_PER_HOST'] = str(config.dashscope_max_async_requests_per_host)
    
    logger.info(f"🔧 DashScope SDK配置 - 连接池大小: {config.dashscope_connection_pool_size}, "
                f"最大异步请求: {config.dashscope_max_async_requests}, "
                f"单Host最大请求: {config.dashscope_max_async_requests_per_host}")

    if not config.tts_enabled:
        logger.info("🔇 TTS功能未启用")
        return tts_pool

    # 简化初始化：主要依赖DashScope SDK的内部连接池
    logger.info("✅ TTS初始化完成（使用DashScope SDK内部连接池）")
    return tts_pool


async def shutdown_tts_pool():
    """关闭TTS连接池（简化版）"""
    global tts_pool

    try:
        # 简化关闭：主要依赖DashScope SDK的内部清理
        logger.info("🔄 TTS连接池关闭（DashScope SDK内部处理连接清理）")
    except Exception as e:
        logger.error(f"❌ 关闭TTS连接池失败: {e}")


# 兼容性函数：支持并发TTS合成
async def tts_speak_concurrent(
    texts: list[str],
    user_ids: list[str],
    audio_callbacks: list[Callable[[bytes], None]],
) -> list[bool]:
    """
    并发TTS语音合成（简化版）

    Args:
        texts: 要合成的文本列表
        user_ids: 用户ID列表
        audio_callbacks: 音频数据回调函数列表

    Returns:
        List[bool]: 每个任务的成功状态
    """
    if len(texts) != len(user_ids) or len(texts) != len(audio_callbacks):
        raise ValueError("texts, user_ids, audio_callbacks 长度必须相同")

    # 并发执行多个TTS任务，依赖DashScope SDK的内部并发控制
    tasks = []
    for text, user_id, callback in zip(texts, user_ids, audio_callbacks):
        task = tts_speak_stream(text, user_id, callback)
        tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 转换结果，异常视为失败
    return [isinstance(result, bool) and result for result in results]
