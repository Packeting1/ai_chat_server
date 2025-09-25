"""
StreamChatConsumer处理器类
封装复杂的业务逻辑处理
"""
import asyncio
import logging
import json
from typing import Optional, Callable, AsyncGenerator

from .consumer_components import (
    ConversationState, ThinkTagProcessor, ConfigCache, ErrorBoundary
)
from .llm_client import call_llm_stream, filter_think_tags
from .utils import (
    get_system_config_async, process_recognition_result, 
    add_language_tag_to_text, session_manager
)

logger = logging.getLogger(__name__)


class LLMProcessor:
    """LLM处理器 - 封装所有LLM相关逻辑"""
    
    def __init__(self, user_id: str, websocket_sender: Callable, 
                 conversation_state: ConversationState):
        self.user_id = user_id
        self.send = websocket_sender
        self.conversation_state = conversation_state
        self.config_cache = ConfigCache()
        self.error_boundary = ErrorBoundary(websocket_sender)
        self.think_processor = None
    
    async def process_and_respond(self, user_input: str) -> str:
        """处理用户输入并生成响应"""
        try:
            # 检查对话状态
            if not self.conversation_state.active:
                logger.warning(f"用户 {self.user_id} 对话已不活跃，跳过LLM调用")
                return ""
            
            # 设置AI说话状态
            self.conversation_state.ai_speaking = True
            
            # 发送AI开始回答的通知
            await self.send(json.dumps({
                "type": "ai_start",
                "user_text": user_input,
                "message": "AI正在思考...",
            }))
            
            # 获取配置和对话历史
            config = await self.config_cache.get_config(get_system_config_async)
            conversation_history = await session_manager.get_conversation_history(
                self.user_id
            )
            
            # 为LLM调用添加语言标签
            tagged_input = add_language_tag_to_text(
                user_input, self.conversation_state.detected_language
            )
            
            # 初始化think标签处理器
            self.think_processor = ThinkTagProcessor(config.filter_think_tags)
            
            # 流式处理LLM响应
            full_response = ""
            async for chunk in call_llm_stream(tagged_input, conversation_history):
                if not self.conversation_state.active:
                    logger.info(f"用户 {self.user_id} 对话中止")
                    break
                
                if chunk:
                    full_response += chunk
                    
                    # 处理think标签
                    processed_chunk = self.think_processor.process_chunk(chunk)
                    if processed_chunk:
                        await self.send(json.dumps({
                            "type": "ai_chunk",
                            "content": processed_chunk
                        }))
                
                # 控制发送频率
                await asyncio.sleep(0.005)
            
            # 处理剩余内容
            final_chunk = self.think_processor.finalize()
            if final_chunk:
                await self.send(json.dumps({
                    "type": "ai_chunk",
                    "content": final_chunk
                }))
            
            # 过滤最终响应
            filtered_response = filter_think_tags(
                full_response, config.filter_think_tags
            )
            
            # 发送完成通知
            await self.send(json.dumps({
                "type": "ai_complete",
                "full_response": filtered_response
            }))
            
            # 保存对话历史
            await session_manager.add_conversation(
                self.user_id, user_input, filtered_response,
                config.max_conversation_history
            )
            
            return filtered_response
            
        except Exception as e:
            await self.error_boundary.handle_error(e, "LLM处理", "ai_error")
            
            # 确保前端状态恢复
            await self.send(json.dumps({
                "type": "ai_response_complete",
                "message": "AI回答失败，对话可继续",
            }))
            
            return ""
        
        finally:
            self.conversation_state.ai_speaking = False


class ASRProcessor:
    """ASR处理器 - 处理语音识别结果"""
    
    def __init__(self, user_id: str, websocket_sender: Callable,
                 conversation_state: ConversationState):
        self.user_id = user_id
        self.send = websocket_sender
        self.conversation_state = conversation_state
        self.config_cache = ConfigCache()
        self.error_boundary = ErrorBoundary(websocket_sender)
    
    async def process_recognition_result(self, data: dict) -> Optional[str]:
        """处理ASR识别结果"""
        try:
            if "text" not in data:
                return None
            
            raw_text = data["text"]
            mode = data.get("mode", "")
            
            # 获取配置并处理识别结果
            config = await self.config_cache.get_config(get_system_config_async)
            result = process_recognition_result(raw_text, config)
            display_text = result["cleaned_text"]
            
            # 更新语言信息
            if result["detected_language"]:
                self.conversation_state.update_language_info(
                    result["detected_language"], result["tts_voice"]
                )
            
            if mode == "2pass-online":
                # 实时结果
                return await self._handle_partial_result(display_text)
                
            elif mode in ["2pass-offline", "offline"]:
                # 最终结果
                return await self._handle_final_result(display_text)
            
            return None
            
        except Exception as e:
            await self.error_boundary.handle_error(e, "ASR结果处理")
            return None
    
    async def _handle_partial_result(self, text: str) -> Optional[str]:
        """处理实时识别结果"""
        self.conversation_state.accumulated_text = text
        
        # 如果AI正在说话且识别到有效文本，中断TTS
        if (self.conversation_state.ai_speaking and text and text.strip()):
            await self._interrupt_tts("用户开始说话")
        
        # 发送实时结果
        await self.send(json.dumps({
            "type": "recognition_partial",
            "text": text,
        }))
        
        return None  # 实时结果不触发LLM
    
    async def _handle_final_result(self, text: str) -> Optional[str]:
        """处理最终识别结果"""
        self.conversation_state.accumulated_text = text
        
        # 检查是否有有效的新文本
        if (text and text.strip() and 
            text != self.conversation_state.last_complete_text and
            self.conversation_state.active):
            
            self.conversation_state.last_complete_text = text
            
            # 确保TTS已中断
            if self.conversation_state.ai_speaking:
                await self._interrupt_tts("用户完成输入")
            
            # 发送最终结果
            await self.send(json.dumps({
                "type": "recognition_final",
                "text": text,
            }))
            
            # 检查对话模式，一次性对话模式下停止监听
            config = await self.config_cache.get_config(get_system_config_async)
            if not config.continuous_conversation:
                self.conversation_state.active = False
            
            return text  # 返回文本用于LLM处理
        
        return None
    
    async def _interrupt_tts(self, reason: str):
        """中断TTS播放"""
        try:
            await self.send(json.dumps({
                "type": "tts_interrupt",
                "message": "中断TTS播放",
                "reason": reason,
            }))
            
            from .tts_pool import interrupt_user_tts
            await interrupt_user_tts(self.user_id)
            
        except Exception as e:
            logger.error(f"中断TTS失败: {e}")


class AudioProcessor:
    """音频处理器 - 处理音频数据流"""
    
    def __init__(self, user_id: str, asr_manager, conversation_state: ConversationState):
        self.user_id = user_id
        self.asr_manager = asr_manager
        self.conversation_state = conversation_state
    
    async def process_binary_data(self, audio_data: bytes) -> bool:
        """处理二进制音频数据"""
        # 检查对话是否活跃
        if not self.conversation_state.active:
            return False
        
        # 委托给ASR管理器处理
        return await self.asr_manager.process_audio(audio_data)
    
    async def process_base64_data(self, audio_data_b64: str) -> bool:
        """处理Base64编码的音频数据"""
        try:
            import base64
            audio_data = base64.b64decode(audio_data_b64)
            return await self.process_binary_data(audio_data)
        except Exception as e:
            logger.error(f"解码Base64音频数据失败: {e}")
            return False


class MessageProcessor:
    """消息处理器 - 统一处理WebSocket消息"""
    
    def __init__(self, user_id: str, websocket_sender: Callable,
                 conversation_manager, llm_processor: LLMProcessor,
                 audio_processor: AudioProcessor):
        self.user_id = user_id
        self.send = websocket_sender
        self.conversation_manager = conversation_manager
        self.llm_processor = llm_processor
        self.audio_processor = audio_processor
        self.error_boundary = ErrorBoundary(websocket_sender)
    
    async def process_message(self, message: dict):
        """处理WebSocket消息"""
        message_type = message.get("type")
        
        try:
            if message_type == "audio_data":
                await self.audio_processor.process_base64_data(message.get("data"))
                
            elif message_type == "reset_conversation":
                await self.conversation_manager.handle_reset()
                
            elif message_type == "restart_conversation":
                await self.conversation_manager.handle_restart()
                
            elif message_type == "get_conversation_mode":
                await self.conversation_manager.get_mode_info()
                
            elif message_type == "test_llm":
                await self._handle_test_llm()
                
            else:
                logger.warning(f"未知消息类型: {message_type}")
                
        except Exception as e:
            await self.error_boundary.handle_error(e, f"处理消息 {message_type}")
    
    async def _handle_test_llm(self):
        """处理LLM测试"""
        try:
            from .llm_client import test_llm_connection
            result = await test_llm_connection()
            await self.send(json.dumps({
                "type": "llm_test_result", 
                "result": result
            }))
        except Exception as e:
            await self.send(json.dumps({
                "type": "llm_test_result",
                "result": {
                    "success": False,
                    "error": "测试失败",
                    "details": str(e),
                },
            }))


class WorkflowOrchestrator:
    """工作流编排器 - 协调各个组件的工作流程"""
    
    def __init__(self, user_id: str, websocket_sender: Callable,
                 conversation_state: ConversationState,
                 conversation_manager, llm_processor: LLMProcessor,
                 tts_manager):
        self.user_id = user_id
        self.send = websocket_sender
        self.conversation_state = conversation_state
        self.conversation_manager = conversation_manager
        self.llm_processor = llm_processor
        self.tts_manager = tts_manager
        self.config_cache = ConfigCache()
    
    async def handle_user_input(self, user_text: str):
        """处理用户输入的完整工作流程"""
        try:
            # 1. LLM处理
            ai_response = await self.llm_processor.process_and_respond(user_text)
            
            if not ai_response:
                return
            
            # 2. TTS处理
            try:
                await self.tts_manager.speak(
                    ai_response,
                    self.conversation_state.detected_language,
                    self.conversation_state.tts_voice
                )
            except Exception as tts_error:
                logger.error(f"TTS调用失败: {tts_error}")
                # TTS失败不影响对话流程
                await self.send(json.dumps({
                    "type": "ai_response_complete",
                    "message": "AI回答已完成，TTS语音合成失败但对话可继续",
                }))
            
            # 3. 检查对话模式
            if not await self.conversation_manager.should_continue_after_response():
                # 一次性对话模式
                config = await self.config_cache.get_config(get_system_config_async)
                if not config.tts_enabled:
                    # TTS未启用时立即发送暂停消息
                    await self.conversation_manager.send_paused_message()
                # 如果TTS启用，暂停消息将在TTS完成后发送
            
        except Exception as e:
            logger.error(f"处理用户输入工作流程失败: {e}")
            await self.send(json.dumps({
                "type": "ai_response_complete",
                "message": "处理失败，对话可继续",
            }))
