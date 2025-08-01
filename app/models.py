from django.db import models

class SystemConfig(models.Model):
    """系统配置模型"""
    # LLM配置
    llm_api_base = models.URLField(default="https://server.com/api", help_text="LLM API基础URL")
    llm_api_key = models.CharField(max_length=200, default="", help_text="LLM API密钥")
    llm_model = models.CharField(max_length=100, default="", help_text="LLM模型名称")
    
    # Web服务器配置
    web_host = models.CharField(max_length=100, default="0.0.0.0", help_text="Web服务器监听地址")
    web_http_port = models.IntegerField(default=8000, help_text="HTTP端口")
    web_https_port = models.IntegerField(default=8443, help_text="HTTPS端口")
    web_ssl_enabled = models.BooleanField(default=False, help_text="启用HTTPS/SSL")
    web_ssl_cert_file = models.FileField(upload_to='ssl_certs/', blank=True, null=True, help_text="上传SSL证书文件(.crt或.pem)")
    web_ssl_key_file = models.FileField(upload_to='ssl_certs/', blank=True, null=True, help_text="上传SSL私钥文件(.key)")
    
    # FunASR配置
    funasr_host = models.CharField(max_length=100, default="funasr", help_text="FunASR服务器地址")
    funasr_port = models.IntegerField(default=10095, help_text="FunASR服务器端口")
    funasr_ssl = models.BooleanField(default=False, help_text="是否使用SSL")
    funasr_ssl_verify = models.BooleanField(default=False, help_text="是否验证SSL证书")
    
    # FunASR连接池配置
    use_connection_pool = models.BooleanField(default=True, help_text="是否使用连接池模式")
    pool_min_connections = models.IntegerField(default=2, help_text="连接池最小连接数")
    pool_max_connections = models.IntegerField(default=20, help_text="连接池最大连接数")
    pool_max_idle_time = models.IntegerField(default=300, help_text="连接最大空闲时间（秒）")
    
    # 用户会话配置
    session_cleanup_hours = models.IntegerField(default=1, help_text="清理非活跃用户的小时数")
    max_conversation_history = models.IntegerField(default=5, help_text="最大对话历史轮数")
    
    # 音频配置
    audio_sample_rate = models.IntegerField(default=16000, choices=[(8000, '8kHz'), (16000, '16kHz'), (22050, '22kHz')])
    audio_chunk_size = models.IntegerField(default=4096, help_text="音频块大小")
    audio_send_interval = models.IntegerField(default=100, help_text="发送间隔(ms)")
    
    # TTS配置
    tts_api_key = models.CharField(max_length=200, default="", help_text="DashScope TTS API密钥")
    tts_model = models.CharField(max_length=50, default="cosyvoice-v2", help_text="TTS模型")
    tts_voice = models.CharField(max_length=50, default="longxiaochun_v2", help_text="TTS音色")
    tts_sample_rate = models.IntegerField(default=22050, choices=[(8000, '8kHz'), (16000, '16kHz'), (22050, '22.05kHz'), (24000, '24kHz'), (44100, '44.1kHz'), (48000, '48kHz')], help_text="TTS采样率")
    tts_enabled = models.BooleanField(default=False, help_text="启用TTS功能")
    
    # AI系统提示词配置
    ai_system_prompt = models.TextField(
        default="你是一个AI语音助手，请尽可能简短自然的回答用户，你的回答将直接被转换为语音播放给用户，要考虑语音播放的时长。",
        help_text="AI助手的系统提示词，用于定义AI的角色和行为"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        app_label = 'app'
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
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            "llm_api_base": self.llm_api_base,
            "llm_api_key": self.llm_api_key,
            "llm_model": self.llm_model,
            "web_host": self.web_host,
            "web_http_port": self.web_http_port,
            "web_https_port": self.web_https_port,
            "web_ssl_enabled": self.web_ssl_enabled,
            "web_ssl_cert_file": self.web_ssl_cert_file.path if self.web_ssl_cert_file else "",
            "web_ssl_key_file": self.web_ssl_key_file.path if self.web_ssl_key_file else "",
            "funasr_host": self.funasr_host,
            "funasr_port": self.funasr_port,
            "funasr_ssl": self.funasr_ssl,
            "funasr_ssl_verify": self.funasr_ssl_verify,
            "use_connection_pool": self.use_connection_pool,
            "pool_min_connections": self.pool_min_connections,
            "pool_max_connections": self.pool_max_connections,
            "pool_max_idle_time": self.pool_max_idle_time,
            "session_cleanup_hours": self.session_cleanup_hours,
            "max_conversation_history": self.max_conversation_history,
            "audio_sample_rate": self.audio_sample_rate,
            "audio_chunk_size": self.audio_chunk_size,
            "audio_send_interval": self.audio_send_interval,
            "tts_api_key": self.tts_api_key,
            "tts_model": self.tts_model,
            "tts_voice": self.tts_voice,
            "tts_sample_rate": self.tts_sample_rate,
            "tts_enabled": self.tts_enabled,
            "ai_system_prompt": self.ai_system_prompt,
            "ai_simple_system_prompt": self.ai_simple_system_prompt
        }

class UserSession(models.Model):
    """用户会话模型"""
    session_id = models.CharField(max_length=50, unique=True, help_text="会话ID")
    conversation_history = models.JSONField(default=list, help_text="对话历史")
    created_at = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)
    
    class Meta:
        app_label = 'app'
        verbose_name = "用户会话"
        verbose_name_plural = "用户会话"
        ordering = ['-last_active']
    
    def __str__(self):
        return f"会话 {self.session_id[:8]}... (最后活跃: {self.last_active.strftime('%Y-%m-%d %H:%M:%S')})"
    
    def add_conversation(self, user_input, ai_response):
        """添加对话记录"""
        from django.utils import timezone
        
        self.conversation_history.append({
            "user": user_input,
            "assistant": ai_response,
            "timestamp": timezone.now().isoformat()
        })
        
        # 只保留最近的对话
        config = SystemConfig.get_config()
        if len(self.conversation_history) > config.max_conversation_history:
            self.conversation_history = self.conversation_history[-config.max_conversation_history:]
        
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
        from django.utils import timezone
        from datetime import timedelta
        
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
            "hours_since_active": round(hours_since_active, 2)
        }
