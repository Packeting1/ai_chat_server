"""
Django管理员配置
"""

from django.contrib import admin
from .models import SystemConfig, UserSession

@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = ['id', 'llm_model', 'funasr_host', 'funasr_port', 'use_connection_pool', 'pool_max_connections', 'updated_at']
    list_filter = ['updated_at', 'created_at', 'use_connection_pool']
    fieldsets = (
        ('LLM配置', {
            'fields': ('llm_api_base', 'llm_api_key', 'llm_model')
        }),
        ('AI系统提示词配置', {
            'fields': ('ai_system_prompt')
        }),
        ('Web服务器配置', {
            'fields': ('web_host', 'web_http_port', 'web_https_port', 'web_ssl_enabled', 'web_ssl_cert_file', 'web_ssl_key_file'),
            'description': 'Web服务器监听地址、HTTP/HTTPS端口和SSL证书配置。启用SSL后需要上传证书和私钥文件。'
        }),
        ('FunASR基础配置', {
            'fields': ('funasr_host', 'funasr_port', 'funasr_ssl', 'funasr_ssl_verify')
        }),
        ('FunASR连接池配置', {
            'fields': ('use_connection_pool', 'pool_min_connections', 'pool_max_connections', 'pool_max_idle_time'),
            'description': '连接池模式可以减少资源消耗，适合多用户场景。独立连接模式适合调试和小规模使用。'
        }),
        ('会话配置', {
            'fields': ('session_cleanup_hours', 'max_conversation_history')
        }),
        ('音频配置', {
            'fields': ('audio_sample_rate', 'audio_chunk_size', 'audio_send_interval')
        }),
        ('TTS配置', {
            'fields': ('tts_api_key', 'tts_model', 'tts_voice', 'tts_sample_rate', 'tts_enabled')
        }),
    )
    
    def has_add_permission(self, request):
        # 只允许有一个配置实例
        return not SystemConfig.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        # 不允许删除配置
        return False

@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ['session_id_short', 'conversation_count', 'created_at', 'last_active', 'hours_since_active']
    list_filter = ['created_at', 'last_active']
    search_fields = ['session_id']
    readonly_fields = ['session_id', 'created_at', 'last_active']
    ordering = ['-last_active']
    
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
    
    actions = ['cleanup_selected_sessions']
    
    def cleanup_selected_sessions(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f'成功清理了 {count} 个用户会话')
    cleanup_selected_sessions.short_description = "清理选中的会话"
