"""Node scoring for auto-scaling — ranks ManagedServers by available resources."""
import logging
import os
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PROMETHEUS_URL = getattr(settings, 'PROMETHEUS_INTERNAL_URL', 'http://smsly-prometheus:9090')
TIMEOUT = 10

# Fallback defaults — used only when PlatformConfig DB values are unavailable
_FALLBACK_MIN_SCORE = int(os.environ.get("NODE_SCORER_MIN_SCORE", "20"))


def _get_min_score() -> int:
    """Read node_scorer_min_score from PlatformConfig, falling back to env var."""
    try:
        from apps.deployments.models.platform import PlatformConfig
        pc, _ = PlatformConfig.objects.get_or_create(pk=1)
        return pc.node_scorer_min_score or _FALLBACK_MIN_SCORE
    except Exception:
        return _FALLBACK_MIN_SCORE


class NodeScorer:
    """Query Prometheus node-exporter metrics and rank nodes by free capacity."""

    MEM_WEIGHT = 0.40
    CPU_WEIGHT = 0.40
    DISK_WEIGHT = 0.20

    def score(self, nodes):
        """Return nodes sorted by available resource score (highest first).

        Each entry is (node, score, {'mem': free%, 'cpu': free%, 'disk': free%}).
        Nodes where Prometheus returns no data for both mem and CPU get score -1.
        """
        if not nodes:
            return []

        scores = []
        for node in nodes:
            free_mem = self._query_node_mem(node)
            free_cpu = self._query_node_cpu(node)
            free_disk = self._query_node_disk(node)

            if free_mem is None and free_cpu is None:
                logger.warning(
                    "Node %s: Prometheus returned no data for mem and cpu "
                    "(ip=%s) — scoring as unavailable",
                    node.name, self._node_ip(node),
                )
                scores.append((node, -1, {'mem': 0, 'cpu': 0, 'disk': 0}))
                continue

            # Default to 50% when a single metric is missing (partial data)
            fm = free_mem if free_mem is not None else 50
            fc = free_cpu if free_cpu is not None else 50
            fd = free_disk if free_disk is not None else 50

            if free_mem is None:
                logger.info("Node %s: Prometheus returned no mem data, defaulting to 50%%", node.name)
            if free_cpu is None:
                logger.info("Node %s: Prometheus returned no cpu data, defaulting to 50%%", node.name)
            if free_disk is None:
                logger.info("Node %s: Prometheus returned no disk data, defaulting to 50%%", node.name)

            score = (fm * self.MEM_WEIGHT + fc * self.CPU_WEIGHT + fd * self.DISK_WEIGHT)
            scores.append((node, score, {'mem': fm, 'cpu': fc, 'disk': fd}))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def best(self, nodes, min_score=None):
        """Return the single best node above min_score, or None.

        Logs per-node scores when no node qualifies so operators can
        diagnose why scaling failed.
        """
        if min_score is None:
            min_score = _get_min_score()
        ranked = self.score(nodes)
        if not ranked:
            logger.warning("NodeScorer.best: no nodes to score")
            return None
        if ranked[0][1] >= min_score:
            return ranked[0][0]
        # Log all scores so the "All nodes too loaded" error is diagnosable
        for node, score, resources in ranked:
            logger.warning(
                "Node %s scored %.1f (min=%.0f) — mem=%.0f%% cpu=%.0f%% disk=%.0f%%",
                node.name, score, min_score,
                resources['mem'], resources['cpu'], resources['disk'],
            )
        return None

    def _query_node_mem(self, node):
        return self._promql_avg(
            f'100 - (node_memory_MemAvailable_bytes{{instance=~".*{self._node_ip(node)}.*"}} '
            f'/ node_memory_MemTotal_bytes{{instance=~".*{self._node_ip(node)}.*"}} * 100)'
        )

    def _query_node_cpu(self, node):
        return self._promql_avg(
            f'100 - (avg by(instance)(rate(node_cpu_seconds_total{{mode!="idle",'
            f'instance=~".*{self._node_ip(node)}.*"}}[2m])) * 100)'
        )

    def _query_node_disk(self, node):
        return self._promql_avg(
            f'100 - (node_filesystem_avail_bytes{{instance=~".*{self._node_ip(node)}.*",'
            f'mountpoint="/"}} / node_filesystem_size_bytes{{instance=~".*{self._node_ip(node)}.*",'
            f'mountpoint="/"}} * 100)'
        )

    def _promql_avg(self, query):
        try:
            resp = requests.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={'query': query},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get('data', {}).get('result', [])
            if not results:
                return None
            values = [float(r['value'][1]) for r in results if r.get('value')]
            return sum(values) / len(values) if values else None
        except requests.exceptions.Timeout:
            logger.warning("PromQL query timed out after %ds: %s", TIMEOUT, query[:80])
            return None
        except requests.exceptions.ConnectionError:
            logger.warning("PromQL connection failed (url=%s)", PROMETHEUS_URL)
            return None
        except Exception as exc:
            logger.warning("PromQL query failed: %s", exc)
            return None

    @staticmethod
    def _node_ip(node):
        raw = node.wg_address or node.private_ip or node.host or ''
        return re.escape(raw)  # escape regex meta-chars for PromQL instance=~ pattern
