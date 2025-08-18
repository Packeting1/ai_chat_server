"""
TTS连接池模块
支持连接池模式和一次性连接模式的双模式TTS系统
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Dict, Optional

from app.models import SystemConfig

logger = logging.getLogger(__name__)

# 全局TTS连接池实例
tts_pool = None


@dataclass
class PooledTTSConnection:
    """池化的TTS连接"""
    client: object  # DashScopeRealtimeTTS实例
    voice: str
    created_at: float
    last_used: float
    in_use: bool = False
    user_id: Optional[str] = None
    error_count: int = 0
    max_error_count: int = 3
    max_idle_time: float = 300.0
    
    def mark_used(self):
        """标记连接被使用"""
        self.last_used = time.time()
    
    def mark_error(self):
        """标记连接错误"""
        self.error_count += 1
    
    def is_expired(self) -> bool:
        """检查连接是否过期"""
        return (time.time() - self.last_used) > self.max_idle_time
    
    def is_healthy(self) -> bool:
        """检查连接是否健康"""
        try:
            if self.error_count >= self.max_error_count:
                return False
            if not hasattr(self.client, 'ws') or not self.client.ws:
                return False
            return not self.client.ws.closed
        except Exception as e:
            logger.debug(f"连接健康检查异常: {e}")
            return False


@dataclass
class PoolConfig:
    """连接池配置"""
    max_connections: int = 10
    max_idle_time: float = 300.0
    cleanup_interval: float = 60.0
    max_error_count: int = 3

    @classmethod
    async def from_system_config(cls):
        """从SystemConfig模型创建配置"""
        config = await SystemConfig.objects.aget(pk=1)
        return cls(
            max_connections=config.tts_pool_max_connections,
            max_idle_time=config.tts_connection_max_idle_time,
            cleanup_interval=config.tts_pool_cleanup_interval,
            max_error_count=config.tts_connection_max_error_count,
        )


class TTSConnectionPool:
    """TTS连接池 - 支持真正的连接复用"""

    def __init__(self, pool_config: PoolConfig | None = None):
        self.config = pool_config
        self._config_loaded = False
        
        # 按音色分组的连接池
        self.connections: Dict[str, list[PooledTTSConnection]] = {}
        self.user_connections: Dict[str, PooledTTSConnection] = {}
        
        # 异步锁
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._shutdown = False

    async def initialize(self):
        """初始化连接池"""
        await self._load_config_from_db()
        
        # 启动清理任务
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("🔄 TTS连接池清理任务已启动")

    async def _load_config_from_db(self):
        """从数据库加载配置"""
        if not self._config_loaded:
            self.config = await PoolConfig.from_system_config()
            self._config_loaded = True

    async def get_connection(self, voice: str, user_id: str) -> Optional[object]:
        """获取TTS连接"""
        async with self._lock:
            # 检查用户是否已有连接
            if user_id in self.user_connections:
                conn = self.user_connections[user_id]
                if conn.voice == voice and conn.is_healthy():
                    conn.mark_used()
                    logger.debug(f"🔄 复用用户连接: {user_id}, 音色: {voice}")
                    return conn.client
                else:
                    # 音色不匹配或连接不健康，释放旧连接
                    await self._release_connection_internal(user_id)

            # 查找可用的空闲连接
            if voice in self.connections:
                for conn in self.connections[voice]:
                    if not conn.in_use and conn.is_healthy():
                        conn.in_use = True
                        conn.user_id = user_id
                        conn.mark_used()
                        self.user_connections[user_id] = conn
                        logger.debug(f"🔄 复用空闲连接: {user_id}, 音色: {voice}")
                        return conn.client

            # 创建新连接
            if self._total_connections() < self.config.max_connections:
                conn = await self._create_connection(voice, user_id)
                if conn:
                    logger.info(f"🆕 创建新TTS连接: {user_id}, 音色: {voice}")
                    return conn.client

            logger.warning(f"⚠️ TTS连接池已满，无法为用户 {user_id} 创建连接")
            return None

    async def release_connection(self, user_id: str):
        """释放用户连接"""
        async with self._lock:
            await self._release_connection_internal(user_id)

    async def _release_connection_internal(self, user_id: str):
        """内部释放连接方法"""
        if user_id in self.user_connections:
            conn = self.user_connections[user_id]
            conn.in_use = False
            conn.user_id = None
            conn.mark_used()
            del self.user_connections[user_id]
            logger.debug(f"📤 释放TTS连接: {user_id}")

    async def _create_connection(self, voice: str, user_id: str) -> Optional[PooledTTSConnection]:
        """创建新连接"""
        try:
            from dashscope_realtime import DashScopeRealtimeTTS
            from dashscope_realtime.tts import TTSConfig
            
            # 获取系统配置
            config = await SystemConfig.objects.aget(pk=1)
            
            # 创建TTS配置
            tts_config = TTSConfig(
                model=config.tts_model,
                voice=voice,
                sample_rate=config.tts_sample_rate,
                volume=config.tts_volume,
                speech_rate=config.tts_speech_rate,
                pitch_rate=config.tts_pitch_rate,
                audio_format=config.tts_audio_format,
            )
            
            # 创建TTS客户端
            client = DashScopeRealtimeTTS(api_key=config.tts_api_key, config=tts_config)
            await client.connect()
            
            # 创建池化连接对象
            conn = PooledTTSConnection(
                client=client,
                voice=voice,
                created_at=time.time(),
                last_used=time.time(),
                in_use=True,
                user_id=user_id,
                max_error_count=self.config.max_error_count,
                max_idle_time=self.config.max_idle_time,
            )
            
            # 添加到连接池
            if voice not in self.connections:
                self.connections[voice] = []
            self.connections[voice].append(conn)
            self.user_connections[user_id] = conn
            
            return conn
            
        except Exception as e:
            logger.error(f"❌ 创建TTS连接失败 (音色: {voice}): {e}")
            return None

    async def handle_connection_error(self, user_id: str, error: Exception):
        """处理连接错误"""
        async with self._lock:
            if user_id in self.user_connections:
                conn = self.user_connections[user_id]
                conn.mark_error()
                
                # 特殊处理1007错误
                error_msg = str(error)
                if "1007" in error_msg or "invalid frame" in error_msg.lower():
                    logger.warning(f"⚠️ TTS WebSocket协议错误 {user_id}: {error}")
                else:
                    logger.error(f"❌ TTS连接错误 {user_id}: {error}")
                
                # 如果连接不健康，从池中移除
                if not conn.is_healthy():
                    await self._remove_connection(conn)

    async def _remove_connection(self, conn: PooledTTSConnection):
        """从池中移除连接"""
        try:
            # 从用户映射中移除
            if conn.user_id and conn.user_id in self.user_connections:
                del self.user_connections[conn.user_id]
            
            # 从连接池中移除
            if conn.voice in self.connections:
                if conn in self.connections[conn.voice]:
                    self.connections[conn.voice].remove(conn)
            
            # 关闭连接
            await conn.client.disconnect()
            logger.debug(f"🗑️ 移除TTS连接: 音色 {conn.voice}")
            
        except Exception as e:
            logger.error(f"❌ 移除TTS连接失败: {e}")

    async def _cleanup_loop(self):
        """清理过期连接的后台任务"""
        while not self._shutdown:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                await self._cleanup_expired_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ TTS连接池清理任务错误: {e}")

    async def _cleanup_expired_connections(self):
        """清理过期和不健康的连接"""
        async with self._lock:
            expired_connections = []
            
            for voice, conns in self.connections.items():
                for conn in conns:
                    if not conn.in_use and (conn.is_expired() or not conn.is_healthy()):
                        expired_connections.append(conn)
            
            for conn in expired_connections:
                await self._remove_connection(conn)
                logger.debug(f"🧹 清理过期TTS连接: 音色 {conn.voice}")

    def _total_connections(self) -> int:
        """计算总连接数"""
        return sum(len(conns) for conns in self.connections.values())

    async def get_stats(self):
        """获取连接池统计"""
        async with self._lock:
            total = self._total_connections()
            busy = len(self.user_connections)
            idle = total - busy
            
            # 按音色统计
            voice_stats = {}
            for voice, conns in self.connections.items():
                voice_stats[voice] = {
                    "total": len(conns),
                    "busy": sum(1 for conn in conns if conn.in_use),
                    "idle": sum(1 for conn in conns if not conn.in_use),
                }
            
            return {
                "mode": "connection_pool",
                "total_connections": total,
                "busy_connections": busy,
                "idle_connections": idle,
                "max_connections": self.config.max_connections,
                "voice_stats": voice_stats,
            }

    async def shutdown(self):
        """关闭连接池"""
        self._shutdown = True
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        async with self._lock:
            # 关闭所有连接
            all_connections = []
            for conns in self.connections.values():
                all_connections.extend(conns)
            
            for conn in all_connections:
                try:
                    await conn.client.disconnect()
                except Exception as e:
                    logger.warning(f"关闭TTS连接失败: {e}")
            
            self.connections.clear()
            self.user_connections.clear()
            logger.info("🔄 TTS连接池已关闭")

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


# 双模式TTS函数
async def tts_speak_stream(
    text: str, user_id: str, audio_callback: Callable[[bytes], None], voice: str = None
) -> bool:
    """
    TTS语音合成 - 支持连接池模式和一次性连接模式

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

    # 根据配置选择模式
    if system_config.tts_use_connection_pool:
        logger.debug(f"🔄 使用连接池模式，用户: {user_id}")
        return await _tts_speak_stream_pooled(text, user_id, audio_callback, voice)
    else:
        logger.debug(f"🆕 使用一次性连接模式，用户: {user_id}")
        return await _tts_speak_stream_disposable(text, user_id, audio_callback, voice)


async def _tts_speak_stream_pooled(
    text: str, user_id: str, audio_callback: Callable[[bytes], None], voice: str = None
) -> bool:
    """
    连接池模式的TTS语音合成
    """
    pool = await get_tts_pool()
    if not pool:
        logger.error(f"❌ TTS连接池未初始化，用户: {user_id}")
        return False
    
    # 获取默认音色
    if not voice:
        config = await SystemConfig.objects.aget(pk=1)
        voice = config.tts_default_voice
    
    # 从连接池获取连接
    client = await pool.get_connection(voice, user_id)
    if not client:
        logger.error(f"❌ 无法从连接池获取TTS连接，用户: {user_id}")
        return False
    
    try:
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
            # 处理错误
            asyncio.create_task(pool.handle_connection_error(user_id, error))

        def on_end():
            logger.debug(f"TTS合成完成，用户: {user_id}")

        # 设置回调
        client.send_audio = on_audio
        client.on_error = on_error
        client.on_end = on_end

        # 执行TTS合成
        cleaned_text = text.strip()
        await client.say(cleaned_text)
        await client.finish()
        await client.wait_done()

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
            f"✅ TTS合成完成（连接池），用户: {user_id}, 音频块: {audio_chunk_count}, 字节: {total_audio_bytes}"
        )
        return True

    except Exception as e:
        # 处理连接错误
        await pool.handle_connection_error(user_id, e)
        # 特殊处理1007错误
        if "1007" in str(e) or "invalid frame" in str(e).lower():
            logger.warning(f"⚠️ TTS WebSocket协议错误，用户: {user_id}: {e}")
        else:
            logger.error(f"❌ TTS合成失败（连接池），用户: {user_id}: {e}")
        return False

    finally:
        # 释放连接回池中
        await pool.release_connection(user_id)


async def _tts_speak_stream_disposable(
    text: str, user_id: str, audio_callback: Callable[[bytes], None], voice: str = None
) -> bool:
    """
    一次性TTS语音合成 - 依赖DashScope SDK的内部连接池
    """
    try:
        from dashscope_realtime import DashScopeRealtimeTTS
        from dashscope_realtime.tts import TTSConfig

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
    中断指定用户的TTS播放 - 支持连接池模式和一次性连接模式

    Args:
        user_id: 用户ID

    Returns:
        bool: 是否成功中断
    """
    try:
        # 获取系统配置
        system_config = await SystemConfig.objects.aget(pk=1)
        
        if system_config.tts_use_connection_pool:
            # 连接池模式：真正的中断
            pool = await get_tts_pool()
            if pool:
                async with pool._lock:
                    if user_id in pool.user_connections:
                        conn = pool.user_connections[user_id]
                        try:
                            # 调用SDK的中断方法
                            await conn.client.interrupt()
                            logger.info(f"🛑 已中断用户 {user_id} 的TTS（连接池模式）")
                            return True
                        except Exception as e:
                            logger.error(f"❌ 中断TTS连接失败: {e}")
                            # 如果中断失败，释放连接
                            await pool.release_connection(user_id)
                            return False
                    else:
                        logger.debug(f"🛑 用户 {user_id} 无活跃TTS连接")
                        return True
            else:
                logger.warning(f"⚠️ TTS连接池未初始化，用户: {user_id}")
                return False
        else:
            # 一次性连接模式：记录日志（依赖SDK内部处理）
            logger.info(f"🛑 请求中断用户 {user_id} 的TTS（一次性连接模式）")
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
    """初始化TTS连接池 - 支持连接池模式和一次性连接模式"""
    global tts_pool

    # 从数据库读取配置
    config = await SystemConfig.objects.aget(pk=1)

    if not config.tts_enabled:
        logger.info("🔇 TTS功能未启用")
        return tts_pool

    if config.tts_use_connection_pool:
        # 连接池模式
        if tts_pool is None:
            tts_pool = TTSConnectionPool()
        
        try:
            await tts_pool.initialize()
            logger.info("✅ TTS连接池模式初始化完成")
        except Exception as e:
            logger.error(f"❌ TTS连接池初始化失败: {e}")
            tts_pool = None
    else:
        # 一次性连接模式
        logger.info("✅ TTS一次性连接模式初始化完成")
        tts_pool = None  # 一次性模式不需要连接池

    return tts_pool


async def shutdown_tts_pool():
    """关闭TTS连接池"""
    global tts_pool

    try:
        if tts_pool:
            await tts_pool.shutdown()
            tts_pool = None
            logger.info("🔄 TTS连接池已关闭")
        else:
            logger.info("🔄 TTS一次性连接模式，无需关闭连接池")
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
