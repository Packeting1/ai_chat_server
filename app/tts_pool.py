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
    is_connected: bool = False

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
                    # 中断后连接可能仍然有效，保持连接状态
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
            conn.is_connected = False  # 标记为未连接
            
            logger.error(f"❌ TTS连接错误 (错误次数: {conn.error_count}): {error}")
            logger.error(f"🔍 错误详情: 类型={type(error).__name__}, 完整信息={repr(error)}")
            logger.error(f"📊 连接信息: 创建时间={time.strftime('%H:%M:%S', time.localtime(conn.created_at))}, "
                       f"空闲时间={int(time.time() - conn.last_used)}秒")
            
            # 检查错误类型，决定处理策略
            error_str = str(error).lower()
            
            # 详细分析错误类型
            if '1007' in error_str:
                logger.error(f"🚨 检测到1007错误 (Invalid frame payload data): 可能是数据格式问题或API密钥问题")
            elif '1011' in error_str:
                logger.error(f"🚨 检测到1011错误 (Internal error): 服务器内部错误")
            elif 'timeout' in error_str:
                logger.error(f"🚨 检测到超时错误: 连接或响应超时")
            elif 'websocket' in error_str or 'connection' in error_str:
                logger.error(f"🚨 检测到连接错误: WebSocket连接问题")
            
            # 对于WebSocket相关错误，立即移除连接
            if any(keyword in error_str for keyword in ['1007', 'invalid frame', 'websocket', 'connection', '1011', 'timeout']):
                logger.warning(f"🔌 检测到严重WebSocket错误，立即移除连接")
                await self._remove_connection(conn)
                return
            
            # 对于其他错误，根据错误次数决定
            if conn.error_count >= self.max_error_count:
                logger.warning(f"⚠️ 连接错误次数过多({conn.error_count}>={self.max_error_count})，移除连接")
                await self._remove_connection(conn)
            else:
                # 尝试重置连接状态
                try:
                    logger.info(f"🔄 尝试重置TTS连接状态...")
                    await conn.tts_client.disconnect()
                    logger.info(f"✅ TTS连接状态重置完成")
                except Exception as reset_err:
                    logger.error(f"❌ 重置TTS连接失败: {reset_err}")
    
    async def _create_connection(self) -> Optional[TTSConnection]:
        """创建新的TTS连接"""
        try:
            logger.info(f"🏗️ 开始创建新的TTS连接...")
            
            config = await self._get_tts_config()
            logger.info(f"📋 获取TTS配置: 启用={config['enabled']}, 模型={config['model']}, 声音={config['voice']}")
            
            if not config['enabled']:
                logger.warning("⚠️ TTS未启用，跳过连接创建")
                return None
                
            if not config['api_key'] or config['api_key'] == 'your-api-key':
                logger.error("❌ TTS API密钥未配置或使用默认值")
                return None
            
            logger.info(f"🔑 API密钥已配置，长度: {len(config['api_key'])}")
            
            tts_config = TTSConfig(
                model=config['model'],
                voice=config['voice'],
                sample_rate=config['sample_rate'],
                volume=80,
                speech_rate=1.0,
                pitch_rate=1.0,
                audio_format="pcm"
            )
            logger.info(f"⚙️ TTS配置创建完成: {tts_config}")
            
            # 创建TTS客户端但不立即连接
            logger.info(f"🔧 创建DashScopeRealtimeTTS客户端...")
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
            logger.info(f"✅ TTS连接创建成功: {conn_id}, 总连接数: {len(self.connections)}")
            return connection
            
        except Exception as e:
            logger.error(f"❌ 创建TTS连接失败: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"📜 详细错误堆栈:\n{traceback.format_exc()}")
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
            # 检查错误次数
            if conn.error_count >= self.max_error_count:
                logger.debug(f"❌ TTS连接错误次数过多: {conn.error_count}")
                return False
            
            # 检查连接是否已断开
            if hasattr(conn.tts_client, '_ws') and conn.tts_client._ws:
                try:
                    if hasattr(conn.tts_client._ws, 'closed') and conn.tts_client._ws.closed:
                        logger.debug(f"🔌 TTS连接已关闭")
                        return False
                except (AttributeError, Exception):
                    # 如果无法访问closed属性，假设连接有效
                    pass
            
            # 检查连接时间是否过长
            if time.time() - conn.created_at > 3600:  # 1小时
                logger.debug(f"⏰ TTS连接时间过长，需要重新创建")
                return False
            
            # 检查错误次数是否过多
            if conn.error_count >= 2:  # 降低错误阈值
                logger.debug(f"⚠️ TTS连接错误次数过多: {conn.error_count}")
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
            active_users = len(self.user_playing_status)
            
            # 检查并清理孤立的用户状态
            orphaned_count = await self._cleanup_orphaned_users_internal()
            
            return {
                'total_connections': total,
                'busy_connections': busy,
                'idle_connections': idle,
                'active_users': active_users,
                'orphaned_cleaned': orphaned_count,
                'max_connections': self.max_connections,
                'min_connections': self.min_connections,
                'connections': [
                    {
                        'id': conn_id[-8:],
                        'is_busy': conn.is_busy,
                        'is_connected': conn.is_connected,
                        'user_id': conn.user_id[-8:] if conn.user_id else None,
                        'error_count': conn.error_count,
                        'idle_time': int(time.time() - conn.last_used)
                    }
                    for conn_id, conn in self.connections.items()
                ]
            }
    
    async def _cleanup_orphaned_users_internal(self):
        """内部清理孤立用户状态（已持有锁）"""
        orphaned_users = []
        for user_id, conn in list(self.user_playing_status.items()):
            # 检查连接是否还存在于连接池中
            conn_exists = any(c for c in self.connections.values() if c == conn)
            if not conn_exists:
                orphaned_users.append(user_id)
        
        for user_id in orphaned_users:
            del self.user_playing_status[user_id]
            logger.warning(f"清理孤立的用户TTS状态: {user_id}")
        
        return len(orphaned_users)
    
    async def cleanup_orphaned_users(self):
        """公开的清理孤立用户状态方法"""
        async with self._lock:
            return await self._cleanup_orphaned_users_internal()
    
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
    
    connection_created = False
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
            logger.error(f"💥 TTS合成错误 (用户: {user_id}): {error}")
            logger.error(f"📊 错误发生时连接状态: is_connected={conn.is_connected}, error_count={conn.error_count}")
            
            # 检查WebSocket状态
            if hasattr(conn.tts_client, '_ws') and conn.tts_client._ws:
                try:
                    ws_state = "open" if not conn.tts_client._ws.closed else "closed"
                    logger.error(f"🔌 WebSocket状态: {ws_state}")
                except Exception as ws_err:
                    logger.error(f"🔌 无法获取WebSocket状态: {ws_err}")
            
            # 避免在回调中使用异步任务，直接记录错误
            conn.error_count += 1
        
        def on_end():
            logger.debug(f"TTS播放结束，用户: {user_id}")
        
        # 检查连接状态，只在需要时才连接
        try:
            # 先检查内部状态标记
            if not conn.is_connected:
                # 再检查实际WebSocket状态
                if hasattr(conn.tts_client, '_ws') and conn.tts_client._ws and not conn.tts_client._ws.closed:
                    conn.is_connected = True
                elif hasattr(conn.tts_client, 'connected') and conn.tts_client.connected:
                    conn.is_connected = True
        except (AttributeError, Exception):
            conn.is_connected = False
        
        if not conn.is_connected:
            logger.info(f"🔗 为用户 {user_id} 建立TTS连接，配置模型: {conn.config.model}")
            try:
                # 获取TTS配置信息用于调试
                config = await pool._get_tts_config()
                logger.info(f"📋 TTS配置检查: 启用={config['enabled']}, API密钥长度={len(config['api_key']) if config['api_key'] != 'your-api-key' else 0}")
                
                await conn.tts_client.connect()
                conn.is_connected = True
                connection_created = True
                logger.info(f"✅ TTS连接建立成功，用户: {user_id}")
            except Exception as connect_err:
                logger.error(f"❌ TTS连接建立失败，用户: {user_id}, 错误: {connect_err}")
                raise
        else:
            logger.info(f"♻️ 复用现有TTS连接，用户: {user_id}, 连接时长: {time.time() - conn.created_at:.1f}秒")
        
        # 设置回调（无论是新连接还是复用的连接都需要设置）
        conn.tts_client.send_audio = on_audio
        conn.tts_client.on_error = on_error
        conn.tts_client.on_end = on_end
        
        logger.info(f"🎤 开始TTS合成，用户: {user_id}, 文本长度: {len(text)}, 内容: {text[:50]}...")
        
        # 检查连接状态再发送
        if hasattr(conn.tts_client, '_ws') and conn.tts_client._ws:
            if conn.tts_client._ws.closed:
                logger.error(f"⚠️ WebSocket已关闭，重新连接")
                await conn.tts_client.connect()
                conn.is_connected = True
        
        logger.debug(f"📤 发送TTS文本，用户: {user_id}")
        await conn.tts_client.say(text)
        
        logger.debug(f"🔚 结束TTS输入，用户: {user_id}")
        await conn.tts_client.finish()
        
        logger.debug(f"⏳ 等待TTS完成，用户: {user_id}")
        await conn.tts_client.wait_done()
        
        logger.info(f"✅ TTS合成完成，用户: {user_id}, 文本: {text[:50]}...")
        return True
        
    except Exception as e:
        logger.error(f"💥 TTS合成异常，用户: {user_id}: {type(e).__name__}: {e}")
        logger.error(f"🔍 异常发生在: 连接建立={'是' if connection_created else '否'}, "
                   f"连接状态={conn.is_connected if conn else 'N/A'}")
        
        # 记录详细的异常堆栈
        import traceback
        logger.error(f"📜 异常堆栈:\n{traceback.format_exc()}")
        
        # 处理连接错误时不使用异步任务
        if conn:
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