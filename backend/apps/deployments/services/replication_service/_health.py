import logging
import time

from django.utils import timezone

from ._utils import _bounded_error

logger = logging.getLogger(__name__)


class HealthMixin:

    @classmethod
    def check_replication_health(cls, mesh):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import requests

        peers = list(
            mesh.peers.filter(is_active=True)
            .select_related("server")
            .order_by("wg_address")
        )
        results = {"nodes": [], "primary": None, "replicas": []}

        node_specs = [
            (
                idx,
                peer.wg_address,
                peer.server.name if peer.server_id and peer.server else "local",
            )
            for idx, peer in enumerate(peers, 1)
        ]

        def _check_node(idx, wg_ip, server_name):
            try:
                resp = requests.get(
                    f"http://{wg_ip}:8008/patroni",
                    timeout=(2, 5),
                    allow_redirects=False,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                data = resp.json()
                role = str(data.get("role", "unknown") or "unknown").lower()
                return {
                    "name": f"patroni{idx}",
                    "wg_address": wg_ip,
                    "server": server_name,
                    "role": role,
                    "state": data.get("state", "unknown"),
                    "timeline": data.get("timeline"),
                    "xlog": data.get("xlog", {}),
                    "lag": data.get("xlog", {}).get("replayed_timestamp"),
                    "patroni_version": data.get("patroni", {}).get("version"),
                    "pg_version": data.get("server_version"),
                    "status": "OK",
                }
            except Exception as e:
                return {
                    "name": f"patroni{idx}",
                    "wg_address": wg_ip,
                    "server": server_name,
                    "status": f"UNREACHABLE: {_bounded_error(e)}",
                }

        with ThreadPoolExecutor(max_workers=min(len(peers) or 1, 16)) as executor:
            future_to_node = {
                executor.submit(_check_node, idx, wg_ip, server_name): wg_ip
                for idx, wg_ip, server_name in node_specs
            }

            node_results = []
            for future in as_completed(future_to_node):
                node_results.append(future.result())

        node_results.sort(key=lambda x: x["wg_address"])
        results["nodes"] = node_results

        for node_info in node_results:
            if node_info.get("status") == "OK":
                if node_info.get("role") in {"master", "primary", "leader"}:
                    results["primary"] = node_info
                else:
                    results["replicas"].append(node_info)

        if results["primary"] and results["replicas"]:
            primary_xlog = results["primary"].get("xlog", {})
            primary_location = primary_xlog.get("location")

            for replica in results["replicas"]:
                replica_xlog = replica.get("xlog", {})
                replica_location = replica_xlog.get("received_location") or \
                                   replica_xlog.get("replayed_location")
                if primary_location and replica_location:
                    try:
                        lag_bytes = cls._parse_lsn(primary_location) - \
                                    cls._parse_lsn(replica_location)
                        replica["lag_bytes"] = max(0, lag_bytes)
                    except Exception:
                        replica["lag_bytes"] = None

        try:
            has_unreachable = any(
                "UNREACHABLE" in str(node.get("status", ""))
                for node in results["nodes"]
            )
            missing_primary = bool(results["nodes"]) and not results["primary"]

            was_active = mesh.replication_status == "ACTIVE"
            was_failed = mesh.replication_status == "FAILED"

            if has_unreachable or missing_primary:
                if was_active or was_failed:
                    mesh.replication_status = "FAILED"
            else:
                mesh.replication_status = "ACTIVE"

            mesh.replication_last_result = results
            mesh.replication_last_error = (
                "One or more replication nodes are unreachable."
                if has_unreachable else (
                    "No Patroni primary detected."
                    if missing_primary else ""
                )
            )
            mesh.replication_updated_at = timezone.now()
            mesh.save(update_fields=[
                "replication_status",
                "replication_last_error",
                "replication_last_result",
                "replication_updated_at",
                "updated_at",
            ])
        except Exception as exc:
            logger.debug("Could not persist replication health for mesh %s: %s", mesh, exc)

        return results

    @classmethod
    def sync_now(cls, mesh):
        health = cls.check_replication_health(mesh)
        return {"status": mesh.replication_status, "health": health}

    @classmethod
    def disable_replication(cls, mesh):
        import os

        import docker

        from apps.deployments.services.wireguard_service import WireGuardService

        results = {"local": None, "remote": []}

        for peer in mesh.peers.filter(is_active=True):
            try:
                if peer.is_local:
                    client = docker.from_env()
                    docker_host = os.environ.get("DOCKER_HOST", "tcp://socket-proxy:2375")
                    client.containers.run(
                        "docker:cli",
                        command=[
                            "sh",
                            "-c",
                            "docker compose -p smsly-patroni down || true && "
                            "docker compose -p smsly-haproxy down || true",
                        ],
                        remove=True,
                        environment={"DOCKER_HOST": docker_host},
                        network_mode="host",
                    )
                    results["local"] = {"peer": str(peer), "status": "OK"}
                elif peer.server:
                    WireGuardService._ssh_run(
                        peer.server,
                        "cd /opt/smsly/patroni 2>/dev/null && docker compose -p smsly-patroni down || true",
                        timeout=120,
                    )
                    results["remote"].append({"peer": str(peer), "status": "OK"})
            except Exception as exc:
                logger.warning("Failed to disable replication on %s: %s", peer, exc)
                result = {"peer": str(peer), "status": f"FAILED: {exc}"}
                if peer.is_local:
                    results["local"] = result
                else:
                    results["remote"].append(result)

        failures = []
        if results["local"] and str(results["local"].get("status", "")).startswith("FAILED"):
            failures.append(results["local"]["status"])
        failures.extend(
            item["status"] for item in results["remote"]
            if str(item.get("status", "")).startswith("FAILED")
        )

        mesh.replication_status = "FAILED" if failures else "DISABLED"
        mesh.replication_last_error = "; ".join(failures)
        mesh.replication_last_result = results
        mesh.replication_updated_at = timezone.now()
        mesh.save(update_fields=[
            "replication_status",
            "replication_last_error",
            "replication_last_result",
            "replication_updated_at",
            "updated_at",
        ])
        return results

    @classmethod
    def check_replication_lag_sql(cls, mesh):
        import requests

        primary_ip = None
        for peer in mesh.peers.filter(is_active=True):
            try:
                resp = requests.get(
                    f"http://{peer.wg_address}:8008/master",
                    timeout=3,
                )
                if resp.status_code == 200:
                    primary_ip = peer.wg_address
                    break
            except Exception:
                continue

        if not primary_ip:
            return {"error": "No primary found"}

        try:
            resp = requests.get(
                f"http://{primary_ip}:8008/patroni",
                timeout=5,
            )
            return resp.json()
        except Exception as e:
            return {"error": str(e)}
