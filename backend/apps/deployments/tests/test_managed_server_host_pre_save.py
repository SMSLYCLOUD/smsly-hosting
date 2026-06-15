import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.deployments.models import ManagedServer
from apps.deployments.signals import _MANAGED_SERVER_HOST_RE


User = get_user_model()


class ManagedServerHostPreSaveSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="host-presave", password="x",
        )

    def test_regex_constant_matches_finding(self):
        self.assertEqual(_MANAGED_SERVER_HOST_RE.pattern, r"^[a-zA-Z0-9.\-]+$")

    def test_save_rejects_underscore(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-underscore",
            host="bad_host.example.com",
        )
        with self.assertRaises(ValidationError):
            s.save()

    def test_save_rejects_space(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-space",
            host="bad host.example.com",
        )
        with self.assertRaises(ValidationError):
            s.save()

    def test_save_rejects_at_sign(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-at",
            host="bad@host.example.com",
        )
        with self.assertRaises(ValidationError):
            s.save()

    def test_save_rejects_empty(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-empty",
            host="",
        )
        with self.assertRaises(ValidationError):
            s.save()

    def test_save_rejects_loopback_ip(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-lo",
            host="127.0.0.1",
        )
        with self.assertRaises(ValidationError):
            s.save()

    def test_save_rejects_private_rfc1918_ip(self):
        for bad in ("10.0.0.5", "172.16.0.5", "192.168.1.5"):
            s = ManagedServer(
                owner=self.user,
                name=f"srv-{bad.replace('.', '-')}",
                host=bad,
            )
            with self.assertRaises(ValidationError):
                s.save()

    def test_save_rejects_metadata_ip(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-meta",
            host="169.254.169.254",
        )
        with self.assertRaises(ValidationError):
            s.save()

    def test_save_accepts_test_net_2_ip(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-tn2",
            host="198.51.100.10",
        )
        s.save()
        self.assertEqual(ManagedServer.objects.filter(name="srv-tn2").count(), 1)

    def test_save_accepts_public_ip(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-pub",
            host="8.8.8.8",
        )
        s.save()
        self.assertEqual(ManagedServer.objects.filter(name="srv-pub").count(), 1)

    def test_save_accepts_domain(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-domain",
            host="node1.example.com",
        )
        s.save()
        self.assertEqual(ManagedServer.objects.filter(name="srv-domain").count(), 1)

    def test_save_accepts_ipv6_loopback_rejected(self):
        s = ManagedServer(
            owner=self.user,
            name="srv-v6",
            host="::1",
        )
        with self.assertRaises(ValidationError):
            s.save()
