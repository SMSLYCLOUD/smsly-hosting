"""meta mixin."""
import logging

from django.db.models import Avg, DurationField, ExpressionWrapper, F

from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models import Deployment, Service
from ...serializers import DeploymentTimelineSerializer

logger = logging.getLogger(__name__)


class MetaActionsMixin:
    """MetaActions actions for the viewset."""


    @action(detail=True, methods=['get'])
    def timeline(self, request, pk=None):
        """
        Deployment timeline for a service — paginated, lightweight.
        GET /api/v1/services/{id}/timeline/
        Query params: ?status=ACTIVE&limit=20
        """
        service = self.get_object()
        deployments = service.deployments.all().order_by('-created_at')

        # Filter by status if requested
        status_filter = request.query_params.get('status')
        if status_filter:
            deployments = deployments.filter(status=status_filter.upper())

        page = self.paginate_queryset(deployments)
        if page is not None:
            serializer = DeploymentTimelineSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = DeploymentTimelineSerializer(deployments, many=True)
        return Response(serializer.data)


    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """
        Deployment statistics for a service.
        GET /api/v1/services/{id}/stats/

        Returns: total deploys, success rate, avg duration, rollback count.
        """
        service = self.get_object()
        deploys = service.deployments.all()

        total = deploys.count()
        active = deploys.filter(status=Deployment.Status.ACTIVE).count()
        failed = deploys.filter(status=Deployment.Status.FAILED).count()
        rollbacks = deploys.filter(is_rollback=True).count()

        # Average duration of successful deployments
        successful = deploys.filter(
            status=Deployment.Status.ACTIVE,
            started_at__isnull=False,
            finished_at__isnull=False,
        ).annotate(
            duration=ExpressionWrapper(
                F('finished_at') - F('started_at'),
                output_field=DurationField()
            )
        ).aggregate(avg_duration=Avg('duration'))

        avg_seconds = None
        if successful['avg_duration']:
            avg_seconds = successful['avg_duration'].total_seconds()

        success_rate = (active / total * 100) if total > 0 else 0

        return Response({
            'total_deployments': total,
            'active': active,
            'failed': failed,
            'rollbacks': rollbacks,
            'success_rate': round(success_rate, 1),
            'avg_duration_seconds': round(avg_seconds, 1) if avg_seconds else None,
        })

    # --- Nested Resources: Environment Variables ---
    # NOTE: Keep GET and POST on a single @action. DRF collects actions via
    # `inspect.getmembers()` (sorted by name), which can register duplicate
    # url_path patterns in an unexpected order and cause 405s for valid methods.

    @action(detail=True, methods=['get'], url_path='dependencies', permission_classes=[permissions.IsAuthenticated])
    def dependencies(self, request, pk=None):
        try:
            service = self.get_queryset().get(id=pk)
        except Service.DoesNotExist:
            return Response({"error": "Service not found"}, status=status.HTTP_404_NOT_FOUND)

        # Build a simple dependency map using the same logic as the ecosystem
        # planner – we reuse the helper that extracts ``depends_on`` from the
        # stored plan (if any).  For simplicity we look at the Service model's
        # ``plan`` JSONField (assumed to exist) and read ``depends_on``.
        plan = getattr(service, 'plan', {}) or {}
        raw_deps = plan.get('depends_on', [])
        deps = []
        for token in raw_deps:
            # Resolve token to a Service if possible — only surface
            # services the caller can access.
            try:
                dep_svc = self.get_queryset().filter(name__iexact=token).first()
                if dep_svc:
                    deps.append({"id": str(dep_svc.id), "name": dep_svc.name})
            except Exception:
                continue

        # Find dependents (services that list this one in their depends_on)
        # and only include those the caller can access.
        dependents_qs = self.get_queryset().filter(plan__contains={"depends_on": [service.name]})
        dependents = [{"id": str(s.id), "name": s.name} for s in dependents_qs]

        return Response({"service": {"id": str(service.id), "name": service.name}, "depends_on": deps, "dependents": dependents})

    # ---------------------------------------------------------------------
    # Bulk actions – deploy, cancel, or run AI Senate on multiple services.
    # Expected payload: {"ids": ["uuid1", "uuid2"], "action": "deploy"}
    # ---------------------------------------------------------------------

    @action(detail=False, methods=['get'], url_path='sidebar', permission_classes=[permissions.IsAuthenticated])
    def sidebar(self, request):
        """Return a minimal hierarchy of projects → repos for the UI.

        The current data model does not have an explicit ``Project`` entity,
        so we infer a project name from the ``Service.project`` attribute if it
        exists, otherwise we fall back to the repository owner (the part before
        the first ``/`` in ``repo``).  Each entry contains:

        ``project`` – string
        ``repos`` – list of ``{id, name, status}``
        """
        from collections import defaultdict
        result = defaultdict(list)
        # SECURITY: scope to the caller's accessible services via get_queryset()
        # so the sidebar cannot be used to enumerate other tenants' services.
        # Limit to 200 services to prevent unbounded queries.
        for svc in self.get_queryset()[:200]:
            # ``svc.repo`` is stored as a full URL in the model; we only need the
            # owner/repo slug for display.
            repo_slug = svc.repository_url.split('/')[-1] if svc.repository_url else str(svc.id)
            project_name = getattr(svc, 'project', None) or repo_slug.split('_')[0]
            result[project_name].append({
                'id': str(svc.id),
                'name': repo_slug,
                'status': svc.status.lower() if hasattr(svc, 'status') else 'unknown',
            })
        # Convert defaultdict to plain list for JSON serialization
        payload = [{'project': k, 'repos': v} for k, v in result.items()]
        return Response(payload)
