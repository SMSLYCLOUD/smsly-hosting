"""a i router mixin."""
import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.services.ai_router import (
    DEFAULT_AI_ROUTER_API_BASE,
    DEFAULT_AI_ROUTER_UI_BASE,
    DEFAULT_BRAID_ALIAS,
    is_ai_router_service,
    persist_ai_router_config,
    serialize_ai_router_config,
)
from .._helpers import _parse_bool

logger = logging.getLogger(__name__)



class AIRouterActionsMixin:
    """AIRouterActions actions for the viewset."""


    @action(detail=True, methods=['get', 'post'], url_path='ai-router-config')
    def ai_router_config(self, request, pk=None):
        service = self.get_object()
        if not is_ai_router_service(service):
            return Response(
                {'error': 'This service is not an AI Router.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.method.upper() == 'GET':
            return Response(serialize_ai_router_config(service))

        raw_ids = request.data.get('selected_service_ids', [])
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list):
            return Response(
                {'error': '"selected_service_ids" must be a list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_base = str(
            request.data.get('api_base', DEFAULT_AI_ROUTER_API_BASE) or DEFAULT_AI_ROUTER_API_BASE
        ).strip() or DEFAULT_AI_ROUTER_API_BASE
        if not api_base.startswith('/'):
            api_base = f'/{api_base}'

        ui_base = str(
            request.data.get('ui_base', DEFAULT_AI_ROUTER_UI_BASE) or DEFAULT_AI_ROUTER_UI_BASE
        ).strip() or DEFAULT_AI_ROUTER_UI_BASE
        if not ui_base.startswith('/'):
            ui_base = f'/{ui_base}'

        braid_alias = str(
            request.data.get('braid_alias', DEFAULT_BRAID_ALIAS) or DEFAULT_BRAID_ALIAS
        ).strip() or DEFAULT_BRAID_ALIAS
        braid_enabled = _parse_bool(request.data.get('braid_enabled', True))

        persist_ai_router_config(
            service,
            selected_service_ids=[str(item).strip() for item in raw_ids],
            api_base=api_base,
            ui_base=ui_base,
            braid_alias=braid_alias,
            braid_enabled=braid_enabled,
        )
        service.refresh_from_db()
        return Response(serialize_ai_router_config(service))
