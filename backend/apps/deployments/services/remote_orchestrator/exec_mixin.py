import logging

from urllib.parse import quote

logger = logging.getLogger(__name__)


class ExecMixin:
    def get_container_logs(self, container_name: str, tail: int = 200) -> dict | None:
        resp = self._request(
            "GET",
            f"/api/v1/node/containers/{quote(str(container_name), safe='')}/logs/",
            params={"tail": max(1, min(int(tail or 200), 1000))},
            timeout=15,
        )
        if resp is not None and resp.status_code == 200:
            return self._parse_json_response(resp, "fetching remote container logs")
        return None

    def get_container_stats(self, container_name: str) -> dict | None:
        resp = self._request(
            "GET",
            f"/api/v1/node/containers/{quote(str(container_name), safe='')}/stats/",
            timeout=15,
        )
        if resp is not None and resp.status_code == 200:
            return self._parse_json_response(resp, "fetching remote container stats")
        return None

    def get_container_networks(self, container_name: str) -> dict | None:
        """Secrets-free network list for one node container (see node container_networks)."""
        resp = self._request(
            "GET",
            f"/api/v1/node/containers/{quote(str(container_name), safe='')}/networks/",
            timeout=15,
        )
        if resp is not None and resp.status_code == 200:
            return self._parse_json_response(resp, "fetching remote container networks")
        return None

    def get_node_storage_overview(self) -> dict | None:
        resp = self._request("GET", "/api/v1/node/storage-overview/", timeout=30)
        if resp is not None and resp.status_code == 200:
            return self._parse_json_response(resp, "fetching remote storage overview")
        return None

    def list_node_volumes(self) -> list | None:
        resp = self._request("GET", "/api/v1/node/volumes/", timeout=30)
        if resp is not None and resp.status_code == 200:
            data = self._parse_json_response(resp, "listing remote volumes")
            if isinstance(data, dict):
                return data.get("volumes")
            return data if isinstance(data, list) else None
        return None

    def ensure_remote_network(self, payload: dict) -> bool:
        resp = self._request("POST", "/api/v1/node/network/ensure/", payload=payload, timeout=60)
        return resp is not None and resp.status_code < 400

    def reconcile_remote_network(self) -> bool:
        resp = self._request("POST", "/api/v1/node/network/reconcile/", timeout=120)
        return resp is not None and resp.status_code < 400

    def ensure_remote_mtls(self, payload: dict) -> bool:
        resp = self._request("POST", "/api/v1/node/mtls/ensure/", payload=payload, timeout=120)
        return resp is not None and resp.status_code < 400

    def get_remote_access_log_tail(self, lines: int = 500) -> dict | None:
        resp = self._request(
            "GET",
            "/api/v1/node/access-log-tail/",
            params={"lines": max(1, min(int(lines or 500), 2000))},
            timeout=20,
        )
        if resp is not None and resp.status_code == 200:
            return self._parse_json_response(resp, "fetching remote access logs")
        return None

    def test_remote_storage(self, payload: dict) -> dict | None:
        # Never log payload: contains cloud credentials. Transport is the
        # authenticated orchestrator channel (token/HMAC + TLS verify).
        resp = self._request("POST", "/api/v1/node/storage/test/", payload=payload, timeout=60)
        if resp is not None and resp.status_code < 400:
            return self._parse_json_response(resp, "testing remote storage")
        return None

    def upsert_remote_service(self, payload: dict) -> dict | None:
        """Idempotent create-or-update of the Service row by exact name.

        Used when the owner-scoped search misses a row that CREATE then
        rejects as already-existing (transfer-restored rows). Returns the
        parsed body (with id) on success, None otherwise. Never logs.
        """
        resp = self._request("POST", "/api/v1/node/service-upsert/", payload=payload, timeout=60)
        if resp is not None and resp.status_code < 400:
            return self._parse_json_response(resp, "upserting remote service")
        return None

    def recreate_remote_container(self, container_name: str, overrides: dict, dry_run: bool = False) -> dict | None:
        """Same-image recreate on the node with merged env (no rebuild).

        Secrets travel only over the authenticated channel; never logged.
        Returns the parsed body on success, None otherwise.
        """
        resp = self._request(
            "POST", "/api/v1/node/containers/recreate/",
            payload={"name": container_name, "overrides": overrides, "dry_run": dry_run},
            timeout=300,
        )
        if resp is not None and resp.status_code < 400:
            return self._parse_json_response(resp, "recreating remote container")
        return None

    # -- Public terminal-relay helpers (stable API for consumers) --
    def find_remote_service_id(self, service, path: str = "/api/v1/services/") -> str:
        try:
            return str(self._search_remote_service(service, path) or "")
        except AttributeError:
            return ""
        except Exception:
            return ""

    def list_remote_deployments(self, remote_service_id: str, limit: int = 1) -> list:
        try:
            resp = self._request(
                "GET",
                f"/api/v1/services/{remote_service_id}/deployments/",
                params={"limit": max(1, min(int(limit or 1), 10))},
                timeout=15,
            )
            if resp is None or resp.status_code != 200:
                return []
            data = self._parse_json_response(resp, "resolving remote deployment") or {}
            results = data.get("results", data) if isinstance(data, dict) else data
            return results if isinstance(results, list) else []
        except Exception:
            return []

    def node_ws_bases(self) -> list[str]:
        try:
            return list(self._candidate_base_urls() or [])
        except Exception:
            return []

    def ws_auth_headers(self, remote_dep_id: str) -> dict:
        try:
            return dict(self._get_headers("GET", f"/ws/terminal/{remote_dep_id}/", b"") or {})
        except Exception:
            return {}
