import asyncio
import json
import logging
import ssl
from typing import Any

import websockets

from .utils import get_system_config_async, process_recognition_result

logger = logging.getLogger(__name__)


async def create_stream_config_async() -> dict[str, Any]:
    """创建FunASR流配置（异步版本）"""
    # 获取系统配置中的FunASR模式
    config = await get_system_config_async()
    funasr_mode = config.funasr_mode

    return {
        "mode": funasr_mode,
        "chunk_size": [5, 10, 5],
        "chunk_interval": 10,
        "wav_name": "stream",
        "is_speaking": True,
        "hotwords": "",
    }


class FunASRClient:
    """FunASR WebSocket客户端"""

    def __init__(self):
        self.websocket = None
        self.config = None
        self.uri = None
        self.ping_task = None
        self.last_ping_time = 0
        self.ping_interval = 30  # 30秒发送一次ping
        self.connection_created_at = 0

    async def connect(self):
        """连接到FunASR服务器"""
        try:
            # 异步获取配置
            if not self.config:
                self.config = await get_system_config_async()
                self.uri = self.config.get_funasr_uri()

            # 如果使用SSL连接，根据配置决定是否验证证书
            ssl_context = None
            if self.uri.startswith("wss://"):
                ssl_context = ssl.create_default_context()
                if not self.config.funasr_ssl_verify:
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE

            # 添加ping_interval参数以启用WebSocket ping/pong
            self.websocket = await websockets.connect(
                self.uri,
                ssl=ssl_context,
                ping_interval=15,  # 更频繁的ping，降低中间设备闲置断开概率
                ping_timeout=30,  # 更宽松的超时，避免误判
                close_timeout=10,  # 10秒关闭超时
            )

            self.connection_created_at = asyncio.get_event_loop().time()

            # 启动ping任务
            self.ping_task = asyncio.create_task(self._ping_loop())

        except Exception as e:
            logger.error(f"连接FunASR失败: {e}")
            raise

    async def _ping_loop(self):
        """定期发送ping以保持连接活跃"""
        try:
            while self.websocket:
                await asyncio.sleep(self.ping_interval)

                if self.websocket:
                    try:
                        # 检查连接状态
                        if hasattr(self.websocket, "closed") and self.websocket.closed:
                            break

                        await self.websocket.ping()
                        self.last_ping_time = asyncio.get_event_loop().time()

                    except websockets.exceptions.ConnectionClosed:
                        break
                    except Exception as e:
                        logger.warning(f"FunASR ping失败: {e}")
                        break
                else:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"FunASR ping循环错误: {e}")

    async def disconnect(self):
        """断开连接"""
        # 取消ping任务
        if self.ping_task and not self.ping_task.done():
            self.ping_task.cancel()
            try:
                await self.ping_task
            except asyncio.CancelledError:
                pass

        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.warning(f"关闭FunASR连接时出错: {e}")
            finally:
                self.websocket = None

    async def send_config(self, config: dict[str, Any]):
        """发送配置消息"""
        if not self.websocket:
            raise RuntimeError("未连接到FunASR服务器")

        try:
            message = json.dumps(config, ensure_ascii=False)
            await self.websocket.send(message)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("FunASR连接已关闭，无法发送配置")
            self.websocket = None
            raise RuntimeError("FunASR连接已断开")
        except Exception as e:
            logger.error(f"发送FunASR配置失败: {e}")
            raise

    async def send_audio_data(self, audio_data: bytes):
        """发送音频数据"""
        if not self.websocket:
            raise RuntimeError("未连接到FunASR服务器")

        try:
            await self.websocket.send(audio_data)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("FunASR连接已关闭，无法发送音频数据")
            self.websocket = None
            raise RuntimeError("FunASR连接已断开")
        except Exception as e:
            logger.error(f"发送FunASR音频数据失败: {e}")
            raise

    async def receive_message(self, timeout: float = 10.0) -> dict[str, Any] | None:
        """接收消息"""
        if not self.websocket:
            return None

        # 安全检查连接状态
        try:
            if hasattr(self.websocket, "closed") and self.websocket.closed:
                return None
        except AttributeError:
            # 某些websockets版本没有closed属性，忽略检查
            pass

        try:
            # 使用asyncio.wait_for设置超时
            message = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)

            # 处理二进制消息
            if isinstance(message, bytes):
                return None

            # 处理文本消息
            try:
                data = json.loads(message)

                return data
            except json.JSONDecodeError:
                logger.warning(f"无法解析JSON消息: {message}")
                return None

        except asyncio.TimeoutError:
            # 超时是正常的，不记录错误
            return None
        except websockets.exceptions.ConnectionClosed:
            logger.warning("FunASR连接已关闭")
            # 标记连接为无效
            self.websocket = None
            return None
        except Exception as e:
            logger.error(f"接收消息错误: {e}")
            # 非 ConnectionClosed 的异常，暂不判定连接失效，返回 None 由上层重试
            return None

    async def recognize_audio(
        self, pcm_data: bytes, sample_rate: int = 16000, progress_callback=None
    ) -> str:
        """识别音频文件（完整文件模式，参考FunASR官方HTML实现）"""
        try:
            await self.connect()

            # 使用官方HTML文件模式的配置：offline模式
            config = {
                "mode": "offline",
                "chunk_size": [5, 10, 5],
                "chunk_interval": 10,
                "audio_fs": sample_rate,
                "wav_name": "uploaded_audio",
                "wav_format": "pcm",
                "is_speaking": True,
                "hotwords": "",
                "itn": True,
            }
            await self.send_config(config)

            # 使用官方HTML的固定块大小960字节（而不是动态计算的stride）
            chunk_size = 960

            # 启动识别结果接收任务
            accumulated_text = ""
            recognition_complete = asyncio.Event()

            async def handle_recognition_results():
                nonlocal accumulated_text

                while True:
                    try:
                        data = await self.receive_message(timeout=5.0)
                        if data is None:
                            continue

                        if "text" in data and data["text"].strip():
                            raw_text = data["text"].strip()
                            mode = data.get("mode", "")

                            if mode == "offline" or mode == "2pass-offline":
                                # offline模式的结果，累积文本
                                accumulated_text += raw_text + " "

                                # 实时发送识别片段到前端
                                if progress_callback:
                                    # 获取配置并处理识别结果
                                    config = await get_system_config_async()
                                    result = process_recognition_result(
                                        raw_text, config
                                    )
                                    accumulated_result = process_recognition_result(
                                        accumulated_text.strip(), config
                                    )

                                    await progress_callback(
                                        {
                                            "type": "recognition_segment",
                                            "text": result["cleaned_text"],
                                            "accumulated": accumulated_result[
                                                "cleaned_text"
                                            ],
                                            "mode": mode,
                                        }
                                    )

                        # 等待is_final信号，这是关键
                        if data.get("is_final", False):
                            recognition_complete.set()
                            break

                    except Exception as e:
                        logger.error(f"接收识别结果错误: {e}")
                        break

            # 启动结果接收任务
            result_task = asyncio.create_task(handle_recognition_results())

            # 按照官方HTML方式分块发送：固定960字节块
            pos = 0
            chunk_count = 0
            while pos < len(pcm_data):
                chunk = pcm_data[pos : pos + chunk_size]
                if len(chunk) == 0:
                    break

                await self.send_audio_data(chunk)
                chunk_count += 1
                pos += chunk_size

            # 发送结束信号（模拟官方HTML的stop()调用）
            end_config = {"is_speaking": False}
            await self.send_config(end_config)

            # 等待识别完成或超时
            try:
                await asyncio.wait_for(recognition_complete.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("等待识别结果超时")

            # 取消结果任务
            result_task.cancel()

            # 清理并返回最终结果
            config = await get_system_config_async()
            result = process_recognition_result(accumulated_text.strip(), config)
            return result["cleaned_text"]

        except Exception as e:
            logger.error(f"音频识别错误: {e}")
            return ""
        finally:
            await self.disconnect()

    async def send_end_signal(self):
        """发送结束信号"""
        if self.websocket:
            try:
                end_config = {"is_speaking": False, "wav_name": "stream_end"}
                await self.send_config(end_config)

            except Exception as e:
                logger.error(f"发送结束信号失败: {e}")

    def is_connected(self) -> bool:
        """检查是否已连接"""
        if self.websocket is None:
            return False

        # 安全检查连接状态
        try:
            if hasattr(self.websocket, "closed"):
                return not self.websocket.closed
            else:
                # 如果没有closed属性，假设连接有效
                return True
        except (AttributeError, Exception):
            # 某些websockets版本没有closed属性或访问失败，假设连接有效
            return True
