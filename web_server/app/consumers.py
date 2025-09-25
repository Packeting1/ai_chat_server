"""
StreamChatConsumer V2 - 重构后的简化版本
采用Linus式设计哲学：简单、清晰、可靠
"""
import asyncio
import json
import logging
from channels.exceptions import StopConsumer
from channels.generic.websocket import AsyncWebsocketConsumer

from .consumer_components import (
    ConversationState, TaskManager, MessageRouter
)
from .consumer_managers import (
    ASRManager, ConversationManager, TTSManager, HealthMonitor
)
from .consumer_processors import (
    LLMProcessor, ASRProcessor, AudioProcessor, 
    MessageProcessor, WorkflowOrchestrator
)
from .utils import session_manager

logger = logging.getLogger(__name__)


class StreamChatConsumerV2(AsyncWebsocketConsumer):
    """
    流式聊天WebSocket消费者 V2
    重构后的简化版本，每个组件职责单一
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = None
        self.is_running = False
        
        # 核心状态
        self.conversation_state = ConversationState()
        
        # 管理器组件
        self.task_manager = TaskManager()
        self.message_router = MessageRouter()
        
        # 业务组件（将在connect中初始化）
        self.asr_manager = None
        self.conversation_manager = None
        self.tts_manager = None
        self.health_monitor = None
        
        # 处理器组件（将在connect中初始化）
        self.llm_processor = None
        self.asr_processor = None
        self.audio_processor = None
        self.message_processor = None
        self.workflow_orchestrator = None
    
    async def connect(self):
        """建立WebSocket连接"""
        await self.accept()
        
        try:
            # 初始化用户会话
            await self._initialize_session()
            
            # 初始化所有组件
            await self._initialize_components()
            
            # 设置消息路由
            self._setup_message_routing()
            
            # 启动后台任务
            await self._start_background_tasks()
            
            # 标记为运行状态
            self.is_running = True
            
            # 发送初始状态信息
            await self._send_initial_state()
            
        except Exception as e:
            logger.error(f"连接初始化失败: {e}")
            await self.close(code=1011, reason="初始化失败")
    
    async def disconnect(self, close_code):
        """断开WebSocket连接"""
        logger.info(f"用户 {self.user_id} 连接断开，code: {close_code}")
        
        # 停止运行标志
        self.is_running = False
        self.conversation_state.active = False
        
        try:
            # 停止健康监控
            if self.health_monitor:
                await self.health_monitor.stop_monitoring()
            
            # 中断TTS
            if self.tts_manager:
                await self.tts_manager.interrupt()
            
            # 断开ASR连接
            if self.asr_manager:
                await self.asr_manager.disconnect()
            
            # 取消所有任务
            await self.task_manager.cancel_all()
            
        except Exception as e:
            logger.error(f"断开连接清理失败: {e}")
        
        raise StopConsumer()
    
    async def receive(self, text_data=None, bytes_data=None):
        """接收WebSocket消息"""
        try:
            if text_data:
                message = json.loads(text_data)
                await self.message_router.route(message)
                
            elif bytes_data:
                await self.audio_processor.process_binary_data(bytes_data)
                
        except json.JSONDecodeError:
            logger.error("收到无效的JSON数据")
        except Exception as e:
            logger.error(f"处理WebSocket消息失败: {e}")
    
    async def _initialize_session(self):
        """初始化用户会话"""
        # 检查URL参数中是否有保存的用户ID
        query_string = self.scope.get("query_string", b"").decode("utf-8")
        query_params = dict(
            param.split("=") for param in query_string.split("&") if "=" in param
        )
        saved_user_id = query_params.get("saved_user_id")
        
        if saved_user_id:
            # 尝试恢复会话
            existing_session = await session_manager.get_session(saved_user_id)
            if existing_session:
                self.user_id = saved_user_id
            else:
                self.user_id = await session_manager.create_session()
                logger.warning(f"保存的会话不存在，创建新会话: {self.user_id}")
        else:
            self.user_id = await session_manager.create_session()
        
        # 发送用户连接通知
        user_count = await session_manager.get_user_count()
        await self.send(text_data=json.dumps({
            "type": "user_connected",
            "user_id": self.user_id,
            "active_users": user_count,
        }))
    
    async def _initialize_components(self):
        """初始化所有组件"""
        # 管理器组件
        self.asr_manager = ASRManager(self.user_id, self.send)
        self.conversation_manager = ConversationManager(self.user_id, self.send)
        self.tts_manager = TTSManager(self.user_id, self.send)
        
        # 处理器组件
        self.llm_processor = LLMProcessor(
            self.user_id, self.send, self.conversation_state
        )
        self.asr_processor = ASRProcessor(
            self.user_id, self.send, self.conversation_state
        )
        self.audio_processor = AudioProcessor(
            self.user_id, self.asr_manager, self.conversation_state
        )
        self.message_processor = MessageProcessor(
            self.user_id, self.send, self.conversation_manager,
            self.llm_processor, self.audio_processor
        )
        self.workflow_orchestrator = WorkflowOrchestrator(
            self.user_id, self.send, self.conversation_state,
            self.conversation_manager, self.llm_processor, self.tts_manager
        )
        
        # 健康监控
        self.health_monitor = HealthMonitor(
            self.user_id, self.asr_manager, self.task_manager
        )
    
    def _setup_message_routing(self):
        """设置消息路由"""
        # 注册消息处理器
        self.message_router.register("audio_data", self._handle_audio_data)
        self.message_router.register("reset_conversation", self._handle_reset)
        self.message_router.register("restart_conversation", self._handle_restart)
        self.message_router.register("get_conversation_mode", self._handle_get_mode)
        self.message_router.register("test_llm", self._handle_test_llm)
    
    async def _start_background_tasks(self):
        """启动后台任务"""
        # 初始化ASR连接
        await self.asr_manager.initialize()
        
        # 启动ASR响应处理
        await self.asr_manager.start_response_handler(
            self._create_asr_response_handler
        )
        
        # 启动健康监控
        await self.health_monitor.start_monitoring()
        
        # 初始化TTS连接池
        try:
            from .tts_pool import initialize_tts_pool_with_manager
            await initialize_tts_pool_with_manager()
            logger.info("TTS连接池和任务管理器初始化完成")
        except Exception as e:
            logger.error(f"初始化TTS连接池失败: {e}")
    
    async def _send_initial_state(self):
        """发送初始状态信息"""
        await self.conversation_manager.get_mode_info()
    
    def _create_asr_response_handler(self):
        """创建ASR响应处理协程"""
        return self._asr_response_loop()
    
    async def _asr_response_loop(self):
        """ASR响应处理循环"""
        try:
            while self.is_running:
                try:
                    # 确保ASR连接就绪
                    if not await self.asr_manager.ensure_ready():
                        await asyncio.sleep(0.2)
                        continue
                    
                    # 接收ASR响应
                    data = await self.asr_manager.state.asr_client.receive_message(
                        timeout=1.0
                    )
                    if data is None:
                        continue
                    
                    # 处理识别结果
                    user_text = await self.asr_processor.process_recognition_result(data)
                    
                    # 如果有最终识别结果，启动完整工作流程
                    if user_text:
                        await self.workflow_orchestrator.handle_user_input(user_text)
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if self.is_running:
                        logger.error(f"ASR响应处理失败: {e}")
                    break
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"ASR响应处理任务异常: {e}")
    
    # 消息处理器方法
    async def _handle_audio_data(self, message):
        """处理音频数据消息"""
        await self.message_processor.process_message(message)
    
    async def _handle_reset(self, message):
        """处理重置对话消息"""
        await self.message_processor.process_message(message)
    
    async def _handle_restart(self, message):
        """处理重启对话消息"""
        await self.message_processor.process_message(message)
        
        # 重启后确保ASR连接就绪
        await self.asr_manager.ensure_ready()
        
        # 发送ASR连接状态确认
        await self.send(text_data=json.dumps({
            "type": "asr_connected",
            "message": "ASR服务已就绪，可以开始语音识别"
        }))
    
    async def _handle_get_mode(self, message):
        """处理获取对话模式消息"""
        await self.message_processor.process_message(message)
    
    async def _handle_test_llm(self, message):
        """处理LLM测试消息"""
        await self.message_processor.process_message(message)
