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
        return self.error_count < self.max_error_count and self.is_connected

    def mark_used(self):
        """标记连接被使用"""
        self.last_used = time.time()

    def mark_error(self):
        """标记连接错误"""
        self.error_count += 1


@dataclass
class PoolConfig:
    """连接池配置"""

    max_total: int
    max_idle: int
    min_idle: int
    max_wait_time: float
    connection_timeout: float
    cleanup_interval: float

    @classmethod
    async def from_system_config(cls):
        """从SystemConfig模型创建配置"""
        config = await SystemConfig.objects.aget(pk=1)
        return cls(
            max_total=config.tts_pool_max_total,
            max_idle=config.tts_pool_max_idle,
            min_idle=config.tts_pool_min_idle,
            max_wait_time=config.tts_pool_max_wait_time,
            connection_timeout=config.tts_pool_connection_timeout,
            cleanup_interval=config.tts_pool_cleanup_interval,
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
            logger.info(f"✅ 创建TTS连接成功: {connection_id}")
        except Exception as e:
            logger.error(f"❌ 创建TTS连接失败: {connection_id}, 错误: {e}")
            raise

        return connection

    async def destroy_connection(self, connection: TTSConnection):
        """销毁TTS连接"""
        try:
            if connection.tts_client and connection.is_connected:
                await connection.tts_client.disconnect()
            connection.is_connected = False
            logger.info(f"🗑️ 销毁TTS连接: {connection.connection_id}")
        except Exception as e:
            logger.warning(f"⚠️ 销毁TTS连接失败: {connection.connection_id}, 错误: {e}")


class TTSConnectionPool:
    """TTS连接池"""

    def __init__(self, pool_config: PoolConfig | None = None):
        self.config = pool_config
        self.factory: TTSConnectionFactory | None = None
        self._config_loaded = False

        # 连接池状态
        self._idle_connections: Queue[TTSConnection] = Queue()
        self._busy_connections: dict[str, TTSConnection] = {}
        self._all_connections: set[str] = set()
        self._user_playing_status: dict[str, TTSConnection] = {}

        # 锁和同步原语
        self._pool_lock = Lock()
        self._cleanup_lock = Lock()
        self._async_lock = asyncio.Lock()

        # 后台任务
        self._cleanup_task: asyncio.Task | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._shutdown = False

        logger.info("🏊 初始化TTS连接池。")

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

        # 预创建最小连接数
        await self._ensure_min_connections()

        logger.info("🚀 TTS连接池初始化完成")

    async def _load_config_from_db(self):
        """从数据库加载配置"""
        if not self._config_loaded:
            self.config = await PoolConfig.from_system_config()
            self._config_loaded = True

            # 初始化线程池
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=self.config.max_total)

            logger.info(
                f"📋 从数据库加载TTS连接池配置: max_total={self.config.max_total}, "
                f"max_idle={self.config.max_idle}, min_idle={self.config.min_idle}"
            )

    async def get_connection(self, user_id: str) -> TTSConnection | None:
        """获取可用连接"""
        if self._shutdown:
            return None

        async with self._async_lock:
            # 检查用户是否已有活跃连接
            if user_id in self._user_playing_status:
                existing_conn = self._user_playing_status[user_id]
                if existing_conn.is_healthy():
                    return existing_conn
                else:
                    # 清理不健康的连接
                    await self._remove_connection(existing_conn)

            # 尝试从空闲池获取连接
            connection = await self._borrow_connection()
            if connection:
                connection.is_busy = True
                connection.user_id = user_id
                connection.mark_used()

                with self._pool_lock:
                    self._busy_connections[connection.connection_id] = connection
                    self._user_playing_status[user_id] = connection

                logger.debug(f"📤 分配连接给用户 {user_id}: {connection.connection_id}")
                return connection

            return None

    async def release_connection(self, conn: TTSConnection, user_id: str):
        """释放连接"""
        if not conn or conn.connection_id not in self._all_connections:
            return

        async with self._async_lock:
            # 清理用户状态
            if user_id in self._user_playing_status:
                del self._user_playing_status[user_id]

            # 重置连接状态
            conn.is_busy = False
            conn.user_id = None
            conn.mark_used()

            with self._pool_lock:
                if conn.connection_id in self._busy_connections:
                    del self._busy_connections[conn.connection_id]

                # 检查连接健康状态
                if conn.is_healthy() and not conn.is_expired():
                    # 归还到空闲池
                    if self._idle_connections.qsize() < self.config.max_idle:
                        self._idle_connections.put_nowait(conn)
                        logger.debug(f"📥 归还连接到空闲池: {conn.connection_id}")
                    else:
                        # 空闲池已满，销毁连接
                        await self._destroy_connection(conn)
                else:
                    # 连接不健康，销毁
                    await self._destroy_connection(conn)

    async def interrupt_user_tts(self, user_id: str):
        """中断指定用户的TTS播放"""
        async with self._async_lock:
            if user_id in self._user_playing_status:
                connection = self._user_playing_status[user_id]
                try:
                    if connection.tts_client:
                        await connection.tts_client.interrupt()
                    logger.info(f"⛔ 中断用户 {user_id} 的TTS播放")
                except Exception as e:
                    logger.error(f"❌ 中断用户 {user_id} TTS失败: {e}")
                    connection.mark_error()

    async def handle_connection_error(self, conn: TTSConnection, error: Exception):
        """处理连接错误"""
        conn.mark_error()
        logger.error(f"❌ TTS连接错误 {conn.connection_id}: {error}")

        # 如果连接不健康，从池中移除
        if not conn.is_healthy():
            await self._remove_connection(conn)

    # 内部管理方法

    async def _borrow_connection(self) -> TTSConnection | None:
        """从池中借用连接"""
        # 尝试从空闲池获取
        try:
            connection = self._idle_connections.get_nowait()
            if connection.is_healthy() and not connection.is_expired():
                return connection
            else:
                # 连接不健康，销毁并重试
                await self._destroy_connection(connection)
                return await self._borrow_connection()
        except Empty:
            pass

        # 空闲池为空，尝试创建新连接
        if len(self._all_connections) < self.config.max_total:
            try:
                connection = await self.factory.create_connection()
                with self._pool_lock:
                    self._all_connections.add(connection.connection_id)
                return connection
            except Exception as e:
                logger.error(f"❌ 创建新连接失败: {e}")

        return None

    async def _destroy_connection(self, connection: TTSConnection):
        """销毁连接"""
        with self._pool_lock:
            self._all_connections.discard(connection.connection_id)
            if connection.connection_id in self._busy_connections:
                del self._busy_connections[connection.connection_id]

        if connection.user_id and connection.user_id in self._user_playing_status:
            del self._user_playing_status[connection.user_id]

        await self.factory.destroy_connection(connection)

    async def _remove_connection(self, connection: TTSConnection):
        """从池中移除连接"""
        await self._destroy_connection(connection)

    async def _ensure_min_connections(self):
        """确保最小连接数"""
        current_idle = self._idle_connections.qsize()
        needed = max(0, self.config.min_idle - current_idle)

        for _ in range(needed):
            if len(self._all_connections) >= self.config.max_total:
                break
            try:
                connection = await self.factory.create_connection()
                with self._pool_lock:
                    self._all_connections.add(connection.connection_id)
                self._idle_connections.put_nowait(connection)
                logger.debug(f"➕ 预创建连接: {connection.connection_id}")
            except Exception as e:
                logger.error(f"❌ 预创建连接失败: {e}")
                break

    async def _cleanup_loop(self):
        """清理过期连接的后台任务"""
        while not self._shutdown:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                await self._cleanup_expired_connections()
                await self._ensure_min_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 清理任务错误: {e}")

    async def _cleanup_expired_connections(self):
        """清理过期连接"""
        expired_connections = []

        # 检查空闲连接
        temp_queue = Queue()
        while not self._idle_connections.empty():
            try:
                conn = self._idle_connections.get_nowait()
                if conn.is_expired() or not conn.is_healthy():
                    expired_connections.append(conn)
                else:
                    temp_queue.put_nowait(conn)
            except Empty:
                break

        # 将未过期的连接放回
        self._idle_connections = temp_queue

        # 销毁过期连接
        for conn in expired_connections:
            await self._destroy_connection(conn)
            logger.debug(f"🧹 清理过期连接: {conn.connection_id}")

    async def _get_system_config(self):
        """获取系统配置"""
        return await SystemConfig.objects.aget(pk=1)

    async def _get_tts_config(self):
        """获取TTS配置"""
        config = await SystemConfig.objects.aget(pk=1)
        return {
            "api_key": config.tts_api_key,
            "model": config.tts_model,
            "voice": config.tts_voice,
            "sample_rate": config.tts_sample_rate,
            "volume": config.tts_volume,
            "speech_rate": config.tts_speech_rate,
            "pitch_rate": config.tts_pitch_rate,
            "audio_format": config.tts_audio_format,
            "enabled": config.tts_enabled,
        }

    async def get_stats(self):
        """获取连接池统计"""
        with self._pool_lock:
            idle_count = self._idle_connections.qsize()
            busy_count = len(self._busy_connections)
            total_count = len(self._all_connections)
            active_users = len(self._user_playing_status)

            # 获取连接详情
            connections = []

            # 空闲连接
            temp_list = []
            while not self._idle_connections.empty():
                try:
                    conn = self._idle_connections.get_nowait()
                    temp_list.append(conn)
                    connections.append(
                        {
                            "connection_id": conn.connection_id,
                            "status": "idle",
                            "created_at": conn.created_at,
                            "last_used": conn.last_used,
                            "error_count": conn.error_count,
                            "user_id": None,
                        }
                    )
                except Empty:
                    break

            # 放回空闲连接
            for conn in temp_list:
                self._idle_connections.put_nowait(conn)

            # 忙碌连接
            for conn in self._busy_connections.values():
                connections.append(
                    {
                        "connection_id": conn.connection_id,
                        "status": "busy",
                        "created_at": conn.created_at,
                        "last_used": conn.last_used,
                        "error_count": conn.error_count,
                        "user_id": conn.user_id,
                    }
                )

            return {
                "total_connections": total_count,
                "busy_connections": busy_count,
                "idle_connections": idle_count,
                "active_users": active_users,
                "max_total": self.config.max_total,
                "max_idle": self.config.max_idle,
                "min_idle": self.config.min_idle,
                "mode": "connection_pool",
                "connections": connections,
            }

    async def shutdown(self):
        """关闭连接池"""
        logger.info("🛑 开始关闭TTS连接池...")

        self._shutdown = True

        # 取消清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 关闭所有连接
        all_connections = []

        # 收集空闲连接
        while not self._idle_connections.empty():
            try:
                conn = self._idle_connections.get_nowait()
                all_connections.append(conn)
            except Empty:
                break

        # 收集忙碌连接
        with self._pool_lock:
            all_connections.extend(self._busy_connections.values())
            self._busy_connections.clear()
            self._user_playing_status.clear()
            self._all_connections.clear()

        # 并发关闭所有连接
        if all_connections:
            tasks = [self.factory.destroy_connection(conn) for conn in all_connections]
            await asyncio.gather(*tasks, return_exceptions=True)

        # 关闭线程池
        if self._executor:
            self._executor.shutdown(wait=True)

        logger.info(f"✅ TTS连接池已关闭，共关闭 {len(all_connections)} 个连接")


# 全局TTS连接池实例
tts_pool = TTSConnectionPool()


async def get_tts_pool():
    """获取TTS连接池实例"""
    return tts_pool


@dataclass
class TTSTask:
    """TTS任务"""

    task_id: str
    user_id: str
    text: str
    audio_callback: Callable[[bytes], None]
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    status: str = "pending"  # pending, running, completed, failed, cancelled


class TTSTaskManager:
    """TTS任务管理器 - 支持并发处理"""

    def __init__(self, pool: TTSConnectionPool, max_concurrent: int):
        self.pool = pool
        self.max_concurrent = max_concurrent
        self._task_queue: asyncio.Queue[TTSTask] = asyncio.Queue()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._task_history: dict[str, TTSTask] = {}
        self._worker_tasks: list[asyncio.Task] = []
        self._shutdown = False
        self._task_counter = 0
        self._lock = asyncio.Lock()

        logger.info(f"🎯 初始化TTS任务管理器，最大并发: {max_concurrent}")

    async def start_workers(self):
        """启动工作线程"""
        for i in range(self.max_concurrent):
            worker = asyncio.create_task(self._worker_loop(f"worker_{i}"))
            self._worker_tasks.append(worker)
        logger.info(f"🚀 启动 {self.max_concurrent} 个TTS工作线程")

    async def submit_task(
        self, text: str, user_id: str, audio_callback: Callable[[bytes], None]
    ) -> str:
        """提交TTS任务"""
        if self._shutdown:
            raise RuntimeError("任务管理器已关闭")

        async with self._lock:
            self._task_counter += 1
            task_id = f"tts_task_{self._task_counter}_{int(time.time())}"

        task = TTSTask(
            task_id=task_id, user_id=user_id, text=text, audio_callback=audio_callback
        )

        await self._task_queue.put(task)
        self._task_history[task_id] = task

        logger.debug(f"📋 提交TTS任务: {task_id}, 用户: {user_id}")
        return task_id

    async def cancel_user_tasks(self, user_id: str) -> int:
        """取消指定用户的所有任务"""
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

        # 中断用户的TTS播放
        await self.pool.interrupt_user_tts(user_id)

        logger.info(f"⛔ 取消用户 {user_id} 的 {cancelled_count} 个TTS任务")
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
            # 获取连接
            connection = await self.pool.get_connection(task.user_id)
            if not connection:
                logger.error(f"❌ 无法获取TTS连接，任务: {task.task_id}")
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
            # 释放连接
            if connection:
                await self.pool.release_connection(connection, task.user_id)

    async def shutdown(self):
        """关闭任务管理器"""
        logger.info("🛑 开始关闭TTS任务管理器...")

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

        logger.info("✅ TTS任务管理器已关闭")


async def tts_speak_stream(
    text: str, user_id: str, audio_callback: Callable[[bytes], None]
) -> bool:
    """
    使用连接池的TTS语音合成

    Args:
        text: 要合成的文本
        user_id: 用户ID
        audio_callback: 音频数据回调函数

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

    # 获取TTS配置
    pool = await get_tts_pool()
    config = await pool._get_tts_config()

    if not config["enabled"]:
        return False

    if not config["api_key"]:
        logger.error(f"❌ TTS API密钥未配置，用户: {user_id}")
        return False

    # 检查是否启用连接池模式
    system_config = await SystemConfig.objects.aget(pk=1)
    use_pool = system_config.tts_use_connection_pool

    # 使用任务管理器处理TTS
    task_manager = getattr(pool, "_task_manager", None) if use_pool else None
    if task_manager and use_pool:
        try:
            task_id = await task_manager.submit_task(text, user_id, audio_callback)

            # 等待任务完成（简化版，实际可以异步处理）
            max_wait = 30  # 最大等待30秒
            start_time = time.time()

            while time.time() - start_time < max_wait:
                status = await task_manager.get_task_status(task_id)
                if status and status["status"] in ["completed", "failed", "cancelled"]:
                    return status["status"] == "completed"
                await asyncio.sleep(0.1)

            logger.warning(f"⚠️ TTS任务超时: {task_id}")
            return False

        except Exception as e:
            logger.error(f"❌ TTS任务提交失败，用户: {user_id}: {e}")
            return False
    else:
        # 降级到一次性连接模式
        return await _tts_speak_stream_disposable(text, user_id, audio_callback)


async def _tts_speak_stream_disposable(
    text: str, user_id: str, audio_callback: Callable[[bytes], None]
) -> bool:
    """
    降级：一次性TTS语音合成 - 每次创建新连接，完成后立即销毁
    """
    pool = await get_tts_pool()
    config = await pool._get_tts_config()

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
        logger.error(f"TTS合成异常，用户: {user_id}: {e}")
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
    中断指定用户的TTS播放

    Args:
        user_id: 用户ID

    Returns:
        bool: 是否成功中断
    """
    try:
        pool = await get_tts_pool()

        # 如果有任务管理器，取消用户任务
        task_manager = getattr(pool, "_task_manager", None)
        if task_manager:
            cancelled_count = await task_manager.cancel_user_tasks(user_id)
            logger.info(
                f"⛔ 通过任务管理器中断用户 {user_id}，取消 {cancelled_count} 个任务"
            )

        # 中断连接池中的用户TTS
        await pool.interrupt_user_tts(user_id)

        return True
    except Exception as e:
        logger.error(f"❌ 中断用户 {user_id} TTS失败: {e}")
        return False


# 全局实例和初始化函数


async def initialize_tts_pool_with_manager():
    """初始化带任务管理器的TTS连接池"""
    global tts_pool

    # 从数据库读取配置
    config = await SystemConfig.objects.aget(pk=1)

    # 检查是否启用连接池模式
    if not config.tts_use_connection_pool:
        logger.info("🔄 使用一次性连接模式")
        return tts_pool

    # 初始化连接池
    await tts_pool.initialize()

    # 创建并启动任务管理器
    max_concurrent = config.tts_max_concurrent
    task_manager = TTSTaskManager(tts_pool, max_concurrent)
    await task_manager.start_workers()

    # 将任务管理器附加到连接池
    tts_pool._task_manager = task_manager

    logger.info(f"🎯 TTS连接池和任务管理器初始化完成，最大并发: {max_concurrent}")
    return tts_pool


async def shutdown_tts_pool():
    """关闭TTS连接池和任务管理器"""
    global tts_pool

    try:
        # 关闭任务管理器
        task_manager = getattr(tts_pool, "_task_manager", None)
        if task_manager:
            await task_manager.shutdown()
            delattr(tts_pool, "_task_manager")

        # 关闭连接池
        await tts_pool.shutdown()

        logger.info("✅ TTS连接池完全关闭")

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

    logger.info(f"🎯 并发TTS完成: {sum(success_results)}/{len(success_results)} 成功")
    return success_results
