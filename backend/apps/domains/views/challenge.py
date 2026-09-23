"""Unauthenticated HTTP-proof challenge endpoint for custom domains.

DNS-quorum verification cannot see through Cloudflare proxying or apex
CNAME flattening (external resolvers get edge IPs / NoAnswer), so
orange-proxied domains could never verify. This endpoint serves the
row's unguessable per-domain token at a fixed well-known path; the
verifier fetches it over the PUBLIC edge (grey or orange), which proves
end-to-end control of the hostname — same strength as DNS proof, plus
path proof. Unknown tokens 404 without revealing anything.
"""
from django.http import HttpResponse, HttpResponseNotFound
from django.views.decorators.http import require_GET


@require_GET
def domain_challenge(request, token):
    token = (token or "").strip()
    if not token or len(token) > 64:
        return HttpResponseNotFound("not found")
    from apps.domains.models import Domain
    row = Domain.objects.filter(verification_token=token).only(
        "verification_token").first()
    if row is None:
        return HttpResponseNotFound("not found")
    return HttpResponse(row.verification_token, content_type="text/plain")
