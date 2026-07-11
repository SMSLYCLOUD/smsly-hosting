"""Traffic geolocation API endpoint."""
import logging

from django.db.models import Avg, Count, Sum
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from .models import Service
from .models_traffic import ServiceTrafficLog

logger = logging.getLogger(__name__)


class TrafficGeoViewSet(viewsets.ViewSet):
    """GET /api/v1/services/{service_pk}/traffic-geo/

    Returns aggregated traffic data grouped by country for the world map."""
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, service_pk=None):
        if not service_pk:
            return Response(
                {'error': 'Service ID required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            service = Service.objects.get(pk=service_pk)
        except Service.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not request.user.is_superuser:
            from apps.teams.permissions import get_team_q_filter
            allowed = Service.objects.filter(
                get_team_q_filter(request.user), pk=service_pk,
            ).exists()
            if not allowed:
                return Response(status=status.HTTP_403_FORBIDDEN)

        country_stats = (
            ServiceTrafficLog.objects
            .filter(service=service, geo_resolved=True, country_code__gt='')
            .values('country_code', 'country_name')
            .annotate(
                total_requests=Sum('request_count'),
                unique_ips=Count('ip_address', distinct=True),
                lat=Avg('latitude'),
                lon=Avg('longitude'),
            )
            .order_by('-total_requests')
        )

        top_cities = (
            ServiceTrafficLog.objects
            .filter(service=service, geo_resolved=True, city__gt='')
            .values('city', 'country_code')
            .annotate(
                total_requests=Sum('request_count'),
                lat=Avg('latitude'),
                lon=Avg('longitude'),
            )
            .order_by('-total_requests')[:10]
        )

        totals = ServiceTrafficLog.objects.filter(service=service).aggregate(
            total_requests=Sum('request_count'),
            unique_ips=Count('ip_address', distinct=True),
        )

        total_requests = totals['total_requests'] or 0

        countries = []
        for row in country_stats:
            count = row['total_requests'] or 0
            countries.append({
                'code': row['country_code'],
                'name': row['country_name'],
                'count': count,
                'percentage': round(count / total_requests * 100, 1) if total_requests > 0 else 0,
                'unique_ips': row['unique_ips'],
                'latitude': round(row['lat'], 4) if row.get('lat') is not None else None,
                'longitude': round(row['lon'], 4) if row.get('lon') is not None else None,
            })

        last_seen = (
            ServiceTrafficLog.objects
            .filter(service=service)
            .order_by('-last_seen')
            .values_list('last_seen', flat=True)
            .first()
        )

        return Response({
            'countries': countries,
            'top_cities': [
                {
                    'city': row['city'],
                    'country': row['country_code'],
                    'count': row['total_requests'] or 0,
                    'latitude': round(row['lat'], 4) if row.get('lat') is not None else None,
                    'longitude': round(row['lon'], 4) if row.get('lon') is not None else None,
                }
                for row in top_cities
            ],
            'total_requests': total_requests,
            'unique_ips': totals['unique_ips'] or 0,
            'unique_countries': len(countries),
            'last_updated': last_seen.isoformat() if last_seen else None,
        })
