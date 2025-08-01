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
            sample_rate=config['sample_rate'],
            volume=80,                    # 音量（0-100）
            speech_rate=1.0,             # 语速
            pitch_rate=1.0,              # 音调
            audio_format="pcm"           # 音频格式
        )
        
        # 创建TTS客户端
        from dashscope_realtime import DashScopeRealtimeTTS
        tts_client = DashScopeRealtimeTTS(
            api_key=config['api_key'],
            config=tts_config
        )
        logger.info(f"🔧 创建一次性TTS客户端成功，用户: {user_id}")
        
        # 设置回调函数
        audio_chunk_count = 0
        total_audio_bytes = 0
        
        def on_audio(audio_data):
            nonlocal audio_chunk_count, total_audio_bytes
            try:
                audio_chunk_count += 1
                total_audio_bytes += len(audio_data) if audio_data else 0
                
                logger.info(f"🎵 收到音频数据块 #{audio_chunk_count}: {len(audio_data)} 字节，用户: {user_id}")
                
                # 详细的音频数据分析
                if audio_data and len(audio_data) > 0:
                    # 分析音频数据的内容
                    import struct
                    if len(audio_data) >= 2:
                        # 解析前几个16位样本
                        sample_count = min(10, len(audio_data) // 2)
                        samples = struct.unpack(f'<{sample_count}h', audio_data[:sample_count*2])
                        max_sample = max(abs(s) for s in samples)
                        avg_sample = sum(abs(s) for s in samples) / len(samples)
                        
                        logger.info(f"🔍 音频分析 #{audio_chunk_count}: 样本数={len(audio_data)//2}, 最大值={max_sample}, 平均值={avg_sample:.1f}")
                        
                        # 检查是否真的是静音
                        is_silence = max_sample < 100  # 阈值调整
                        if is_silence:
                            logger.warning(f"🔇 音频块 #{audio_chunk_count} 疑似静音，最大样本值: {max_sample}")
                        else:
                            logger.info(f"🔊 音频块 #{audio_chunk_count} 有效，最大样本值: {max_sample}")
                    
                    # 显示前几个字节的十六进制
                    hex_preview = audio_data[:16].hex() if len(audio_data) >= 16 else audio_data.hex()
                    logger.debug(f"📊 音频数据预览: {hex_preview}...")
                else:
                    logger.warning(f"⚠️ 收到空音频数据块 #{audio_chunk_count}，用户: {user_id}")
                
                # 调用用户回调
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
        cleaned_text = text.strip()
        logger.info(f"📤 发送TTS文本，用户: {user_id}")
        logger.info(f"📝 文本内容: '{cleaned_text}'")
        logger.info(f"📏 文本长度: {len(cleaned_text)} 字符")
        
        await tts_client.say(cleaned_text)
        logger.info(f"✅ TTS文本发送完成，用户: {user_id}")
        
        logger.info(f"🔚 结束TTS输入，用户: {user_id}")
        await tts_client.finish()
        
        logger.info(f"⏳ 开始等待TTS完成，用户: {user_id}")
        await tts_client.wait_done()
        logger.info(f"🎯 TTS等待完成返回，用户: {user_id}")
        
        # 增加短暂延迟，确保所有音频数据都已处理
        await asyncio.sleep(0.1)
        
        # 输出TTS统计信息
        logger.info(f"📊 TTS完成统计，用户: {user_id}")
        logger.info(f"  📝 原始文本: '{cleaned_text}'")
        logger.info(f"  📏 文本长度: {len(cleaned_text)} 字符")
        logger.info(f"  🎵 音频块数: {audio_chunk_count}")
        logger.info(f"  💾 总音频字节: {total_audio_bytes}")
        logger.info(f"  ⏱️ 平均每块: {total_audio_bytes/audio_chunk_count if audio_chunk_count > 0 else 0:.1f} 字节")
        
        if audio_chunk_count == 0:
            logger.error(f"❌ 警告：没有收到任何音频数据！用户: {user_id}")
        elif total_audio_bytes < 1000:
            logger.warning(f"⚠️ 警告：音频数据量过少({total_audio_bytes}字节)，可能不完整，用户: {user_id}")
        
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