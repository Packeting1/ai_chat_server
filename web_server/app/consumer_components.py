"""
StreamChatConsumer重构组件
按照Linus哲学设计的简洁组件类
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Callable, Dict, Any
import json

logger = logging.getLogger(__name__)


class ConnectionStatus(Enum):
    """连接状态枚举"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting" 
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class ConnectionState:
    """ASR连接状态管理 - 单一数据源"""
    asr_client: Optional[Any] = None
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    last_activity: float = field(default_factory=time.time)
    reconnect_attempts: int = 0
    max_reconnect_attempts: int = 3
    
    def is_ready(self) -> bool:
        """检查连接是否就绪"""
        return (self.asr_client is not None and 
                self.status == ConnectionStatus.CONNECTED and
                hasattr(self.asr_client, 'is_connected') and
                self.asr_client.is_connected())
    
    def mark_active(self):
        """标记连接活跃"""
        self.last_activity = time.time()
    
    def should_reconnect(self) -> bool:
        """判断是否应该重连"""
        return (self.status != ConnectionStatus.CONNECTED and 
                self.reconnect_attempts < self.max_reconnect_attempts)


@dataclass
class ConversationState:
    """对话状态管理 - 集中管理所有对话相关状态"""
    active: bool = True
    accumulated_text: str = ""
    last_complete_text: str = ""
    ai_speaking: bool = False
    detected_language: Optional[str] = None
    tts_voice: Optional[str] = None
    
    def reset_current_turn(self):
        """重置当前轮次状态"""
        self.accumulated_text = ""
        self.ai_speaking = False
    
    def update_language_info(self, language: Optional[str], voice: Optional[str]):
        """更新语言检测信息"""
        if language:
            self.detected_language = language
        if voice:
            self.tts_voice = voice


class AudioBuffer:
    """音频缓冲管理 - 简单高效的音频缓冲"""
    
    def __init__(self, max_size_bytes: int = 160 * 1024):  # 160KB上限
        self.chunks: List[bytes] = []
        self.total_bytes: int = 0
        self.max_size_bytes = max_size_bytes
    
    def add(self, data: bytes) -> bool:
        """添加音频数据，返回是否成功"""
        if self.total_bytes + len(data) <= self.max_size_bytes:
            self.chunks.append(data)
            self.total_bytes += len(data)
            return True
        return False
    
    def flush(self) -> List[bytes]:
        """获取所有缓冲数据并清空"""
        chunks = self.chunks.copy()
        self.clear()
        return chunks
    
    def clear(self):
        """清空缓冲"""
        self.chunks.clear()
        self.total_bytes = 0
    
    def is_empty(self) -> bool:
        """检查缓冲是否为空"""
        return len(self.chunks) == 0


class ThinkTagProcessor:
    """Think标签处理器 - 消除重复代码的单一实现"""
    
    def __init__(self, filter_enabled: bool):
        self.filter_enabled = filter_enabled
        self.reset()
    
    def reset(self):
        """重置处理状态"""
        self.in_think_block = False
        self.is_start_output = True
        self.pending_content = ""
    
    def process_chunk(self, chunk: str) -> str:
        """处理文本块，返回过滤后的内容"""
        if not self.filter_enabled:
            return chunk
        
        result = []
        for char in chunk:
            processed_char = self._process_char(char)
            if processed_char:
                result.append(processed_char)
        
        return ''.join(result)
    
    def _process_char(self, char: str) -> str:
        """处理单个字符"""
        if self.in_think_block:
            return self._handle_think_block(char)
        elif self.is_start_output:
            return self._handle_start_output(char)
        else:
            return self._handle_normal_output(char)
    
    def _handle_think_block(self, char: str) -> str:
        """处理think块内的字符"""
        if char == "<" and not self.pending_content:
            self.pending_content = "<"
        elif self.pending_content and len(self.pending_content) < 8:
            self.pending_content += char
            if self.pending_content == "</think>":
                self.in_think_block = False
                self.pending_content = ""
            elif not "</think>".startswith(self.pending_content):
                self.pending_content = ""
        else:
            self.pending_content = ""
        return ""  # think块内容不输出
    
    def _handle_start_output(self, char: str) -> str:
        """处理开头输出状态"""
        if char.isspace():
            return ""
        elif char == "<":
            self.pending_content = "<"
            return ""
        elif self.pending_content and len(self.pending_content) < 7:
            self.pending_content += char
            if self.pending_content == "<think>":
                self.in_think_block = True
                self.pending_content = ""
                return ""
            elif not "<think>".startswith(self.pending_content):
                self.is_start_output = False
                content = self.pending_content
                self.pending_content = ""
                return content
            return ""
        else:
            self.is_start_output = False
            content = self.pending_content + char if self.pending_content else char
            self.pending_content = ""
            return content
    
    def _handle_normal_output(self, char: str) -> str:
        """处理正常输出状态"""
        if char == "<" and not self.pending_content:
            self.pending_content = "<"
            return ""
        elif self.pending_content and len(self.pending_content) < 7:
            self.pending_content += char
            if self.pending_content == "<think>":
                self.in_think_block = True
                self.pending_content = ""
                return ""
            elif not "<think>".startswith(self.pending_content):
                content = self.pending_content
                self.pending_content = ""
                return content
            return ""
        else:
            return char
    
    def finalize(self) -> str:
        """完成处理，返回剩余内容"""
        if (self.pending_content and 
            not self.in_think_block and 
            not self.is_start_output):
            content = self.pending_content
            self.pending_content = ""
            return content
        return ""


class TaskManager:
    """异步任务管理器 - 简化任务生命周期管理"""
    
    def __init__(self):
        self.tasks: Dict[str, asyncio.Task] = {}
    
    async def start_task(self, name: str, coro) -> bool:
        """启动新任务"""
        try:
            await self.cancel_task(name)
            self.tasks[name] = asyncio.create_task(coro)
            return True
        except Exception as e:
            logger.error(f"启动任务 {name} 失败: {e}")
            return False
    
    async def cancel_task(self, name: str):
        """取消指定任务"""
        if name in self.tasks:
            task = self.tasks[name]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self.tasks[name]
    
    async def cancel_all(self):
        """取消所有任务"""
        for name in list(self.tasks.keys()):
            await self.cancel_task(name)
    
    def is_task_running(self, name: str) -> bool:
        """检查任务是否在运行"""
        return (name in self.tasks and 
                not self.tasks[name].done())


class MessageRouter:
    """消息路由器 - 简化消息处理逻辑"""
    
    def __init__(self):
        self.handlers: Dict[str, Callable] = {}
    
    def register(self, message_type: str, handler: Callable):
        """注册消息处理器"""
        self.handlers[message_type] = handler
    
    async def route(self, message: Dict[str, Any]):
        """路由消息到对应处理器"""
        message_type = message.get("type")
        if message_type in self.handlers:
            try:
                await self.handlers[message_type](message)
            except Exception as e:
                logger.error(f"处理消息 {message_type} 失败: {e}")
                raise
        else:
            logger.warning(f"未知消息类型: {message_type}")


class ConfigCache:
    """配置缓存 - 减少数据库查询"""
    
    def __init__(self, cache_duration: float = 30.0):  # 30秒缓存
        self.cache_duration = cache_duration
        self.cached_config = None
        self.cache_time = 0
    
    async def get_config(self, config_getter: Callable):
        """获取配置（带缓存）"""
        current_time = time.time()
        if (self.cached_config is None or 
            current_time - self.cache_time > self.cache_duration):
            self.cached_config = await config_getter()
            self.cache_time = current_time
        return self.cached_config
    
    def invalidate(self):
        """使缓存失效"""
        self.cached_config = None
        self.cache_time = 0


class ErrorBoundary:
    """错误边界 - 统一错误处理"""
    
    def __init__(self, websocket_sender: Callable):
        self.send = websocket_sender
    
    async def handle_error(self, error: Exception, context: str, 
                          error_type: str = "error"):
        """统一错误处理"""
        error_msg = f"{context}: {str(error)}"
        logger.error(error_msg)
        
        try:
            await self.send(json.dumps({
                "type": error_type,
                "message": error_msg,
                "context": context
            }))
        except Exception as send_error:
            logger.error(f"发送错误消息失败: {send_error}")
