import logging
import os
import re
import shutil
import socket
import ssl
import time
from datetime import UTC, datetime

from celery import current_app
from django.conf import settings
from django.core.cache import cache
from django.db import connections

logger = logging.getLogger(__name__)

class HealthCheckService:
    """Service to run comprehensive health checks across the PaaS infrastructure."""

    @classmethod
    def _scrub_error(cls, error_msg):
        # Prevent leaking secrets in health check responses
        msg = str(error_msg)
        msg = re.sub(r'://.*?:.*?@', '://***:***@', msg) # basic auth scrub
        msg = re.sub(r'password=.*?\s', 'password=*** ', msg)
        return msg

    @classmethod
    def run_all_checks(cls):
        checks = {
            "api": {"ok": True},
            "database": cls.check_database("default"),
            "redis": cls.check_redis(),
            "rabbitmq": cls.check_rabbitmq(),
            "celery": cls.check_celery(),
            "disk": cls.check_disk(),
            "ssl": cls.check_ssl(),
            "dns": cls.check_dns()
        }

        if getattr(settings, "DIRECT_DATABASE_URL", None) or os.getenv("DIRECT_DATABASE_URL"):
            if "direct" in connections:
                 checks["direct_postgres"] = cls.check_database("direct")

        all_ok = all(check.get("ok", False) for check in checks.values())
        return {
            "ok": all_ok,
            "status": "healthy" if all_ok else "unhealthy",
            "checks": checks
        }

    @classmethod
    def check_database(cls, alias="default"):
        start = time.time()
        try:
            with connections[alias].cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            latency = int((time.time() - start) * 1000)
            return {"ok": True, "latency_ms": latency}
        except Exception as e:
            logger.error(f"Database health check failed for {alias}: {e}")
            return {"ok": False, "error": cls._scrub_error(e)}

    @classmethod
    def check_redis(cls):
        start = time.time()
        try:
            cache.set('_health_ping', 'pong', timeout=5)
            val = cache.get('_health_ping')
            if val != 'pong':
                raise ValueError("Cache read/write mismatch")
            latency = int((time.time() - start) * 1000)
            return {"ok": True, "latency_ms": latency}
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return {"ok": False, "error": cls._scrub_error(e)}

    @classmethod
    def check_rabbitmq(cls):
        start = time.time()
        try:
            with current_app.connection() as conn:
                conn.connect()
            latency = int((time.time() - start) * 1000)
            return {"ok": True, "latency_ms": latency}
        except Exception as e:
            logger.error(f"RabbitMQ health check failed: {e}")
            return {"ok": False, "error": cls._scrub_error(e)}

    @classmethod
    def check_celery(cls):
        try:
            i = current_app.control.inspect(timeout=1.0)
            pings = i.ping()
            if not pings:
                 return {"ok": False, "error": "No workers responded to ping"}
            workers_count = len(pings)
            return {"ok": True, "workers": workers_count}
        except Exception as e:
            logger.error(f"Celery health check failed: {e}")
            return {"ok": False, "error": cls._scrub_error(e)}

    @classmethod
    def check_disk(cls):
        try:
            path = "/"
            total, _used, free = shutil.disk_usage(path)
            free_percent = int((free / total) * 100)
            if free_percent < 5:
                 return {"ok": False, "error": "Disk space critically low (<5%)", "free_percent": free_percent}
            return {"ok": True, "free_percent": free_percent}
        except Exception as e:
            return {"ok": False, "error": cls._scrub_error(e)}

    @classmethod
    def check_ssl(cls):
        """Check SSL certificate expiry for all configured domains."""
        try:
            domains = getattr(settings, 'SSL_CHECK_DOMAINS', [])
            if not domains:
                # Fallback: read from Caddy config or env
                domain_str = os.environ.get('SSL_CHECK_DOMAINS', '')
                domains = [d.strip() for d in domain_str.split(',') if d.strip()]
            if not domains:
                return {"ok": True, "skipped": True, "reason": "No domains configured"}

            results = []
            for domain in domains:
                try:
                    ctx = ssl.create_default_context()
                    with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
                        s.settimeout(5)
                        s.connect((domain, 443))
                        cert = s.getpeercert()
                        expiry_str = cert.get('notAfter', '')
                        if expiry_str:
                            expiry = datetime.strptime(expiry_str, '%b %d %H:%M:%S %Y %Z')
                            expiry = expiry.replace(tzinfo=UTC)
                            days_left = (expiry - datetime.now(UTC)).days
                            ok = days_left > 7
                            results.append({
                                "domain": domain,
                                "ok": ok,
                                "expires_in_days": days_left,
                            })
                        else:
                            results.append({"domain": domain, "ok": True, "expires_in_days": None})
                except Exception as e:
                    results.append({"domain": domain, "ok": False, "error": str(e)})

            all_ok = all(r.get("ok", False) for r in results)
            return {"ok": all_ok, "certificates": results}
        except Exception as e:
            return {"ok": False, "error": cls._scrub_error(e)}

    @classmethod
    def check_dns(cls):
        """Check DNS resolution for critical domains."""
        try:
            domains = getattr(settings, 'DNS_CHECK_DOMAINS', [])
            if not domains:
                domain_str = os.environ.get('DNS_CHECK_DOMAINS', '')
                domains = [d.strip() for d in domain_str.split(',') if d.strip()]
            if not domains:
                return {"ok": True, "skipped": True, "reason": "No domains configured"}

            results = []
            for domain in domains:
                try:
                    start = time.time()
                    socket.getaddrinfo(domain, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
                    latency_ms = int((time.time() - start) * 1000)
                    results.append({"domain": domain, "ok": True, "latency_ms": latency_ms})
                except socket.gaierror as e:
                    results.append({"domain": domain, "ok": False, "error": f"DNS resolution failed: {e}"})

            all_ok = all(r.get("ok", False) for r in results)
            return {"ok": all_ok, "resolutions": results}
        except Exception as e:
            return {"ok": False, "error": cls._scrub_error(e)}
