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
from ...serializers.service import _must_mask_env_value
from .._helpers import _is_valid_env_key, _looks_masked_secret, _parse_bool
from apps.teams.permissions import assert_can_write, user_can_write
from apps.deployments.utils.env_sanitizer import (
    sanitize_env_value,
    is_placeholder,
    looks_wildcard_host,
)

logger = logging.getLogger(__name__)


def _scrub_user_value(value: str, key: str) -> str:
    """Scrub a value submitted via the API before persisting.

    Strips backticks / quotes / ``{{...}}`` wrappers / JS trailing comments /
    newlines / wildcards / GENERATE placeholders. Wildcards in
    ALLOWED_HOSTS / CORS are replaced with the safe ``""`` (same-origin
    only) default. Placeholders are dropped (returned as ``""``).
    """
    cleaned = sanitize_env_value(value, key=key, allow_empty=True)
    if cleaned is None or is_placeholder(cleaned):
        return ""
    return cleaned



class EnvVarActionsMixin:
    """EnvVarActions actions for the viewset."""

    @action(detail=True, methods=['get', 'post'], url_path='env_vars')
    def env_vars(self, request, pk=None):
        service = self.get_object()
        # List and write responses always mask secret values; explicit reveal uses
        # GET /services/{id}/env_vars/{var_id}/ on an individual secret.
        reveal_secrets = False

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
                except (ValueError, UnicodeDecodeError):
                    pass  # not valid base64, not ciphertext
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
                if existing and existing.is_locked:
                    return Response(
                        {'error': f'Environment variable "{key}" is locked. Unlock it before modifying.'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                value = str(row.get('value', '') or '')
                has_explicit_unmasked_value = (
                    'value' in row
                    and row.get('value') is not None
                    and not _looks_masked_secret(value)
                )
                if existing and _must_mask_env_value(existing.key, existing.value, existing.is_secret) and _looks_masked_secret(value):
                    value = existing.value
                else:
                    value = _scrub_user_value(value, key)

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
                    if existing and existing.is_secret and not is_secret and not (has_explicit_unmasked_value and value):
                        return Response(
                            {'error': f'Cannot unmask secret "{key}" (is_secret=False) without supplying a new value.'},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
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
        if existing and existing.is_locked:
            return Response(
                {'error': f'Environment variable "{key}" is locked. Unlock it before modifying.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        value = str(request.data.get('value', '') or '')
        has_explicit_unmasked_value = (
            'value' in request.data
            and request.data.get('value') is not None
            and not _looks_masked_secret(value)
        )
        if existing and _must_mask_env_value(existing.key, existing.value, existing.is_secret) and _looks_masked_secret(value):
            value = existing.value
        else:
            value = _scrub_user_value(value, key)
        if _is_ciphertext(value):
            return Response(
                {'value': ['Cannot save Fernet ciphertext as value. Sender must decrypt before sending.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'is_secret' in request.data:
            is_secret = _parse_bool(request.data.get('is_secret'))
            if existing and existing.is_secret and not is_secret and not (has_explicit_unmasked_value and value):
                return Response(
                    {'is_secret': ['Cannot flip is_secret from True to False without supplying a new value.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
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

        reveal_secrets = False
        if request.method.upper() == 'GET':
            auth_obj = getattr(request, 'auth', None)
            token_allows_write = True
            if auth_obj is not None and hasattr(auth_obj, 'has_scope'):
                token_allows_write = bool(auth_obj.has_scope('write'))
            reveal_secrets = (
                user_can_write(request.user, service)
                and token_allows_write
            )
            return Response(
                EnvVarSerializer(
                    var,
                    context={'request': request, 'reveal_secrets': reveal_secrets},
                ).data
            )
        assert_can_write(self.request.user, service)
        if request.method.upper() == 'DELETE':
            if var.is_locked:
                return Response(
                    {'error': 'Environment variable is locked. Unlock it before deleting.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            var.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH — toggle is_locked (or update fields)
        requested_is_locked = _parse_bool(request.data['is_locked']) if 'is_locked' in request.data else var.is_locked
        if var.is_locked and requested_is_locked:
            return Response(
                {'error': 'Environment variable is locked. Unlock it (is_locked=false) before modifying.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if 'is_locked' in request.data:
            var.is_locked = requested_is_locked
        if 'value' in request.data:
            raw_val = str(request.data.get('value', '') or '')
            if _looks_masked_secret(raw_val) and _must_mask_env_value(var.key, var.value, var.is_secret):
                new_val = var.value
                has_new_val = False
            else:
                new_val = _scrub_user_value(raw_val, var.key)
                has_new_val = bool(new_val)
            if _is_ciphertext(new_val):
                return Response(
                    {'value': ['Cannot save Fernet ciphertext as value. Sender must decrypt before sending.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            new_val = var.value
            has_new_val = False

        if 'is_secret' in request.data:
            new_is_secret = _parse_bool(request.data['is_secret'])
            if var.is_secret and not new_is_secret and not has_new_val:
                return Response(
                    {'is_secret': ['Cannot flip is_secret from True to False without supplying a new value.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            var.is_secret = new_is_secret
        if 'value' in request.data and has_new_val:
            var.value = new_val
        var.save()
        return Response(
            EnvVarSerializer(
                var,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            ).data
        )
