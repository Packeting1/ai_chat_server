from django.db import models


class SystemConfig(models.Model):
    """系统配置模型"""

    # LLM配置
    llm_api_base = models.URLField(
        default="https://server.com/api",
        verbose_name="LLM API基础URL",
        help_text="须兼容OpenAI API格式。",
    )
    llm_api_key = models.CharField(
        max_length=200, default="", verbose_name="LLM API密钥"
    )
    llm_model = models.CharField(
        max_length=100, default="", verbose_name="LLM 模型名称"
    )

    # Web服务器配置
    web_host = models.CharField(
        max_length=100, default="0.0.0.0", verbose_name="Web服务器监听地址"
    )
    web_http_port = models.IntegerField(default=8000, verbose_name="HTTP端口")
    web_https_port = models.IntegerField(default=8443, verbose_name="HTTPS端口")
    web_ssl_enabled = models.BooleanField(default=False, verbose_name="启用HTTPS/SSL")
    web_ssl_cert_file = models.FileField(
        upload_to="ssl_certs/",
        blank=True,
        null=True,
        help_text="文件类型：crt、pem",
        verbose_name="SSL证书文件",
    )
    web_ssl_key_file = models.FileField(
        upload_to="ssl_certs/",
        blank=True,
        null=True,
        help_text="文件类型：key",
        verbose_name="SSL私钥文件",
    )

    # FunASR配置
    funasr_host = models.CharField(
        max_length=100, default="funasr_server", verbose_name="FunASR服务器地址"
    )
    funasr_port = models.IntegerField(default=10095, verbose_name="FunASR服务器端口")
    funasr_mode = models.CharField(
        max_length=10,
        default="2pass-offline",
        verbose_name="FunASR模式",
        choices=[("offline", "离线模式"), ("online", "在线模式"), ("2pass-offline", "2pass离线模式")],
    )
    funasr_ssl = models.BooleanField(default=False, verbose_name="使用SSL")
    funasr_ssl_verify = models.BooleanField(default=False, verbose_name="验证SSL证书")

    # FunASR连接池配置
    use_connection_pool = models.BooleanField(
        default=True, verbose_name="使用FunASR连接池模式"
    )
    pool_min_connections = models.IntegerField(
        default=2, verbose_name="连接池最小连接数"
    )
    pool_max_connections = models.IntegerField(
        default=20, verbose_name="连接池最大连接数"
    )
    pool_max_idle_time = models.IntegerField(
        default=300, verbose_name="连接最大空闲时间（秒）"
    )

    # 用户会话配置
    session_cleanup_hours = models.IntegerField(
        default=1, verbose_name="清理非活跃用户的小时数"
    )
    max_conversation_history = models.IntegerField(
        default=5, verbose_name="最大对话历史轮数"
    )

    # 对话模式配置
    continuous_conversation = models.BooleanField(
        default=True,
        verbose_name="持续对话模式",
        help_text="启用时为持续对话模式，关闭时为一次性对话模式。",
    )

    # TTS配置
    tts_enabled = models.BooleanField(default=False, verbose_name="启用TTS功能")

    tts_api_key = models.CharField(
        max_length=200, default="", verbose_name="DashScope API密钥"
    )
    tts_model = models.CharField(
        max_length=50, default="cosyvoice-v2", verbose_name="TTS模型名称"
    )
    tts_sample_rate = models.IntegerField(
        default=22050,
        choices=[
            (8000, "8kHz"),
            (16000, "16kHz"),
            (22050, "22.05kHz"),
            (24000, "24kHz"),
            (44100, "44.1kHz"),
            (48000, "48kHz"),
        ],
        verbose_name="TTS采样率",
    )

    tts_volume = models.IntegerField(
        default=80, verbose_name="TTS音量", help_text="音量大小，范围0-100"
    )
    tts_speech_rate = models.FloatField(
        default=1.0, verbose_name="TTS语速", help_text="语速倍率，1.0为正常语速"
    )
    tts_pitch_rate = models.FloatField(
        default=1.0, verbose_name="TTS音调", help_text="音调倍率，1.0为正常音调"
    )
    tts_audio_format = models.CharField(
        max_length=10,
        default="pcm",
        verbose_name="TTS音频格式",
        choices=[("pcm", "PCM"), ("mp3", "MP3"), ("wav", "WAV")],
        help_text="音频输出格式",
    )

    # TTS语言配置
    tts_default_voice = models.CharField(
        max_length=50,
        default="longanran",
        verbose_name="TTS默认音色"
    )
    tts_mandarin_voice = models.CharField(
        max_length=50,
        default="longanran",
        verbose_name="TTS普通话音色"
    )
    tts_cantonese_voice = models.CharField(
        max_length=50,
        default="longtao_v2",
        verbose_name="TTS粤语音色"
    )
    tts_english_voice = models.CharField(
        max_length=50,
        default="loongcally_v2",
        verbose_name="TTS英语音色"
    )
    tts_japanese_voice = models.CharField(
        max_length=50,
        default="loongtomoka_v2",
        verbose_name="TTS日语音色"
    )
    tts_korean_voice = models.CharField(
        max_length=50,
        default="loongkyong_v2",
        verbose_name="TTS韩语音色"
    )

    # TTS连接池配置
    tts_use_connection_pool = models.BooleanField(
        default=True, verbose_name="启用TTS连接池模式"
    )
    tts_max_concurrent = models.IntegerField(
        default=3, verbose_name="TTS最大并发任务数"
    )
    tts_pool_max_total = models.IntegerField(
        default=10, verbose_name="TTS连接池最大连接数"
    )
    tts_connection_max_error_count = models.IntegerField(
        default=3, verbose_name="TTS连接最大错误次数"
    )
    tts_pool_min_idle = models.IntegerField(
        default=2, verbose_name="TTS连接池最小空闲连接数"
    )
    tts_pool_max_idle = models.IntegerField(
        default=5, verbose_name="TTS连接池最大空闲连接数"
    )
    tts_pool_connection_timeout = models.FloatField(
        default=10.0, verbose_name="TTS连接超时时间（秒）"
    )
    tts_pool_cleanup_interval = models.FloatField(
        default=60.0, verbose_name="TTS连接池清理间隔（秒）"
    )
    tts_connection_max_idle_time = models.FloatField(
        default=300.0, verbose_name="TTS连接最大空闲时间（秒）"
    )
    tts_pool_max_wait_time = models.FloatField(
        default=30.0, verbose_name="TTS连接池最大等待时间（秒）"
    )

    # 页面标题配置
    page_title_zh = models.CharField(
        max_length=100, default="实时智能语音对话", verbose_name="页面标题（中文）"
    )
    page_title_en = models.CharField(
        max_length=100,
        default="Real-time AI Voice Chat",
        verbose_name="页面标题（英文）",
    )
    main_title_zh = models.CharField(
        max_length=100, default="实时智能语音助手", verbose_name="主标题（中文）"
    )
    main_title_en = models.CharField(
        max_length=100,
        default="Real-time AI Voice Assistant",
        verbose_name="主标题（英文）",
    )

    # Logo配置
    logo_image = models.ImageField(
        upload_to="logos/",
        blank=True,
        null=True,
        verbose_name="Logo图片",
        help_text="支持PNG、JPG、SVG等格式，建议尺寸：154x35像素",
    )

    # AI系统提示词配置
    ai_system_prompt = models.TextField(
        default="你是一个AI语音助手，请尽可能简短自然的回答用户，你的回答将直接被转换为语音播放给用户，要考虑语音播放的时长。",
        help_text="AI助手的系统提示词，用于定义AI的角色和行为",
        verbose_name="AI系统提示词",
    )

    # Think标签过滤配置
    filter_think_tags = models.BooleanField(
        default=False,
        help_text="过滤AI响应中的think标签及其内容，仅适用于LLM推理模型。",
        verbose_name="过滤think标签",
    )

    # Nothink前缀配置
    use_nothink_prefix = models.BooleanField(
        default=False,
        help_text="指示AI不使用推理模式，仅适用于部分LLM动态推理模型，如QWen3部分系列模型。",
        verbose_name="使用/nothink前缀",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "app"
        verbose_name = "系统配置"
        verbose_name_plural = "系统配置"

    def __str__(self):
        return f"系统配置 (更新于: {self.updated_at.strftime('%Y-%m-%d %H:%M:%S')})"

    @classmethod
    def get_config(cls):
        """获取当前配置，如果不存在则创建默认配置"""
        config, created = cls.objects.get_or_create(pk=1)
        return config

    def get_funasr_uri(self):
        """获取FunASR连接URI"""
        protocol = "wss" if self.funasr_ssl else "ws"
        return f"{protocol}://{self.funasr_host}:{self.funasr_port}"

class UserSession(models.Model):
    """用户会话模型"""

    session_id = models.CharField(max_length=50, unique=True, verbose_name="会话ID")
    conversation_history = models.JSONField(default=list, verbose_name="对话历史")
    created_at = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "app"
        verbose_name = "用户会话"
        verbose_name_plural = "用户会话"
        ordering = ["-last_active"]

    def __str__(self):
        return f"会话 {self.session_id[:8]}... (最后活跃: {self.last_active.strftime('%Y-%m-%d %H:%M:%S')})"

    def add_conversation(self, user_input, ai_response, max_history=None):
        """添加对话记录"""
        from django.utils import timezone

        self.conversation_history.append(
            {
                "user": user_input,
                "assistant": ai_response,
                "timestamp": timezone.now().isoformat(),
            }
        )

        # 只保留最近的对话
        if max_history is None:
            config = SystemConfig.get_config()
            max_history = config.max_conversation_history

        if len(self.conversation_history) > max_history:
            self.conversation_history = self.conversation_history[-max_history:]

        self.save()

    def reset_conversation(self):
        """重置对话历史"""
        self.conversation_history = []
        self.save()

    def get_conversation_history(self):
        """获取对话历史（不含时间戳）"""
        return [
            {"user": conv["user"], "assistant": conv["assistant"]}
            for conv in self.conversation_history
        ]

    @classmethod
    def cleanup_inactive_sessions(cls, inactive_hours=None):
        """清理非活跃的用户会话"""
        from datetime import timedelta

        from django.utils import timezone

        if inactive_hours is None:
            config = SystemConfig.get_config()
            inactive_hours = config.session_cleanup_hours

        cutoff_time = timezone.now() - timedelta(hours=inactive_hours)
        inactive_sessions = cls.objects.filter(last_active__lt=cutoff_time)
        count = inactive_sessions.count()
        inactive_sessions.delete()

        return count

    def to_dict(self):
        """转换为字典格式"""
        from django.utils import timezone

        current_time = timezone.now()
        hours_since_active = (current_time - self.last_active).total_seconds() / 3600

        return {
            "session_id": self.session_id[:8] + "...",  # 只显示前8位保护隐私
            "conversation_count": len(self.conversation_history),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "last_active": self.last_active.strftime("%Y-%m-%d %H:%M:%S"),
            "hours_since_active": round(hours_since_active, 2),
        }
