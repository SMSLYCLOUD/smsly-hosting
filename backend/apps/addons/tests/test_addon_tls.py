"""Comprehensive tests for F10 Platform Addon CA & Transport TLS."""
from __future__ import annotations

import ipaddress
import os
import stat
import tempfile
from types import SimpleNamespace
from unittest import mock

import psycopg2
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.addons.services import addon_tls, shared_postgres, tenant_pooler
from apps.addons.services.addon_ha import AddonHaManager
from apps.addons.services.addon_provisioner import AddonProvisioner
from apps.addons.services.db_proxy import DatabaseProxy
from apps.deployments.models import Addon, Service

User = get_user_model()


class AddonCaAndCertTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env_patch = mock.patch.dict(os.environ, {"SMSLY_ADDON_CA_DIR": self._tmp.name})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_ensure_ca_creates_valid_ca_with_strict_permissions(self):
        ca_crt_pem, ca_key_pem = addon_tls.ensure_addon_ca()
        ca_crt_path = os.path.join(self._tmp.name, "ca.crt")
        ca_key_path = os.path.join(self._tmp.name, "ca.key")
        self.assertTrue(os.path.isfile(ca_crt_path))
        self.assertTrue(os.path.isfile(ca_key_path))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(ca_key_path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(ca_crt_path).st_mode), 0o644)

        cert = x509.load_pem_x509_certificate(ca_crt_pem)
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        self.assertTrue(bc.ca)
        self.assertEqual(bc.path_length, 0)
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        self.assertTrue(ku.key_cert_sign)
        self.assertTrue(ku.crl_sign)

        # Idempotent reuse
        ca_crt_2, ca_key_2 = addon_tls.ensure_addon_ca()
        self.assertEqual(ca_crt_2, ca_crt_pem)
        self.assertEqual(ca_key_2, ca_key_pem)
        self.assertEqual(addon_tls.get_addon_ca_cert_path(), ca_crt_path)

    def test_issue_addon_cert_sans_dedup_and_signature(self):
        long_cn = "postgres-" + ("x" * 70)
        ca_pem, srv_pem, key_pem = addon_tls.issue_addon_cert(
            long_cn,
            dns_names=[long_cn, "smsly-addon-pg-1", "localhost", "10.100.0.1"],
            ip_addrs=["127.0.0.1", "10.100.0.1"],
        )
        self.assertIn(b"BEGIN PRIVATE KEY", key_pem)
        ca_cert = x509.load_pem_x509_certificate(ca_pem)
        srv_cert = x509.load_pem_x509_certificate(srv_pem)

        cn = srv_cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
        self.assertLessEqual(len(cn), 64)
        self.assertEqual(cn, long_cn[:64])

        san = srv_cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_names = san.get_values_for_type(x509.DNSName)
        ip_addrs = [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
        self.assertIn(long_cn, dns_names)
        self.assertIn("smsly-addon-pg-1", dns_names)
        self.assertIn("localhost", dns_names)
        self.assertEqual(len(dns_names), len(set(dns_names)))
        self.assertEqual(sorted(ip_addrs), ["10.100.0.1", "127.0.0.1"])

        # Verify signature against CA public key
        ca_cert.public_key().verify(
            srv_cert.signature,
            srv_cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            srv_cert.signature_hash_algorithm,
        )

    def test_url_helpers(self):
        self.assertEqual(
            addon_tls.ensure_pg_ssl_url("postgresql://u:p@h:5432/db"),
            "postgresql://u:p@h:5432/db?sslmode=require",
        )
        self.assertEqual(
            addon_tls.ensure_pg_ssl_url("postgresql://u:p@h:5432/db?sslmode=disable"),
            "postgresql://u:p@h:5432/db?sslmode=require",
        )
        self.assertEqual(
            addon_tls.ensure_pg_ssl_url("postgresql://u:p@h:5432/db?sslmode=verify-ca"),
            "postgresql://u:p@h:5432/db?sslmode=verify-ca",
        )
        self.assertEqual(
            addon_tls.ensure_redis_tls_url("redis://:pw@r:6379/0"),
            "rediss://:pw@r:6379/0?ssl_cert_reqs=none",
        )
        self.assertEqual(
            addon_tls.ensure_redis_tls_url("rediss://:pw@r:6379/0?ssl_cert_reqs=required"),
            "rediss://:pw@r:6379/0?ssl_cert_reqs=required",
        )

    def test_ensure_tls_volume_local_and_remote(self):
        seen_env_files = []

        def fake_run(cmd, **kwargs):
            if "--env-file" in cmd:
                env_path = cmd[cmd.index("--env-file") + 1]
                seen_env_files.append(env_path)
                self.assertTrue(os.path.isfile(env_path))
                content = open(env_path, encoding="utf-8").read()
                self.assertIn("CA_CRT=", content)
                self.assertIn("SRV_CRT=", content)
                self.assertIn("SRV_KEY=", content)
            self.assertIn("timeout", kwargs)
            m = mock.MagicMock()
            m.returncode = 0
            return m

        with mock.patch("apps.addons.services.addon_tls.subprocess.run", side_effect=fake_run) as mrun:
            vol = addon_tls.ensure_tls_volume(
                "smsly-addon-redis-1-tls",
                "redis-app",
                ["redis-app", "smsly-addon-redis-1", "localhost"],
                image="redis:7-alpine",
                owner="redis",
                target_dir="/certs",
            )
        self.assertEqual(vol, "smsly-addon-redis-1-tls")
        self.assertEqual(len(seen_env_files), 1)
        self.assertFalse(os.path.exists(seen_env_files[0]))
        run_cmd = mrun.call_args_list[1][0][0]
        flat = " ".join(run_cmd)
        self.assertIn("smsly-addon-redis-1-tls:/certs", flat)
        self.assertIn("chown -R redis:redis /certs", flat)
        self.assertIn("chmod 0600 /certs/server.key", flat)

        ssh = mock.MagicMock()
        ssh.exec_command.return_value = ("", "", 0)
        addon_tls.ensure_tls_volume(
            "smsly-addon-postgres-2-tls",
            "pg-app",
            ["pg-app", "localhost"],
            image="pgvector/pgvector:pg16",
            owner="postgres",
            ssh=ssh,
        )
        ssh.upload_file.assert_called_once()
        cmds = " ".join(c[0][0] for c in ssh.exec_command.call_args_list)
        self.assertIn("docker volume create smsly-addon-postgres-2-tls", cmds)
        self.assertIn("chown -R postgres:postgres", cmds)
        self.assertIn("rm -f /tmp/smsly-tls-env-", cmds)


class ProvisionerTlsTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env_patch = mock.patch.dict(os.environ, {"SMSLY_ADDON_CA_DIR": self._tmp.name})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.user = User.objects.create_user(username="tlstest", password="x")
        self.service = Service.objects.create(name="tlssvc", owner=self.user)

    def test_provision_postgres_mounts_tls_and_emits_sslmode_require(self):
        prov = AddonProvisioner()
        proc = mock.MagicMock(returncode=0, stdout="deadbeef1234\n", stderr="")
        with mock.patch("apps.addons.services.addon_tls.ensure_tls_volume", return_value="pg1-tls") as mock_tls, \
             mock.patch("apps.addons.services.addon_provisioner.subprocess.run", return_value=proc) as mock_run, \
             mock.patch.object(prov, "_wait_for_health"):
            cid, url = prov._provision_postgres("pg1", "pw1", 5432, "pg-alias")

        self.assertEqual(cid, "deadbeef1234")
        self.assertEqual(url, "postgresql://pg_alias:pw1@pg-alias:5432/pg_alias?sslmode=require")
        mock_tls.assert_called_once()
        docker_run_cmd = mock_run.call_args_list[0][0][0]
        flat = " ".join(docker_run_cmd)
        self.assertIn("pg1-tls:/etc/ssl/smsly-addon:ro", flat)
        self.assertIn("ssl=on", flat)
        self.assertIn("ssl_cert_file=/etc/ssl/smsly-addon/server.crt", flat)
        self.assertIn("ssl_key_file=/etc/ssl/smsly-addon/server.key", flat)
        exec_cmds = [" ".join(c[0][0]) for c in mock_run.call_args_list[1:]]
        self.assertTrue(any("hostssl" in c for c in exec_cmds))
        self.assertTrue(any("pg_reload_conf" in c for c in exec_cmds))

    def test_provision_redis_enforces_tls_port_and_rediss_url(self):
        prov = AddonProvisioner()
        proc = mock.MagicMock(returncode=0, stdout="abc987654321\n", stderr="")
        with mock.patch("apps.addons.services.addon_tls.ensure_tls_volume", return_value="rd1-tls") as mock_tls, \
             mock.patch("apps.addons.services.addon_provisioner.subprocess.run", return_value=proc) as mock_run, \
             mock.patch.object(prov, "_wait_for_health"):
            cid, url = prov._provision_redis("rd1", "secretpw", 6379, "redis-alias")

        self.assertEqual(cid, "abc987654321")
        self.assertEqual(url, "rediss://:secretpw@redis-alias:6379/0?ssl_cert_reqs=none")
        mock_tls.assert_called_once()
        run_cmd = " ".join(mock_run.call_args_list[0][0][0])
        self.assertIn("rd1-tls:/etc/redis/tls:ro", run_cmd)
        self.assertIn("--tls-port 6379 --port 0", run_cmd)
        self.assertIn("--tls-cert-file /etc/redis/tls/server.crt", run_cmd)
        self.assertIn("--tls-key-file /etc/redis/tls/server.key", run_cmd)
        self.assertIn("--tls-ca-cert-file /etc/redis/tls/ca.crt", run_cmd)
        self.assertIn("--tls-auth-clients no", run_cmd)

    def test_provision_remote_postgres_and_redis_enable_tls(self):
        prov = AddonProvisioner()
        server = SimpleNamespace(
            name="node-2", host="10.0.0.2", ssh_key="", ssh_password="",
            ssh_user="root", ssh_port=22, wg_address="10.100.0.2",
            is_primary=False, is_lite_agent=False,
        )
        for atype, expected_scheme, expected_flag in (
            ("POSTGRES", "postgresql://", "ssl=on"),
            ("REDIS", "rediss://", "--tls-port 6379 --port 0"),
        ):
            addon = Addon.objects.create(
                service=self.service, name=f"r-{atype.lower()}",
                addon_type=atype, status=Addon.Status.ACTIVE,
            )
            ssh = mock.MagicMock()
            ssh.exec_command.return_value = ("cid-remote\n", "", 0)
            with mock.patch("apps.deployments.services.ssh_client.SSHClient", return_value=ssh), \
                 mock.patch.object(prov, "_ensure_network"), \
                 mock.patch("apps.addons.services.addon_tls.ensure_tls_volume", return_value="vol-tls") as mtls, \
                 mock.patch.object(prov, "_wait_for_remote_tcp"), \
                 mock.patch.object(prov, "_attach_remote_scoped_network"):
                _cid, url = prov.provision_remote(addon, server)
            self.assertTrue(url.startswith(expected_scheme))
            if atype == "POSTGRES":
                self.assertIn("sslmode=require", url)
            else:
                self.assertIn("ssl_cert_reqs=none", url)
            self.assertEqual(mtls.call_args.kwargs.get("ssh"), ssh)
            sent = " ".join(c[0][0] for c in ssh.exec_command.call_args_list)
            self.assertIn(expected_flag, sent)

    def test_provision_missing_url_reconstruction_adds_tls_when_mounted(self):
        prov = AddonProvisioner()
        pg_addon = Addon.objects.create(
            service=self.service, name="pg-reconst", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, connection_url="",
        )
        ok_proc = mock.MagicMock(returncode=0, stdout="", stderr="")
        with mock.patch.object(prov, "_ensure_network"), \
             mock.patch.object(prov, "_container_status", return_value=("cidpg", True)), \
             mock.patch("apps.addons.services.addon_provisioner.subprocess.run", return_value=ok_proc), \
             mock.patch.object(prov, "_connect_addon_networks"), \
             mock.patch.object(prov, "_wait_for_health"), \
             mock.patch.object(prov, "_container_has_mount", return_value=True), \
             mock.patch.object(prov, "_get_container_env", side_effect=["u1", "d1", "p1"]):
            _cid, url = prov.provision(pg_addon)
        self.assertEqual(url, "postgresql://u1:p1@pg-reconst:5432/d1?sslmode=require")

        rd_addon = Addon.objects.create(
            service=self.service, name="rd-reconst", addon_type="REDIS",
            status=Addon.Status.ACTIVE, connection_url="",
        )
        with mock.patch.object(prov, "_ensure_network"), \
             mock.patch.object(prov, "_container_status", return_value=("cidrd", True)), \
             mock.patch("apps.addons.services.addon_provisioner.subprocess.run", return_value=ok_proc), \
             mock.patch.object(prov, "_connect_addon_networks"), \
             mock.patch.object(prov, "_wait_for_health"), \
             mock.patch.object(prov, "_container_has_mount", return_value=True), \
             mock.patch.object(prov, "_get_container_env", return_value="rp1"):
            _cid, rurl = prov.provision(rd_addon)
        self.assertEqual(rurl, "rediss://:rp1@rd-reconst:6379/0?ssl_cert_reqs=none")

    def test_create_backup_redis_passes_conditional_tls_flags(self):
        prov = AddonProvisioner()
        rd_addon = Addon.objects.create(
            service=self.service, name="rd-bkp", addon_type="REDIS",
            status=Addon.Status.ACTIVE, connection_url="rediss://:pw@rd-bkp:6379/0?ssl_cert_reqs=none",
        )
        with mock.patch("apps.addons.services.addon_provisioner.subprocess.run") as mrun:
            mrun.return_value = mock.MagicMock(returncode=0)
            prov.create_backup(rd_addon)
        exec_cmd = " ".join(mrun.call_args_list[0][0][0])
        self.assertIn("/etc/redis/tls/ca.crt", exec_cmd)
        self.assertIn("--tls --cacert /etc/redis/tls/ca.crt", exec_cmd)

    def test_deprovision_and_remote_remove_tls_volume(self):
        prov = AddonProvisioner()
        with mock.patch("apps.addons.services.addon_provisioner.subprocess.run") as mrun, \
             mock.patch("apps.addons.services.volume_encryption.cleanup_volume", return_value=True):
            mrun.return_value = mock.MagicMock(returncode=0)
            self.assertTrue(prov.deprovision("cid-1", container_name="smsly-addon-redis-x", retain_volume=False))
        cmds = [" ".join(c[0][0]) for c in mrun.call_args_list]
        self.assertTrue(any("volume rm smsly-addon-redis-x-tls" in c for c in cmds))

        ssh = mock.MagicMock()
        ssh.exec_command.return_value = ("", "", 0)
        server = SimpleNamespace(host="10.0.0.2", ssh_key="", ssh_password="", ssh_user="root", ssh_port=22)
        with mock.patch("apps.deployments.services.ssh_client.SSHClient", return_value=ssh):
            self.assertTrue(prov.deprovision_remote("cid-2", server, container_name="smsly-addon-postgres-y"))
        sent = " ".join(c[0][0] for c in ssh.exec_command.call_args_list)
        self.assertIn("smsly-addon-postgres-y-tls", sent)


class SharedPostgresAndPoolerTlsTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env_patch = mock.patch.dict(os.environ, {"SMSLY_ADDON_CA_DIR": self._tmp.name})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_shared_postgres_upgrades_non_tls_container_preserving_networks(self):
        calls = []

        def fake_run(cmd, timeout=60):
            calls.append(cmd)
            m = mock.MagicMock(returncode=0, stdout="", stderr="")
            if cmd[:3] == ["docker", "ps", "-a"]:
                m.stdout = "oldcid123\n"
            elif cmd[:2] == ["docker", "inspect"] and ".Mounts" in " ".join(cmd):
                m.stdout = "/var/lib/postgresql/data "
            elif cmd[:2] == ["docker", "inspect"] and ".NetworkSettings.Networks" in " ".join(cmd):
                m.stdout = '{"smsly-net": {"Aliases": ["postgres-a"]}, "smsly-net-123": {"Aliases": ["postgres-b"]}}'
            elif cmd[:2] == ["docker", "ps"]:
                m.stdout = "newcid456\n"
            return m

        with mock.patch("apps.addons.services.addon_tls.ensure_tls_volume", return_value="smsly-shared-postgres-tls"), \
             mock.patch("apps.addons.services.shared_postgres._superuser_password", return_value="superpw"), \
             mock.patch("apps.addons.services.shared_postgres._wait_ready"), \
             mock.patch("apps.addons.services.shared_postgres._psql", return_value=""), \
             mock.patch("apps.addons.services.shared_postgres._psql_on", return_value=""), \
             mock.patch("apps.addons.services.shared_postgres._run", side_effect=fake_run):
            cid = shared_postgres.ensure_shared_server()

        self.assertEqual(cid, "oldcid123")
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("docker stop -t 30 smsly-shared-postgres" in c for c in flat))
        run_cmd = next(c for c in flat if c.startswith("docker run -d"))
        self.assertIn("smsly-shared-postgres-tls:/etc/ssl/smsly-addon:ro", run_cmd)
        self.assertIn("-c ssl=on", run_cmd)
        self.assertTrue(any("network connect --alias postgres-b smsly-net-123" in c for c in flat))
        self.assertTrue(any("hostssl" in c for c in flat))

    def test_ensure_replication_access_uses_hostssl(self):
        with mock.patch("apps.addons.services.shared_postgres._replicator_password", return_value="rpw"), \
             mock.patch("apps.addons.services.shared_postgres._psql_on"), \
             mock.patch("apps.addons.services.shared_postgres._run", return_value=mock.MagicMock(returncode=0)) as mrun:
            shared_postgres._ensure_replication_access_on("smsly-shared-postgres")
        cmd = " ".join(mrun.call_args[0][0])
        self.assertIn("hostssl replication shared_replicator", cmd)

    def test_ensure_pooler_tls_certs_chowns_and_chmods(self):
        calls = []

        def fake_run(cmd, timeout=60):
            calls.append(cmd)
            return {}

        with mock.patch.object(tenant_pooler, "_read_remote_file", return_value=None), \
             mock.patch.object(tenant_pooler, "_write_remote_file", return_value={}) as mwrite, \
             mock.patch.object(tenant_pooler, "_run", side_effect=fake_run):
            ok = tenant_pooler.ensure_pooler_tls_certs(
                "smsly-hosting-pgbouncer-tenants-1",
                [{"alias": "postgres-acme"}],
            )
        self.assertTrue(ok)
        self.assertEqual(mwrite.call_count, 3)
        self.assertEqual(mwrite.call_args_list[2].kwargs.get("mode"), 0o600)
        flat = " ".join(" ".join(c) for c in calls)
        self.assertIn("chown -R postgres:postgres /etc/pgbouncer/tls", flat)
        self.assertIn("chmod 0600 /etc/pgbouncer/tls/server.key", flat)


class DbProxyAndHaTlsTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env_patch = mock.patch.dict(os.environ, {"SMSLY_ADDON_CA_DIR": self._tmp.name})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_db_proxy_upgrades_pg_and_redis_to_ca_verified_tls(self):
        ca_path = addon_tls.get_addon_ca_cert_path()
        pg_addon = SimpleNamespace(
            addon_type="POSTGRES",
            connection_url="postgresql://u:p@pg:5432/db?sslmode=require",
        )
        pg_proxy = DatabaseProxy(pg_addon)
        with mock.patch("psycopg2.connect") as mconn:
            pg_proxy._build_pg_connection()
        mconn.assert_called_once_with(
            pg_addon.connection_url,
            connect_timeout=10,
            sslmode="verify-ca",
            sslrootcert=ca_path,
        )

        # Fallback on legacy plaintext container when URL had no sslmode
        legacy_addon = SimpleNamespace(
            addon_type="POSTGRES",
            connection_url="postgresql://u:p@pg:5432/db",
        )
        legacy_proxy = DatabaseProxy(legacy_addon)
        with mock.patch(
            "psycopg2.connect",
            side_effect=[psycopg2.OperationalError("server does not support SSL"), mock.MagicMock()],
        ) as mconn2:
            legacy_proxy._build_pg_connection()
        self.assertEqual(mconn2.call_count, 2)

        rd_addon = SimpleNamespace(
            addon_type="REDIS",
            connection_url="rediss://:pw@rd:6379/0?ssl_cert_reqs=none",
        )
        rd_proxy = DatabaseProxy(rd_addon)
        with mock.patch("redis.from_url") as mredis:
            rd_proxy._build_redis_connection()
        mredis.assert_called_once_with(
            "rediss://:pw@rd:6379/0",
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=15,
            ssl_cert_reqs="required",
            ssl_ca_certs=ca_path,
            ssl_check_hostname=False,
        )

    def test_addon_ha_preserves_tls_and_query_params(self):
        mgr = AddonHaManager("smsly-net")
        addon = SimpleNamespace(
            id=SimpleNamespace(hex="a" * 32),
            addon_type="POSTGRES",
            name="pg-ha",
            public_domain=None,
            connection_url="postgresql://u:p@pg-ha:5432/db?sslmode=require",
        )
        with mock.patch.object(mgr, "_ensure_pg_tls_volume", return_value="smsly-addon-postgres-x-tls"), \
             mock.patch.object(mgr, "_docker_run") as mrun, \
             mock.patch("apps.addons.services.addon_provisioner.addon_provisioner._get_published_host_port", return_value=None), \
             mock.patch("subprocess.run"):
            mgr._recreate_primary_with_replication(
                addon, "smsly-addon-postgres-x", "u", "db", 5432, "p"
            )
        cmd = " ".join(mrun.call_args[0][0])
        self.assertIn("smsly-addon-postgres-x-tls:/etc/ssl/smsly-addon:ro", cmd)
        self.assertIn("ssl=on", cmd)

        with mock.patch("subprocess.run") as msub:
            msub.return_value = mock.MagicMock(returncode=0, stdout="master\n")
            master = mgr._current_master_container(SimpleNamespace(addon_type="REDIS", id="1"))
        self.assertEqual(master, "smsly-addon-redis-1")
        self.assertIn("--tls --cacert /etc/redis/tls/ca.crt", " ".join(msub.call_args[0][0]))
