import os
from unittest.mock import patch
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.organizations.models import Organization
from apps.teams.models import Team
from apps.deployments.models.core import Project, Service
from apps.deployments.models.registry_scope import ScopedRegistry
from apps.deployments.models.network_scope import ScopedNetwork
from apps.deployments.services.spawning_service import _scoped_network_for


from django.contrib.auth import get_user_model

User = get_user_model()


class ScopingResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pw")
        self.org = Organization.objects.create(name="Acme Org", slug="acme-org", owner=self.user)
        self.team = Team.objects.create(name="Engineering Team", organization=self.org, owner=self.user)
        self.project = Project.objects.create(name="Core API", slug="core-api", team=self.team, owner=self.user)
        self.service = Service.objects.create(name="web", project=self.project, owner=self.user, docker_image="nginx:latest")

    def test_registry_scope_hierarchy_resolution(self):
        # Organization level registry
        org_ct = ContentType.objects.get_for_model(Organization)
        ScopedRegistry.objects.create(
            content_type=org_ct,
            object_id=self.org.id,
            registry_url="org.registry.io:5000",
            username="org-user",
            password="org-secret",
            allowed_registry_hosts=["org.registry.io"],
        )

        creds = ScopedRegistry.resolve_registry_credentials(self.project)
        self.assertEqual(creds["url"], "org.registry.io:5000")
        self.assertEqual(creds["username"], "org-user")
        self.assertIn("org.registry.io", ScopedRegistry.resolve_allowed_hosts(self.project))

        # Project level override wins over organization
        proj_ct = ContentType.objects.get_for_model(Project)
        ScopedRegistry.objects.create(
            content_type=proj_ct,
            object_id=self.project.id,
            registry_url="proj.registry.io:5000",
            username="proj-user",
            password="proj-secret",
            allowed_registry_hosts=["proj.registry.io"],
        )

        creds = ScopedRegistry.resolve_registry_credentials(self.project)
        self.assertEqual(creds["url"], "proj.registry.io:5000")
        self.assertEqual(creds["username"], "proj-user")
        hosts = ScopedRegistry.resolve_allowed_hosts(self.project)
        self.assertIn("proj.registry.io", hosts)
        self.assertIn("org.registry.io", hosts)

    @patch("apps.deployments.services.spawning_service.ensure_scoped_network")
    @patch("apps.deployments.services.spawning_service.apply_egress_restrictions")
    def test_network_scope_override_in_spawning(self, mock_apply, mock_ensure):
        # Without explicit override, falls back to per-service short ID network
        net_default = _scoped_network_for(self.service)
        short_id = str(self.service.id).replace("-", "")[:12]
        # Default prefix is PAAS_NETWORK_PREFIX (paas-svc since 146d5387;
        # overridable per install).
        net_prefix = os.getenv("PAAS_NETWORK_PREFIX", "paas-svc")
        self.assertEqual(net_default, f"{net_prefix}-{short_id}")

        # Add explicit ScopedNetwork override to project
        proj_ct = ContentType.objects.get_for_model(Project)
        ScopedNetwork.objects.create(
            content_type=proj_ct,
            object_id=self.project.id,
            network_name="smsly-net-acme-prod",
            driver="bridge",
            isolated=True,
            allowed_egress_networks=["10.100.0.0/16"],
        )

        net_custom = _scoped_network_for(self.service)
        self.assertEqual(net_custom, "smsly-net-acme-prod")
        mock_apply.assert_called_with("smsly-net-acme-prod", ["10.100.0.0/16"])

    def test_service_joins_project_scope(self):
        # Configure project level registry and network
        proj_ct = ContentType.objects.get_for_model(Project)
        ScopedRegistry.objects.create(
            content_type=proj_ct,
            object_id=self.project.id,
            registry_url="app.registry.io:5000",
            username="app-user",
            password="app-secret",
        )
        ScopedNetwork.objects.create(
            content_type=proj_ct,
            object_id=self.project.id,
            network_name="smsly-net-coreapi",
            driver="bridge",
            isolated=True,
        )

        reg_scope = self.service.get_resolved_registry_scope()
        self.assertEqual(reg_scope["url"], "app.registry.io:5000")
        self.assertEqual(reg_scope["username"], "app-user")

        net_scope = self.service.get_resolved_network_scope()
        self.assertEqual(net_scope["name"], "smsly-net-coreapi")
