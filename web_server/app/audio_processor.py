import io
import logging
import struct
import wave
from typing import Any

logger = logging.getLogger(__name__)


def get_audio_info(audio_data: bytes) -> dict[str, Any]:
    """
    获取音频文件信息

    Args:
        audio_data: 音频文件数据

    Returns:
        音频信息字典
    """
    info = {
        "size": len(audio_data),
        "format": "unknown",
        "channels": 0,
        "sample_rate": 0,
        "duration": 0.0,
    }

    try:
        # 检测WAV格式
        if audio_data.startswith(b"RIFF") and b"WAVE" in audio_data[:12]:
            info["format"] = "wav"

            # 解析WAV头部
            with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
                info["channels"] = wav_file.getnchannels()
                info["sample_rate"] = wav_file.getframerate()
                info["duration"] = wav_file.getnframes() / wav_file.getframerate()

        # 检测其他格式
        elif audio_data.startswith(b"ID3") or audio_data.startswith(b"\xff\xfb"):
            info["format"] = "mp3"
        elif audio_data.startswith(b"OggS"):
            info["format"] = "ogg"
        elif audio_data.startswith(b"fLaC"):
            info["format"] = "flac"

    except Exception as e:
        logger.warning(f"解析音频信息失败: {e}")

    return info


def convert_to_pcm(audio_data: bytes, filename: str = "") -> tuple[bytes, int]:
    """
    转换音频到PCM格式

    Args:
        audio_data: 音频文件数据
        filename: 文件名（用于格式判断）

    Returns:
        (PCM数据, 采样率)
    """
    try:
        # 检测是否为WAV格式
        if audio_data.startswith(b"RIFF") and b"WAVE" in audio_data[:12]:
            return process_wav_audio(audio_data)

        # 对于其他格式，尝试使用ffmpeg转换
        return convert_with_ffmpeg(audio_data, filename)

    except Exception as e:
        logger.error(f"音频转换失败: {e}")
        # 返回空数据和默认采样率
        return b"", 16000


def process_wav_audio(audio_data: bytes) -> tuple[bytes, int]:
    """
    处理WAV音频文件

    Args:
        audio_data: WAV音频数据

    Returns:
        (PCM数据, 采样率)
    """
    try:
        with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
            # 获取音频参数
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())

            # 转换为单声道16位PCM
            pcm_data = convert_to_mono_16bit(frames, channels, sample_width)

            # 重采样到16kHz（如果需要）
            if sample_rate != 16000:
                pcm_data = resample_audio(pcm_data, sample_rate, 16000)
                sample_rate = 16000

            return pcm_data, sample_rate

    except Exception as e:
        logger.error(f"处理WAV音频失败: {e}")
        raise


def convert_to_mono_16bit(audio_data: bytes, channels: int, sample_width: int) -> bytes:
    """
    转换音频为单声道16位格式

    Args:
        audio_data: 原始音频数据
        channels: 声道数
        sample_width: 采样宽度（字节）

    Returns:
        转换后的PCM数据
    """
    if sample_width == 2 and channels == 1:
        # 已经是目标格式
        return audio_data

    # 解析音频数据
    if sample_width == 1:
        # 8位音频
        samples = struct.unpack(f"<{len(audio_data)}B", audio_data)
        # 转换到16位范围
        samples = [(s - 128) * 256 for s in samples]
    elif sample_width == 2:
        # 16位音频
        samples = struct.unpack(f"<{len(audio_data)//2}h", audio_data)
    elif sample_width == 4:
        # 32位音频
        samples = struct.unpack(f"<{len(audio_data)//4}i", audio_data)
        # 转换到16位范围
        samples = [s // 65536 for s in samples]
    else:
        raise ValueError(f"不支持的采样宽度: {sample_width}")

    # 转换为单声道
    if channels > 1:
        mono_samples = []
        for i in range(0, len(samples), channels):
            # 取所有声道的平均值
            channel_sum = sum(samples[i : i + channels])
            mono_samples.append(channel_sum // channels)
        samples = mono_samples

    # 确保在16位范围内
    samples = [max(-32768, min(32767, s)) for s in samples]

    # 打包为16位PCM
    return struct.pack(f"<{len(samples)}h", *samples)


def resample_audio(pcm_data: bytes, from_rate: int, to_rate: int) -> bytes:
    """
    简单的音频重采样

    Args:
        pcm_data: 16位单声道PCM数据
        from_rate: 源采样率
        to_rate: 目标采样率

    Returns:
        重采样后的PCM数据
    """
    if from_rate == to_rate:
        return pcm_data

    # 解析16位样本
    samples = struct.unpack(f"<{len(pcm_data)//2}h", pcm_data)

    # 计算重采样比率
    ratio = to_rate / from_rate
    new_length = int(len(samples) * ratio)

    # 简单线性插值重采样
    resampled = []
    for i in range(new_length):
        # 计算源样本位置
        src_pos = i / ratio
        src_index = int(src_pos)

        if src_index >= len(samples) - 1:
            resampled.append(samples[-1])
        else:
            # 线性插值
            t = src_pos - src_index
            sample = samples[src_index] * (1 - t) + samples[src_index + 1] * t
            resampled.append(int(sample))

    # 打包为字节
    return struct.pack(f"<{len(resampled)}h", *resampled)


def convert_with_ffmpeg(audio_data: bytes, filename: str) -> tuple[bytes, int]:
    """
    使用ffmpeg转换音频格式

    Args:
        audio_data: 音频数据
        filename: 文件名

    Returns:
        (PCM数据, 采样率)
    """
    import os
    import subprocess
    import tempfile

    # 创建临时文件
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=os.path.splitext(filename)[1]
    ) as input_file:
        input_file.write(audio_data)
        input_path = input_file.name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as output_file:
        output_path = output_file.name

    try:
        # 使用ffmpeg转换
        cmd = [
            "ffmpeg",
            "-i",
            input_path,
            "-ar",
            "16000",  # 采样率16kHz
            "-ac",
            "1",  # 单声道
            "-f",
            "wav",  # WAV格式
            "-y",  # 覆盖输出文件
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"ffmpeg转换失败: {result.stderr}")
            raise RuntimeError(f"ffmpeg转换失败: {result.stderr}")

        # 读取转换后的文件
        with open(output_path, "rb") as f:
            wav_data = f.read()

        # 处理WAV文件
        return process_wav_audio(wav_data)

    finally:
        # 清理临时文件
        try:
            os.unlink(input_path)
            os.unlink(output_path)
        except OSError:
            # 忽略文件删除错误（文件可能不存在）
            pass


def process_audio_data(audio_data: bytes, filename: str = "") -> tuple[bytes, int]:
    """
    处理音频数据的主函数

    Args:
        audio_data: 音频文件数据
        filename: 文件名

    Returns:
        (PCM数据, 采样率)
    """

    try:
        # 验证音频数据
        get_audio_info(audio_data)

        # 转换为PCM
        pcm_data, sample_rate = convert_to_pcm(audio_data, filename)

        return pcm_data, sample_rate

    except Exception as e:
        logger.error(f"处理音频数据失败: {e}")
        raise
