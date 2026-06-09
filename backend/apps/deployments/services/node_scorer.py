"""Node scoring for auto-scaling — ranks ManagedServers by available resources."""
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PROMETHEUS_URL = getattr(settings, 'PROMETHEUS_INTERNAL_URL', 'http://prometheus:9090')
TIMEOUT = 10


class NodeScorer:
    """Query Prometheus node-exporter metrics and rank nodes by free capacity."""

    MEM_WEIGHT = 0.40
    CPU_WEIGHT = 0.40
    DISK_WEIGHT = 0.20

    def score(self, nodes):
        """Return nodes sorted by available resource score (highest first)."""
        if not nodes:
            return []

        scores = []
        for node in nodes:
            free_mem = self._query_node_mem(node)
            free_cpu = self._query_node_cpu(node)
            free_disk = self._query_node_disk(node)

            if free_mem is None and free_cpu is None:
                scores.append((node, -1, {'mem': 0, 'cpu': 0, 'disk': 0}))
                continue

            fm = free_mem or 50
            fc = free_cpu or 50
            fd = free_disk or 50

            score = (fm * self.MEM_WEIGHT + fc * self.CPU_WEIGHT + fd * self.DISK_WEIGHT)
            scores.append((node, score, {'mem': fm, 'cpu': fc, 'disk': fd}))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def best(self, nodes, min_score=20):
        """Return the single best node above min_score, or None."""
        ranked = self.score(nodes)
        if ranked and ranked[0][1] >= min_score:
            return ranked[0][0]
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
        except Exception as exc:
            logger.debug("PromQL query failed: %s", exc)
            return None

    @staticmethod
    def _node_ip(node):
        return node.wg_address or node.private_ip or node.host or ''
