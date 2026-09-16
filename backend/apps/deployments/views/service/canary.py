"""Traffic-split (weighted canary) actions for the service viewset."""
import logging
import os
import re
import time

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.teams.permissions import assert_can_write

logger = logging.getLogger(__name__)

# Loki instant-query windows accepted by canary-metrics (?window=30m).
CANARY_METRIC_WINDOWS = {
    '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '6h': 21600, '24h': 86400,
}
CANARY_DEFAULT_WINDOW = '30m'

# Verdict thresholds (compare staging vs live error share).
CANARY_MIN_SAMPLES = 20
CANARY_ERR_PP_WARN = 2.0
CANARY_ERR_ABS_BLOCK = 10.0
CANARY_ERR_RATIO_BLOCK = 3.0

_SERVICE_LABEL_RE = re.compile(r"[^A-Za-z0-9_.-]")


class CanaryMetricsThrottle(UserRateThrottle):
    scope = 'observability'
    rate = '30/minute'


def split_weights(strategy, percentage) -> tuple[int, int]:
    """(live_weight, staging_weight); (100, 0) when no split is active."""
    try:
        pct = int(percentage or 0)
    except (TypeError, ValueError):
        pct = 0
    if str(strategy or "").upper() == "CANARY" and 1 <= pct <= 100:
        return 100 - pct, pct
    return 100, 0


def green_state(service) -> dict:
    """Green-container presence/health for the active STAGED deployment.

    Total: any failure yields present=False (never raises — status
    endpoints must not 500 on docker/DB blips).
    """
    result = {"present": False, "healthy": None, "deployment_id": None, "commit_hash": None}
    try:
        from apps.deployments.models import Deployment

        deployment = (
            Deployment.objects.filter(
                service=service,
                status__in=(Deployment.Status.STAGED, Deployment.Status.HEALTH_CHECK),
            )
            .exclude(green_container_id__isnull=True)
            .exclude(green_container_id="")
            .order_by("-created_at")
            .first()
        )
        if deployment is None:
            return result
        result["deployment_id"] = str(getattr(deployment, "id", ""))
        result["commit_hash"] = str(getattr(deployment, "commit_hash", "") or "")
        green_id = str(getattr(deployment, "green_container_id", "") or "").strip()
        if not green_id:
            return result
        result["present"] = True
        import docker

        green = docker.from_env().containers.get(green_id)
        green.reload()
        state = green.attrs.get("State", {}) or {}
        status_s = str(state.get("Status") or "").lower()
        health = str((state.get("Health", {}) or {}).get("Status") or "").lower()
        result["healthy"] = status_s == "running" and health in ("healthy", "")
    except Exception as exc:
        logger.debug("traffic-split green lookup failed: %s", exc)
    return result


def build_traffic_split_status(service, green=None) -> dict:
    """Pure status payload (green injectable for tests)."""
    live_w, staging_w = split_weights(
        getattr(service, "deploy_strategy", "ROLLING"),
        getattr(service, "canary_percentage", 0),
    )
    if green is None:
        green = green_state(service)
    try:
        from apps.deployments.services.safedeploy.promotion_guard import (
            get_promotion_policy,
        )

        policy = get_promotion_policy(service)
        get_only = bool(policy.get("canary_get_only", False))
        sticky = bool(policy.get("canary_sticky", False))
    except Exception:
        get_only = False
        sticky = False
    try:
        from apps.deployments.services.traefik_manager.canary_file import (
            canary_file_path,
        )

        file_present = os.path.exists(canary_file_path(service))
    except Exception:
        file_present = False
    return {
        "service_id": str(getattr(service, "id", "")),
        "deploy_strategy": getattr(service, "deploy_strategy", "ROLLING"),
        "canary_percentage": getattr(service, "canary_percentage", 0),
        "live_weight": live_w,
        "staging_weight": staging_w,
        # Configured = operator intent; active = Traefik is splitting now
        # (file present + green live). They differ while a green is
        # missing or the file was removed out-of-band.
        "split_configured": staging_w > 0,
        "split_active": staging_w > 0 and bool(green.get("present")) and file_present,
        "file_present": file_present,
        "get_only": get_only,
        "sticky": sticky,
        "green": green,
    }


_HOST_SANITIZE_RE = re.compile(r"[^a-z0-9.-]")


def _loki_instant(query: str) -> list:
    """Run a Loki instant (metric) query; returns the result vector."""
    from apps.core.views.observability import LOKI_INTERNAL_URL, PROXY_TIMEOUT

    import requests

    resp = requests.get(
        f"{LOKI_INTERNAL_URL}/loki/api/v1/query",
        params={"query": query, "time": int(time.time() * 1e9)},
        timeout=PROXY_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("data", {}).get("result", [])


def service_hostnames(service) -> list[str]:
    """Routable hostnames for LogQL scoping (tenant-safe: own service only)."""
    hosts: list[str] = []
    customs = getattr(service, "custom_domains", None) or []
    if not isinstance(customs, list):
        customs = []
    for raw in [getattr(service, "public_domain", None)] + customs:
        host = _HOST_SANITIZE_RE.sub("", str(raw or "").strip().lower())
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def split_backend_ips(service) -> tuple[set, set]:
    """(live_ips, green_ips) across all container networks. Never raises."""
    live_ips: set = set()
    green_ips: set = set()

    def ips_of(container) -> set:
        try:
            container.reload()
            nets = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
            return {
                str((data or {}).get("IPAddress") or "")
                for data in nets.values()
            } - {""}
        except Exception:
            return set()

    try:
        import docker

        from apps.deployments.models import Deployment

        client = docker.from_env()
        live_name = str(getattr(service, "name", "") or "").strip()
        if live_name:
            try:
                live_ips = ips_of(client.containers.get(live_name))
            except Exception:
                pass
        deployment = (
            Deployment.objects.filter(
                service=service,
                status__in=(Deployment.Status.STAGED, Deployment.Status.HEALTH_CHECK),
            )
            .exclude(green_container_id__isnull=True)
            .exclude(green_container_id="")
            .order_by("-created_at")
            .first()
        )
        green_id = str(getattr(deployment, "green_container_id", "") or "").strip() if deployment else ""
        if green_id:
            try:
                green_ips = ips_of(client.containers.get(green_id))
            except Exception:
                pass
    except Exception as exc:
        logger.debug("split backend IP lookup failed: %s", exc)
    return live_ips, green_ips


def variant_addr_map(service, port: int) -> dict:
    """Map Traefik ServiceAddr (ip:port) to live/staging variants."""
    live_ips, green_ips = split_backend_ips(service)
    addr_map: dict = {}
    for ip in live_ips:
        addr_map[f"{ip}:{port}"] = "live"
        addr_map[ip] = "live"
    for ip in green_ips:
        addr_map[f"{ip}:{port}"] = "staging"
        addr_map[ip] = "staging"
    return addr_map


def aggregate_canary_counts(vector: list, window_s: int, addr_map: dict) -> dict:
    """Fold a `sum by (service_addr, status)` vector into per-variant stats.

    The stream label is Traefik's ``ServiceAddr`` (backend ip:port that
    served the request); ``upstream`` is accepted as a legacy fallback.
    Unknown addresses land in `unattributed` (log lines from before the
    split, or backends that no longer resolve). Pure — unit tested.
    """
    buckets: dict[str, dict] = {}

    def bucket(key: str) -> dict:
        return buckets.setdefault(key, {"upstream": None, "count": 0, "errors": 0})

    for series in vector:
        stream = series.get("stream", {}) or {}
        addr = str(stream.get("service_addr", "") or stream.get("upstream", "") or "")
        code = str(stream.get("status", "") or "")
        try:
            count = float(series.get("value", [0, "0"])[1])
        except (TypeError, ValueError, IndexError):
            continue
        variant = addr_map.get(addr, "unattributed")
        b = bucket(variant)
        if b["upstream"] is None:
            b["upstream"] = addr or None
        b["count"] += count
        if code.startswith("5"):
            b["errors"] += count
    out = {}
    for variant in ("live", "staging", "unattributed"):
        b = buckets.get(variant, {"upstream": None, "count": 0, "errors": 0})
        total = b["count"]
        out[variant] = {
            "upstream": b["upstream"],
            "count": int(total),
            "rps": round(total / window_s, 3) if window_s else 0,
            "err_rate": round(100.0 * b["errors"] / total, 2) if total else 0.0,
            "p50_ms": None,
            "p95_ms": None,
        }
    return out


def percentile(sorted_values: list, pct: float) -> float | None:
    """Nearest-rank percentile over pre-sorted values. Pure — unit tested."""
    if not sorted_values:
        return None
    if pct <= 0:
        return float(sorted_values[0])
    if pct >= 100:
        return float(sorted_values[-1])
    import math

    rank = math.ceil((pct / 100.0) * len(sorted_values))
    return float(sorted_values[max(0, min(rank - 1, len(sorted_values) - 1))])


def parse_duration_ms(line: str) -> float | None:
    """Extract Traefik `Duration` (nanoseconds) from a JSON log line → ms.

    Returns None when the line isn't JSON or carries no numeric Duration.
    Pure — unit tested.
    """
    try:
        import json

        payload = json.loads(line)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("Duration", payload.get("duration", None))
    try:
        ns = float(raw)
    except (TypeError, ValueError):
        return None
    if ns < 0:
        return None
    return ns / 1e6


def attach_latency(variants: dict, samples: dict) -> dict:
    """Attach p50/p95 (ms) per variant from {variant: [ms, ...]}. Pure."""
    for variant in ("live", "staging", "unattributed"):
        values = sorted(samples.get(variant, []) or [])
        variants[variant]["p50_ms"] = (
            round(percentile(values, 50), 2) if values else None
        )
        variants[variant]["p95_ms"] = (
            round(percentile(values, 95), 2) if values else None
        )
    return variants


def _loki_log_sample(query: str, limit: int = 500) -> list:
    """Fetch raw log lines (query_range) for latency sampling."""
    from apps.core.views.observability import LOKI_INTERNAL_URL, PROXY_TIMEOUT

    import requests

    resp = requests.get(
        f"{LOKI_INTERNAL_URL}/loki/api/v1/query_range",
        params={"query": query, "limit": max(1, min(limit, 1000))},
        timeout=PROXY_TIMEOUT,
    )
    resp.raise_for_status()
    streams = resp.json().get("data", {}).get("result", [])
    lines = []
    for stream in streams:
        for _ts, line in stream.get("values", []):
            lines.append(line)
            if len(lines) >= limit:
                return lines
    return lines


CANARY_LATENCY_SAMPLE_LIMIT = 500


def canary_verdict(variants: dict) -> tuple[str, list[str]]:
    """NEUTRAL/WARN/BLOCK from live-vs-staging error shares. Pure."""
    live = variants.get("live", {})
    staging = variants.get("staging", {})
    live_n = live.get("count", 0)
    staging_n = staging.get("count", 0)
    if staging_n < CANARY_MIN_SAMPLES:
        return "NEUTRAL", [
            f"Only {staging_n} staging requests in window "
            f"(need {CANARY_MIN_SAMPLES}) — no verdict yet."
        ]
    live_err = live.get("err_rate", 0.0)
    staging_err = staging.get("err_rate", 0.0)
    delta_pp = staging_err - live_err
    ratio = (staging_err / live_err) if live_err > 0 else (float("inf") if staging_err > 0 else 1.0)
    if staging_err >= CANARY_ERR_ABS_BLOCK or (
        live_n >= CANARY_MIN_SAMPLES and ratio >= CANARY_ERR_RATIO_BLOCK and delta_pp > 0
    ):
        return "BLOCK", [
            f"Staging error rate {staging_err}% vs live {live_err}% — abort the split."
        ]
    if delta_pp >= CANARY_ERR_PP_WARN:
        return "WARN", [
            f"Staging error rate {staging_err}% is +{round(delta_pp, 2)}pp vs live "
            f"{live_err}% — investigate before ramping up."
        ]
    return "NEUTRAL", [
        f"Staging {staging_err}% vs live {live_err}% over {staging_n} staging requests."
    ]


class TrafficSplitMixin:
    """Traffic-split actions for the service viewset."""

    # NOTE: GET and POST MUST stay on a single @action. DRF collects
    # actions via inspect.getmembers() (sorted by name), so two actions
    # with the same url_path register in name order and Django serves
    # only the first — POSTs then 405 against the GET-only pattern
    # (same trap documented in meta.py for env_vars).
    @action(detail=True, methods=["get", "post"], url_path="traffic-split")
    def traffic_split(self, request, pk=None):
        """Read (GET) or set (POST) the staging weight (0-100).

        GET → build_traffic_split_status(service).
        POST /api/v1/services/{id}/traffic-split/ {"canary_percentage": 25}
        validates through ServiceSerializer (expand/contract gate) WITHOUT
        saving, writes the Traefik file-provider WRR config (hot-applied,
        no container recreates), then persists. A failed write leaves the
        row untouched. Caddy is not involved (single traefik:80 endpoint).
        """
        if request.method == "GET":
            service = self.get_object()
            return Response(build_traffic_split_status(service))
        return self.set_traffic_split(request, pk=pk)

    def set_traffic_split(self, request, pk=None):
        service = self.get_object()
        assert_can_write(self.request.user, service)
        try:
            pct = int(request.data.get("canary_percentage", 0))
        except (TypeError, ValueError):
            return Response(
                {"error": "canary_percentage must be an integer 0-100."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not 0 <= pct <= 100:
            return Response(
                {"error": "canary_percentage must be between 0 and 100."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if pct == 0:
            return self.abort_traffic_split(request, pk=pk)

        from ...serializers import ServiceSerializer
        from apps.deployments.services.traefik_manager.canary_file import (
            CanaryFileError,
            write_canary_file,
        )

        serializer = ServiceSerializer(
            service,
            data={"deploy_strategy": "CANARY", "canary_percentage": pct},
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        try:
            file_info = write_canary_file(service, 100 - pct, pct)
        except CanaryFileError as exc:
            return Response(
                {"error": f"Traffic split refused: {exc}"},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception:
            logger.exception("traffic-split file write failed")
            return Response(
                {"error": "Traffic split failed while applying weights."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            serializer.save()
        except Exception:
            # Row and file must never disagree about an active split:
            # a failed save with a live file would split traffic
            # invisibly. Remove the file, then surface the error.
            logger.exception("traffic-split save failed — removing just-written file")
            try:
                from apps.deployments.services.traefik_manager.canary_file import (
                    remove_canary_file,
                )
                remove_canary_file(service)
            except Exception:
                pass
            return Response(
                {"error": "Traffic split failed while saving."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        # Opportunistic orphan cleanup for OTHER services (own file was
        # just written, so this never touches the active split).
        try:
            from apps.deployments.services.traefik_manager.canary_file import (
                sync_canary_files,
            )
            sync_canary_files()
        except Exception as exc:
            logger.debug("traffic-split orphan sync failed: %s", exc)
        service.refresh_from_db()
        payload = build_traffic_split_status(service)
        payload["traefik_applied"] = True
        payload["router"] = file_info.get("router")
        return Response(payload)

    @action(detail=True, methods=["post"], url_path="traffic-split/abort")
    def abort_traffic_split(self, request, pk=None):
        """Kill switch: remove the WRR file (instant restore of plain
        routing) and reset weight to 0. Never blocked — abort must
        always work."""
        service = self.get_object()
        assert_can_write(self.request.user, service)
        from apps.deployments.services.traefik_manager.canary_file import (
            remove_canary_file,
        )

        remove_canary_file(service)
        service.canary_percentage = 0
        service.save(update_fields=["canary_percentage", "updated_at"])
        payload = build_traffic_split_status(service)
        payload["traefik_applied"] = True
        return Response(payload)

    @action(
        detail=True, methods=["get"], url_path="canary-metrics",
        throttle_classes=[CanaryMetricsThrottle],
    )
    def canary_metrics(self, request, pk=None):
        """Live-vs-staging comparison over a window (?window=5m..24h).

        Attribution comes from Traefik access logs: each entry's
        ``ServiceAddr`` (backend ip:port that served it) is mapped to
        live/staging via current container inspection. The LogQL is built
        server-side from the service's own hostnames — no user-supplied
        query, injection-safe by construction.
        """
        service = self.get_object()
        window = str(request.GET.get("window", CANARY_DEFAULT_WINDOW))
        window_s = CANARY_METRIC_WINDOWS.get(window)
        if not window_s:
            return Response(
                {"error": f"window must be one of {sorted(CANARY_METRIC_WINDOWS)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        hosts = service_hostnames(service)
        if not hosts:
            variants = aggregate_canary_counts([], window_s, {})
            verdict, reasons = canary_verdict(variants)
            return Response({
                "service_id": str(getattr(service, "id", "")),
                "window": window,
                "live_weight": 100,
                "staging_weight": 0,
                "split_active": False,
                "live": variants["live"],
                "staging": variants["staging"],
                "unattributed": variants["unattributed"],
                "verdict": verdict,
                "reasons": ["Service has no routable hostnames."] + reasons,
            })
        host_re = "|".join(h.replace(".", "\\.") for h in hosts)
        query = (
            "sum by (service_addr, status) (count_over_time("
            f'{{job="traefik-access"}} | json | RequestHost=~"{host_re}" [{window}]))'
        )
        try:
            vector = _loki_instant(query)
        except Exception:
            logger.exception("canary-metrics Loki query failed")
            return Response(
                {"error": "Metrics backend unreachable."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        try:
            port = int(getattr(service, "internal_port", None) or 8000)
        except (TypeError, ValueError):
            port = 8000
        addr_map = variant_addr_map(service, port)
        variants = aggregate_canary_counts(vector, window_s, addr_map)
        # Latency sampling (best-effort, never fails the endpoint): parse
        # Traefik Duration (ns → ms) from a bounded sample of raw log
        # lines and attribute each line via its ServiceAddr.
        try:
            sample_query = (
                f'{{job="traefik-access"}} | json | RequestHost=~"{host_re}"'
            )
            samples: dict = {"live": [], "staging": [], "unattributed": []}
            for line in _loki_log_sample(sample_query, CANARY_LATENCY_SAMPLE_LIMIT):
                try:
                    import json as _json

                    payload = _json.loads(line)
                except Exception:
                    continue
                addr = str(
                    payload.get("ServiceAddr", payload.get("service_addr", "") or "") or ""
                )
                ms = parse_duration_ms(line)
                if ms is None:
                    continue
                samples.setdefault(addr_map.get(addr, "unattributed"), []).append(ms)
            attach_latency(variants, samples)
            variants["latency_note"] = (
                f"p50/p95 from up to {CANARY_LATENCY_SAMPLE_LIMIT} sampled requests."
            )
        except Exception as exc:
            logger.debug("canary latency sampling failed: %s", exc)
        verdict, reasons = canary_verdict(variants)
        status_payload = build_traffic_split_status(service)
        return Response({
            "service_id": str(getattr(service, "id", "")),
            "window": window,
            "live_weight": status_payload["live_weight"],
            "staging_weight": status_payload["staging_weight"],
            "split_active": status_payload["split_active"],
            "live": variants["live"],
            "staging": variants["staging"],
            "unattributed": variants["unattributed"],
            "verdict": verdict,
            "reasons": reasons,
        })
