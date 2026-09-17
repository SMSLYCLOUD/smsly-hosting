"""Static checks for the egress mirror (Alpine CDN rewrite).

No Docker needed: validates that the nginx config only ever rewrites
dl-cdn.alpinelinux.org (never hijacks other hosts), binds loopback +
docker0 gateway only (never 80/443, never 0.0.0.0), carries per-mirror
Host headers on every fallback hop, and that the installer + boot unit
wire it up.
"""
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NGINX_CONF = os.path.join(REPO, "infrastructure", "egress-mirror", "nginx.conf")
LIB = os.path.join(REPO, "lib", "egress_mirror.sh")
SETUP = os.path.join(REPO, "scripts", "setup-egress-mirror.sh")
UNIT = os.path.join(REPO, "scripts", "smsly-egress-mirror.service")
FRESH_DEPLOY = os.path.join(REPO, "lib", "fresh_deploy.sh")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestEgressMirrorNginxConf(unittest.TestCase):
    def setUp(self):
        self.conf = _read(NGINX_CONF)

    def test_dl_cdn_server_block_exists(self):
        self.assertIn("server_name dl-cdn.alpinelinux.org;", self.conf)

    def test_never_listens_on_edge_ports(self):
        for line in self.conf.splitlines():
            s = line.strip()
            if s.startswith("listen"):
                self.assertNotRegex(s, r":80\b")
                self.assertNotRegex(s, r":443\b")

    def test_no_wildcard_bind(self):
        for line in self.conf.splitlines():
            s = line.strip()
            if s.startswith("listen"):
                self.assertNotIn("*:8888", s)
                self.assertNotIn("0.0.0.0", s)

    def test_default_server_is_transparent(self):
        # Non-dl-cdn traffic must pass through with its own Host intact.
        self.assertIn("default_server", self.conf)
        self.assertIn("proxy_set_header Host $host;", self.conf)

    def test_private_ranges_only(self):
        for net in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
            self.assertIn(f"allow {net};", self.conf)
        self.assertIn("deny all;", self.conf)

    def test_fallback_chain_has_per_hop_host(self):
        # Every fallback hop must set the Host header matching the mirror
        # it talks to (wrong-Host requests 404 on mirror farms).
        for mirror in ("mirror.leaseweb.com",
                       "mirrors.edge.kernel.org",
                       "mirror.netcologne.de"):
            self.assertIn(f"proxy_set_header Host {mirror};", self.conf)


class TestEgressMirrorWiring(unittest.TestCase):
    def test_lib_defines_ensure(self):
        lib = _read(LIB)
        self.assertIn("ensure_egress_mirror()", lib)
        self.assertIn("dl-cdn.alpinelinux.org", lib)

    def test_setup_calls_ensure(self):
        setup = _read(SETUP)
        self.assertIn("ensure_egress_mirror", setup)

    def test_unit_points_at_setup(self):
        unit = _read(UNIT)
        self.assertIn("setup-egress-mirror.sh", unit)
        self.assertIn("Type=oneshot", unit)

    def test_fresh_deploy_calls_setup(self):
        self.assertIn("setup-egress-mirror.sh", _read(FRESH_DEPLOY))

    def test_default_site_removed(self):
        # Ubuntu's stock :80 default site collides with the edge proxy
        # and takes the whole nginx down (including our listeners).
        lib = _read(os.path.join(REPO, "lib", "egress_mirror.sh"))
        self.assertIn("sites-enabled/default", lib)


if __name__ == "__main__":
    unittest.main()
