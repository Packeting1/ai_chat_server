import asyncio
import base64
import json
import logging
import time

from channels.exceptions import StopConsumer
from channels.generic.websocket import AsyncWebsocketConsumer

from .audio_processor import get_audio_info, process_audio_data
from .funasr_client import FunASRClient, create_stream_config_async
from .funasr_pool import get_connection_pool
from .llm_client import call_llm_stream, filter_think_tags
from .models import SystemConfig
from .tts_pool import get_tts_pool, interrupt_user_tts, tts_speak_stream
from .utils import clean_recognition_text, get_system_config_async, session_manager

logger = logging.getLogger(__name__)


class StreamChatConsumer(AsyncWebsocketConsumer):
    """流式聊天WebSocket消费者"""

    async def connect(self):
        await self.accept()

        # 为每个连接分配唯一的用户ID
        self.user_id = await session_manager.create_session()

        # 发送用户ID到前端
        user_count = await session_manager.get_user_count()
        await self.send(
            text_data=json.dumps(
                {
                    "type": "user_connected",
                    "user_id": self.user_id,
                    "active_users": user_count,
                }
            )
        )

        self.funasr_client = None
        self.funasr_task = None
        self.is_running = True
        self.asr_connected = False

        # 用于累积文本和状态管理
        self.accumulated_text = ""
        self.last_complete_text = ""
        self.is_ai_speaking = False

        # 初始化TTS连接池
        await self.initialize_tts_pool()

        # 连接到FunASR服务
        await self.connect_funasr()

    async def disconnect(self, close_code):
        self.is_running = False

        # 立即中断TTS播放，避免资源泄露
        try:
            from .tts_pool import interrupt_user_tts

            await interrupt_user_tts(self.user_id)
        except Exception as e:
            logger.error(f"中断用户 {self.user_id} TTS播放失败: {e}")

        # 取消所有异步任务
        if self.funasr_task:
            self.funasr_task.cancel()
            try:
                await self.funasr_task
            except asyncio.CancelledError:
                pass

        # 取消健康检查任务
        if hasattr(self, "health_check_task") and self.health_check_task:
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                pass

        # 根据配置决定如何处理连接
        if self.funasr_client:
            try:
                config = await get_system_config_async()

                if config.use_connection_pool:
                    # 连接池模式：释放连接
                    pool = await get_connection_pool()
                    await pool.release_connection(self.user_id)
                else:
                    # 独立连接模式：直接断开
                    await self.funasr_client.disconnect()
            except Exception as e:
                logger.error(f"处理FunASR连接断开失败: {e}")

        # 清理TTS连接池中的用户状态
        try:
            tts_pool = await get_tts_pool()
            if (
                hasattr(tts_pool, "user_playing_status")
                and self.user_id in tts_pool.user_playing_status
            ):
                async with tts_pool._lock:
                    del tts_pool.user_playing_status[self.user_id]
        except Exception as e:
            logger.error(f"清理用户 {self.user_id} TTS状态失败: {e}")

        # 清理用户会话
        await session_manager.remove_session(self.user_id)

        raise StopConsumer()

    async def connect_funasr(self):
        """连接到FunASR服务（支持连接池和独立连接模式）"""
        try:
            # 获取配置，决定使用连接池还是独立连接
            config = await get_system_config_async()

            if config.use_connection_pool:
                # 连接池模式
                pool = await get_connection_pool()
                self.funasr_client = await pool.get_connection(self.user_id)

                if self.funasr_client is None:
                    raise Exception("连接池已满，无法获取FunASR连接")

                # 发送ASR连接成功通知到前端
                pool_stats = pool.get_stats()
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "asr_connected",
                            "message": "ASR服务器连接成功（连接池模式）",
                            "connection_mode": "pool",
                            "pool_stats": pool_stats,
                        }
                    )
                )
            else:
                # 独立连接模式
                self.funasr_client = FunASRClient()
                await self.funasr_client.connect()

                # 发送初始配置
                stream_config = await create_stream_config_async()
                await self.funasr_client.send_config(stream_config)

                # 发送ASR连接成功通知到前端
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "asr_connected",
                            "message": "ASR服务器连接成功（独立连接模式）",
                            "connection_mode": "independent",
                            "config": stream_config,
                        }
                    )
                )

            self.asr_connected = True

            # 启动FunASR响应处理任务
            self.funasr_task = asyncio.create_task(self.handle_funasr_responses())

            # 启动连接健康检查任务
            self.health_check_task = asyncio.create_task(self.connection_health_check())

        except Exception as asr_error:
            logger.error(f"用户 {self.user_id} 连接FunASR失败: {asr_error}")
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "asr_connection_failed",
                        "message": "无法连接到ASR服务器，请检查服务状态",
                        "error": str(asr_error),
                    }
                )
            )

    async def reconnect_funasr(self):
        """重新获取FunASR连接"""
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # 停止当前的响应处理任务
                if self.funasr_task and not self.funasr_task.done():
                    self.funasr_task.cancel()

                # 释放当前连接
                if self.funasr_client:
                    try:
                        config = await get_system_config_async()

                        if config.use_connection_pool:
                            pool = await get_connection_pool()
                            await pool.release_connection(self.user_id)
                        else:
                            await self.funasr_client.disconnect()
                    except Exception as e:
                        logger.error(f"释放连接失败: {e}")

                # 等待一小段时间再重连
                await asyncio.sleep(1)

                # 重新从连接池获取连接
                await self.connect_funasr()

                return

            except Exception as e:
                retry_count += 1
                logger.error(
                    f"用户 {self.user_id} FunASR重连失败 (尝试 {retry_count}/{max_retries}): {e}"
                )

                if retry_count < max_retries:
                    # 等待一段时间再重试
                    await asyncio.sleep(2 * retry_count)  # 递增等待时间
                else:
                    # 所有重试都失败了
                    self.asr_connected = False
                    await self.send(
                        text_data=json.dumps(
                            {
                                "type": "asr_reconnect_failed",
                                "message": "ASR服务重连失败，请刷新页面重试",
                                "error": str(e),
                            }
                        )
                    )

    async def receive(self, text_data=None, bytes_data=None):
        """接收WebSocket消息"""
        try:
            if text_data:
                # 处理文本消息
                message = json.loads(text_data)
                message_type = message.get("type")

                if message_type == "audio_data":
                    await self.handle_audio_data(message.get("data"))
                elif message_type == "reset_conversation":
                    await self.handle_reset_conversation()
                elif message_type == "test_llm":
                    await self.handle_test_llm()

            elif bytes_data:
                # 处理二进制数据（直接的音频数据）
                await self.handle_binary_audio_data(bytes_data)

        except json.JSONDecodeError:
            logger.error("收到无效的JSON数据")
        except Exception as e:
            logger.error(f"处理WebSocket消息失败: {e}")

    async def handle_binary_audio_data(self, audio_data):
        """处理二进制音频数据"""
        if not self.asr_connected or not self.funasr_client:
            logger.warning(f"⚠️ 用户 {self.user_id} ASR未连接，音频数据被丢弃")
            return

        try:
            # 检查连接状态
            if not self.funasr_client.is_connected():
                logger.warning(f"🔌 用户 {self.user_id} FunASR连接已断开，尝试重连...")
                await self.reconnect_funasr()
                return

            # 直接发送二进制音频数据到FunASR
            await self.funasr_client.send_audio_data(audio_data)

        except Exception as e:
            logger.error(f"处理二进制音频数据失败: {e}")

            # 向前端发送错误通知
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "asr_error",
                        "message": "语音识别服务暂时不可用，正在尝试重连...",
                        "error": str(e),
                    }
                )
            )

            # 连接失败时尝试重连
            await self.reconnect_funasr()

    async def handle_audio_data(self, audio_data_b64):
        """处理音频数据"""
        if not self.asr_connected or not self.funasr_client:
            return

        try:
            # 解码Base64音频数据
            audio_data = base64.b64decode(audio_data_b64)

            # 检查连接状态
            if not self.funasr_client.is_connected():
                logger.warning(f"用户 {self.user_id} FunASR连接已断开，尝试重连...")
                await self.reconnect_funasr()
                return

            # 发送音频数据到FunASR
            await self.funasr_client.send_audio_data(audio_data)

        except Exception as e:
            logger.error(f"处理音频数据失败: {e}")
            # 连接失败时尝试重连
            await self.reconnect_funasr()

    async def handle_funasr_responses(self):
        """处理FunASR的识别结果"""
        try:
            while self.is_running:
                try:
                    # 检查FunASR连接状态
                    if not self.funasr_client or not self.funasr_client.is_connected():
                        logger.warning(
                            f"用户 {self.user_id} FunASR连接已断开，停止响应处理"
                        )
                        break

                    data = await self.funasr_client.receive_message(timeout=1.0)
                    if data is None:
                        continue

                    if "text" in data and self.is_running:
                        raw_text = data["text"]
                        mode = data.get("mode", "")

                        if mode == "2pass-online":
                            # 实时结果，更新显示
                            self.accumulated_text = raw_text
                            display_text = clean_recognition_text(raw_text)

                            # 只有在AI正在说话且识别到有效文本时才中断TTS
                            if (
                                self.is_ai_speaking
                                and display_text
                                and display_text.strip()
                            ):
                                await self.send_tts_interrupt("用户开始说话")
                                from .tts_pool import interrupt_user_tts

                                await interrupt_user_tts(self.user_id)

                            if self.is_running:
                                await self.send(
                                    text_data=json.dumps(
                                        {
                                            "type": "recognition_partial",
                                            "text": display_text,
                                        }
                                    )
                                )

                        elif mode == "2pass-offline" or mode == "offline":
                            # 最终结果，检查是否需要调用LLM
                            self.accumulated_text = raw_text
                            display_text = clean_recognition_text(raw_text)

                            # 检查是否有有效的新文本
                            if (
                                display_text
                                and display_text.strip()
                                and display_text != self.last_complete_text
                            ):
                                self.last_complete_text = display_text

                                # 如果AI仍在说话，在用户完成输入时确保TTS已中断
                                if self.is_ai_speaking:
                                    await self.send_tts_interrupt("用户完成输入")
                                    from .tts_pool import interrupt_user_tts

                                    await interrupt_user_tts(self.user_id)

                                # 发送最终识别结果
                                if self.is_running:
                                    await self.send(
                                        text_data=json.dumps(
                                            {
                                                "type": "recognition_final",
                                                "text": display_text,
                                            }
                                        )
                                    )

                                # 调用LLM获取回答
                                await self.call_llm_and_respond(display_text)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if self.is_running:
                        logger.error(f"处理FunASR响应失败: {e}")
                    # 发生异常时也退出循环
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"FunASR响应处理任务异常: {e}")

    async def call_llm_and_respond(self, user_input):
        """调用LLM并发送响应"""
        try:
            self.is_ai_speaking = True

            # 发送TTS中断信号给前端
            await self.send_tts_interrupt("AI开始回答")

            # 中断当前用户的TTS播放
            await interrupt_user_tts(self.user_id)

            # 获取对话历史
            conversation_history = await session_manager.get_conversation_history(
                self.user_id
            )

            # 发送AI开始回答的通知
            if self.is_running:
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "ai_start",
                            "user_text": user_input,
                            "message": "AI正在思考...",
                        }
                    )
                )

            # 获取系统配置，决定是否启用think标签过滤
            config = await get_system_config_async()

            # 流式调用LLM - 使用跳过方式的实时处理
            full_response = ""
            in_think_block = False
            is_start_output = True  # flag: 是否还在开头输出状态
            pending_content = ""  # 暂存可能需要跳过的内容

            async for chunk in call_llm_stream(user_input, conversation_history):
                if not self.is_running:
                    break

                if chunk:  # 确保chunk不为空
                    full_response += chunk

                    # 根据配置决定是否过滤think标签
                    if not config.filter_think_tags:
                        # 如果不过滤，直接发送所有内容
                        await self.send(
                            text_data=json.dumps({"type": "ai_chunk", "content": chunk})
                        )
                    else:
                        # 改进的逐字符跳过处理
                        for char in chunk:
                            # 优先处理think块逻辑
                            if in_think_block:
                                # 在think块内，检查结束标签
                                if char == "<" and not pending_content:
                                    pending_content = "<"
                                elif pending_content and len(pending_content) < 8:
                                    pending_content += char
                                    if pending_content == "</think>":
                                        in_think_block = False
                                        pending_content = ""
                                    elif not "</think>".startswith(pending_content):
                                        # 不是结束标签，重置暂存
                                        pending_content = ""
                                else:
                                    # 超出长度，重置暂存
                                    pending_content = ""
                                # think块内的所有字符都跳过（不发送）
                            elif is_start_output:
                                # 在开头状态（且不在think块内）
                                if char.isspace():
                                    continue
                                elif char == "<":
                                    pending_content = "<"
                                    continue
                                elif pending_content and len(pending_content) < 7:
                                    pending_content += char
                                    if pending_content == "<think>":
                                        in_think_block = True
                                        pending_content = ""
                                        continue
                                    elif not "<think>".startswith(pending_content):
                                        is_start_output = False
                                        await self.send(
                                            text_data=json.dumps(
                                                {
                                                    "type": "ai_chunk",
                                                    "content": pending_content,
                                                }
                                            )
                                        )
                                        pending_content = ""
                                    else:
                                        continue
                                else:
                                    # 遇到其他字符，结束开头状态
                                    is_start_output = False
                                    content_to_send = (
                                        pending_content + char
                                        if pending_content
                                        else char
                                    )
                                    await self.send(
                                        text_data=json.dumps(
                                            {
                                                "type": "ai_chunk",
                                                "content": content_to_send,
                                            }
                                        )
                                    )
                                    pending_content = ""
                            else:
                                # 正常状态（非开头，非think块）
                                if char == "<" and not pending_content:
                                    pending_content = "<"
                                elif pending_content and len(pending_content) < 7:
                                    pending_content += char
                                    if pending_content == "<think>":
                                        in_think_block = True
                                        pending_content = ""
                                    elif not "<think>".startswith(pending_content):
                                        await self.send(
                                            text_data=json.dumps(
                                                {
                                                    "type": "ai_chunk",
                                                    "content": pending_content,
                                                }
                                            )
                                        )
                                        pending_content = ""
                                else:
                                    await self.send(
                                        text_data=json.dumps(
                                            {"type": "ai_chunk", "content": char}
                                        )
                                    )

                # 减少延迟
                await asyncio.sleep(0.005)

            # 处理可能剩余的暂存内容（仅在启用过滤时）
            if (
                config.filter_think_tags
                and pending_content
                and not in_think_block
                and not is_start_output
            ):
                await self.send(
                    text_data=json.dumps(
                        {"type": "ai_chunk", "content": pending_content}
                    )
                )

            # 发送AI回答完成的通知
            if self.is_running:
                # 根据配置决定是否过滤think标签后保存到历史记录
                filtered_response = filter_think_tags(
                    full_response, config.filter_think_tags
                )

                await self.send(
                    text_data=json.dumps(
                        {"type": "ai_complete", "full_response": filtered_response}
                    )
                )

                # 保存对话历史（使用过滤后的内容）
                await session_manager.add_conversation(
                    self.user_id, user_input, filtered_response
                )

                # TTS语音合成（确保即使TTS失败也不会影响对话流程）
                try:
                    await self.handle_tts_speak(filtered_response)
                except Exception as tts_error:
                    logger.error(f"🚨 TTS调用失败，用户: {self.user_id}: {tts_error}")
                    # TTS失败时发送完成通知，确保前端状态恢复
                    await self.send(
                        text_data=json.dumps(
                            {
                                "type": "ai_response_complete",
                                "message": "AI回答已完成，TTS语音合成失败但对话可继续",
                            }
                        )
                    )

        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            if self.is_running:
                await self.send(
                    text_data=json.dumps(
                        {"type": "ai_error", "error": "AI服务暂时不可用"}
                    )
                )
                # LLM失败时也要发送完成通知，确保前端状态恢复
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "ai_response_complete",
                            "message": "AI回答失败，对话可继续",
                        }
                    )
                )
        finally:
            self.is_ai_speaking = False

    async def handle_reset_conversation(self):
        """处理重置对话"""
        await session_manager.reset_conversation(self.user_id)
        await self.send(
            text_data=json.dumps(
                {"type": "conversation_reset", "message": "对话历史已重置"}
            )
        )

    async def handle_test_llm(self):
        """处理LLM测试"""
        try:
            from .llm_client import test_llm_connection

            result = await test_llm_connection()
            await self.send(
                text_data=json.dumps({"type": "llm_test_result", "result": result})
            )
        except Exception as e:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "llm_test_result",
                        "result": {
                            "success": False,
                            "error": "测试失败",
                            "details": str(e),
                        },
                    }
                )
            )

    async def initialize_tts_pool(self):
        """初始化TTS连接池"""
        try:
            tts_pool = await get_tts_pool()
            if not hasattr(tts_pool, "_initialized"):
                await tts_pool.initialize()
                tts_pool._initialized = True
        except Exception as e:
            logger.error(f"初始化TTS连接池失败: {e}")

    async def connection_health_check(self):
        """连接健康检查任务"""
        while self.is_running:
            try:
                await asyncio.sleep(5)  # 每5秒检查一次，提高检查频率

                if not self.is_running:
                    break

                # 检查FunASR连接状态
                if self.funasr_client and not self.funasr_client.is_connected():
                    logger.warning(
                        f"🔌 用户 {self.user_id} FunASR连接已断开，尝试重连..."
                    )
                    self.asr_connected = False
                    await self.reconnect_funasr()

                # 检查任务状态
                if self.funasr_task and self.funasr_task.done():
                    logger.warning(
                        f"⚠️ 用户 {self.user_id} FunASR响应处理任务已结束，重新启动..."
                    )
                    self.funasr_task = asyncio.create_task(
                        self.handle_funasr_responses()
                    )

                # 检查连接时间，如果连接时间过长则重新连接
                if self.funasr_client and hasattr(
                    self.funasr_client, "connection_created_at"
                ):
                    current_time = asyncio.get_event_loop().time()
                    if (
                        current_time - self.funasr_client.connection_created_at
                    ) > 1800:  # 30分钟
                        logger.warning(
                            f"⏰ 用户 {self.user_id} FunASR连接时间过长，重新连接..."
                        )
                        self.asr_connected = False
                        await self.reconnect_funasr()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"用户 {self.user_id} 连接健康检查失败: {e}")
                await asyncio.sleep(3)  # 错误后等待3秒再继续

    async def send_tts_interrupt(self, reason=""):
        """发送TTS中断信号给前端"""
        try:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "tts_interrupt",
                        "message": "中断TTS播放",
                        "reason": reason,
                    }
                )
            )

        except Exception as e:
            logger.error(f"发送TTS中断信号失败: {e}")

    async def handle_tts_speak(self, text: str):
        """处理TTS语音合成"""
        try:
            # 检查TTS是否启用
            config = await SystemConfig.objects.aget(pk=1)
            if not config.tts_enabled:
                return

            # 发送TTS开始通知
            await self.send(
                text_data=json.dumps(
                    {"type": "tts_start", "message": "开始语音合成..."}
                )
            )

            # 获取采样率配置
            sample_rate = config.tts_sample_rate

            # 音频数据缓冲
            audio_buffer = []
            buffer_size = 0
            max_buffer_size = 20000  # 更小的分块，约<=~0.5-0.9秒
            last_send_time = 0
            min_send_interval = 0.10  # 更快的发送节奏，约100ms

            # 使用事件驱动的音频发送机制
            audio_send_event = asyncio.Event()

            async def send_buffered_audio():
                nonlocal audio_buffer, buffer_size, last_send_time
                while True:
                    try:
                        # 等待事件或定时器
                        await asyncio.wait_for(
                            audio_send_event.wait(), timeout=0.05
                        )  # 减少到50ms
                        audio_send_event.clear()

                        if buffer_size > 0:
                            # 合并音频数据
                            combined_audio = b"".join(audio_buffer)
                            audio_b64 = base64.b64encode(combined_audio).decode("utf-8")

                            # 直接发送，保证实时性
                            await self.send(
                                text_data=json.dumps(
                                    {
                                        "type": "tts_audio",
                                        "audio_data": audio_b64,
                                        "sample_rate": sample_rate,
                                        "format": "pcm",
                                    }
                                )
                            )

                            # 重置缓冲区
                            audio_buffer = []
                            buffer_size = 0
                            last_send_time = time.time()

                    except asyncio.TimeoutError:
                        # 定时检查，发送累积的数据
                        current_time = time.time()
                        if (
                            buffer_size > 0
                            and current_time - last_send_time >= min_send_interval
                        ):
                            audio_send_event.set()
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"发送缓冲音频失败: {e}")

            # 音频数据回调函数
            def on_audio_data(audio_data):
                nonlocal audio_buffer, buffer_size, last_send_time
                try:
                    # 基本验证
                    if not audio_data or len(audio_data) == 0:
                        logger.warning(f"⚠️ 收到空音频数据，用户: {self.user_id}")
                        return

                    # 添加到缓冲区
                    audio_buffer.append(audio_data)
                    buffer_size += len(audio_data)

                    # 立即发送条件：缓冲区满了
                    if buffer_size >= max_buffer_size:
                        audio_send_event.set()

                    # 定时发送条件：达到最小间隔且有数据
                    current_time = time.time()
                    if (
                        buffer_size > 0
                        and current_time - last_send_time >= min_send_interval
                    ):
                        audio_send_event.set()

                except Exception as e:
                    logger.error(f"💥 音频回调处理失败，用户: {self.user_id}: {e}")
                    import traceback

                    logger.error(f"📜 音频回调异常堆栈:\n{traceback.format_exc()}")

            # 启动音频发送任务
            audio_task = asyncio.create_task(send_buffered_audio())

            try:
                # 使用TTS连接池进行语音合成
                success = await tts_speak_stream(text, self.user_id, on_audio_data)
            finally:
                # 停止音频发送任务
                audio_task.cancel()
                try:
                    await audio_task
                except asyncio.CancelledError:
                    pass

            # 发送剩余的音频数据
            if audio_buffer:
                combined_audio = b"".join(audio_buffer)
                audio_b64 = base64.b64encode(combined_audio).decode("utf-8")
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "tts_audio",
                            "audio_data": audio_b64,
                            "sample_rate": sample_rate,
                            "format": "pcm",
                        }
                    )
                )

            if success:
                # 先发送AI完成通知（表示后端处理完成）
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "ai_response_complete",
                            "message": "AI回答和语音合成都已完成",
                        }
                    )
                )

                # 再发送TTS完成通知（前端可以根据需要处理UI状态）
                await self.send(
                    text_data=json.dumps(
                        {"type": "tts_complete", "message": "语音合成完成"}
                    )
                )

            else:
                # TTS失败时发送错误通知并确保状态恢复
                await self.send(
                    text_data=json.dumps(
                        {"type": "tts_error", "error": "语音合成失败，但对话可以继续"}
                    )
                )
                logger.error(
                    f"❌ TTS合成失败，用户: {self.user_id}, 文本: {text[:50]}..."
                )

                # 发送AI完成状态，让前端知道AI已经回答完毕，可以继续对话
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "ai_response_complete",
                            "message": "AI回答已完成，语音合成失败但对话可继续",
                        }
                    )
                )

        except SystemConfig.DoesNotExist:
            logger.warning(f"⚠️ 系统配置不存在，跳过TTS，用户: {self.user_id}")
            # 配置不存在时也要发送完成通知
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "ai_response_complete",
                        "message": "AI回答已完成，系统配置异常，跳过语音合成",
                    }
                )
            )
        except Exception as e:
            logger.error(
                f"💥 TTS处理异常，用户: {self.user_id}: {type(e).__name__}: {e}"
            )

            # 记录详细的异常信息
            import traceback

            logger.error(f"📜 TTS异常堆栈:\n{traceback.format_exc()}")

            # TTS异常时确保前端状态恢复
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "tts_error",
                        "error": f"语音合成异常: {str(e)}，但对话可以继续",
                    }
                )
            )

            # 发送AI完成状态，确保前端不会卡住
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "ai_response_complete",
                        "message": "AI回答已完成，语音合成异常但对话可继续",
                    }
                )
            )


class UploadConsumer(AsyncWebsocketConsumer):
    """文件上传WebSocket消费者"""

    async def connect(self):
        await self.accept()

    async def disconnect(self, close_code):
        raise StopConsumer()

    async def receive(self, text_data=None, bytes_data=None):
        """接收文件上传数据"""
        try:
            if text_data:
                # 处理文本消息
                message = json.loads(text_data)
                message_type = message.get("type")

                if message_type == "upload_audio":
                    await self.handle_upload_audio(message)

            elif bytes_data:
                # 处理二进制文件数据
                await self.handle_binary_upload(bytes_data)

        except json.JSONDecodeError:
            logger.error("收到无效的JSON数据")
        except Exception as e:
            logger.error(f"处理文件上传失败: {e}")

    async def handle_binary_upload(self, audio_data):
        """处理二进制音频文件上传"""
        try:
            # 发送处理开始通知
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "file_received",
                        "size": len(audio_data),
                        "message": "开始处理音频文件...",
                    }
                )
            )

            # 获取音频信息
            audio_info = get_audio_info(audio_data)
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "processing",
                        "message": f"音频信息: {audio_info['format']} 格式，大小: {audio_info['size']} 字节",
                    }
                )
            )

            # 处理音频数据
            pcm_data, sample_rate = process_audio_data(audio_data, "upload.wav")
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "processing",
                        "message": "音频处理完成，开始语音识别...",
                        "processed_size": len(pcm_data),
                        "sample_rate": sample_rate,
                    }
                )
            )

            # 使用流式识别方法（2pass模式）处理二进制文件
            await self.stream_recognize_audio(pcm_data, sample_rate)

        except Exception as e:
            logger.error(f"处理二进制音频上传失败: {e}")
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": f"处理失败: {str(e)}"}
                )
            )

    async def stream_recognize_audio(self, audio_data, sample_rate):
        """流式识别音频文件（参考web_server实现）"""
        funasr_client = None
        accumulated_text = ""

        try:
            # 连接FunASR服务
            funasr_client = FunASRClient()
            await funasr_client.connect()

            await self.send(
                text_data=json.dumps(
                    {
                        "type": "recognition_start",
                        "message": "连接到FunASR服务，开始识别...",
                    }
                )
            )

            # 使用2pass模式进行流式识别
            config = {
                "mode": "2pass",
                "chunk_size": [5, 10, 5],
                "chunk_interval": 10,
                "audio_fs": sample_rate,
                "wav_name": "web_upload_stream",
                "wav_format": "pcm",
                "is_speaking": True,
                "hotwords": "",
                "itn": True,
            }
            await funasr_client.send_config(config)

            # 启动识别结果接收任务
            async def handle_recognition_results():
                nonlocal accumulated_text

                while True:
                    try:
                        data = await funasr_client.receive_message(timeout=5.0)
                        if data is None:
                            continue

                        if "text" in data and data["text"].strip():
                            raw_text = data["text"].strip()
                            display_text = clean_recognition_text(raw_text)
                            mode = data.get("mode", "")

                            if mode == "2pass-online":
                                # 实时结果
                                await self.send(
                                    text_data=json.dumps(
                                        {
                                            "type": "recognition_partial",
                                            "text": display_text,
                                            "mode": mode,
                                        }
                                    )
                                )

                            elif mode == "2pass-offline" or mode == "offline":
                                # 最终结果
                                accumulated_text += raw_text

                                await self.send(
                                    text_data=json.dumps(
                                        {
                                            "type": "recognition_segment",
                                            "text": display_text,
                                            "accumulated": clean_recognition_text(
                                                accumulated_text
                                            ),
                                            "mode": mode,
                                        }
                                    )
                                )

                        if data.get("is_final", False):
                            break

                    except Exception as e:
                        logger.error(f"接收识别结果错误: {e}")
                        break

            # 启动结果接收任务
            result_task = asyncio.create_task(handle_recognition_results())

            # 发送音频数据
            stride = int(60 * 10 / 10 / 1000 * sample_rate * 2)
            chunk_num = max(1, (len(audio_data) - 1) // stride + 1)

            for i in range(chunk_num):
                beg = i * stride
                chunk = audio_data[beg : beg + stride]

                if len(chunk) == 0:
                    continue

                await funasr_client.send_audio_data(chunk)

                # 发送进度更新
                if (i + 1) % 50 == 0 or i == chunk_num - 1:
                    progress = (i + 1) / chunk_num * 100
                    await self.send(
                        text_data=json.dumps(
                            {
                                "type": "upload_progress",
                                "progress": progress,
                                "current": i + 1,
                                "total": chunk_num,
                            }
                        )
                    )

                await asyncio.sleep(0.01)

            # 发送结束标志
            end_config = {"is_speaking": False}
            await funasr_client.send_config(end_config)

            await self.send(
                text_data=json.dumps(
                    {
                        "type": "upload_complete",
                        "message": "音频发送完成，等待最终识别结果...",
                    }
                )
            )

            # 等待识别完成
            await result_task

            # 调用LLM生成回复
            if accumulated_text.strip():
                await self.send(
                    text_data=json.dumps(
                        {"type": "llm_start", "message": "开始AI回复生成..."}
                    )
                )

                try:
                    # 获取系统配置，决定是否启用think标签过滤
                    config = await get_system_config_async()

                    llm_response = ""
                    chunk_count = 0
                    is_start_output = True  # flag: 是否还在开头输出状态
                    in_think_block = False
                    pending_content = ""

                    async for chunk in call_llm_stream(accumulated_text.strip(), []):
                        chunk_count += 1

                        if chunk:
                            llm_response += chunk

                            # 根据配置决定是否过滤think标签
                            if not config.filter_think_tags:
                                # 如果不过滤，直接发送所有内容
                                await self.send(
                                    text_data=json.dumps(
                                        {"type": "llm_chunk", "chunk": chunk}
                                    )
                                )
                            else:
                                # 改进的逐字符跳过处理
                                for char in chunk:
                                    # 优先处理think块逻辑
                                    if in_think_block:
                                        # 在think块内，检查结束标签
                                        if char == "<" and not pending_content:
                                            pending_content = "<"
                                        elif (
                                            pending_content and len(pending_content) < 8
                                        ):
                                            pending_content += char
                                            if pending_content == "</think>":
                                                in_think_block = False
                                                pending_content = ""
                                                # think块结束后，继续处理后续字符
                                            elif not "</think>".startswith(
                                                pending_content
                                            ):
                                                # 不是结束标签，重置暂存
                                                pending_content = ""
                                        else:
                                            # 超出长度，重置暂存
                                            pending_content = ""
                                        # think块内的所有字符都跳过（不发送）
                                    elif is_start_output:
                                        # 在开头状态（且不在think块内）
                                        if char.isspace():
                                            continue
                                        elif char == "<":
                                            pending_content = "<"
                                            continue
                                        elif (
                                            pending_content and len(pending_content) < 7
                                        ):
                                            pending_content += char
                                            if pending_content == "<think>":
                                                in_think_block = True
                                                pending_content = ""
                                                continue
                                            elif not "<think>".startswith(
                                                pending_content
                                            ):
                                                is_start_output = False
                                                await self.send(
                                                    text_data=json.dumps(
                                                        {
                                                            "type": "llm_chunk",
                                                            "chunk": pending_content,
                                                        }
                                                    )
                                                )
                                                pending_content = ""
                                            else:
                                                continue
                                        else:
                                            # 遇到其他字符，结束开头状态
                                            is_start_output = False
                                            content_to_send = (
                                                pending_content + char
                                                if pending_content
                                                else char
                                            )
                                            await self.send(
                                                text_data=json.dumps(
                                                    {
                                                        "type": "llm_chunk",
                                                        "chunk": content_to_send,
                                                    }
                                                )
                                            )
                                            pending_content = ""
                                    else:
                                        # 正常状态（非开头，非think块）
                                        if char == "<" and not pending_content:
                                            pending_content = "<"
                                        elif (
                                            pending_content and len(pending_content) < 7
                                        ):
                                            pending_content += char
                                            if pending_content == "<think>":
                                                in_think_block = True
                                                pending_content = ""
                                            elif not "<think>".startswith(
                                                pending_content
                                            ):
                                                await self.send(
                                                    text_data=json.dumps(
                                                        {
                                                            "type": "llm_chunk",
                                                            "chunk": pending_content,
                                                        }
                                                    )
                                                )
                                                pending_content = ""
                                        else:
                                            await self.send(
                                                text_data=json.dumps(
                                                    {"type": "llm_chunk", "chunk": char}
                                                )
                                            )
                        else:
                            logger.warning(
                                f"[上传识别] LLM chunk #{chunk_count} 为空或None"
                            )

                    # 处理剩余的暂存内容（仅在启用过滤时）
                    if (
                        config.filter_think_tags
                        and pending_content
                        and not in_think_block
                        and not is_start_output
                    ):
                        await self.send(
                            text_data=json.dumps(
                                {"type": "llm_chunk", "chunk": pending_content}
                            )
                        )

                    # 根据配置决定是否过滤think标签
                    filtered_response = filter_think_tags(
                        llm_response, config.filter_think_tags
                    )

                    await self.send(
                        text_data=json.dumps(
                            {
                                "type": "llm_complete",
                                "recognized_text": clean_recognition_text(
                                    accumulated_text
                                ),
                                "llm_response": filtered_response,
                            }
                        )
                    )

                except Exception as llm_error:
                    logger.error(f"LLM调用失败: {llm_error}")
                    await self.send(
                        text_data=json.dumps(
                            {"type": "llm_error", "error": "AI服务暂时不可用"}
                        )
                    )

        except Exception as e:
            logger.error(f"流式识别错误: {e}")
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": f"识别失败: {str(e)}"}
                )
            )
        finally:
            if funasr_client:
                await funasr_client.disconnect()

    async def handle_upload_audio(self, message):
        """处理音频文件上传（Base64格式）"""
        try:
            # 获取音频数据
            audio_data_b64 = message.get("audio_data")
            filename = message.get("filename", "uploaded_audio")

            if not audio_data_b64:
                await self.send(
                    text_data=json.dumps(
                        {"type": "upload_error", "error": "缺少音频数据"}
                    )
                )
                return

            # 解码音频数据
            audio_data = base64.b64decode(audio_data_b64)

            # 发送处理开始通知
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "upload_progress",
                        "message": "开始处理音频文件...",
                        "filename": filename,
                    }
                )
            )

            # 获取音频信息
            audio_info = get_audio_info(audio_data)
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "upload_progress",
                        "message": f"音频信息: {audio_info['format']} 格式，大小: {audio_info['size']} 字节",
                    }
                )
            )

            # 处理音频数据
            pcm_data, sample_rate = process_audio_data(audio_data, filename)
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "upload_progress",
                        "message": "音频处理完成，开始语音识别...",
                    }
                )
            )

            # 使用离线识别方法，支持实时显示识别片段
            async def progress_callback(data):
                await self.send(text_data=json.dumps(data))

            funasr_client = FunASRClient()
            recognized_text = await funasr_client.recognize_audio(
                pcm_data, sample_rate, progress_callback
            )

            if recognized_text:
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "upload_progress",
                            "message": "语音识别完成，正在调用AI...",
                        }
                    )
                )

                # 调用LLM
                from .llm_client import call_llm_simple

                llm_response = await call_llm_simple(recognized_text, [])

                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "upload_complete",
                            "recognized_text": recognized_text,
                            "llm_response": llm_response,
                            "debug_info": {
                                "original_size": len(audio_data),
                                "processed_size": len(pcm_data),
                                "sample_rate": sample_rate,
                                "filename": filename,
                                "audio_info": audio_info,
                            },
                        }
                    )
                )
            else:
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "upload_error",
                            "error": "语音识别失败，未能识别到有效内容",
                        }
                    )
                )
            return

        except Exception as e:
            logger.error(f"处理音频上传失败: {e}")
            await self.send(
                text_data=json.dumps(
                    {"type": "upload_error", "error": f"处理失败: {str(e)}"}
                )
            )
