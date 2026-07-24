from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..services.analytics import RevenueAnalytics


class AnalyticsPayloadSerializer(serializers.Serializer):
    data = serializers.JSONField()  # type: ignore[assignment]


class AnalyticsViewSet(viewsets.GenericViewSet):
    serializer_class = AnalyticsPayloadSerializer
    permission_classes = [permissions.IsAdminUser]

    def list(self, request):
        # Overview
        analytics = RevenueAnalytics()
        data = analytics.get_overview()
        return Response(data)

    @action(detail=False, methods=['GET'])
    def revenue(self, request):
        analytics = RevenueAnalytics()
        data = analytics.get_revenue_chart()
        return Response(data)

    @action(detail=False, methods=['GET'])
    def plans(self, request):
        analytics = RevenueAnalytics()
        data = analytics.get_plan_breakdown()
        return Response(data)

    @action(detail=False, methods=['GET'])
    def customers(self, request):
        analytics = RevenueAnalytics()
        data = analytics.get_top_customers()
        return Response(data)

    @action(detail=False, methods=['GET'])
    def costs(self, request):
        analytics = RevenueAnalytics()
        data = analytics.get_infrastructure_costs()
        return Response(data)
