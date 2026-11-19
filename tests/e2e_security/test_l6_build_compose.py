"""E2E security tests for L6 (BuildKit `smsly-build-net` egress isolation & Compose hardening parity)."""
from __future__ import annotations

import os
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from apps.addons.services.bundle_provisioner import BundleProvisioner
from apps.addons.services.grid_addons_parser import BundleDecl, BundleServiceDecl
from apps.cloud.services import builder as builder_mod
from apps.deployments.services import network_scope as ns_mod
from apps.deployments.services.pipeline import build as build_mod
from apps.deployments.services.pipeline.compose_networking import ComposeNetworkingMixin
from apps.deployments.services.pipeline.exceptions import BuildError

pytestmark = [pytest.mark.security]


# ============================================================================
# F7: L6 BuildKit egress isolation (smsly-build-net & DOCKER-USER drops)
# ============================================================================


def test_f7_ephemeral_builder_attaches_to_smsly_build_net_never_smsly_net():
    seen_cmds = []

    def _fake_run(cmd, **kwargs):
        seen_cmds.append(cmd)
        if cmd[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                '[{"State": {"Running": true}, "HostConfig": {"Memory": 536870912, "MemorySwap": 536870912, "CpuPeriod": 100000, "CpuQuota": 150000}}]',
                "",
            )
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    with patch.object(build_mod, "_local_registry_host", return_value="registry:5000"), patch(
        "apps.deployments.services.network_scope.ensure_build_network",
        return_value="smsly-build-net",
    ) as mock_net, patch.object(build_mod.subprocess, "run", side_effect=_fake_run):
        name = build_mod._create_ephemeral_builder("eph-test", 512, 150)

    assert name == "eph-test"
    mock_net.assert_called_once()
    create_cmd = next(c for c in seen_cmds if c[:3] == ["docker", "buildx", "create"])
    opts = [create_cmd[i + 1] for i, a in enumerate(create_cmd[:-1]) if a == "--driver-opt"]
    assert "network=smsly-build-net" in opts
    assert "network=smsly-net" not in opts


def test_f7_buildx_fallback_builder_uses_smsly_build_net():
    seen = []

    def _fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch(
        "apps.deployments.services.network_scope.ensure_build_network",
        return_value="smsly-build-net",
    ), patch.object(builder_mod.subprocess, "run", side_effect=_fake_run):
        ok, _ = builder_mod._ensure_buildx_fallback("smsly-fallback")

    assert ok is True
    create_cmd = next(c for c in seen if c[:3] == ["docker", "buildx", "create"])
    assert "network=smsly-build-net" in create_cmd
    assert "network=smsly-net" not in create_cmd


def test_f7_apply_build_network_isolation_drops_rfc1918_metadata_mesh_and_cross_bridge():
    fake_net = MagicMock()
    fake_net.attrs = {"Id": "buildnet1234567890abcdef"}
    client = MagicMock()
    client.networks.get.return_value = fake_net
    ok_proc = MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod.subprocess, "run", return_value=ok_proc
    ) as mock_run:
        assert ns_mod.apply_build_network_isolation("smsly-build-net") is True

    scripts = [c.args[0][-1] for c in mock_run.call_args_list]
    joined = "\n".join(scripts)
    for blocked in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "169.254.169.254/32",
    ):
        assert f"-d {blocked} -j DROP" in joined
    assert "-o br-+ -j DROP" in joined
    assert "-o wg+ -j DROP" in joined
    assert "-p udp --dport 53 -j RETURN" in joined
    assert "-p tcp --dport 53 -j RETURN" in joined


def test_f7_compose_build_passes_ephemeral_buildx_builder_in_env(db, user):
    from apps.deployments.models import Deployment, Service

    svc = Service.objects.create(
        name="comp-svc",
        owner=user,
        compose_file="docker-compose.yml",
        compose_main_service="web",
    )
    dep = Deployment.objects.create(service=svc, status=Deployment.Status.BUILDING, commit_hash="c1")

    class _DummyComposeBuilder(build_mod.BuildMixin, ComposeNetworkingMixin):
        def __init__(self, src):
            self.source_dir = src
            self.build_dir = src
            self.deployment = dep
            self.service = svc
            self.secret_values = []

        def _resolve_service_network_name(self):
            return "smsly-net-comp1234"

    with tempfile.TemporaryDirectory() as tmp:
        cpath = os.path.join(tmp, "docker-compose.yml")
        open(cpath, "w", encoding="utf-8").write("services:\n  web:\n    image: nginx:alpine\n")
        dummy = _DummyComposeBuilder(tmp)
        seen_envs = []

        def _fake_run(cmd, **kwargs):
            seen_envs.append((cmd, kwargs.get("env") or {}))
            return subprocess.CompletedProcess(cmd, 0, "built", "")

        with patch.object(build_mod, "append_log"), patch.object(
            build_mod, "update_stage"
        ), patch.object(
            build_mod, "_create_ephemeral_builder", return_value="smsly-eph-comp999"
        ), patch.object(build_mod, "_remove_ephemeral_builder") as mock_rm, patch.object(
            dummy, "_write_compose_routing_override", return_value=os.path.join(tmp, "override.yml")
        ), patch.object(
            dummy, "_ensure_docker_network"
        ), patch.object(build_mod.subprocess, "run", side_effect=_fake_run):
            try:
                dummy._build_with_compose()
            except Exception:
                pass

        build_call_env = next(env for cmd, env in seen_envs if "build" in cmd)
        assert build_call_env.get("BUILDX_BUILDER") == "smsly-eph-comp999"
        assert build_call_env.get("DOCKER_BUILDKIT") == "1"
        mock_rm.assert_called_once_with("smsly-eph-comp999")


# ============================================================================
# F8: L6 Compose runtime & security parity (compose_networking.py & bundle_provisioner.py)
# ============================================================================


def _make_compose_mixin(tmp_dir: str, compose_yaml: str):
    cfile = os.path.join(tmp_dir, "docker-compose.yml")
    with open(cfile, "w", encoding="utf-8") as fh:
        fh.write(compose_yaml)

    class _Harness(ComposeNetworkingMixin):
        def __init__(self):
            self.build_dir = tmp_dir
            self.source_dir = tmp_dir
            self.deployment = SimpleNamespace(id="dep-f8-1")
            self.service = SimpleNamespace(
                id="svc-f8-1",
                name="f8-web",
                compose_file="docker-compose.yml",
                managed_by="USER",
            )

        def _resolve_service_network_name(self):
            return "smsly-net-f8scope"

        def _compose_traefik_labels(self, project_name: str):
            return {"traefik.enable": "true"}

    return _Harness()


def test_f8_compose_override_enforces_local_adapter_hardening_and_runsc_dns():
    with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
        os.environ, {"SMSLY_BUILDS_DIR": tmp_dir}
    ), patch(
        "apps.deployments.services.container_runtime.detect_best_runtime",
        return_value="runsc",
    ), patch(
        "apps.deployments.services.container_runtime.sandbox_dns_servers",
        return_value=["10.100.0.1", "8.8.8.8", "1.1.1.1"],
    ), patch(
        "apps.cloud.docker_client.get_docker_client",
        return_value=MagicMock(),
    ), patch(
        "apps.deployments.services.container_refresh._resolve_gvisor_extra_hosts",
        return_value={"postgres-f8": "172.30.1.5"},
    ):
        harness = _make_compose_mixin(
            tmp_dir,
            "services:\n  f8-web:\n    image: nginx:alpine\n  worker:\n    image: python:3.12-alpine\n",
        )
        override_path = harness._write_compose_routing_override("f8-web", "proj-f8")
        data = yaml.safe_load(open(override_path, encoding="utf-8").read())

    for sname in ("f8-web", "worker"):
        sdef = data["services"][sname]
        assert sdef["init"] is True
        assert sdef["cap_drop"] == ["ALL"]
        assert sdef["cap_add"] == ["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"]
        assert sdef["pids_limit"] == 1024
        assert sdef["ulimits"]["nofile"] == {"soft": 1024, "hard": 2048}
        assert sdef["runtime"] == "runsc"
        assert sdef["dns"] == ["10.100.0.1", "8.8.8.8", "1.1.1.1"]
        assert "postgres-f8:172.30.1.5" in sdef["extra_hosts"]
        assert any(":/etc/resolv.conf:ro" in v for v in sdef["volumes"])


@pytest.mark.parametrize(
    "bad_yaml,match_text",
    [
        ("services:\n  web:\n    image: x\n    privileged: true\n", "privileged: true"),
        ("services:\n  web:\n    image: x\n    network_mode: host\n", "network_mode: host"),
        ("services:\n  web:\n    image: x\n    pid: host\n", "pid: host"),
        ("services:\n  web:\n    image: x\n    ipc: host\n", "ipc: host"),
        ("services:\n  web:\n    image: x\n    cap_add:\n      - SYS_ADMIN\n", "SYS_ADMIN"),
        (
            "services:\n  web:\n    image: x\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
            "docker.sock",
        ),
        (
            "services:\n  web:\n    image: x\n    volumes:\n      - /etc/shadow:/mnt/shadow:ro\n",
            "sensitive host path",
        ),
        (
            "services:\n  web:\n    image: x\n    security_opt:\n      - apparmor:unconfined\n",
            "security profile",
        ),
        ("services:\n  web:\n    image: x\n    userns_mode: host\n", "userns_mode: host"),
        ("services:\n  web:\n    image: x\n    uts: host\n", "uts: host"),
        ("services:\n  web:\n    image: x\n    cgroup: host\n", "cgroup: host"),
        ("services:\n  web:\n    image: x\n    devices:\n      - /dev/kvm\n", "host devices"),
        ("services:\n  web:\n    image: x\n    cap_add:\n      - SYS_PTRACE\n", "SYS_PTRACE"),
        (
            "services:\n  web:\n    image: x\n    volumes:\n      - ../../etc/passwd:/mnt/p:ro\n",
            "traversal",
        ),
        (
            "services:\n  web:\n    image: x\n    volumes:\n      - /proc:/hostproc:ro\n",
            "sensitive host path",
        ),
        (
            "services:\n  web:\n    image: x\n    security_opt:\n      - seccomp:unconfined\n",
            "security profile",
        ),
    ],
)
def test_f8_compose_override_rejects_dangerous_directives(bad_yaml, match_text):
    with tempfile.TemporaryDirectory() as tmp_dir:
        harness = _make_compose_mixin(tmp_dir, bad_yaml)
        with pytest.raises(BuildError, match=match_text):
            harness._write_compose_routing_override("web", "proj-bad")


def test_f8_bundle_provisioner_enforces_hardening_and_rejects_sensitive_mounts():
    prov = BundleProvisioner()
    bundle = BundleDecl(name="testbundle", services=[])
    svc_decl = BundleServiceDecl(
        name="api",
        image="nginx:alpine",
        ports=["8080:80"],
    )
    with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
        prov, "_compose_path", return_value=os.path.join(tmp_dir, "bundle.yml")
    ), patch(
        "apps.deployments.services.container_runtime.detect_best_runtime",
        return_value="runsc",
    ), patch(
        "apps.deployments.services.container_runtime.sandbox_dns_servers",
        return_value=["8.8.8.8", "1.1.1.1"],
    ):
        cpath = prov._generate_compose(bundle, [svc_decl], "svc-123", "my-svc", {})
        rendered = yaml.safe_load(open(cpath, encoding="utf-8").read())

    api_def = rendered["services"]["api"]
    assert api_def["init"] is True
    assert api_def["cap_drop"] == ["ALL"]
    assert api_def["pids_limit"] == 1024
    assert api_def["runtime"] == "runsc"
    assert api_def["dns"] == ["8.8.8.8", "1.1.1.1"]

    bad_decl = BundleServiceDecl(
        name="evil",
        image="alpine",
        volumes=["/var/run/docker.sock:/var/run/docker.sock"],
    )
    with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
        prov, "_compose_path", return_value=os.path.join(tmp_dir, "evil.yml")
    ):
        with pytest.raises(ValueError, match="docker.sock"):
            prov._generate_compose(bundle, [bad_decl], "svc-123", "my-svc", {})


def test_f7_ensure_build_network_creates_icc_false_bridge_and_applies_isolation():
    client = MagicMock()
    client.networks.get.side_effect = ns_mod.docker.errors.NotFound("missing")
    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod, "apply_build_network_isolation", return_value=True
    ) as mock_iso:
        name = ns_mod.ensure_build_network()

    assert name == "smsly-build-net"
    create_kw = client.networks.create.call_args.kwargs
    assert create_kw["options"]["com.docker.network.bridge.enable_icc"] == "false"
    assert create_kw["enable_ipv6"] is False
    mock_iso.assert_called_once_with("smsly-build-net")


def test_f7_apply_build_network_isolation_drops_internal_dns_before_public_dns():
    fake_net = MagicMock()
    fake_net.attrs = {"Id": "1234567890abcdef12345678"}
    client = MagicMock()
    client.networks.get.return_value = fake_net
    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod.subprocess, "run", return_value=MagicMock(returncode=0, stdout="", stderr="")
    ) as mrun:
        ns_mod.apply_build_network_isolation("smsly-build-net")

    scripts = [c.args[0][-1] for c in mrun.call_args_list]
    assert any("-d 10.0.0.0/8 -p udp --dport 53 -j DROP" in s for s in scripts)
    assert any("-d 169.254.0.0/16 -p udp --dport 53 -j DROP" in s for s in scripts)
    assert any("-d 172.16.0.0/12 -p tcp --dport 53 -j DROP" in s for s in scripts)


def test_f7_apply_build_network_isolation_is_idempotent_when_tag_present():
    fake_net = MagicMock()
    fake_net.attrs = {"Id": "abcdefabcdefabcdefabcdef"}
    client = MagicMock()
    client.networks.get.return_value = fake_net
    tag = ns_mod._rule_tag("br-abcdefabcdef")
    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod, "_list_docker_user_rules", return_value=[f"-i br-abcdefabcdef -j DROP -m comment --comment {tag}"]
    ) as mock_list, patch.object(ns_mod, "_sh") as mock_sh:
        assert ns_mod.apply_build_network_isolation("smsly-build-net") is True
    mock_list.assert_called_once()
    mock_sh.assert_not_called()


def test_f8_bundle_provisioner_rejects_privileged_and_dangerous_caps_and_host_ns():
    prov = BundleProvisioner()
    bundle = BundleDecl(name="badbundle", services=[])
    with tempfile.TemporaryDirectory() as tmp_dir:
        cpath = os.path.join(tmp_dir, "bad.yml")
        bad_priv = BundleServiceDecl(name="priv", image="alpine", extra={"privileged": True})
        bad_cap = BundleServiceDecl(name="cap", image="alpine", cap_add=["NET_ADMIN"])
        bad_pid = BundleServiceDecl(name="pid", image="alpine", extra={"pid": "host"})
        bad_dev = BundleServiceDecl(name="dev", image="alpine", extra={"devices": ["/dev/sda"]})
        with patch.object(prov, "_compose_path", return_value=cpath):
            for bad in (bad_priv, bad_cap, bad_pid, bad_dev):
                with pytest.raises(ValueError):
                    prov._generate_compose(bundle, [bad], "svc-x", "my-svc", {})


def test_f8_merged_compose_validation_catches_interpolated_mounts_external_networks_and_enforces_limits():
    with tempfile.TemporaryDirectory() as tmp_dir:
        harness = _make_compose_mixin(
            tmp_dir,
            "services:\n  web:\n    image: nginx:alpine\n",
        )
        override_path = harness._write_compose_routing_override("web", "proj-limits")
        compose_path = os.path.join(tmp_dir, "docker-compose.yml")
        merged = harness._validate_merged_compose_config(
            compose_path, override_path, {}, allowed_network="smsly-net-f8scope"
        )
        web_cfg = merged["services"]["web"]
        assert web_cfg["mem_limit"] == "512m"
        assert web_cfg["memswap_limit"] == "512m"
        assert web_cfg["cpus"] == 1.0
        assert web_cfg["pids_limit"] == 1024

        # Env-interpolated sensitive host mount must fail closed
        cfile = os.path.join(tmp_dir, "docker-compose.yml")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write("services:\n  web:\n    image: alpine\n    volumes:\n      - ${HOST_DIR}:/mnt\n")
        with pytest.raises(BuildError, match="sensitive host path"):
            harness._validate_merged_compose_config(
                cfile, None, {"HOST_DIR": "/etc"}, allowed_network="smsly-net-f8scope"
            )

        # Top-level named volume with driver_opts.device bind to /root must fail closed
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(
                "services:\n  web:\n    image: alpine\n"
                "volumes:\n  sneaky:\n    driver_opts:\n      type: none\n      o: bind\n      device: /root\n"
            )
        with pytest.raises(BuildError, match="sensitive host path"):
            harness._write_compose_routing_override("web", "proj-vol")

        # Unauthorized external or platform network attachment must fail closed
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(
                "services:\n  web:\n    image: alpine\n    networks:\n      - smsly-platform-net\n"
                "networks:\n  smsly-platform-net:\n    external: true\n"
            )
        with pytest.raises(BuildError, match="protected platform network|forbidden platform network"):
            harness._write_compose_routing_override("web", "proj-net")

        # build.network: host or network_mode: container:* must fail closed
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write("services:\n  web:\n    build:\n      context: .\n      network: host\n")
        with pytest.raises(BuildError, match="build.network: host"):
            harness._write_compose_routing_override("web", "proj-bnet")


def test_f7_nixpacks_builder_enforces_source_validation_and_env_filtering():
    with tempfile.TemporaryDirectory() as tmp_dir:
        bad_toml = os.path.join(tmp_dir, "nixpacks.toml")
        with open(bad_toml, "w", encoding="utf-8") as fh:
            fh.write('[phases.build]\ncmds = ["docker run --privileged alpine"]\n')
        with pytest.raises(RuntimeError, match="forbidden pattern"):
            builder_mod.NixpacksBuilder.build_image(
                source_dir=tmp_dir,
                image_name="registry:5000/smsly/bad:1",
            )
        os.remove(bad_toml)

        proc_ok = MagicMock(returncode=0, stdout="built", stderr="")
        with patch.object(builder_mod.shutil, "which", return_value="/usr/bin/nixpacks"), patch.object(
            builder_mod.subprocess, "run", return_value=proc_ok
        ) as mrun, patch.object(builder_mod.NixpacksBuilder, "scan_image", return_value={"status": "clean"}):
            builder_mod.NixpacksBuilder.build_image(
                source_dir=tmp_dir,
                image_name="registry:5000/smsly/good:1",
                env_vars={"APP_ENV": "prod", "DOCKER_HOST": "tcp://evil:2375", "LD_PRELOAD": "/tmp/evil.so"},
                builder_name="smsly-eph-nix123",
            )
        called_cmd = mrun.call_args.args[0]
        called_env = mrun.call_args.kwargs["env"]
        assert "APP_ENV=prod" in called_cmd
        assert not any("DOCKER_HOST=" in a for a in called_cmd)
        assert not any("LD_PRELOAD=" in a for a in called_cmd)
        assert called_env.get("BUILDX_BUILDER") == "smsly-eph-nix123"


