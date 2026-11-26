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
        # Non-dl-cdn traffic must pass through with its own Host intact on :80,
        # while blocking empty/IP/private/internal Hosts against SSRF.
        self.assertIn("default_server", self.conf)
        self.assertIn("proxy_pass http://$host:80$request_uri;", self.conf)
        self.assertIn("proxy_set_header Host $host;", self.conf)
        for token in ("169\\.254", "::1", "fe80:", "localhost", "return 403;"):
            self.assertIn(token, self.conf)

    def test_private_ranges_only(self):
        for net in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
            self.assertIn(f"allow {net};", self.conf)
        self.assertIn("deny all;", self.conf)

    def test_fallback_chain_has_per_hop_host(self):
        # Every fallback hop must set the Host header matching the mirror
        # it talks to (wrong-Host requests 404 on mirror farms). Hosts
        # are set per-location (a single upstream block cannot do
        # per-peer Host headers).
        for mirror in ("mirror.leaseweb.com",
                       "mirrors.edge.kernel.org",
                       "mirror.netcologne.de"):
            self.assertIn(f"proxy_set_header Host $apk_mirror;", self.conf)
            self.assertIn(f"set $apk_mirror {mirror};", self.conf)

    def test_no_ipv6_upstream(self):
        # These hosts have no v6 route: resolving AAAA first stalls every
        # fetch until the worker pool fills (observed 768 exhaustion).
        self.assertIn("ipv6=off", self.conf)

    def test_worker_ceiling_raised(self):
        lib = _read(os.path.join(REPO, "lib", "egress_mirror.sh"))
        self.assertIn("LimitNOFILE=32768", lib)
        self.assertIn("smsly-egress-mirror.conf", lib)
        # rlimits apply at start only: a changed drop-in must restart,
        # not reload, nginx.
        self.assertIn("_nofile_dirty", lib)

    def test_rate_limit_codes_fail_over(self):
        # Mirrors intermittently 403/429 single sources; those must chain
        # to the next mirror, not fail the fetch.
        for code in ("403", "429"):
            self.assertIn(code, self.conf)

    def test_listen_targets_are_placeholders(self):
        # Gateway and public IP differ per host — rendered at install,
        # never hardcoded (except loopback).
        self.assertIn("__GATEWAY_IP__", self.conf)
        self.assertIn("__HOST_PUBLIC_IP__", self.conf)
        for line in self.conf.splitlines():
            s = line.strip()
            if s.startswith("listen") and "8888" in s and "127.0.0.1" not in s:
                self.assertIn("__GATEWAY_IP__", s)


class TestEgressMirrorWiring(unittest.TestCase):
    def test_lib_defines_ensure(self):
        lib = _read(os.path.join(REPO, "lib", "egress_mirror.sh"))
        self.assertIn("ensure_egress_mirror()", lib)
        self.assertIn("dl-cdn.alpinelinux.org", lib)

    def test_lib_renders_template(self):
        # Gateway differs per host (custom --bip) and REDIRECT preserves
        # the public source IP — both rendered live, never hardcoded.
        lib = _read(os.path.join(REPO, "lib", "egress_mirror.sh"))
        self.assertIn("__GATEWAY_IP__", lib)
        self.assertIn("__HOST_PUBLIC_IP__", lib)
        self.assertIn("ip -4 route get 1.1.1.1", lib)

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

    def test_nginx_self_traffic_exempted(self):
        # The OUTPUT REDIRECT also catches nginx's OWN upstream fetches
        # (it proxies TO port 80) — without an owner-uid exemption each
        # shimmed request re-enters the shim recursively until
        # worker_connections/FDs exhaust and everything 500s (live incident).
        # Kernel OUTPUT filter rules also drop nginx access to private/metadata CIDRs.
        lib = _read(os.path.join(REPO, "lib", "egress_mirror.sh"))
        self.assertIn("--uid-owner", lib)
        self.assertIn("-j RETURN", lib)
        self.assertIn("169.254.0.0/16", lib)
        self.assertIn("100.64.0.0/10", lib)
        self.assertIn("-j DROP", lib)
        self.assertIn("iptables -I OUTPUT 3", lib)
        self.assertNotIn("iptables -A OUTPUT -m owner", lib)

    def test_unit_requires_nginx(self):
        unit = _read(UNIT)
        self.assertIn("After=network-online.target docker.service nginx.service", unit)
        self.assertIn("Requires=nginx.service", unit)

    def test_default_server_resolver_and_ssrf_regex(self):
        conf = _read(NGINX_CONF)
        default_blk = conf.split("default_server", 1)[1]
        self.assertIn("resolver 127.0.0.53", default_blk)

        patterns = re.findall(r'if \(\$host ~\* "([^"]+)"\)', default_blk)
        self.assertGreaterEqual(len(patterns), 4)

        def is_blocked(host):
            return any(re.search(p, host, re.IGNORECASE) for p in patterns)

        # Public FQDNs starting with 0. or 10. must NOT be blocked
        self.assertFalse(is_blocked("0.pool.ntp.org"))
        self.assertFalse(is_blocked("10.example.com"))
        self.assertFalse(is_blocked("0.pool.ntp.org:80"))

        # Dotted/decimal/hex/octal numeric IPv4 SSRF forms MUST be blocked
        for bad in (
            "0177.0.0.1",
            "0x7f.0.0.1",
            "2130706433",
            "127.0.0.1",
            "127.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
        ):
            self.assertTrue(is_blocked(bad), f"Expected SSRF host {bad!r} to be blocked")


if __name__ == "__main__":
    unittest.main()
