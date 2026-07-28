import logging

from rest_framework import serializers
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import build_serializer_context

logger = logging.getLogger(__name__)


class _PlaceholderSerializer(serializers.Serializer):
    pass


class SmslyAutoSchema(AutoSchema):
    def _get_serializer(self):
        view = self.view
        context = build_serializer_context(view)
        try:
            if isinstance(view, serializers.Serializer):
                return view
            elif hasattr(view, 'get_serializer') and callable(view.get_serializer):
                return view.get_serializer(context=context)
            elif hasattr(view, 'get_serializer_class') and callable(view.get_serializer_class):
                return view.get_serializer_class()(context=context)
            elif hasattr(view, 'serializer_class'):
                return view.serializer_class
        except Exception as exc:
            logger.debug("Failed to resolve serializer for %s: %s", view, exc)
        return _PlaceholderSerializer()