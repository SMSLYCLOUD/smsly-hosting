def enforce_custom_domain_quota(self, service, new_total: int):
    from django.conf import settings
    if getattr(settings, 'SMSLY_DISABLE_TIER_GATES', False):
        return None
    try:
        from apps.billing.models import UserSubscription
        sub = UserSubscription.objects.filter(user=service.owner, status='ACTIVE').first()
        limit = sub.plan.max_custom_domains if sub else 1
        if new_total > limit:
            from rest_framework.response import Response
            from rest_framework import status
            return Response(
                {'error': f'Custom domain limit reached ({limit}). Please upgrade your plan.'},
                status=status.HTTP_403_FORBIDDEN
            )
    except ImportError:
        pass
    return None
