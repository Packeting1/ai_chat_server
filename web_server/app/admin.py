from django.contrib import admin

from .models import SystemConfig, UserSession


@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "llm_model",
        "use_connection_pool",
        "pool_max_connections",
        "tts_enabled",
        "tts_use_connection_pool",
        "updated_at",
    ]
    fieldsets = (
        (
            "Web服务器配置",
            {
                "fields": (
                    "web_host",
                    "web_http_port",
                    "web_https_port",
                    "web_ssl_enabled",
                    "web_ssl_cert_file",
                    "web_ssl_key_file",
                )
            },
        ),
        (
            "页面外观配置",
            {
                "fields": (
                    "logo_image",
                    "page_title_zh",
                    "page_title_en",
                    "main_title_zh",
                    "main_title_en",
                ),
                "description": "配置页面Logo、标题和主标题的中英文版本，支持动态语言切换。",
            },
        ),
        (
            "LLM服务配置",
            {
                "fields": (
                    "llm_api_base",
                    "llm_api_key",
                    "llm_model",
                    "use_nothink_prefix",
                    "filter_think_tags",
                    "ai_system_prompt",
                )
            },
        ),
        (
            "FunASR服务配置",
            {
                "fields": (
                    "funasr_host",
                    "funasr_port",
                    "funasr_mode",
                    "funasr_ssl",
                    "funasr_ssl_verify",
                )
            },
        ),
        (
            "DashScope TTS基础配置",
            {
                "fields": (
                    "tts_enabled",
                    "tts_api_key",
                    "tts_model",
                    "tts_sample_rate",
                ),
                "description": "DashScope为阿里云旗下的AI模型服务。",
            },
        ),
        (
            "TTS音频参数配置",
            {
                "fields": (
                    "tts_volume",
                    "tts_speech_rate",
                    "tts_pitch_rate",
                    "tts_audio_format",
                ),
                "description": "调整TTS语音的音量、语速、音调和输出格式。",
            },
        ),
        (
            "TTS语言配置",
            {
                "fields": (
                    "tts_default_voice",
                    "tts_mandarin_voice",
                    "tts_cantonese_voice",
                    "tts_english_voice",
                    "tts_japanese_voice",
                    "tts_korean_voice",
                ),
                "description": "配置TTS语言音色。ASR必须使用SenseVoice模型才可使用此功能。否则只可使用默认音色。",
            },
        ),
        (
            "TTS连接池配置",
            {
                "fields": (
                    "tts_use_connection_pool",
                    "tts_connection_max_error_count",
                    "tts_connection_max_idle_time",
                ),
                "description": "TTS连接池可以提高并发性能，适合多用户场景。连接池大小和并发数由DashScope SDK配置控制。",
            },
        ),

        (
            "会话配置",
            {
                "fields": (
                    "session_cleanup_hours",
                    "max_conversation_history",
                    "continuous_conversation",
                ),
                "description": "配置用户会话管理和对话模式。持续对话模式适合多轮交互，一次性对话模式类似Siri体验。",
            },
        ),
    )

    def has_add_permission(self, request):
        # 只允许有一个配置实例
        return not SystemConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # 不允许删除配置
        return False


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = [
        "session_id_short",
        "conversation_count",
        "created_at",
        "last_active",
        "hours_since_active",
    ]
    list_filter = ["created_at", "last_active"]
    search_fields = ["session_id"]
    readonly_fields = ["session_id", "created_at", "last_active"]
    ordering = ["-last_active"]

    def session_id_short(self, obj):
        return f"{obj.session_id[:8]}..."

    session_id_short.short_description = "会话ID"

    def conversation_count(self, obj):
        return len(obj.conversation_history)

    conversation_count.short_description = "对话数量"

    def hours_since_active(self, obj):
        from django.utils import timezone

        hours = (timezone.now() - obj.last_active).total_seconds() / 3600
        return f"{hours:.1f}小时"

    hours_since_active.short_description = "离线时间"

    actions = ["cleanup_selected_sessions"]

    def cleanup_selected_sessions(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f"成功清理了 {count} 个用户会话")

    cleanup_selected_sessions.short_description = "清理选中的会话"
