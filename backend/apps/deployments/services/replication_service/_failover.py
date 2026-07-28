import logging

from ._utils import _bounded_error

logger = logging.getLogger(__name__)


class FailoverMixin:

    @classmethod
    def manual_failover(cls, mesh, target_wg_address):
        import requests

        cls.validate_mesh_for_replication(mesh)
        peers = list(mesh.peers.filter(is_active=True).order_by("wg_address"))
        if not any(peer.wg_address == target_wg_address for peer in peers):
            raise ValueError(f"Target {target_wg_address} not found in mesh")

        primary_ip = None
        primary_name = None
        for idx, peer in enumerate(peers, 1):
            try:
                resp = requests.get(
                    f"http://{peer.wg_address}:8008/patroni",
                    timeout=(2, 5),
                    allow_redirects=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if str(data.get("role", "")).lower() not in {"master", "primary", "leader"}:
                        continue
                    primary_ip = peer.wg_address
                    primary_name = (
                        data.get("patroni", {}).get("name")
                        or data.get("name")
                        or f"patroni{idx}"
                    )
                    break
            except Exception:
                continue

        if not primary_ip or not primary_name:
            raise RuntimeError("Cannot find current primary")

        target_name = None
        for idx, peer in enumerate(peers, 1):
            if peer.wg_address == target_wg_address:
                target_name = f"patroni{idx}"
                break

        if not target_name:
            raise ValueError(f"Target {target_wg_address} not found in mesh")

        target_wg_address_for_check = target_wg_address
        try:
            target_resp = requests.get(
                f"http://{target_wg_address_for_check}:8008/replica",
                timeout=(2, 5),
                allow_redirects=False,
            )
            if target_resp.status_code != 200:
                raise RuntimeError(
                    f"Target replica {target_wg_address_for_check} is not healthy (HTTP {target_resp.status_code}). "
                    "Aborting failover to prevent data loss."
                )
            target_data = target_resp.json()
            lag = target_data.get('replication_lag', target_data.get('lag', None))
            if lag is not None and isinstance(lag, (int, float)) and lag > 10:
                raise RuntimeError(
                    f"Target replica {target_wg_address_for_check} has replication lag of {lag}s. "
                    "Aborting failover to prevent data loss."
                )
        except requests.RequestException as e:
            raise RuntimeError(
                f"Cannot reach target replica {target_wg_address_for_check}: {e}. "
                "Aborting failover."
            ) from e

        resp = requests.post(
            f"http://{primary_ip}:8008/switchover",
            json={
                "leader": primary_name,
                "candidate": target_name,
            },
            timeout=(3, 30),
            allow_redirects=False,
        )

        if resp.status_code in (200, 202):
            logger.info("Failover initiated: %s -> %s", primary_name, target_name)
            return {"status": "Failover initiated", "from": primary_name,
                    "to": target_name}
        else:
            raise RuntimeError(
                f"Failover failed: HTTP {resp.status_code} {_bounded_error(resp.text)}"
            )

    @classmethod
    def reinitialize_replica(cls, mesh, target_wg_address):
        import requests

        cls.validate_mesh_for_replication(mesh)

        peers = list(mesh.peers.filter(is_active=True).order_by("wg_address"))
        target_name = None
        for idx, peer in enumerate(peers, 1):
            if peer.wg_address == target_wg_address:
                target_name = f"patroni{idx}"
                break

        if not target_name:
            raise ValueError(f"Target {target_wg_address} not found")

        for peer in peers:
            try:
                resp = requests.post(
                    f"http://{peer.wg_address}:8008/reinitialize",
                    json={"member": target_name},
                    timeout=(3, 30),
                    allow_redirects=False,
                )
                if resp.status_code in (200, 202):
                    return {"status": "Reinitialize started",
                            "target": target_name}
            except Exception:
                continue

        raise RuntimeError("Could not reach any Patroni node")
