"""drf-spectacular extensions for deployment auth classes."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class APITokenAuthenticationScheme(OpenApiAuthenticationExtension):
    """Document APITokenAuthentication as HTTP Bearer auth."""

    target_class = "apps.deployments.api_token_auth.APITokenAuthentication"
    name = "APITokenAuth"
    match_subclasses = True

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "smsly_<token>",
            "description": "SMSLY personal API token, prefixed with smsly_.",
        }
