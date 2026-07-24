"""API Token management views — generate, list, revoke tokens for CLI access."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.deployments.models.api_token import APIToken


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_tokens(request):
    """List all API tokens for the current user."""
    tokens = APIToken.objects.filter(user=request.user).values(
        "id", "name", "prefix", "created_at", "last_used_at", "is_active"
    )
    return Response({"tokens": list(tokens)})


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_token(request):
    """
    Generate a new API token.
    Body: { "name": "My CLI" }

    Returns the raw token ONCE — it cannot be retrieved again.
    """
    name = request.data.get("name", "CLI Token").strip()
    if not name:
        name = "CLI Token"

    token_obj, raw_token = APIToken.create_token(user=request.user, name=name)

    return Response({
        "token": raw_token,
        "id": str(token_obj.id),
        "name": token_obj.name,
        "prefix": token_obj.prefix,
        "message": "Save this token — it won't be shown again.",
    }, status=status.HTTP_201_CREATED)


@extend_schema(
    parameters=[
        OpenApiParameter(
            name='token_id',
            type=OpenApiTypes.UUID,
            location=OpenApiParameter.PATH,
        )
    ],
    responses=OpenApiTypes.OBJECT,
)
@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def revoke_token(request, token_id):
    """Revoke (deactivate) an API token."""
    try:
        token = APIToken.objects.get(id=token_id, user=request.user)
    except APIToken.DoesNotExist:
        return Response({"error": "Token not found."}, status=status.HTTP_404_NOT_FOUND)

    token.is_active = False
    token.save(update_fields=["is_active"])
    return Response({"status": "revoked", "id": str(token.id)})
