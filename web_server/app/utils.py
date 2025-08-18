import logging
import re
import uuid

from channels.db import database_sync_to_async

from .models import SystemConfig, UserSession

logger = logging.getLogger(__name__)


@database_sync_to_async
def get_system_config_async():
    """获取系统配置（异步版本）"""
    return SystemConfig.get_config()


def extract_language_from_text(text: str) -> str | None:
    """
    从识别文本中提取语言标记

    Args:
        text: 原始识别文本

    Returns:
        检测到的语言代码，如 'zh', 'yue', 'en' 等，如果没有检测到则返回None
    """
    if not text:
        return None

    # 匹配语言标记，如 <|zh|>, <|yue|>, <|en|> 等
    language_match = re.search(r"<\|([^|]+)\|>", text)
    if language_match:
        return language_match.group(1)
    
    return None


def get_tts_voice_by_language(language: str, config) -> str:
    """
    根据语言代码获取对应的TTS音色

    Args:
        language: 语言代码，如 'zh', 'yue', 'en' 等
        config: 系统配置对象

    Returns:
        对应的TTS音色名称
    """
    # 语言到音色的映射
    language_voice_map = {
        'zh': config.tts_mandarin_voice,      # 中文普通话
        'yue': config.tts_cantonese_voice,    # 粤语
        'en': config.tts_english_voice,       # 英语
        'ja': config.tts_japanese_voice,      # 日语
        'ko': config.tts_korean_voice,        # 韩语
    }
    
    # 返回对应语言的音色，如果没有找到则返回默认音色
    return language_voice_map.get(language, config.tts_default_voice)


def clean_recognition_text(text: str) -> str:
    """
    清理识别文本，移除语言标记和元数据前缀（仅用于显示）

    Args:
        text: 原始识别文本

    Returns:
        清理后的文本
    """
    if not text:
        return text

    # 移除语言和元数据标记，如 <|zh|><|NEUTRAL|><|Speech|>, <|yue|><|NEUTRAL|><|Speech|> 等
    # 匹配模式：<|任意字符|><|任意字符|><|任意字符|>
    cleaned_text = re.sub(r"<\|[^|]*\|><\|[^|]*\|><\|[^|]*\|>\s*", "", text)

    # 移除其他可能的标记格式
    cleaned_text = re.sub(r"<\|[^|]*\|>\s*", "", cleaned_text)

    # 移除开头和结尾的空白字符
    cleaned_text = cleaned_text.strip()

    return cleaned_text


def process_recognition_result(raw_text: str, config) -> dict:
    """
    处理ASR识别结果，提取语言信息和清理文本

    Args:
        raw_text: 原始识别文本
        config: 系统配置对象

    Returns:
        包含清理后文本、检测语言和推荐音色的字典
    """
    if not raw_text:
        return {
            'cleaned_text': '',
            'detected_language': None,
            'tts_voice': config.tts_default_voice
        }

    # 提取语言标记
    detected_language = extract_language_from_text(raw_text)
    
    # 清理文本（移除所有标记）
    cleaned_text = clean_recognition_text(raw_text)
    
    # 根据检测到的语言选择TTS音色
    tts_voice = get_tts_voice_by_language(detected_language, config) if detected_language else config.tts_default_voice
    
    return {
        'cleaned_text': cleaned_text,
        'detected_language': detected_language,
        'tts_voice': tts_voice
    }


class SessionManager:
    """Django版会话管理器"""

    @staticmethod
    @database_sync_to_async
    def create_session() -> str:
        """创建新用户会话，返回会话ID"""
        session_id = str(uuid.uuid4())
        UserSession.objects.create(session_id=session_id)

        return session_id

    @staticmethod
    @database_sync_to_async
    def get_session(session_id: str) -> UserSession | None:
        """获取用户会话"""
        try:
            session = UserSession.objects.get(session_id=session_id)
            # 更新最后活跃时间
            session.save()  # 这会触发auto_now字段更新
            return session
        except UserSession.DoesNotExist:
            return None

    @staticmethod
    @database_sync_to_async
    def get_conversation_history(session_id: str) -> list[dict]:
        """获取用户对话历史"""
        try:
            session = UserSession.objects.get(session_id=session_id)
            # 更新最后活跃时间
            session.save()
            return session.get_conversation_history()
        except UserSession.DoesNotExist:
            return []

    @staticmethod
    @database_sync_to_async
    def add_conversation(
        session_id: str, user_input: str, ai_response: str, max_history: int = None
    ):
        """添加对话到用户历史"""
        try:
            session = UserSession.objects.get(session_id=session_id)
            session.add_conversation(user_input, ai_response, max_history)

        except UserSession.DoesNotExist:
            logger.warning(f"尝试更新不存在的会话: {session_id}")

    @staticmethod
    @database_sync_to_async
    def reset_conversation(session_id: str):
        """重置用户对话历史"""
        try:
            session = UserSession.objects.get(session_id=session_id)
            session.reset_conversation()

        except UserSession.DoesNotExist:
            logger.warning(f"尝试重置不存在的会话: {session_id}")

    @staticmethod
    @database_sync_to_async
    def remove_session(session_id: str):
        """移除用户会话"""
        try:
            UserSession.objects.get(session_id=session_id).delete()
        except UserSession.DoesNotExist:
            pass

    @staticmethod
    @database_sync_to_async
    def cleanup_inactive_sessions(inactive_hours: int = None):
        """清理非活跃的用户会话"""
        count = UserSession.cleanup_inactive_sessions(inactive_hours)
        return count

    @staticmethod
    @database_sync_to_async
    def get_active_users_info() -> dict:
        """获取活跃用户信息"""
        sessions = UserSession.objects.all()
        return {
            "total_users": sessions.count(),
            "users": [session.to_dict() for session in sessions],
        }

    @staticmethod
    @database_sync_to_async
    def get_user_count() -> int:
        """获取活跃用户数量"""
        return UserSession.objects.count()


# 全局会话管理器实例
session_manager = SessionManager()
