import logging

import requests

from .client import _safe_error_snippet

logger = logging.getLogger(__name__)


class ErrorHandlingMixin:
    def _response_error(self, fallback: str, response: requests.Response | None = None) -> str:
        if self.last_error:
            return self.last_error
        if response is not None:
            return (
                f"{fallback}: HTTP {response.status_code}. "
                f"{_safe_error_snippet(getattr(response, 'text', ''))}"
            ).strip()
        return fallback

    @staticmethod
    def _classify_404_response(response) -> str:
        body = (getattr(response, 'text', '') or '').strip()
        body_lower = body.lower()
        if body_lower == '404 page not found':
            return 'traefik_no_router'
        if '"detail"' in body_lower and '"not found' in body_lower:
            return 'django_not_found'
        if '<html' in body_lower or '<!doctype' in body_lower:
            return 'proxy_html_404'
        return 'unknown_404'

    _404_DIAGNOSIS_MESSAGES = {
        'traefik_no_router': (
            'Traefik is running on the remote node but no router matched '
            '/api/v1/. The backend container is most likely down, not on '
            'the smsly-net Docker network, or its Traefik labels are missing.'
        ),
        'django_not_found': (
            'The remote Django API is reachable but returned a 404 for this '
            'endpoint. This may indicate a version mismatch between the '
            'controller and agent codebases.'
        ),
        'proxy_html_404': (
            'A reverse proxy (Nginx/Caddy) on the remote node returned an '
            'HTML 404 page. The proxy may be misconfigured or the backend '
            'upstream is unreachable.'
        ),
        'unknown_404': 'The remote node returned an unrecognised 404 response.',
    }

    @staticmethod
    def _classify_400_response(response) -> str:
        body = (getattr(response, 'text', '') or '').strip()
        body_lower = body.lower()
        if '<html' in body_lower or '<!doctype' in body_lower:
            if 'bad request' in body_lower:
                return 'tls_mismatch'
            return 'proxy_html_400'
        if '400 bad request' in body_lower:
            return 'tls_mismatch'
        if body_lower.startswith('400'):
            return 'traefik_bad_request'
        return 'unknown_400'

    _400_DIAGNOSIS_MESSAGES = {
        'tls_mismatch': (
            'An HTTPS request was sent to an HTTP-only service. This happens '
            'when the orchestrator tries TLS on a node that only has Traefik '
            '(no Caddy). The wg_address or api_url should use HTTP.'
        ),
        'traefik_bad_request': (
            'Traefik returned a 400 Bad Request, likely due to a malformed '
            'Host header or an unsupported request. The backend may be down '
            'or the Traefik routing configuration is incorrect.'
        ),
        'proxy_html_400': (
            'A reverse proxy on the remote node returned an HTML 400 page. '
            'The proxy may be misconfigured or the backend upstream is '
            'unreachable.'
        ),
        'unknown_400': 'The remote node returned an unrecognised 400 response.',
    }

    def _enrich_404_error(self, response, base_url: str):
        classification = self._classify_404_response(response)
        diagnosis = self._404_DIAGNOSIS_MESSAGES.get(classification, '')
        self._set_last_error(
            f"Remote API returned HTTP 404 at {base_url}. "
            f"Diagnosis ({classification}): {diagnosis}",
            response=response,
        )

    def _parse_json_response(self, response: requests.Response, context: str):
        try:
            return response.json()
        except ValueError:
            self._set_last_error(
                f"Remote API returned non-JSON response while {context}.",
                response=response,
            )
            logger.error(self.last_error)
            return None
