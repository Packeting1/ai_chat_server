import asyncio
import logging
import time
from typing import Dict, Optional, Callable
from dataclasses import dataclass
from dashscope_realtime import DashScopeRealtimeTTS
from dashscope_realtime.tts import TTSConfig
from .models import SystemConfig

logger = logging.getLogger(__name__)

@dataclass
class TTSConnection:
    """TTS连接对象"""
    tts_client: DashScopeRealtimeTTS
    config: TTSConfig
    created_at: float
    last_used: float
    is_busy: bool = False
    user_id: Optional[str] = None
    error_count: int = 0

class TTSConnectionPool:
    """TTS连接池管理器"""
    
    def __init__(self):
        self.connections: Dict[str, TTSConnection] = {}
        self.max_connections = 10
        self.min_connections = 2
        self.max_idle_time = 300  # 5分钟
        self.max_error_count = 2  # 降低错误容忍度，更快移除问题连接
        self.cleanup_interval = 30  # 30秒清理一次，更频繁的健康检查
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        # 用户当前TTS播放状态
        self.user_playing_status: Dict[str, TTSConnection] = {}
        
    async def initialize(self):
        """初始化连接池"""
        logger.info("🎵 初始化TTS连接池...")
        
        # 创建最小连接数
        for i in range(self.min_connections):
            try:
                await self._create_connection()
            except Exception as e:
                logger.error(f"初始化TTS连接失败: {e}")
        
        # 启动清理任务
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"✅ TTS连接池初始化完成，当前连接数: {len(self.connections)}")
    
    async def get_connection(self, user_id: str) -> Optional[TTSConnection]:
        """获取可用连接"""
        async with self._lock:
            # 查找空闲连接
            for conn_id, conn in self.connections.items():
                if not conn.is_busy and conn.error_count < self.max_error_count:
                    # 检查连接是否健康
                    if await self._is_connection_healthy(conn):
                        conn.is_busy = True
                        conn.user_id = user_id
                        conn.last_used = time.time()
                        logger.debug(f"🔗 分配TTS连接 {conn_id} 给用户 {user_id}")
                        return conn
                    else:
                        # 连接不健康，移除它
                        logger.warning(f"⚠️ 检测到不健康的TTS连接，移除: {conn_id}")
                        await self._remove_connection(conn)
            
            # 如果没有空闲连接且未达到最大连接数，创建新连接
            if len(self.connections) < self.max_connections:
                try:
                    conn = await self._create_connection()
                    if conn:
                        conn.is_busy = True
                        conn.user_id = user_id
                        conn.last_used = time.time()
                        return conn
                except Exception as e:
                    logger.error(f"创建新TTS连接失败: {e}")
            
            logger.warning(f"⚠️ 无可用TTS连接，当前连接数: {len(self.connections)}")
            return None
    
    async def release_connection(self, conn: TTSConnection, user_id: str):
        """释放连接"""
        async with self._lock:
            if conn.user_id == user_id:
                conn.is_busy = False
                conn.user_id = None
                conn.last_used = time.time()
                # 清除用户播放状态
                if user_id in self.user_playing_status:
                    del self.user_playing_status[user_id]
                logger.debug(f"🔄 释放TTS连接，用户: {user_id}")
    
    async def interrupt_user_tts(self, user_id: str):
        """中断指定用户的TTS播放"""
        async with self._lock:
            if user_id in self.user_playing_status:
                conn = self.user_playing_status[user_id]
                try:
                    # 中断当前播放
                    await conn.tts_client.interrupt()
                    logger.info(f"🛑 中断用户 {user_id} 的TTS播放")
                    
                    # 释放连接
                    conn.is_busy = False
                    conn.user_id = None
                    conn.last_used = time.time()
                    del self.user_playing_status[user_id]
                    
                except Exception as e:
                    logger.error(f"中断TTS播放失败: {e}")
                    # 出错时强制释放连接
                    await self.handle_connection_error(conn, e)
    
    async def handle_connection_error(self, conn: TTSConnection, error: Exception):
        """处理连接错误"""
        async with self._lock:
            conn.error_count += 1
            conn.is_busy = False
            conn.user_id = None
            
            logger.error(f"❌ TTS连接错误 (错误次数: {conn.error_count}): {error}")
            
            # 检查错误类型，决定处理策略
            error_str = str(error).lower()
            
            # 对于WebSocket相关错误，立即移除连接
            if any(keyword in error_str for keyword in ['1007', 'invalid frame', 'websocket', 'connection']):
                logger.warning(f"🔌 检测到WebSocket错误，立即移除连接")
                await self._remove_connection(conn)
                return
            
            # 对于其他错误，根据错误次数决定
            if conn.error_count >= self.max_error_count:
                logger.warning(f"⚠️ 连接错误次数过多，移除连接")
                await self._remove_connection(conn)
            else:
                # 尝试重置连接状态
                try:
                    await conn.tts_client.disconnect()
                    logger.info(f"🔄 重置TTS连接状态")
                except:
                    pass
    
    async def _create_connection(self) -> Optional[TTSConnection]:
        """创建新的TTS连接"""
        try:
            config = await self._get_tts_config()
            if not config['enabled'] or not config['api_key'] or config['api_key'] == 'your-api-key':
                logger.warning("TTS未启用或API密钥未配置")
                return None
            
            tts_config = TTSConfig(
                model=config['model'],
                voice=config['voice'],
                sample_rate=config['sample_rate'],
                volume=80,
                speech_rate=1.0,
                pitch_rate=1.0,
                audio_format="pcm"
            )
            
            # 创建TTS客户端但不立即连接
            tts_client = DashScopeRealtimeTTS(
                api_key=config['api_key'],
                config=tts_config
            )
            
            conn_id = f"tts_{int(time.time() * 1000)}_{len(self.connections)}"
            connection = TTSConnection(
                tts_client=tts_client,
                config=tts_config,
                created_at=time.time(),
                last_used=time.time()
            )
            
            self.connections[conn_id] = connection
            logger.debug(f"✅ 创建TTS连接: {conn_id}")
            return connection
            
        except Exception as e:
            logger.error(f"创建TTS连接失败: {e}")
            return None
    
    async def _remove_connection(self, conn: TTSConnection):
        """移除连接"""
        try:
            # 找到连接ID
            conn_id = None
            for cid, c in self.connections.items():
                if c == conn:
                    conn_id = cid
                    break
            
            if conn_id:
                # 断开连接
                try:
                    await conn.tts_client.disconnect()
                except:
                    pass
                
                del self.connections[conn_id]
                logger.info(f"🗑️ 移除TTS连接: {conn_id}")
        except Exception as e:
            logger.error(f"移除TTS连接失败: {e}")
    
    async def _cleanup_loop(self):
        """清理循环"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_idle_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TTS连接池清理异常: {e}")
    
    async def _cleanup_idle_connections(self):
        """清理空闲连接"""
        async with self._lock:
            current_time = time.time()
            connections_to_remove = []
            
            for conn_id, conn in self.connections.items():
                # 检查连接健康状态
                if not await self._is_connection_healthy(conn):
                    logger.warning(f"🔍 清理不健康的TTS连接: {conn_id}")
                    connections_to_remove.append(conn)
                    continue
                
                # 清理空闲时间过长的连接
                if (not conn.is_busy and 
                    current_time - conn.last_used > self.max_idle_time and
                    len(self.connections) > self.min_connections):
                    connections_to_remove.append(conn)
            
            for conn in connections_to_remove:
                await self._remove_connection(conn)
    
    async def _get_tts_config(self):
        """获取TTS配置"""
        try:
            config = await SystemConfig.objects.aget(pk=1)
            return {
                'api_key': config.tts_api_key if config.tts_api_key else 'your-api-key',
                'model': getattr(config, 'tts_model', 'cosyvoice-v2'),
                'voice': config.tts_voice if config.tts_voice else 'longxiaochun_v2',
                'sample_rate': config.tts_sample_rate,
                'enabled': config.tts_enabled
            }
        except SystemConfig.DoesNotExist:
            return {
                'api_key': 'your-api-key',
                'model': 'cosyvoice-v2',
                'voice': 'longxiaochun_v2',
                'sample_rate': 22050,
                'enabled': False
            }
    
    async def _is_connection_healthy(self, conn: TTSConnection) -> bool:
        """检查连接是否健康"""
        try:
            # 检查连接是否已断开
            if hasattr(conn.tts_client, '_ws') and conn.tts_client._ws:
                if conn.tts_client._ws.closed:
                    logger.debug(f"🔌 TTS连接已关闭")
                    return False
            
            # 检查连接时间是否过长
            if time.time() - conn.created_at > 3600:  # 1小时
                logger.debug(f"⏰ TTS连接时间过长，需要重新创建")
                return False
                
            return True
        except Exception as e:
            logger.debug(f"检查TTS连接健康状态失败: {e}")
            return False
    
    async def get_stats(self):
        """获取连接池统计"""
        async with self._lock:
            total = len(self.connections)
            busy = sum(1 for conn in self.connections.values() if conn.is_busy)
            idle = total - busy
            
            return {
                'total_connections': total,
                'busy_connections': busy,
                'idle_connections': idle,
                'max_connections': self.max_connections,
                'min_connections': self.min_connections
            }
    
    async def shutdown(self):
        """关闭连接池"""
        logger.info("🔌 关闭TTS连接池...")
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        async with self._lock:
            for conn in list(self.connections.values()):
                await self._remove_connection(conn)
        
        logger.info("✅ TTS连接池已关闭")
    
    async def reset_pool(self):
        """重置连接池"""
        logger.info("🔄 重置TTS连接池...")
        
        async with self._lock:
            # 移除所有现有连接
            for conn in list(self.connections.values()):
                await self._remove_connection(conn)
            
            # 重新创建最小连接数
            for i in range(self.min_connections):
                try:
                    await self._create_connection()
                except Exception as e:
                    logger.error(f"重置时创建TTS连接失败: {e}")
        
        logger.info(f"✅ TTS连接池重置完成，当前连接数: {len(self.connections)}")

# 全局TTS连接池实例
tts_pool = TTSConnectionPool()

async def get_tts_pool():
    """获取TTS连接池实例"""
    return tts_pool

async def tts_speak_stream(text: str, user_id: str, audio_callback: Callable[[bytes], None]) -> bool:
    """
    使用连接池进行TTS语音合成
    
    Args:
        text: 要合成的文本
        user_id: 用户ID
        audio_callback: 音频数据回调函数
    
    Returns:
        bool: 是否成功
    """
    pool = await get_tts_pool()
    
    conn = await pool.get_connection(user_id)
    
    if not conn:
        logger.warning(f"无法获取TTS连接，用户: {user_id}")
        return False
    
    try:
        # 记录用户播放状态
        async with pool._lock:
            pool.user_playing_status[user_id] = conn
        
        # 设置回调函数
        def on_audio(audio_data):
            try:
                audio_callback(audio_data)
            except Exception as e:
                logger.error(f"音频回调失败: {e}")
        
        def on_error(error):
            logger.error(f"TTS合成错误: {error}")
            asyncio.create_task(pool.handle_connection_error(conn, error))
        
        def on_end():
            logger.debug(f"TTS播放结束，用户: {user_id}")
        
        # 连接并合成
        await conn.tts_client.connect()
        conn.tts_client.send_audio = on_audio
        conn.tts_client.on_error = on_error
        conn.tts_client.on_end = on_end
        
        await conn.tts_client.say(text)
        await conn.tts_client.finish()
        await conn.tts_client.wait_done()
        
        logger.info(f"✅ TTS合成完成，用户: {user_id}, 文本: {text[:50]}...")
        return True
        
    except Exception as e:
        logger.error(f"TTS合成异常: {e}")
        await pool.handle_connection_error(conn, e)
        return False
    finally:
        await pool.release_connection(conn, user_id)

async def interrupt_user_tts(user_id: str) -> bool:
    """
    中断指定用户的TTS播放
    
    Args:
        user_id: 用户ID
    
    Returns:
        bool: 是否成功中断
    """
    pool = await get_tts_pool()
    await pool.interrupt_user_tts(user_id)
    return True