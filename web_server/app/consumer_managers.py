"""
StreamChatConsumer管理器类
封装业务逻辑，保持接口兼容
"""
import asyncio
import logging
import time
from typing import Optional, Callable, List
import json

from .consumer_components import (
    ConnectionState, ConnectionStatus, ConversationState, 
    AudioBuffer, TaskManager, ConfigCache, ErrorBoundary
)
from .funasr_client import FunASRClient, create_stream_config_async
from .utils import get_system_config_async

logger = logging.getLogger(__name__)


class ASRManager:
    """ASR连接管理器 - 封装所有ASR相关逻辑"""
    
    def __init__(self, user_id: str, websocket_sender: Callable):
        self.user_id = user_id
        self.send = websocket_sender
        self.state = ConnectionState()
        self.buffer = AudioBuffer()
        self.task_manager = TaskManager()
        self.error_boundary = ErrorBoundary(websocket_sender)
        self._reconnecting_lock = asyncio.Lock()
    
    async def initialize(self) -> bool:
        """初始化ASR连接"""
        try:
            return await self._connect()
        except Exception as e:
            await self.error_boundary.handle_error(
                e, "ASR初始化", "asr_connection_failed"
            )
            return False
    
    async def ensure_ready(self) -> bool:
        """确保ASR连接就绪"""
        if self.state.is_ready():
            return True
        
        try:
            return await self._reconnect()
        except Exception as e:
            await self.error_boundary.handle_error(
                e, "ASR连接检查", "asr_connection_failed"
            )
            return False
    
    async def process_audio(self, audio_data: bytes) -> bool:
        """处理音频数据"""
        # 检查连接状态
        if not self.state.is_ready():
            # 缓冲音频数据
            if not self.buffer.add(audio_data):
                logger.warning(f"用户 {self.user_id} 音频缓冲已满，丢弃数据")
            # 触发重连
            await self._reconnect()
            return False
        
        try:
            await self.state.asr_client.send_audio_data(audio_data)
            self.state.mark_active()
            return True
        except Exception as e:
            await self.error_boundary.handle_error(e, "音频数据发送", "asr_error")
            # 缓冲数据并触发重连
            self.buffer.add(audio_data)
            await self._reconnect()
            return False
    
    async def start_response_handler(self, handler_func: Callable) -> bool:
        """启动响应处理任务"""
        return await self.task_manager.start_task(
            "response_handler", handler_func()
        )
    
    async def disconnect(self):
        """断开连接并清理资源"""
        await self.task_manager.cancel_all()
        
        if self.state.asr_client:
            try:
                await self.state.asr_client.disconnect()
            except Exception as e:
                logger.error(f"断开ASR连接失败: {e}")
        
        self.state.asr_client = None
        self.state.status = ConnectionStatus.DISCONNECTED
        self.buffer.clear()
    
    async def _connect(self) -> bool:
        """建立ASR连接"""
        try:
            self.state.status = ConnectionStatus.CONNECTING
            
            # 创建新的ASR客户端
            self.state.asr_client = FunASRClient()
            await self.state.asr_client.connect()
            
            # 发送初始配置
            stream_config = await create_stream_config_async()
            await self.state.asr_client.send_config(stream_config)
            
            # 更新状态
            self.state.status = ConnectionStatus.CONNECTED
            self.state.reconnect_attempts = 0
            self.state.mark_active()
            
            # 发送连接成功通知
            await self.send(json.dumps({
                "type": "asr_connected",
                "message": "ASR服务器连接成功",
                "connection_mode": "independent",
                "config": stream_config,
            }))
            
            # 处理缓冲的音频数据
            await self._flush_buffer()
            
            return True
            
        except Exception as e:
            self.state.status = ConnectionStatus.FAILED
            raise e
    
    async def _reconnect(self) -> bool:
        """重新连接ASR"""
        async with self._reconnecting_lock:
            # 如果已经在重连或已连接，直接返回
            if (self.state.status == ConnectionStatus.RECONNECTING or 
                self.state.is_ready()):
                return self.state.is_ready()
            
            if not self.state.should_reconnect():
                return False
            
            self.state.status = ConnectionStatus.RECONNECTING
            self.state.reconnect_attempts += 1
            
            try:
                # 清理旧连接
                if self.state.asr_client:
                    try:
                        await self.state.asr_client.disconnect()
                    except:
                        pass
                
                # 等待后重连
                await asyncio.sleep(min(2 * self.state.reconnect_attempts, 10))
                
                # 重新建立连接
                return await self._connect()
                
            except Exception as e:
                logger.error(f"用户 {self.user_id} ASR重连失败 "
                           f"(尝试 {self.state.reconnect_attempts}): {e}")
                
                if not self.state.should_reconnect():
                    await self.send(json.dumps({
                        "type": "asr_reconnect_failed",
                        "message": "ASR服务重连失败，请刷新页面重试",
                        "error": str(e)
                    }))
                
                return False
    
    async def _flush_buffer(self):
        """处理缓冲的音频数据"""
        if self.buffer.is_empty() or not self.state.is_ready():
            return
        
        chunks = self.buffer.flush()
        for chunk in chunks:
            try:
                await self.state.asr_client.send_audio_data(chunk)
            except Exception as e:
                logger.warning(f"回放缓冲音频失败: {e}")
                break


class ConversationManager:
    """对话管理器 - 管理对话状态和流程"""
    
    def __init__(self, user_id: str, websocket_sender: Callable):
        self.user_id = user_id
        self.send = websocket_sender
        self.state = ConversationState()
        self.config_cache = ConfigCache()
        self.error_boundary = ErrorBoundary(websocket_sender)
    
    async def handle_restart(self):
        """处理对话重启"""
        # 重新激活对话状态
        self.state.active = True
        self.state.reset_current_turn()
        
        # 获取对话历史
        from .utils import session_manager
        conversation_history = await session_manager.get_conversation_history(
            self.user_id
        )
        history_count = len(conversation_history)
        
        # 发送重新开始通知
        await self.send(json.dumps({
            "type": "conversation_restarted",
            "message": "对话已重启",
            "history_count": history_count,
            "user_id": self.user_id,
        }))
    
    async def handle_reset(self):
        """处理对话重置"""
        from .utils import session_manager
        await session_manager.reset_conversation(self.user_id)
        await self.send(json.dumps({
            "type": "conversation_reset", 
            "message": "对话历史已重置"
        }))
    
    async def get_mode_info(self):
        """获取对话模式信息"""
        try:
            config = await self.config_cache.get_config(get_system_config_async)
            from .utils import session_manager
            conversation_history = await session_manager.get_conversation_history(
                self.user_id
            )
            history_count = len(conversation_history)
            
            await self.send(json.dumps({
                "type": "conversation_mode_info",
                "continuous_conversation": config.continuous_conversation,
                "conversation_active": self.state.active,
                "history_count": history_count,
                "mode_description": "持续对话模式" if config.continuous_conversation else "一次性对话模式",
            }))
        except Exception as e:
            await self.error_boundary.handle_error(e, "获取对话模式")
    
    async def should_continue_after_response(self) -> bool:
        """判断响应后是否应该继续对话"""
        config = await self.config_cache.get_config(get_system_config_async)
        return config.continuous_conversation
    
    async def send_paused_message(self):
        """发送对话暂停消息"""
        try:
            from .utils import session_manager
            conversation_history = await session_manager.get_conversation_history(
                self.user_id
            )
            history_count = len(conversation_history)
            
            await self.send(json.dumps({
                "type": "conversation_paused",
                "message": "本次对话已结束",
                "mode": "one_time",
                "history_count": history_count,
            }))
            
            self.state.active = False
            
        except Exception as e:
            await self.error_boundary.handle_error(e, "发送暂停消息")


class TTSManager:
    """TTS管理器 - 管理TTS相关逻辑"""
    
    def __init__(self, user_id: str, websocket_sender: Callable):
        self.user_id = user_id
        self.send = websocket_sender
        self.config_cache = ConfigCache()
        self.error_boundary = ErrorBoundary(websocket_sender)
    
    async def is_enabled(self) -> bool:
        """检查TTS是否启用"""
        config = await self.config_cache.get_config(get_system_config_async)
        return config.tts_enabled
    
    async def speak(self, text: str, detected_language: str = None, 
                   tts_voice: str = None) -> bool:
        """执行TTS语音合成"""
        try:
            if not await self.is_enabled():
                await self.send(json.dumps({
                    "type": "ai_response_complete",
                    "message": "AI回答已完成（TTS未启用）",
                }))
                return True
            
            # 这里保持原有的TTS逻辑不变，只是封装在管理器中
            # 具体实现将在后续阶段迁移
            return True
            
        except Exception as e:
            await self.error_boundary.handle_error(e, "TTS语音合成", "tts_error")
            return False
    
    async def interrupt(self):
        """中断TTS播放"""
        try:
            from .tts_pool import interrupt_user_tts
            await interrupt_user_tts(self.user_id)
            
            await self.send(json.dumps({
                "type": "tts_interrupt",
                "message": "中断TTS播放",
            }))
            
        except Exception as e:
            await self.error_boundary.handle_error(e, "TTS中断")


class HealthMonitor:
    """健康监控器 - 监控系统健康状态"""
    
    def __init__(self, user_id: str, asr_manager: ASRManager, 
                 task_manager: TaskManager):
        self.user_id = user_id
        self.asr_manager = asr_manager
        self.task_manager = task_manager
        self.is_running = True
    
    async def start_monitoring(self):
        """开始健康监控"""
        await self.task_manager.start_task("health_monitor", self._monitor_loop())
    
    async def stop_monitoring(self):
        """停止健康监控"""
        self.is_running = False
        await self.task_manager.cancel_task("health_monitor")
    
    async def _monitor_loop(self):
        """监控循环"""
        while self.is_running:
            try:
                await asyncio.sleep(5)  # 每5秒检查一次
                
                if not self.is_running:
                    break
                
                # 检查ASR连接状态
                if not self.asr_manager.state.is_ready():
                    logger.warning(f"用户 {self.user_id} ASR连接异常，尝试重连...")
                    await self.asr_manager._reconnect()
                
                # 检查响应处理任务状态
                if not self.task_manager.is_task_running("response_handler"):
                    if self.asr_manager.state.is_ready():
                        logger.warning(f"用户 {self.user_id} 响应处理任务异常，重新启动...")
                        # 这里需要重新启动响应处理任务
                        # 具体实现在后续阶段完成
                
                # 检查连接超时（30分钟）
                if (self.asr_manager.state.asr_client and 
                    hasattr(self.asr_manager.state.asr_client, "connection_created_at")):
                    current_time = time.time()
                    if (current_time - self.asr_manager.state.asr_client.connection_created_at > 1800):
                        logger.warning(f"用户 {self.user_id} ASR连接时间过长，重新连接...")
                        await self.asr_manager._reconnect()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"用户 {self.user_id} 健康检查失败: {e}")
                await asyncio.sleep(3)
