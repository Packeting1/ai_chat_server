import json
import logging

from asgiref.sync import async_to_sync
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .audio_processor import get_audio_info, process_audio_data
from .funasr_client import FunASRClient
from .llm_client import call_llm_simple
from .utils import process_recognition_result, add_language_tag_to_text, get_system_config_async, session_manager

logger = logging.getLogger(__name__)


async def index(request):
    """主页视图（异步版本）"""
    # 获取系统配置（直接使用异步版本）
    config = await get_system_config_async()

    # 准备模板上下文
    context = {
        "config": {
            "page_title_zh": config.page_title_zh,
            "page_title_en": config.page_title_en,
            "main_title_zh": config.main_title_zh,
            "main_title_en": config.main_title_en,
            "logo_image": config.logo_image.url if config.logo_image else "",
        }
    }

    return render(request, "app/index.html", context)


@csrf_exempt
@require_http_methods(["POST"])
def recognize_audio_api(request):
    """音频识别API（传统模式）"""
    if "audio" not in request.FILES:
        return JsonResponse({"success": False, "error": "未提供音频文件"}, status=400)

    audio_file = request.FILES["audio"]

    try:
        # 读取音频文件
        audio_data = audio_file.read()

        # 获取音频信息（用于调试）
        audio_info = get_audio_info(audio_data)

        # 异步处理音频
        async def process_audio():
            # 处理音频数据
            pcm_data, sample_rate = process_audio_data(
                audio_data, audio_file.name or ""
            )

            # 语音识别
            funasr_client = FunASRClient()
            recognized_text = await funasr_client.recognize_audio(pcm_data, sample_rate)

            # 调用LLM（传统模式不保存历史记录）
            llm_response = ""
            if recognized_text:
                # 处理识别结果，获取语言信息
                config = await get_system_config_async()
                result = process_recognition_result(recognized_text, config)
                detected_language = result["detected_language"]
                
                # 为LLM调用添加语言标签
                tagged_text = add_language_tag_to_text(recognized_text, detected_language)
                
                llm_response = await call_llm_simple(tagged_text, [])

            return {
                "success": True,
                "text": recognized_text,
                "llm_response": llm_response,
                "debug_info": {
                    "original_size": len(audio_data),
                    "processed_size": len(pcm_data),
                    "sample_rate": sample_rate,
                    "filename": audio_file.name,
                    "audio_info": audio_info,
                },
            }

        # 运行异步函数
        result = async_to_sync(process_audio)()
        return JsonResponse(result)

    except Exception as e:
        logger.error(f"音频识别API错误: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@require_http_methods(["GET"])
async def get_config(request):
    """获取系统配置（异步版本）"""
    try:
        config = await get_system_config_async()
        frontend_config = {"max_conversation_history": config.max_conversation_history}
        return JsonResponse(frontend_config)
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def cleanup_users(request):
    """清理非活跃用户"""
    try:
        data = json.loads(request.body) if request.body else {}
        inactive_hours = data.get("inactive_hours")

        cleaned_count = async_to_sync(session_manager.cleanup_inactive_sessions)(
            inactive_hours
        )
        remaining_users = async_to_sync(session_manager.get_user_count)()

        return JsonResponse(
            {
                "success": True,
                "message": f"成功清理 {cleaned_count} 个非活跃用户会话",
                "cleaned_count": cleaned_count,
                "remaining_users": remaining_users,
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "无效的JSON数据"}, status=400)
    except Exception as e:
        logger.error(f"清理用户失败: {e}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)
