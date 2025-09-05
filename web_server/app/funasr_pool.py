import asyncio
import logging
import time
from dataclasses import dataclass

from .funasr_client import FunASRClient, create_stream_config_async
from .utils import get_system_config_async

logger = logging.getLogger(__name__)


@dataclass
class PooledConnection:
    """池化连接信息"""

    client: FunASRClient
    created_at: float
    last_used: float
    in_use: bool = False
    user_id: str | None = None


class FunASRConnectionPool:
    """FunASR连接池管理器"""

    def __init__(
        self,
        min_connections: int = 2,
        max_connections: int = 10,
        max_idle_time: int = 300,
    ):
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.max_idle_time = max_idle_time  # 最大空闲时间（秒）

        self.connections: list[PooledConnection] = []
        self.user_connections: dict[str, PooledConnection] = {}  # 用户到连接的映射
        self._lock = asyncio.Lock()
        self._cleanup_task = None
        self._initialized = False

    async def initialize(self):
        """初始化连接池"""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            # 创建最小连接数
            for i in range(self.min_connections):
                try:
                    conn = await self._create_connection()
                    self.connections.append(conn)

                except Exception as e:
                    logger.error(f"创建连接池连接失败: {e}")

            # 启动清理任务
            self._cleanup_task = asyncio.create_task(self._cleanup_idle_connections())
            self._initialized = True

    async def get_connection(self, user_id: str) -> FunASRClient | None:
        """为用户获取连接"""
        async with self._lock:
            # 检查用户是否已有连接
            if user_id in self.user_connections:
                conn = self.user_connections[user_id]
                if conn.client.is_connected():
                    conn.last_used = time.time()
                    return conn.client
                else:
                    # 连接已断开，移除映射
                    await self._remove_user_connection(user_id)

            # 优先为每个用户创建独立连接，避免连接复用导致的并发问题
            if len(self.connections) < self.max_connections:
                try:
                    conn = await self._create_connection()
                    conn.in_use = True
                    conn.user_id = user_id
                    conn.last_used = time.time()
                    self.connections.append(conn)
                    self.user_connections[user_id] = conn

                    logger.info(f"为用户 {user_id} 创建新的独立连接")
                    return conn.client
                except Exception as e:
                    logger.error(f"创建新连接失败: {e}")

            # 如果连接池已满，尝试寻找空闲连接（但这是最后的选择）
            for conn in self.connections:
                if not conn.in_use and conn.client.is_connected():
                    conn.in_use = True
                    conn.user_id = user_id
                    conn.last_used = time.time()
                    self.user_connections[user_id] = conn

                    # 复用空闲连接时，重新下发本会话的流配置，确保状态一致
                    try:
                        stream_cfg = await create_stream_config_async()
                        await conn.client.send_config(stream_cfg)
                        logger.warning(f"为用户 {user_id} 复用现有连接（连接池已满）")
                    except Exception as e:
                        logger.error(f"复用连接下发配置失败: {e}")

                    return conn.client

            # 连接池已满且无空闲连接，返回None
            logger.warning(f"连接池已满，无法为用户 {user_id} 分配连接")
            return None

    async def release_connection(self, user_id: str):
        """释放用户连接"""
        async with self._lock:
            await self._remove_user_connection(user_id)

    async def _remove_user_connection(self, user_id: str):
        """移除用户连接映射"""
        if user_id in self.user_connections:
            conn = self.user_connections[user_id]
            conn.in_use = False
            conn.user_id = None
            conn.last_used = time.time()
            del self.user_connections[user_id]

    async def _create_connection(self) -> PooledConnection:
        """创建新连接"""
        client = FunASRClient()
        await client.connect()

        config = await create_stream_config_async()
        await client.send_config(config)

        return PooledConnection(
            client=client, created_at=time.time(), last_used=time.time()
        )

    async def _cleanup_idle_connections(self):
        """清理空闲连接的后台任务"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次

                async with self._lock:
                    current_time = time.time()
                    connections_to_remove = []

                    for i, conn in enumerate(self.connections):
                        # 保留最小连接数，清理超时的空闲连接
                        if (
                            not conn.in_use
                            and len(self.connections) > self.min_connections
                            and current_time - conn.last_used > self.max_idle_time
                        ):
                            connections_to_remove.append(i)

                    # 从后往前删除，避免索引问题
                    for i in reversed(connections_to_remove):
                        conn = self.connections[i]
                        try:
                            await conn.client.disconnect()
                        except Exception as e:
                            logger.error(f"关闭连接失败: {e}")

                        self.connections.pop(i)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"连接池清理任务错误: {e}")

    async def close(self):
        """关闭连接池"""

        # 停止清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 关闭所有连接
        async with self._lock:
            for conn in self.connections:
                try:
                    await conn.client.disconnect()
                except Exception as e:
                    logger.error(f"关闭连接失败: {e}")

            self.connections.clear()
            self.user_connections.clear()

    def get_stats(self) -> dict:
        """获取连接池统计信息"""
        total_connections = len(self.connections)
        active_connections = sum(1 for conn in self.connections if conn.in_use)
        idle_connections = total_connections - active_connections

        return {
            "total_connections": total_connections,
            "active_connections": active_connections,
            "idle_connections": idle_connections,
            "active_users": len(self.user_connections),
            "max_connections": self.max_connections,
            "min_connections": self.min_connections,
        }


# 全局连接池实例
_connection_pool: FunASRConnectionPool | None = None


async def get_connection_pool() -> FunASRConnectionPool:
    """获取全局连接池实例"""
    global _connection_pool
    if _connection_pool is None:
        # 从数据库配置获取连接池参数
        config = await get_system_config_async()
        _connection_pool = FunASRConnectionPool(
            min_connections=config.pool_min_connections,
            max_connections=config.pool_max_connections,
            max_idle_time=config.pool_max_idle_time,
        )
        await _connection_pool.initialize()
    return _connection_pool


async def close_connection_pool():
    """关闭全局连接池"""
    global _connection_pool
    if _connection_pool:
        await _connection_pool.close()
        _connection_pool = None
