import logging
import socket

logger = logging.getLogger(__name__)

class DNSVerifier:
    """Provider-agnostic DNS verification."""

    @classmethod
    def verify_a_record(cls, domain: str, expected_ip: str) -> dict:
        """
        Validates global DNS resolution.
        """
        try:
            resolved_ips = socket.gethostbyname_ex(domain)[2]
            if expected_ip in resolved_ips:
                return {"ok": True, "status": "verified", "resolved": resolved_ips}
            return {
                "ok": False,
                "error": {
                    "code": "DNS_NOT_POINTING_TO_SERVER",
                    "message": "The domain does not point to this server yet. Update DNS records and try again.",
                    "details": {
                        "domain": domain,
                        "expected_ip": expected_ip,
                        "resolved_ip": resolved_ips[0] if resolved_ips else None
                    }
                }
            }
        except socket.gaierror:
            return {
                "ok": False,
                "error": {
                    "code": "DNS_RESOLUTION_FAILED",
                    "message": "Domain failed to resolve.",
                    "details": {"domain": domain}
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "DNS_CHECK_ERROR",
                    "message": f"Unexpected error during DNS resolution: {e}",
                    "details": {"domain": domain}
                }
            }
