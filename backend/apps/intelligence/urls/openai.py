from django.urls import path

from ..views import ai_chat_completions

urlpatterns = [
    path('chat/completions/', ai_chat_completions, name='ai-chat-completions-root'),
]
