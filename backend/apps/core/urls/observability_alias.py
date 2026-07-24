"""Frontend compatibility alias: /api/v1/observability/...

The frontend observability/log page calls:

    GET /api/v1/observability/loki/query/?query=...&start=...&end=...&limit=...
    GET /api/v1/observability/grafana/embed/<slug>/?time=...&var-service=...

The canonical routes live one level deeper at
``/api/v1/core/observability/...`` (see ``apps/core/urls.py``).
This module re-exports the same function-based views under the
flatter ``/api/v1/observability/`` prefix so the frontend does
not need to be rebuilt. New code should call the canonical path.
"""
from apps.core.views.observability import (
    grafana_embed_url,
    loki_label_values,
    loki_query,
    prometheus_query,
)
from django.urls import path

urlpatterns = [
    path('loki/query/', loki_query, name='observability-loki-query-alias'),
    path(
        'loki/label/<str:label>/values/',
        loki_label_values,
        name='observability-loki-label-values-alias',
    ),
    path(
        'grafana/embed/<str:dashboard_uid>/',
        grafana_embed_url,
        name='observability-grafana-embed-alias',
    ),
    path(
        'prometheus/query/',
        prometheus_query,
        name='observability-prometheus-query-alias',
    ),
]
