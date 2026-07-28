import logging
import re
import secrets

from .constants import generate_strong_secret

logger = logging.getLogger(__name__)


class SecretsMixin:
    def _resolve_cross_service_secret(self, var_name: str) -> str | None:
        for entry in self.secrets_manifest.get("expects_from", []):
            if isinstance(entry, dict):
                for local_var in entry:
                    if local_var == var_name:
                        if self.cross_service_map:
                            paired = self._lookup_paired_secret(var_name, entry[local_var])
                            if paired:
                                return paired
                        return generate_strong_secret(48)
            elif isinstance(entry, str) and "→" in entry:
                parts = entry.split("→")
                if parts[0].strip() == var_name:
                    return generate_strong_secret(48)
        return None

    def _lookup_paired_secret(self, local_var: str, mapping: str) -> str | None:
        match = re.search(r"\(([^)]+)\)", str(mapping))
        if match:
            remote_var = match.group(1)
            for svc_data in (self.cross_service_map.get("resolved") or {}).values():
                if remote_var in svc_data:
                    return svc_data[remote_var]
        return generate_strong_secret(48)

    def _generate_mock_for_var(self, var_name: str) -> str | None:
        if any(p in var_name for p in ("ALLOWED_IPS", "GATEWAY_IPS", "TRUSTED_IPS", "WHITELIST", "TRUSTED_PROXIES", "TRUSTED_NETWORKS")):
            return "0.0.0.0/0"

        if "ACCOUNT_SID" in var_name or var_name.endswith("_SID"):
            return "AC" + secrets.token_hex(16)

        if "PHONE_NUMBER" in var_name or "FROM_NUMBER" in var_name:
            return "+15005550006"

        if "AUTH_TOKEN" in var_name:
            return secrets.token_hex(32)

        if var_name.endswith("_ENDPOINT_URL") or var_name.endswith("_ENDPOINT"):
            return "http://minio:9000"

        if var_name.endswith("_REGION"):
            return "us-east-1"

        if "_API_URL" in var_name and any(p in var_name for p in ("STRIPE", "COINBASE", "PAYPAL")):
            return "https://api.mock-provider.local"

        if "SENTRY_DSN" in var_name or var_name.endswith("_DSN"):
            return ""

        if "DEFAULT_FROM_EMAIL" in var_name and not self.env_example_vars.get(var_name):
            return f"noreply@{self.service_name.replace('smsly-', '')}.smsly.local"

        if "EMAIL_HOST_USER" in var_name and not self.env_example_vars.get(var_name):
            return "mock@localhost"

        if "EMAIL_HOST" in var_name and not self.env_example_vars.get(var_name):
            return "smtp.mock.local"

        if var_name.endswith("_URL") and not self.env_example_vars.get(var_name):
            scheme = "https" if "SECURE" in var_name or "GATEWAY" in var_name else "http"
            return f"{scheme}://localhost:{self.port}"

        if "PUBLISHABLE_KEY" in var_name or "PUBLIC_KEY" in var_name:
            return f"pk_mock_{secrets.token_hex(8)}"

        if var_name.endswith("_KEY_ID") or var_name.endswith("_ACCESS_KEY"):
            return secrets.token_hex(20)

        if "ADMIN_URL" in var_name and not self.env_example_vars.get(var_name):
            return "admin/"

        if "JWT_ISSUER" in var_name:
            return self.service_name

        if "JWT_AUDIENCE" in var_name:
            return "smsly-services"

        if var_name.endswith("_DIR") or var_name.endswith("_PATH"):
            return "/var/log"

        if var_name in ("APP_NAME", "PROJECT_NAME") and not self.env_example_vars.get(var_name):
            return self.service_name.replace("smsly-", "")

        if "SERVICE_VERSION" in var_name or var_name == "VERSION":
            return "1.0.0"

        if var_name.startswith("GF_"):
            return generate_strong_secret(24)

        if "STRIPE_SECRET_KEY" in var_name:
            return f"sk_test_mock_{secrets.token_hex(16)}"

        logger.warning(
            "No mock strategy for var %s in service %s; generating generic mock",
            var_name, self.service_name,
        )
        return ""

    @staticmethod
    def generate_placeholder_for_external(var_name: str) -> str:
        name = var_name.upper()

        if "MODEL" in name and any(p in name for p in ("ALIBABA", "OPENAI", "ANTHROPIC", "GEMINI", "CLAUDE", "GPT", "LLM", "AI_")):
            return "REPLACE_ME__ai-model-name"
        if "MODEL" in name:
            return "REPLACE_ME__model-name"

        if "CLOUDFLARE" in name and "ACCOUNT" in name:
            return "REPLACE_ME__cloudflare-account-id"
        if "CLOUDFLARE" in name and "ZONE" in name:
            return "REPLACE_ME__cloudflare-zone-id"
        if "CLOUDFLARE" in name:
            return "REPLACE_ME__cloudflare-value"
        if "AWS_ACCOUNT" in name:
            return "REPLACE_ME__aws-account-id"
        if "GCP_PROJECT" in name or "GOOGLE_PROJECT" in name:
            return "REPLACE_ME__gcp-project-id"
        if "AZURE_SUBSCRIPTION" in name:
            return "REPLACE_ME__azure-subscription-id"

        if "PAYPAL" in name and ("CLIENT" in name or "APP" in name):
            return "REPLACE_ME__paypal-client-id"
        if "PAYPAL" in name and "WEBHOOK" in name:
            return "REPLACE_ME__paypal-webhook-id"
        if "PAYPAL" in name:
            return "REPLACE_ME__paypal-value"
        if "STRIPE" in name and "PUBLISHABLE" in name:
            return "pk_test_REPLACE_ME"
        if "STRIPE" in name and "WEBHOOK" in name:
            return "whsec_REPLACE_ME"
        if "STRIPE" in name:
            return "sk_test_REPLACE_ME"
        if "COINBASE" in name:
            return "REPLACE_ME__coinbase-api-key"

        if "TWILIO" in name and "WHATSAPP" in name and "FROM" in name:
            return "whatsapp:+15005550006"
        if "TWILIO" in name and "FROM" in name:
            return "+15005550006"
        if "TWILIO" in name and "SID" in name:
            return "AC" + "0" * 32
        if "TWILIO" in name:
            return "REPLACE_ME__twilio-value"
        if "VONAGE" in name or "NEXMO" in name:
            return "REPLACE_ME__vonage-api-key"
        if "SENDGRID" in name:
            return "SG.REPLACE_ME"
        if "MAILGUN" in name:
            return "REPLACE_ME__mailgun-api-key"
        if "POSTMARK" in name:
            return "REPLACE_ME__postmark-api-token"

        if "GITHUB" in name and "CLIENT" in name:
            return "REPLACE_ME__github-client-id"
        if "GOOGLE" in name and "CLIENT" in name:
            return "REPLACE_ME__google-client-id"
        if "FACEBOOK" in name or "META" in name:
            return "REPLACE_ME__meta-app-id"
        if "LINKEDIN" in name:
            return "REPLACE_ME__linkedin-client-id"
        if "TWITTER" in name or "X_API" in name:
            return "REPLACE_ME__twitter-api-key"

        if name.endswith("_ID") or name.endswith("_ACCOUNT_ID"):
            return f"REPLACE_ME__{var_name.lower().replace('_', '-')}"
        if name.endswith("_SECRET") or name.endswith("_KEY") or name.endswith("_TOKEN"):
            return f"REPLACE_ME__{var_name.lower().replace('_', '-')}"
        if "WEBHOOK" in name:
            return f"REPLACE_ME__{var_name.lower().replace('_', '-')}"

        return f"REPLACE_ME__{var_name.lower().replace('_', '-')}"
