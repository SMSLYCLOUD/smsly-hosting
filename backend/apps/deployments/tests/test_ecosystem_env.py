from django.test import TestCase
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph
from apps.deployments.services.ecosystem_env import EcosystemEnvResolver, is_weak_value

class TestEcosystemEnvSafe(TestCase):
    def test_weak_values(self):
        self.assertTrue(is_weak_value("changeme"))
        self.assertTrue(is_weak_value("my_SECRET_123"))
        self.assertFalse(is_weak_value("kdjf8394jf9834jf984"))

    def test_resolver(self):
        manifest_yaml = """
        version: "1"
        mode: "production"
        shared_env:
          groups:
            core:
              vars:
                JWT_SECRET:
                  source: generated
        services:
          api:
            env:
              JWT_SECRET:
                source: shared_group
                group: core
              EXTERNAL_KEY:
                source: external_required
                required: true
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()

        self.assertFalse(success)
        self.assertIn("Service 'api' missing external required env 'EXTERNAL_KEY'", errors)

    def test_frontend_receives_backend_url(self):
        manifest_yaml = """
        version: "1"
        services:
          api:
            type: backend
          frontend:
            type: frontend
            env:
              NEXT_PUBLIC_API_URL:
                source: service_public_url
                service: api
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()
        self.assertTrue(success)
        self.assertEqual(envs["frontend"]["NEXT_PUBLIC_API_URL"], "https://api.placeholder.domain")

    def test_backend_receives_database_url(self):
        manifest_yaml = """
        version: "1"
        addons:
          my_db:
            type: postgres
        services:
          api:
            type: backend
            env:
              DATABASE_URL:
                source: addon
                addon: my_db
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()
        self.assertTrue(success)
        self.assertTrue(envs["api"]["DATABASE_URL"].startswith("postgresql://"))

    def test_worker_receives_queue_url(self):
        manifest_yaml = """
        version: "1"
        addons:
          my_redis:
            type: redis
        services:
          worker:
            type: worker
            env:
              CELERY_BROKER_URL:
                source: addon
                addon: my_redis
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()
        self.assertTrue(success)
        self.assertTrue(envs["worker"]["CELERY_BROKER_URL"].startswith("redis://"))

    def test_shared_secrets_reused(self):
        manifest_yaml = """
        version: "1"
        shared_env:
          groups:
            core:
              vars:
                SECRET:
                  source: generated
        services:
          api:
            env:
              API_SECRET:
                source: shared_group
                group: core
                var: SECRET
          worker:
            env:
              WORKER_SECRET:
                source: shared_group
                group: core
                var: SECRET
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()
        self.assertTrue(success)
        self.assertEqual(envs["api"]["API_SECRET"], envs["worker"]["WORKER_SECRET"])

    def test_missing_dependency_detected(self):
        manifest_yaml = """
        version: "1"
        services:
          frontend:
            type: frontend
            env:
              NEXT_PUBLIC_API_URL:
                source: service_public_url
                service: missing_api
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()
        self.assertFalse(success)
        self.assertTrue(any("missing service 'missing_api'" in e for e in errors))

    def test_canonical_env_map_generated(self):
        manifest_yaml = """
        version: "1"
        services:
          api:
            type: backend
            env:
              STATIC:
                source: service_internal_url
                service: api
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()
        self.assertTrue(success)
        self.assertEqual(envs["api"]["STATIC"], "http://api")
