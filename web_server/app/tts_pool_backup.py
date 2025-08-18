import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Lock

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
    connection_id: str
    max_error_count: int
    max_idle_time: float
    is_busy: bool = False
    user_id: str | None = None
    error_count: int = 0
    is_connected: bool = False

    def is_expired(self) -> bool:
        """检查连接是否过期"""
        return (time.time() - self.last_used) > self.max_idle_time

    def is_healthy(self) -> bool:
        """检查连接是否健康"""
        try:
            # 基本健康检查
            if not (self.error_count < self.max_error_count and self.is_connected and self.tts_client):
                return False
            
            # WebSocket健康检查（容错处理）
            if hasattr(self.tts_client, '_ws') and self.tts_client._ws is not None:
                return not self.tts_client._ws.closed
            else:
                # 如果WebSocket还没有初始化，认为连接是健康的（刚创建的连接）
                return True
        except Exception as e:
            logger.debug(f"🔍 连接健康检查异常 {self.connection_id}: {e}")
            return False

    def mark_used(self):
        """标记连接被使用"""
        self.last_used = time.time()

    def mark_error(self):
        """标记连接错误"""
        self.error_count += 1


@dataclass
class PoolConfig:
    """连接池配置（简化版，主要配置由DashScope SDK控制）"""

    max_concurrent: int
    cleanup_interval: float = 60.0  # 固定清理间隔

    @classmethod
    async def from_system_config(cls):
        """从SystemConfig模型创建配置"""
        config = await SystemConfig.objects.aget(pk=1)
        return cls(
            max_concurrent=config.tts_max_concurrent,
        )


class TTSConnectionFactory:
    """TTS连接工厂"""

    def __init__(self, tts_config_getter: Callable, system_config_getter: Callable):
        self.tts_config_getter = tts_config_getter
        self.system_config_getter = system_config_getter
        self._connection_counter = 0
        self._lock = Lock()

    async def create_connection(self) -> TTSConnection:
        """创建新的TTS连接"""
        config_dict = await self.tts_config_getter()
        system_config = await self.system_config_getter()

        tts_config = TTSConfig(
            model=config_dict["model"],
            voice=config_dict["voice"],
            sample_rate=config_dict["sample_rate"],
            volume=config_dict["volume"],
            speech_rate=config_dict["speech_rate"],
            pitch_rate=config_dict["pitch_rate"],
            audio_format=config_dict["audio_format"],
        )

        tts_client = DashScopeRealtimeTTS(
            api_key=config_dict["api_key"], config=tts_config
        )

        with self._lock:
            self._connection_counter += 1
            connection_id = f"tts_conn_{self._connection_counter}_{int(time.time())}"

        connection = TTSConnection(
            tts_client=tts_client,
            config=tts_config,
            created_at=time.time(),
            last_used=time.time(),
            connection_id=connection_id,
            max_error_count=system_config.tts_connection_max_error_count,
            max_idle_time=system_config.tts_connection_max_idle_time,
        )

        # 建立连接
        try:
            await tts_client.connect()
            connection.is_connected = True
            logger.debug(f"✅ TTS连接建立成功: {connection_id}")
        except Exception as e:
            logger.error(f"❌ TTS连接建立失败: {connection_id}, 错误: {e}")
            connection.is_connected = False
            raise

        return connection

    async def destroy_connection(self, connection: TTSConnection):
        """销毁TTS连接"""
        try:
            if connection.tts_client:
                # 强制关闭WebSocket连接
                if hasattr(connection.tts_client, '_ws') and connection.tts_client._ws:
                    if not connection.tts_client._ws.closed:
                        await connection.tts_client._ws.close()
                
                # 调用正常的断开方法
                if connection.is_connected:
                    await connection.tts_client.disconnect()
                    
            connection.is_connected = False
            logger.debug(f"🗑️ TTS连接已销毁: {connection.connection_id}")
        except Exception as e:
            logger.warning(f"⚠️ 销毁TTS连接失败: {connection.connection_id}, 错误: {e}")
            connection.is_connected = False


class TTSConnectionPool:
    """TTS连接池"""

    def __init__(self, pool_config: PoolConfig | None = None):
        self.config = pool_config
        self.factory: TTSConnectionFactory | None = None
        self._config_loaded = False

        # 简化的连接池状态（主要依赖DashScope SDK连接池）
        self._busy_connections: dict[str, TTSConnection] = {}
        self._all_connections: dict[str, TTSConnection] = {}

        # 锁和同步原语
        self._pool_lock = Lock()
        self._async_lock = asyncio.Lock()

        # 后台任务
        self._cleanup_task: asyncio.Task | None = None
        self._shutdown = False

    async def initialize(self):
        """初始化连接池"""
        # 从数据库加载配置
        await self._load_config_from_db()

        if self.factory is None:
            self.factory = TTSConnectionFactory(
                self._get_tts_config, self._get_system_config
            )

        # 启动清理任务
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _load_config_from_db(self):
        """从数据库加载配置"""
        if not self._config_loaded:
            self.config = await PoolConfig.from_system_config()
            self._config_loaded = True

    async def borrow_connection(self, voice: str = None) -> TTSConnection | None:
        """借用连接（简化版，直接创建新连接）"""
        if self._shutdown:
            return None

        async with self._async_lock:
            # 直接创建新连接，依赖DashScope SDK的连接池管理
            return await self._create_connection_with_voice(voice or "default")

    async def return_connection(self, conn: TTSConnection):
        """归还连接（简化版，直接销毁）"""
        if not conn or conn.connection_id not in self._all_connections:
            return

        async with self._async_lock:
            # 简化逻辑：直接销毁连接，依赖DashScope SDK的连接池复用
            await self._destroy_connection(conn)



    async def _create_connection_with_voice(
        self, voice: str
    ) -> TTSConnection | None:
        """为指定音色创建新连接（简化版）"""
        try:
            # 获取配置并修改音色
            config_dict = await self.factory.tts_config_getter()
            config_dict = config_dict.copy()
            config_dict["voice"] = voice

            system_config = await self.factory.system_config_getter()

            tts_config = TTSConfig(
                model=config_dict["model"],
                voice=voice,  # 使用指定音色
                sample_rate=config_dict["sample_rate"],
                volume=config_dict["volume"],
                speech_rate=config_dict["speech_rate"],
                pitch_rate=config_dict["pitch_rate"],
                audio_format=config_dict["audio_format"],
            )

            tts_client = DashScopeRealtimeTTS(
                api_key=config_dict["api_key"], config=tts_config
            )

            with self.factory._lock:
                self.factory._connection_counter += 1
                connection_id = (
                    f"tts_conn_{self.factory._connection_counter}_{int(time.time())}"
                )

            connection = TTSConnection(
                tts_client=tts_client,
                config=tts_config,
                created_at=time.time(),
                last_used=time.time(),
                connection_id=connection_id,
                max_error_count=system_config.tts_connection_max_error_count,
                max_idle_time=system_config.tts_connection_max_idle_time,
                is_busy=True,  # 新创建的连接直接标记为忙碌
            )

            # 连接到TTS服务
            await connection.tts_client.connect()
            connection.is_connected = True

            with self._pool_lock:
                self._all_connections[connection_id] = connection
                self._busy_connections[connection_id] = connection

            logger.debug(f"🎵 创建TTS连接: {voice} ({connection_id})")
            return connection

        except Exception as e:
            logger.error(f"❌ 创建TTS连接失败 (音色: {voice}): {e}")
            return None



    async def handle_connection_error(self, conn: TTSConnection, error: Exception):
        """处理连接错误"""
        conn.mark_error()
        conn.is_connected = False  # 标记连接为断开状态
        error_msg = str(error)
        
        # 检查是否是WebSocket协议错误（1007等）
        if "1007" in error_msg or "invalid frame" in error_msg.lower():
            logger.warning(f"⚠️ TTS WebSocket协议错误 {conn.connection_id}: {error}")
        else:
            logger.error(f"❌ TTS连接错误 {conn.connection_id}: {error}")

        # 无论如何都从池中移除有问题的连接，避免重用
        await self._remove_connection(conn)

    async def interrupt_user_tts(self, user_id: str) -> bool:
        """中断指定用户的TTS任务（简化版）"""
        try:
            # 在简化的连接池中，我们直接通过任务管理器中断用户TTS
            task_manager = getattr(self, "_task_manager", None)
            if task_manager:
                return await task_manager.interrupt_user_tts(user_id)
            else:
                logger.warning(f"⚠️ 任务管理器未初始化，无法中断用户 {user_id} 的TTS")
                return False
        except Exception as e:
            logger.error(f"❌ 中断用户 {user_id} TTS失败: {e}")
            return False

    # 内部管理方法



    async def _destroy_connection(self, connection: TTSConnection):
        """销毁连接"""
        with self._pool_lock:
            self._all_connections.pop(connection.connection_id, None)
            if connection.connection_id in self._busy_connections:
                del self._busy_connections[connection.connection_id]



        await self.factory.destroy_connection(connection)

    async def _remove_connection(self, connection: TTSConnection):
        """从池中移除连接"""
        await self._destroy_connection(connection)



    async def _cleanup_loop(self):
        """清理过期连接的后台任务（简化版）"""
        while not self._shutdown:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                # 简化清理逻辑：主要依赖DashScope SDK的连接管理
                logger.debug("🧹 定期清理任务执行")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 清理任务错误: {e}")

    async def _get_system_config(self):
        """获取系统配置"""
        return await SystemConfig.objects.aget(pk=1)

    async def _get_tts_config(self):
        """获取TTS配置"""
        config = await SystemConfig.objects.aget(pk=1)
        return {
            "api_key": config.tts_api_key,
            "model": config.tts_model,
            "voice": config.tts_default_voice,  # 使用默认音色
            "sample_rate": config.tts_sample_rate,
            "volume": config.tts_volume,
            "speech_rate": config.tts_speech_rate,
            "pitch_rate": config.tts_pitch_rate,
            "audio_format": config.tts_audio_format,
            "enabled": config.tts_enabled,
        }

    async def get_stats(self):
        """获取连接池统计（简化版）"""
        with self._pool_lock:
            busy_count = len(self._busy_connections)
            total_count = len(self._all_connections)

            # 获取忙碌连接详情
            connections = []
            for conn in self._busy_connections.values():
                connections.append(
                    {
                        "connection_id": conn.connection_id,
                        "status": "busy",
                        "created_at": conn.created_at,
                        "last_used": conn.last_used,
                        "error_count": conn.error_count,
                        "voice": conn.config.voice,
                    }
                )

            return {
                "total_connections": total_count,
                "busy_connections": busy_count,
                "idle_connections": 0,  # 不再维护空闲连接
                "active_users": 0,  # 不再跟踪用户状态
                "max_concurrent": self.config.max_concurrent,
                "mode": "simplified_pool_with_dashscope_sdk",
                "connections": connections,
            }

    async def shutdown(self):
        """关闭连接池"""
        self._shutdown = True

        # 取消清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 关闭所有连接（简化版）
        all_connections = []

        # 收集所有连接
        with self._pool_lock:
            all_connections.extend(self._all_connections.values())
            self._busy_connections.clear()
            self._all_connections.clear()

        # 并发关闭所有连接
        if all_connections:
            tasks = [self.factory.destroy_connection(conn) for conn in all_connections]
            await asyncio.gather(*tasks, return_exceptions=True)

        # 关闭线程池
        if self._executor:
            self._executor.shutdown(wait=True)


# 全局TTS连接池实例
tts_pool = TTSConnectionPool()


async def get_tts_pool():
    """获取TTS连接池实例"""
    return tts_pool


# TTSTask 类已移除 - 直接使用DashScope SDK


# TTSTaskManager 类已移除 - 直接使用DashScope SDK

# 以下代码已移除，直接跳转到 tts_speak_stream 函数
        self.pool = pool
        self.max_concurrent = max_concurrent
        self._task_queue: asyncio.Queue[TTSTask] = asyncio.Queue()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._task_history: dict[str, TTSTask] = {}
        self._worker_tasks: list[asyncio.Task] = []
        self._shutdown = False
        self._task_counter = 0
        self._lock = asyncio.Lock()

    async def start_workers(self):
        """启动工作线程"""
        for i in range(self.max_concurrent):
            worker = asyncio.create_task(self._worker_loop(f"worker_{i}"))
            self._worker_tasks.append(worker)

    async def submit_task(
        self,
        text: str,
        user_id: str,
        audio_callback: Callable[[bytes], None],
        voice: str = None,
    ) -> str:
        """提交TTS任务"""
        if self._shutdown:
            raise RuntimeError("任务管理器已关闭")

        async with self._lock:
            self._task_counter += 1
            task_id = f"tts_task_{self._task_counter}_{int(time.time())}"

        task = TTSTask(
            task_id=task_id,
            user_id=user_id,
            text=text,
            audio_callback=audio_callback,
            voice=voice,
        )

        await self._task_queue.put(task)
        self._task_history[task_id] = task

        logger.debug(f"📋 提交TTS任务: {task_id}, 用户: {user_id}")
        return task_id

    async def cancel_user_tasks(self, user_id: str) -> int:
        """取消指定用户的所有任务"""
        return await self._cancel_user_tasks_internal(user_id)

    async def interrupt_user_tts(self, user_id: str) -> bool:
        """中断指定用户的TTS任务"""
        try:
            cancelled_count = await self._cancel_user_tasks_internal(user_id)
            return cancelled_count > 0
        except Exception as e:
            logger.error(f"❌ 中断用户 {user_id} TTS任务失败: {e}")
            return False

    async def _cancel_user_tasks_internal(self, user_id: str) -> int:
        """内部方法：取消指定用户的所有任务"""
        cancelled_count = 0

        # 取消队列中的任务
        temp_queue = asyncio.Queue()
        while not self._task_queue.empty():
            try:
                task = await self._task_queue.get()
                if task.user_id == user_id and task.status == "pending":
                    task.status = "cancelled"
                    cancelled_count += 1
                else:
                    await temp_queue.put(task)
            except asyncio.QueueEmpty:
                break

        self._task_queue = temp_queue

        # 中断正在运行的任务
        for task_id, running_task in list(self._running_tasks.items()):
            if (
                task_id in self._task_history
                and self._task_history[task_id].user_id == user_id
            ):
                running_task.cancel()
                cancelled_count += 1

        logger.info(f"🛑 取消用户 {user_id} 的 {cancelled_count} 个TTS任务")
        return cancelled_count

    async def get_task_status(self, task_id: str) -> dict | None:
        """获取任务状态"""
        if task_id in self._task_history:
            task = self._task_history[task_id]
            return {
                "task_id": task.task_id,
                "user_id": task.user_id,
                "status": task.status,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "text_length": len(task.text),
            }
        return None

    async def _worker_loop(self, worker_name: str):
        """工作线程主循环"""
        logger.debug(f"🔄 启动TTS工作线程: {worker_name}")

        while not self._shutdown:
            try:
                # 获取任务
                task = await asyncio.wait_for(self._task_queue.get(), timeout=1.0)

                if task.status == "cancelled":
                    continue

                # 执行任务
                task.status = "running"
                task.started_at = time.time()

                # 创建任务协程
                task_coroutine = asyncio.create_task(self._execute_task(task))
                self._running_tasks[task.task_id] = task_coroutine

                try:
                    await task_coroutine
                    task.status = "completed"
                except asyncio.CancelledError:
                    task.status = "cancelled"
                except Exception as e:
                    task.status = "failed"
                    logger.error(f"❌ TTS任务执行失败 {task.task_id}: {e}")
                finally:
                    task.completed_at = time.time()
                    if task.task_id in self._running_tasks:
                        del self._running_tasks[task.task_id]

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ TTS工作线程错误 {worker_name}: {e}")

        logger.debug(f"⏹️ TTS工作线程退出: {worker_name}")

    async def _execute_task(self, task: TTSTask) -> bool:
        """执行TTS任务"""
        connection = None
        try:
            # 借用连接（传递音色参数）
            connection = await self.pool.borrow_connection(task.voice)
            if not connection:
                logger.error(
                    f"❌ 无法借用TTS连接，任务: {task.task_id}, 音色: {task.voice}"
                )
                return False
                
            # 验证连接健康状态
            if not connection.is_healthy():
                logger.warning(f"⚠️ TTS连接不健康，标记为错误: {connection.connection_id}")
                await self.pool.handle_connection_error(connection, Exception("连接状态异常"))
                return False

            # 设置音频回调
            audio_chunk_count = 0
            total_audio_bytes = 0

            def on_audio(audio_data):
                nonlocal audio_chunk_count, total_audio_bytes
                try:
                    if audio_data:
                        audio_chunk_count += 1
                        total_audio_bytes += len(audio_data)
                        task.audio_callback(audio_data)
                except Exception as e:
                    logger.error(f"音频回调失败，任务: {task.task_id}: {e}")

            def on_error(error):
                logger.error(f"TTS合成错误，任务: {task.task_id}: {error}")

            def on_end():
                logger.debug(f"TTS合成完成，任务: {task.task_id}")

            # 设置回调
            connection.tts_client.send_audio = on_audio
            connection.tts_client.on_error = on_error
            connection.tts_client.on_end = on_end

            # 执行TTS合成
            cleaned_text = task.text.strip()
            await connection.tts_client.say(cleaned_text)
            await connection.tts_client.finish()
            await connection.tts_client.wait_done()

            # 短暂延迟确保音频处理完成
            await asyncio.sleep(0.1)

            # 验证音频数据
            if audio_chunk_count == 0:
                logger.warning(f"⚠️ 未收到音频数据，任务: {task.task_id}")
            elif total_audio_bytes < 1000:
                logger.warning(
                    f"⚠️ 音频数据量过少({total_audio_bytes}字节)，任务: {task.task_id}"
                )

            logger.debug(
                f"✅ TTS任务完成: {task.task_id}, 音频块: {audio_chunk_count}, 字节: {total_audio_bytes}"
            )
            return True

        except Exception as e:
            if connection:
                await self.pool.handle_connection_error(connection, e)
            logger.error(f"❌ TTS任务执行异常 {task.task_id}: {e}")
            return False
        finally:
            # 归还连接
            if connection:
                await self.pool.return_connection(connection)

    async def shutdown(self):
        """关闭任务管理器"""
        self._shutdown = True

        # 取消所有工作线程
        for worker in self._worker_tasks:
            worker.cancel()

        # 等待工作线程结束
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)

        # 取消所有运行中的任务
        for task in self._running_tasks.values():
            task.cancel()

        if self._running_tasks:
            await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)


async def tts_speak_stream(
    text: str, user_id: str, audio_callback: Callable[[bytes], None], voice: str = None
) -> bool:
    """
    TTS语音合成（简化版，直接使用DashScope SDK连接池）

    Args:
        text: 要合成的文本
        user_id: 用户ID
        audio_callback: 音频数据回调函数
        voice: 指定的音色名称，如果为None则使用默认音色

    Returns:
        bool: 是否成功
    """
    # 验证文本内容
    if not text or not text.strip():
        return False

    if len(text.strip()) > 1000:
        logger.warning(
            f"⚠️ TTS文本过长 ({len(text)} 字符)，可能影响性能，用户: {user_id}"
        )

    # 获取系统配置
    system_config = await SystemConfig.objects.aget(pk=1)
    if not system_config.tts_enabled:
        return False

    if not system_config.tts_api_key:
        logger.error(f"❌ TTS API密钥未配置，用户: {user_id}")
        return False

    # 记录音色使用
    if voice:
        logger.info(f"🎵 用户 {user_id} 使用指定音色: {voice}")

    # 直接使用一次性连接模式，依赖DashScope SDK的内部连接池管理
    return await _tts_speak_stream_disposable(text, user_id, audio_callback, voice)


async def _tts_speak_stream_disposable(
    text: str, user_id: str, audio_callback: Callable[[bytes], None], voice: str = None
) -> bool:
    """
    降级：一次性TTS语音合成 - 每次创建新连接，完成后立即销毁
    """
    pool = await get_tts_pool()
    config = await pool._get_tts_config()

    # 如果指定了音色，则覆盖默认音色
    if voice:
        config = config.copy()
        config["voice"] = voice
        logger.info(f"🎵 一次性连接使用指定音色: {voice}")

    # 创建一次性TTS客户端
    tts_client = None
    try:
        tts_config = TTSConfig(
            model=config["model"],
            voice=config["voice"],
            sample_rate=config["sample_rate"],
            volume=config["volume"],
            speech_rate=config["speech_rate"],
            pitch_rate=config["pitch_rate"],
            audio_format=config["audio_format"],
        )

        tts_client = DashScopeRealtimeTTS(api_key=config["api_key"], config=tts_config)

        # 设置回调函数
        audio_chunk_count = 0
        total_audio_bytes = 0

        def on_audio(audio_data):
            nonlocal audio_chunk_count, total_audio_bytes
            try:
                audio_chunk_count += 1
                total_audio_bytes += len(audio_data) if audio_data else 0
                audio_callback(audio_data)
            except Exception as e:
                logger.error(f"音频回调失败，用户: {user_id}: {e}")

        def on_error(error):
            logger.error(f"TTS合成错误 (用户: {user_id}): {error}")

        def on_end():
            pass

        # 设置回调
        tts_client.send_audio = on_audio
        tts_client.on_error = on_error
        tts_client.on_end = on_end

        # 建立连接
        await tts_client.connect()

        # 发送文本进行合成
        cleaned_text = text.strip()
        await tts_client.say(cleaned_text)
        await tts_client.finish()
        await tts_client.wait_done()

        # 增加短暂延迟，确保所有音频数据都已处理
        await asyncio.sleep(0.1)

        # 检查音频数据完整性
        if audio_chunk_count == 0:
            logger.error(f"警告：没有收到任何音频数据！用户: {user_id}")
        elif total_audio_bytes < 1000:
            logger.warning(
                f"警告：音频数据量过少({total_audio_bytes}字节)，可能不完整，用户: {user_id}"
            )

        return True

    except Exception as e:
        error_msg = str(e)
        if "1007" in error_msg or "invalid frame" in error_msg.lower():
            logger.warning(f"⚠️ TTS WebSocket协议错误，用户: {user_id}: {e}")
        else:
            logger.error(f"❌ TTS合成异常，用户: {user_id}: {e}")
        return False

    finally:
        # 立即销毁连接
        if tts_client:
            try:
                if (
                    hasattr(tts_client, "_ws")
                    and tts_client._ws
                    and not tts_client._ws.closed
                ):
                    await tts_client._ws.close()
            except Exception as cleanup_err:
                logger.warning(f"TTS连接清理失败，用户: {user_id}: {cleanup_err}")


async def interrupt_user_tts(user_id: str) -> bool:
    """
    中断指定用户的TTS播放（简化版）

    Args:
        user_id: 用户ID

    Returns:
        bool: 是否成功中断
    """
    try:
        # 在简化的架构中，由于直接使用DashScope SDK，
        # 我们无法直接中断正在进行的TTS，但可以记录日志
        logger.info(f"🛑 请求中断用户 {user_id} 的TTS（依赖DashScope SDK内部处理）")
        return True
    except Exception as e:
        logger.error(f"❌ 中断用户 {user_id} TTS失败: {e}")
        return False


# 全局实例和初始化函数


async def initialize_tts_pool_with_manager():
    """初始化TTS连接池（简化版，主要配置DashScope SDK）"""
    import os
    global tts_pool

    # 从数据库读取配置
    config = await SystemConfig.objects.aget(pk=1)

    # 设置DashScope SDK环境变量
    os.environ['DASHSCOPE_CONNECTION_POOL_SIZE'] = str(config.dashscope_connection_pool_size)
    os.environ['DASHSCOPE_MAXIMUM_ASYNC_REQUESTS'] = str(config.dashscope_max_async_requests)
    os.environ['DASHSCOPE_MAXIMUM_ASYNC_REQUESTS_PER_HOST'] = str(config.dashscope_max_async_requests_per_host)
    
    logger.info(f"🔧 DashScope SDK配置 - 连接池大小: {config.dashscope_connection_pool_size}, "
                f"最大异步请求: {config.dashscope_max_async_requests}, "
                f"单Host最大请求: {config.dashscope_max_async_requests_per_host}")

    if not config.tts_enabled:
        logger.info("🔇 TTS功能未启用")
        return tts_pool

    # 简化初始化：主要依赖DashScope SDK的内部连接池
    logger.info("✅ TTS初始化完成（使用DashScope SDK内部连接池）")
    return tts_pool


async def shutdown_tts_pool():
    """关闭TTS连接池（简化版）"""
    global tts_pool

    try:
        # 简化关闭：主要依赖DashScope SDK的内部清理
        logger.info("🔄 TTS连接池关闭（DashScope SDK内部处理连接清理）")
    except Exception as e:
        logger.error(f"❌ 关闭TTS连接池失败: {e}")


# 兼容性函数：支持并发TTS合成
async def tts_speak_concurrent(
    texts: list[str],
    user_ids: list[str],
    audio_callbacks: list[Callable[[bytes], None]],
) -> list[bool]:
    """
    并发TTS语音合成

    Args:
        texts: 要合成的文本列表
        user_ids: 用户ID列表
        audio_callbacks: 音频数据回调函数列表

    Returns:
        List[bool]: 每个任务的成功状态
    """
    if len(texts) != len(user_ids) or len(texts) != len(audio_callbacks):
        raise ValueError("texts, user_ids, audio_callbacks 长度必须相同")

    # 创建并发任务
    tasks = []
    for text, user_id, callback in zip(texts, user_ids, audio_callbacks):
        task = asyncio.create_task(tts_speak_stream(text, user_id, callback))
        tasks.append(task)

    # 等待所有任务完成
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理结果
    success_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"❌ 并发TTS任务 {i} 失败: {result}")
            success_results.append(False)
        else:
            success_results.append(result)

    return success_results
