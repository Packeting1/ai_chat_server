from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/stream/?$", consumers.StreamChatConsumerV2.as_asgi()),
    # re_path(r"ws/upload/?$", consumers.UploadConsumer.as_asgi()),
]
