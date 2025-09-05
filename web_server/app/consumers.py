import asyncio
import secrets
import base64
import json
import logging
import time
import uuid

from channels.exceptions import StopConsumer
from channels.generic.websocket import AsyncWebsocketConsumer

from .audio_processor import get_audio_info, process_audio_data
from .funasr_client import FunASRClient, create_stream_config_async
from .llm_client import call_llm_stream, filter_think_tags
from .models import SystemConfig
from .tts_pool import get_tts_pool, interrupt_user_tts, tts_speak_stream
from .utils import (
    get_system_config_async,
    process_recognition_result,
    session_manager,
)

logger = logging.getLogger(__name__)

# 断开后的延迟清理令牌，防止误删重连后的会话
_pending_cleanup_tokens: dict[str, str] = {}


class StreamChatConsumer(AsyncWebsocketConsumer):
    """流式聊天WebSocket消费者"""

    async def connect(self):
        await self.accept()

        # 检查URL参数中是否有保存的用户ID
        query_string = self.scope.get("query_string", b"").decode("utf-8")
        query_params = dict(
            param.split("=") for param in query_string.split("&") if "=" in param
        )
        saved_user_id = query_params.get("saved_user_id")

        if saved_user_id:
            # 尝试使用保存的用户ID恢复会话
            existing_session = await session_manager.get_session(saved_user_id)
            if existing_session:
                self.user_id = saved_user_id
            else:
                # 保存的会话不存在，创建新会话
                self.user_id = await session_manager.create_session()
                logger.warning(f"❌ 保存的会话不存在，创建新会话: {self.user_id}")
        else:
            # 没有保存的用户ID，创建新会话
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
        self._reconnecting_lock = asyncio.Lock()
        self._reconnecting = False

        # 用于累积文本和状态管理
        self.accumulated_text = ""
        self.last_complete_text = ""
        self.is_ai_speaking = False
        self.conversation_active = True  # 控制是否继续监听对话
        self.is_one_time_disconnect = False  # 标记是否为一次性对话的强制断开
        self.detected_language = None  # 存储检测到的语言
        self.tts_voice = None  # 存储选择的TTS音色
        # 短暂音频缓冲（用于重连窗口避免丢包）
        self._pending_audio_chunks: list[bytes] = []
        self._pending_audio_bytes: int = 0
        self._pending_audio_max_bytes: int = 160 * 1024  # 上限160KB，约0.5-1s音频

        # 初始化TTS连接池
        await self.initialize_tts_pool()

        # 连接到FunASR服务
        await self.connect_funasr()
        # 注册清理令牌，标记此连接为最新活跃者
        self._cleanup_token = secrets.token_hex(8)
        _pending_cleanup_tokens[self.user_id] = self._cleanup_token


        # 发送当前对话模式信息
        await self.handle_get_conversation_mode()

    async def disconnect(self, close_code):
        self.is_running = False
        self.conversation_active = False  # 立即停止对话活动
        self.is_ai_speaking = False  # 停止AI说话状态

        logger.info(f"🔌 用户 {self.user_id} 连接断开，code: {close_code}")

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

        # 断开FunASR连接（独立连接模式）
        if self.funasr_client:
            try:
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

        # 延迟5秒后清理会话与残留状态；若期间用户重连，会刷新令牌，旧任务将退出
        try:
            token = secrets.token_hex(8)
            _pending_cleanup_tokens[self.user_id] = token

            async def delayed_cleanup(user_id: str, expected_token: str):
                try:
                    await asyncio.sleep(5)
                    # 若用户已在5秒内重连，令牌会被刷新，不应清理
                    if _pending_cleanup_tokens.get(user_id) != expected_token:
                        return

                    # 双保险：清理ASR/TTS资源（独立连接模式）

                    # 这里只释放运行态资源（ASR/TTS等），不触碰会话数据
                finally:
                    # 清理令牌
                    if _pending_cleanup_tokens.get(user_id) == expected_token:
                        _pending_cleanup_tokens.pop(user_id, None)

            asyncio.create_task(delayed_cleanup(self.user_id, token))
        except Exception:
            # 兜底：仅忽略错误，不删除会话数据
            pass

        raise StopConsumer()

    async def connect_funasr(self):
        """连接到FunASR服务（独立连接模式）"""
        try:
            # 独立连接模式 - 每个用户一个独立的ASR连接
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

            # 如果有重连期间的待发送音频，尽力回放
            await self._flush_pending_audio()

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
        async with self._reconnecting_lock:
            if self._reconnecting:
                return
            self._reconnecting = True

            max_retries = 3
            retry_count = 0

            try:
                while retry_count < max_retries:
                    try:
                        # 停止当前的响应处理任务（如果存在）
                        if self.funasr_task and not self.funasr_task.done():
                            self.funasr_task.cancel()

                        # 释放当前连接
                        if self.funasr_client:
                            try:
                                await self.funasr_client.disconnect()
                            except Exception as e:
                                logger.error(f"释放连接失败: {e}")

                        # 等待一小段时间再重连
                        await asyncio.sleep(1)

                        # 重新建立连接
                        await self.connect_funasr()

                        return

                    except Exception as e:
                        retry_count += 1
                        logger.error(
                            f"用户 {self.user_id} FunASR重连失败 (尝试 {retry_count}/{max_retries}): {e}"
                        )

                        if retry_count < max_retries:
                            await asyncio.sleep(2 * retry_count)
                        else:
                            self.asr_connected = False
                            await self.send(
                                text_data=json.dumps(
                                    {
                                        "type": "asr_reconnect_failed",
                                        "message": "ASR服务重连失败，请稍后重试或刷新页面",
                                        "error": str(e),
                                    }
                                )
                            )
            finally:
                self._reconnecting = False

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
                elif message_type == "restart_conversation":
                    await self.handle_restart_conversation(message)
                elif message_type == "get_conversation_mode":
                    await self.handle_get_conversation_mode()

            elif bytes_data:
                # 处理二进制数据（直接的音频数据）
                await self.handle_binary_audio_data(bytes_data)

        except json.JSONDecodeError:
            logger.error("收到无效的JSON数据")
        except Exception as e:
            logger.error(f"处理WebSocket消息失败: {e}")

    async def handle_binary_audio_data(self, audio_data):
        """处理二进制音频数据"""
        if not self.asr_connected or not self.funasr_client or not self.funasr_client.is_connected():
            # 在短暂重连窗口内进行缓冲，避免直接丢弃
            if self._pending_audio_bytes < self._pending_audio_max_bytes:
                self._pending_audio_chunks.append(bytes(audio_data))
                self._pending_audio_bytes += len(audio_data)
            else:
                logger.warning(f"⚠️ 用户 {self.user_id} ASR未连接且缓冲已满，丢弃音频 {len(audio_data)} 字节")

            # 触发重连（带锁去抖）
            await self.reconnect_funasr()
            return

        # 检查对话是否活跃（一次性对话模式下的关键检查）
        if not self.conversation_active:
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
            # 失败时也缓冲，尽力回放
            if self._pending_audio_bytes < self._pending_audio_max_bytes:
                self._pending_audio_chunks.append(bytes(audio_data))
                self._pending_audio_bytes += len(audio_data)

    async def handle_audio_data(self, audio_data_b64):
        """处理音频数据"""
        if not self.asr_connected or not self.funasr_client or not self.funasr_client.is_connected():
            # 前端Base64路径也进行缓冲
            try:
                audio_data = base64.b64decode(audio_data_b64)
                if self._pending_audio_bytes < self._pending_audio_max_bytes:
                    self._pending_audio_chunks.append(audio_data)
                    self._pending_audio_bytes += len(audio_data)
            except Exception:
                pass
            await self.reconnect_funasr()
            return

        # 检查对话是否活跃（一次性对话模式下的关键检查）
        if not self.conversation_active:
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
            # 失败时也缓冲，尽力回放
            try:
                if self._pending_audio_bytes < self._pending_audio_max_bytes:
                    self._pending_audio_chunks.append(bytes(audio_data))
                    self._pending_audio_bytes += len(audio_data)
            except Exception:
                pass

    async def _flush_pending_audio(self):
        """在连接恢复后回放短暂缓冲的音频数据"""
        try:
            if not self.funasr_client or not self.funasr_client.is_connected():
                return
            if not self._pending_audio_chunks:
                return

            # 快速发送缓冲，保持帧顺序
            for chunk in self._pending_audio_chunks:
                try:
                    await self.funasr_client.send_audio_data(chunk)
                except Exception as e:
                    logger.warning(f"回放缓冲音频失败，用户 {self.user_id}: {e}")
                    break
        finally:
            # 无论成功与否，清空缓冲，避免无限增长
            self._pending_audio_chunks.clear()
            self._pending_audio_bytes = 0

    async def handle_funasr_responses(self):
        """处理FunASR的识别结果"""
        try:
            while self.is_running:
                try:
                    # 检查FunASR连接状态
                    if not self.funasr_client or not self.funasr_client.is_connected():
                        # 自愈式重连而不是退出任务，避免健康检查与此处互相打架
                        logger.warning(f"用户 {self.user_id} FunASR连接已断开，尝试自愈重连...")
                        await self.reconnect_funasr()
                        await asyncio.sleep(0.2)
                        continue

                    data = await self.funasr_client.receive_message(timeout=1.0)
                    if data is None:
                        continue

                    if "text" in data and self.is_running:
                        raw_text = data["text"]
                        mode = data.get("mode", "")

                        if mode == "2pass-online":
                            # 实时结果，更新显示
                            self.accumulated_text = raw_text

                            # 获取配置并处理识别结果
                            config = await get_system_config_async()
                            result = process_recognition_result(raw_text, config)
                            display_text = result["cleaned_text"]

                            # 更新检测到的语言和TTS音色（实时结果也可能包含语言信息）
                            if result["detected_language"]:
                                self.detected_language = result["detected_language"]
                                self.tts_voice = result["tts_voice"]

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

                            # 获取配置并处理识别结果
                            config = await get_system_config_async()
                            result = process_recognition_result(raw_text, config)
                            display_text = result["cleaned_text"]

                            # 更新检测到的语言和TTS音色（最终结果通常包含更准确的语言信息）
                            if result["detected_language"]:
                                self.detected_language = result["detected_language"]
                                self.tts_voice = result["tts_voice"]
                                logger.info(
                                    f"🌐 用户 {self.user_id} 检测到语言: {self.detected_language}, 选择音色: {self.tts_voice}"
                                )

                            # 检查是否有有效的新文本且对话仍然活跃，同时确保连接状态正常
                            if (
                                display_text
                                and display_text.strip()
                                and display_text != self.last_complete_text
                                and self.conversation_active
                                and self.is_running
                                and hasattr(
                                    self, "channel_layer"
                                )  # 确保WebSocket连接仍然有效
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

                                # 在一次性对话模式下，ASR识别完成后立即停止监听（但不发送暂停消息）
                                if not config.continuous_conversation:
                                    self.conversation_active = False

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
            # 在开始LLM调用前再次确认对话状态和连接状态
            if not self.conversation_active or not self.is_running:
                logger.warning(
                    f"❌ 用户 {self.user_id} 对话已不活跃或连接已断开，跳过LLM调用"
                )
                return

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
                # 检查多个状态，确保任务应该继续执行
                if not self.is_running or not self.conversation_active:
                    logger.info(
                        f"🛑 用户 {self.user_id} LLM流式响应中止：is_running={self.is_running}, conversation_active={self.conversation_active}"
                    )
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

                # 保存对话历史（使用过滤后的内容，传递配置参数以减少数据库查询）
                await session_manager.add_conversation(
                    self.user_id,
                    user_input,
                    filtered_response,
                    config.max_conversation_history,
                )

                # TTS语音合成（确保即使TTS失败也不会影响对话流程）
                try:
                    await self.handle_tts_speak(
                        filtered_response, self.detected_language, self.tts_voice
                    )
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

                # 检查对话模式配置，决定是否停止监听
                if not config.continuous_conversation:
                    # 一次性对话模式：发送暂停消息（但只在TTS未启用时才在这里发送）
                    if not config.tts_enabled:
                        await self.send_conversation_paused_message()
                    # 如果TTS启用，暂停消息将在TTS完成后发送

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

    async def handle_restart_conversation(self, message=None):
        """处理重新开始对话（用于一次性对话模式）"""
        # 重新激活对话状态
        self.conversation_active = True

        # 清理当前轮次的文本状态和AI状态，防止继续执行之前未完成的任务
        self.accumulated_text = ""
        self.last_complete_text = ""
        self.is_ai_speaking = False  # 重置AI说话状态，防止之前的任务继续执行

        # 中断可能仍在进行的TTS播放
        try:
            from .tts_pool import interrupt_user_tts

            await interrupt_user_tts(self.user_id)
        except Exception as e:
            logger.warning(f"中断用户 {self.user_id} TTS播放失败: {e}")

        # 等待一小段时间，让可能仍在执行的异步任务有机会检查状态并退出
        await asyncio.sleep(0.1)

        # 获取当前对话历史数量
        conversation_history = await session_manager.get_conversation_history(
            self.user_id
        )
        history_count = len(conversation_history)

        # 发送重新开始通知，包含对话历史信息
        await self.send(
            text_data=json.dumps(
                {
                    "type": "conversation_restarted",
                    "message": "对话已重启",
                    "history_count": history_count,
                    "user_id": self.user_id,
                }
            )
        )

    async def handle_get_conversation_mode(self):
        """获取当前对话模式配置"""
        try:
            config = await get_system_config_async()
            conversation_history = await session_manager.get_conversation_history(
                self.user_id
            )
            history_count = len(conversation_history)

            await self.send(
                text_data=json.dumps(
                    {
                        "type": "conversation_mode_info",
                        "continuous_conversation": config.continuous_conversation,
                        "conversation_active": self.conversation_active,
                        "history_count": history_count,
                        "mode_description": "持续对话模式"
                        if config.continuous_conversation
                        else "一次性对话模式",
                    }
                )
            )
        except Exception as e:
            logger.error(f"获取对话模式失败: {e}")

    async def force_disconnect_after_delay(self, delay_seconds=2):
        """在延迟后强制断开连接（用于一次性对话模式）"""
        try:
            # 标记这是一次性对话的强制断开，不应删除session
            self.is_one_time_disconnect = True
            # 延迟一段时间，确保前端收到所有消息
            await asyncio.sleep(delay_seconds)

            # 发送连接即将关闭的通知
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "connection_closing",
                        "message": "一次性对话完成，连接即将关闭",
                        "reason": "one_time_conversation_complete",
                    }
                )
            )

            # 再延迟一小段时间让前端处理消息
            await asyncio.sleep(0.5)

            # 主动关闭WebSocket连接
            await self.close(code=1000, reason="一次性对话完成")

        except Exception as e:
            logger.error(f"强制断开连接失败: {e}")

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
            from .tts_pool import initialize_tts_pool_with_manager
            
            # 使用完整的初始化函数，包含任务管理器
            await initialize_tts_pool_with_manager()
            logger.info("🎵 TTS连接池和任务管理器初始化完成")
        except Exception as e:
            logger.error(f"初始化TTS连接池失败: {e}")

    async def connection_health_check(self):
        """连接健康检查任务（避免与响应处理任务冲突）"""
        while self.is_running:
            try:
                await asyncio.sleep(5)  # 每5秒检查一次，提高检查频率

                if not self.is_running:
                    break

                # 检查FunASR连接状态（不调用recv，只检查连接状态）
                if self.funasr_client and not self.funasr_client.is_connected():
                    logger.warning(
                        f"🔌 用户 {self.user_id} FunASR连接已断开，尝试重连..."
                    )
                    self.asr_connected = False
                    await self.reconnect_funasr()

                # 检查任务状态
                # 仅当已连接时才重启响应处理任务，未连接则先重连
                if self.funasr_task and self.funasr_task.done():
                    if self.funasr_client and self.funasr_client.is_connected():
                        logger.warning(
                            f"⚠️ 用户 {self.user_id} FunASR响应处理任务已结束，重新启动..."
                        )
                        self.funasr_task = asyncio.create_task(
                            self.handle_funasr_responses()
                        )
                    else:
                        # 未连接，优先重连
                        await self.reconnect_funasr()

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

    async def send_conversation_paused_message(self):
        """发送对话暂停消息（统一方法）"""
        try:
            # 获取当前对话历史数量
            conversation_history = await session_manager.get_conversation_history(
                self.user_id
            )
            history_count = len(conversation_history)

            await self.send(
                text_data=json.dumps(
                    {
                        "type": "conversation_paused",
                        "message": "本次对话已结束",
                        "mode": "one_time",
                        "history_count": history_count,
                    }
                )
            )

            # 设置对话为非活跃状态
            self.conversation_active = False

        except Exception as e:
            logger.error(f"发送conversation_paused消息失败: {e}")

    async def handle_tts_speak(
        self, text: str, detected_language: str = None, tts_voice: str = None
    ):
        """处理TTS语音合成"""
        current_tts_id = str(uuid.uuid4())

        try:
            # 检查TTS是否启用
            config = await SystemConfig.objects.aget(pk=1)
            if not config.tts_enabled:
                # TTS未启用时，直接发送AI完成通知
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "ai_response_complete",
                            "message": "AI回答已完成（TTS未启用）",
                        }
                    )
                )

                # 检查是否为一次性对话模式，发送暂停消息
                if not config.continuous_conversation:
                    await self.send_conversation_paused_message()

                return

            # 获取采样率配置
            sample_rate = config.tts_sample_rate

            # 发送TTS开始通知
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "tts_start",
                        "tts_id": current_tts_id,
                        "message": "开始语音合成...",
                        "sample_rate": sample_rate,
                        "format": "pcm",
                        "bits_per_sample": 16,
                        "send_interval_ms": 80,
                        "encoding": "base64",
                    }
                )
            )

            # 固定帧长度：80ms
            frame_samples = max(1, int(sample_rate * 80 / 1000))
            bytes_per_sample = 2  # 16-bit PCM
            frame_bytes = frame_samples * bytes_per_sample

            # 音频数据缓冲（按字节聚合，按80ms帧发送）
            pending_bytes = bytearray()
            last_send_time = 0
            min_send_interval = 0.05

            # 使用事件驱动的音频发送机制
            audio_send_event = asyncio.Event()

            async def send_buffered_audio():
                nonlocal pending_bytes, last_send_time
                while True:
                    try:
                        # 等待事件或定时器
                        try:
                            await asyncio.wait_for(
                                audio_send_event.wait(), timeout=0.05
                            )
                        except asyncio.TimeoutError:
                            pass
                        audio_send_event.clear()

                        # 按固定80ms帧发送
                        while len(pending_bytes) >= frame_bytes:
                            frame = bytes(pending_bytes[:frame_bytes])
                            del pending_bytes[:frame_bytes]

                            # 使用 Base64 编码二进制数据
                            audio_b64 = base64.b64encode(frame).decode("ascii")
                            await self.send(
                                text_data=json.dumps(
                                    {
                                        "type": "tts_audio",
                                        "tts_id": current_tts_id,
                                        "audio_data": audio_b64,
                                        "audio_size": len(audio_b64),
                                    }
                                )
                            )

                            last_send_time = time.time()

                        # 定时触发以降低延迟（当存在可发送的整帧时）
                        current_time = time.time()
                        if (
                            len(pending_bytes) >= frame_bytes
                            and current_time - last_send_time >= min_send_interval
                        ):
                            audio_send_event.set()

                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"发送缓冲音频失败: {e}")

            # 音频数据回调函数
            def on_audio_data(audio_data):
                nonlocal pending_bytes, last_send_time
                try:
                    # 基本验证
                    if not audio_data or len(audio_data) == 0:
                        logger.warning(f"⚠️ 收到空音频数据，用户: {self.user_id}")
                        return

                    # 追加到字节缓冲
                    pending_bytes.extend(audio_data)

                    # 当可形成整帧或到达最小发送间隔时触发发送
                    if len(pending_bytes) >= frame_bytes:
                        audio_send_event.set()
                    else:
                        current_time = time.time()
                        if current_time - last_send_time >= min_send_interval:
                            audio_send_event.set()

                except Exception as e:
                    logger.error(f"💥 音频回调处理失败，用户: {self.user_id}: {e}")
                    import traceback

                    logger.error(f"📜 音频回调异常堆栈:\n{traceback.format_exc()}")

            # 启动音频发送任务
            audio_task = asyncio.create_task(send_buffered_audio())

            try:
                # 使用TTS连接池进行语音合成，传递检测到的音色
                success = await tts_speak_stream(
                    text, self.user_id, on_audio_data, tts_voice
                )
            finally:
                # 停止音频发送任务
                audio_task.cancel()
                try:
                    await audio_task
                except asyncio.CancelledError:
                    pass

            # 发送剩余的音频数据（不足80ms的最终帧进行补零）
            if len(pending_bytes) > 0:
                # 如果不是整帧，补齐到80ms
                remainder = len(pending_bytes) % frame_bytes
                if remainder != 0:
                    pad_len = frame_bytes - remainder
                    pending_bytes.extend(b"\x00" * pad_len)

                # 发送所有剩余整帧
                while len(pending_bytes) >= frame_bytes:
                    frame = bytes(pending_bytes[:frame_bytes])
                    del pending_bytes[:frame_bytes]

                    audio_b64 = base64.b64encode(frame).decode("ascii")
                    await self.send(
                        text_data=json.dumps(
                            {
                                "type": "tts_audio",
                                "tts_id": current_tts_id,
                                "audio_data": audio_b64,
                                "audio_size": len(audio_b64),
                                "is_final": True,
                            }
                        )
                    )

            if success:
                # 先发送AI完成通知
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "ai_response_complete",
                            "message": "AI回答和语音合成都已完成",
                        }
                    )
                )

                # 再发送TTS完成通知
                await self.send(
                    text_data=json.dumps(
                        {"type": "tts_complete", "message": "语音合成完成"}
                    )
                )

                # 检查是否为一次性对话模式，如果是则发送暂停消息（保持连接）
                config = await SystemConfig.objects.aget(pk=1)
                if not config.continuous_conversation:
                    await self.send_conversation_paused_message()

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

                # 发送AI完成状态
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "ai_response_complete",
                            "message": "AI回答已完成，语音合成失败但对话可继续",
                        }
                    )
                )

                # 即使TTS失败，在一次性对话模式下也要发送暂停消息（保持连接）
                config = await SystemConfig.objects.aget(pk=1)
                if not config.continuous_conversation:
                    await self.send_conversation_paused_message()

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

            # TTS异常时，也要检查是否需要发送暂停消息（保持连接）
            config = await SystemConfig.objects.aget(pk=1)
            if not config.continuous_conversation:
                await self.send_conversation_paused_message()


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

                            # 获取配置并处理识别结果
                            config = await get_system_config_async()
                            result = process_recognition_result(raw_text, config)
                            display_text = result["cleaned_text"]
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

                                # 处理累积文本
                                accumulated_result = process_recognition_result(
                                    accumulated_text, config
                                )

                                await self.send(
                                    text_data=json.dumps(
                                        {
                                            "type": "recognition_segment",
                                            "text": display_text,
                                            "accumulated": accumulated_result[
                                                "cleaned_text"
                                            ],
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
                                "recognized_text": process_recognition_result(
                                    accumulated_text, config
                                )["cleaned_text"],
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
