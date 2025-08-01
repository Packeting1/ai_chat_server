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
    """TTS配置管理器 - 简化版，用于一次性连接模式"""
    
    def __init__(self):
        # 保留这些属性以兼容现有代码
        self.connections: Dict[str, TTSConnection] = {}
        self.user_playing_status: Dict[str, TTSConnection] = {}
        self._lock = asyncio.Lock()
        logger.info("🎵 TTS配置管理器已初始化（一次性连接模式）")
        
    async def initialize(self):
        """初始化配置管理器（一次性连接模式无需预创建连接）"""
        logger.info("🎵 TTS配置管理器初始化完成（一次性连接模式）")
    
    async def get_connection(self, user_id: str) -> Optional[TTSConnection]:
        """获取可用连接（一次性连接模式下不再使用）"""
        logger.debug(f"🔗 get_connection调用，用户: {user_id}（一次性连接模式下此方法无效）")
        return None
    
    async def release_connection(self, conn: TTSConnection, user_id: str):
        """释放连接（一次性连接模式下不再使用）"""
        logger.debug(f"🔄 release_connection调用，用户: {user_id}（一次性连接模式下此方法无效）")
    
    async def interrupt_user_tts(self, user_id: str):
        """中断指定用户的TTS播放（一次性连接模式下简化）"""
        logger.debug(f"🛑 interrupt_user_tts调用，用户: {user_id}（一次性连接模式下由前端处理中断）")
    
    async def handle_connection_error(self, conn: TTSConnection, error: Exception):
        """处理连接错误（一次性连接模式下简化）"""
        logger.debug(f"❌ handle_connection_error调用: {error}（一次性连接模式下无需处理）")
    
    # 以下方法在一次性连接模式下已简化或移除
    
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
    
    async def get_stats(self):
        """获取连接池统计（一次性连接模式下简化）"""
        return {
            'total_connections': 0,
            'busy_connections': 0,
            'idle_connections': 0,
            'active_users': 0,
            'orphaned_cleaned': 0,
            'mode': 'disposable_connection',
            'connections': []
        }
    
    async def shutdown(self):
        """关闭连接池（一次性连接模式下简化）"""
        logger.info("🔌 TTS配置管理器关闭（一次性连接模式）")
    
    async def reset_pool(self):
        """重置连接池（一次性连接模式下简化）"""
        logger.info("🔄 TTS配置管理器重置（一次性连接模式）")

# 全局TTS连接池实例
tts_pool = TTSConnectionPool()

async def get_tts_pool():
    """获取TTS连接池实例"""
    return tts_pool

async def tts_speak_stream(text: str, user_id: str, audio_callback: Callable[[bytes], None]) -> bool:
    """
    一次性TTS语音合成 - 每次创建新连接，完成后立即销毁
    
    Args:
        text: 要合成的文本
        user_id: 用户ID
        audio_callback: 音频数据回调函数
    
    Returns:
        bool: 是否成功
    """
    logger.info(f"🆕 开始一次性TTS合成，用户: {user_id}, 文本长度: {len(text)}")
    
    # 验证文本内容
    if not text or not text.strip():
        logger.warning(f"⚠️ TTS文本为空或只有空白字符，用户: {user_id}")
        return False
        
    if len(text.strip()) > 1000:
        logger.warning(f"⚠️ TTS文本过长 ({len(text)} 字符)，可能影响性能，用户: {user_id}")
    
    # 获取TTS配置
    pool = await get_tts_pool()
    config = await pool._get_tts_config()
    
    if not config['enabled']:
        logger.warning(f"⚠️ TTS功能未启用，跳过合成，用户: {user_id}")
        return False
    
    if config['api_key'] == 'your-api-key':
        logger.error(f"❌ TTS API密钥未配置，用户: {user_id}")
        return False
    
    logger.info(f"📋 TTS配置: 模型={config['model']}, 声音={config['voice']}, 采样率={config['sample_rate']}")
    
    # 创建一次性TTS客户端
    tts_client = None
    try:
        # 创建TTS配置
        from dashscope_realtime.tts import TTSConfig
        tts_config = TTSConfig(
            model=config['model'],
            voice=config['voice'],
            sample_rate=config['sample_rate']
        )
        
        # 创建TTS客户端
        from dashscope_realtime import DashScopeRealtimeTTS
        tts_client = DashScopeRealtimeTTS(
            api_key=config['api_key'],
            config=tts_config
        )
        logger.info(f"🔧 创建一次性TTS客户端成功，用户: {user_id}")
        
        # 设置回调函数
        def on_audio(audio_data):
            try:
                logger.debug(f"🎵 收到音频数据: {len(audio_data)} 字节，用户: {user_id}")
                
                # 简单的音频数据验证
                if audio_data and len(audio_data) > 0:
                    # 检查前几个字节是否全为0（静音检测）
                    sample_bytes = audio_data[:min(10, len(audio_data))]
                    is_likely_silence = all(b == 0 for b in sample_bytes)
                    if is_likely_silence:
                        logger.debug(f"🔇 检测到静音数据片段，用户: {user_id}")
                else:
                    logger.warning(f"⚠️ 收到空音频数据，用户: {user_id}")
                
                audio_callback(audio_data)
            except Exception as e:
                logger.error(f"💥 音频回调失败，用户: {user_id}: {e}")
                import traceback
                logger.error(f"📜 音频回调异常堆栈:\n{traceback.format_exc()}")
        
        def on_error(error):
            logger.error(f"💥 TTS合成错误 (用户: {user_id}): {error}")
            logger.error(f"🔍 错误类型: {type(error).__name__}")
        
        def on_end():
            logger.debug(f"🔚 TTS播放结束，用户: {user_id}")
        
        # 设置回调
        tts_client.send_audio = on_audio
        tts_client.on_error = on_error
        tts_client.on_end = on_end
        
        # 建立连接
        logger.info(f"🔗 建立TTS连接，用户: {user_id}")
        await tts_client.connect()
        logger.info(f"✅ TTS连接建立成功，用户: {user_id}")
        
        # 发送文本进行合成
        logger.info(f"📤 发送TTS文本，用户: {user_id}, 内容: '{text.strip()[:30]}...'")
        await tts_client.say(text)
        
        logger.info(f"🔚 结束TTS输入，用户: {user_id}")
        await tts_client.finish()
        
        logger.info(f"⏳ 等待TTS完成，用户: {user_id}")
        await tts_client.wait_done()
        
        logger.info(f"✅ TTS合成完成，用户: {user_id}, 文本: {text[:50]}...")
        return True
        
    except Exception as e:
        logger.error(f"💥 TTS合成异常，用户: {user_id}: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"📜 详细异常堆栈:\n{traceback.format_exc()}")
        return False
        
    finally:
        # 立即销毁连接
        if tts_client:
            try:
                if hasattr(tts_client, '_ws') and tts_client._ws and not tts_client._ws.closed:
                    await tts_client._ws.close()
                    logger.info(f"🗑️ TTS连接已销毁，用户: {user_id}")
            except Exception as cleanup_err:
                logger.warning(f"⚠️ TTS连接清理失败，用户: {user_id}: {cleanup_err}")
        
        logger.info(f"🏁 一次性TTS合成流程结束，用户: {user_id}")

async def interrupt_user_tts(user_id: str) -> bool:
    """
    中断指定用户的TTS播放
    
    注意：在一次性连接模式下，TTS连接是临时的，
    中断主要通过前端停止播放来实现。
    
    Args:
        user_id: 用户ID
    
    Returns:
        bool: 总是返回True（兼容现有代码）
    """
    logger.info(f"🛑 请求中断TTS播放，用户: {user_id}（一次性连接模式下无需特殊处理）")
    # 在一次性连接模式下，每次TTS都是独立的连接
    # 中断主要依赖前端停止音频播放
    return True