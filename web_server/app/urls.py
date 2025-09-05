from django.urls import path

from . import views

app_name = "app"

urlpatterns = [
    # 主页
    path("", views.index, name="index"),
    # API端点
    path("api/recognize/", views.recognize_audio_api, name="recognize_audio"),
    path("api/config/", views.get_config, name="config"),
    path("api/cleanup/", views.cleanup_users, name="cleanup"),
]
