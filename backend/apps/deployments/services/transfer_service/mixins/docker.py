import logging
import os
import shlex

from apps.deployments.models.core import PlatformConfig

logger = logging.getLogger(__name__)


class DockerMixin:
    def _generate_docker_run_command(self, service, metadata):
        name = service.name
        image = metadata.get('docker_image') or service.docker_image
        if not image:
            raise RuntimeError(
                f"No Docker image was available in the backup for service {service.name}. "
                "Use remote Git deployment or provide service.docker_image for this service."
            )

        config = PlatformConfig.load()

        from ....models.network_scope import ScopedNetwork
        net = ScopedNetwork.resolve_network_name(service.project) if service.project else 'smsly-net'

        run_args = ["docker", "run", "-d", "--name", name, "--restart", "unless-stopped"]
        run_args.extend([
            "--security-opt", "no-new-privileges:true",
            "--cap-drop=ALL",
            "--cap-add=NET_BIND_SERVICE",
            "--cap-add=CHOWN",
            "--cap-add=SETUID",
            "--cap-add=SETGID",
            "--pids-limit", "1024",
        ])
        run_args.extend(["--network", net])

        env_vars = metadata.get('env_vars', [])
        for e in env_vars:
            run_args.extend(["-e", f"{e['key']}={e['value']}"])

        domain = service.public_domain
        port = service.internal_port

        run_args.extend(["-l", "traefik.enable=true"])
        run_args.extend(["-l", f"traefik.docker.network={net}"])
        run_args.extend(["-l", f"traefik.http.routers.{name}.rule=Host(`{domain}`)"])
        run_args.extend(["-l", f"traefik.http.routers.{name}.service={name}"])
        run_args.extend(["-l", f"traefik.http.services.{name}.loadbalancer.server.port={port}"])

        enable_traefik_tls = (
            str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if config.use_ssl and enable_traefik_tls:
            run_args.extend(["-l", f"traefik.http.routers.{name}.entrypoints=websecure"])
            run_args.extend(["-l", f"traefik.http.routers.{name}.tls=true"])
            run_args.extend(["-l", f"traefik.http.routers.{name}.tls.certresolver=letsencrypt"])
        else:
            run_args.extend(["-l", f"traefik.http.routers.{name}.entrypoints=web"])
            if config.use_ssl:
                middleware_name = f"{name}-forwarded-https"
                run_args.extend(["-l", f"traefik.http.routers.{name}.middlewares={middleware_name}"])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Proto=https",
                ])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Port=443",
                ])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Ssl=on",
                ])

        if 'volumes' in metadata:
            for vol in metadata['volumes']:
                run_args.extend(["-v", f"{vol['name']}:{vol['mount_path']}"])

        run_args.append(image)

        safe_run = " ".join(shlex.quote(arg) for arg in run_args)

        safe_net = shlex.quote(net)
        # EGRESS ISOLATION on the target's scoped bridge — mirrors the
        # master's network_scope.py rules exactly:
        #   1. NIC wildcards must cover EVERY naming scheme (the master's
        #      OVH NIC is ens3; missing ens+ killed all internet on that
        #      host — AGENTS.md #17).
        #   2. Platform bridge RETURN: transferred services are dual-homed
        #      (project + platform bridge) on the master; without a RETURN
        #      to smsly-platform-net, cross-project traffic is DROPped.
        #   3. Same-bridge RETURN for addon reachability.
        net_cmd = (
            f"docker network inspect {safe_net} >/dev/null 2>&1 "
            f"|| docker network create {safe_net} >/dev/null; "
            f"BR=$(docker network inspect {safe_net} --format '{{{{.Id}}}}' 2>/dev/null | tr -d '-' | head -c 12); "
            f"if [ -n \"$BR\" ] && ! iptables -C DOCKER-USER -i br-$BR -j DROP 2>/dev/null; then "
            # DNS first (never shadowed)
            f"iptables -I DOCKER-USER -i br-$BR -p udp --dport 53 -j RETURN; "
            # Metadata IP guard
            f"iptables -I DOCKER-USER -i br-$BR -d 169.254.169.254/32 -j DROP; "
            # Established connections
            f"iptables -I DOCKER-USER -i br-$BR -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN; "
            # Internet via physical NICs — ALL naming schemes
            f"iptables -I DOCKER-USER -i br-$BR -o wl+ -j RETURN; "
            f"iptables -I DOCKER-USER -i br-$BR -o enp+ -j RETURN; "
            f"iptables -I DOCKER-USER -i br-$BR -o ens+ -j RETURN; "
            f"iptables -I DOCKER-USER -i br-$BR -o eno+ -j RETURN; "
            f"iptables -I DOCKER-USER -i br-$BR -o eth+ -j RETURN; "
            # Platform bridge (cross-project communication)
            f"iptables -I DOCKER-USER -i br-$BR -o docker0 -j RETURN 2>/dev/null || true; "
            # Same bridge (addon reachability)
            f"iptables -I DOCKER-USER -i br-$BR -o br-$BR -j RETURN; "
            # Cross-bridge DROP (project isolation)
            f"iptables -I DOCKER-USER -i br-$BR -o br-+ -j DROP; "
            # Catch-all DROP
            f"iptables -I DOCKER-USER -i br-$BR -j DROP; "
            f"fi"
        )
        rm_cmd = f"docker rm -f {shlex.quote(name)} || true"

        return f"{net_cmd} && {rm_cmd} && {safe_run}"
