from django.urls import path

from . import views

urlpatterns = [
    path('mcp/status/', views.McpStatusView.as_view(), name='mcp-status'),
    path('mcp/control/', views.McpControlView.as_view(), name='mcp-control'),
    path('mcp/tools/', views.McpToolListView.as_view(), name='mcp-tools'),
    path('mcp/tools/<str:name>/call/', views.McpToolCallView.as_view(), name='mcp-tool-call'),
]
