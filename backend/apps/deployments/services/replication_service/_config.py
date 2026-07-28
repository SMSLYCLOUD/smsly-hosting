import base64
import textwrap

from ._utils import _yaml_scalar


class ConfigMixin:

    @classmethod
    def generate_patroni_compose(cls, mesh, db_password, admin_password,
                                  replication_password="repl_pass",
                                  is_fresh: bool = True):
        peers = list(mesh.peers.filter(is_active=True).order_by("wg_address"))
        if len(peers) < 2:
            raise ValueError("Need at least 2 peers for replication")

        etcd_cluster = ",".join(
            f"etcd{i}=http://{p.wg_address}:2380"
            for i, p in enumerate(peers, 1)
        )
        etcd_endpoints = ",".join(f"{p.wg_address}:2379" for p in peers)

        cluster_state = "new" if is_fresh else "existing"

        if len(peers) <= 3:
            sync_lines = (
                '                      PATRONI_SYNCHRONOUS_MODE: "on"\n'
                "                      PATRONI_POSTGRESQL_PARAMETERS: "
                "'synchronous_commit=on synchronous_standby_names=\\'\\'*\\''"
            )
        else:
            sync_lines = (
                '                      PATRONI_POSTGRESQL_PARAMETERS: "synchronous_commit=remote_apply"'
            )

        configs = {}
        for idx, peer in enumerate(peers, 1):
            wg_ip = peer.wg_address
            node_name = f"patroni{idx}"
            etcd_name = f"etcd{idx}"

            compose = textwrap.dedent(f"""\
                version: '3.8'

                services:
                  etcd:
                    image: {cls.ETCD_IMAGE}
                    container_name: {etcd_name}
                    restart: unless-stopped
                    command: >
                      etcd
                      --name {etcd_name}
                      --initial-advertise-peer-urls http://{wg_ip}:2380
                      --listen-peer-urls http://{wg_ip}:2380
                      --listen-client-urls http://{wg_ip}:2379
                      --advertise-client-urls http://{wg_ip}:2379
                      --initial-cluster {etcd_cluster}
                      --initial-cluster-state {cluster_state}
                      --initial-cluster-token smsly-etcd-cluster
                    network_mode: host
                    volumes:
                      - etcd-data:/etcd-data

                  patroni:
                    image: {cls.PATRONI_IMAGE}
                    container_name: {node_name}
                    hostname: {node_name}
                    extra_hosts:
                      - "{node_name}:{wg_ip}"
                    restart: unless-stopped
                    network_mode: host
                    environment:
                      SCOPE: smsly-cluster
                      PGVERSION: "16"
                      ETCD3_HOSTS: "{etcd_endpoints}"
                      PATRONI_NAME: {node_name}
                      PATRONI_RESTAPI_CONNECT_ADDRESS: "{wg_ip}:8008"
                      PATRONI_RESTAPI_LISTEN: "{wg_ip}:8008"
                      PATRONI_POSTGRESQL_CONNECT_ADDRESS: "{wg_ip}:{cls.PATRONI_POSTGRES_PORT}"
                      PATRONI_POSTGRESQL_LISTEN: "{wg_ip}:{cls.PATRONI_POSTGRES_PORT}"
                      PATRONI_POSTGRESQL_DATA_DIR: /home/postgres/pgdata/pgroot/data
                      PATRONI_REPLICATION_USERNAME: replicator
                      PATRONI_REPLICATION_PASSWORD: {_yaml_scalar(replication_password)}
                      PATRONI_SUPERUSER_USERNAME: postgres
                      PATRONI_SUPERUSER_PASSWORD: {_yaml_scalar(db_password)}
                      PGUSER_SUPERUSER: postgres
                      PGPASSWORD_SUPERUSER: {_yaml_scalar(db_password)}
                      PGUSER_ADMIN: smsly_admin
                      PGPASSWORD_ADMIN: {_yaml_scalar(admin_password)}
{sync_lines}
                    volumes:
                      - patroni-data:/home/postgres/pgdata
                    depends_on:
                      - etcd

                volumes:
                  etcd-data:
                  patroni-data:
            """)
            configs[wg_ip] = compose

        return configs

    @classmethod
    def generate_haproxy_config(cls, mesh):
        peers = list(mesh.peers.filter(is_active=True).order_by("wg_address"))
        local_peer = next((peer for peer in peers if peer.is_local), None)
        bind_ip = local_peer.wg_address if local_peer else "127.0.0.1"

        master_servers = "\n".join(
            f"    server patroni{i} {p.wg_address}:{cls.PATRONI_POSTGRES_PORT} "
            f"maxconn 100 check port 8008"
            for i, p in enumerate(peers, 1)
        )

        replica_servers = "\n".join(
            f"    server patroni{i} {p.wg_address}:{cls.PATRONI_POSTGRES_PORT} "
            f"maxconn 100 check port 8008"
            for i, p in enumerate(peers, 1)
        )

        import secrets
        haproxy_stats_password = secrets.token_urlsafe(24)

        config = textwrap.dedent(f"""\
            defaults
                mode tcp
                timeout connect 5000ms
                timeout client 50000ms
                timeout server 50000ms

            frontend master
                bind {bind_ip}:5000
                default_backend master

            backend master
                option httpchk GET /master
                http-check expect status 200
                default-server inter 3s fall 3 rise 2 on-marked-down shutdown-sessions
            {master_servers}

            frontend replicas
                bind {bind_ip}:5001
                default_backend replicas

            backend replicas
                option httpchk GET /replica
                http-check expect status 200
                default-server inter 3s fall 3 rise 2 on-marked-down shutdown-sessions
            {replica_servers}

            listen stats
                bind {bind_ip}:7000
                mode http
                stats enable
                stats uri /
                stats auth admin:{haproxy_stats_password}
                stats refresh 10s
        """)
        return config, haproxy_stats_password

    @classmethod
    def generate_haproxy_compose(cls, mesh):
        haproxy_cfg, haproxy_stats_password = cls.generate_haproxy_config(mesh)
        haproxy_cfg_b64 = base64.b64encode(haproxy_cfg.encode()).decode()

        compose = textwrap.dedent(f"""\
            version: '3.8'

            services:
              haproxy:
                image: {cls.HAPROXY_IMAGE}
                container_name: smsly-haproxy
                restart: unless-stopped
                network_mode: host
                environment:
                  HAPROXY_CONFIG_B64: "{haproxy_cfg_b64}"
                command:
                  - sh
                  - -c
                  - |
                    printf '%s' "$$HAPROXY_CONFIG_B64" | base64 -d > /usr/local/etc/haproxy/haproxy.cfg
                    exec haproxy -f /usr/local/etc/haproxy/haproxy.cfg
        """)
        return compose, haproxy_cfg
