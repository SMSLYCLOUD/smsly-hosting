"""env var mixin."""
import logging

from django.db import transaction
from django.db.utils import DataError, IntegrityError

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ...models import EnvironmentVariable
from ...serializers import EnvVarSerializer
from .._helpers import _is_valid_env_key, _looks_masked_secret, _parse_bool
from apps.teams.permissions import assert_can_write

logger = logging.getLogger(__name__)



class EnvVarActionsMixin:
    """EnvVarActions actions for the viewset."""

    @action(detail=True, methods=['get', 'post'], url_path='env_vars')
    def env_vars(self, request, pk=None):
        service = self.get_object()
        reveal_secrets = not hasattr(getattr(request, 'auth', None), 'prefix')

        def _is_ciphertext(val: str) -> bool:
            """Detect Fernet ciphertext to prevent storing it as plaintext."""
            if not val or not isinstance(val, str):
                return False
            if val.startswith("gAAAA"):
                return True
            if len(val) > 100 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in val):
                try:
                    import base64
                    padded = val + '=' * (-len(val) % 4)
                    decoded = base64.urlsafe_b64decode(padded)
                    if len(decoded) >= 57 and decoded[0] == 0x80:
                        return True
                except Exception:
                    pass
            return False

        if request.method.upper() == 'GET':
            vars = service.env_vars.all().order_by('key')
            serializer = EnvVarSerializer(
                vars,
                many=True,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            )
            return Response(serializer.data)

        assert_can_write(self.request.user, service)
        payload_vars = request.data.get('vars')
        if payload_vars is not None:
            if not isinstance(payload_vars, list):
                return Response(
                    {'error': '"vars" must be a list of objects.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            normalized = []
            seen_keys = set()
            skipped_count = 0

            for idx, row in enumerate(payload_vars):
                if not isinstance(row, dict):
                    return Response(
                        {'error': f'Invalid item at index {idx}; expected object.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                key = str(row.get('key') or '').strip()
                if not key:
                    return Response(
                        {'error': f'Missing key at index {idx}.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not _is_valid_env_key(key):
                    return Response(
                        {'error': f'Invalid environment variable key "{key}".'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if key in seen_keys:
                    return Response(
                        {'error': f'Duplicate key "{key}" in import payload.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                seen_keys.add(key)

                existing = EnvironmentVariable.objects.filter(
                    service=service, key=key).first()
                value = str(row.get('value', '') or '')
                if existing and existing.is_secret and _looks_masked_secret(value):
                    value = existing.value

                if _is_ciphertext(value):
                    logger.warning(
                        "[DB-ENCRYPT] Rejecting ciphertext env var %s for service %s — "
                        "sender sent undecrypted/double-encrypted data. "
                        "This var will NOT be saved to prevent corruption.",
                        key, service.name,
                    )
                    skipped_count += 1
                    continue

                if 'is_secret' in row:
                    is_secret = _parse_bool(row.get('is_secret'))
                else:
                    is_secret = bool(existing.is_secret) if existing else False

                normalized.append({
                    'key': key,
                    'value': value,
                    'is_secret': is_secret,
                })

            added = 0
            updated = 0
            try:
                with transaction.atomic():
                    for item in normalized:
                        _, created = EnvironmentVariable.objects.update_or_create(
                            service=service,
                            key=item['key'],
                            defaults={
                                'value': item['value'],
                                'is_secret': item['is_secret'],
                                'source': 'USER',
                            },
                        )
                        if created:
                            added += 1
                        else:
                            updated += 1
            except (ValidationError, DataError, IntegrityError) as exc:
                logger.warning(
                    "Invalid bulk env payload for service %s: %s",
                    service.id, exc,
                )
                return Response(
                    {'error': 'Invalid environment variable payload.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Failed bulk env upsert for service %s: %s", service.id, exc)
                return Response(
                    {'error': 'Failed to save environment variables'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            serializer = EnvVarSerializer(
                service.env_vars.all().order_by('key'),
                many=True,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            )
            resp_data = {
                'added': added,
                'updated': updated,
                'count': len(normalized),
                'env_vars': serializer.data,
            }
            if skipped_count > 0:
                resp_data['warning'] = f"Skipped {skipped_count} environment variables with ciphertext values."
            return Response(resp_data)

        # Allow partial data — key is required, value can be empty
        key = str(request.data.get('key') or '').strip()
        if not key:
            return Response(
                {'key': ['This field is required.']},
                status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_env_key(key):
            return Response(
                {'key': ['Use letters, numbers, and underscore; cannot start with a number.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = EnvironmentVariable.objects.filter(service=service, key=key).first()
        value = str(request.data.get('value', '') or '')
        if existing and existing.is_secret and _looks_masked_secret(value):
            value = existing.value
        if _is_ciphertext(value):
            return Response(
                {'value': ['Cannot save Fernet ciphertext as value. Sender must decrypt before sending.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'is_secret' in request.data:
            is_secret = _parse_bool(request.data.get('is_secret'))
        else:
            is_secret = bool(existing.is_secret) if existing else False

        is_locked = _parse_bool(request.data.get('is_locked', False))

        try:
            env_var, created = EnvironmentVariable.objects.update_or_create(
                service=service,
                key=key,
                defaults={'value': value, 'is_secret': is_secret, 'is_locked': is_locked, 'source': 'USER'},
            )
        except (ValidationError, DataError, IntegrityError) as exc:
            logger.warning("Invalid env var payload for service %s key=%s: %s", service.id, key, exc)
            return Response(
                {'error': f'Invalid environment variable payload for key "{key}"'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to save env var for service %s key=%s: %s", service.id, key, exc)
            return Response(
                {'error': 'Failed to save environment variable'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        out = EnvVarSerializer(
            env_var,
            context={'request': request, 'reveal_secrets': reveal_secrets},
        ).data
        return Response(
            out,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @action(detail=True, methods=['get', 'delete', 'patch'],
            url_path='env_vars/(?P<var_id>\\d+)')

    def env_var_detail(self, request, pk=None, var_id=None):
        """GET / PATCH / DELETE on a single env var.

        The frontend ``getEnvVarValue`` (api.ts:591) calls
        ``GET /services/{id}/env_vars/{varId}/`` to reveal a
        secret. The previous decorator only allowed
        ``['delete', 'patch']`` which made the GET return 405
        and the secret-reveal flow silently fail.
        """
        service = self.get_object()
        try:
            var = EnvironmentVariable.objects.get(id=var_id, service=service)
        except EnvironmentVariable.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if request.method.upper() == 'GET':
            reveal_secrets = (
                request.user.is_superuser
                or var.service.owner_id == request.user.id
                or (getattr(request, 'auth', None)
                and hasattr(request.auth, 'prefix'))  # APIToken
            )
            return Response(
                EnvVarSerializer(
                    var,
                    context={'request': request, 'reveal_secrets': reveal_secrets},
                ).data
            )
        assert_can_write(self.request.user, service)
        if request.method.upper() == 'DELETE':
            var.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH — toggle is_locked (or update any field)
        if 'is_locked' in request.data:
            var.is_locked = _parse_bool(request.data['is_locked'])
        if 'is_secret' in request.data:
            var.is_secret = _parse_bool(request.data['is_secret'])
        var.save()
        return Response(
            EnvVarSerializer(
                var,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            ).data
        )
