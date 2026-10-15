"""Trust-domain-aware SPIRE socket volumes for Envoy sidecars.

Regression test for the 2026-10-11 outage: every sidecar mounted the
ecosystem agent socket, so platform-trust sidecars were denied
("not authorized for the requested identities") while looking healthy.
"""
from unittest import mock

from django.test import SimpleTestCase

from apps.mtls.services.envoy_sidecar import (
    PLATFORM_SPIRE_AGENT_SOCKET_VOLUME,
    PLATFORM_SPIRE_SVIDS_VOLUME,
    SPIRE_AGENT_SOCKET_VOLUME,
    SPIRE_SVIDS_VOLUME,
    sidecar_sablier_labels,
    spire_volumes_for,
)


class SpireVolumesForTests(SimpleTestCase):
    def test_platform_domain_uses_platform_volumes(self):
        sock, svids = spire_volumes_for("platform.local")
        self.assertEqual(sock, PLATFORM_SPIRE_AGENT_SOCKET_VOLUME)
        self.assertEqual(svids, PLATFORM_SPIRE_SVIDS_VOLUME)
        self.assertNotEqual(sock, SPIRE_AGENT_SOCKET_VOLUME)

    def test_ecosystem_domain_uses_ecosystem_volumes(self):
        sock, svids = spire_volumes_for("ecosystem.local")
        self.assertEqual(sock, SPIRE_AGENT_SOCKET_VOLUME)
        self.assertEqual(svids, SPIRE_SVIDS_VOLUME)

    def test_blank_and_unknown_fall_back_to_ecosystem(self):
        for domain in (None, "", "custom.example.com"):
            sock, svids = spire_volumes_for(domain)
            self.assertEqual(sock, SPIRE_AGENT_SOCKET_VOLUME)
            self.assertEqual(svids, SPIRE_SVIDS_VOLUME)


class SidecarSablierLabelsTests(SimpleTestCase):
    def test_always_on_service_gets_no_labels(self):
        svc = mock.Mock()
        svc.sablier_enabled = False
        svc.name = "plain-svc"
        self.assertEqual(sidecar_sablier_labels(svc), {})

    def test_sablier_service_joins_group(self):
        # Mesh-served apps never enroll (no in-band wake path for
        # sidecar-to-sidecar dials): the sidecar stays up with its app.
        svc = mock.Mock()
        svc.sablier_enabled = True
        svc.name = "sleepy-svc"
        self.assertEqual(sidecar_sablier_labels(svc), {})

    def test_never_raises(self):
        self.assertEqual(sidecar_sablier_labels(None), {})
